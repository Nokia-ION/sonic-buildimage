#!/usr/bin/env python
#
# Name: platform.py, version: 1.0
#
# Description: Module contains the definitions of SONiC platform APIs 
# which provide the platform specific details
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#


import sys

try:
   from sonic_platform_base.platform_base import PlatformBase
   from chassis import Chassis
except ImportError as e:
   raise ImportError("%s - required module not found" % e)

platformDict = {'platform':'IXR7250'}

class Platform(PlatformBase):
    def __init__(self):
        self.platform = self.getPlatform()
        self.chassis = Chassis()

    def getPlatformDict(self):
        global platformDict
        if platformDict:
            return platformDict

    def readPlatformName(self):
        return self.getPlatformDict().get('platform')

    def getPlatform(self):
        platformCls = self.readPlatformName()
        return platformCls

    def get_chassis(self):
        # chassis = Chassis()
        return self.chassis
