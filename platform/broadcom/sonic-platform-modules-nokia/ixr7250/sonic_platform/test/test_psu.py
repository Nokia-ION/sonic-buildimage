#!/usr/bin/env python
#
# Name: test_psu.py, version: 1.0
#
# Description: Module contains the definitions of Unit-test suite for
# PSU related APIs for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import os.path
import time
try:
    from sonic_platform.psu import Psu
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
    import subprocess
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

def _get_index(psu):
    return str(psu.index)

def test_psu_set_status_led(psu, color):
    print "\tSet-status-led: Not supported"

def test_psu_get_status_led(psu):
    print "\tGet-status-led: "+str(psu.get_status_led())

def test_psu_get_status(psu):
    print "\tStatus: "+str(psu.get_status())

def test_psu_get_presence(psu):
    print "\tPresence: "+str(psu.get_presence())

def run_all(psu_list):
    for psu in psu_list:
        print "PSU-"+_get_index(psu)
        test_psu_get_presence(psu)
        test_psu_get_status(psu)
        test_psu_get_status_led(psu)
        test_psu_set_status_led(psu, psu.STATUS_LED_COLOR_OFF)
