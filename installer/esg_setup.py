import sys
import os
import subprocess
import re
import shutil
from OpenSSL import crypto
import requests
import socket
import platform
import netifaces
import tld
import grp
import shlex
import hashlib
import urlparse
import datetime
import tarfile
from time import sleep
from esg_exceptions import UnprivilegedUserError, WrongOSError, UnverifiedScriptError
from distutils.spawn import find_executable
from clint.textui import progress
import esg_bash2py
import esg_functions
import esg_bootstrap
import esg_property_manager
import esg_version_manager
import esg_logging_manager
import esg_init
import yaml
import semver

logger = esg_logging_manager.create_rotating_log(__name__)

with open('esg_config.yaml', 'r') as config_file:
    config = yaml.load(config_file)

force_install = False

def check_if_root():
    '''Check to see if the user is root'''
    print "Checking that you have root privileges on %s... " % (socket.gethostname())
    root_check = os.geteuid()
    try:
        if root_check != 0:
            raise UnprivilegedUserError
        logger.debug("Root user found.")
    except UnprivilegedUserError:
        logger.exception("\nMust run this program with root's effective UID\n\n")
        esg_functions.exit_with_error(1)

def check_os():
    '''Check if the operating system on server is Redhat or CentOS;
    returns False Otherwise'''
    print "Checking operating system....."
    release_version = re.search(
        "(centos|redhat)-(\S*)-", platform.platform()).groups()
    logger.debug("Release Version: %s", release_version)
    try:
        if "6" not in release_version[1]:
            raise WrongOSError
    except WrongOSError:
        logger.exception("ESGF can only be installed on versions 6 of Red Hat, CentOS or Scientific Linux x86_64 systems")
        esg_functions.exit_with_error(1)
    else:
        print "Operating System = {OS} {version}".format(OS=release_version[0], version=release_version[1])


def check_prerequisites():
    '''
        Checking for what we expect to be on the system a-priori that we are not going to install or be responsible for
    '''

    check_if_root()

    #----------------------------------------
    print "Checking requisites... "

    # checking for OS, architecture, distribution and version
    check_os()

def create_esg_directories():
    '''Create directories to hold ESGF scripts, config files, and logs'''
    directories_to_check = [config["scripts_dir"], config["esg_backup_dir"], config["esg_tools_dir"],
                            config[
                                "esg_log_dir"], config["esg_config_dir"], config["esg_etc_dir"],
                            config["tomcat_conf_dir"]]
    for directory in directories_to_check:
        if not os.path.isdir(directory):
            esg_bash2py.mkdir_p(directory)
    os.chmod(config["esg_etc_dir"], 0777)

def init_structure():

    create_esg_directories()

    #Create esgf.properties file
    if not os.path.isfile(config["config_file"]):
        esg_bash2py.touch(config["config_file"])

    #--------------
    # Setup variables....
    #--------------

    check_for_my_ip()

def _select_ip_address():
    choice = int(raw_input(""))
    return choice


def _render_ip_address_menu(ip_addresses):
    print "Detected multiple IP addresses bound to this host...\n"
    print "Please select the IP address to use for this installation\n"
    print "\t-------------------------------------------\n"
    for index, ip in enumerate(ip_addresses.iteritems(), 1):
        print "\t %i) %s" % (index, ip)
    print "\t-------------------------------------------\n"


def check_for_my_ip(force_install=False):
    logger.debug("Checking for IP address(es)...")
    matched = 0
    my_ip_address = None
    eth0 = netifaces.ifaddresses(netifaces.interfaces()[1])
    ip_addresses = [ip["addr"] for ip in eth0[netifaces.AF_INET]]

    try:
        esgf_host_ip
    except NameError:
        esgf_host_ip = esg_property_manager.get_property("esgf_host_ip")

    if esgf_host_ip and not force_install:
        logger.info("Using IP: %s", esgf_host_ip)
        return 0

    # We want to make sure that the IP address we have in our config
    # matches one of the IPs that are associated with this host
    for ip in ip_addresses:
        if ip == esgf_host_ip:
            matched += 1

    if matched == 0:
        logger.info(
            "Configured host IP address does not match available IPs...")

    if not esgf_host_ip or force_install or matched == 0:
        if len(ip_addresses) > 1:
            # ask the user to choose...
            while True:
                _render_ip_address_menu(ip_addresses)
                default = 0
                choice = _select_ip_address() or default
                my_ip_address = ip_addresses[choice]
                logger.info("selected address -> %s", my_ip_address)
                break
        else:
            my_ip_address = ip_addresses[0]

    esg_property_manager.write_as_property("esgf_host_ip", my_ip_address)
    esgf_host_ip = esg_property_manager.get_property("esgf_host_ip")
    return esgf_host_ip


def _choose_fqdn(esgf_host):
    if not esgf_host or force_install:
        default_host_name = esgf_host or socket.getfqdn()
        defaultdomain_regex = r"^\w+-*\w*\W*(.+)"
        defaultdomain = re.search(
            defaultdomain_regex, default_host_name).group(1)
        if not default_host_name:
            default_host_name = "localhost.localdomain"
        elif not defaultdomain:
            default_host_name = default_host_name + ".localdomain"

        default_host_name = raw_input("What is the fully qualified domain name of this node? [{default_host_name}]: ".format(
            default_host_name=default_host_name)) or default_host_name
        esgf_host = default_host_name
        logger.info("esgf_host = [%s]", esgf_host)
        esg_property_manager.write_as_property("esgf_host", esgf_host)
    else:
        logger.info("esgf_host = [%s]", esgf_host)
        esg_property_manager.write_as_property("esgf_host", esgf_host)


def _is_valid_password(password_input):
    if not password_input or not str.isalnum(password_input):
        print "Invalid password... "
        return False
    if not password_input or len(password_input) < 6:
        print "Sorry password must be at least six characters :-( "
        return False
    else:
        return True


def _confirm_password(password_input, password_confirmation):
    if password_confirmation == password_input:
        return True
    else:
        print "Sorry, values did not match"
        return False


def _update_admin_password_file(updated_password):
    #TODO: Rename esgf_secret_file to esgf_admin_password_file
    '''Updates the esgf_secret_file'''
    try:
        security_admin_password_file = open(config["esgf_secret_file"], 'w+')
        security_admin_password_file.write(updated_password)
    except IOError:
        logger.exception("Unable to update security_admin_password file: %s", config["esgf_secret_file"])
    finally:
        security_admin_password_file.close()

    if os.path.isfile(config['esgf_secret_file']):
        os.chmod(config['esgf_secret_file'], 0640)
        try:
            os.chown(config['esgf_secret_file'], config[
                     "installer_uid"], esg_functions.get_tomcat_group_id())
        except OSError:
            logger.exception("Unable to change ownership of %s", config["esgf_secret_file"])

    # Use the same password when creating the postgress account
    config["pg_sys_acct_passwd"] = updated_password


def _update_password_files_permissions():
    #TODO: Rename and refactor this function
    if not esg_functions.get_tomcat_group_id():
        esg_functions.add_unix_group(config["tomcat_group"])
    tomcat_group_id = esg_functions.get_tomcat_group_id()

    if not os.path.isfile(config['pg_secret_file']):
        try:
            with open(config['pg_secret_file'], "w") as secret_file:
                secret_file.write(config["pg_sys_acct_passwd"])
        except IOError:
            logger.exception("Could not open %s", config['pg_secret_file'])
    else:
        os.chmod(config['pg_secret_file'], 0640)
        try:
            os.chown(config['pg_secret_file'], config[
                     "installer_uid"], tomcat_group_id)
        except OSError:
            logger.exception("Unable to change ownership of %s", config["pg_secret_file"])


def _choose_admin_password():
    while True:
        password_input = raw_input(
            "What is the admin password to use for this installation? (alpha-numeric only): ")

        security_admin_password = esg_functions.get_security_admin_password()
        if password_input == security_admin_password:
            changed = False
            break
        if not _is_valid_password(password_input):
            continue
        if password_input:
            security_admin_password = password_input

        security_admin_password_confirmation = raw_input(
            "Please re-enter password to confirm: ")
        if _confirm_password(security_admin_password, security_admin_password_confirmation):
            changed = True
            break

    if changed is True:
        _update_admin_password_file(password_input)

    _update_password_files_permissions()


def _choose_organization_name():
    esg_root_id = esg_property_manager.get_property("esg_root_id")
    if esg_root_id:
        logger.info("esg_root_id = [%s]", esg_root_id)
        return
    elif force_install:
        try:
            default_org_name = tld.get_tld(
                "http://" + socket.gethostname(), as_object=True).domain
        except tld.exceptions.TldDomainNotFound, error:
            logger.exception("Could not find top level domain for %s.", socket.gethostname())
            default_org_name = "llnl"
        while True:
            org_name_input = raw_input("What is the name of your organization? [{default_org_name}]: ".format(default_org_name=default_org_name)) or default_org_name
            org_name_input.replace("", "_")
            esg_property_manager.write_as_property("esg_root_id", org_name_input)
            break

def _choose_node_short_name():
    node_short_name = esg_property_manager.get_property("node_short_name")
    if not node_short_name or force_install:
        while True:
            node_short_name_input = raw_input("Please give this node a \"short\" name [{node_short_name}]: ".format(node_short_name=node_short_name)) or node_short_name
            node_short_name_input.replace("", "_")
            esg_property_manager.write_as_property(
                "node_short_name", node_short_name_input)
            break
    else:
        logger.info("node_short_name = [%s]", node_short_name)


def _choose_node_long_name():
    node_long_name = esg_property_manager.get_property("node_long_name")
    if not node_long_name or force_install:
        while True:
            node_long_name_input = raw_input("Please give this node a more descriptive \"long\" name [{node_long_name}]: ".format(
                node_long_name=node_long_name)) or node_long_name
            esg_property_manager.write_as_property(
                "node_long_name", node_long_name_input)
            break
    else:
        logger.info("node_long_name = [%s]", node_long_name)


def _choose_node_namespace():
    node_namespace = esg_property_manager.get_property("node_namespace")
    if not node_namespace or force_install:
        try:
            top_level_domain = tld.get_tld(
                "http://" + socket.gethostname(), as_object=True)
            domain = top_level_domain.domain
            suffix = top_level_domain.suffix
            default_node_namespace = suffix + "." + domain
        except tld.exceptions.TldDomainNotFound, error:
            top_level_domain = None
            default_node_namespace = None
        while True:
            node_namespace_input = raw_input("What is the namespace to use for this node? (set to your reverse fqdn - Ex: \"gov.llnl\") [{default_node_namespace}]: ".format(
                default_node_namespace=default_node_namespace)) or default_node_namespace
            namespace_pattern_requirement = re.compile("(\w+.{1}\w+)$")
            if not namespace_pattern_requirement.match(node_namespace_input):
                print "Namespace entered is not in a valid format.  Valid format is [suffix].[domain].  Example: gov.llnl"
                continue
            else:
                esg_property_manager.write_as_property(
                    "node_namespace", node_namespace_input)
                break
    else:
        logger.info("node_namespace = [%s]", node_namespace)


def _choose_node_peer_group():
    node_peer_group = esg_property_manager.get_property("node_peer_group")
    if node_peer_group:
        logger.info("node_peer_group = [%s]", node_peer_group)
        return
    if not node_peer_group or force_install:
        try:
            node_peer_group
        except NameError:
            node_peer_group = "esgf-dev"
        while True:
            print "Only choose esgf-test for test federation install or esgf-prod for production installation.  Otherwise choose esgf-dev."
            node_peer_group_input = raw_input(
                "What peer group(s) will this node participate in? (esgf-test|esgf-prod|esgf-dev) [{node_peer_group}]: ".format(node_peer_group=node_peer_group)) or node_peer_group
            if node_peer_group_input.strip() not in ["esgf-test", "esgf-prod", "esgf-dev"]:
                print "Invalid Selection: {node_peer_group_input}".format(node_peer_group_input=node_peer_group_input)
                print "Please choose either esgf-test, esgf-dev, or esgf-prod"
                continue
            else:
                esg_property_manager.write_as_property(
                    "node_peer_group", node_peer_group_input)
                break

def _choose_esgf_index_peer():
    esgf_index_peer = esg_property_manager.get_property("esgf_index_peer")
    esgf_default_peer = esg_property_manager.get_property("esgf_default_peer")
    esgf_host = esg_property_manager.get_property("esgf_host")
    if not esgf_index_peer or force_install:
        default_esgf_index_peer = esgf_default_peer or esgf_host or socket.getfqdn()
        esgf_index_peer_input = raw_input("What is the hostname of the node do you plan to publish to? [{default_esgf_index_peer}]: ".format(
            default_esgf_index_peer=default_esgf_index_peer)) or default_esgf_index_peer
        esg_property_manager.write_as_property(
            "esgf_index_peer", esgf_index_peer_input)
    else:
        logger.info("esgf_index_peer = [%s]", esgf_index_peer)


def _choose_mail_admin_address():
    mail_admin_address = esg_property_manager.get_property("mail_admin_address")
    if not mail_admin_address or force_install:
        mail_admin_address_input = raw_input(
            "What email address should notifications be sent as? [{mail_admin_address}]: ".format(mail_admin_address=mail_admin_address))
        if mail_admin_address_input:
            esg_property_manager.write_as_property(
                "mail_admin_address", mail_admin_address_input)
        else:
            print " (The notification system will not be enabled without an email address)"
    else:
        logger.info("mail_admin_address = [%s]", mail_admin_address)


def _choose_publisher_db_user():
    default_publisher_db_user = None
    publisher_db_user = esg_property_manager.get_property("publisher_db_user")
    if publisher_db_user:
        print "Found existing value for property publisher_db_user: {publisher_db_user}".format(publisher_db_user=publisher_db_user)
        logger.info("publisher_db_user: %s", publisher_db_user)
        return
    if not publisher_db_user or force_install:
        default_publisher_db_user = publisher_db_user or "esgcet"
        publisher_db_user_input = raw_input(
            "What is the (low privilege) db account for publisher? [{default_publisher_db_user}]: ".format(default_publisher_db_user=default_publisher_db_user)) or default_publisher_db_user
        esg_property_manager.write_as_property(
            "publisher_db_user", publisher_db_user_input)

def _choose_publisher_db_user_passwd():
    if config["publisher_db_user_passwd"]:
        print "Using previously configured publisher DB password"
        return
    if not config["publisher_db_user_passwd"] or force_install:
        publisher_db_user = esg_property_manager.get_property("publisher_db_user") or "esgcet"
        publisher_db_user_passwd_input = raw_input(
            "What is the db password for publisher user ({publisher_db_user})?: ".format(publisher_db_user=publisher_db_user))
        if publisher_db_user_passwd_input:
            with open(config['pub_secret_file'], "w") as secret_file:
                secret_file.write(publisher_db_user_passwd_input)

    # if not os.path.isfile(config['pub_secret_file']):
    #     esg_bash2py.touch(config['pub_secret_file'])
    #     with open(config['pub_secret_file'], "w") as secret_file:
    #         secret_file.write(config[
    #                           "publisher_db_user_passwd"])

def get_db_properties():
    db_properties_dict = {"db_user": None, "db_host": None,
                          "db_port": None, "db_database": None, "db_managed": None}
    for key, _ in db_properties_dict.items():
        db_properties_dict[key] = esg_property_manager.get_property(key)

    return db_properties_dict

def initial_setup_questionnaire():
    print "-------------------------------------------------------"
    print 'Welcome to the ESGF Node installation program! :-)'
    print "-------------------------------------------------------"

    esg_bash2py.mkdir_p(config['esg_config_dir'])

    starting_directory = os.getcwd()

    os.chdir(config['esg_config_dir'])

    esgf_host = esg_property_manager.get_property("esgf_host")
    _choose_fqdn(esgf_host)

    if not esg_functions.get_security_admin_password() or force_install:
        _choose_admin_password()

    _choose_organization_name()
    _choose_node_short_name()
    _choose_node_long_name()
    _choose_node_namespace()
    _choose_node_peer_group()
    _choose_esgf_index_peer()
    _choose_mail_admin_address()

    #TODO:Extract constructring DB string into separate function
    db_properties = get_db_properties()

    if not all(db_properties) or force_install:
        _is_managed_db(db_properties)
        _get_db_conn_str_questionnaire(db_properties)
    else:
        if db_properties["db_host"] == esgf_host or db_properties["db_host"] == "localhost":
            print "db_connection_string = {db_user}@localhost".format(db_user=db_properties["db_user"])
        else:
            connstring_ = "{db_user}@{db_host}:{db_port}/{db_database} [external = ${db_managed}]".format(db_user=db_properties["db_user"],
                                                                                                          db_host=db_properties[
                                                                                                              "db_host"],
                                                                                                          db_port=db_properties[
                                                                                                              "db_port"],
                                                                                                          db_database=db_properties[
                                                                                                              "db_database"],
                                                                                                          db_managed=db_properties["db_managed"])

    _choose_publisher_db_user()
    _choose_publisher_db_user_passwd()

    os.chmod(config['pub_secret_file'], 0640)
    if "tomcat" not in esg_functions.get_group_list():
        esg_functions.add_unix_group(config["tomcat_group"])
    os.chown(config['esgf_secret_file'], config[
             "installer_uid"], esg_functions.get_tomcat_group_id())

    if db_properties["db_host"] == esgf_host or db_properties["db_host"] == "localhost":
        logger.info("db publisher connection string %s@localhost",
                    db_properties["db_user"])
    else:
        logger.info("db publisher connection string %s@%s:%s/%s",
                    db_properties["db_user"], db_properties["db_host"], db_properties["db_port"], db_properties["db_database"])

    os.chdir(starting_directory)

    return True


def _get_db_conn_str_questionnaire(db_properties):
    # postgresql://esgcet@localhost:5432/esgcet
    user_ = None
    host_ = None
    port_ = None
    dbname_ = None
    connstring_ = None
    valid_connection_string = None

    # Note the values referenced here should have been set by prior get_property *** calls
    # that sets these values in the script scope. (see the call in
    # questionnaire function - above)
    esgf_host = esg_property_manager.get_property("esgf_host")
    if not db_properties["db_user"] or not db_properties["db_host"] or not db_properties["db_port"] or not db_properties["db_database"]:
        if not db_properties["db_host"]:
            if db_properties["db_host"] == esgf_host or db_properties["db_host"] == "localhost":
                connstring_ = "{db_user}@localhost"
            else:
                connstring_ = "{db_user}@{db_host}:{db_port}/{db_database}"
    while True:
        print "Please enter the database connection string..."
        print " (form: postgresql://[username]@[host]:[port]/esgcet)"
        db_managed = esg_property_manager.get_property("db_managed")
        #(if it is a not a force install and we are using a LOCAL (NOT MANAGED) database then db_managed == "no")
        if not connstring_ and db_managed != "yes" and not force_install:
            connstring_ = "dbsuper@localhost:5432/esgcet"
        db_connection_input = raw_input(
            "What is the database connection string? [postgresql://${connstring_}]: postgresql://".format(connstring_=connstring_)) or connstring_
        parsed_db_conn_string = urlparse.urlparse(db_connection_input)
        # result.path[1:] is database name
        if not parsed_db_conn_string.username or not parsed_db_conn_string.hostname or parsed_db_conn_string.port or parsed_db_conn_string.result.path[1:]:
            logger.error("ERROR: Incorrect connection string syntax or values")
            valid_connection_string = False
        else:
            valid_connection_string = True
            break
    logger.debug("user = %s", user_)
    logger.debug("host = %s", host_)
    logger.debug("port = %s", port_)
    logger.debug("database = %s", dbname_)

    # write vars to property file
    esg_property_manager.write_as_property("db_user", user_)
    esg_property_manager.write_as_property("db_host", host_)
    esg_property_manager.write_as_property("db_port", port_)
    esg_property_manager.write_as_property("db_database", dbname_)

    logger.debug("valid_connection_string: %s",  valid_connection_string)
    return valid_connection_string


def _is_managed_db(db_properties):
    '''
        responds true (returns 0) if this IS intended to be a managed database
        is expecting the vars:
        ---- "db_host"
        ---- "esgf_host"
        to be set
        Define: managed - (true|0) this means NOT manipulated by this script but done by external means
        (hint prepend "externally" before managed to get the meaning - yes I find it confusing but Stephen likes this term :-))
        db_managed=no means that it is a LOCAL database. (I have to change this damn verbiage... what I get for following pasco-speak ;-).
    '''
    db_managed_default = None
    default_selection_output = None
    db_managed = esg_property_manager.get_property("db_managed")
    if not force_install:
        if db_managed == "yes":
            return True
        else:
            return False

    if not db_managed:
        esgf_host = esg_property_manager.get_property("esgf_host")
        logger.debug("esgf_host = %s", esgf_host)
        logger.debug("db_host = %s", db_properties["db_host"])

        # Try to come up with some "sensible" default value for the user...
        if db_properties["db_host"] == esgf_host or db_properties["db_host"] == "localhost" or not db_properties["db_host"]:
            db_managed_default = "no"
            default_selection_output = "[y/N]:"
        else:
            db_managed_default = "yes"
            default_selection_output = "[Y/n]:"

        external_db_input = raw_input(
            "Is the database external to this node? " + default_selection_output)
        if not external_db_input:
            db_managed = db_managed_default
            esg_property_manager.write_as_property("db_managed", db_managed)
        else:
            if external_db_input.lower() == "y" or external_db_input.lower() == "yes":
                db_managed == "yes"
            else:
                db_managed == "no"
            esg_property_manager.write_as_property("db_managed", db_managed)
    else:
        logger.info("db_managed = [%s]", db_managed)

    if db_managed == "yes":
        print "Set to use externally \"managed\" database on host: {db_host}".format(db_host=db_properties["db_host"])
        return True
    else:
        logger.debug("(hmm... setting db_host to localhost)")
        # Note: if not managed and on the local machine... always use
        # "localhost"
        db_properties["db_host"] = "localhost"
        return False


def install_prerequisites():
    '''
        Install prerequisite modules via yum
    '''
    print '''
    *******************************
    Installing prerequisites from yum
    *******************************
    '''
    yum_remove_rpm_forge = subprocess.Popen(
        ["yum", "-y", "remove", "rpmforge-release"], stdout=subprocess.PIPE)
    esg_functions.stream_subprocess_output(yum_remove_rpm_forge)

    yum_install_epel = subprocess.Popen(
        ["yum", "-y", "install", "epel-release"], stdout=subprocess.PIPE)
    esg_functions.stream_subprocess_output(yum_install_epel)

    yum_install_list = ["yum", "-y", "install", "yum-plugin-priorities", "sqlite-devel", "freetype-devel", "git", "curl-devel",
                        "autoconf", "automake", "bison", "file", "flex", "gcc", "gcc-c++",
                        "gettext-devel", "libtool", "uuid-devel", "libuuid-devel", "libxml2",
                        "libxml2-devel", "libxslt", "libxslt-devel", "lsof", "make",
                        "openssl-devel", "pam-devel", "pax", "readline-devel", "tk-devel",
                        "wget", "zlib-devel", "perl-Archive-Tar", "perl-XML-Parser",
                        "libX11-devel", "libtool-ltdl-devel", "e2fsprogs-devel", "gcc-gfortran",
                        "libicu-devel", "libgtextutils-devel", "httpd,"" httpd-devel",
                        "mod_ssl", "libjpeg-turbo-devel", "myproxy", '*ExtUtils*']

    esg_functions.stream_subprocess_output(" ".join(yum_install_list))

def check_for_existing_java():
    '''Check if a valid java installation is currently on the system'''
    java_path = find_executable("java", os.path.join(config["java_install_dir"],"bin"))
    if java_path:
        print "Detected an existing java installation at {java_path}...".format(java_path=java_path)
        java_version_stdout = esg_functions.call_subprocess("{java_path} -version".format(java_path=java_path))
        print java_version_stdout["stderr"]
        installed_java_version = re.search("1.8.0_\w+", java_version_stdout["stderr"]).group()
        if esg_version_manager.compare_versions(installed_java_version, config["java_version"]):
            print "Installed java version meets the minimum requirement "
        return java_version_stdout["stderr"]

def check_java_version(java_path):
    try:
        return esg_functions.call_subprocess("{java_path} -version".format(java_path=java_path))["stderr"]
    except KeyError:
        logger.exception("Could not check the Java version")
        esg_functions.exit_with_error(1)

def download_java(java_tarfile):
    print "Downloading Java from ", config["java_dist_url"]
    if not esg_functions.download_update(java_tarfile, config["java_dist_url"], force_install):
        logger.error("ERROR: Could not download Java")
        esg_functions.exit_with_error(1)

def setup_java():
    '''
        Installs Oracle Java from rpm using yum localinstall.  Does nothing if an acceptible Java install is found.
    '''

    print "*******************************"
    print "Setting up Java {java_version}".format(java_version=config["java_version"])
    print "******************************* \n"

    if force_install:
        pass
    if check_for_existing_java():
            setup_java_answer = raw_input("Do you want to continue with Java installation and setup? [y/N]: ") or "N"
            if setup_java_answer.lower().strip() not in ["y", "yes"]:
                print "Skipping Java installation"
                return
            last_java_truststore_file = esg_functions.readlinkf(config["truststore_file"])

    esg_bash2py.mkdir_p(config["workdir"])
    with esg_bash2py.pushd(config["workdir"]):

        java_tarfile = esg_bash2py.trim_string_from_head(config["java_dist_url"])
        jdk_directory = java_tarfile.split("-")[0]
        java_install_dir_parent = config["java_install_dir"].rsplit("/",1)[0]

        #Check for Java tar file
        if not os.path.isfile(java_tarfile):
            print "Don't see java distribution file {java_dist_file_path} either".format(java_dist_file_path=os.path.join(os.getcwd(),java_tarfile))
            download_java(java_tarfile)

        print "Extracting Java tarfile", java_tarfile
        esg_functions.extract_tarball(java_tarfile, java_install_dir_parent)

        #Create symlink to Java install directory (/usr/local/java)
        esg_bash2py.symlink_force(os.path.join(java_install_dir_parent, jdk_directory), config["java_install_dir"])

        os.chown(config["java_install_dir"], config["installer_uid"], config["installer_gid"])
        #recursively change permissions
        esg_functions.change_permissions_recursive(config["java_install_dir"], config["installer_uid"], config["installer_gid"])

    print check_java_version("{java_install_dir}/bin/java".format(java_install_dir=config["java_install_dir"]))

def setup_ant():
    '''
        Install ant via yum.
    '''

    print "\n*******************************"
    print "Setting up Ant"
    print "******************************* \n"

    if os.path.exists(os.path.join("/usr", "bin", "ant")):
        logger.info("Found existing Ant installation.  Skipping set up.")
        esg_functions.stream_subprocess_output("ant -version")
        force_ant_install = raw_input("Do you want to continue with the Ant installation [y/N]: ") or "no"
        if force_ant_install.lower() in ["n", "no"]:
            return

    esg_functions.stream_subprocess_output("yum -y install ant")


def download_conda(CDAT_HOME="/usr/local/conda"):
    print "\n*******************************"
    print "Downloading Miniconda"
    print "******************************* \n"
    with esg_bash2py.pushd("/tmp"):
        conda_url = "https://repo.continuum.io/miniconda/Miniconda2-latest-Linux-x86_64.sh"
        r = requests.get(conda_url, stream=True)
        path = '/tmp/Miniconda2-latest-Linux-x86_64.sh'
        with open(path, 'wb') as f:
            total_length = int(r.headers.get('content-length'))
            for chunk in progress.bar(r.iter_content(chunk_size=1024), expected_size=(total_length/1024) + 1):
                if chunk:
                    f.write(chunk)
                    f.flush()
        install_conda(CDAT_HOME)
        create_conda_env(CDAT_HOME=CDAT_HOME)



def install_conda(CDAT_HOME="/usr/local/conda"):
    esg_functions.stream_subprocess_output("bash Miniconda2-latest-Linux-x86_64.sh -b -p {CDAT_HOME}".format(CDAT_HOME=CDAT_HOME))
    sys.path.append(os.path.join(CDAT_HOME, "bin"))


def create_conda_env(CDAT_HOME="/usr/local/conda"):
    esg_functions.stream_subprocess_output("{CDAT_HOME}/bin/conda create -y -n esgf-pub -c conda-forge -c uvcdat cdutil".format(CDAT_HOME=CDAT_HOME))
    #Test by trying to import cdms2
    #Absolutely needs to be in a conda env
    #Have bash script to activate conda then kick off installation
    #Ask Sasha what all he needs from uvcdat for Publisher

def setup_cdat():
    print "Checking for *UV* CDAT (Python+CDMS) {cdat_version} ".format(cdat_version=config["cdat_version"])
    try:
        sys.path.insert(0, os.path.join(
            config["cdat_home"], "bin", "python"))
        import cdat_info
        import cdms2
        #if semver.match(cdat_info.Version, ">="+config["cdat_version"])
        if esg_version_manager.check_version_atleast(cdat_info.Version, config["cdat_version"]) == 0 and not force_install:
            print "CDAT already installed [OK]"
            return True
    except ImportError:
        logger.exception("Unable to import cdms2")

    print "\n*******************************"
    print "Setting up CDAT - (Python + CDMS)... {cdat_version}".format(cdat_version=config["cdat_version"])
    print "******************************* \n"


    if os.access(os.path.join(config["cdat_home"], "bin", "uvcdat"), os.X_OK):
        print "Detected an existing CDAT installation..."
        cdat_setup_choice = raw_input(
            "Do you want to continue with CDAT installation and setup? [y/N] ")
        if cdat_setup_choice.lower().strip() not in ["y", "yes"]:
            print "Skipping CDAT installation and setup - will assume CDAT is setup properly"
            return True

    return True
