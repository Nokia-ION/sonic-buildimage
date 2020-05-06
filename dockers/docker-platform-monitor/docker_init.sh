#!/usr/bin/env bash

# Generate supervisord config file and the start.sh scripts
mkdir -p /etc/supervisor/conf.d/

if [ -e /usr/share/sonic/platform/pmon_daemon_control.json ]; 
then
    sonic-cfggen -j /usr/share/sonic/platform/pmon_daemon_control.json -t /usr/share/sonic/templates/docker-pmon.supervisord.conf.j2 > /etc/supervisor/conf.d/supervisord.conf
    sonic-cfggen -j /usr/share/sonic/platform/pmon_daemon_control.json -t /usr/share/sonic/templates/start.sh.j2 > /usr/bin/start.sh
    chmod +x /usr/bin/start.sh
    sonic-cfggen -j /usr/share/sonic/platform/pmon_daemon_control.json -t /usr/share/sonic/templates/critical_processes.j2 > /etc/supervisor/critical_processes
elif [ -e /usr/share/sonic/hwsku/pmon_daemon_control.json ];
then
    sonic-cfggen -j /usr/share/sonic/hwsku/pmon_daemon_control.json -t /usr/share/sonic/templates/docker-pmon.supervisord.conf.j2 > /etc/supervisor/conf.d/supervisord.conf
    sonic-cfggen -j /usr/share/sonic/hwsku/pmon_daemon_control.json -t /usr/share/sonic/templates/start.sh.j2 > /usr/bin/start.sh
    chmod +x /usr/bin/start.sh
    sonic-cfggen -j /usr/share/sonic/hwsku/pmon_daemon_control.json -t /usr/share/sonic/templates/critical_processes.j2 > /etc/supervisor/critical_processes
    
else
    sonic-cfggen -t /usr/share/sonic/templates/docker-pmon.supervisord.conf.j2 > /etc/supervisor/conf.d/supervisord.conf
    sonic-cfggen -t /usr/share/sonic/templates/start.sh.j2 > /usr/bin/start.sh
    chmod +x /usr/bin/start.sh
    sonic-cfggen -t /usr/share/sonic/templates/critical_processes.j2 > /etc/supervisor/critical_processes
fi

exec /usr/bin/supervisord

