#!/usr/bin/env python
#
# Name: psu.py, version: 1.0
#
# Description: Module contains the definitions of PSU related APIs
# for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

try:
    import os
    from sonic_platform_base.psu_base import PsuBase
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
    import subprocess
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

class Psu(PsuBase):
    """Nokia IXR-7250 Platform-specific PSU class"""


    def __init__(self, psu_index, stub):
        self.index = psu_index + 1
        self.stub = stub
        # Overriding _fan_list class variable defined in PsuBase, to
        # make it unique per Psu object
        #self._fan_list = []

    def get_name(self):
        """
        Retrieves the name of the device

        Returns:
            string: The name of the device
        """
        return "PSU{}".format(self.index)

    def get_presence(self):
        """
        Retrieves the presence of the Power Supply Unit (PSU)

        Returns:
            bool: True if PSU is present, False if not
        """
        status = False
        if nokia_common.is_cpm() == 0:
            return status

        req_etype = devmgr_sonic_pb2.ReqPsuOpsType.PSU_OPS_GET_PRESENCE
        response = self.stub.ReqPsuInfo(devmgr_sonic_pb2.ReqPsuInfoPb(psu_idx = self.index, type = req_etype))
        status = response.psu_presence

        return status

    def get_status(self):
        """
        Retrieves the operational status of the PSU

        Returns:
            bool: True if PSU is operating properly, False if not
        """
        status = False
        if nokia_common.is_cpm() == 0:
            return status

        req_etype = devmgr_sonic_pb2.ReqPsuOpsType.PSU_OPS_GET_STATUS
        response = self.stub.ReqPsuInfo(devmgr_sonic_pb2.ReqPsuInfoPb(psu_idx = self.index, type = req_etype))
        status = response.psu_status
        return status

    def get_powergood_status(self):
        """
        Retrieves the powergood status of PSU

        Returns:
            A boolean, True if PSU has stablized its output voltages and
            passed all its internal self-tests, False if not.
        """
        status = False
        if self.get_status():
            status = True

        return status

    def get_status_led(self):
        """
        Gets the state of the PSU status LED

        Returns:
            A string, one of the predefined STATUS_LED_COLOR_* strings.
        """
        if self.get_powergood_status():
            return self.STATUS_LED_COLOR_GREEN
        else:
            return self.STATUS_LED_COLOR_OFF

    def set_status_led(self, color):
        """
        Sets the state of the PSU status LED
        Args:
            color: A string representing the color with which to set the
                   PSU status LED
        Returns:
            bool: True if status LED state is set successfully, False if
                  not
        """
        return False

    def get_all_fans(self):
        return []

