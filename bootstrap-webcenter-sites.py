#! /usr/bin/env python

# This script bootstraps a WebCentre Sites Remote Satellite Server
# EC2 instance.
#
# The script retrieves a bootstrapping bundle (a zip file) from S3
# containing a a bootstrapping file list and a property file.
# The bootstrapping file list contains a list of files that need
# to be "bootstrapped" with host specific information, and the property
# file provides a mapping from properties which exist as placeholders
# in the virgin instance, to (optionally) default values which will
# replace placeholders in the install base for the virgin instance.
#
# If values are defined for placeholders in the properties file,
# those values are used for the associated placeholder. For all other
# undefined placeholders, EC2 metadata is interrogated and the results
# are used. If after interrogating EC2 metadata there exist undefined
# property values, the bootstrapping script stops with an error.
#
# Once all property values are defined, the bootstrap script loops
# through the list of files specified in the file list and replaces
# instances of the placeholder with an associated value.
#
# Finally, the script attempts to start application services. If this
# succeeds, the EC2 instance has been bootstrapped.
#


import boto
import boto.ec2
import boto.utils
from boto.s3.key import Key

import sys
import os.path
import urllib
import subprocess
import logging
import fileinput
import ConfigParser
import simplejson as json



# a list of files to migrate
AMI_FILELIST = 'bootstrap.filelist'
# a template of properties to migrate
AMI_PROPERTIES = 'bootstrap.properties'
LOG_FILE = 'ami-bootstrap.log'

# set up logging
logger = logging.getLogger('ami-bootstrap')
logger_file = logging.FileHandler(LOG_FILE)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
logger_file.setFormatter(formatter)
logger.addHandler(logger_file)
logger.setLevel(logging.DEBUG) 


def main():
    # retrive our user data JSON from the instance user data
    try:
        json_data = get_userdata_json()
    except boto.exception.AWSConnectionError, e:
        print >>sys.stderr, "Couldn't connect to AWS to retrieve user data:", e
        return 1
    except json.decoder.JSONDecodeError, e:
        print >>sys.stderr, "Couldn't parse JSON data:", e
        return 1

    # get bucket_name and bundle_name from user data
    if 'bootstrap' in json_data and 'bucket_name' in json_data['bootstrap']:
        bucket_name = json_data['bootstrap']['bucket_name']
    else:
        return 1

    if 'bootstrap' in json_data and 'bundle_name' in json_data['bootstrap']:
        bundle_name = json_data['bootstrap']['bundle_name']
    else:
        return 1

    # retrieve bootstrapping bundle from S3 (returns a file path)
    bundle_path = get_bundle(bucket_name, bundle_name, '/tmp')

    # explode it
    if os.path.exists(bundle_path):
        exploded_bundle_path = explode_bundle(bundle_path)
    else:
        print >>sys.stderr, "Didn't get bootstrapping bundle"
        return 1

    # bootstrap!
    bootstrap(exploded_bundle_path)

    # set metadata iff it was supplied in user data
    if 'bootstrap' in json_data and 'metadata' in json_data['bootstrap']:
        metadata = json_data['bootstrap']['metadata']
        set_metadata(metadata)

    # start the application service(s) if required
    if 'bootstrap' in json_data and 'services' in json_data['bootstrap']:
        services = json_data['bootstrap']['services']
        start_services(services)


# download a "bootstrapping bundle" from S3
def get_bundle(bucket_name, bundle_name, to_path):
    local_bundle_path = os.path.join(to_path, bundle_name)

    try:
        s3_conn = boto.connect_s3()
        bucket = s3_conn.get_bucket(bucket_name)
    except Exception, e:
        print >>sys.stderr, "Exception:", e
    else:
        k = Key(bucket)
        k.key = bundle_name
        k.get_contents_to_filename(local_bundle_path)

    return local_bundle_path


# expand bootstrapping bundle
def explode_bundle(bundle_path):
    exploded_path = os.path.dirname(bundle_path)

    # we support zip files - that's it :)
    import zipfile

    if not zipfile.is_zipfile(bundle_path):
        raise Exception("Unsupported bundle type: " + ext)

    archive = zipfile.ZipFile(bundle_path, 'r')

    # there is no extract or extractall in python 2.5 :(
    for file in archive.namelist():
        exploded_file = os.path.join(exploded_path, file)
        exploded_fh = open(exploded_file, 'w')
        exploded_fh.write(archive.read(file))

    return exploded_path


# run the bootstrapping logic
def bootstrap(bundle_path):
    print "Ready to run bootstrap using extracted bundle in: " + bundle_path

    properties_file = os.path.join(bundle_path, AMI_PROPERTIES)
    filelist_file   = os.path.join(bundle_path, AMI_FILELIST)

    # create case-sensitive properties lookup structure
    config = ConfigParser.ConfigParser()
    config.optionxform = str
    config.read(properties_file)

    # loop through ami filelist applying filters/patches to it
    for line in open(filelist_file, 'r'):
        line = line.rstrip('\n')
        migrate_file(config, line)


# migrate a file
def migrate_file(config, file):
    logger.info(file)

    # open file and do search and replace for each key/value pair
    for line in fileinput.FileInput(file, inplace=1):

        for key, value in config.items('host_config'):
            if key in line:
                logger.debug('Replacing ' + key + ' with ' + value + ' in line: ' + line)
                line = line.replace(key, value)

        print line,


def start_services(services):
    # start all required services
    for service in services:
        retcode = subprocess.call(['service', service, 'start'])

        try:
            if retcode != 0:
                print >>sys.stderr, "Starting " + service + " returned a non-zero exit code"
                raise OSError(retcode, "Starting " + service + " returned non-zero exit code")
        except OSError, e:
            print >>sys.stderr, "Execution failed:", e
            return 1


def set_metadata(metadata):
    # check if we have instance metadata to set - that's all we support anyway
    if 'instance' in metadata:

        region = os.environ['AWS_REGION']
        ec2 = boto.ec2.connect_to_region(region)
        instance_metadata = metadata['instance']

        # TODO: not sure there is a way to get the current instance id using boto...
        instance_id = urllib.urlopen("http://169.254.169.254/latest/meta-data/instance-id").read()
        resources   = [instance_id]

        if not ec2.create_tags(resources, instance_metadata):
            print >> sys.stderr, "Couldn't tag instance: " + instance_id
            raise Exception("Couldn't tag instance: " + instance_id);

        # we'll also tag volumes with the instance name metadata if it's available
        m = ec2.get_instance_metadata()

        # TODO: this doesn't seem inefficient - should be a way to query instance volumes directly
        volumes = [v for v in ec2.get_all_volumes() if v.attach_data.instance_id == m['instance-id']]

        if 'name' in instance_metadata:
            if not ec2.create_tags(volumes, {instance_metadata['name']}):
                print >> sys.stderr, "Couldn't tag volumes with instance name: " + instance_metadata['name']
                raise Exception("Couldn't tag volumes with instance name: " + instance_metadata['name']);


# get user data which is assumed to be JSON, parse it and return a JSON object
def get_userdata_json():
    # get user data
    user_data = boto.utils.get_instance_userdata()

    # parse our userdata - to make sure it's valid JSON (this will throw exceptions otherwise)
    json_data = json.loads(user_data)

    return json_data



if __name__ == '__main__':
    sys.exit(main())


