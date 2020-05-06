#!/usr/bin/env python
#
# Name: sfp.py, version: 1.0
#
# Description: Module contains the definitions of SFP related APIs
# for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

try:
    import os
    import time
    from sonic_platform_base.chassis_base import ChassisBase
    from sonic_platform_base.sfp_base import SfpBase
    from sonic_platform_base.sonic_sfp.sff8436 import sff8436InterfaceId
    from sonic_platform_base.sonic_sfp.sff8436 import sff8436Dom
    from sonic_platform_base.sonic_sfp.qsfpdd import qsfpDDInterfaceId
    from sonic_platform_base.sonic_sfp.qsfpdd import qsfpDDDom
    from sonic_platform_base.sonic_sfp.qsfpdd import sffbase
    import threading
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
    from sonic_daemon_base.daemon_base import Logger

except ImportError as e:
    raise ImportError(str(e) + "- required module not found")
logger = Logger("sfp")

READ_TYPE = 0
KEY_OFFSET = 1
KEY_WIDTH = 2
FUNC_NAME = 3
STRUCT_TYPE = 4
PAGE_NUMBER = 5


INFO_TYPE = devmgr_sonic_pb2.ReqSfpEepromType.SFP_EEPROM_DATA
CTRL_TYPE = devmgr_sonic_pb2.ReqSfpEepromType.SFP_EEPROM_CTRL
EXT_CTRL_TYPE = devmgr_sonic_pb2.ReqSfpEepromType.SFP_EEPROM_EXT_CTRL
UPPER_PAGE_TYPE = devmgr_sonic_pb2.ReqSfpEepromType.SFP_EEPROM_UPPER_PAGE
UPPER_BANK_PAGE_TYPE = devmgr_sonic_pb2.ReqSfpEepromType.SFP_EEPROM_UPPER_BANK_AND_PAGE


ALL_PAGES_TYPE = 99

STATUS_CONTROL_OFFSET = 110
STATUS_CONTROL_WIDTH = 1
TX_DISABLE_HARD_BIT = 7
TX_DISABLE_SOFT_BIT = 6

"""
INFO_OFFSET = 128
DOM_OFFSET = 0
DOM_OFFSET1 = 384
"""

cable_length_tup = ('LengthSMFkm-UnitsOfKm', 'LengthSMF(UnitsOf100m)',
                    'Length50um(UnitsOf10m)', 'Length62.5um(UnitsOfm)',
                    'LengthCable(UnitsOfm)', 'LengthOM3(UnitsOf10m)')

compliance_code_tup = ('10GEthernetComplianceCode', 'InfinibandComplianceCode',
                       'ESCONComplianceCodes', 'SONETComplianceCodes',
                       'EthernetComplianceCodes', 'FibreChannelLinkLength',
                       'FibreChannelTechnology', 'SFP+CableTechnology',
                       'FibreChannelTransmissionMedia', 'FibreChannelSpeed')

qsfp_cable_length_tup = ('Length(km)', 'Length OM3(2m)', 'Length OM2(m)',
                    'Length OM1(m)', 'Length Cable Assembly(m)')

qsfp_compliance_code_tup = (
    '10/40G Ethernet Compliance Code',
    'SONET Compliance codes',
    'SAS/SATA compliance codes',
    'Gigabit Ethernet Compliant codes',
    'Fibre Channel link length/Transmitter Technology',
    'Fibre Channel transmission media',
    'Fibre Channel Speed')


info_dict_keys = ['type', 'hardwarerev', 'serialnum',
                  'manufacturename', 'modelname', 'Connector',
                  'vendor_date', 'vendor_oui', 'power', 'cable_length',
                  'revision_compliance', 'flatmem', 'fw_revision', 
                  'app_advertising', 'supported_link_length', 'type_abbrv_name']                  

dom_dict_keys = ['rx_los',       'tx_fault',   'reset_status',
                 'power_lpmode', 'tx_disable', 'tx_disable_channel',
                 'temperature',  'voltage',    'rx1power',
                 'rx2power',     'rx3power',   'rx4power',
                 'tx1bias',      'tx2bias',    'tx3bias',
                 'tx4bias',      'tx1power',   'tx2power',
                 'tx3power',     'tx4power']

threshold_dict_keys = ['temphighalarm',    'temphighwarning',
                       'templowalarm',     'templowwarning',
                       'vcchighalarm',     'vcchighwarning',
                       'vcclowalarm',      'vcclowwarning',
                       'rxpowerhighalarm', 'rxpowerhighwarning',
                       'rxpowerlowalarm',  'rxpowerlowwarning',
                       'txpowerhighalarm', 'txpowerhighwarning',
                       'txpowerlowalarm',  'txpowerlowwarning',
                       'txbiashighalarm',  'txbiashighwarning',
                       'txbiaslowalarm',   'txbiaslowwarning']

sff8436_parser = {
     'reset_status': [CTRL_TYPE,   2,  1, 'parse_dom_status_indicator'],
           'rx_los': [CTRL_TYPE,   3,  1, 'parse_dom_tx_rx_los'],
         'tx_fault': [CTRL_TYPE,   4,  1, 'parse_dom_tx_fault'],
       'tx_disable': [CTRL_TYPE,  86,  1, 'parse_dom_tx_disable'],
     'power_lpmode': [CTRL_TYPE,  93,  1, 'parse_dom_power_control'],
   'power_override': [CTRL_TYPE,  93,  1, 'parse_dom_power_control'],
      'Temperature': [CTRL_TYPE,  22,  2, 'parse_temperature'],
          'Voltage': [CTRL_TYPE,  26,  2, 'parse_voltage'],
   'ChannelMonitor': [CTRL_TYPE,  34, 16, 'parse_channel_monitor_params'],

       'cable_type': [INFO_TYPE, -1, -1, 'parse_sfp_info_bulk'],
     'cable_length': [INFO_TYPE, -1, -1, 'parse_sfp_info_bulk'],
        'Connector': [INFO_TYPE,  0, 20, 'parse_sfp_info_bulk'],
             'type': [INFO_TYPE,  0, 20, 'parse_sfp_info_bulk'],
         'encoding': [INFO_TYPE,  0, 20, 'parse_sfp_info_bulk'],
   'ext_identifier': [INFO_TYPE,  0, 20, 'parse_sfp_info_bulk'],
 'ext_rateselect_compliance':
                     [INFO_TYPE,  0, 20, 'parse_sfp_info_bulk'],
 'nominal_bit_rate': [INFO_TYPE,  0, 20, 'parse_sfp_info_bulk'],
 'specification_compliance':
                     [INFO_TYPE,  0, 20, 'parse_sfp_info_bulk'],
 'type_abbrv_name':  [INFO_TYPE,  0,  20, 'parse_sfp_info_bulk'],
  'manufacturename': [INFO_TYPE, 20, 16, 'parse_vendor_name'],
       'vendor_oui': [INFO_TYPE,  37, 3, 'parse_vendor_oui'],
        'modelname': [INFO_TYPE, 40, 16, 'parse_vendor_pn'],
      'hardwarerev': [INFO_TYPE, 56,  2, 'parse_vendor_rev'],
        'serialnum': [INFO_TYPE, 68, 16, 'parse_vendor_sn'],
      'vendor_date': [INFO_TYPE, 84,  8, 'parse_vendor_date'],
  'ModuleThreshold': [EXT_CTRL_TYPE, 128, 24, 'parse_module_threshold_values'],
 'ChannelThreshold': [EXT_CTRL_TYPE, 176, 16, 'parse_channel_threshold_values'],
}


DD_INFO_STRUCT = 1
DD_DOM_STRUCT = 2

qsfpdd_parser = {
    'RevisionCompliance': [CTRL_TYPE,  1, 1, 'parse_rev_compliance', DD_INFO_STRUCT],
         'summary byte2': [CTRL_TYPE,  2, 1, 'parse_summary_byte_2', DD_INFO_STRUCT],
            'FW version': [CTRL_TYPE,  39, 2, 'parse_FW_rev', DD_INFO_STRUCT],
           'Temperature': [CTRL_TYPE,  14,  2, 'parse_temperature', DD_DOM_STRUCT],
               'Voltage': [CTRL_TYPE,  16,  2, 'parse_voltage', DD_DOM_STRUCT],
       'App advertising': [CTRL_TYPE,  85, 33, 'parse_app_advertising', DD_INFO_STRUCT],
        'ChannelMonitor': [CTRL_TYPE,  100, 6, 'parse_channel_monitor_params', DD_DOM_STRUCT],
          'Cable length': [INFO_TYPE,  0, 76, 'parse_sfp_info_bulk', DD_INFO_STRUCT],
          'Module power': [INFO_TYPE,  0, 76, 'parse_sfp_info_bulk', DD_INFO_STRUCT],
             'Connector': [INFO_TYPE,  0, 76, 'parse_sfp_info_bulk', DD_INFO_STRUCT],
       'type_abbrv_name': [INFO_TYPE,  0, 76, 'parse_sfp_info_bulk', DD_INFO_STRUCT],           
                  'type': [INFO_TYPE,  0, 76, 'parse_sfp_info_bulk', DD_INFO_STRUCT],           
       'manufacturename': [INFO_TYPE,  1, 16, 'parse_vendor_name', DD_INFO_STRUCT],
            'vendor_oui': [INFO_TYPE, 17,  3, 'parse_vendor_oui', DD_INFO_STRUCT],
             'modelname': [INFO_TYPE, 20, 16, 'parse_vendor_pn', DD_INFO_STRUCT],
           'hardwarerev': [INFO_TYPE, 36,  2, 'parse_vendor_rev', DD_INFO_STRUCT],
             'serialnum': [INFO_TYPE, 38, 16, 'parse_vendor_sn', DD_INFO_STRUCT],
           'vendor_date': [INFO_TYPE, 54,  8, 'parse_vendor_date', DD_INFO_STRUCT],
                   'MIT': [INFO_TYPE, 84,  1, 'parse_MIT', DD_INFO_STRUCT],
 'Supported Link Length': [UPPER_PAGE_TYPE, 4, 6, 'parse_supported_link_lengths', DD_INFO_STRUCT, 1],         
        'AlarmThreshold': [EXT_CTRL_TYPE,  0, 72, 'parse_alarm_warning_threshold', DD_DOM_STRUCT],
}

QSFP_TYPE = "QSFP"
QSFPDD_TYPE = "QSFP-DD"

"""
# definitions of the offset for values in OSFP info eeprom. These are used relative to upper memory page 0
QSFPDD_UPPER_PAGE0_OFFSET = 128
QSFPDD_TYPE_OFFSET = 128
QSFPDD_VENDOR_NAME_OFFSET = 129
QSFPDD_VENDOR_OUI_OFFSET = 145
QSFPDD_VENDOR_PN_OFFSET = 148
QSFPDD_VENDOR_REV_OFFSET = 164
QSFPDD_VENDOR_SN_OFFSET = 166
QSFPDD_VENDOR_DATE_OFFSET = 182
QSFPDD_MIT_OFFSET = 212
QSFPDD_MEDIA_TYPE_ENCODING_OFFSET = 85  # lower page 0
"""

class Sfp(SfpBase):
    """
    Nokia IXR-7250 Platform-specific Sfp class
    """
    
    instances = []

    # used by sfp_event to synchronize presence info
    @staticmethod
    def SfpHasBeenTransitioned(port, status):
        for inst in Sfp.instances:
           if (inst.index == port):
              logger.log_error("SfpHasBeenTransitioned: invalidating_page_cache for Sfp index{} : status is {}".format(inst.index, status))
              inst.invalidate_page_cache(ALL_PAGES_TYPE)
              return
        logger.log_error("SfpHasBeenTransitioned: no match for port index{}".format(port))        



    def __init__(self, index, sfp_type, stub):
        SfpBase.__init__(self)
        self.sfp_type = sfp_type
        self.index = index
        self.qsfpDDInfo = qsfpDDInterfaceId()
        self.qsfpDDDomInfo = qsfpDDDom(calibration_type=1)
        self.qsfpInfo = sff8436InterfaceId()
        self.qsfpDomInfo = sff8436Dom()
        self.lastPresence = False
     
        self.CTRL_TYPE_page = bytearray([0] * 128)
        self.INFO_TYPE_page = bytearray([0] * 128)
        self.EXT_CTRL_TYPE_page = bytearray([0] * 128)
        self.CTRL_TYPE_page_cached = False
        self.INFO_TYPE_page_cached = False        
        self.EXT_CTRL_TYPE_page_cached = False 

        self.stub = stub
        Sfp.instances.append(self)


    def invalidate_page_cache(self, page_type):
        # more logic is needed for DD extended pages/banks since there are many more
        if (page_type == CTRL_TYPE):
           self.CTRL_TYPE_page = None
           self.CTRL_TYPE_page_cached = False
        elif (page_type == INFO_TYPE):
           self.INFO_TYPE_page = None
           self.INFO_TYPE_page_cached = False
        elif (page_type == EXT_CTRL_TYPE):
           self.EXT_CTRL_TYPE_page = None
           self.EXT_CTRL_TYPE_page_cached = False
        elif (page_type == ALL_PAGES_TYPE):
           self.CTRL_TYPE_page = None
           self.CTRL_TYPE_page_cached = False
           self.INFO_TYPE_page = None
           self.INFO_TYPE_page_cached = False
           self.EXT_CTRL_TYPE_page = None
           self.EXT_CTRL_TYPE_page_cached = False
        else:
           logger.log_error("invalidate_page_cache for Ethernet{} unknown page_type{}".format(self.index, page_type))


    def debug_eeprom_ops(self, etype, offset, num_bytes):
        stub = self.stub

        response = stub.GetSfpEepromInfo(devmgr_sonic_pb2.ReqSfpEepromPb(hw_port_id=self.index,
                                                                    etype=etype,
                                                                    offset=offset,
                                                                    num_bytes=num_bytes))
        if response.data is None:
            return None

    def _read_eeprom_bytes(self, etype, offset, num_bytes, *more):
        stub = self.stub
        cached_page = None;
        page_num = 0;
        bank_num = 0;

        if (etype == CTRL_TYPE):
           if (self.CTRL_TYPE_page_cached == True):
              cached_page = self.CTRL_TYPE_page
        elif (etype == INFO_TYPE):
           if (self.INFO_TYPE_page_cached == True):
              cached_page = self.INFO_TYPE_page
        elif (etype == EXT_CTRL_TYPE):
           if (self.EXT_CTRL_TYPE_page_cached == True):
              cached_page = self.EXT_CTRL_TYPE_page
        elif (etype == UPPER_PAGE_TYPE):             
              page_num = more[0]
              logger.log_debug("_read_eeprom_bytes for Ethernet{} with UPPER_PAGE_TYPE : page {}".format(self.index, page_num))
        elif (etype == UPPER_BANK_PAGE_TYPE):             
              page_num = more[0]
              bank_num = more[1]
              logger.log_debug("_read_eeprom_bytes for Ethernet{} with UPPER_BANK_PAGE_TYPE : page {} bank {}".format(self.index, page_num, bank_num))              
        else:
            logger.log_debug("_read_eeprom_bytes for Ethernet{} with BAD etype{} offset{} and num_bytes{}".format(self.index, etype, offset, num_bytes))           
            return None

        if (cached_page is None):
            logger.log_debug("_read_eeprom_bytes for Ethernet{} with etype{} offset{} and num_bytes{}".format(self.index, etype, offset, num_bytes))
            response = stub.GetSfpEepromInfo(devmgr_sonic_pb2.ReqSfpEepromPb(hw_port_id=self.index,
                                                                        etype=etype,
                                                                        offset=offset,
                                                                        num_bytes=num_bytes,
                                                                        page=page_num))

            #logger.log_error("Getting eeprom data for SFP {} num-bytes {}".format(self.index, num_bytes))
            #import binascii
            #res = binascii.hexlify(bytearray(response.data))
            #if etype == CTRL_TYPE:
            #    logger.log_error("Eeprom ctrl '%s' - " % str(res))
            #elif etype == EXT_CTRL_TYPE:
            #    logger.log_error("Eeprom ext ctrl '%s' - " % str(res))
            #else:
            #    logger.log_error("Eeprom data '%s' - " % str(res))

            if response.data is None:
               return None

            raw = bytearray(response.data)

            # cache the page  
            # more logic is needed for DD extended pages/banks since there are many more
            if (etype == CTRL_TYPE):
               self.CTRL_TYPE_page = raw
               self.CTRL_TYPE_page_cached = True
            elif (etype == INFO_TYPE):
               self.INFO_TYPE_page = raw
               self.INFO_TYPE_page_cached = True
            elif (etype == EXT_CTRL_TYPE):
               self.EXT_CTRL_TYPE_page = raw
               self.EXT_CTRL_TYPE_page_cached = True
            #else:
            #   logger.log_error("_read_eeprom_bytes for Ethernet{} with BAD etype{} offset{} and num_bytes".format(self.index, etype, offset, num_bytes))           
            #   return None
        
        else:
            logger.log_debug("_read_eeprom_bytes CACHE HIT for Ethernet{} with etype{} offset{} and num_bytes{}".format(self.index, etype, offset, num_bytes))
            raw = cached_page

        eeprom_raw = [0] * num_bytes
        try:
            for n in range(0, num_bytes):#Since we are getting entire 128 bytes every single time
                eeprom_raw[n] = hex(raw[offset+n])[2:].zfill(2)
        except BaseException:
            return None

        return eeprom_raw


    def _get_eeprom_dd_dom_data(self, eeprom_key):
        eeprom_data = None
        read_type = None

        # if (self.qsfpInfo is None):
        #    return None
        
        if (self.sfp_type == QSFPDD_TYPE):
            #logger.log_error("Getting eeprom size for SFP {} type {} num-bytes {}".format(self.index, eeprom_key, qsfpdd_parser[eeprom_key][KEY_WIDTH]))
            read_type = qsfpdd_parser[eeprom_key][READ_TYPE]
            eeprom_data_raw = self._read_eeprom_bytes(
               qsfpdd_parser[eeprom_key][READ_TYPE],
               qsfpdd_parser[eeprom_key][KEY_OFFSET],
               qsfpdd_parser[eeprom_key][KEY_WIDTH])
        
            if (eeprom_data_raw is not None):
                eeprom_data = getattr(
                     self.qsfpDDDomInfo, qsfpdd_parser[eeprom_key][FUNC_NAME])(
                     eeprom_data_raw, 0)
        else:
            logger.log_error("_get_eeprom_dom_data called with non-DOM-data for SFP {} eeprom_key {}".format(self.index, eeprom_key))            

        return eeprom_data


    def _get_eeprom_data(self, eeprom_key):
        eeprom_data = None
        read_type = None

        if (self.sfp_type == QSFPDD_TYPE):
            read_type = qsfpdd_parser[eeprom_key][READ_TYPE]
            struct_type = qsfpdd_parser[eeprom_key][STRUCT_TYPE]
            
            # if PAGE_NUMBER parameter present
            if (len(qsfpdd_parser[eeprom_key]) > PAGE_NUMBER):          
                eeprom_data_raw = self._read_eeprom_bytes(
                  qsfpdd_parser[eeprom_key][READ_TYPE],
                  qsfpdd_parser[eeprom_key][KEY_OFFSET],
                  qsfpdd_parser[eeprom_key][KEY_WIDTH],
                  qsfpdd_parser[eeprom_key][PAGE_NUMBER])
            else:
                eeprom_data_raw = self._read_eeprom_bytes(
                  qsfpdd_parser[eeprom_key][READ_TYPE],
                  qsfpdd_parser[eeprom_key][KEY_OFFSET],
                  qsfpdd_parser[eeprom_key][KEY_WIDTH])                    

            if (eeprom_data_raw is not None):
                # if (read_type == INFO_TYPE):
                if (struct_type == DD_INFO_STRUCT):
                    eeprom_data = getattr(
                      self.qsfpDDInfo, qsfpdd_parser[eeprom_key][FUNC_NAME])(
                      eeprom_data_raw, 0)
                elif (struct_type == DD_DOM_STRUCT):
                    eeprom_data = getattr(
                      self.qsfpDDDomInfo, qsfpdd_parser[eeprom_key][FUNC_NAME])(
                      eeprom_data_raw, 0)
                else:
                   logger.log_error("_get_eeprom_data bad struct_type {} for SFP {} and eeprom_key {}".format(struct_type, self.index, eeprom_key))
                   return None

        else:
            op_type = sff8436_parser[eeprom_key][READ_TYPE]
            eeprom_data_raw = self._read_eeprom_bytes(
               sff8436_parser[eeprom_key][READ_TYPE], 
               sff8436_parser[eeprom_key][KEY_OFFSET],
               sff8436_parser[eeprom_key][KEY_WIDTH])
            
            if (eeprom_data_raw is not None):
                # INFO_TYPE is used to retrieve sff8436InterfaceId Info
                # CTRL_TYPE is used to retrieve sff8436Dom Info
                if (op_type == INFO_TYPE):
                    if ( self.qsfpInfo is None):
                        return None
                    eeprom_data = getattr(
                        self.qsfpInfo, sff8436_parser[eeprom_key][FUNC_NAME])(
                        eeprom_data_raw, 0)
                else:
                    if ( self.qsfpDomInfo is None):
                        return None
                    eeprom_data = getattr(
                        self.qsfpDomInfo, sff8436_parser[eeprom_key][FUNC_NAME])(
                        eeprom_data_raw, 0)        

        return eeprom_data



    def get_transceiver_info(self):
        """
        Retrieves transceiver info of this SFP
        """
        transceiver_info_dict = {}
        compliance_code_dict = {}
        transceiver_info_dict = dict.fromkeys(info_dict_keys, 'N/A')
        
        # get upper page 0 Administrative data directly
        # eeprom_raw = self._get_eeprom_data_raw()       
        # id = int(eeprom_raw[0], 16)    

        # pull the INFO page into cache so we can dynamically grab the ID
        eeprom_data_raw = self._read_eeprom_bytes(INFO_TYPE, 0, 128)
        if eeprom_data_raw == None:
            logger.log_error("_read_eeprom_bytes returned None for Ethernet{}".format(self.index))
            # self.sfp_type == 'None'
            return None 

        id = int(eeprom_data_raw[0], 16)
        # we should set type correctly ad hoc, immediately after module insertion has been detected. Do it here for now...
        if (id == 24):
           self.sfp_type = QSFPDD_TYPE
        elif (id == 17) or (id == 13):
           self.sfp_type = QSFP_TYPE
        else:
           logger.log_error("Unknown module ID {} for Ethernet{}".format(id, self.index))
           self.sfp_type = None
           return None

        # now parse up the cached page
        iface_data = self._get_eeprom_data('type')
        # check for None  
     
        logger.log_debug("get_transceiver_info iface_data for Ethernet{} is: {}".format(self.index, iface_data))  

        # we can also do check here less dynamically via
        # if (self.sfp_type == 'QSFP-DD'):
        # but dynamic is better so long as we already have module ID returned
        if(id == 24):
            logger.log_debug("QSFP-DD {} in get_transceiver_info for Ethernet{}".format(id, self.index ))
            if self.qsfpDDInfo is None:
                logger.log_error("qsfpDDInfo obj is None for QSFP-DD in get_transceiver_info for Ethernet{}".format(self.index ))              
                return None

            # BaseInformation
            # data_type = 'type'
            # iface_data = self._get_eeprom_data(data_type)

            if (iface_data is not None):
                connector = iface_data['data']['Connector']['value']
                length = iface_data['data']['CableAssemblyLength']['value']
                power = iface_data['data']['ModulePower']['value']
                identifier = iface_data['data']['type']['value']
                type_abbrv_name = iface_data['data']['type_abbrv_name']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

            # Vendor Date
            data_type = 'vendor_date'
            vendor_date_data = self._get_eeprom_data(data_type)
            if (vendor_date_data is not None):
                vendor_date = vendor_date_data['data']['VendorDataCode(YYYY-MM-DD Lot)']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

            # Vendor Name
            data_type = 'manufacturename'
            vendor_name_data = self._get_eeprom_data(data_type)
            if (vendor_name_data is not None):
                vendor_name = vendor_name_data['data']['Vendor Name']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

            # Vendor OUI
            data_type = 'vendor_oui'
            vendor_oui_data = self._get_eeprom_data(data_type)
            if (vendor_oui_data is not None):
                vendor_oui = vendor_oui_data['data']['Vendor OUI']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

            # Vendor PN
            data_type = 'modelname'
            vendor_pn_data = self._get_eeprom_data(data_type)
            if (vendor_pn_data is not None):
                vendor_pn = vendor_pn_data['data']['Vendor PN']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

            # Vendor Revision
            data_type = 'hardwarerev'
            vendor_rev_data = self._get_eeprom_data(data_type)
            if (vendor_rev_data is not None):
                vendor_rev = vendor_rev_data['data']['Vendor Rev']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

            # Vendor Serial Number
            data_type = 'serialnum'
            vendor_sn_data = self._get_eeprom_data(data_type)
            if (vendor_sn_data is not None):
                vendor_sn = vendor_sn_data['data']['Vendor SN']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

           # Revision Compliance
            data_type = 'RevisionCompliance'
            rev_compliance_data = self._get_eeprom_data(data_type)
            if (rev_compliance_data is not None):
                rev_compliance = rev_compliance_data['data']['Revision Compliance']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

           # Flat Mem
            data_type = 'summary byte2'
            summary_byte2_data = self._get_eeprom_data(data_type)
            if (summary_byte2_data is not None):
                summary_byte2 = summary_byte2_data['data']['Summary Byte2']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

           # FW version
            data_type = 'FW version'
            FW_rev_data = self._get_eeprom_data(data_type)
            if (FW_rev_data is not None):
                FW_rev = FW_rev_data['data']['FW Version']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

           # MIT
            data_type = 'MIT'
            MIT_data = self._get_eeprom_data(data_type)
            if (MIT_data is not None):
                MIT = MIT_data['data']['MIT']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

           # advertising
            data_type = 'App advertising'
            app_adv_data = self._get_eeprom_data(data_type)
            if (app_adv_data is not None):
                app_adv = app_adv_data['data']['App Advertising']['value']
            else:
                logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                return None

            # supported link lengths
            if (self.qsfpDDInfo.Flat_mem == False):
               data_type = 'Supported Link Length'
               SLL_data = self._get_eeprom_data(data_type)
               if (SLL_data is not None):
                   SLL = SLL_data['data']['Supported Link Length']['value']
               else:
                   logger.log_error("Failed to get {} for get_transceiver_info for Ethernet{}".format(data_type, self.index ))
                   SLL = "N/A"
            else:
               SLL = "N/A"



            # logger.log_error("  type {} name {} pn {} rev {} sn {} oui {} for Ethernet{}".format(sfp_type_data, sfp_vendor_name_data, sfp_vendor_pn_data, sfp_vendor_rev_data, sfp_vendor_sn_data, sfp_vendor_oui_data, sfp_vendor_data_data, self.index))

            # Fill The Dictionary and return
            transceiver_info_dict['type'] = identifier
            transceiver_info_dict['hardwarerev'] = vendor_rev
            transceiver_info_dict['serialnum'] = vendor_sn
            transceiver_info_dict['manufacturename'] = vendor_name
            transceiver_info_dict['modelname'] = vendor_pn
            transceiver_info_dict['Connector'] = connector
            transceiver_info_dict['vendor_date'] = vendor_date
            transceiver_info_dict['vendor_oui'] = vendor_oui
            transceiver_info_dict['revision_compliance'] = str(rev_compliance)
            transceiver_info_dict['power'] = power            
            transceiver_info_dict['cable_length'] = str(length) + 'm'
            transceiver_info_dict['summary_byte2'] = summary_byte2         
            transceiver_info_dict['fw_revision'] = str(FW_rev)
            transceiver_info_dict['app_advertising'] = app_adv
            transceiver_info_dict['type_abbrv_name']=type_abbrv_name
            transceiver_info_dict['supported_link_length'] = SLL           
            transceiver_info_dict['cable_type'] = MIT

            return transceiver_info_dict

        # we can also do check here less dynamically via
        # if (self.sfp_type == 'QSFP28'):
        # but dynamic is better so long as we already have module ID returned
        elif (id == 17) or (id == 13):  
            logger.log_debug("QSFP28 {} in get_transceiver_info for Ethernet{}".format(id, self.index ))
            if self.qsfpInfo is None:
                logger.log_error("qsfpInfo obj is None for QSFP28 in get_transceiver_info for Ethernet{}".format(self.index ))            
                return None
            
            connector = iface_data['data']['Connector']['value']
            encoding = iface_data['data']['EncodingCodes']['value']
            ext_id = iface_data['data']['Extended Identifier']['value']
            rate_identifier = iface_data['data']['RateIdentifier']['value']
            identifier = iface_data['data']['type']['value']
            type_abbrv_name=iface_data['data']['type_abbrv_name']['value']
            bit_rate = str(iface_data['data']['Nominal Bit Rate(100Mbs)']['value'])
            for key in qsfp_compliance_code_tup:
              if key in iface_data['data']['Specification compliance']['value']:
                  compliance_code_dict[key] = iface_data['data']['Specification compliance']['value'][key]['value']
            for key in qsfp_cable_length_tup:
              if key in iface_data['data']:
                  cable_type = key
                  cable_length = str(iface_data['data'][key]['value'])

            # Vendor Date
            vendor_date_data = self._get_eeprom_data('vendor_date')
            if (vendor_date_data is not None):
                vendor_date = vendor_date_data['data']['VendorDataCode(YYYY-MM-DD Lot)']['value']
            else:
               return transceiver_info_dict

            # Vendor Name
            vendor_name_data = self._get_eeprom_data('manufacturename')
            if (vendor_name_data is not None):
                vendor_name = vendor_name_data['data']['Vendor Name']['value']
            else:
                return transceiver_info_dict

            # Vendor OUI
            vendor_oui_data = self._get_eeprom_data('vendor_oui')
            if (vendor_oui_data is not None):
                vendor_oui = vendor_oui_data['data']['Vendor OUI']['value']
            else:
                return transceiver_info_dict

            # Vendor PN
            vendor_pn_data = self._get_eeprom_data('modelname')
            if (vendor_pn_data is not None):
                vendor_pn = vendor_pn_data['data']['Vendor PN']['value']
            else:
                return transceiver_info_dict

            # Vendor Revision
            vendor_rev_data = self._get_eeprom_data('hardwarerev')
            if (vendor_rev_data is not None):
                vendor_rev = vendor_rev_data['data']['Vendor Rev']['value']
            else:
                return transceiver_info_dict

            # Vendor Serial Number
            vendor_sn_data = self._get_eeprom_data('serialnum')
            if (vendor_sn_data is not None):
                vendor_sn = vendor_sn_data['data']['Vendor SN']['value']
            else:
                return transceiver_info_dict

            # Fill The Dictionary and return
            transceiver_info_dict['type'] = identifier
            transceiver_info_dict['hardwarerev'] = vendor_rev
            transceiver_info_dict['serialnum'] = vendor_sn
            transceiver_info_dict['manufacturename'] = vendor_name
            transceiver_info_dict['modelname'] = vendor_pn
            transceiver_info_dict['Connector'] = connector
            transceiver_info_dict['encoding'] = encoding
            transceiver_info_dict['ext_identifier'] = ext_id
            transceiver_info_dict['ext_rateselect_compliance'] = rate_identifier
            transceiver_info_dict['cable_type'] = cable_type
            transceiver_info_dict['cable_length'] = cable_length
            transceiver_info_dict['nominal_bit_rate'] = bit_rate
            transceiver_info_dict['specification_compliance'] = str(compliance_code_dict)
            transceiver_info_dict['vendor_date'] = vendor_date
            transceiver_info_dict['vendor_oui'] = vendor_oui
            transceiver_info_dict['type_abbrv_name']=type_abbrv_name

            return transceiver_info_dict

        else:
            logger.log_error("unrecognizedID is {} in get_transceiver_info for Ethernet{}".format(id, self.index ))
            return None

            # if self.sfp_type == QSFP_TYPE:
        """
      

        return transceiver_info_dict
        """


    def get_transceiver_threshold_info(self):
        """
        Retrieves transceiver threshold info of this SFP
        """
        transceiver_dom_threshold_dict = {}
        transceiver_dom_threshold_dict = dict.fromkeys(
                threshold_dict_keys, 'N/A')

        # flush cache for all germane pages where dynamically updated data exists
        self.invalidate_page_cache(CTRL_TYPE)
        self.invalidate_page_cache(EXT_CTRL_TYPE)        

        if (self.sfp_type ==QSFPDD_TYPE):
            if (self.qsfpDDInfo.Flat_mem == False):
                alarm_threshold_data = self._get_eeprom_data('AlarmThreshold')
                if (alarm_threshold_data is not None):
                    tempHighAlarm = alarm_threshold_data['data']['TempHighAlarm']['value']
                    tempLowAlarm = alarm_threshold_data['data']['TempLowAlarm']['value']
                    tempHighWarn = alarm_threshold_data['data']['TempHighWarning']['value']
                    tempLowWarn = alarm_threshold_data['data']['TempLowWarning']['value']
                    vccHighAlarm = alarm_threshold_data['data']['VoltageHighAlarm']['value']
                    vccLowAlarm = alarm_threshold_data['data']['VoltageLowAlarm']['value']
                    vccHighWarn = alarm_threshold_data['data']['VoltageHighWarning']['value']
                    vccLowWarn = alarm_threshold_data['data']['VoltageLowWarning']['value']

                    txBiasHighAlarm = alarm_threshold_data['data']['BiasHighAlarm']['value']
                    txBiasLowAlarm = alarm_threshold_data['data']['BiasLowAlarm']['value']
                    txBiasHighWarn = alarm_threshold_data['data']['BiasHighWarning']['value']
                    txBiasLowWarn = alarm_threshold_data['data']['BiasLowWarning']['value']
                    txPowerHighAlarm = alarm_threshold_data['data']['TXPowerHighAlarm']['value']
                    txPowerLowAlarm = alarm_threshold_data['data']['TXPowerLowAlarm']['value']
                    txPowerHighWarn = alarm_threshold_data['data']['TXPowerHighWarning']['value']
                    txPowerLowWarn = alarm_threshold_data['data']['TXPowerLowWarning']['value']
                    rxPowerHighAlarm = alarm_threshold_data['data']['RXPowerHighAlarm']['value']
                    rxPowerLowAlarm = alarm_threshold_data['data']['RXPowerLowAlarm']['value']
                    rxPowerHighWarn = alarm_threshold_data['data']['RXPowerHighWarning']['value']
                    rxPowerLowWarn = alarm_threshold_data['data']['RXPowerLowWarning']['value']
                else:
                    logger.log_error("Failed to get {} for get_transceiver_threshold_info for Ethernet{}".format('alarm_threshold', self.index ))
                    return None
            else:
                logger.log_debug("Flat_mem; Skipped {} for get_transceiver_threshold_info for Ethernet{}".format('alarm_threshold', self.index ))              
                return None  

            transceiver_dom_threshold_dict['temphighalarm'] = tempHighAlarm
            transceiver_dom_threshold_dict['templowalarm'] = tempLowAlarm
            transceiver_dom_threshold_dict['temphighwarning'] = tempHighWarn
            transceiver_dom_threshold_dict['templowwarning'] = tempLowWarn
            transceiver_dom_threshold_dict['vcchighalarm'] = vccHighAlarm
            transceiver_dom_threshold_dict['vcclowalarm'] = vccLowAlarm
            transceiver_dom_threshold_dict['vcchighwarning'] = vccHighWarn
            transceiver_dom_threshold_dict['vcclowwarning'] = vccLowWarn
            transceiver_dom_threshold_dict['txbiashighalarm'] = txBiasHighAlarm
            transceiver_dom_threshold_dict['txbiaslowalarm'] = txBiasLowAlarm
            transceiver_dom_threshold_dict['txbiashighwarning'] = txBiasHighWarn
            transceiver_dom_threshold_dict['txbiaslowwarning'] = txBiasLowWarn
            transceiver_dom_threshold_dict['txpowerhighalarm'] = txPowerHighAlarm
            transceiver_dom_threshold_dict['txpowerlowalarm'] = txPowerLowAlarm
            transceiver_dom_threshold_dict['txpowerhighwarning'] = txPowerHighWarn
            transceiver_dom_threshold_dict['txpowerlowwarning'] = txPowerLowWarn
            transceiver_dom_threshold_dict['rxpowerhighalarm'] = rxPowerHighAlarm
            transceiver_dom_threshold_dict['rxpowerlowalarm'] = rxPowerLowAlarm
            transceiver_dom_threshold_dict['rxpowerhighwarning'] = rxPowerHighWarn
            transceiver_dom_threshold_dict['rxpowerlowwarning'] = rxPowerLowWarn

            return transceiver_dom_threshold_dict
        elif (self.sfp_type == QSFP_TYPE):

            # Module Threshold
            module_threshold_data = self._get_eeprom_data('ModuleThreshold')
            # Channel Threshold
            channel_threshold_data = self._get_eeprom_data('ChannelThreshold')

            if (channel_threshold_data is not None and module_threshold_data is not None):
                transceiver_dom_threshold_dict['temphighalarm'] = module_threshold_data['data']['TempHighAlarm']['value']
                transceiver_dom_threshold_dict['temphighwarning'] = module_threshold_data['data']['TempHighWarning']['value']
                transceiver_dom_threshold_dict['templowalarm'] = module_threshold_data['data']['TempLowAlarm']['value']
                transceiver_dom_threshold_dict['templowwarning'] = module_threshold_data['data']['TempLowWarning']['value']
                transceiver_dom_threshold_dict['vcchighalarm'] = module_threshold_data['data']['VccHighAlarm']['value']
                transceiver_dom_threshold_dict['vcchighwarning'] = module_threshold_data['data']['VccHighWarning']['value']
                transceiver_dom_threshold_dict['vcclowalarm'] = module_threshold_data['data']['VccLowAlarm']['value']
                transceiver_dom_threshold_dict['vcclowwarning'] = module_threshold_data['data']['VccLowWarning']['value']
                transceiver_dom_threshold_dict['rxpowerhighalarm'] = channel_threshold_data['data']['RxPowerHighAlarm']['value']
                transceiver_dom_threshold_dict['rxpowerhighwarning'] = channel_threshold_data['data']['RxPowerHighWarning']['value']
                transceiver_dom_threshold_dict['rxpowerlowalarm'] = channel_threshold_data['data']['RxPowerLowAlarm']['value']
                transceiver_dom_threshold_dict['rxpowerlowwarning'] = channel_threshold_data['data']['RxPowerLowWarning']['value']
                transceiver_dom_threshold_dict['txbiashighalarm'] = channel_threshold_data['data']['TxBiasHighAlarm']['value']
                transceiver_dom_threshold_dict['txbiashighwarning'] = channel_threshold_data['data']['TxBiasHighWarning']['value']
                transceiver_dom_threshold_dict['txbiaslowalarm'] = channel_threshold_data['data']['TxBiasLowAlarm']['value']
                transceiver_dom_threshold_dict['txbiaslowwarning'] = channel_threshold_data['data']['TxBiasLowWarning']['value']
            else:
                return transceiver_dom_threshold_dict
        else:
           logger.log_error("get_transceiver_threshold_info: Unknown module type {} for Ethernet{}".format(self.sfp_type, self.index))
           return None 


    def get_transceiver_bulk_status(self):
        """
        Retrieves transceiver bulk status of this SFP
        """
        tx_bias_list = []
        rx_power_list = []
        transceiver_dom_dict = {}
        transceiver_dom_dict = dict.fromkeys(dom_dict_keys, 'N/A')

        # flush cache for all germane pages where dynamically updated data exists
        self.invalidate_page_cache(CTRL_TYPE)
        self.invalidate_page_cache(EXT_CTRL_TYPE)        

        # RxLos
        rx_los = self.get_rx_los()

        # TxFault
        tx_fault = self.get_tx_fault()

        # ResetStatus
        reset_state = self.get_reset_status()

        # LowPower Mode
        lp_mode = self.get_low_power_mode()

        # TxDisable
        tx_disable = self.get_tx_disable()

        # TxDisable Channel
        tx_disable_channel = self.get_tx_disable_channel()

        # Temperature
        temperature = self.get_temperature()

        # Voltage
        voltage = self.get_voltage()

        # Channel Monitor
        channel_monitor_data = self._get_eeprom_data('ChannelMonitor')

        if (self.sfp_type == QSFPDD_TYPE):
           if (channel_monitor_data is not None):
               logger.log_error("channel_monitor_data for Ethernet{} is {}".format(self.index, channel_monitor_data))
               transceiver_dom_dict['tx1power'] = channel_monitor_data['data']['TXPower']['value']
               transceiver_dom_dict['rx1power'] = channel_monitor_data['data']['RXPower']['value']
               transceiver_dom_dict['tx1bias']  = channel_monitor_data['data']['TXBias']['value']
           else:
               transceiver_dom_dict['tx1power'] = "N/A"
               transceiver_dom_dict['rx1power'] = "N/A"
               transceiver_dom_dict['tx1bias']  = "N/A"
          
           transceiver_dom_dict['rx_los'] = rx_los
           transceiver_dom_dict['tx_fault'] = tx_fault
           transceiver_dom_dict['reset_status'] = reset_state
           transceiver_dom_dict['power_lpmode'] = lp_mode
           transceiver_dom_dict['tx_disable'] = tx_disable
           transceiver_dom_dict['tx_disable_channel'] = tx_disable_channel
           transceiver_dom_dict['temperature'] = temperature
           transceiver_dom_dict['voltage'] = voltage
           return transceiver_dom_dict
        else:
           # QSFP
           if (channel_monitor_data is not None):
                tx_bias = channel_monitor_data['data']['TX1Bias']['value']
                tx_bias_list.append(tx_bias)
                tx_bias = channel_monitor_data['data']['TX2Bias']['value']
                tx_bias_list.append(tx_bias)
                tx_bias = channel_monitor_data['data']['TX3Bias']['value']
                tx_bias_list.append(tx_bias)
                tx_bias = channel_monitor_data['data']['TX4Bias']['value']
                tx_bias_list.append(tx_bias)
                rx_power = channel_monitor_data['data']['RX1Power']['value']
                rx_power_list.append(rx_power)
                rx_power = channel_monitor_data['data']['RX2Power']['value']
                rx_power_list.append(rx_power)
                rx_power = channel_monitor_data['data']['RX3Power']['value']
                rx_power_list.append(rx_power)
                rx_power = channel_monitor_data['data']['RX4Power']['value']
                rx_power_list.append(rx_power)
           else:
                return transceiver_dom_dict

           transceiver_dom_dict['rx_los'] = rx_los
           transceiver_dom_dict['tx_fault'] = tx_fault
           transceiver_dom_dict['reset_status'] = reset_state
           transceiver_dom_dict['power_lpmode'] = lp_mode
           transceiver_dom_dict['tx_disable'] = tx_disable
           transceiver_dom_dict['tx_disable_channel'] = tx_disable_channel
           transceiver_dom_dict['temperature'] = temperature
           transceiver_dom_dict['voltage'] = voltage
           transceiver_dom_dict['tx1bias'] = tx_bias_list[0]
           transceiver_dom_dict['tx2bias'] = tx_bias_list[1]
           transceiver_dom_dict['tx3bias'] = tx_bias_list[2]
           transceiver_dom_dict['tx4bias'] = tx_bias_list[3]
           transceiver_dom_dict['rx1power'] = rx_power_list[0]
           transceiver_dom_dict['rx2power'] = rx_power_list[1]
           transceiver_dom_dict['rx3power'] = rx_power_list[2]
           transceiver_dom_dict['rx4power'] = rx_power_list[3]
           return transceiver_dom_dict

    def get_name(self):
        """
        Retrieves the name of the sfp
        Returns : QSFP or QSFP+ or QSFP28
        """
        iface_data = self._get_eeprom_data('type')
        if (iface_data is not None):
            identifier = iface_data['data']['type']['value']
        else:
            return None

        return identifier

    def get_presence(self):
        """
        Retrieves the presence of the sfp
        """
        stub = self.stub
        #logger.log_error("Get-presence with thread-{} start for SFP {}".format(str(threading.current_thread().ident), self.index))
        op_type = devmgr_sonic_pb2.ReqSfpOpsType.SFP_OPS_REQ_PRESENCE
        response = stub.ManageSfpOpsInfo(devmgr_sonic_pb2.ReqSfpOpsPb(type=op_type,
                                                                      hw_port_id_begin=self.index))

        status_msg = response.sfp_status
        if (self.lastPresence != status_msg):
           logger.log_error("get_presence status changed for SFP {} to {}".format(self.index, status_msg))
           self.lastPresence = status_msg
           # really only need to do this on transition to present, but since it's not an expensive operation...
           self.invalidate_page_cache(ALL_PAGES_TYPE)

        return status_msg.status

    def get_model(self):
        """
        Retrieves the model number (or part number) of the sfp
        """
        vendor_pn_data = self._get_eeprom_data('modelname')
        if (vendor_pn_data is not None):
            vendor_pn = vendor_pn_data['data']['Vendor PN']['value']
        else:
            return None

        return vendor_pn

    def get_serial(self):
        """
        Retrieves the serial number of the sfp
        """
        vendor_sn_data = self._get_eeprom_data('serialnum')
        if (vendor_sn_data is not None):
            vendor_sn = vendor_sn_data['data']['Vendor SN']['value']
        else:
            return None

        return vendor_sn

    def get_reset_status(self):
        """
        Retrieves the reset status of SFP
        """
        reset_status = False

        if (self.sfp_type == QSFPDD_TYPE):
            # check Sfp::IsOpticsReady at dev-mgr (or similar)
            # stub for now
            pass
        elif (self.sfp_type == QSFP_TYPE):
            # check Sfp::IsOpticsReady at dev-mgr (or similar)
            # stub for now
            pass
        else:
            logger.log_error("get_tx_fault for Ethernet{} found invalid sfp_type{}".format(self.index, self.sfp_type))

        return reset_status

    def get_rx_los(self):
        """
        Retrieves the RX LOS (lost-of-signal) status of SFP
        """
        rx_los = None
        rx_los_list = []
        if (self.sfp_type == QSFPDD_TYPE):
            # check for rx los implemented and then gather status
            # stub for now
            rx_los = False

        elif (self.sfp_type == QSFP_TYPE):
            rx_los_data = self._get_eeprom_data('rx_los')
            if (rx_los_data is not None):
                rx_los = rx_los_data['data']['Rx1LOS']['value']
                if (rx_los is 'On'):
                    rx_los_list.append(True)
                else:
                    rx_los_list.append(False)
                rx_los = rx_los_data['data']['Rx2LOS']['value']
                if (rx_los is 'On'):
                    rx_los_list.append(True)
                else:
                    rx_los_list.append(False)
                rx_los = rx_los_data['data']['Rx3LOS']['value']
                if (rx_los is 'On'):
                    rx_los_list.append(True)
                else:
                    rx_los_list.append(False)
                rx_los = rx_los_data['data']['Rx4LOS']['value']
                if (rx_los is 'On'):
                    rx_los_list.append(True)
                else:
                    rx_los_list.append(False)

                if (rx_los_list[0] and rx_los_list[1]
                        and rx_los_list[2] and rx_los_list[3]):
                    rx_los = True
                else:
                    rx_los = False
        else:
            logger.log_error("get_rx_los for Ethernet{} found invalid sfp_type{}".format(self.index, self.sfp_type))

        return rx_los


    def get_tx_fault(self):
        """
        Retrieves the TX fault status of SFP
        """
        tx_fault = False
        tx_fault_list = []

        if (self.sfp_type == QSFPDD_TYPE):
            # check for tx fault implemented and then gather status
            # stub for now
            tx_los = False

        elif (self.sfp_type == QSFP_TYPE):
            tx_fault_data = self._get_eeprom_data('tx_fault')
            if (tx_fault_data is not None):
                tx_fault = tx_fault_data['data']['Tx1Fault']['value']
                if (tx_fault is 'On'):
                    tx_fault_list.append(True)
                else:
                    tx_fault_list.append(False)
                tx_fault = tx_fault_data['data']['Tx2Fault']['value']
                if (tx_fault is 'On'):
                    tx_fault_list.append(True)
                else:
                    tx_fault_list.append(False)
                tx_fault = tx_fault_data['data']['Tx3Fault']['value']
                if (tx_fault is 'On'):
                    tx_fault_list.append(True)
                else:
                    tx_fault_list.append(False)
                tx_fault = tx_fault_data['data']['Tx4Fault']['value']
                if (tx_fault is 'On'):
                    tx_fault_list.append(True)
                else:
                    tx_fault_list.append(False)

                if (tx_fault_list[0] and tx_fault_list[1]
                        and tx_fault_list[2] and tx_fault_list[3]):
                    tx_fault = True
                else:
                    tx_fault = False
        else:
            logger.log_error("get_tx_fault for Ethernet{} found invalid sfp_type{}".format(self.index, self.sfp_type))

        return tx_fault




    def get_tx_disable(self):
        """
        Retrieves the tx_disable status of this SFP
        """
        tx_disable = False
        tx_disable_list = []

        if (self.sfp_type == QSFPDD_TYPE):
            # check for tx disable implemented and then gather status
            # stub for now
            pass
        elif (self.sfp_type == QSFP_TYPE):
            tx_disable_data = self._get_eeprom_data('tx_disable')
            if (tx_disable_data is not None):
                tx_disable = tx_disable_data['data']['Tx1Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(True)
                else:
                    tx_disable_list.append(False)
                tx_disable = tx_disable_data['data']['Tx2Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(True)
                else:
                    tx_disable_list.append(False)
                tx_disable = tx_disable_data['data']['Tx3Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(True)
                else:
                    tx_disable_list.append(False)
                tx_disable = tx_disable_data['data']['Tx4Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(True)
                else:
                    tx_disable_list.append(False)

                if (tx_disable_list[0] and tx_disable_list[1]
                        and tx_disable_list[2] and tx_disable_list[3]):
                    tx_disable = True
                else:
                    tx_disable = False
        else:
            logger.log_error("get_tx_disable for Ethernet{} found invalid sfp_type{}".format(self.index, self.sfp_type))

        return tx_disable

    def get_tx_disable_channel(self):
        """
        Retrieves the TX disabled channels in this SFP
        """
        tx_disable = None
        tx_disable_list = []
        tx_disable_channel = 0        

        if (self.sfp_type == QSFPDD_TYPE):
            # check for tx disabled channels implemented and then gather status
            # stub for now
            pass
        elif (self.sfp_type == QSFP_TYPE):
            tx_disable_data = self._get_eeprom_data('tx_disable')
            if (tx_disable_data is not None):
                tx_disable = tx_disable_data['data']['Tx1Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(1)
                else:
                    tx_disable_list.append(0)
                tx_disable = tx_disable_data['data']['Tx2Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(1)
                else:
                    tx_disable_list.append(0)
                tx_disable = tx_disable_data['data']['Tx3Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(1)
                else:
                    tx_disable_list.append(0)
                tx_disable = tx_disable_data['data']['Tx4Disable']['value']
                if (tx_disable is 'On'):
                    tx_disable_list.append(1)
                else:
                    tx_disable_list.append(0)

                bit4 = int(tx_disable_list[3]) * 8
                bit3 = int(tx_disable_list[2]) * 4
                bit2 = int(tx_disable_list[1]) * 2
                bit1 = int(tx_disable_list[0]) * 1

                tx_disable_channel = hex(bit4 + bit3 + bit2 + bit1)

        else:
            logger.log_error("get_tx_disable_channel for Ethernet{} found invalid sfp_type{}".format(self.index, self.sfp_type))

        return tx_disable_channel


    def get_low_power_mode(self):
        """
        Retrieves the lpmode (low power mode) status of this SFP
        """
        lpmode_state = False
        # check for low power mode via dev-mgr and/or Module Global and Squelch Mode Controls 
        # dev-mgr should implicitly take module out of low power mode if it is present
        # stub for now

        return lpmode_state

    def get_power_override(self):
        """
        Retrieves the power-override status of this SFP
        """
        power_override_state = False
        return power_override_state

    def get_temperature(self):
        """
        Retrieves the temperature of this SFP
        """
        temperature = None

        temperature_data = self._get_eeprom_data('Temperature')
        if (temperature_data is not None):
            temperature = temperature_data['data']['Temperature']['value']

        return temperature

    def get_voltage(self):
        """
        Retrieves the supply voltage of this SFP
        """
        voltage = None

        voltage_data = self._get_eeprom_data('Voltage')
        if (voltage_data is not None):
            voltage = voltage_data['data']['Vcc']['value']

        return voltage

    """
    def get_tx_bias(self):
        
        # Retrieves the TX bias current of this SFP
        
        if (self.sfp_type == QSFPDD_TYPE):
            # check for rx los implemented and then gather status
            # stub for now

        elif (self.sfp_type == QSFP_TYPE):

        else:
            logger.log_error("get_tx_bias for Ethernet{} found invalid sfp_type{}".format(self.index, self.sfp_type))

        channel_monitor_data = self._get_eeprom_data('ChannelMonitor')
        if (channel_monitor_data is not None):
            tx1bias = channel_monitor_data['data']['TXBias']['value']
            return [tx1bias, "N/A", "N/A", "N/A"]
        else:
            return []
    """


    """
    def get_rx_power(self):
        
        # Retrieves the received optical power for this SFP
        
        channel_monitor_data = self._get_eeprom_data('ChannelMonitor')
        if (channel_monitor_data is not None):
            rx1power = channel_monitor_data['data']['RXPower']['value']
            return [rx1power, "N/A", "N/A", "N/A"]
        else:
            return []
    
    """

    """
    def get_tx_power(self):
        
        # Retrieves the TX power of this SFP
        
        channel_monitor_data = self._get_eeprom_data('ChannelMonitor')
        if (channel_monitor_data is not None):
            tx1power = channel_monitor_data['data']['TXPower']['value']
            return [tx1power, "N/A", "N/A", "N/A"]
        else:
            return []
    """

    def reset(self):
        """
        Reset SFP and return all user module settings to their default srate.
        """
        return False

    def set_low_power_mode(self, lpmode):
        """
        Sets the lpmode (low power mode) of SFP
        """
        return False

    def tx_disable(self, tx_disable):
        """
        Disable SFP TX for all channels
        """
        return False

    def tx_disable_channel(self, channel, disable):
        """
        Sets the tx_disable for specified SFP channels
        """
        return False

    def set_power_override(self, power_override, power_set):
        """
        Sets SFP power level using power_override and power_set
        """
        return False

    def get_status(self):
        """
        Retrieves the operational status of the device
        """
        reset = self.get_reset_status()

        if (reset == True):
            status = False
        else:
            status = True

        return status
