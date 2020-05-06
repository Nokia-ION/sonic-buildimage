#!/usr/bin/env python
#
# Name: sonic_common.py, version: 1.0
#
# Description: Module contains the common definitions for Nokia
# specific platform monitoring code.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import imp
import sys
import os
import tempfile
import subprocess

NOKIA_UNIX_SOCKET_PREFIX = "unix://"
NOKIA_SONIC_UNIX_SOCKET_FOLDER = "/var/run/redis/"
NOKIA_DEVMGR_SONIC_UNIX_SOCKET_NAME = "devmgr_sonic_server"
NOKIA_DEVMGR_SONIC_SRVR_PORT = "50100"
NOKIA_DEVMGR_SONIC_SRVR_IP = "127.0.0.1"
NOKIA_DEVMGR_MONITOR_UPDATE_PERIOD_SECS = 1

NOKIA_DEVMGR_UNIX_SOCKET_PATH = NOKIA_UNIX_SOCKET_PREFIX+\
        NOKIA_SONIC_UNIX_SOCKET_FOLDER+\
        NOKIA_DEVMGR_SONIC_UNIX_SOCKET_NAME+":"+\
        NOKIA_DEVMGR_SONIC_SRVR_PORT

NOKIA_MAX_PORTS_PER_ASIC = 12

def setup():
    PROTO_PATH='/usr/local/lib/python2.7/dist-packages/sonic_common'
    if not os.path.exists(PROTO_PATH):
        PROTO_PATH='/usr/lib/python2.7/dist-packages/sonic_common'
    try:
        #Generate grpc related python file
        dirpath = tempfile.mkdtemp()
        sys.path.insert(0, dirpath)

        grpc_cmd =  'python -m grpc_tools.protoc -I '+PROTO_PATH
        grpc_cmd += ' --python_out='+dirpath
        grpc_cmd += ' --grpc_python_out='+dirpath
        grpc_cmd += ' devmgr_sonic.proto'
        grpc_output = subprocess.check_output(grpc_cmd, shell=True)

    except ImportError as e:
        raise ImportError("%s - unable to generate grpc python files" % e)

    return dirpath

def cleanup(dirpath):
    cleanup_cmd = 'rm -rf '+dirpath
    cleanup_output = subprocess.check_output(cleanup_cmd, shell=True)

def is_cpm():
    is_cpm_cmd = 'sonic-cfggen -j /etc/sonic/card_details.json -v is_cpm'
    is_cpm_output = subprocess.check_output(is_cpm_cmd, shell=True)
    if is_cpm_output.rstrip() == "No":
        return 0
    else:
        return 1
