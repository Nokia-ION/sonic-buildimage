#!/usr/bin/env python
#
# Name: psuutil.py, version: 1.0
#
# Description: Module contains the Nokia specific PSU util
# class object creation.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#
import imp
import sys

try:
    from sonic_common import nokia_psu_relay

except ImportError as e:
    raise ImportError("%s - required module not found" % e)

PsuUtil = nokia_psu_relay.getPsuUtil()
