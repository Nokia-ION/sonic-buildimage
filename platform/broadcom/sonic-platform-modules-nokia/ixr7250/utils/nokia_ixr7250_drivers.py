#!/usr/bin/env python
#
# Modified to work on Nokia IXR7250
#
# Based on accton_as7816_util.py
#
# Copyright (C) 2016 Accton Networks, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Usage: %(scriptName)s [options] command object

options:
    -h | --help     : this help message
    -d | --debug    : run with debug mode
    -f | --force    : ignore error during installation or clean
command:
    install     : install drivers
    clean       : uninstall drivers
    show        : show all system status
"""

import os
import commands
import sys, getopt
import binascii
import logging
import re
import time
import random
import optparse
from collections import namedtuple




PROJECT_NAME = 'ixr7250_64x'
version = '0.1.0'
verbose = False
DEBUG = False
args = []
FORCE = 0

if DEBUG == True:
    print sys.argv[0]
    print 'ARGV      :', sys.argv[1:]


def main():
    global DEBUG
    global args
    global FORCE

    if len(sys.argv)<2:
        show_help()

    options, args = getopt.getopt(sys.argv[1:], 'hdf', ['help',
                                                       'debug',
                                                       'force',
                                                          ])
    os.environ['PATH'] += ":/sbin"
    if DEBUG == True:
        print options
        print args
        print len(sys.argv)

    for opt, arg in options:
        if opt in ('-h', '--help'):
            show_help()
        elif opt in ('-d', '--debug'):
            DEBUG = True
            logging.basicConfig(level=logging.INFO)
        elif opt in ('-f', '--force'):
            FORCE = 1
        else:
            logging.info('no option')
    for arg in args:
        if arg == 'install':
           do_install()
        elif arg == 'clean':
           do_uninstall()
        else:
            show_help()

    return True

def show_help():
    print __doc__ % {'scriptName' : sys.argv[0].split("/")[-1]}
    sys.exit(0)

def my_log(txt):
    if DEBUG == True:
        print "[NOKIA]"+txt
    return

def log_os_system(cmd, show):
    logging.info('Run :'+cmd)
    status, output = commands.getstatusoutput(cmd)
    my_log (cmd +" with result: " + str(status))
    my_log ("      output: "+output)
    if status != 0:
        logging.info('Failed : '+cmd)
        if show:
            print('Failed :'+cmd)
    return  status, output

kos = [
'modprobe i2c_dev',
'modprobe 8021q',
'modprobe amd-xgbe',
'modprobe nokia_gpio_wdt']

def driver_check():
    for i in range(0,len(kos)):
        mods = kos[i].split()
        ret, lsmod = log_os_system("sudo lsmod| grep "+ mods[1], 0)
        logging.info('mods:'+lsmod)
        if len(lsmod) ==0:
            return False
    return True

def driver_install():
    global FORCE
    status, output = log_os_system("sudo depmod", 1)
    for i in range(0,len(kos)):
        ins = kos[-(i+1)].replace("modprobe", "sudo modprobe")
        status, output = log_os_system(ins, 1)
        if status:
            if FORCE == 0:
                return status
    return 0

def driver_uninstall():
    global FORCE
    for i in range(0,len(kos)):
        rm = kos[-(i+1)].replace("modprobe", "sudo modprobe -rq")
        rm = rm.replace("insmod", "rmmod")
        status, output = log_os_system(rm, 1)
        if status:
            if FORCE == 0:
                return status
    return 0

def do_install():
    print "Checking system...."
    if driver_check() == False:
        print "No driver, installing...."
        status = driver_install()
        if status:
            if FORCE == 0:
                return  status
    else:
        print PROJECT_NAME.upper()+" drivers detected...."
    return

def do_uninstall():
    print "Checking system...."
    if driver_check()== False :
        print PROJECT_NAME.upper() +" has no driver installed...."
    else:
        print "Removing installed driver...."
        status = driver_uninstall()
        if status:
            if FORCE == 0:
                return  status

    return

if __name__ == "__main__":
    main()

