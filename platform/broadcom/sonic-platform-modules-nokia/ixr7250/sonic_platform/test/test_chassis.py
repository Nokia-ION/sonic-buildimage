#!/usr/bin/env python
#
# Name: test_chassis.py, version: 1.0
#
# Description: Module contains the definitions of Unit-test suite for
# Chassis related APIs for Nokia IXR 7250 platform.
#
# Copyright (c) 2019, Nokia
# All rights reserved.
#

import os.path
import time
try:
    import grpc
    from sonic_common import nokia_common
    from sonic_common import devmgr_sonic_pb2
    from sonic_common import devmgr_sonic_pb2_grpc
    import subprocess
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

MAX_NAMESPACE = 3

def run_all(chassis):
    for i in range(0, MAX_NAMESPACE):
        chassis.set_namespace(i, 'ixr7250-chassis')
        my_namespace = chassis.get_namespace()
        print('Namespace is '+str(my_namespace))
