'''
Certificate Management Functions
'''
import os
import shutil
import glob
import filecmp
import logging
import socket
import tarfile
import datetime
import re
import ConfigParser
import errno
import requests
import yaml
import jks
import OpenSSL
import esg_bash2py
import esg_functions
import esg_property_manager
from esg_exceptions import SubprocessError


logger = logging.getLogger("esgf_logger" +"."+ __name__)
current_directory = os.path.join(os.path.dirname(__file__))

NO_LIST = ["n", "no", "N", "No", "NO"]
YES_LIST = ["y", "yes", "Y", "Yes", "YES" ]


with open(os.path.join(os.path.dirname(__file__), os.pardir, 'esg_config.yaml'), 'r') as config_file:
    config = yaml.load(config_file)


def convert_hash_to_hex(subject_name_hash):
    '''Converts the subject_name_hash from a long to a hex string'''
    return format(subject_name_hash, 'x')

#------------------------------------
#   Certificate functions
#------------------------------------

def create_self_signed_cert(cert_dir):
    """
    If datacard.crt and datacard.key don't exist in cert_dir, create a new
    self-signed cert and keypair and write them into that directory.

    Source: https://skippylovesmalorie.wordpress.com/2010/02/12/how-to-generate-a-self-signed-certificate-using-pyopenssl/
    """
    CERT_FILE = "hostcert.pem"
    KEY_FILE = "hostkey.pem"

    if not os.path.exists(os.path.join(cert_dir, CERT_FILE)) \
            or not os.path.exists(os.path.join(cert_dir, KEY_FILE)):

        # create a key pair
        k = OpenSSL.crypto.PKey()
        k.generate_key(OpenSSL.crypto.TYPE_RSA, 4096)

        # create a self-signed cert
        cert = OpenSSL.crypto.X509()
        cert.get_subject().C = "US"
        cert.get_subject().ST = "California"
        cert.get_subject().L = "Livermore"
        cert.get_subject().O = "LLNL"
        cert.get_subject().OU = "ESGF"
        cert.get_subject().CN = socket.gethostname()
        cert.set_serial_number(1000)
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(10*365*24*60*60)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.sign(k, 'sha1')

        esg_bash2py.mkdir_p(cert_dir)

        with open(os.path.join(cert_dir, CERT_FILE), "wt") as cert_file_handle:
            cert_file_handle.write(OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_PEM, cert))
        with open(os.path.join(cert_dir, KEY_FILE), "wt") as key_file_handle:
            key_file_handle.write(OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, k))

def create_certificate_chain_list():
    '''Create a list of the certificates that will be a part of the certificate
        chain file'''
    default_cachain = "/etc/esgfcerts/cachain.pem"
    cert_files = []
    #Enter ca_chain file into list
    while True:
        if esg_property_manager.get_property("certfile.entry") in NO_LIST:
            certfile_entry = None
        else:
            print "Please enter your Certificate Authority's certificate chain file(s)"
            print "[enter each cert file/url press return, press return with blank entry when done]"
            certfile_entry = raw_input("Enter certificate chain file name: ")
        if not certfile_entry:
            if not cert_files:
                print "Adding default certificate chain file {default_cachain}".format(default_cachain=default_cachain)
                if os.path.isfile(default_cachain):
                    cert_files.append(default_cachain)
                    break
                else:
                    print "{default_cachain} does not exist".format(default_cachain=default_cachain)
                    print "Creating {default_cachain}".format(default_cachain=default_cachain)
                    esg_bash2py.mkdir_p("/etc/esgfcerts")
                    esg_bash2py.touch(default_cachain)
                    cert_files.append(default_cachain)
                    break
                    # esg_functions.exit_with_error(1)
            else:
                break
        else:
            if os.path.isfile(certfile_entry):
                cert_files.append(certfile_entry)
            else:
                print "{certfile_entry} does not exist".format(certfile_entry=certfile_entry)

    return cert_files


def create_certificate_chain(cert_files):
    '''Concatenate the certificates in the chain and copy them to /etc/certs'''
    print "\n*******************************"
    print "Creating Certificate Chain"
    print "******************************* \n"

    #Copy the tmpchain and rename to cachain
    with open("/etc/certs/tmpchain", "w") as tmpchain_file:
        for cert in cert_files:
            if not os.path.isfile(cert):
                print "{cert} not found. Exiting.".format(cert=cert)
                esg_functions.exit_with_error()

            with open(cert, "r") as cert_file_handle:
                cert_file_contents = cert_file_handle.read()
            tmpchain_file.write(cert_file_contents+"\n")

    shutil.copyfile("/etc/certs/tmpchain", "/etc/certs/cachain.pem")


def fetch_esgf_certificates(globus_certs_dir=config["globus_global_certs_dir"]):
    '''Goes to ESG distribution server and pulls down all certificates for the federation.
    (suitable for crontabbing)'''

    print "\n*******************************"
    print "Fetching freshest ESG Federation Certificates..."
    print "******************************* \n"
    #if globus_global_certs_dir already exists, backup and delete, then recreate empty directory
    if os.path.isdir(config["globus_global_certs_dir"]):
        esg_functions.backup(config["globus_global_certs_dir"], os.path.join(config["globus_global_certs_dir"], ".bak.tz"))
        shutil.rmtree(config["globus_global_certs_dir"])
    esg_bash2py.mkdir_p(config["globus_global_certs_dir"])

    #Download trusted certs tarball
    esg_trusted_certs_file = "esg_trusted_certificates.tar"
    esg_dist_url = esg_property_manager.get_property("esg.dist.url")
    esg_trusted_certs_file_url = "{esg_dist_url}/certs/{esg_trusted_certs_file}".format(esg_dist_url=esg_dist_url, esg_trusted_certs_file=esg_trusted_certs_file)
    esg_functions.download_update(os.path.join(globus_certs_dir,esg_trusted_certs_file), esg_trusted_certs_file_url)

    #untar the esg_trusted_certs_file
    esg_functions.extract_tarball(os.path.join(globus_certs_dir,esg_trusted_certs_file), globus_certs_dir)
    os.remove(os.path.join(globus_certs_dir,esg_trusted_certs_file))

    #certificate_issuer_cert "/var/lib/globus-connect-server/myproxy-ca/cacert.pem"
    simpleCA_cert = "/var/lib/globus-connect-server/myproxy-ca/cacert.pem"
    if os.path.isfile(simpleCA_cert):
        try:
            cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open(simpleCA_cert).read())
        except OpenSSL.crypto.Error:
            logger.exception("Certificate is not correct.")


        simpleCA_cert_hash = convert_hash_to_hex(cert_obj.subject_name_hash())
        print "checking for MY cert: {globus_global_certs_dir}/{simpleCA_cert_hash}.0".format(globus_global_certs_dir=config["globus_global_certs_dir"], simpleCA_cert_hash=simpleCA_cert_hash)
        if os.path.isfile("{globus_global_certs_dir}/{simpleCA_cert_hash}.0".format(globus_global_certs_dir=config["globus_global_certs_dir"], simpleCA_cert_hash=simpleCA_cert_hash)):
            print "Local CA cert file detected...."
            print "Integrating in local simpleCA_cert... "
            print "Local SimpleCA Root Cert: {simpleCA_cert}".format(simpleCA_cert=simpleCA_cert)
            print "Extracting Signing policy"

            #Copy simple CA cert to globus cert directory
            shutil.copyfile(simpleCA_cert, "{globus_global_certs_dir}/{simpleCA_cert_hash}.0".format(globus_global_certs_dir=config["globus_global_certs_dir"], simpleCA_cert_hash=simpleCA_cert_hash))

            #extract simple CA cert tarball and copy to globus cert directory
            simpleCA_cert_parent_dir = esg_functions.get_parent_directory(simpleCA_cert)
            simpleCA_setup_tar_file = os.path.join(simpleCA_cert_parent_dir, "globus_simple_ca_{simpleCA_cert_hash}_setup-0.tar.gz".format(simpleCA_cert_hash=simpleCA_cert_hash))
            esg_functions.extract_tarball(simpleCA_setup_tar_file)

            with esg_bash2py.pushd("globus_simple_ca_{simpleCA_cert_hash}_setup-0".format(simpleCA_cert_hash=simpleCA_cert_hash)):
                shutil.copyfile("{simpleCA_cert_hash}.signing_policy".format(simpleCA_cert_hash=simpleCA_cert_hash), "{globus_global_certs_dir}/{simpleCA_cert_hash}.signing_policy".format(globus_global_certs_dir=config["globus_global_certs_dir"], simpleCA_cert_hash=simpleCA_cert_hash))
            if os.path.isdir("/usr/local/tomcat/webapps/ROOT"):
                esg_functions.stream_subprocess_output("openssl x509 -text -hash -in {simpleCA_cert} > {tomcat_install_dir}/webapps/ROOT/cacert.pem".format(simpleCA_cert=simpleCA_cert, tomcat_install_dir="/usr/loca/tomcat"))
                print " My CA Cert now posted @ http://{fqdn}/cacert.pem ".format(fqdn=socket.getfqdn())
                os.chmod("/usr/local/tomcat/webapps/ROOT/cacert.pem", 0644)

        os.chmod(config["globus_global_certs_dir"], 0755)
        esg_functions.change_permissions_recursive(config["globus_global_certs_dir"], 0644)

def install_extkeytool():
    '''Install the Extkeytool from the distribution mirror'''
    print "\n*******************************"
    print "Installing Extkeytool"
    print "******************************* \n"
    extkeytool_tarfile = esg_bash2py.trim_string_from_head(config["extkeytool_download_url"])
    esg_functions.download_update(extkeytool_tarfile, config["extkeytool_download_url"])
    esg_functions.extract_tarball(extkeytool_tarfile, "/esg/tools/idptools")


def convert_per_to_dem(private_key, key_output_dir):
    '''Convert your private key into from PEM to DER format that java likes'''
    print "\n*******************************"
    print "converting private key from PEM to DER... "
    print "******************************* \n"
    derkey = os.path.join(key_output_dir,"key.der")
    convert_to_der = esg_functions.call_subprocess("openssl pkcs8 -topk8 -nocrypt -inform PEM -in {private_key} -outform DER -out {derkey}".format(private_key=private_key, derkey=derkey))
    if convert_to_der["returncode"] !=0:
        print "Problem with preparing initial keystore...Exiting."
        esg_functions.exit_with_error(convert_to_der["stderr"])
    return derkey

def check_cachain_validity(ca_chain_bundle):
    '''Verify that the CA chain is valid'''
    print "checking that chain is valid... "
    if os.path.isfile(ca_chain_bundle):
        try:
            valid_chain = esg_functions.call_subprocess("openssl verify -CAfile {ca_chain_bundle} {ca_chain_bundle}".format(ca_chain_bundle=ca_chain_bundle))
            if "error" in valid_chain['stdout'] or "error" in valid_chain['stderr']:
                print "The chain is not valid.  (hint: did you include the root cert for the chain?)"
            else:
                print "{ca_chain_bundle} is valid.".format(ca_chain_bundle=ca_chain_bundle)
        except SubprocessError, error:
            print "Error verifying cachain:", error
    else:
        print "Hmmm... no chain provided [{ca_chain_bundle}], skipping this check..."


def bundle_certificates(public_cert, ca_chain, idptools_install_dir):
    '''Create certificate bundle from public cert and cachain'''
    print "\n*******************************"
    print "Bundling Certificates"
    print "******************************* \n"

    cert_bundle = os.path.join(idptools_install_dir, "cert.bundle")
    ca_chain_bundle = os.path.join(idptools_install_dir, "ca_chain.bundle")

    print "public_cert:", public_cert
    print "ca_chain:", ca_chain

    #Write public_cert to bundle first
    print "Signed Cert -----> ", public_cert
    if "http" not in public_cert:
        #Write contents of cert to cert_bundle_file
        with open(public_cert, "r") as cert_data:
            cert_contents = cert_data.read()
        with open(cert_bundle, "a") as cert_bundle_file:
            cert_bundle_file.write(cert_contents)
    else:
        #Make request for public_cert, then write public_cert contents to cert_bundle_file
        cert_contents = requests.get(public_cert).content
        with open(cert_bundle, "a") as cert_bundle_file:
            cert_bundle_file.write(cert_contents)

    num_of_certs = len(ca_chain)
    if num_of_certs > 0:
        for index, cert in enumerate(ca_chain):
            if index == num_of_certs-1:
                print "Root Cert -------> ", cert
                if "http" not in cert:
                    #Write contents of cert to cert_bundle_file and ca_chain_bundle
                    with open(cert, "r") as cert_data:
                        cert_contents = cert_data.read()
                    with open(cert_bundle, "a") as cert_bundle_file:
                        cert_bundle_file.write(cert_contents)
                    with open(ca_chain_bundle, "a") as ca_chain_bundle_file:
                        ca_chain_bundle_file.write(cert_contents)
                else:
                    cert_contents = requests.get(cert).content
                    with open(cert_bundle, "a") as cert_bundle_file:
                        cert_bundle_file.write(cert_contents)
                    with open(ca_chain_bundle, "a") as ca_chain_bundle_file:
                        ca_chain_bundle_file.write(cert_contents)
            else:
                print "Intermediate cert #{index} ----> {cert}".format(index=index, cert=cert)
                if "http" not in cert:
                    with open(cert, "r") as cert_data:
                        cert_contents = cert_data.read()
                    with open(cert_bundle, "a") as cert_bundle_file:
                        cert_bundle_file.write(cert_contents)
                    with open(ca_chain_bundle, "a") as ca_chain_bundle_file:
                        ca_chain_bundle_file.write(cert_contents)
                else:
                    cert_contents = requests.get(cert).content
                    with open(cert_bundle, "a") as cert_bundle_file:
                        cert_bundle_file.write(cert_contents)
                    with open(ca_chain_bundle, "a") as ca_chain_bundle_file:
                        ca_chain_bundle_file.write(cert_contents)

    return cert_bundle, ca_chain_bundle


def copy_cert_to_tomcat_conf(public_cert):
    '''Copy the signed cert to the ESGF Tomcat config directory (i.e. /esg/config/tomcat)'''
    #Check for newer version of public_cert; if found backup old cert
    esgf_cert_name = os.path.join(config["tomcat_conf_dir"], "{esgf_host}-esg-node.pem".format(esgf_host=esg_functions.get_esgf_host()))

    try:
        if os.path.getctime(public_cert) > os.path.getctime(esgf_cert_name):
            backed_up_cert_name = "{esgf_cert_name}_{date}".format(esgf_cert_name=esgf_cert_name, date=str(datetime.date.today()))
            shutil.move(esgf_cert_name, backed_up_cert_name)
            shutil.copyfile(public_cert, esgf_cert_name)
    except IOError:
        logger.exception("Error while copying public cert")
    except OSError, error:
        if error.errno == errno.ENOENT:
            logger.info("Existing cert %s not found.  Copying public cert to Tomcat config directory: %s", esgf_cert_name, config["tomcat_conf_dir"])
            shutil.copyfile(public_cert, esgf_cert_name)


#------------------------------------
#   Keystore functions
#------------------------------------

def generate_tomcat_keystore(keystore_name, keystore_alias, private_key, public_cert, intermediate_certs):
    '''The following helper function creates a new keystore for your tomcat installation'''

    provider = "org.bouncycastle.jce.provider.BouncyCastleProvider"
    idptools_install_dir = os.path.join(config["esg_tools_dir"], "idptools")

    if len(intermediate_certs) < 1:
        print "No intermediate_certs files given"
        esg_functions.exit_with_error()

    if not os.path.isfile(private_key):
        print "Private key file {private_key} does not exist".format(private_key=private_key)

    keystore_password = esg_functions.get_java_keystore_password()

    #-------------
    #Display values
    #-------------
    print "Keystore name : {keystore_name}".format(keystore_name=keystore_name)
    print "Keystore alias: {keystore_alias}".format(keystore_alias=keystore_alias)
    print "Keystore password: {keystore_password}".format(keystore_password=keystore_password)
    print "Private key   : {private_key}".format(private_key=private_key)
    print "Public cert  : {public_cert}".format(public_cert=public_cert)
    print "Certificates..."

    esg_bash2py.mkdir_p(idptools_install_dir)

    cert_bundle = os.path.join(idptools_install_dir, "cert.bundle")
    ca_chain_bundle = os.path.join(idptools_install_dir, "ca_chain.bundle")

    cert_bundle, ca_chain_bundle = bundle_certificates(public_cert, intermediate_certs, idptools_install_dir)

    print "checking that key pair is congruent... "
    if check_associate_cert_with_private_key(public_cert, private_key):
        print "The keypair was congruent"
    else:
        print "The keypair was not congruent"
        esg_functions.exit_with_error()


    print "creating keystore... "
    #create a keystore with a self-signed cert
    distinguished_name = "CN={esgf_host}".format(esgf_host=esg_functions.get_esgf_host())

    #if previous keystore is found; backup
    backup_previous_keystore(keystore_name)

    #-------------
    #Make empty keystore...
    #-------------
    create_empty_java_keystore(keystore_name, keystore_alias, keystore_password, distinguished_name)

    #-------------
    #Convert your private key into from PEM to DER format that java likes
    #-------------
    derkey = convert_per_to_dem(private_key, idptools_install_dir)

    #-------------
    #Now we gather up all the other keys in the key chain...
    #-------------
    check_cachain_validity(ca_chain_bundle)

    print "Constructing new keystore content... "
    import_cert_into_keystore(keystore_name, keystore_alias, keystore_password, derkey, cert_bundle, provider)

    #Check keystore output
    java_keytool_executable = "{java_install_dir}/bin/keytool".format(java_install_dir=config["java_install_dir"])
    #TODO: Look at using pyjks to list Owner, Issuer, MD5, SHA1, and Serial Number
    check_keystore_command = "{java_keytool_executable} -v -list -keystore {keystore_name} -storepass {store_password}".format(java_keytool_executable=java_keytool_executable, keystore_name=keystore_name, store_password=keystore_password)
    print "check_keystore_command:", check_keystore_command
    keystore_output = esg_functions.call_subprocess(check_keystore_command)
    if keystore_output["returncode"] == 0:
        print "Mmmm, freshly baked keystore!"
        print "If Everything looks good... then replace your current tomcat keystore with {keystore_name}, if necessary.".format(keystore_name=keystore_name)
        print "Don't forget to change your tomcat's server.xml entry accordingly :-)"
        print "Remember: Keep your private key {private_key} and signed cert {public_cert} in a safe place!!!".format(private_key=private_key, public_cert=public_cert)
    else:
        print "Failed to check keystore"
        esg_functions.exit_with_error(keystore_output["stderr"])

def check_associate_cert_with_private_key(cert, private_key):
    """
    :type cert: str
    :type private_key: str
    :rtype: bool
    """
    with open(private_key, "r") as private_key_file:
        private_key_contents = private_key_file.read()
    try:
        private_key_obj = OpenSSL.crypto.load_privatekey(OpenSSL.crypto.FILETYPE_PEM, private_key_contents)
    except OpenSSL.crypto.Error:
        logger.exception("Private key is not correct.")

    try:
        cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open(cert).read())
    except OpenSSL.crypto.Error:
        logger.exception("Certificate is not correct.")

    context = OpenSSL.SSL.Context(OpenSSL.SSL.TLSv1_METHOD)
    context.use_privatekey(private_key_obj)
    context.use_certificate(cert_obj)
    try:
        context.check_privatekey()
        return True
    except OpenSSL.SSL.Error:
        return False

def check_keystore(keystore_name, keystore_password):
    '''Check the contents of a given keystore or truststore'''

    keystore = jks.KeyStore.load(keystore_name, keystore_password)
    print "keystore:", keystore
    return keystore


def create_empty_java_keystore(keystore_name, keystore_alias, keystore_password, distinguished_name):
    '''Create a new empty Java Keystore using the JDK's keytool'''
    java_keytool_executable = "{java_install_dir}/bin/keytool".format(java_install_dir=config["java_install_dir"])
    generate_keystore_string = "{java_keytool_executable} -genkey -keyalg RSA -alias {keystore_alias} -keystore {keystore_name} -storepass {keystore_password} -keypass {keystore_password} -validity 360 -dname {distinguished_name} -noprompt".format(java_keytool_executable=java_keytool_executable, keystore_alias=keystore_alias, keystore_name=keystore_name, keystore_password=keystore_password, distinguished_name=distinguished_name)
    keystore_output = esg_functions.call_subprocess(generate_keystore_string)
    if keystore_output["returncode"] !=0:
        print "Problem with generating initial keystore...Exiting."
        esg_functions.exit_with_error(keystore_output["stderr"])


def backup_previous_keystore(keystore_name):
    '''If a previous keystore exists, back it up'''
    if os.path.isfile(keystore_name):
        shutil.move(keystore_name, os.path.join(keystore_name+".bak"))

def import_cert_into_keystore(keystore_name, keystore_alias, keystore_password, derkey, cert_bundle, provider):
    '''Imports a signed Certificate into the keystore'''

    idptools_install_dir = os.path.join(config["esg_tools_dir"], "idptools")
    extkeytool_executable = os.path.join(idptools_install_dir, "bin", "extkeytool")
    if not os.path.isfile(extkeytool_executable):
        install_extkeytool()

    command = "{extkeytool} -importkey -keystore {keystore_name} -alias {keystore_alias} -storepass {keystore_password} -keypass {keystore_password} -keyfile {derkey} -certfile {cert_bundle} -provider {provider}".format(extkeytool=extkeytool_executable, keystore_name=keystore_name, keystore_alias=keystore_alias, keystore_password=keystore_password, derkey=derkey, cert_bundle=cert_bundle, provider=provider)
    construct_keystore_output = esg_functions.call_subprocess(command)
    if construct_keystore_output["returncode"] !=0:
        print "Could not import cert %s into keystore %s" % (cert_bundle, keystore_name)

def install_tomcat_keypair(private_key="/etc/esgfcerts/hostkey.pem", public_cert="/etc/esgfcerts/hostcert.pem", keystore_name=config["keystore_file"], keystore_alias=config["keystore_alias"]):
    '''
    Once you have submitted the CSR and have gotten it back *signed*; now install the keypair
    If you want to install a commercial CA issued certificate:
    esg-node --install-keypair <certificate file> <key file>
    When prompted for the cachain file, specify the chain file provided by your CA'''


    #Exit if public_cert(signed CSR isn't found)
    if not os.path.isfile(public_cert):
        print "{public_cert} not found. Exiting.".format(public_cert=public_cert)
        esg_functions.exit_with_error()

    if not os.path.isfile(private_key):
        print "{private_key} not found. Exiting.".format(private_key=private_key)
        esg_functions.exit_with_error()

    print "private key = ", private_key
    print "public cert = ", public_cert
    print "keystore name  = ", keystore_name
    print "keystore alias = ", keystore_alias


    #Copy and rename private_key and cert
    try:
        shutil.copyfile(private_key, "/etc/certs/hostkey.pem")
    except shutil.Error:
        if os.path.samefile(private_key, "/etc/certs/hostkey.pem"):
            logger.debug("%s and /etc/certs/hostkey.pem are the same file", private_key)
        else:
            logger.exception("Error copying private key.")
    try:
        shutil.copyfile(public_cert, "/etc/certs/hostcert.pem")
    except shutil.Error:
        if os.path.samefile(public_cert, "/etc/certs/hostcert.pem"):
            logger.debug("%s and /etc/certs/hostcert.pem are the same file", public_cert)
        else:
            logger.exception("Error copying host cert.")

    cert_files = create_certificate_chain_list()
    create_certificate_chain(cert_files)

    os.chmod("/etc/certs/hostkey.pem", 0400)
    os.chmod("/etc/certs/hostcert.pem", 0644)
    os.chmod("/etc/certs/cachain.pem", 0644)


    generate_tomcat_keystore(keystore_name, keystore_alias, private_key, public_cert, cert_files)

    copy_cert_to_tomcat_conf(public_cert)

    if os.path.isfile(config["truststore_file"]):
        shutil.move(config["truststore_file"], config["truststore_file"]+".bak")

    #(In order for ORP or any other local service to trust eachother put your own cert into the truststore)
    rebuild_truststore(config["truststore_file"])
    add_my_cert_to_truststore(config["truststore_file"], keystore_name, keystore_alias)
    #     #register ${esgf_idp_peer}
    #
    #     echo "Please restart this node for keys to take effect: \"$0 restart\""
    #     echo
    # }

#------------------------------------
#   Truststore functions
#------------------------------------

def create_new_truststore(truststore_file):
    '''Create a new Java Truststore file by copying the JRE's cacerts file'''
    shutil.copyfile("{java_install_dir}/jre/lib/security/cacerts".format(java_install_dir=config["java_install_dir"]), truststore_file)

def rebuild_truststore(truststore_file, certs_dir=config["globus_global_certs_dir"]):
    '''Converts ESG certificates (that can be fetch by above function) into a truststore'''

    print "(Re)building truststore from esg certificates... [{truststore_file}]".format(truststore_file=truststore_file)

    if not os.path.isdir(certs_dir):
        print "Sorry, No esg certificates found... in {certs_dir}".format(certs_dir=certs_dir)
        fetch_esgf_certificates(certs_dir)

    #If you don't already have a truststore to build on....
    #Start building from a solid foundation i.e. Java's set of ca certs...
    if not os.path.isfile(truststore_file):
        create_new_truststore(truststore_file)

    tmp_dir = "/tmp/esg_scratch"
    esg_bash2py.mkdir_p(tmp_dir)

    cert_files = glob.glob('{certs_dir}/*.0'.format(certs_dir=certs_dir))
    for cert in cert_files:
        _insert_cert_into_truststore(cert, truststore_file, tmp_dir)
    shutil.rmtree(tmp_dir)

    sync_with_java_truststore(truststore_file)
    os.chown(truststore_file, esg_functions.get_user_id("tomcat"), esg_functions.get_group_id("tomcat"))


def add_my_cert_to_truststore(truststore_file=config["truststore_file"], keystore_file=config["keystore_file"], keystore_alias=config["keystore_alias"]):
    '''
        #This takes our certificate from the keystore and adds it to the
        #truststore.  This is done for other services that use originating
        #from this server talking to another service on this same host.  This
        #is the interaction scenario with part of the ORP security mechanism.
        #The param here is the password of the *keystore*
    '''
    #----------------------------------------------------------------
    #Re-integrate my public key (I mean, my "certificate") from my keystore into the truststore (the place housing all public keys I allow to talk to me)
    #----------------------------------------------------------------

    print "\n*******************************"
    print "Adding public key to truststore file {truststore_file}".format(truststore_file=truststore_file)
    print "******************************* \n"
    if os.path.isfile(truststore_file):
        print "Re-Integrating keystore's certificate into truststore.... "
        print "Extracting keystore's certificate... "
        keystore_password = esg_functions.get_java_keystore_password()
        extract_cert_output= esg_functions.call_subprocess("{java_install_dir}/bin/keytool -export -alias {keystore_alias} -file {keystore_file}.cer -keystore {keystore_file} -storepass {keystore_password}".format(java_install_dir=config["java_install_dir"], keystore_alias=keystore_alias, keystore_file=keystore_file, keystore_password=keystore_password))
        if extract_cert_output["returncode"] !=0:
            print "Could not extract certificate from keystore"
            esg_functions.exit_with_error(extract_cert_output["stderr"])

        print "Importing keystore's certificate into truststore... "
        import_to_truststore_output = esg_functions.call_subprocess("{java_install_dir}/bin/keytool -import -v -trustcacerts -alias {keystore_alias} -keypass {keystore_password} -file {keystore_file}.cer -keystore {truststore_file} -storepass {truststore_password} -noprompt".format(java_install_dir=config["java_install_dir"], keystore_alias=keystore_alias, keystore_file=keystore_file, keystore_password=keystore_password, truststore_file=config["truststore_file"], truststore_password=config["truststore_password"]))
        if import_to_truststore_output["returncode"] !=0:
            print "Could not import the certificate into the truststore"
            esg_functions.exit_with_error(import_to_truststore_output["stderr"])

        sync_with_java_truststore(truststore_file)

        try:
            os.remove(keystore_file+".cer")
        except OSError:
            logger.exception("Could not delete extracted cert file")

    os.chown(truststore_file, esg_functions.get_user_id("tomcat"), esg_functions.get_group_id("tomcat"))


def sync_with_java_truststore(truststore_file):
    jssecacerts_path = "{java_install_dir}/jre/lib/security/jssecacerts".format(java_install_dir=config["java_install_dir"])
    cacerts_path = "{java_install_dir}/jre/lib/security/cacerts".format(java_install_dir=config["java_install_dir"])
    if not os.path.isfile(jssecacerts_path) and os.path.isfile(cacerts_path):
        shutil.copyfile(cacerts_path, jssecacerts_path)

    if not os.path.join(truststore_file):
        print "{truststore_file} does not exist. Exiting."
        esg_functions.exit_with_error()

    print "Syncing {truststore_file} with {java_truststore} ... ".format(truststore_file=truststore_file, java_truststore=jssecacerts_path)
    if filecmp.cmp(truststore_file, jssecacerts_path):
        print "Files already in sync"
        return

    try:
        shutil.copyfile(jssecacerts_path, jssecacerts_path+".bak")
    except OSError:
        logger.exception("Could not back up java truststore file.")

    try:
        shutil.copyfile(truststore_file, jssecacerts_path)
    except OSError:
        logger.exception("Could not sync truststore files.")

    os.chmod(jssecacerts_path, 0644)
    os.chown(jssecacerts_path, esg_functions.get_user_id("root"), esg_functions.get_group_id("root"))


def _insert_cert_into_truststore(cert_file, truststore_file, tmp_dir):
    '''Takes full path to a pem certificate file and incorporates it into the given truststore'''

    print "{cert_file} ->".format(cert_file=cert_file)
    cert_hash = cert_file.split(".")[0]
    der_file = os.path.join(tmp_dir, cert_hash+".der")
    #--------------
    # Convert from PEM format to DER format - for ingest into keystore
    cert_pem = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open(cert_file).read())
    with open(der_file, "w") as der_file_handle:
        der_file_handle.write(OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_ASN1,cert_pem))

    #--------------
    if os.path.isfile(truststore_file):
        output = esg_functions.call_subprocess("/usr/local/java/bin/keytool -delete -alias {cert_hash} -keystore {truststore_file} -storepass {truststore_password}".format(cert_hash=cert_hash, truststore_file=truststore_file, truststore_password=config["truststore_password"]))
        if output["returncode"] == 0:
            print "Deleted cert hash"

        output = esg_functions.call_subprocess("/usr/local/java/bin/keytool -import -alias {cert_hash} -file {der_file} -keystore {truststore_file} -storepass {truststore_password} -noprompt".format(cert_hash=cert_hash, der_file=der_file, truststore_file=truststore_file, truststore_password=config["truststore_password"]))
        if output["returncode"] == 0:
            print "added {der_file} to {truststore_file}".format(der_file=der_file, truststore_file=truststore_file)
        os.remove(der_file)


def delete_existing_temp_CA():
    try:
        shutil.rmtree("CA")
    except OSError, error:
        if error.errno == errno.ENOENT:
            pass
    file_extensions = ["*.pem", "*.gz", "*.ans", "*.tmpl"]
    files_to_delete = []
    for ext in file_extensions:
        files_to_delete.extend(glob.glob(ext))
    for file_name in files_to_delete:
        try:
            shutil.rmtree(file_name)
        except OSError, error:
            if error.errno == errno.ENOENT:
                pass


def createKeyPair(type, bits):
    '''source: https://github.com/pyca/pyopenssl/blob/master/examples/certgen.py'''
    """
    Create a public/private key pair.
    Arguments: type - Key type, must be one of TYPE_RSA and TYPE_DSA
               bits - Number of bits to use in the key
    Returns:   The public/private key pair in a PKey object
    """
    pkey = OpenSSL.crypto.PKey()
    pkey.generate_key(type, bits)
    return pkey


def createCertRequest(pkey, digest="sha256", **name):
    '''source: https://github.com/pyca/pyopenssl/blob/master/examples/certgen.py'''
    """
    Create a certificate request.
    Arguments: pkey   - The key to associate with the request
               digest - Digestion method to use for signing, default is sha256
               **name - The name of the subject of the request, possible
                        arguments are:
                          C     - Country name
                          ST    - State or province name
                          L     - Locality name
                          O     - Organization name
                          OU    - Organizational unit name
                          CN    - Common name
                          emailAddress - E-mail address
    Returns:   The certificate request in an X509Req object
    """
    req = OpenSSL.crypto.X509Req()
    subj = req.get_subject()

    for key, value in name.items():
        setattr(subj, key, value)

    req.set_pubkey(pkey)
    req.sign(pkey, digest)
    return req


def createCertificate(req, issuerCertKey, serial, validityPeriod,
                      digest="sha256"):
    """
    Generate a certificate given a certificate request.
    Arguments: req        - Certificate request to use
               issuerCert - The certificate of the issuer
               issuerKey  - The private key of the issuer
               serial     - Serial number for the certificate
               notBefore  - Timestamp (relative to now) when the certificate
                            starts being valid
               notAfter   - Timestamp (relative to now) when the certificate
                            stops being valid
               digest     - Digest method to use for signing, default is sha256
    Returns:   The signed certificate in an X509 object
    """
    issuerCert, issuerKey = issuerCertKey
    notBefore, notAfter = validityPeriod
    cert = OpenSSL.crypto.X509()
    cert.set_serial_number(serial)
    cert.gmtime_adj_notBefore(notBefore)
    cert.gmtime_adj_notAfter(notAfter)
    cert.set_issuer(issuerCert.get_subject())
    cert.set_subject(req.get_subject())
    cert.set_pubkey(req.get_pubkey())
    cert.sign(issuerKey, digest)
    return cert


def new_ca():
    '''Mimics perl CA.pl -newca'''
    esg_bash2py.mkdir_p("CA")
    esg_bash2py.mkdir_p("CA/certs")
    esg_bash2py.mkdir_p("CA/crl")
    esg_bash2py.mkdir_p("CA/newcerts")
    esg_bash2py.mkdir_p("CA/private")
    esg_bash2py.touch("CA/index.txt")
    with open("CA/crlnumber", "w") as crlnumber_file:
        crlnumber_file.write("01\n")

    cakey = createKeyPair(OpenSSL.crypto.TYPE_RSA, 4096)
    ca_answer = "{fqdn}-CA".format(fqdn=esg_functions.get_esgf_host())
    careq = createCertRequest(cakey, CN=ca_answer, O="ESGF", OU="ESGF.ORG")
    # CA certificate is valid for five years.
    cacert = createCertificate(careq, (careq, cakey), 0, (0, 60*60*24*365*5))

    print('Creating Certificate Authority private key in "CA/private/cakey.pem"')
    with open('CA/private/cakey.pem', 'w') as capkey:
        capkey.write(
            OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, cakey).decode('utf-8')
    )

    print('Creating Certificate Authority certificate in "CA/cacert.pem"')
    with open('CA/cacert.pem', 'w') as ca:
        ca.write(
            OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_PEM, cacert).decode('utf-8')
    )

def newreq_nodes():
    '''Mimics perl CA.pl -newreq-nodes'''

    new_req_key = createKeyPair(OpenSSL.crypto.TYPE_RSA, 4096)
    new_careq = createCertRequest(new_req_key, CN=esg_functions.get_esgf_host(), O="ESGF", OU="ESGF.ORG")

    print('Creating Certificate Authority private key in "newkey.pem"')
    with open('newkey.pem', 'w') as new_key_file:
        new_key_file.write(
            OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, new_req_key).decode('utf-8')
    )

    print('Creating Certificate Authority private key in "newreq.pem"')
    with open('newreq.pem', 'w') as new_req_file:
        new_req_file.write(
            OpenSSL.crypto.dump_certificate_request(OpenSSL.crypto.FILETYPE_PEM, new_careq).decode('utf-8')
    )
    print "Request is in newreq.pem, private key is in newkey.pem\n";

    return new_careq, new_req_key

def sign_request(ca_req, req_key):
    '''Mimics perl CA.pl -sign'''
    newcert = createCertificate(ca_req, (ca_req, req_key), 0, (0, 60*60*24*365*5))

    with open('newcert.pem', 'w') as ca:
        ca.write(
            OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_PEM, newcert).decode('utf-8')
    )
    print "Signed certificate is in newcert.pem\n";

def setup_temp_ca(temp_ca_dir="/etc/tempcerts"):
    print "*******************************"
    print "Setting up Temp CA"
    print "******************************* \n"

    esg_bash2py.mkdir_p(temp_ca_dir)

    #Copy CA perl script and openssl conf file that it uses.  The CA perl script
    #is used to create a temporary root CA
    shutil.copyfile(os.path.join(current_directory, "CA.pl"), "{}/CA.pl".format(temp_ca_dir))
    shutil.copyfile(os.path.join(current_directory, "openssl.cnf"), "{}/openssl.cnf".format(temp_ca_dir))
    shutil.copyfile(os.path.join(current_directory, "myproxy-server.config"), "{}/myproxy-server.config".format(temp_ca_dir))
    os.chmod("{}/CA.pl".format(temp_ca_dir), 0755)
    os.chmod("{}/openssl.cnf".format(temp_ca_dir), 0755)

    with esg_bash2py.pushd(temp_ca_dir):
        delete_existing_temp_CA()
        new_ca()

        # openssl rsa -in CA/private/cakey.pem -out clearkey.pem -passin pass:placeholderpass && mv clearkey.pem CA/private/cakey.pem
        private_cakey = OpenSSL.crypto.load_privatekey(OpenSSL.crypto.FILETYPE_PEM, open("CA/private/cakey.pem").read())

        with open('clearkey.pem', 'w') as clearkey:
            clearkey.write(
                OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, private_cakey).decode('utf-8')
        )

        shutil.copyfile("clearkey.pem", "CA/private/cakey.pem")

        # perl CA.pl -newreq-nodes <reqhost.ans
        new_careq, new_req_key = newreq_nodes()
        # perl CA.pl -sign <setuphost.ans
        sign_request(new_careq, new_req_key)

        # openssl x509 -in CA/cacert.pem -inform pem -outform pem >cacert.pem
        try:
            cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open("CA/cacert.pem").read())
        except OpenSSL.crypto.Error:
            logger.exception("Certificate is not correct.")

        with open('cacert.pem', 'w') as ca:
            ca.write(
                OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_PEM, cert_obj).decode('utf-8')
        )
        # cp CA/private/cakey.pem cakey.pem
        shutil.copyfile("CA/private/cakey.pem", "cakey.pem")

        # openssl x509 -in newcert.pem -inform pem -outform pem >hostcert.pem
        try:
            cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open("newcert.pem").read())
        except OpenSSL.crypto.Error:
            logger.exception("Certificate is not correct.")

        with open('hostcert.pem', 'w') as ca:
            ca.write(
                OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_PEM, cert_obj).decode('utf-8')
        )

        # mv newkey.pem hostkey.pem
        shutil.move("newkey.pem", "hostkey.pem")

        # chmod 400 cakey.pem
        os.chmod("cakey.pem", 0400)

        # chmod 400 hostkey.pem
        os.chmod("hostkey.pem", 0400)

        # rm -f new*.pem
        new_pem_files = glob.glob('new*.pem')
        for pem_file in new_pem_files:
            os.remove(pem_file)

        try:
            cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open("cacert.pem").read())
        except OpenSSL.crypto.Error:
            logger.exception("Certificate is not correct.")

        local_hash = convert_hash_to_hex(cert_obj.subject_name_hash())
        globus_cert_dir = "globus_simple_ca_{}_setup-0".format(local_hash)
        esg_bash2py.mkdir_p(globus_cert_dir)

        shutil.copyfile("cacert.pem", os.path.join(globus_cert_dir,local_hash+".0"))
        shutil.copyfile(os.path.join(current_directory, "signing-policy.template"), os.path.join(globus_cert_dir,local_hash+".signing_policy"))

        cert_subject_object = cert_obj.get_subject()
        cert_subject = "/OU={OU}/CN={CN}/O={O}".format(OU=cert_subject_object.OU, CN=cert_subject_object.CN, O=cert_subject_object.O)
        esg_functions.replace_string_in_file(os.path.join(globus_cert_dir,local_hash+".signing_policy"), '/O=ESGF/OU=ESGF.ORG/CN=placeholder', cert_subject)
        shutil.copyfile(os.path.join(globus_cert_dir,local_hash+".signing_policy"), "signing-policy")

        # tar -cvzf globus_simple_ca_${localhash}_setup-0.tar.gz $tgtdir;
        with tarfile.open(globus_cert_dir+".tar.gz", "w:gz") as tar:
            tar.add(globus_cert_dir)

        # rm -rf $tgtdir;
        shutil.rmtree(globus_cert_dir)

        # mkdir -p /etc/certs
        esg_bash2py.mkdir_p("/etc/certs")
    	# cp openssl.cnf /etc/certs/
        shutil.copyfile("openssl.cnf", "/etc/certs/openssl.cnf")
    	# cp host*.pem /etc/certs/
        host_pem_files = glob.glob('new*.pem')
        for pem_file in host_pem_files:
            shutil.copyfile(pem_file, "/etc/certs/{}".format(pem_file))
    	# cp cacert.pem /etc/certs/cachain.pem
        shutil.copyfile("cacert.pem", "/etc/certs/cachain.pem")
    	# mkdir -p /etc/esgfcerts
        esg_bash2py.mkdir_p("/etc/esgfcerts")


def set_commercial_ca_paths():
    try:
        commercial_key_path = esg_property_manager.get_property("commercial.key.path")
    except ConfigParser.NoOptionError:
        commercial_key_path = raw_input("Enter the file path of the commercial key: ")

    try:
        commercial_cert_path = esg_property_manager.get_property("commercial.cert.path")
    except ConfigParser.NoOptionError:
        commercial_cert_path = raw_input("Enter the file path of the commercial cert: ")

    try:
        ca_chain_path = esg_property_manager.get_property("cachain.path")
    except ConfigParser.NoOptionError:
        ca_chain_path = raw_input("Enter the file path of the ca chain: ")

    return (commercial_key_path, commercial_cert_path, ca_chain_path)

def backup_existing_certs():
    '''Backup existing SSL certs on system'''
    if os.path.isfile("/etc/certs/hostcert.pem"):
        shutil.copyfile("/etc/certs/hostcert.pem", "/etc/certs/hostcert.pem.{date}.bak".format(date=str(datetime.date.today())))
    if os.path.isfile("/etc/certs/hostkey.pem"):
        shutil.copyfile("/etc/certs/hostkey.pem", "/etc/certs/hostkey.pem.{date}.bak".format(date=str(datetime.date.today())))

def install_commerical_certs(commercial_key_path, commercial_cert_path, ca_chain_path):
    '''Install the signed, commericial SSL credentials to /etc/certs'''
    shutil.copyfile(commercial_key_path, "/etc/certs/hostkey.pem")
    shutil.copyfile(commercial_cert_path, "/etc/certs/hostcert.pem")
    shutil.copyfile(ca_chain_path, "/etc/certs/cachain.pem")


def check_for_commercial_ca(commercial_ca_directory="/etc/esgfcerts"):
    '''Checks if Commerical CA directory has been created; asks user if they would like proceed with
    Commercial CA installation if directory is found'''

    print "*******************************"
    print "Checking for Commercial CA"
    print "******************************* \n"

    try:
        commercial_ca_setup = esg_property_manager.get_property("install.signed.certs")
    except ConfigParser.NoOptionError:
        commercial_ca_setup = raw_input("Do you have a commercial CA that you want to install [Y/n]: ") or "yes"

    if commercial_ca_setup in YES_LIST:
        commercial_key_path, commercial_cert_path, ca_chain_path = set_commercial_ca_paths()

        #Backup existing certs
        backup_existing_certs()

        install_commerical_certs(commercial_key_path, commercial_cert_path, ca_chain_path)

        print "Local installation of certs complete."

    else:
        return
            # file_list = ["hostcert.pem", "hostkey.pem"]
            # with esg_bash2py.pushd(commercial_ca_directory):
            #     for file_name in file_list:
            #         if not os.path.isfile(file_name):
            #             print "{file_name} not found in /etc/esgfcerts. Exiting."
            #             esg_functions.exit_with_error(1)
            #         else:
            #             try:
            #                 shutil.copyfile(file_name, "/etc/grid-security/{file_name}".format(file_name=file_name))
            #             except OSError:
            #                 logger.exception("Could not copy %s", file_name)

def install_local_certs(node_type_list, firstrun=None):
    if firstrun:
        file_list = ("cakey.pem", "cacert.pem", "hostcert.pem", "hostkey.pem", "myproxy-server.config")
        certdir = "/etc/tempcerts"
    else:
        if "IDP" in node_type_list:
            file_list = ("cakey.pem", "cacert.pem", "hostcert.pem", "hostkey.pem")
        else:
            file_list = ("hostcert.pem", "hostkey.pem")

        certdir= "/etc/esgfcerts"

    with esg_bash2py.pushd(certdir):
        for file_name in file_list:
            if not os.path.exists(file_name):
                esg_functions.exit_with_error("File {} is not found in {}; Please place it there and reexecute esg_node.py --install-local-certs".format(file_name, certdir))

        if "IDP" in node_type_list:
            try:
                cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open("cacert.pem").read())
            except OpenSSL.crypto.Error:
                logger.exception("Certificate is not correct.")

            local_hash = convert_hash_to_hex(cert_obj.subject_name_hash())
            globus_pack = "globus_simple_ca_{}_setup-0.tar.gz".format(local_hash)
            if not os.path.exists(globus_pack):
                esg_functions.exit_with_error("File {} is not found in {}; Please place it there and reexecute esg_node.py --install-local-certs".format(globus_pack, certdir))

        if "IDP" in node_type_list:
            shutil.copyfile("cacert.pem", "/var/lib/globus-connect-server/myproxy-ca/cacert.pem")
            shutil.copyfile("cakey.pem", "/var/lib/globus-connect-server/myproxy-ca/private/cakey.pem")
            shutil.copyfile(globus_pack, "/var/lib/globus-connect-server/myproxy-ca/{}".format(globus_pack))
            shutil.copyfile(globus_pack, "/etc/grid-security/certificates/{}".format(globus_pack))

        if os.path.exists("hostkey.pem"):
            shutil.copyfile("hostkey.pem", "/etc/grid-security/hostkey.pem")
            os.chmod("/etc/grid-security/hostkey.pem", 0600)
        if os.path.exists("hostcert.pem"):
            shutil.copyfile("hostcert.pem", "/etc/grid-security/hostcert.pem")

        print "Local installation of certs complete."

def check_cert_expiry(file_name):
    if not os.path.exists(file_name):
        print "Certficate file {} does not exists".format(file_name)
        return

    file_data = open(file_name).read()
    x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, file_data)
    expire_date = x509.get_notAfter()

    if x509.has_expired():
        print "{} is expired".format(file_name)

    print "{} will expire {}".format(file_name, str(expire_date))

def check_certificates():
    print "check_certificates..."
    tomcat_cert_file = "{}/{}-esg-node.pem".format(config["tomcat_conf_dir"], esg_functions.get_esgf_host())
    check_cert_expiry(tomcat_cert_file)

    from idp_node import globus
    globus.globus_check_certificates()

def extract_openssl_dn(public_cert="/etc/grid-security/hostcert.pem"):
    '''Regex's the output from openssl's x509 output in "openssl" format:
    Subject: O=Grid, OU=GlobusTest, OU=simpleCA-pcmdi3.llnl.gov, CN=pcmdi7.llnl.gov
    and transforms it to our "standard" format
    /O=Grid/OU=GlobusTest/OU=simpleCA-pcmdi3.llnl.gov/CN=pcmdi7.llnl.gov
    arg 1 -> the location of the x509 pem file'''

    x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open(public_cert).read())
    subject_components = x509.get_subject().get_components()
    subject_string = ""

    for component in subject_components:
        subject_string = subject_string + "/" +  component[0] + "=" + component[1]


    return subject_string

def get_certificate_subject_hash(cert_path):
    try:
        cert_obj = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, open(cert_path).read())
    except OpenSSL.crypto.Error:
        logger.exception("Certificate is not correct.")
    except IOError:
        logger.exception("Could not open %s", cert_path)

    return convert_hash_to_hex(cert_obj.subject_name_hash())

def find_certificate_issuer_cert():
    myproxy_config_file = "{}/config/myproxy/myproxy-server.config".format(config["esg_root_dir"])
    if os.path.exists(myproxy_config_file):
        try:
            with open(myproxy_config_file) as config_file:
                for line in config_file.readlines():
                    if "certificate_issuer_cert" in line:
                        #.strip('\"') is for removing quotes
                        simpleCA_cert = line.split()[1].strip('\"')
                        return simpleCA_cert
        except IOError:
            raise

def fetch_esgf_truststore(truststore_file=config["truststore_file"]):
    if not os.path.exists(truststore_file):
        try:
            node_peer_group = esg_property_manager.get_property("node_peer_group")
            if node_peer_group == "esgf-test":
                shutil.copyfile(os.path.join(os.path.dirname(__file__), "tomcat_certs/esg-truststore-test-federation.ts"), truststore_file)
            else:
                shutil.copyfile(os.path.join(os.path.dirname(__file__), "tomcat_certs/esg-truststore.ts"), truststore_file)
        except ConfigParser.NoOptionError:
            print "Could not find node peer group property"

        apache_truststore = '/etc/certs/esgf-ca-bundle.crt'
        if not os.path.exists(apache_truststore):
            if node_peer_group == "esgf-test":
                shutil.copyfile(os.path.join(os.path.dirname(__file__), "apace_certs/esgf-ca-bundle-test-federation.crt"), apache_truststore)
            else:
                shutil.copyfile(os.path.join(os.path.dirname(__file__), "apace_certs/esgf-ca-bundle.crt"), apache_truststore)

        with open(apache_truststore, "a") as apache_truststore_file:
            ca_cert = open("/etc/tempcerts/cacert.pem").read()
            apache_truststore_file.write(ca_cert)

        simpleCA_cert = find_certificate_issuer_cert()

        simpleCA_cert_hash = get_certificate_subject_hash(simpleCA_cert)

        simpleCA_cert_hash_file = os.path.join(config["globus_global_certs_dir"], simpleCA_cert_hash+".0")
        _insert_cert_into_truststore(simpleCA_cert_hash_file, truststore_file, "/tmp/esg_scratch")

        add_my_cert_to_truststore()

def extract_keystore_dn():
    '''Returns the distinguished name from the Java keystore'''
    try:
        keystore_info = esg_functions.call_subprocess("/usr/local/java/bin/keytool -list -v -keystore /esg/config/tomcat/keystore-tomcat")
    except SubprocessError:
        logger.exception("Could not extract distinguished name from keystore")
    keystore_owner = re.search("Owner:.*", keystore_info["stdout"]).group()
    distinguished_name = keystore_owner.split()[1]
    return distinguished_name


def generate_ssl_key_and_csr(private_key="/usr/local/tomcat/hostkey.pem", public_cert_req=None):
    print "Generating private host key... "
    key_pair = createKeyPair(OpenSSL.crypto.TYPE_RSA, 1024)
    with open(private_key, "wt") as key_file_handle:
        key_file_handle.write(OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, key_pair))

    os.chmod(private_key, 0400)

    print "Generating Certificate Signing Request (csr)... "
    if not public_cert_req:
        public_cert_req = "/usr/local/tomcat/{}-esg-node.csr".format(esg_functions.get_esgf_host())

    public_cert_dn = extract_keystore_dn()
    if public_cert_dn:
        careq = createCertRequest(private_key, public_cert_dn)
    else:
        careq = createCertRequest(private_key, CN=esg_functions.get_esgf_host(), O="ESGF", OU="ESGF.ORG")

    print "Generating 30 day temporary self-signed certificate... "

    newcert = createCertificate(careq, (careq, private_key), 0, (0, 30*60*24*365*5))

    with open('/usr/local/tomcat/{}-esg-node.pem'.format(esg_functions.get_esgf_host()), 'w') as ca:
        ca.write(
            OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_PEM, newcert).decode('utf-8')
    )

        print "--------------------------------------------------------"
        print "In Directory: /usr/local/tomcat"
        print "Generated private key: {}".format(private_key)
        print "Generated certificate: /usr/local/tomcat/{}-esg-node.pem".format(esg_functions.get_esgf_host())
        print "Please obtain and install appropriate certificates at the earliest. Execute esg_node.py --cert-howto for details.";
        #print "Then run %> esg-node --install-ssl-keypair <signed cert> <private key> (use --help for details)"
        print "--------------------------------------------------------"

def generate_esgf_csrs(node_type_list):
    esgf_host = esg_functions.get_esgf_host()
    if "IDP" in node_type_list:
        key_pair = createKeyPair(OpenSSL.crypto.TYPE_RSA, 1024)
        with open("/etc/esgfcerts/cakey.pem", "wt") as key_file_handle:
            key_file_handle.write(OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, key_pair))

        careq = createCertRequest("/etc/esgfcerts/cakey.pem", CN=esg_functions.get_esgf_host()+"-CA", O="ESGF", OU="ESGF.ORG")
        with open('/etc/esgfcerts/cacert_req.csr', 'w') as csr:
            csr.write(
                OpenSSL.crypto.dump_certificate_request(OpenSSL.crypto.FILETYPE_PEM, careq).decode('utf-8')
        )
        print "Successfully generated request for a simpleCA CA certificate: /etc/esgfcerts/cacert_req.csr"

    print "You are strongly advised to obtain and install commercial CA issued certificates for the web container."

    key_pair = createKeyPair(OpenSSL.crypto.TYPE_RSA, 1024)
    with open("/etc/esgfcerts/hostkey.pem", "wt") as key_file_handle:
        key_file_handle.write(OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, key_pair))

    careq = createCertRequest("/etc/esgfcerts/hostkey.pem", CN=esg_functions.get_esgf_host(), O="ESGF", OU="ESGF.ORG")
    with open('/etc/esgfcerts/hostcert_req.csr', 'w') as csr:
        csr.write(
            OpenSSL.crypto.dump_certificate_request(OpenSSL.crypto.FILETYPE_PEM, careq).decode('utf-8')
    )
    print "Successfully generated request for a simpleCA CA certificate: /etc/esgfcerts/hostcert_req.csr"

    print "Please mail the csr files for signing to Lukasz Lacinski <lukasz@uchicago.edu>, Prashanth Dwarakanath <pchengi@nsc.liu.se>, or Sebastien Denvil <sebastien.denvil@ipsl.jussieu.fr>"
    print "When you receive the signed certificate pack, untar all files into /etc/esgfcerts and execute esg_node.py --install-local-certs"
    print "If you also want to install the local certs for the tomcat web-container, execute esg_node.py --install-keypair /etc/esgfcerts/hostcert.pem /etc/esgfcerts/hostkey.pem"
    print "When prompted for the cachain file, specify /etc/esgfcerts/cachain.pem"

def generate_esgf_csrs_ext(node_type):
    print "Are you requesting certs for an index-node or a datanode? (index/data)?"
    if node_type.lower() != "index" or node_type != "data":
        print "Please specify index or data as node type"
        return

    req_node_hostname = raw_input("Enter FQDN of node you are requesting certificates for")

    esg_bash2py.mkdir_p("/etc/extcsrs")

    cert_files = ['hostkey.pem', 'hostcert_req.csr']
    if node_type == "index":
        cert_files.append('cacert_req.csr')
        cert_files.append('cakey.pem')

        key_pair = createKeyPair(OpenSSL.crypto.TYPE_RSA, 1024)
        with open("/etc/extcsrs/cakey.pem", "wt") as key_file_handle:
            key_file_handle.write(OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, key_pair))

        careq = createCertRequest("/etc/extcsrs/cakey.pem", CN=esg_functions.get_esgf_host()+"-CA", O="ESGF", OU="ESGF.ORG")
        with open('/etc/extcsrs/cacert_req.csr', 'w') as csr:
            csr.write(
                OpenSSL.crypto.dump_certificate_request(OpenSSL.crypto.FILETYPE_PEM, careq).decode('utf-8')
        )
        print "Successfully generated request for a simpleCA CA certificate: /etc/extcsrs/cacert_req.csr"

    print "You are strongly advised to obtain and install commercial CA issued certificates for the web container."

    key_pair = createKeyPair(OpenSSL.crypto.TYPE_RSA, 1024)
    with open("/etc/extcsrs/hostkey.pem", "wt") as key_file_handle:
        key_file_handle.write(OpenSSL.crypto.dump_privatekey(OpenSSL.crypto.FILETYPE_PEM, key_pair))

    careq = createCertRequest("/etc/extcsrs/hostkey.pem", CN=req_node_hostname, O="ESGF", OU="ESGF.ORG")
    with open('/etc/extcsrs/hostcert_req.csr', 'w') as csr:
        csr.write(
            OpenSSL.crypto.dump_certificate_request(OpenSSL.crypto.FILETYPE_PEM, careq).decode('utf-8')
    )
    print "Successfully generated request for a simpleCA CA certificate: /etc/extcsrs/hostcert_req.csr"

    with esg_bash2py.pushd("/etc/extcsrs"):
        try:
            with tarfile.open(req_node_hostname, "w:tgz") as tar:
                for file_name in cert_files:
                    tar.add(file_name)
            print "A copy of the generated keys and CSRs has been saved as /etc/extcsrs/{}.tgz".format(req_node_hostname)
        except:
            print "ERROR: Problem with creating backup archive: {}".format(req_node_hostname)

    print "Please mail the csr files for signing to Lukasz Lacinski <lukasz@uchicago.edu>, Prashanth Dwarakanath <pchengi@nsc.liu.se>, or Sebastien Denvil <sebastien.denvil@ipsl.jussieu.fr>"
    print "When you receive the signed certificate pack, untar all files into /etc/esgfcerts and execute esg_node.py --install-local-certs"
    print "If you also want to install the local certs for the tomcat web-container, execute esg_node.py --install-keypair /etc/esgfcerts/hostcert.pem /etc/esgfcerts/hostkey.pem"
    print "When prompted for the cachain file, specify /etc/esgfcerts/cachain.pem"


def main():
    print "*******************************"
    print "Setting up SSL Certificates"
    print "******************************* \n"

    create_self_signed_cert("/etc/certs")
    setup_temp_ca()
    install_tomcat_keypair("/etc/certs/hostkey.pem", "/etc/certs/hostcert.pem")
    check_for_commercial_ca()


if __name__ == '__main__':
    main()
