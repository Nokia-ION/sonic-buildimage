#!/usr/bin/env python
#
# Name: led_control.py, version: 1.0
#
# Description: Module contains the Nokia specific LED control
# class object creation.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import imp
import sys

try:
    from sonic_common import nokia_led_relay

except ImportError as e:
    raise ImportError("%s - required module not found" % e)

LedControl = nokia_led_relay.getLedControl()
