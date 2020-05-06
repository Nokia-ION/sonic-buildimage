#
# Name: sonic_led_relay.py, version: 1.0
#
# Description: Module contains the Nokia IXR7250 specific implementation
# of the LedControlCommon class
#
# Copyright (c) 2019, Nokia
# All rights reserved.

import sys
import threading
import grpc
from sonic_common import nokia_common
from sonic_common import devmgr_sonic_pb2
from sonic_common import devmgr_sonic_pb2_grpc
from sonic_daemon_base.daemon_base import Logger

try:
   from sonic_led import led_control_base
except ImportError as e:
   raise ImportError('%s - required module not found' % str(e))

SYSLOG_IDENTIFIER = "nokia-ledd"
logger = Logger(SYSLOG_IDENTIFIER)

class LedControlCommon(led_control_base.LedControlBase):
   def __init__(self):
       self.namespace = threading.local()
       self.stop = threading.Event()
       # Set all the leds to init color
       self.num_ports_per_asic = nokia_common.NOKIA_MAX_PORTS_PER_ASIC

       #Create a GRPC stub
       self.stub = None
       server_path = nokia_common.NOKIA_DEVMGR_UNIX_SOCKET_PATH
       channel = grpc.insecure_channel(server_path)
       channel_ready = grpc.channel_ready_future(channel)
       try:
           channel_ready.result(timeout=1)
       except grpc.FutureTimeoutError:
           print('GRPC channel could not be established')
       else:
           self.stub = devmgr_sonic_pb2_grpc.DevMgrSonicServiceStub(channel)
       pass

    # Signal handler
   def signal_handler(self, sig, frame):
       if sig == signal.SIGHUP:
           logger.log_info("Caught SIGHUP - ignoring...")
       elif sig == signal.SIGINT:
           logger.log_info("Caught SIGINT - exiting...")
           self.stop_event.set()
       elif sig == signal.SIGTERM:
           logger.log_info("Caught SIGTERM - exiting...")
           self.stop_event.set()
       else:
           logger.log_warning("Caught unhandled signal '" + sig + "'")

   def set_namespace(self, namespace):
       self.namespace.val = namespace

   def get_namespace(self):
       return self.namespace.val

   def port_link_state_change(self, port, state):
       intf_prefix='Ethernet'
       if port.startswith(intf_prefix) == False:
           return
       else:
           import re
           parse_nums=re.findall(r'\d+',port)
           port_id = int(parse_nums[0])
           logger.log_debug("Namespace:{} received port {} for port-id {}".format(self.get_namespace(), state, port_id))
           port_id = (self.num_ports_per_asic * self.get_namespace()) + port_id

       if state == 'up':
           port_up = 1
       elif state == 'down':
           port_up = 0
       else:
           return
       response = self.stub.UpdateIntfInfo(devmgr_sonic_pb2.UpdateIntfInfoPb(port_name='FPport'+port,
                                                                  port_id=port_id,
                                                                  port_up=port_up,
                                                                  presence=port_up,
                                                                  laser_up=port_up))

def getLedControl():
   return LedControlCommon
