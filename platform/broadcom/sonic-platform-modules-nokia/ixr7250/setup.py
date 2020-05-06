#!/usr/bin/env python

import os
import sys
from setuptools import setup
os.listdir

setup(
   name='sonic_platform',
   version='1.0',
   description='Module to initialize Nokia IXR7250 platforms',

   packages=['sonic_common', 'sonic_platform', 'sonic_platform.test'],
   package_data={'sonic_common': ['*.proto']},
   include_package_data=True,
   package_dir={'sonic_platform': 'ixr7250/sonic_platform',
                'sonic_common': 'ixr7250/sonic_common'},
)

