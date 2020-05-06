#!/bin/bash

function getMountPoint()
{
    echo $1 | python -c "import sys, json, os; mnts = [x for x in json.load(sys.stdin)[0]['Mounts'] if x['Destination'] == '/usr/share/sonic/hwsku']; print '' if len(mnts) == 0 else os.path.basename(mnts[0]['Source'])" 2>/dev/null
}

function updateHostName()
{
    HOSTS=/etc/hosts
    HOSTS_TMP=/etc/hosts.tmp

    EXEC="docker exec -i device-manager bash -c"

    NEW_HOSTNAME="$1"
    HOSTNAME=`$EXEC "hostname"`
    if ! [[ $HOSTNAME =~ ^[a-zA-Z0-9.\-]*$ ]]; then
        HOSTNAME=`hostname`
    fi

    # copy HOSTS to HOSTS_TMP
    $EXEC "cp $HOSTS $HOSTS_TMP"
    # remove entry with hostname
    $EXEC "sed -i \"/$HOSTNAME$/d\" $HOSTS_TMP"
    # add entry with new hostname
    $EXEC "echo -e \"127.0.0.1\t$NEW_HOSTNAME\" >> $HOSTS_TMP"

    echo "Set hostname in device-manager container"
    $EXEC "hostname '$NEW_HOSTNAME'"
    $EXEC "cat $HOSTS_TMP > $HOSTS"
    $EXEC "rm -f $HOSTS_TMP"
}

function getBootType()
{
    # same code snippet in files/scripts/syncd.sh
    case "$(cat /proc/cmdline)" in
    *SONIC_BOOT_TYPE=warm*)
        TYPE='warm'
        ;;
    *SONIC_BOOT_TYPE=fastfast*)
        TYPE='fastfast'
        ;;
    *SONIC_BOOT_TYPE=fast*|*fast-reboot*)
        TYPE='fast'
        ;;
    *)
        TYPE='cold'
    esac
    echo "${TYPE}"
}

function hw_install_kmods()
{
    sudo depmod
    sudo modprobe i2c_dev
    sudo modprobe 8021q

    #Will be loaded on both CPM and IMM
    sudo modprobe nokia_gpio_wdt

    sudo modprobe amd-xgbe
    sudo modprobe vfio-pci
#        "vfio",
#        "vfio_iommu_type1",
#        "vfio_virqfd",
#        "irqbypass",
#        "vfio-pci"

}

function preStartAction()
{
    if [ ! -e /etc/sonic/card_input.json ]; then
        echo "device-manager.sh unable to find card_input.json." >> /var/log/devmgr.log
        #Running on hw
        hw_install_kmods
        return
    fi

    running_sim=$(sonic-cfggen -j /etc/sonic/card_input.json -v is_sim)
    if [[ $running_sim == "No" ]]; then
        echo "device-manager.sh is not running in sim mode..exit early." >> /var/log/devmgr.log
        #Running on hw
        hw_install_kmods
        return
    fi

    while true; do
        oper_up=$(cat /sys/class/net/int-mgmt0/operstate)
        slot_num=$(sonic-cfggen -j /etc/sonic/card_input.json -v slot_num)
        if [[ $oper_up == "up" ]]; then
            sudo ifconfig int-mgmt0 10.0.0.$slot_num up
            echo "device-manager.sh has set interface imgmt-eth0 to 10.0.0.$slot_num" >> /var/log/devmgr.log
            break;
        fi
        sleep 5
        echo "device-manager.sh is waiting for interface imgmt-eth0 to be up.." >> /var/log/devmgr.log
    done
}

function postStartAction()
{
    # opennsl-modules is a dependent service and runs after this one.
    # Wait a few seconds to ensure that 88690 enable timing constraints have been met.
    while true; do
	if [ -e /etc/sonic/card_details.json ]; then
	    break;
	fi
        sleep 1
        echo "device-manager.sh is waiting for /etc/sonic/card_details.json to appear.." >> /var/log/devmgr.log
    done

    sleep 60        # Devmgr boot and reset all 3 J2 and PCI probes needs 60 seconds

    route del default gw 10.0.0.16 # Workaround until its fixed in devmgr. 
}

start() {
    
    # Obtain boot type from kernel arguments
    BOOT_TYPE=`getBootType`

    HOSTNAME=`hostname`

    ONIE_PLATFORM=`grep onie_platform /host/machine.conf | awk 'BEGIN { FS = "=" } ; { print $2 }'`
    if [ "$ONIE_PLATFORM" == "x86_64-nokia_ixr7250-r0" ]; then
        # USB block storage requires explicit, and small, write cache settings to prevent stalling at cache flush time
        sudo sh -c "echo $((16*1024*1024)) > /proc/sys/vm/dirty_background_bytes"
        sudo sh -c "echo $((48*1024*1024)) > /proc/sys/vm/dirty_bytes"
    fi
    #Remove old docker instance
    docker rm -f device-manager

    echo "Creating new device-manager container with hostname $HOSTNAME"
    docker create --net=host --privileged -v /var/run/docker.sock:/var/run/docker.sock -v /etc/sonic:/etc/sonic:rw  \
        --log-opt max-size=2M --log-opt max-file=5 \
        -v /var/run/redis:/var/run/redis:rw \
        --tmpfs /tmp \
        --tmpfs /var/tmp \
        -e HW_TYPE="$HW_TYPE" \
        --hostname "$HOSTNAME" \
        --name=device-manager docker-device-manager:latest || {
            echo "Failed to docker run" >&1
            exit 4
    }

    preStartAction
    docker start device-manager
    postStartAction
}

wait() {
    docker wait device-manager
}

stop() {
    docker stop device-manager
}


echo "Debugservice - in common device-manager.sh - Platform Service caller = $1 "

case "$1" in
    start|wait|stop|updateHostName)
        cmd=$1
        shift
        $cmd $@
        ;;
    *)
        echo "Usage: $0 {start|wait|stop|updateHostName new_hostname}"
        exit 1
        ;;
esac
