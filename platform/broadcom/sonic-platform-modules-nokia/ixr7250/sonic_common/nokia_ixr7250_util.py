#!/usr/bin/env python
#
# Name: nokia_ixr7250_util.py, version: 1.0
#
# Description: Module contains the test show, set and ut commands
# for interfacing with platform code
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

"""
Usage: %(scriptName)s [options] command object

options:
    -h | --help     : this help message
command:
    show        : show all systen status
    set         : change board setting with fan|led|sfp
    ut          : run unit-tests
"""

import os
import commands
import sys, getopt
import binascii
import logging
import re
import time
import random
import optparse
from collections import namedtuple
from google.protobuf import text_format
from google.protobuf.json_format import MessageToJson

import grpc
from sonic_common import nokia_common
from sonic_common import devmgr_sonic_pb2
from sonic_common import devmgr_sonic_pb2_grpc

PROJECT_NAME = 'nokia_ixr7250'
verbose = False
DEBUG = False
args = []
format_type = ''


if DEBUG == True:
    print sys.argv[0]
    print 'ARGV      :', sys.argv[1:]

def print_table(field, item_list):
    i = 0
    header = ''
    while i < len(field):
        header += "| "+field[i]
        i += 1
    header += " | "
    print('-' * (len(header) - 1))
    print(header)
    print('-' * (len(header) - 1))

    i = 0
    while i < len(item_list):
        item = item_list[i]
        row = ''
        j = 0
        while j < len(item):
            row += "| "+item[j]+' '*(len(field[j]) - len(item[j]))
            j += 1
        row += " |"
        print(row)
        i += 1
    print('-' * (len(header) - 1))
    print('\n')

def generate_stub():
    stub = None
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

def show_led(stub, port_start, port_end):
    response = stub.ShowLedPortInfo(devmgr_sonic_pb2.ReqLedPortIndexPb(
                                                            port_start_idx=port_start,
                                                            port_end_idx=port_end))
    if len(response.port_device) == 0:
        print('Port/LED Output not available on this card')
        return

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    field = []
    field.append('Interface     ')
    field.append('Status                    ')

    i = 0
    item_list = []
    while i < len(response.port_device):
        port_device = response.port_device[i]
        item_list.append(['Ethernet'+str(port_device.led_idx),
                         (port_device.led_status.rstrip())])
        i += 1

    print('FRONT-PANEL INTERFACE STATUS TABLE')
    print_table(field, item_list)
    return

def show_psu(stub):
    req_etype = devmgr_sonic_pb2.ReqPsuOpsType.PSU_OPS_SHOW_ALL
    response = stub.ReqPsuInfo(devmgr_sonic_pb2.ReqPsuInfoPb(psu_idx = 0, type = req_etype))

    if len(response.psu_info.psu_device) == 0:
        print('PSU Output not available on this card')
        return

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    field = []
    field.append('Index   ')
    field.append('Status       ')

    i = 0
    item_list = []
    while i < len(response.psu_info.psu_device):
        psu_device = response.psu_info.psu_device[i]
        item = []
        item.append(str(i + 1))
        if psu_device.psu_presence:
            if psu_device.psu_status:
                item.append('UP')
            else:
                item.append('DOWN')
        else:
            item.append('NOT-PRESENT')
        item_list.append(item)
        i += 1

    print('PSU TABLE')
    print_table(field, item_list)
    return

def show_fan(stub):
    req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=0)
    req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_SHOW_ALL
    response = stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))
    if len(response.fan_show.fan_device) == 0:
        print('Fan output not available on this card')
        return

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    field = []
    field.append('        Type         ')
    field.append('        Value                    ')

    i = 0
    item_list = []
    while i < len(response.fan_show.fan_device):
        fan_device = response.fan_show.fan_device[i]
        item = []
        item.append('Fan Tray - '+str(i))
        item.append(fan_device.fan_state)
        item_list.append(item)
        i += 1

    item_list.append(['Fan Algorithm Disable', 'True' if response.fan_show.fan_algorithm_disable else 'False'])
    item_list.append(['Current Fan Speed', str(response.fan_show.current_fan_speed)])
    item_list.append(['Max Fan Speed', str(response.fan_show.max_fan_speed)])
    item_list.append(['Force Max Fan Speed', 'True' if response.fan_show.is_force_max_speed else 'False'])

    print('FAN TABLE')
    print_table(field, item_list)
    return

def show_fan_detail(stub):
    max_fan_speed = 9000

    #Get Num-fans
    req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=0)
    req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_GET_NUM_TRAYS
    response = stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    fan_msg = response.fan_nums
    num_fans = fan_msg.num_fantrays

    if num_fans == 0:
        print('Fan detail output not available on this card')
        return

    field = []
    field.append('Index   ')
    field.append('Presence')
    field.append('Status  ')
    field.append('Target Speed')
    field.append('Speed   ')
    field.append('SerialNo      ')
    field.append('PartNo        ')

    item_list = []
    #For each fan check presence
    for index in range(0, num_fans):
        req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=index)

        #Presence
        req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_REQ_PRESENCE
        response = stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))
        presence_msg = response.fan_presence
        presence = presence_msg.fantray_presence

        #Status
        status = False
        req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_REQ_STATUS
        response = stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))
        status_msg = response.fan_status
        #Empty, Init, SpinUp, Online are possible states
        if status_msg.fantray_status == 'Online':
            status = True

        #Target Speed
        req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_GET_SPEED
        response = stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))
        speed_msg = response.fan_speed
        target_speed = speed_msg.fantray_speed

        #Speed
        req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_GET_SPEED_RPM
        response = stub.GetFanTrayOpsInfo(devmgr_sonic_pb2.ReqFanTrayOpsPb(idx=req_idx, type = req_etype))
        speed_msg = response.fan_speed_rpm
        speed_in_rpm = speed_msg.fantray_speed_rpm
        speed = (100 * speed_in_rpm)/max_fan_speed

        #PartNo
        req_etype = devmgr_sonic_pb2.ReqFanTrayEepromType.FAN_EEPROM_PARTNO
        response = stub.GetFanTrayEepromInfo(devmgr_sonic_pb2.ReqFanTrayEepromPb(idx=req_idx, etype = req_etype))
        fantray_partno = response.fantray_edata

        #SerialNo
        req_etype = devmgr_sonic_pb2.ReqFanTrayEepromType.FAN_EEPROM_SERIALNO
        response = stub.GetFanTrayEepromInfo(devmgr_sonic_pb2.ReqFanTrayEepromPb(idx=req_idx, etype = req_etype))
        fantray_serialno = response.fantray_edata

        item = []
        item.append(str(index))
        item.append(str(presence))
        item.append(str(status))
        item.append(str(target_speed))
        item.append(str(speed))
        item.append(fantray_serialno)
        item.append(fantray_partno)
        item_list.append(item)

    print('FAN DETAIL TABLE')
    print_table(field, item_list)
    return

def show_temp(stub):
    req_etype = devmgr_sonic_pb2.ReqTempOpsType.TEMP_OPS_SHOW_READINGS
    response = stub.ReqTempParams(devmgr_sonic_pb2.ReqTempParamsPb(type = req_etype))

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    field = []
    field.append('Name    ')
    field.append('Local   ')
    field.append('Remote   ')
    field.append('Description        ')

    i = 0
    item_list = []
    while i < len(response.temp_show.temp_device):
        temp_device = response.temp_show.temp_device[i]
        item = []
        item.append(temp_device.sensor_name)
        item.append(str(temp_device.local))
        item.append(str(temp_device.remote))
        item.append(temp_device.device_desc)
        item_list.append(item)
        i += 1

    print('TEMPERATURE DEVICES TABLE')
    print_table(field, item_list)

    field = []
    field.append('Hw Slot')
    field.append('Current Temp')
    field.append('Min Temp')
    field.append('Max Temp')
    field.append('Margin')

    i = 0
    item_list = []
    while i < len(response.temp_show.temp_summary):
        temp_summary = response.temp_show.temp_summary[i]
        item = []
        item.append(str(temp_summary.slot_num))
        item.append(str(temp_summary.current_temp))
        item.append(str(temp_summary.min_temp))
        item.append(str(temp_summary.max_temp))
        item.append(str(temp_summary.margin))
        item_list.append(item)
        i += 1

    print('TEMPERATURE SUMMARY TABLE')
    print_table(field, item_list)

    return

def show_platform(stub):
    response = stub.GetHwInfo(devmgr_sonic_pb2.GetHwInfoPb())
    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    field = []
    field.append('Slot   ')
    field.append('Name                              ')
    field.append('Status    ')

    i = 0
    item_list = []
    while i < len(response.hw_device):
        hw_device = response.hw_device[i]
        item = []
        item.append(str(hw_device.slot_num))
        item.append(hw_device.hw_name)
        item.append(hw_device.state)
        item_list.append(item)
        i += 1

    print('PLATFORM INFO TABLE')
    print_table(field, item_list)
    return

def show_firmware(stub):
    dev_type = devmgr_sonic_pb2.HwFirmwareDeviceType.HW_FIRMWARE_DEVICE_BIOS
    req_etype = devmgr_sonic_pb2.ReqHwFirmwareOpsType.REQ_HWFW_OPS_SHOW_VERSION
    response = stub.ReqHwFirmwareInfo(devmgr_sonic_pb2.ReqHwFirmwareInfoPb(type=req_etype,
        dev_type=dev_type))

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    print(response.version)
    return

def show_sfm_summary(stub):
    req_etype = devmgr_sonic_pb2.ReqSfmOpsType.SFM_OPS_SHOW_SUMMARY
    response = stub.ReqSfmInfo(devmgr_sonic_pb2.ReqSfmInfoPb(type = req_etype))

    if len(response.sfm_summary.sfm_info) == 0:
        print('SFM summary not available on this card')
        return

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    field = []
    field.append('SFM-Num')
    field.append('Hw-slot')
    field.append('Admin Down')
    field.append('Initialized')
    field.append('Num Ramons')
    field.append('Presence')
    field.append('Error')
    field.append('Instance')

    i = 0
    item_list = []
    while i < len(response.sfm_summary.sfm_info):
        sfm_info = response.sfm_summary.sfm_info[i]
        item = []
        item.append(str(sfm_info.sfm_num))
        item.append(str(sfm_info.hw_slot))
        item.append(str(sfm_info.admin_down))
        item.append(str(sfm_info.is_initialized))
        item.append(str(sfm_info.num_ramons))
        item.append(str(sfm_info.presence))
        item.append(str(sfm_info.error))
        item.append(str(sfm_info.instance))
        item_list.append(item)
        i += 1

    print('SFM SUMMARY TABLE')
    print_table(field, item_list)
    return

def show_sfm_imm_links(stub, imm_slot):
    req_etype = devmgr_sonic_pb2.ReqSfmOpsType.SFM_OPS_SHOW_IMMLINKS
    response = stub.ReqSfmInfo(devmgr_sonic_pb2.ReqSfmInfoPb(type = req_etype, imm_slot = imm_slot))

    if format_type == 'json-format':
        json_response = MessageToJson(response)
        print(json_response)
        return

    field = []
    field.append('IMM-Link    ')
    field.append('SFM-Link    ')
    field.append('Link Status')
    field.append('Isolate')
    field.append('Module-Id')

    i = 0
    item_list = []
    while i < len(response.sfm_imm_info.link_info):
        link_info = response.sfm_imm_info.link_info[i]
        item = []
        imm_link = 'IMM '+ str(response.sfm_imm_info.imm_slot)+'.'+str(link_info.imm_unit)+'.'+str(link_info.imm_link)
        sfm_link = 'SFM '+ str(response.sfm_imm_info.sfm_slot)+'.'+str(link_info.sfm_unit)+'.'+str(link_info.sfm_link)

        item.append(imm_link)
        item.append(sfm_link)
        if link_info.link_status_up:
            item.append('UP')
        else:
            item.append('DOWN')
        item.append(str(link_info.link_is_isolate))
        item.append('M'+str(link_info.link_module_id))
        item_list.append(item)
        i += 1

    print('SFM IMM LINK TABLE')
    print_table(field, item_list)

    print('Num links UP: '+str(response.sfm_imm_info.up_links))
    print('Num links total: '+str(response.sfm_imm_info.total_links))
    return

def set_temp_offset(stub, offset):
    req_etype = devmgr_sonic_pb2.ReqTempOpsType.TEMP_OPS_SET_TEMP_OFFSET
    stub.SetTempParams(devmgr_sonic_pb2.ReqTempParamsPb(type=req_etype, temp_offset=offset))

def set_fan_algo_disable(stub, disable):
    req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=1)
    req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_DISABLE_ALGORITHM
    req_fan_algo = devmgr_sonic_pb2.SetFanTrayAlgorithmPb(fantray_algo_disable=disable)
    stub.SetFanTraySpeedParams(devmgr_sonic_pb2.ReqFanTraySpeedParamsPb(idx=req_idx, type=req_etype, fan_algo=req_fan_algo))

def set_fan_speed(stub, speed):
    req_idx = devmgr_sonic_pb2.ReqFanTrayIndexPb(fantray_idx=1)
    req_etype = devmgr_sonic_pb2.ReqFanTrayOpsType.FAN_OPS_SET_SPEED
    req_fan_speed = devmgr_sonic_pb2.GetFanTraySpeedPercentPb(fantray_speed=speed)
    stub.SetFanTraySpeedParams(devmgr_sonic_pb2.ReqFanTraySpeedParamsPb(idx=req_idx, type=req_etype, fan_speed=req_fan_speed))

def request_admintech(stub, filepath):
    stub.ReqAdminTech(devmgr_sonic_pb2.ReqAdminTechPb(admintech_path=filepath))

def main():
    global DEBUG
    global args
    global format_type

    if len(sys.argv)<2:
        show_all_help()

    options, args = getopt.getopt(sys.argv[1:], 'hd', ['help',
                                                       'debug',
                                                          ])
    if DEBUG == True:
        print options
        print args
        print len(sys.argv)

    for opt, arg in options:
        if opt in ('-h', '--help'):
            show_all_help()
        elif opt in ('-d', '--debug'):
            DEBUG = True
            logging.basicConfig(level=logging.INFO)
        else:
            logging.info('no option')

    num_args = 1
    arg = args[0]
    if arg == 'show':
        num_args += 1
        if not validate(arg, num_args, len(args)):
            return

        show_arg = args[1]
        num_args += 1
        if num_args == len(args): #format
           format_type = args[2]

        if show_arg == 'sensors':
           stub = generate_stub()
           if stub == None:
               return
           show_temp(stub)
           show_fan(stub)
           show_fan_detail(stub)
        elif show_arg == 'platform':
           stub = generate_stub()
           if stub == None:
               return
           show_platform(stub)
        elif show_arg == 'psus':
           stub = generate_stub()
           if stub == None:
               return
           show_psu(stub)
        elif show_arg == 'fp-status':
           stub = generate_stub()
           if stub == None:
               return
           show_led(stub, 1, 36)
        elif show_arg == 'firmware':
           stub = generate_stub()
           if stub == None:
               return
           show_firmware(stub)
        elif show_arg == 'sfm-summary':
           stub = generate_stub()
           if stub == None:
               return
           show_sfm_summary(stub)
        elif show_arg == 'sfm-imm-links':
           if not validate(arg, num_args, len(args)):
               return

           imm_slot = int(args[2])
           num_args += 1
           if num_args == len(args): #format
              format_type = args[3]

           stub = generate_stub()
           if stub == None:
               return
           show_sfm_imm_links(stub, imm_slot)
        return
    elif arg == 'set':
        num_args += 1
        if not validate(arg, num_args, len(args)):
            return

        set_arg = args[1]
        if set_arg == 'temp-offset':
            num_args += 1
            if not validate(arg, num_args, len(args)):
                return

            set_val = int(args[2])
            stub = generate_stub()
            if stub == None:
                return
            set_temp_offset(stub, set_val)
        elif set_arg == 'fan-algo-disable':
            num_args += 1
            if not validate(arg, num_args, len(args)):
                return

            set_val = int(args[2])
            stub = generate_stub()
            if stub == None:
                return
            set_fan_algo_disable(stub, set_val)
        elif set_arg == 'fan-speed-percent':
            num_args += 1
            if not validate(arg, num_args, len(args)):
                return

            set_val = int(args[2])
            stub = generate_stub()
            if stub == None:
                return
            set_fan_speed(stub, set_val)
        return
    elif arg == 'ut':
        num_args += 1
        if not validate(arg, num_args, len(args)):
            return

        ut_arg = args[1]
        if ut_arg == 'fan':
            # Load new platform api class
            try:
                import sonic_platform.platform
                platform_chassis = sonic_platform.platform.Platform().get_chassis()
                platform_chassis.test_suite_fan()
            except Exception as e:
                print('Failed to load chassis due to '+str(e))
        elif ut_arg == 'sfp':
            # Load new platform api class
            try:
                import sonic_platform.platform
                platform_chassis = sonic_platform.platform.Platform().get_chassis()
                platform_chassis.test_suite_sfp()
            except Exception as e:
                print('Failed to load chassis due to '+str(e))
        elif ut_arg == 'chassis':
            # Load new platform api class
            try:
                import sonic_platform.platform
                platform_chassis = sonic_platform.platform.Platform().get_chassis()
                platform_chassis.test_suite_chassis()
            except Exception as e:
                print('Failed to load chassis due to '+str(e))
        elif ut_arg == 'psu':
            # Load new platform api class
            try:
                import sonic_platform.platform
                platform_chassis = sonic_platform.platform.Platform().get_chassis()
                platform_chassis.test_suite_psu()
            except Exception as e:
                print('Failed to load chassis due to '+str(e))
        return
    elif arg == 'request':
        num_args += 1
        if not validate(arg, num_args, len(args)):
            return

        debug_arg = args[1]
        if debug_arg == 'admintech':
            num_args += 1
            if not validate(arg, num_args, len(args)):
                return

            filepath_val = args[2]
            stub = generate_stub()
            if stub == None:
                return
            request_admintech(stub, filepath_val)
        else:
            debug_all_help()
    else:
        show_all_help()

    return True

show_parser = {
    'platform' : [1, 'Platform Information', 'show_help'],
}

def validate(cmd, num_args, passed_args):
    if passed_args < num_args:
        if (cmd == 'show'):
            show_help()
        elif (cmd == 'set'):
            show_set_help()
        elif (cmd == 'ut'):
            show_ut_help()
        elif (cmd == 'request'):
            show_request_help()
        else:
            print('Unsupported command')
        return 0
    return 1

def show_help():
    print("show platform    [json-format]      - Show Platform Information")
    print("show sensors     [json-format]      - Show Temperature/Fan Information")
    print("show psus        [json-format]      - Show PSUs Information")
    print("show fp-status   [json-format]      - Show FrontPanel LEDs")
    print("show firmware    [json-format]      - Show Firmware")
    print("show sfm-summary [json-format]      - Show SFM summary")
    print("show sfm-imm-links [imm-slot] [json-format]    - Show SFM summary")

def show_set_help():
    print("set temp-offset <0-100>          - Set temperature offset")
    print("set fan-algo-disable <1/0>       - Set 1 to disable")
    print("set fan-speed-percent <0-100>    - Set fan speed manually")

def show_ut_help():
    print("ut chassis                       - To run chassis unit-tests")
    print("ut psu                           - To run psu unit-tests")
    print("ut fan                           - To run fan unit-tests")
    print("ut sfp                           - To run sfp unit-tests")

def show_request_help():
    print("request admintech <full-file-path>  - Request admin-tech dump")

def show_all_help():
    show_help()
    show_set_help()
    show_ut_help()
    show_request_help()
    sys.exit(0)

def my_log(txt):
    if DEBUG == True:
        print "[DEBUG]"+txt
    return

if __name__ == "__main__":
    main()
