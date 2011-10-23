#! /usr/bin/env python

# This script bootstraps a WebCentre Sites ContentServer EC2 instance.
#
# The script reads a list of files that need to be migrated, and a
# template containing properties and (optionally) default values.
# If values are defined for properties in the properties.template
# those values are used for the associated property. For all other
# properties without values, EC2 metadata is interrogated and those
# values are used. If after interrogating EC2 metadata, some property
# values aren't known, the bootstrapping script stops with an error
#

# Once all property values are defined, the bootstrap script loops
# through the list of files specified in the file list and replaces
# instances of the property name with it's associated value.
# Once this is completed, the EC2 instance has been bootstrapped.
#


import logging
import fileinput
import ConfigParser

# a list of files to migrate
AMI_FILELIST = 'cs-ami.filelist'
# a template of properties to migrate
AMI_PROPERTIES = 'cs-ami.properties.template'
LOG_FILE = 'cs-ami-bootstrap.log'


# set up logging
logger = logging.getLogger('cs-ami-bootstrap')
logger_file = logging.FileHandler(LOG_FILE)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
logger_file.setFormatter(formatter)
logger.addHandler(logger_file)
logger.setLevel(logging.DEBUG) 

# create case-sensitive properties lookup structure
config = ConfigParser.ConfigParser()
config.optionxform = str
config.read(AMI_PROPERTIES)


# migrate a file
def migrate_file(file):
    logger.info(file)

    # open file and do search and replace for each key/value pair
    for line in fileinput.FileInput(file, inplace=1):

        for key, value in config.items('host_config'):
            if key in line:
                logger.debug('Replacing ' + key + ' with ' + value + ' in line: ' + line)
                line = line.replace(key, value)

        print line,


# loop through ami filelist applying filters/patches to it
for line in open(AMI_FILELIST, 'r'):
    line = line.rstrip('\n')
    migrate_file(line)

