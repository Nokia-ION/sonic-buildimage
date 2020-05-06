#!/usr/bin/env python
#
# Name: test_sfp.py, version: 1.0
#
# Description: Module contains the definitions of Unit-test suite for
# SFP related APIs for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import os.path
import time
try:
    from sonic_platform.sfp import Sfp
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
    import subprocess
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

def test_sfp_status(sfp_event):
    port_list = sfp_event._get_transceiver_status()
    return list(port_list)

def test_sfp_transceiver_info(sfp):
    page_type = 'Data'
    xcvr_info = sfp.get_transceiver_info()
    if xcvr_info:
        print " Getting "+page_type+" page:"
        print "     Type:     "+xcvr_info['type']
    else:
        print " Failed to get "+page_type+" page"

def test_sfp_transceiver_bulk_status(sfp):
    page_type = 'Ctrl'
    dom_info = sfp.get_transceiver_bulk_status()
    if dom_info:
        print " Getting "+page_type+" page:"
        print "     Temp:     "+dom_info['temperature']
        print "     Volatage: "+dom_info['voltage']
    else:
        print " Failed to get "+page_type+" page"

def test_sfp_transceiver_threshold_info(sfp):
    page_type = 'Ext-Ctrl'
    thresh_info = sfp.get_transceiver_threshold_info()
    if thresh_info:
        print " Getting "+page_type+" page:"
        print "     Temp-high-alarm: "+thresh_info['temphighalarm']
    else:
        print " Failed to get "+page_type+" page"

def test_sfp_debug_eeprom(sfp):
    etype = devmgr_sonic_pb2.ReqSfpEepromType.SFP_EEPROM_DEBUG
    eeprom_info = sfp.debug_eeprom_ops(etype, 0, 128)

def run_all(sfp_list, sfp_event):
    port_status_list = test_sfp_status(sfp_event)

    for i in range(sfp_event.num_ports):
        if port_status_list[i] == True:
            print "For Port-"+str(i+1)+":"
            test_sfp_transceiver_info(sfp_list[i+1])
            test_sfp_transceiver_bulk_status(sfp_list[i+1])
            test_sfp_transceiver_threshold_info(sfp_list[i+1])
            #test_sfp_debug_eeprom(sfp_list[i+1])
            print "--------------------------------------------"
