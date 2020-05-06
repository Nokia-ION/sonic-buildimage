#
# Name: sonic_psu_relay.py, version: 1.0
#
# Description: Module contains the Nokia IXR7250 specific implementation
# of the PsuUtil class
#
# Copyright (c) 2019, Nokia
# All rights reserved.

import sys
import grpc
from sonic_common import nokia_common
from sonic_common import devmgr_sonic_pb2
from sonic_common import devmgr_sonic_pb2_grpc
import subprocess

try:
    from sonic_psu.psu_base import PsuBase
except ImportError as e:
    raise ImportError (str(e) + "- required module not found")

class PsuUtil(PsuBase):
    """Platform-specific PSUutil class"""

    def __init__(self):
        PsuBase.__init__(self)
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

    def get_num_psus(self):
        """
        Retrieves the number of PSUs available on the device

        :return: An integer, the number of PSUs available on the device
        """
        #PSU handling happens only on CPM, ignore on IMM
        if not nokia_common.is_cpm():
            return 0

        req_etype = devmgr_sonic_pb2.ReqPsuOpsType.PSU_OPS_NUM_PSUS
        response = self.stub.ReqPsuInfo(devmgr_sonic_pb2.ReqPsuInfoPb(psu_idx=0, type = req_etype))
        return response.num_psus

    def get_psu_presence(self, index):
        req_etype = devmgr_sonic_pb2.ReqPsuOpsType.PSU_OPS_GET_PRESENCE
        response = self.stub.ReqPsuInfo(devmgr_sonic_pb2.ReqPsuInfoPb(psu_idx=index, type = req_etype))
        return response.psu_presence

    def get_psu_status(self, index):
        req_etype = devmgr_sonic_pb2.ReqPsuOpsType.PSU_OPS_GET_STATUS
        response = self.stub.ReqPsuInfo(devmgr_sonic_pb2.ReqPsuInfoPb(psu_idx=index, type = req_etype))
        return response.psu_status

def getPsuUtil():
    return PsuUtil
