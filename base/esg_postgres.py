import os
import grp
import pwd
import shutil
import sys
import re
import datetime
import ConfigParser
import logging
import getpass
from time import sleep
from distutils.spawn import find_executable
import semver
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from esgf_utilities import esg_functions
from esgf_utilities import esg_property_manager
from esgf_utilities import pybash

from installer import install_codes
from esg_init import PostgresConfig

logger = logging.getLogger("esgf_logger" + "." + __name__)


def download_postgres():
    '''Download postgres from yum'''
    print "\n*******************************"
    print "Downloading Postgres"
    print "******************************* \n"

    esg_functions.stream_subprocess_output(
        "yum -y install postgresql-server.x86_64 postgresql.x86_64 postgresql-devel.x86_64")


def initialize_postgres():
    '''Sets up postgres data directory'''
    try:
        if os.listdir("/var/lib/pgsql/data"):
            logger.info("Data directory already exists. Skipping initialize_postgres().")
            return
    except OSError, error:
        logger.error(error)

    esg_functions.stream_subprocess_output("service postgresql initdb")
    os.chmod(os.path.join(config["postgress_install_dir"], "data"), 0700)


def check_existing_pg_version(psql_path, force_install=False):
    '''Gets the version number if a previous Postgres installation is detected'''
    print "Checking for postgresql >= {postgress_min_version} ".format(postgress_min_version=config["postgress_min_version"])

    if not psql_path:
        print "Postgres not found on system"
    else:
        try:
            postgres_version_found = esg_functions.call_subprocess("psql --version")["stdout"]
            postgres_version_number = re.search("\d.*", postgres_version_found).group()
            if semver.compare(postgres_version_number, config["postgress_min_version"]) >= 0 and not force_install:
                logger.info("Found acceptible Postgres version")
                return True
            else:
                logger.info("The Postgres version on the system does not meet the minimum requirements")
                return False
        except OSError:
            logger.exception("Unable to check existing Postgres version \n")


def setup_postgres(force_install=False, default_continue_install="N"):
    '''Installs postgres'''
    print "\n*******************************"
    print "Setting up Postgres"
    print "******************************* \n"

    psql_path = find_executable("psql")

    if check_existing_pg_version(psql_path):
        try:
            setup_postgres_answer = esg_property_manager.get_property("update.postgres")
        except ConfigParser.NoOptionError:
            setup_postgres_answer = raw_input(
                "Valid existing Postgres installation found. Do you want to continue with the setup [y/N]: ") or default_continue_install

        if setup_postgres_answer.lower().strip() in ["no", 'n']:
            logger.info("Skipping Postgres installation. Using existing Postgres version")
            return True
        else:
            force_install = True
            backup_db("postgres", "postgres")

    download_postgres()

    #XXX This is all done by default by the 'yum' installer:
    #Create system account (postgres) if it doesn't exist
    ########
    # Create the system account for postgress to run as.
    ########
    # pg_sys_acct_homedir = psql_path
    # if not check_for_postgres_sys_acct():
    #     # NOTE: "useradd/groupadd" are a RedHat/CentOS thing... to make this cross distro compatible clean this up.
    #     create_postgres_group()
    #     set_pg_sys_account_password()
    #     create_postgres_system_user(pg_sys_acct_homedir)
    #
    # else:
    #     postgress_user_shell = get_postgres_user_shell()
    #     if postgress_user_shell != "/bin/bash":
    #         set_postgres_user_shell()
    # change_pg_install_dir_ownership()

    initialize_postgres()

    # start the postgres server
    start_postgres()
    setup_postgres_conf_file()
    setup_hba_conf_file()

    restart_postgres()

    setup_db_schemas()
    create_pg_pass_file()

    esg_functions.check_shmmax()
    write_postgress_env()
    write_postgress_install_log()


def create_pg_super_user(psycopg2_cursor, db_user_password):
    '''Create postgres super user'''
    print "Create {db_user} user: ".format(db_user=config["postgress_user"]), psycopg2_cursor.mogrify("CREATE USER {db_user} with CREATEROLE superuser PASSWORD \'{db_user_password}\';".format(db_user=config["postgress_user"], db_user_password=db_user_password))
    try:
        psycopg2_cursor.execute("CREATE USER {db_user} with CREATEROLE superuser PASSWORD \'{db_user_password}\';".format(
            db_user=config["postgress_user"], db_user_password=db_user_password))
    except psycopg2.ProgrammingError, error:
        # Error code reference: https://www.postgresql.org/docs/current/static/errcodes-appendix.html#ERRCODES-TABLE
        if error.pgcode == "42710":
            print "{db_user} role already exists. Skipping creation".format(db_user=config["postgress_user"])


def create_pg_publisher_user(cursor, db_user_password):
    '''Creates postgres user for the ESGF Publisher (esgcet by default)'''
    publisher_db_user = esg_property_manager.get_property("publisher.db.user")
    if not publisher_db_user:
        publisher_db_user = raw_input(
            "What is the (low privilege) db account for publisher? [esgcet]: ") or "esgcet"
    print "Create {publisher_db_user} user:".format(publisher_db_user=publisher_db_user), cursor.mogrify("CREATE USER {publisher_db_user} PASSWORD \'{db_user_password}\';".format(publisher_db_user=publisher_db_user, db_user_password=db_user_password))
    try:
        cursor.execute("CREATE USER {publisher_db_user} PASSWORD \'{db_user_password}\';".format(
            publisher_db_user=publisher_db_user, db_user_password=db_user_password))
    except psycopg2.ProgrammingError, error:
        # Error code reference: https://www.postgresql.org/docs/current/static/errcodes-appendix.html#ERRCODES-TABLE
        if error.pgcode == "42710":
            print "{publisher_db_user} role already exists. Skipping creation".format(publisher_db_user=publisher_db_user)

def setup_db_schemas(publisher_password=None):
    '''Load ESGF schemas'''
    conn = connect_to_db("postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    try:
        db_user_password = esg_functions.get_publisher_password()
    except IOError, error:
        logger.debug(error)
        esg_functions.set_publisher_password(publisher_password)
        db_user_password = esg_functions.get_publisher_password()

    create_pg_super_user(cur, db_user_password)

    # create 'esgcet' user
    create_pg_publisher_user(cur, db_user_password)

    # create CoG and publisher databases
    # create_database("cogdb", cur)
    create_database("esgcet", cur)
    cur.close()
    conn.close()

    # TODO: move download_config_files() here

    load_esgf_schemas(db_user_password)

    # IMPORTANT: change connections to require encrypted password
    esg_functions.replace_string_in_file("/var/lib/pgsql/data/pg_hba.conf", "ident", "md5")


def load_esgf_schemas(db_user_password):
    '''Loads ESGF schemas from SQL scripts'''
    conn = connect_to_db("dbsuper", db_name='esgcet', password=db_user_password)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    # load ESGF schemas
    cur.execute(open(os.path.join(os.path.dirname(__file__), "sqldata/esgf_esgcet.sql"), "r").read())
    cur.execute(open(os.path.join(os.path.dirname(__file__), "sqldata/esgf_node_manager.sql"), "r").read())
    cur.execute(open(os.path.join(os.path.dirname(__file__), "sqldata/esgf_dashboard.sql"), "r").read())

    #TODO: A bit of a hack; the CoG setup in an index node has a dependency on the esgf_security schema;
    # thus the index node has a dependency on the IDP node.  First attempt to decouple
    #Will eventually be handled in the ClI enforcing valid node types (i.e. index+idp)
    # node_type = esg_functions.get_node_type()
    # if "INDEX" in node_type:
    #     cur.execute(open(os.path.join(os.path.dirname(__file__), "sqldata/esgf_security.sql"), "r").read())


    load_esgf_data(cur)
    cur.close()
    conn.close()

def load_esgf_data(cur):
    '''# load ESGF data
    su --login - postgres --command "psql esgcet < /usr/local/bin/esgf_security_data.sql"
    cur.execute(open(os.path.join(os.path.dirname(__file__), "sqldata/esgf_security_data.sql"), "r").read())

    # initialize migration table
    su --login - postgres --command "psql esgcet < /usr/local/bin/esgf_migrate_version.sql"'''
    cur.execute(open(os.path.join(os.path.dirname(__file__), "sqldata/esgf_migrate_version.sql"), "r").read())


def backup_db(db_name, user_name, backup_dir="/etc/esgf_db_backup"):
    '''Backup database to directory specified by backup_dir'''
    backup_existing_db = esg_property_manager.get_property("backup.database")
    if not backup_existing_db or backup_existing_db.lower() not in ["yes", "y", "n", "no"]:
        backup_db_input = raw_input("Do you want to backup the current database? [Y/n]: ")
        if backup_db_input.lower() in ["n", "no"]:
            logger.info("Skipping backup database.")
            return

    pybash.mkdir_p(backup_dir)
    try:
        conn = connect_to_db(db_name, user_name)
        cur = conn.cursor()
        tables = list_tables(conn)
        for table in tables:
            # cur.execute('SELECT x FROM t')
            backup_file = os.path.join(backup_dir, '{table}_backup_{date}.sql'.format(
                table=table, date=str(datetime.date.today())))
            cur.execute("SELECT * FROM %s" % (table))
            backup_file_object = open(backup_file, 'w')
            for row in cur:
                backup_file_object.write("insert into t values (" + str(row) + ");")
            backup_file_object.close()
    except psycopg2.DatabaseError, error:
        print 'Error %s' % error
        sys.exit(1)
    finally:
        if conn:
            conn.close()

#----------------------------------------------------------
# Postgresql connections functions
#----------------------------------------------------------

def build_connection_string(user, db_name=None, host=None, password=None):
    '''Creates the db connection string using the params as options '''
    db_connection_string = ["user={user}".format(user=user)]
    if db_name:
        db_connection_string.append("dbname={db_name}".format(db_name=db_name))
    if host:
        db_connection_string.append("host={host}".format(host=host))
    if password:
        db_connection_string.append("password={password}".format(password=password))

    return " ".join(db_connection_string)


def connect_to_db(user, db_name=None, host="/tmp", password=None):
    ''' Connect to database '''
    # Using password auth currently;
    # if the user is postgres, the effective user id (euid) needs to be postgres' user id.
    # Essentially change user from root to postgres
    root_id = pwd.getpwnam("root").pw_uid
    if user == "postgres":
        postgres_id = pwd.getpwnam("postgres").pw_uid

        os.seteuid(postgres_id)
    db_connection_string = build_connection_string(user, db_name, host, password)
    try:
        conn = psycopg2.connect(db_connection_string)
        logger.debug("Connected to %s database as user '%s'", db_name, user)
        if not conn:
            print "Failed to connect to {db_name}".format(db_name=db_name)
            raise Exception

        # Set effective user id (euid) back to root
        if os.geteuid() != root_id:
            os.seteuid(root_id)

        return conn
    except Exception:
        logger.exception("Unable to connect to the database.")
        raise

#----------------------------------------------------------
# Postgresql user/group management functions
#----------------------------------------------------------
def create_pg_pass_file():
    '''Creates the file to store login passwords for psql'''
    pg_pass_file_path = os.path.join(os.environ["HOME"], ".pgpass")
    with open(pg_pass_file_path, "w") as pg_pass_file:
        pg_pass_file.write('localhost:5432:cogdb:dbsuper:password')
        pg_pass_file.write('localhost:5432:esgcet:dbsuper:password')
    os.chmod(pg_pass_file_path, 0600)



#----------------------------------------------------------
# Postgresql process management functions
#----------------------------------------------------------

def start_postgres():
    ''' Start db '''
    # if the data directory doesn't exist or is empty
    if not os.path.isdir("/var/lib/pgsql/data/") or not os.listdir("/var/lib/pgsql/data/"):
        initialize_postgres()
    esg_functions.stream_subprocess_output("service postgresql start")
    esg_functions.stream_subprocess_output("chkconfig postgresql on")

    sleep(3)
    if postgres_status():
        return True


def stop_postgres():
    '''Stops the postgres server'''
    esg_functions.stream_subprocess_output("service postgresql stop")


def postgres_status():
    '''Checks the status of the postgres server'''
    status = esg_functions.call_subprocess("service postgresql status")
    print "Postgres server status:", status["stdout"]
    if "running" in status["stdout"]:
        return (True, status)
    else:
        return False


def restart_postgres():
    '''Restarts the postgres server'''
    print "Restarting postgres server"
    restart_process = esg_functions.call_subprocess("service postgresql restart")
    if restart_process["returncode"] != 0:
        print "Restart failed."
        print "Error:", restart_process["stderr"]
        sys.exit(1)
    else:
        print restart_process["stdout"]
    sleep(7)
    postgres_status()


#----------------------------------------------------------
# Postgresql configuration management functions
#----------------------------------------------------------

def setup_postgres_conf_file():
    '''Copies postgres.conf file to proper location'''
    shutil.copyfile(os.path.join(os.path.dirname(__file__), "postgres_conf/postgresql.conf"), "/var/lib/pgsql/data/postgresql.conf")
    postgres_user_id = esg_functions.get_user_id("postgres")
    postgres_group_id = esg_functions.get_group_id("postgres")
    os.chown("/var/lib/pgsql/data/postgresql.conf", postgres_user_id, postgres_group_id)

def setup_hba_conf_file():
    '''Modify the pg_hba.conf file for md5 authencation'''
    with open("/var/lib/pgsql/data/pg_hba.conf", "w") as hba_conf_file:
        hba_conf_file.write("local    all             postgres                    ident\n")
        hba_conf_file.write("local    all             all                         md5\n")
        hba_conf_file.write("host     all             all         ::1/128         md5\n")


#----------------------------------------------------------
# Postgresql logging functions
#----------------------------------------------------------

def write_postgress_env():
    '''Write postgres environment properties to /etc/esg.env'''
    esg_property_manager.set_property("PGHOME", "export PGHOME=/usr/bin/postgres", property_file=config["envfile"], section_name="esgf.env", separator="_")
    esg_property_manager.set_property("PGUSER", "export PGUSER={}".format(config["postgress_user"]), property_file=config["envfile"], section_name="esgf.env", separator="_")
    esg_property_manager.set_property("PGPORT", "export PGPORT={}".format(config["postgress_port"]), property_file=config["envfile"], section_name="esgf.env", separator="_")
    esg_property_manager.set_property("PGBINDIR", "export PGBINDIR={}".format(config["postgress_bin_dir"]), property_file=config["envfile"], section_name="esgf.env", separator="_")
    esg_property_manager.set_property("PGLIBDIR", "export PGLIBDIR={}".format(config["postgress_lib_dir"]), property_file=config["envfile"], section_name="esgf.env", separator="_")
    esg_property_manager.set_property("PATH", config["myPATH"], property_file=config["envfile"], section_name="esgf.env", separator="_")
    esg_property_manager.set_property("LD_LIBRARY_PATH", config["myLD_LIBRARY_PATH"], property_file=config["envfile"], section_name="esgf.env", separator="_")

def write_postgress_install_log():
    '''Write postgres version to install manifest'''
    postgres_version_found = esg_functions.call_subprocess("psql --version")["stdout"]
    postgres_version_number = re.search("\d.*", postgres_version_found).group()
    esg_functions.write_to_install_manifest("postgres", config["postgress_install_dir"], postgres_version_number)

#----------------------------------------------------------
# Postgresql informational functions
#
# These functions require that Postgresql be already installed and
# running correctly.
#----------------------------------------------------------


def create_database(database_name, cursor=None):
    '''Create database in postgres'''
    if not cursor:
        conn = connect_to_db("postgres")
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
    try:
        cursor.execute("CREATE DATABASE {};".format(database_name))
    except psycopg2.ProgrammingError, error:
        # Error code reference: https://www.postgresql.org/docs/current/static/errcodes-appendix.html#ERRCODES-TABLE
        if error.pgcode == "42P04":
            logger.info("%s database already exists.  Skipping creation.", database_name)


def postgres_list_db_schemas(conn=None, user_name="postgres", db_name="postgres", password=None):
    '''This prints a list of all schemas known to postgres.'''
    if not conn:
        conn = connect_to_db(user_name, db_name, password=password)
    cur = conn.cursor()
    try:
        cur.execute("select schema_name from information_schema.schemata;")
        schemas = cur.fetchall()
        schema_list = [schema[0] for schema in schemas]
        return schema_list
    except Exception:
        logger.exception("Could not list database schemas")


def postgres_list_schemas_tables(conn=None, user_name="postgres", db_name="postgres"):
    '''List all Postgres tables in all schemas, in the schemaname.tablename format, in the ESGF database'''
    if not conn:
        conn = connect_to_db(user_name, db_name)
    cur = conn.cursor()
    try:
        cur.execute("SELECT schemaname,relname FROM pg_stat_user_tables;")
        schemas_tables = cur.fetchall()
        logger.debug("schemas_tables: %s", schemas_tables)
        return schemas_tables
    except Exception:
        logger.exception("Could not list schema tables")


def postgres_list_dbs(conn=None, user_name="postgres", db_name="postgres"):
    '''This prints a list of all databases known to postgres.'''
    if not conn:
        conn = connect_to_db(user_name, db_name)
    cur = conn.cursor()
    try:
        cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false;")
        databases = cur.fetchall()
        database_list = [database[0] for database in databases]
        return database_list
    except Exception:
        logger.exception("Could not list databases")


def list_users(conn=None, user_name="postgres", db_name="postgres"):
    '''List all users in database'''
    if not conn:
        conn = connect_to_db(user_name, db_name)
    cur = conn.cursor()
    try:
        cur.execute("""SELECT usename FROM pg_user;""")
        users = cur.fetchall()
        user_list = [user[0] for user in users]
        return user_list
    except Exception:
        logger.exception("Could not list users")


def list_roles(conn=None, user_name="postgres", db_name="postgres"):
    '''List all roles'''
    if not conn:
        conn = connect_to_db(user_name, db_name)
    cur = conn.cursor()
    cur.execute("""SELECT rolname FROM pg_roles;""")
    roles = cur.fetchall()
    roles_list = [role[0] for role in roles]
    return roles_list


def list_tables(conn=None, user_name="postgres", db_name="postgres"):
    '''List all tables in current database'''
    if not conn:
        conn = connect_to_db(user_name, db_name)
    cur = conn.cursor()
    cur.execute(
        """SELECT * FROM pg_catalog.pg_tables WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';""")
    tables = cur.fetchall()
    tables_list = [{"schema_name": table[0], "table_name":table[1], "owner": table[2]}
                   for table in tables]
    return tables_list


def postgres_clean_schema_migration(repository_id):
    '''Removes entries from the esgf_migrate_version table if any exist
    where repository_id matches an SQL LIKE to the first argument

    The SQL LIKE strings are generally defined in
    "src/python/esgf/<reponame>/schema_migration/migrate.cfg" in
    each relevant repository.'''
    db_user_password = esg_functions.get_publisher_password()
    conn = connect_to_db(config["postgress_user"], config["node_db_name"], password=db_user_password)
    cur = conn.cursor()

    try:
        cur.execute(
            "select count(*) from esgf_migrate_version where repository_id LIKE '%$%s%'", repository_id)
        results = cur.fetchall()

        if results > 0:
            print "cleaning out schema migration bookeeping for esgf_node_manager..."
            cur.execute(
                "delete from esgf_migrate_version where repository_id LIKE '%$%s%'", repository_id)
    except Exception, error:
        print "error: ", error

class Postgres(PostgresConfig):
    def __init__(self):
        PostgresConfig.__init__(self)
        self.existing_version = None
        self.data_dir = os.path.join(self.postgress_install_dir, "data")
        logger.debug("Initialize a Postgres component")

    def version(self):
        ''' Gets the version of ant using "psql --version" '''
        print "Checking for postgresql >= {} ".format(self.postgress_min_version)
        psql_bin_path = find_executable("psql")
        if psql_bin_path is not None:
            try:
                postgres_version_found = esg_functions.call_subprocess("psql --version")["stdout"]
                self.existing_version = re.search(r"\d.*", postgres_version_found).group()
                print "Found version {}".format(self.existing_version)
                return self.existing_version
            except OSError:
                logger.exception("Unable to check existing Postgres version \n")
        print "None found"
        return None

    def status(self):
        '''Check status of this components installation, return respective code to the Installer'''
        self.existing_version = self.version()
        if self.existing_version is not None:
            if semver.compare(self.existing_version, self.postgress_min_version) >= 0:
                logger.info("Found acceptible Postgres version")
                return install_codes.OK
            logger.info("The Postgres version on the system does not meet the minimum requirements")
            return install_codes.BAD_VERSION
        return install_codes.NOT_INSTALLED

    def install(self):
        '''Called by the Installer if this component needs to be installed'''
        esg_functions.install_header("Postgres")

        esg_functions.stream_subprocess_output(
            "yum -y install postgresql-server.x86_64 postgresql.x86_64 postgresql-devel.x86_64"
        )
        self.initialize()
        self.post_install()

    def post_install(self):
        '''Writes Postgres config to install manifest and env'''
        logger.debug("psql %s %s", find_executable("psql"), self.version())
        esg_property_manager.set_property("db.user", self.postgress_user)
        esg_property_manager.set_property("db.host", self.postgress_host)
        esg_property_manager.set_property("db.port", self.postgress_port)
        esg_property_manager.set_property("db.database", self.db_database)
        esg_property_manager.set_property("PGHOME", "export PGHOME=/usr/bin/postgres", property_file=self.envfile, section_name="esgf.env", separator="_")
        esg_property_manager.set_property("PGUSER", "export PGUSER={}".format(self.postgress_user), property_file=self.envfile, section_name="esgf.env", separator="_")
        esg_property_manager.set_property("PGPORT", "export PGPORT={}".format(self.postgress_port), property_file=self.envfile, section_name="esgf.env", separator="_")
        esg_property_manager.set_property("PGBINDIR", "export PGBINDIR={}".format(self.postgress_bin_dir), property_file=self.envfile, section_name="esgf.env", separator="_")
        esg_property_manager.set_property("PGLIBDIR", "export PGLIBDIR={}".format(self.postgress_lib_dir), property_file=self.envfile, section_name="esgf.env", separator="_")
        esg_property_manager.set_property("PATH", self.myPATH, property_file=self.envfile, section_name="esgf.env", separator="_")
        esg_property_manager.set_property("LD_LIBRARY_PATH", self.myLD_LIBRARY_PATH, property_file=self.envfile, section_name="esgf.env", separator="_")
        esg_functions.write_to_install_manifest("psql", find_executable("psql"), self.version())

    def initialize(self):
        try:
            if os.listdir(self.data_dir):
                logger.info("Data directory already exists. Skipping initialize_postgres().")
                return
        except OSError, error:
            logger.error(error)
        esg_functions.stream_subprocess_output("service postgresql initdb")
        os.chmod(self.data_dir, 0700)
