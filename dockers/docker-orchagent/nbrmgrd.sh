#!/usr/bin/env bash

# Export platform information. Required to be able to write
# vendor specific code.
export platform=`sonic-cfggen -y /etc/sonic/sonic_version.yml -v asic_type`

# Export  MY_SLOT_NUM, NUMBER_OF_ASICS and BCM_UNIT_MDA_SIZE 
MY_SLOT_NUM=$(sonic-cfggen -j /etc/sonic/card_details.json -v slot_num)
# MY_SLOT_NUMBER is 0 base
export MY_SLOT_NUMBER=`expr $MY_SLOT_NUM - 1`
export NUMBER_OF_ASICS=$(sonic-cfggen -j /etc/sonic/card_details.json -v chassis_num_asics)
export BCM_UNIT_NUMBER=$(sonic-cfggen -j /usr/share/sonic/hwsku/asic_details.json -v asic_num)

exec /usr/bin/nbrmgrd
