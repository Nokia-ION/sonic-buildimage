# sfputil.py
#
# Platform-specific SFP transceiver interface for SONiC
#

try:
    import time
    from sonic_sfp.sfputilbase import SfpUtilBase
except ImportError as e:
    raise ImportError("%s - required module not found" % str(e))


class SfpUtil(SfpUtilBase):
    """Platform-specific SfpUtil class"""

    PORT_START = 0
    PORT_END = 31
    PORTS_IN_BLOCK = 32

    EEPROM_OFFSET = 50

    _port_to_eeprom_mapping = {}

    @property
    def port_start(self):
        raise NotImplementedError        
        # return self.PORT_START

    @property
    def port_end(self):
        raise NotImplementedError
        # return self.PORT_END

    @property
    def qsfp_ports(self):
        raise NotImplementedError
        # return range(0, self.PORTS_IN_BLOCK + 1)

    @property
    def port_to_eeprom_mapping(self):
        raise NotImplementedError
        # return self._port_to_eeprom_mapping

    def get_presence(self, port_num):     
        raise NotImplementedError
    
    def get_low_power_mode(self, port_num):
       
        raise NotImplementedError
  
    def set_low_power_mode(self, port_num, lpmode):
      
        raise NotImplementedError

    def reset(self, port_num):
       
        raise NotImplementedError

    def get_transceiver_change_event(self, timeout=2000):
        """
        TODO: This function need to be implemented
        when decide to support monitoring SFP(Xcvrd)
        on this platform.
        """
        raise NotImplementedError

    def __init__(self):
        return

