import os
import subprocess
import sys
import logging
import socket
import platform
import glob
import shutil
import errno
import filecmp
import ConfigParser
import stat
import yaml
import semver
import pip
from git import Repo
#This needs to be imported before other esg_* modules to properly setup the root logger
from esgf_utilities import esg_logging_manager
from esgf_utilities import esg_functions
from esgf_utilities import esg_bash2py
from base import esg_setup
from base import esg_postgres
from base import esg_java
from data_node import esg_publisher
from esgf_utilities import esg_cli_argument_manager
from base import esg_tomcat_manager
from esgf_utilities import esg_version_manager
from esgf_utilities import esg_mirror_manager
from base import esg_apache_manager
from esgf_utilities import esg_property_manager
from esgf_utilities import esg_questionnaire
from esgf_utilities import esg_cert_manager
from esgf_utilities.esg_exceptions import UnprivilegedUserError, WrongOSError, UnverifiedScriptError
from filters import access_logging_filters, esg_security_tokenless_filters


logger = logging.getLogger("esgf_logger" +"."+ __name__)

with open(os.path.join(os.path.dirname(__file__), 'esg_config.yaml'), 'r') as config_file:
    config = yaml.load(config_file)

force_install = False

#--------------
# User Defined / Settable (public)
#--------------
#--------------

def setup_esg_config_permissions():
    '''Set permissions on /esg directory and subdirectories'''
    esg_functions.change_permissions_recursive("/esg/config", 0644)

    root_id = esg_functions.get_user_id("root")
    tomcat_group_id = esg_functions.get_user_id("tomcat")
    for file_name in  glob.glob("/esg/config/.esg*"):
        os.chown(file_name, root_id, tomcat_group_id)
        os.chmod(file_name, 0640)

    tomcat_user_id = esg_functions.get_user_id("tomcat")
    esg_functions.change_ownership_recursive("/esg/config/tomcat", tomcat_user_id, tomcat_group_id)
    os.chmod("/esg/config/tomcat", 0755)
    for file_name in  glob.glob("/esg/config/tomcat/*"):
        os.chmod(file_name, 0600)

    os.chmod("/esg/config/esgcet", 0755)
    for file_name in  glob.glob("/esg/config/esgcet/*"):
        os.chmod(file_name, 0644)
    os.chmod("/esg/config/esgcet/esg.ini", 640)


def esgf_node_info():
    '''Print basic info about ESGF installation'''
    with open(os.path.join(os.path.dirname(__file__), 'docs', 'esgf_node_info.txt'), 'r') as info_file:
        print info_file.read()

def set_esg_dist_url(install_type):
    esgf_dist_mirrors_list = ("http://distrib-coffee.ipsl.jussieu.fr/pub/esgf/dist", "http://dist.ceda.ac.uk/esgf/dist", "http://aims1.llnl.gov/esgf/dist", "http://esg-dn2.nsc.liu.se/esgf/dist", "https://distrib-coffee.ipsl.jussieu.fr/pub/esgf/dist", "https://dist.ceda.ac.uk/esgf/dist", "https://aims1.llnl.gov/esgf/dist", "https://esg-dn2.nsc.liu.se/esgf/dist")

    try:
        local_mirror = esg_property_manager.get_property("local_mirror")
    except ConfigParser.NoOptionError:
        pass
    else:
        if local_mirror.lower() in ["y", "yes"]:
            logger.debug("Using local mirror %s", local_mirror)
            esg_property_manager.set_property("esg.dist.url", local_mirror)
            return

    try:
        if esg_property_manager.get_property("esg.dist.url") in esgf_dist_mirrors_list:
            esg_property_manager.set_property("esg.mirror.url", esg_property_manager.get_property("esg.dist.url").rsplit("/",1)[0])
            return
        elif esg_property_manager.get_property("esg.dist.url") == "fastest":
            esg_property_manager.set_property("esg.dist.url", esg_mirror_manager.find_fastest_mirror(install_type))
            esg_property_manager.set_property("esg.mirror.url", esg_property_manager.get_property("esg.dist.url").rsplit("/",1)[0])
        else:
            selected_mirror = esg_mirror_manager.select_dist_mirror()
            esg_property_manager.set_property("esg.dist.url", selected_mirror)
            esg_property_manager.set_property("esg.mirror.url", esg_property_manager.get_property("esg.dist.url").rsplit("/",1)[0])
    except ConfigParser.NoOptionError:
        selected_mirror = esg_mirror_manager.select_dist_mirror()
        esg_property_manager.set_property("esg.dist.url", selected_mirror)
        esg_property_manager.set_property("esg.mirror.url", esg_property_manager.get_property("esg.dist.url").rsplit("/",1)[0])

def download_esg_installarg(esg_dist_url):
    ''' Downloading esg-installarg file '''
    if not os.path.isfile(config["esg_installarg_file"]) or force_install or os.path.getmtime(config["esg_installarg_file"]) < os.path.getmtime(os.path.realpath(__file__)):
        esg_installarg_file_name = esg_bash2py.trim_string_from_head(
            config["esg_installarg_file"])
        esg_functions.download_update(config["esg_installarg_file"], os.path.join(
            esg_dist_url, "esgf-installer", esg_installarg_file_name), force_download=force_install)
        try:
            if not os.path.getsize(config["esg_installarg_file"]) > 0:
                os.remove(config["esg_installarg_file"])
            esg_bash2py.touch(config["esg_installarg_file"])
        except IOError:
            logger.exception("Unable to access esg-installarg file")


def check_selected_node_type(node_types, node_type_list):
    ''' Make sure a valid node_type has been selected before performing and install '''
    for option in node_type_list:
        logger.debug("option: %s", option)
        if option.upper() in node_types:
            continue
        else:
            print '''
                Sorry no suitable node type has been selected
                Please run the script again with --set-type and provide any number of type values (\"data\", \"index\", \"idp\", \"compute\" [or \"all\"]) you wish to install
                (no quotes - and they can be specified in any combination or use \"all\" as a shortcut)

                Ex:  esg_node.py --set-type data
                esg_node.py install

                or do so as a single command line:

                Ex:  esg_node.py --type data install

                Use the --help | -h option for more information

                Note: The type value is recorded upon successfully starting the node.
                the value is used for subsequent launches so the type value does not have to be
                always specified.  A simple \"esg_node.py start\" will launch with the last type used
                that successfully launched.  Thus ideal for use in the boot sequence (chkconfig) scenario.
                (more documentation available at https://github.com/ESGF/esgf-installer/wiki)\n\n
                  '''
            sys.exit(1)
    return True


def init_connection():
    """ Initialize Connection to node."""
    logger.info("esg-node initializing...")
    try:
        logger.info(socket.getfqdn())
    except socket.error:
        logger.error(
            "Please be sure this host has a fully qualified hostname and reponds to socket.getfdqn() command")
        sys.exit()


def get_installation_type(script_version):
    # Determining if devel or master directory of the ESGF distribution mirror
    # will be use for download of binaries
    if "devel" in script_version:
        logger.debug("Using devel version")
        return "devel"
    else:
        return "master"


def install_log_info(node_type_list):
    if force_install:
        logger.info("(force install is ON)")
    if "DATA" in node_type_list:
        logger.info("(data node type selected)")
    if "INDEX" in node_type_list:
        logger.info("(index node type selected)")
    if "IDP" in node_type_list:
        logger.info("(idp node type selected)")
    if "COMPUTE" in node_type_list:
        logger.info("(compute node type selected)")

def show_summary():
    '''Show user summary and environment variables that have been set'''
    print "-------------------"
    print " esgf_node Run Summary: "
    print "-------------------"

    print "The following environment variables were used during last full install"
    print "They are written to the file {}".format(config["envfile"])
    print "Please source this file when using these tools"

    try:
        with open(config["envfile"], 'r') as env_file:
            print env_file.read()
    except IOError, error:
        logger.exception(error)

    print "-------------------"
    print "Installation Log:"
    try:
        with open(config["install_manifest"], 'r') as env_file:
            print env_file.read()
    except IOError, error:
        logger.exception(error)
    print "-------------------"



def system_component_installation(esg_dist_url, node_type_list):
    '''
    Installation of basic system components.
    (Only when one setup in the sequence is okay can we move to the next)
    '''
    if "INSTALL" in node_type_list:
        esg_java.setup_java()
        esg_java.setup_ant()
        esg_postgres.setup_postgres()
        esg_tomcat_manager.main()
        esg_apache_manager.main()
    if "DATA" in node_type_list:
        print "\n*******************************"
        print "Installing Data Node Components"
        print "******************************* \n"
        pip.main(['install', "esgprep=={}".format(config["esgprep_version"])])
        esg_publisher.main()
        from data_node import esg_dashboard, orp, thredds
        from idp_node import globus
        orp.main()
        thredds.main()
        access_logging_filters.install_access_logging_filter()
        esg_security_tokenless_filters.setup_security_tokenless_filters()
        globus.setup_globus("DATA")
    if "IDP" in node_type_list:
        print "\n*******************************"
        print "Installing IDP Node Components"
        print "******************************* \n"
        from idp_node import idp, esg_security, globus
        idp.main()
        esg_security.setup_security(node_type_list, esg_dist_url)
        globus.setup_globus("IDP")
        idp.setup_slcs()
    esg_cert_manager.install_local_certs("firstrun")
    if "INDEX" in node_type_list:
        print "\n*******************************"
        print "Installing Index Node Components"
        print "******************************* \n"
        if "DATA" not in node_type_list:
            from data_node import orp
            orp.main()
        from index_node import esg_cog, esg_search, solr
        esg_cog.main()
        index_config = config["index_config"].split()
        solr.main(index_config)
        esg_search.main()



def done_remark(node_type_list):
    '''Prints info to denote that the installation has completed'''
    print "\nFinished!..."
    print "In order to see if this node has been installed properly you may direct your browser to:"

    if "DATA" in node_type_list or "INSTALL" in node_type_list:
        esgf_host = esg_functions.get_esgf_host()
        print "http://{esgf_host}/thredds".format(esgf_host=esgf_host)
        print "http://{esgf_host}/esg-orp".format(esgf_host=esgf_host)
    if "INDEX" in node_type_list:
        print "http://{esgf_host}/".format(esgf_host=esgf_host)
    if "COMPUTE" in node_type_list:
        print "http://{esgf_host}/las".format(esgf_host=esgf_host)

    print "Your peer group membership -- :  [{node_peer_group}]".format(node_peer_group=esg_property_manager.get_property("node.peer.group"))
    print "Your specified \"index\" peer - :[{esgf_index_peer}]) (url = http://{esgf_index_peer}/)".format(esgf_index_peer=esg_property_manager.get_property("esgf.index.peer"))

    if os.path.isdir(os.path.join("{thredds_content_dir}".format(thredds_content_dir=config["thredds_content_dir"]), "thredds")):
        print "[Note: Use UNIX group permissions on {thredds_content_dir}/thredds/esgcet to enable users to be able to publish thredds catalogs from data therein]".format(thredds_content_dir=config["thredds_content_dir"])
        print " %> chgrp -R <appropriate unix group for publishing users> {thredds_content_dir}/thredds".format(thredds_content_dir=config["thredds_content_dir"])

    print '''
        -------------------------------------------------------
        Administrators of this node should subscribe to the
        esgf-node-admins@lists.llnl.gov by sending email to: "sasha@llnl.gov"
        with the body: "subscribe esgf-node-admins"
        -------------------------------------------------------
'''

    show_summary()

    try:
        esg_org_name = esg_property_manager.get_property("esg.org.name")
    except Exception:
        logger.exception("esg.org.name could not be found in config file")
    print "(\"Test Project\" -> pcmdi.{esg_org_name}.{node_short_name}.test.mytest)".format(esg_org_name=esg_org_name, node_short_name=esg_property_manager.get_property("node.short.name"))

def setup_esgf_rpm_repo():
    '''Creates the esgf repository definition file'''

    print "*******************************"
    print "Setting up ESGF RPM repository"
    print "******************************* \n"

    esg_mirror_url = esg_property_manager.get_property("esg.mirror.url")

    parser = ConfigParser.SafeConfigParser()
    parser.read("esgf_utilities/esgf.repo")
    os_version = platform.platform()

    if "centos" in os_version:
        parser.set("esgf", "baseurl", "{}/RPM/centos/6/x86_64\n".format(esg_mirror_url))
    elif "redhat" in os_version:
        parser.set("esgf", "baseurl", "{}/RPM/redhat/6/x86_64\n".format(esg_mirror_url))

    with open("/etc/yum.repos.d/esgf.repo", "w") as repo_file_object:
        parser.write(repo_file_object)


def main():
    node_types = ("INSTALL", "DATA", "INDEX", "IDP", "COMPUTE", "ALL")
    script_version, script_maj_version, script_release = esg_version_manager.set_version_info()

    # initialize connection
    init_connection()

    # determine installation type
    install_type = get_installation_type(script_version)

    cli_info = esg_cli_argument_manager.process_arguments()
    logger.debug("cli_info: %s", cli_info)
    if cli_info:
        node_type_list = cli_info

    try:
        devel = bool(esg_property_manager.get_property("devel"))
    except ConfigParser.NoOptionError:
        devel = False


    set_esg_dist_url(install_type)
    esg_dist_url = esg_property_manager.get_property("esg.dist.url")
    download_esg_installarg(esg_dist_url)

    esg_setup.check_prerequisites()

    esg_functions.verify_esg_node_script(os.path.basename(
        __file__), esg_dist_url, script_version, script_maj_version, devel)

    logger.debug("node_type_list: %s", node_type_list)

    print '''
    -----------------------------------
    ESGF Node Installation Program
    -----------------------------------'''
    check_selected_node_type(node_types, node_type_list)

    # Display node information to user
    esgf_node_info()

    if devel:
        print "(Installing DEVELOPMENT tree...)"

    esg_setup.init_structure()

    # log info
    install_log_info(node_type_list)

    esg_questionnaire.initial_setup_questionnaire()

    setup_esgf_rpm_repo()

    # install dependencies
    system_component_installation(esg_dist_url, node_type_list)
    system_launch(esg_dist_url, node_type_list, script_version, script_release)


def sanity_check_web_xmls():
    '''Editing web.xml files for projects who use the authorizationService'''
    print "sanity checking webapps' web.xml files accordingly... "

    with esg_bash2py.pushd("/usr/local/tomcat/webapps"):
        webapps = os.listdir("/usr/local/tomcat/webapps")
        if not webapps:
            return

        instruct_to_reboot = False
        tomcat_user = esg_functions.get_user_id("tomcat")
        tomcat_group = esg_functions.get_group_id("tomcat")

        for app in webapps:
            with esg_bash2py.pushd(os.path.join(app,"WEB-INF")):
                print " |--setting ownership of web.xml files... to ${tomcat_user}.${tomcat_group}"
                os.chown("web.xml", tomcat_user, tomcat_group)

                print " |--inspecting web.xml files for proper authorization service assignment... "
                esg_functions.stream_subprocess_output("sed -i.bak 's@\(https://\)[^/]*\(/esg-orp/saml/soap/secure/authorizationService.htm[,]*\)@\1'{}'\2@g' web.xml".format(esg_functions.get_esgf_host()))
                esg_functions.stream_subprocess_output("sed -i.bak '/<param-name>[ ]*'trustoreFile'[ ]*/,/<\/param-value>/ s#\(<param-value>\)[ ]*[^<]*[ ]*\(</param-value>\)#\1'{}'\2#' web.xml".format(config["truststore_file"]))

                try:
                    if not filecmp.cmp("web.xml", "web.xml.bak", shallow=False):
                        logger.debug("%s/web.xml file was edited. Reboot needed", os.getcwd())
                        instruct_to_reboot = True
                except OSError, error:
                    logger.exception(error)

        if instruct_to_reboot:
            print '''-------------------------------------------------------------------------------------------------
            webapp web.xml files have been modified - you must restart node stack for changes to be in effect
            (esg-node restart)
            -------------------------------------------------------------------------------------------------'''

def clear_tomcat_cache():
    try:
        cache_directories = glob.glob("/usr/local/tomcat/work/Catalina/localhost/*")
        for directory in cache_directories:
            shutil.rmtree(directory)
        print "Cleared tomcat cache... "
    except OSError, error:
        logger.exception(error)

def remove_unused_esgf_webapps():
    '''Hard coded to remove node manager, desktop and dashboard'''
    try:
        shutil.rmtree("/usr/local/tomcat/webapps/esgf-node-manager")
    except OSError, error:
        if error.errno == errno.ENOENT:
            pass

    try:
        shutil.rmtree("/usr/local/tomcat/webapps/esgf-desktop")
    except OSError, error:
        if error.errno == errno.ENOENT:
            pass

    try:
        shutil.rmtree("/usr/local/tomcat/webapps/esgf-dashboard")
    except OSError, error:
        if error.errno == errno.ENOENT:
            pass

def install_bash_completion_file(esg_dist_url):
    if os.path.exists("/etc/bash_completion") and not os.path.exists("/etc/bash_completion.d/esg-node"):
        esg_functions.download_update("/etc/bash_completion.d/esg-node", "{}/esgf-installer/esg-node.completion".format(esg_dist_url))

def write_script_version_file(script_version):
    with open(os.path.join(config["esg_root_dir"], "version"), "w") as version_file:
        version_file.write(script_version)

def system_launch(esg_dist_url, node_type_list, script_version, script_release):
    #---------------------------------------
    #System Launch...
    #---------------------------------------
    sanity_check_web_xmls()
    esg_tomcat_manager.setup_root_app()
    clear_tomcat_cache()
    remove_unused_esgf_webapps()

    esg_functions.update_fileupload_jar()
    esg_functions.setup_whitelist_files()

    esg_cli_argument_manager.start(node_type_list)
    install_bash_completion_file(esg_dist_url)
    done_remark(node_type_list)
    write_script_version_file(script_version)
    print script_version

    esg_property_manager.set_property("version", script_version)
    esg_property_manager.set_property("release", script_release)

    esg_property_manager.set_property("activate_conda", "source /usr/local/conda/bin/activate esgf-pub", config_file=config["envfile"], section_name="esgf.env", separator="_")
    #     write_as_property gridftp_config
    esg_node_finally(node_type_list)

def esg_node_finally(node_type_list):
    global_x509_cert_dir = "/etc/grid-security/certificates"
    esg_functions.change_ownership_recursive(global_x509_cert_dir, config["installer_uid"], config["installer_gid"])

    if "IDP" in node_type_list:
        os.environ["PGPASSWORD"] = esg_functions.get_postgres_password()
        print "Writing additional settings to db.  If these settings already exist, psql will report an error, but ok to disregard."
        # psql -U dbsuper -c "insert into esgf_security.permission values (1, 1, 1, 't'); insert into esgf_security.role values (6, 'user', 'User Data Access');" esgcet
        #     echo "Node installation is complete."


if __name__ == '__main__':
    main()
