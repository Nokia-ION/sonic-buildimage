#!/bin/bash

fw_uboot_env_cfg()
{
    echo "Setting up U-Boot environment..."

    MACH_FILE="/host/machine.conf"
    PLATFORM=`sed -n 's/onie_platform=\(.*\)/\1/p' $MACH_FILE`

    if [ "$PLATFORM" = "armhf-aspeed_ast2700_evb-r0" ]; then
        #                 MTD device name       Device offset   Env. size       Flash sector size       Number of sectors
        FW_ENV_DEFAULT='/dev/mtd/u-boot-env     0x0000          0x20000         0x1000'
    fi

    echo "Using pre-configured uboot env"
    echo $FW_ENV_DEFAULT > /etc/fw_env.config

}


main()
{
    fw_uboot_env_cfg
    echo "Aspeed-ast2700_evb: /dev/mtd0 ${FW_ENV_DEFAULT}"
}

main $@
