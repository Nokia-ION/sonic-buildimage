#!/usr/bin/env python

#############################################################################
#
# Platform and model specific eeprom subclass, inherits from the base class,
# and provides the followings:
# - the eeprom format definition
# - specific encoder/decoder if there is special need
#############################################################################

try:
    import exceptions
    import binascii
    import time
    import optparse
    import warnings
    import os
    import sys
    from sonic_eeprom import eeprom_base
    from sonic_eeprom import eeprom_tlvinfo
    import subprocess
except ImportError, e:
    raise ImportError (str(e) + "- required module not found")

CMD_1="/usr/share/sonic/platform/get_hwsku"
CMD_2="/usr/share/sonic/device/x86_64-nokia_ixr7250-r0/get_hwsku"
EEPROM_PATH = "/etc/sonic/nokia/syseeprom.bin"
class board(eeprom_tlvinfo.TlvInfoDecoder):

    _TLV_INFO_MAX_LEN = 256

    def __init__(self, name, path, cpld_root, ro):
        if os.path.isfile(EEPROM_PATH) is not True:
            try:
                if os.path.isfile(CMD_1) is True:
                    os.popen('sudo {}'.format(CMD_1))
                else:
                    os.popen('sudo {}'.format(CMD_2)) 

                self.eeprom_path = EEPROM_PATH
	        super(board, self).__init__(self.eeprom_path, 0, '', True)            
            except:
                e = sys.exc_info()
                print("Fail to create syseeprom code: {}".format(e[1]))
        else:
	    self.eeprom_path = EEPROM_PATH
	    super(board, self).__init__(self.eeprom_path, 0, '', True)
