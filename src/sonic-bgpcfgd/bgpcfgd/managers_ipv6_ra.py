from swsscommon import swsscommon

from .log import log_debug
from .manager import Manager
from .zebra_ipv6_ra import (
    apply_ipv6_ra,
    explicit_ipv6_ra_prefix_networks,
    has_ipv6_ra_any_config,
    has_unnumbered_bgp_neighbor,
    lookup_ipv6_ra_data,
    lookup_ipv6_ra_prefixes,
)


class ZebraIpv6RaMgr(Manager):
    """Push INTERFACE / PORTCHANNEL_INTERFACE / VLAN_SUB_INTERFACE RA into zebra."""

    def __init__(self, common_objs, db, table):
        super(ZebraIpv6RaMgr, self).__init__(
            common_objs,
            [],
            db,
            table,
        )
        self._applied = {}
        self._applied_prefixes = {}
        self._config_db = None

    def _config_db_conn(self):
        if self._config_db is None:
            self._config_db = swsscommon.ConfigDBConnector()
            self._config_db.connect()
        return self._config_db

    def _iface_ra_data(self, ifname):
        data = self._config_db_conn().get_entry(self.table_name, ifname)
        if isinstance(data, dict) and data:
            return data
        return lookup_ipv6_ra_data(self.directory, ifname)

    def _prefix_key_parts(self, key, ifname):
        if isinstance(key, tuple):
            if len(key) < 2 or key[0] != ifname:
                return None
            return key[1]
        if isinstance(key, str) and key.startswith(ifname + "|"):
            return key.split("|", 1)[1]
        return None

    def _prefix_map(self, ifname):
        """CONFIG_DB is authoritative (Directory can be partial on gNMI/OC updates)."""
        prefix_map = {}
        try:
            keys = list(self._config_db_conn().get_keys(self.table_name))
        except (TypeError, AttributeError):
            keys = []
        for key in keys:
            pfx = self._prefix_key_parts(key, ifname)
            if not pfx or ":" not in pfx:
                continue
            entry = self._config_db_conn().get_entry(self.table_name, key)
            if isinstance(entry, dict):
                prefix_map[pfx] = entry
        return prefix_map

    def sync_all(self):
        """Re-apply RA from CONFIG_DB (e.g. after bgpcfgd restart)."""
        ifnames = set()
        for key in self._config_db_conn().get_keys(self.table_name):
            if isinstance(key, tuple):
                ifnames.add(key[0])
            else:
                ifnames.add(self._ifname_from_key(key))
        for ifname in sorted(ifnames):
            self._apply_iface(ifname)

    def _ifname_from_key(self, key):
        if isinstance(key, tuple):
            return key[0]
        if isinstance(key, str) and "|" in key:
            return key.split("|", 1)[0]
        return key

    def _key_has_prefix_suffix(self, key):
        if isinstance(key, tuple):
            return len(key) > 1
        return isinstance(key, str) and "|" in key

    def _apply_iface(self, ifname):
        iface_data = self._iface_ra_data(ifname)
        prefix_map = self._prefix_map(ifname)
        # Only prefixes with explicit ipv6_ra_prefix_* ever get an FRR nd prefix line.
        # Dropping an address-only row must not send "no ipv6 nd prefix" (zebra drops
        # connected globals when the address goes away).
        old_nets = self._applied_prefixes.get(ifname, set())
        new_nets = explicit_ipv6_ra_prefix_networks(prefix_map)
        removed = old_nets - new_nets
        if has_ipv6_ra_any_config(iface_data, prefix_map) or (
            removed and ifname in self._applied
        ):
            apply_ipv6_ra(
                self.cfg_mgr,
                ifname,
                iface_data,
                directory=self.directory,
                prefix_map=prefix_map,
                removed_prefixes=removed,
            )
            self._applied[ifname] = True
            self._applied_prefixes[ifname] = new_nets
            return True
        if ifname in self._applied:
            restore = has_unnumbered_bgp_neighbor(self.directory, ifname)
            apply_ipv6_ra(
                self.cfg_mgr,
                ifname,
                {},
                restore_bgp_auto_ra=restore,
                revert=not restore,
                directory=self.directory,
                removed_prefixes=old_nets,
            )
            self._applied.pop(ifname, None)
            self._applied_prefixes.pop(ifname, None)
        return True

    def set_handler(self, key, data):
        ifname = self._ifname_from_key(key)
        return self._apply_iface(ifname)

    def del_handler(self, key):
        ifname = self._ifname_from_key(key)
        if self._key_has_prefix_suffix(key):
            return self._apply_iface(ifname)
        if ifname not in self._applied:
            log_debug("IPv6 RA FRR: no applied RA to remove on %s" % ifname)
            return
        restore = has_unnumbered_bgp_neighbor(self.directory, ifname)
        apply_ipv6_ra(
            self.cfg_mgr,
            ifname,
            {},
            restore_bgp_auto_ra=restore,
            revert=not restore,
            directory=self.directory,
            removed_prefixes=self._applied_prefixes.get(ifname, set()),
        )
        self._applied.pop(ifname, None)
        self._applied_prefixes.pop(ifname, None)
