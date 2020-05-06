#!/usr/bin/env python
#
# Name: component.py, version: 1.0
#
# Description: Module contains the definitions of firmware component
# related APIs for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#
#
try:
    import os
    import subprocess
    from sonic_platform_base.component_base import ComponentBase
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")


class Component(ComponentBase):
    """Nokia Platform-specific Component class"""

    CHASSIS_CPM_COMPONENTS = [
        [devmgr_sonic_pb2.HwFirmwareDeviceType.HW_FIRMWARE_DEVICE_BIOS,
         "BIOS",
         "Performs initialization of hardware components during booting"],
        [devmgr_sonic_pb2.HwFirmwareDeviceType.HW_FIRMWARE_DEVICE_FPGA1,
         "FPGA1",
         ("Platform management controller for on-board temperature "
          "monitoring, in-chassis power, Fan and LED control")]
    ]
    CHASSIS_IMM_COMPONENTS = [
        [devmgr_sonic_pb2.HwFirmwareDeviceType.HW_FIRMWARE_DEVICE_BIOS,
         "BIOS",
         "Performs initialization of hardware components during booting"],
        [devmgr_sonic_pb2.HwFirmwareDeviceType.HW_FIRMWARE_DEVICE_FPGA1,
         "FPGA1",
         ("Platform management controller for on-board temperature "
                  "monitoring, QSFP monitoring, LED control")],
        [devmgr_sonic_pb2.HwFirmwareDeviceType.HW_FIRMWARE_DEVICE_FPGA2,
         "FPGA2",
         "Manage Host bound traffic"]
    ]

    def __init__(self, component_index, is_cpm, stub):
        self.index = component_index
        self.stub = stub
        if is_cpm:
            self.dev_type = self.CHASSIS_CPM_COMPONENTS[self.index][0]
            self.name = self.CHASSIS_CPM_COMPONENTS[self.index][1]
            self.description = self.CHASSIS_CPM_COMPONENTS[self.index][2]
        else:
            self.dev_type = self.CHASSIS_IMM_COMPONENTS[self.index][0]
            self.name = self.CHASSIS_IMM_COMPONENTS[self.index][1]
            self.description = self.CHASSIS_IMM_COMPONENTS[self.index][2]

    def get_name(self):
        """
        Retrieves the name of the component

        Returns:
            A string containing the name of the component
        """
        return self.name

    def get_description(self):
        """
        Retrieves the description of the component

        Returns:
            A string containing the description of the component
        """
        return self.description

    def get_firmware_version(self):
        """
        Retrieves the firmware version of the component

        Returns:
            A string containing the firmware version of the component
        """
        req_etype = devmgr_sonic_pb2.ReqHwFirmwareOpsType.REQ_HWFW_OPS_SHOW_VERSION
        response = self.stub.ReqHwFirmwareInfo(devmgr_sonic_pb2.ReqHwFirmwareInfoPb(type=req_etype, dev_type=self.dev_type))

        return response.version

    def install_firmware(self, image_path):
        """
        Installs firmware to the component

        Args:
            image_path: A string, path to firmware image

        Returns:
            A boolean, True if install was successful, False if not
        """
        return False
