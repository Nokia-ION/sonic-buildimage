#!/usr/bin/env python
#
# Name: fan.py, version: 1.0
#
# Description: Module contains the definitions of FAN related APIs
# for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import os.path

try:
    from sonic_platform_base.fan_base import FanBase
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
    import subprocess
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

NOKIA_MAX_IXR7250_FAN_SPEED = 9000 #Max RPMs per fan
NOKIA_MAX_IXR7250_TOLERANCE = 10

class Fan(FanBase):
    """Nokia IXR-7250 Platform-specific Fan class"""

    def __init__(self, fantray_index, psu_fan, stub):
        self.max_fan_speed = NOKIA_MAX_IXR7250_FAN_SPEED
        self.is_psu_fan = psu_fan
        self.stub = stub
        if not self.is_psu_fan:
            self.fantray_idx = fantray_index
        else:
            self.fantray_idx = 0
            # Do we have PSU Fan?

    def get_name(self):
        """
        Retrieves the fan name
        Returns:
            string: The name of the device
        """
        if not self.is_psu_fan:
            return "FanTray{}".format(self.fantray_idx)
        else:
            return "PSU{} Fan Missing"

    def get_model(self):
        """
        Retrieves the part number of the FAN
        Returns:
            string: Part number of FAN
        """
        if nokia_common.is_cpm() == 0:
            return status

        req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
        req_etype = devmgr_sonic_pb2.ReqFanTrayEepromType.FAN_EEPROM_PARTNO
        response = self.stub.GetFanTrayEepromInfo(devmgr_sonic_pb2.ReqFanTrayEepromPb(idx=req_idx, etype = req_etype))

        fantray_partno = response.fantray_edata
        return fantray_partno

    def get_serial(self):
        """
        Retrieves the serial number of the FAN
        Returns:
            string: Serial number of FAN
        """
        if nokia_common.is_cpm() == 0:
            return status

        req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
        req_etype = devmgr_sonic_pb2.ReqFanTrayEepromType.FAN_EEPROM_SERIALNO
        response = self.stub.GetFanTrayEepromInfo(devmgr_sonic_pb2.ReqFanTrayEepromPb(idx=req_idx, etype = req_etype))

        fantray_serialno = response.fantray_edata
        return fantray_serialno

    def get_presence(self):
        """
        Retrieves the presence of the FAN
        Returns:
            bool: True if fan is present, False if not
        """
        status = False
        if nokia_common.is_cpm() == 0:
            return status

        req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
        req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_REQ_PRESENCE
        response = self.stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))

        presence_msg = response.fan_presence
        status = presence_msg.fantray_presence
        return status

    def get_status(self):
        """
        Retrieves the operational status of the FAN
        Returns:
            bool: True if FAN is operating properly, False if not
        """
        status = False
        if nokia_common.is_cpm() == 0:
            return status

        req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
        req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_REQ_STATUS
        response = self.stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))

        status_msg = response.fan_status
        #Empty, Init, SpinUp, Online are possible states
        if status_msg.fantray_status == 'Online':
            status = True

        return status

    def get_direction(self):
        """
        Retrieves the fan airflow direction
        Returns:
            A string, either FAN_DIRECTION_INTAKE or FAN_DIRECTION_EXHAUST
            depending on fan direction
        """
        direction = ['FAN_DIRECTION_INTAKE', 'FAN_DIRECTION_EXHAUST']
        fan_direction = 1
        return direction[fan_direction]

    def get_speed(self):
        """
        Retrieves the speed of fan
        Returns:
            int: percentage of the max fan speed
        """
        if nokia_common.is_cpm() == 0:
            return 0

        if self.get_presence():
            req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
            req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_GET_SPEED_RPM
            response = self.stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))

            speed_msg = response.fan_speed_rpm
            speed_in_rpm = speed_msg.fantray_speed_rpm
            speed = (100 * speed_in_rpm)/self.max_fan_speed
        else:
            speed = 0

        return speed

    def get_speed_tolerance(self):
        """
        Retrieves the speed tolerance of the fan
        Returns:
            An integer, the percentage of variance from target speed which is
        considered tolerable
        """
        tolerance = NOKIA_MAX_IXR7250_TOLERANCE

        return tolerance

    def disable_fan_algorithm(self, disable):
        if nokia_common.is_cpm() == 0:
            return status

        if self.get_presence():
            req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
            req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_DISABLE_ALGORITHM
            req_fan_algo = devmgr_sonic_pb2.SetFanTrayAlgorithmPb(fantray_algo_disable=disable)
            response = self.stub.SetFanTraySpeedParams(devmgr_sonic_pb2.ReqFanTraySpeedParamsPb(idx=req_idx, type=req_etype, fan_algo=req_fan_algo))
            status = True

        return status

    def set_speed(self, speed):
        """
        Set fan speed to expected value
        Args:
            speed: An integer, the percentage of full fan speed to set fan to,
                   in the range 0 (off) to 100 (full speed)
        Returns:
            bool: True if set success, False if fail.
        """
        status = False
        if nokia_common.is_cpm() == 0:
            return status

        if self.get_presence():
            req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
            req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_SET_SPEED
            req_fan_speed = devmgr_sonic_pb2.GetFanTraySpeedPercentPb(fantray_speed=speed)
            response = self.stub.SetFanTraySpeedParams(devmgr_sonic_pb2.ReqFanTraySpeedParamsPb(idx=req_idx, type=req_etype, fan_speed=req_fan_speed))
            status = True

        return status

    def set_status_led(self, color):
        """
        Set led to expected color
        Args:
            color: A string representing the color with which to set the
                   fan module status LED
        Returns:
            bool: True if set success, False if fail.
        """
        # Leds are controlled by device-manager
        status = False
        return status

    def get_status_led(self):
        """
        Gets the state of the Fan status LED

        Returns:
            A string, one of the predefined STATUS_LED_COLOR_* strings.
        """
        if self.get_status():
            return self.STATUS_LED_COLOR_GREEN
        else:
            return self.STATUS_LED_COLOR_OFF

    def get_target_speed(self):
        """
        Retrieves the target (expected) speed of the fan
        Returns:
            An integer, the percentage of full fan speed, in the range 0 (off)
                 to 100 (full speed)
        """
        if nokia_common.is_cpm() == 0:
            return 0

        if self.get_presence():
            req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=self.fantray_idx)
            req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_GET_SPEED
            response = self.stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))

            speed_msg = response.fan_speed
            speed = speed_msg.fantray_speed
        else:
            speed = 0

        return speed
