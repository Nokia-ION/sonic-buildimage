#!/usr/bin/env python
#
# Name: chassis.py, version: 1.0
#
# Description: Module contains the definitions of SONiC platform APIs 
# which provide the chassis specific details
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

try:
    import os
    import commands
    import sys
    import time
    import threading
    import subprocess
    import json
    from sonic_platform_base.chassis_base import ChassisBase
    from sonic_platform_base.platform_base import PlatformBase
    from sonic_platform.sfp import Sfp
    from sonic_platform.psu import Psu
    from sonic_platform.fan import Fan
    from sonic_platform.module import Module
    from sonic_platform.thermal import Thermal
    from sonic_platform.component import Component
    #from eeprom import Eeprom
    from sonic_daemon_base.daemon_base import Logger
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc

except ImportError as e:
    raise ImportError(str(e) + "- required module not found")
logger = Logger("Chassis")

NOKIA_MAX_IXR7250_MODULE = 3
NOKIA_MAX_IXR7250_FAN = 3
NOKIA_MAX_IXR7250_PSU = 6
NOKIA_MAX_IXR7250_THERMAL = 26
NOKIA_MAX_IXR7250_CPM_COMPONENT = 2
NOKIA_MAX_IXR7250_IMM_COMPONENT = 3

# for Hurricane
# MAX_NOKIA_PORTS_PER_ASIC = 12
# for Vodka
# MAX_NOKIA_PORTS_PER_ASIC = 36

# NOKIA_MAX_PORTS_PER_ASIC = MAX_NOKIA_PORTS_PER_ASIC

os_pid = 0
channel = None
stub = None

def generate_grpc_stub():
    #Create a GRPC stub
    global channel
    global os_pid

    if os_pid != os.getpid():
        os_pid = os.getpid()
        if channel:
            channel.close()
    server_path = nokia_common.NOKIA_DEVMGR_UNIX_SOCKET_PATH
    channel = grpc.insecure_channel(server_path)
    channel_ready = grpc.channel_ready_future(channel)
    try:
        channel_ready.result(timeout=1)
    except grpc.FutureTimeoutError:
        print('GRPC channel could not be established')
    else:
        stub = devmgr_sonic_pb2_grpc.DevMgrSonicServiceStub(channel)

    return stub


class Chassis(ChassisBase):
    """
    NOKIA IXR7250 Platform-specific Chassis class
    """
    num_asics = 0
    num_ports_per_asic = 0
    
    def __init__(self):
        ChassisBase.__init__(self)
        #super(chassisBase, self).__init__()
        #self.os_pid = os.getpid()
        
        """
        self.num_ports_per_asic = nokia_common.NOKIA_MAX_PORTS_PER_ASIC

        num_asics_cmd = 'sonic-cfggen -j /etc/sonic/card_details.json -v num_asics'
        num_asics_output = subprocess.check_output(num_asics_cmd, shell=True)
        # should only be doing this once
        Chassis.num_asics = int(num_asics_output.rstrip())
        if Chassis.num_asics == 0:
            num_entries = 1
        else:
            num_entries = Chassis.num_asics

        logger.log_error("Chassis __init__ invoked num_entries {} self {}".format(num_entries, self)) 
        """

        try:
            # with open("/usr/share/sonic/platform/$HWSKU/hwsku.json", 'r') as stream:
            # .../hwsku/hwsku.json should be automatically mapped by host prior to pmon launch
            with open("/usr/share/sonic/hwsku/hwsku.json", 'r') as stream:
                try:
                    data = json.load(stream)
                    if data.has_key("asic_num"):
                        num_json_asics = data['asic_num']
                    else:
                        num_json_asics = 0
                        logger.log_error("asic_num not found in hwsku.json")  

                    if data.has_key("max_ports_per_asic"):
                        num_json_ports_per_asic = data['max_ports_per_asic']
                    else:
                        num_json_ports_per_asic = 0
                        logger.log_error("max_ports_per_asic not found in hwsku.json")  
                
                except :
                    logging.info("Load hwsku.json load error")
                    pass
        except:
            # file not found, treat it as disable
            logger.log_error("hwsku.json not found") 


        logger.log_error("hwsku.json: num_json_asics {} max_json_ports_per_asic {}".format(num_json_asics, num_json_ports_per_asic))  

        Chassis.num_asics = int(num_json_asics, 10)
        
        """
        if Chassis.num_asics == 0:
           num_entries = 1
        else:
           num_entries = Chassis.num_asics
        """

        Chassis.num_ports_per_asic = int(num_json_ports_per_asic, 10)
      

       #Create a GRPC stub
        self.stub = generate_grpc_stub()

        # Initialize EEPROM
        #self.sys_eeprom = Eeprom()

         #SFP list - index 1 to 36
        if not nokia_common.is_cpm():
                if Chassis.num_asics == 0:
                    logger.log_error("IMM has Chassis.num_asics set to 0, aborting")
                    return                      
                self._sfp_list = [None] * ((Chassis.num_asics * Chassis.num_ports_per_asic) + 1)
                for i in range(Chassis.num_asics):
                    #module = Module(i)
                    #self._module_list.append(module)
                    port_start = (i * Chassis.num_ports_per_asic) + 1
                    port_end = ((i + 1) * Chassis.num_ports_per_asic)
                    logger.log_error("Creating SFPs: i {} port_start {} port_end {}".format(i, port_start, port_end))     
                    
                    # default to QSFP-DD type for the moment
                    for num in range(port_start, port_end + 1):
                        logger.log_error("Creating SFP '%d' - get_presence" % num)
                        sfp_node = Sfp(num, 'QSFP-DD', self.stub)
                        # Vodka ports 33-36 will be QSFP-DD, but 1-32 will be QSFP
                        self._sfp_list.insert(num, sfp_node)

                logger.log_error("Init loops done i {} num {} self {} - get_presence".format(i, num, self))
                self.sfp_event_list = [None] * (Chassis.num_asics)
                self.sfp_event_initialized =  [False] * (Chassis.num_asics)

        #FAN list
        if nokia_common.is_cpm():
            for i in range(NOKIA_MAX_IXR7250_FAN):
                fan = Fan(i, False, self.stub)
                self._fan_list.append(fan)

        #PSU list
        if nokia_common.is_cpm():
            for i in range(NOKIA_MAX_IXR7250_PSU):
                psu = Psu(i, self.stub)
                self._psu_list.append(psu)

        #Thermal list
        self.get_thermal_list()

        #import traceback
        #traceback.print_stack()

        #Component List
        if nokia_common.is_cpm():
            max_components = NOKIA_MAX_IXR7250_CPM_COMPONENT
        else:
            max_components = NOKIA_MAX_IXR7250_IMM_COMPONENT

        for i in range(max_components):
            component = Component(i, nokia_common.is_cpm(), self.stub)
            self._component_list.append(component)

    def get_name(self):
        return 'IXR-7250'

    def set_namespace(self, namespace, identifier):
        #logger.log_error("Set-namespace with thread-{} id-{} namespace-{} ".format(str(os.getpid())+str(threading.current_thread().ident), identifier, namespace))

        #Call base class set_namespace
        ChassisBase.set_namespace(self, namespace, identifier)

        #GRPC has a limitation if child processes inherit the channel.
        #Current workaround to cleanup existing channels and opening new ones.
        time.sleep(namespace * 2)
        stub = generate_grpc_stub()

        if identifier == 'Xcvrd-SFP-Monitor':
            port_begin = (namespace * Chassis.num_ports_per_asic) + 1
            port_end = ((namespace + 1) * Chassis.num_ports_per_asic)

            from sonic_platform.sfp_event import sfp_event
            self.sfp_event_list[namespace] = sfp_event(namespace, Chassis.num_ports_per_asic, port_begin, port_end, stub)

    def get_namespace(self):
        #logger.log_error("Get-namespace with thread-{} start for namespace{} ".format(str(os.getpid())+str(threading.current_thread().ident), self._namespace.val))
        return ChassisBase.get_namespace(self)

    def get_change_event(self, timeout=0):
        namespace = self.get_namespace()
        #logger.log_error("Get-change-event with thread-{} start for namespace{} ".format(str(os.getpid())+str(threading.current_thread().ident), namespace))
        if not self.sfp_event_initialized[namespace]:
            self.sfp_event_list[namespace].initialize()
            self.sfp_event_initialized[namespace] = True

        timeout = 0
        port_dict = {}
        status = self.sfp_event_list[namespace].check_sfp_status(port_dict, timeout)

        return True, {'sfp':port_dict}

    def get_multiasic_port(self, port_id):
        return ((Chassis.num_ports_per_asic * self.get_namespace()) + port_id)

    def get_thermal_list(self):
        if self.stub == None:
            return

        req_etype = devmgr_sonic_pb2.ReqTempOpsType.TEMP_OPS_GET_NAME_IDX_MAPPING
        response = self.stub.ReqTempParams(devmgr_sonic_pb2.ReqTempParamsPb(type = req_etype))

        all_temp_devices = response.temp_devices
        num_devices = len(all_temp_devices.temp_device)
        for i in range(num_devices):
            temp_device_ = all_temp_devices.temp_device[i]
            thermal = Thermal(i, temp_device_.device_idx, temp_device_.sensor_name, self.stub)
            self._thermal_list.append(thermal)

    #Thermal Manager support not added for Chassis. Should be made optional in
    #thermalctld if platform internally wants to manager temperature control
    #def get_thermal_manager(self):
    #    from .thermal_manager import ThermalManager
    #    return ThermalManager

    def test_suite_fan(self):
        print "Starting FAN UT"
        from sonic_platform.test import test_fan
        test_fan.run_all(self._fan_list)

    def test_suite_sfp(self):
        print "Starting SFP UT"
        from sonic_platform.sfp_event import sfp_event
        from sonic_platform.test import test_sfp
        port_begin = 1
        port_end = (Chassis.num_ports_per_asic * Chassis.num_asics)
        test_sfp_event = sfp_event(0, port_end, port_begin, port_end, self.stub)
        test_sfp.run_all(self._sfp_list, test_sfp_event)

    def test_suite_chassis(self):
        print "Starting Chassis UT"
        from sonic_platform.test import test_chassis
        test_chassis.run_all(self)

    def test_suite_psu(self):
        print "Starting PSUs UT"
        from sonic_platform.test import test_psu
        test_psu.run_all(self._psu_list)
