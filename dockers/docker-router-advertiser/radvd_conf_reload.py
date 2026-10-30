#!/usr/bin/env python3
"""
Regenerate /etc/radvd.conf when VLAN_INTERFACE RA-related CONFIG_DB entries change.
"""

import os
import signal
import subprocess
import threading

from sonic_py_common import daemon_base
from swsscommon.swsscommon import ConfigDBConnector

SYSLOG_IDENTIFIER = "radvd_conf_reload"
VERSION = "1.1"

RADVD_CONF_TMPL = "/usr/share/sonic/templates/radvd.conf.j2"
RADVD_CONF = "/etc/radvd.conf"
CFGGEN = "/usr/local/bin/sonic-cfggen"
DEBOUNCE_SEC = 2

# radvd.conf.j2 is generated from VLAN_INTERFACE today.
RA_WATCH_TABLES = ("VLAN_INTERFACE",)


def _key_to_str(key):
    if isinstance(key, tuple):
        return "|".join(str(part) for part in key)
    return str(key)


def _is_ipv6_composite_intf_key(key):
    """True for CONFIG_DB keys like Vlan100|2001:db8::/64 (tuple or string)."""
    key_s = _key_to_str(key)
    if "|" not in key_s:
        return False
    suffix = key_s.split("|", 1)[1]
    return ":" in suffix


def _entry_affects_radvd(key, data):
    # Any change on an IPv6 suffix row can alter radvd prefix {} blocks.
    if _is_ipv6_composite_intf_key(key):
        return True
    if not data:
        return True
    for field in data:
        if field.startswith("ipv6_ra_"):
            return True
    return False


def _regenerate_radvd_conf():
    subprocess.check_call(
        [CFGGEN, "-d", "-t", "{},{}".format(RADVD_CONF_TMPL, RADVD_CONF)]
    )


def _reload_radvd_process():
    # Prefix lifetime/flag updates are not always applied on SIGHUP alone; restart
    # the supervised radvd child when available.
    try:
        subprocess.run(
            ["supervisorctl", "restart", "radvd"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    pid_path = "/var/run/radvd.pid"
    if not os.path.isfile(pid_path):
        return
    with open(pid_path) as pid_file:
        pid = int(pid_file.read().strip())
    os.kill(pid, signal.SIGHUP)


class RadvdConfReload(daemon_base.DaemonBase):
    def __init__(self):
        super().__init__(SYSLOG_IDENTIFIER)
        self.config_db = ConfigDBConnector()
        self.config_db.connect()
        self._reload_lock = threading.Lock()
        self._reload_timer = None

    def _apply_reload(self):
        try:
            _regenerate_radvd_conf()
            _reload_radvd_process()
            self.log_notice("Regenerated {} and reloaded radvd".format(RADVD_CONF))
        except Exception as err:
            self.log_error("Failed to refresh radvd config: {}".format(err))

    def _schedule_reload(self):
        with self._reload_lock:
            if self._reload_timer is not None:
                self._reload_timer.cancel()
            self._reload_timer = threading.Timer(DEBOUNCE_SEC, self._apply_reload)
            self._reload_timer.daemon = True
            self._reload_timer.start()

    def intf_handler(self, table, key, data):
        if table not in RA_WATCH_TABLES:
            return
        if not _entry_affects_radvd(key, data):
            return
        self.log_notice(
            "Scheduling radvd reload for {} key {}".format(table, _key_to_str(key))
        )
        self._schedule_reload()

    def run(self):
        self.log_notice("Starting radvd CONFIG_DB watcher (version {})".format(VERSION))
        for table in RA_WATCH_TABLES:
            self.config_db.subscribe(table, self.intf_handler)
        self.config_db.listen()


def main():
    reload_daemon = RadvdConfReload()
    reload_daemon.run()


if __name__ == "__main__":
    main()
