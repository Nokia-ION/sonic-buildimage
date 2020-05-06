reload_i2c_drivers()
{
    local piix4bus="/sys/bus/i2c/devices/i2c-0"
    local piix4drv="i2c-piix4"
    local piix4id="/sys/devices/pci"
    local designbus="/sys/bus/i2c/devices/i2c-5"
    local designdrv="i2c-designware-platform"
    local designid="/sys/devices/platform"
    local count=0
    local found
 
    found=$(readlink -e "${designbus}" |grep "${designid}")
    if [[ ${#found} -gt 0 ]]; then
        printf "i2c buses are in order\n"
        return;
    fi
    sleep 1
    found=$(readlink -e "${piix4bus}" |grep "${piix4id}")
    if [[ ${#found} -eq 0 ]]; then
        printf "piix4 i2c is not in order. Reloading\n"
        modprobe -v -r ${piix4drv}
        sleep 1
        modprobe -v ${piix4drv}
    fi
    printf "Unloading %s driver\n" ${designdrv}
    rmmod -v ${designdrv}
    until [[ ${count} -gt 5 ]];
    do
        printf "Loading %s driver\n" ${designdrv}
        modprobe -v -s ${designdrv}
        sleep 1
        found=$(readlink -e "${designbus}" |grep "${designid}")
        if [[ ${#found} -gt 0 ]]; then
            break
        fi
        printf "Unloading %s driver\n" ${designdrv}
        rmmod -v -s ${designdrv}
        sleep 3
        (( count++ ))
    done
}

reload_i2c_drivers

