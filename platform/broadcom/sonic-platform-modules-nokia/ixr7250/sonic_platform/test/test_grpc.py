#!/usr/bin/env python
#
# Name: test_grpc.py, version: 1.0
#
# Description: Module contains the test show, set and ut commands
# for interfacing with platform code
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import os
import commands
import sys, getopt
import binascii
import logging
import re
import time
import random
import threading
import multiprocessing
from collections import namedtuple
from google.protobuf import text_format
from google.protobuf.json_format import MessageToJson

import grpc
from sonic_common import nokia_common
from sonic_common import devmgr_sonic_pb2
from sonic_common import devmgr_sonic_pb2_grpc
DEBUG = False
format_type = ''
channel = None

class test_process_task:
    def __init__(self):
        self.task_process = None
        self.task_stopping_event = multiprocessing.Event()

    def task_worker(self, stopping_event):
        self.stub = generate_stub()
        print('Starting new process '+str(os.getpid())+':'+str(threading.current_thread().ident))
        i = 0
        while i < 10:
            show_temp(self.stub)
            time.sleep(5)
            i = i+1

    def task_run(self):
        if self.task_stopping_event.is_set():
            return

        self.task_process = multiprocessing.Process(target=self.task_worker,args=(self.task_stopping_event,))
        self.task_process.start()

    def task_stop(self):
        self.task_stopping_event.set()
        os.kill(self.task_process.pid, signal.SIGKILL)

def generate_stub():
    global channel
    stub = None
    if channel:
        print('OS '+str(os.getpid())+': '+'Old GRPC channel '+hex(id(channel))+' closed')
        channel.close()

    server_path = nokia_common.NOKIA_DEVMGR_UNIX_SOCKET_PATH
    channel = grpc.insecure_channel(server_path)
    #channel_ready = grpc.channel_ready_future(channel)
    #try:
    #    print('OS '+str(os.getpid())+': '+'GRPC channel '+hex(id(channel))+' was established')
    #    channel_ready.result(timeout=1)
    #except grpc.FutureTimeoutError:
    #    print('OS '+str(os.getpid())+': '+'GRPC channel could not be established')
    #else:
    #    stub = devmgr_sonic_pb2_grpc.DevMgrSonicServiceStub(channel)
    print('OS '+str(os.getpid())+': '+'GRPC channel '+hex(id(channel))+' was established')
    stub = devmgr_sonic_pb2_grpc.DevMgrSonicServiceStub(channel)
    return stub

def show_temp(stub):
    req_etype = devmgr_sonic_pb2.ReqTempOpsType.TEMP_OPS_SHOW_READINGS
    response = stub.ReqTempParams(devmgr_sonic_pb2.ReqTempParamsPb(type = req_etype))

    print('OS '+str(os.getpid())+': '+'Received response with len '+str(len(response.temp_show.temp_device)))
    return

def main():
    global DEBUG
    global format_type
    global channel

    stub = generate_stub()
    show_temp(stub)

    num_processes = 2
    for i in range(0, num_processes):
        test_process = test_process_task()
        test_process.task_run()

    i = 0
    while i < 10:
        show_temp(stub)
        time.sleep(5)
        i = i+1

    return True

def my_log(txt):
    if DEBUG == True:
        print "[DEBUG]"+txt
    return

if __name__ == "__main__":
    main()
