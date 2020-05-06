#!/bin/bash

#################################################################################################################
#
# A Linux bridge connects the local interface used for iBGP sessions to the host interfaces representing
# the cpu ports of each of the other ASICs in the system. The same thing is done in the ASIC namespace of
# all SONiC instances in the system. This results in a full mesh of bridged interfaces between all the
# CPU ports. Linux ebtables are used to create split-horizon rules in-order to prevent the L2 loop that
# a full mesh of bridged connections would cause. The split horizon rules prevent the linux bridge from
# forwarding packets received on a remote CPU interface from being forwarded to other remote CPU
# interfaces. Forwarding is only allowed from the local interface to the remote CPU interfaces or from the
# remote CPU interfaces to the local interface.
#
# The script in this file does the following
#
# 1. Create the ebtables rules
# 2. Create a veth pair for local interface and assign an IP to one end of it.
# 3. Create the "cpulan" bridge and attach the other "veth" created above to it.
# 4. Attach remote CPU interfaces to the cpulan (these were created earlier using SAI host interface APIs)
# 5. Enable the local interface.
# 6. Enable the cpulan
#
#  Figure below shows asic0 for example
#
#
#                                |------------------|
#                  _______       |                  | <---------> asic1
#                 |       |      |                  | <---------> asic2
#      iBGP <---> |3.3.3.1| <--> |   cpulan         |  .
#                 |_______|      |                  |  .
#                                |                  |  .
#                                |                  | <---------> asicN
#                                |------------------|
#                                 
#
#
#
#################################################################################################################
setup_ebtables()
{
    echo setup_ebtables: setting up ebtables for cpulan
    echo setup_ebtables: create the cpulan chain
    ebtables -N cpulan
    echo setup_ebtables: default rule for cpulan is DROP
    ebtables -P cpulan DROP
    echo setup_ebtables: allow packets from local interface to go to other asics
    ebtables -A cpulan -i localsonic -j ACCEPT
    echo setup_ebtables: allow packets from other asics to go to local interface
    ebtables -A cpulan -o localsonic -j ACCEPT
    echo setup_ebtables: link the cpulan chain to the main FORWARD rule
    ebtables -I FORWARD --logical-in cpulan -j cpulan
}

cleanup_ebtables()
{
    echo cleanup_ebtables: cleaning up ebtables for cpulan
    echo cleanup_ebtables: unlink the cpulan chain from the main FORWARD rule
    ebtables -D FORWARD --logical-in cpulan -j cpulan
    echo cleanup_ebtables: delete the cpulan chain
    ebtables -X cpulan
}


setup_ip()
{
    myAsicNum=$1
    echo setup_ip: myAsicNum=$myAsicNum
    ip=$((myAsicNum+1))
    # Create configure and enable veth for remote sonic communication
    echo setup_ip: create the local interface called remotesonics
    ip link add remotesonics type veth peer name localsonic
    echo setup_ip: assign MAC address 02:00:00:00:00:$ip to the local interface
    ifconfig remotesonics hw ether 02:00:00:00:00:$ip
    echo setup_ip: assign IP address 3.3.3.$ip/24 to the local interface
    ip address add 3.3.3.$ip/24 dev remotesonics
    echo setup_ip: bring up the interface
    ifconfig remotesonics up
    ifconfig localsonic up

    # create bridge and add localsonic to it
    echo setup_ip: create cpulan bridge
    ip link add cpulan type bridge
    echo setup_ip: attach the other end of local interface to cpulan bridge
    ip link set localsonic master cpulan
}

cleanup_ip()
{
    echo cleanup_ip: delete local interface
    ip link del localsonic
    echo cleanup_ip: delete cpulan
    ip link del cpulan
}

setup_remoteasics()
{
    maxAsicNum=$1
    myAsicNum=$2
    echo setup_remoteasics: maxAsicNum=$maxAsicNum myAsicNum=$myAsicNum
    for i in `seq 0 $maxAsicNum`;
    do
        if(("$i"!="$myAsicNum")); then
            # echo setup_remoteasics: create remote asic$i. This is not needed since syncd creates this
            # ip link add asic$i type veth peer name asic$i-bcm
            echo setup_remoteasics: attach asic$i to cpulan
            ip link set asic$i master cpulan
            echo setup_remoteasics: bring up asic$i
            ifconfig asic$i up
            # echo setup_remoteasics: bring up the other end of asic$i. This not needed since knet takes care of this
            # ifconfig asic$i-bcm up
        fi
    done

}

cleanup_remoteasics()
{
    echo cleanup_remoteasics: maxAsicNum=$1 myAsicNum=$2
    maxAsicNum=$1
    myAsicNum=$2
    for i in `seq 0 $maxAsicNum`;
    do
        if(("$i"!="$myAsicNum")); then
            echo cleanup_remoteasics: delete remote asic$i
            ip link del asic$i
        fi
    done

}

setup_all()
{
    maxAsicNum=$1
    myAsicNum=$2
    echo setup_all: maxAsicNum=$1 myAsicNum=$2
    setup_ebtables
    setup_ip $myAsicNum
    setup_remoteasics $1 $2
    echo setup_all: enable the cpulan bridge
    ifconfig cpulan up
}

clean_all()
{
    echo clean_all: maxAsicNum=$1 myAsicNum=$2
    # cleanup_remoteasics $1 $2
    cleanup_ip
    cleanup_ebtables
}

if(("$1"=="clean")); then
    echo cpulan: $1 maxAsicNum=$2 myAsicNum=$3
    clean_all $2 $3
else
    echo cpulan: maxAsicNum=$1 myAsicNum=$2
    setup_all $1 $2
fi
