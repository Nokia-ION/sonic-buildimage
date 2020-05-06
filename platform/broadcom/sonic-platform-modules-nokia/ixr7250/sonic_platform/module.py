#!/usr/bin/env python

########################################################################
# Nokia IXR7250
#
# Module contains an implementation of SONiC Platform Base API and
# provides the Modules' information which are available in the platform
#
########################################################################


try:
    import os
    from sonic_platform_base.module_base import ModuleBase
    from sonic_platform.sfp import Sfp
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")


class Module(ModuleBase):
    """Nokia IXR Platform-specific Module class"""

    def __init__(self, module_index):
        self.index = module_index + 1
        self.port_start = (self.index - 1) * 16
        self.port_end = (self.index * 16) - 1
        self._component_name_list = ['IMM ASIC']
        self.eeprom_tlv_dict = dict()

        # Overriding _sfp_list class variable defined in ModuleBase, to
        # make it unique per Module object
        self._sfp_list = []

        # sfp.py will read eeprom contents and retrive the eeprom data.
        # It will also provide support sfp controls like reset and setting
        # low power mode.
        for index in range(self.port_start, self.port_end + 1):
            sfp_node = Sfp(index, 'QSFP-DD')
            self._sfp_list.append(sfp_node)

    def get_name(self):
        """
        Retrieves the name of the device

        Returns:
            string: The name of the device
        """
        return 'NA'

    def get_presence(self):
        """
        Retrieves the presence of the Module

        Returns:
            bool: True if Module is present, False if not
        """
        status = False

        return status

    def get_model(self):
        """
        Retrieves the part number of the module

        Returns:
            string: part number of module
        """
        return 'NA'

    def get_serial(self):
        """
        Retrieves the serial number of the module

        Returns:
            string: Serial number of module
        """
        return 'NA'

    def get_status(self):
        """
        Retrieves the operational status of the Module

        Returns:
            bool: True if Module is operating properly, False if not
        """
        status = False
        return status

    def get_base_mac(self):
        """
        Retrieves the base MAC address for the module

        Returns:
            A string containing the MAC address in the format
            'XX:XX:XX:XX:XX:XX'
        """
        # In S6100, individual modules doesn't have MAC address
        return '00:00:00:00:00:00'

    def get_serial_number(self):
        """
        Retrieves the hardware serial number for the module

        Returns:
            A string containing the hardware serial number for this module.
        """
        return 'NA'

    def get_system_eeprom_info(self):
        return self.eeprom_tlv_dict

    def get_firmware_version(self, component_name):
        return 'NA'
