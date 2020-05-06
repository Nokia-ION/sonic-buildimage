#!/usr/bin/env python
#
# Name: test_fan.py, version: 1.0
#
# Description: Module contains the definitions of Unit-test suite for
# FAN related APIs for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import os.path
import time
try:
    from sonic_platform.fan import Fan
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
    import subprocess
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

WAIT_TIME_SEC = 2
TEST_SPEED_PERCENTAGE = 90

def _get_index(fan):
    return str(fan.fantray_idx)

def test_fan_set_status_led(fan, color):
    print "Fan-"+_get_index(fan)+" set-status-led: Not supported"

def test_fan_get_status_led(fan):
    print "Fan-"+_get_index(fan)+" get-status-led: "+str(fan.get_status_led())

def test_fan_set_speed(fan, speed):
    current_speed = fan.get_target_speed()
    print "Fan-"+_get_index(fan)+" disabling fan-algorithm"
    fan.disable_fan_algorithm(1)
    time.sleep(WAIT_TIME_SEC * 5)
    print "Fan-"+_get_index(fan)+" setting new-speed: "+str(fan.set_speed(speed))
    time.sleep(WAIT_TIME_SEC * 5)
    print "Fan-"+_get_index(fan)+" checking speed: "+str(fan.get_target_speed())
    time.sleep(WAIT_TIME_SEC)
    print "Fan-"+_get_index(fan)+" resetting old-speed: "+str(fan.set_speed(current_speed))
    time.sleep(WAIT_TIME_SEC * 5)
    print "Fan-"+_get_index(fan)+" checking speed: "+str(fan.get_target_speed())
    print "Fan-"+_get_index(fan)+" enabling fan-algorithm"
    fan.disable_fan_algorithm(0)

def test_fan_get_target_speed(fan):
    print "Fan-"+_get_index(fan)+" target speed: "+str(fan.get_target_speed())

def test_fan_get_current_speed(fan):
    print "Fan-"+_get_index(fan)+" current speed: "+str(fan.get_speed())

def test_fan_get_direction(fan):
    print "Fan-"+_get_index(fan)+" direction: "+(fan.get_direction())

def test_fan_get_status(fan):
    print "Fan-"+_get_index(fan)+" status: "+str(fan.get_status())

def test_fan_get_presence(fan):
    print "Fan-"+_get_index(fan)+" presence: "+str(fan.get_presence())

def test_fan_get_serial(fan):
    print "Fan-"+_get_index(fan)+" serial: "+fan.get_serial()

def test_fan_get_model(fan):
    print "Fan-"+_get_index(fan)+" model: "+fan.get_model()

def test_fan_get_name(fan):
    print "Fan-"+_get_index(fan)+" name: "+fan.get_name()

def run_all(fan_list):
    for fan in fan_list:
        test_fan_get_name(fan)
        test_fan_get_model(fan)
        test_fan_get_serial(fan)
        test_fan_get_presence(fan)
        test_fan_get_status(fan)
        test_fan_get_direction(fan)
        test_fan_get_current_speed(fan)
        test_fan_get_target_speed(fan)
        test_fan_set_speed(fan, TEST_SPEED_PERCENTAGE)
        test_fan_get_status_led(fan)
        test_fan_set_status_led(fan, fan.STATUS_LED_COLOR_OFF)
