#!/usr/bin/env python
#
# Name: thermal.py, version: 1.0
#
# Description: Module contains the definitions of Thermal sensors
# related APIs for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

try:
    import os
    from sonic_platform_base.thermal_base import ThermalBase
    from sonic_platform.psu import Psu
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")


class Thermal(ThermalBase):
    """Nokia platform-specific Thermal class"""

    def __init__(self, thermal_index, map_index, thermal_name, stub):
        self.index = thermal_index
        self.map_index = map_index
        self.thermal_name = 'Thermal {}'.format(map_index)
        self.is_psu_thermal = False
        self.dependency = None
        self.stub = stub

    def get_name(self):
        """
        Retrieves the name of the thermal

        Returns:
            string: The name of the thermal
        """
        return self.thermal_name

    def get_presence(self):
        """
        Retrieves the presence of the thermal

        Returns:
            bool: True if thermal is present, False if not
        """
        if self.dependency:
            return self.dependency.get_presence()
        else:
            return True

    def get_model(self):
        """
        Retrieves the model number (or part number) of the Thermal

        Returns:
            string: Model/part number of Thermal
        """
        return 'NA'

    def get_serial(self):
        """
        Retrieves the serial number of the Thermal

        Returns:
            string: Serial number of Thermal
        """
        return 'NA'

    def get_status(self):
        """
        Retrieves the operational status of the thermal

        Returns:
            A boolean value, True if thermal is operating properly,
            False if not
        """
        if self.dependency:
            return self.dependency.get_status()
        else:
            return True

    def get_temperature(self):
        """
        Retrieves current temperature reading from thermal

        Returns:
            A float number of current temperature in Celsius up to
            nearest thousandth of one degree Celsius, e.g. 30.125
        """
        req_etype = devmgr_sonic_pb2.ReqTempOpsType.TEMP_OPS_GET_CURR_TEMP
        response = self.stub.ReqTempParams(devmgr_sonic_pb2.ReqTempParamsPb(type = req_etype,
                                                                       idx = self.map_index))
        thermal_temperature = float(response.curr_temp)

        return float("{:.3f}".format(thermal_temperature))

    def get_high_threshold(self):
        """
        Retrieves the high threshold temperature of thermal

        Returns:
            A float number, the high threshold temperature of thermal in
            Celsius up to nearest thousandth of one degree Celsius,
            e.g. 30.125
        """
        req_etype = devmgr_sonic_pb2.ReqTempOpsType.TEMP_OPS_GET_THRESHOLD
        response = self.stub.ReqTempParams(devmgr_sonic_pb2.ReqTempParamsPb(type = req_etype,
                                                                       idx = self.map_index))
        thermal_high_threshold = float(response.threshold)

        return float("{:.3f}".format(thermal_high_threshold))

    def get_low_threshold(self):
        """
        Retrieves the low threshold temperature of thermal

        Returns:
            A float number, the low threshold temperature of thermal in
            Celsius up to nearest thousandth of one degree Celsius,
            e.g. 30.125
        """
        thermal_low_threshold = 0
        return float("{:.3f}".format(thermal_low_threshold))

    def set_high_threshold(self, temperature):
        """
        Sets the high threshold temperature of thermal

        Args :
            temperature: A float number up to nearest thousandth of one
            degree Celsius, e.g. 30.125
        Returns:
            A boolean, True if threshold is set successfully, False if
            not
        """
        # Thermal threshold values are pre-defined based on HW.
        return False

    def set_low_threshold(self, temperature):
        """
        Sets the low threshold temperature of thermal

        Args :
            temperature: A float number up to nearest thousandth of one
            degree Celsius, e.g. 30.125
        Returns:
            A boolean, True if threshold is set successfully, False if
            not
        """
        # Thermal threshold values are pre-defined based on HW.
        return False
