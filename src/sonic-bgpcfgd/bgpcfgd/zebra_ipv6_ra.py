"""Map CONFIG_DB ipv6_ra_* fields onto FRR zebra IPv6 ND commands."""

import ipaddress

from .log import log_info

RA_SUPPRESS = "ipv6_ra_suppress"
RA_INTERVAL = "ipv6_ra_interval"
RA_LIFETIME = "ipv6_ra_lifetime"
RA_REACHABLE_TIME = "ipv6_ra_reachable_time"
RA_ENABLE = "ipv6_ra_enable"
RA_MANAGED = "ipv6_ra_managed"
RA_OTHER_CONFIG = "ipv6_ra_other_config"
RA_FIELDS = (
    RA_SUPPRESS,
    RA_INTERVAL,
    RA_LIFETIME,
    RA_REACHABLE_TIME,
    RA_ENABLE,
    RA_MANAGED,
    RA_OTHER_CONFIG,
)

RA_PREFIX_VALID = "ipv6_ra_prefix_valid_lifetime"
RA_PREFIX_PREFERRED = "ipv6_ra_prefix_preferred_lifetime"
RA_PREFIX_DISABLE_AUTOCONF = "ipv6_ra_prefix_disable_autoconfig"
RA_PREFIX_ENABLE_ONLINK = "ipv6_ra_prefix_enable_onlink"
RA_PREFIX_FIELDS = (
    RA_PREFIX_VALID,
    RA_PREFIX_PREFERRED,
    RA_PREFIX_DISABLE_AUTOCONF,
    RA_PREFIX_ENABLE_ONLINK,
)

RA_INFINITE_LIFETIME = "4294967295"

# BGP ipv6-auto-ra adds these when an unnumbered neighbor is created.
BGP_AUTO_RA_INTERVAL = "10"

RA_IFACE_TABLES = (
    "INTERFACE",
    "PORTCHANNEL_INTERFACE",
    "VLAN_SUB_INTERFACE",
)

BGP_NEIGHBOR_TABLES = (
    "BGP_NEIGHBOR",
    "BGP_INTERNAL_NEIGHBOR",
    "BGP_VOQ_CHASSIS_NEIGHBOR",
)

# FRR ignores "ipv6 nd suppress-ra" while BGP auto-RA owns the interface
# (BGP_RA_CONFIGURED). "no neighbor <if> interface" calls terminate_radv.
PEER_UNNUMBERED = "PEER_UNNUMBERED"


def _truthy(value):
    return str(value).strip().lower() in ("true", "1", "yes")


def has_ipv6_ra_config(data):
    if not data:
        return False
    return any(field in data and str(data[field]).strip() != "" for field in RA_FIELDS)


def has_ipv6_ra_prefix_config(data):
    if not data:
        return False
    for field in RA_PREFIX_FIELDS:
        if field in data and str(data[field]).strip() != "":
            return True
    return False


def has_ipv6_ra_any_config(iface_data, prefix_map=None):
    if has_ipv6_ra_config(iface_data):
        return True
    if not prefix_map:
        return False
    return any(has_ipv6_ra_prefix_config(val) for val in prefix_map.values())


def ra_advertising_disabled(data):
    if not data:
        return False
    if RA_ENABLE in data and str(data[RA_ENABLE]).strip() != "":
        if not _truthy(data[RA_ENABLE]):
            return True
    if RA_SUPPRESS in data and str(data[RA_SUPPRESS]).strip() != "":
        if _truthy(data[RA_SUPPRESS]):
            return True
    return False


def ra_suppress_enabled(data):
    return ra_advertising_disabled(data)


def _effective_interval(data):
    if data.get(RA_INTERVAL):
        return data[RA_INTERVAL]
    return None


def _network_prefix(prefix):
    return str(ipaddress.ip_interface(prefix).network)


def _format_lifetime(value):
    if str(value).strip() == RA_INFINITE_LIFETIME:
        return "infinite"
    return str(value)


def lookup_ipv6_ra_data(directory, ifname):
    """Return iface-row RA data for Ethernet / PortChannel / subif."""
    if not ifname:
        return {}
    for table in RA_IFACE_TABLES:
        slot = directory.get_slot("CONFIG_DB", table)
        if ifname in slot:
            data = slot[ifname]
            if isinstance(data, dict):
                return data
    return {}


def lookup_ipv6_ra_prefixes(directory, ifname):
    """Return IPv6 prefix-row RA data keyed by configured ip-prefix."""
    prefixes = {}
    if not ifname:
        return prefixes
    prefix_head = ifname + "|"
    for table in RA_IFACE_TABLES:
        slot = directory.get_slot("CONFIG_DB", table)
        for key, val in slot.items():
            if not key.startswith(prefix_head) or not isinstance(val, dict):
                continue
            pfx = key.split("|", 1)[1]
            if ":" in pfx:
                prefixes[pfx] = val
    return prefixes


def lookup_interface_ipv6_prefixes(directory, ifname):
    """All CONFIG_DB IPv6 prefix suffixes on ifname (assigned addresses)."""
    out = []
    if not ifname:
        return out
    prefix_head = ifname + "|"
    seen = set()
    for table in RA_IFACE_TABLES:
        slot = directory.get_slot("CONFIG_DB", table)
        for key in slot:
            if not key.startswith(prefix_head):
                continue
            pfx = key.split("|", 1)[1]
            if ":" not in pfx:
                continue
            net = _network_prefix(pfx)
            if net not in seen:
                seen.add(net)
                out.append(pfx)
    return out


def lookup_bgp_asn(directory):
    if not directory:
        return None
    slot = directory.get_slot("CONFIG_DB", "DEVICE_METADATA")
    host = slot.get("localhost") if slot else None
    if isinstance(host, dict):
        return host.get("bgp_asn")
    return None


def lookup_bgp_neighbor(directory, ifname):
    if not directory or not ifname:
        return {}
    for table in BGP_NEIGHBOR_TABLES:
        slot = directory.get_slot("CONFIG_DB", table)
        for key, val in slot.items():
            if key.split("|")[-1] == ifname and isinstance(val, dict):
                return val
    return {}


def has_unnumbered_bgp_neighbor(directory, ifname):
    if not ifname:
        return False
    return bool(lookup_bgp_neighbor(directory, ifname)) or _has_neighbor_key(directory, ifname)


def _has_neighbor_key(directory, ifname):
    for table in BGP_NEIGHBOR_TABLES:
        slot = directory.get_slot("CONFIG_DB", table)
        for key in slot:
            if key.split("|")[-1] == ifname:
                return True
    return False


def bgp_unnumbered_stop_ra_commands(ifname, directory):
    """Stop BGP-driven RAs on ifname. ipv6 nd suppress-ra is not enough."""
    asn = lookup_bgp_asn(directory)
    if not asn or not has_unnumbered_bgp_neighbor(directory, ifname):
        return []
    return [
        "router bgp %s" % asn,
        " no neighbor %s interface" % ifname,
        "exit",
    ]


def bgp_unnumbered_restore_commands(ifname, directory):
    """Re-create the unnumbered neighbor so BGP auto-RA can run again."""
    asn = lookup_bgp_asn(directory)
    nbr = lookup_bgp_neighbor(directory, ifname)
    if not asn or not has_unnumbered_bgp_neighbor(directory, ifname):
        return []
    cmds = [
        "router bgp %s" % asn,
        " neighbor %s interface peer-group %s" % (ifname, PEER_UNNUMBERED),
    ]
    peer_asn = nbr.get("asn")
    if peer_asn:
        cmds.append(" neighbor %s remote-as %s" % (ifname, peer_asn))
    name = nbr.get("name")
    if name:
        cmds.append(" neighbor %s description %s" % (ifname, name))
    cmds.extend(
        [
            " address-family ipv4 unicast",
            "  neighbor %s activate" % ifname,
            " exit-address-family",
            " address-family ipv6 unicast",
            "  neighbor %s activate" % ifname,
            " exit-address-family",
            "exit",
        ]
    )
    return cmds


def _append_bool_nd_flag(cmds, data, field, enable_cmd, disable_cmd):
    if field not in data or str(data[field]).strip() == "":
        return
    if _truthy(data[field]):
        cmds.append(enable_cmd)
    else:
        cmds.append(disable_cmd)


def explicit_ipv6_ra_prefix_networks(prefix_map):
    """
    Prefix networks that have explicit ipv6_ra_prefix_* in CONFIG_DB.

    Address-only INTERFACE rows (no RA fields) are omitted; zebra advertises
    those connected globals without an ipv6 nd prefix line.
    """
    return _ipv6_nd_prefix_configured_networks(prefix_map)


# Backward-compatible alias for tests / callers.
configured_ipv6_prefix_networks = explicit_ipv6_ra_prefix_networks


def ipv6_nd_prefix_remove_commands(removed_prefixes):
    return [" no ipv6 nd prefix %s" % net for net in sorted(removed_prefixes or [])]


def _address_only_ipv6_prefix_networks(prefix_map):
    nets = set()
    for pfx, pdata in (prefix_map or {}).items():
        if ":" not in pfx:
            continue
        if not has_ipv6_ra_prefix_config(pdata):
            nets.add(_network_prefix(pfx))
    return nets


def ipv6_nd_prefix_restore_after_explicit_removed(
    directory, ifname, cleared_nets, prefix_map
):
    """Re-advertise address-only prefixes after explicit RA prefix config is removed."""
    if not cleared_nets:
        return []
    still_present = _address_only_ipv6_prefix_networks(prefix_map)
    cmds = []
    for net in sorted(cleared_nets):
        if net not in still_present:
            continue
        if directory and ifname:
            configured = {
                _network_prefix(p)
                for p in lookup_interface_ipv6_prefixes(directory, ifname)
            }
            if net not in configured:
                continue
        cmds.append(" ipv6 nd prefix %s" % net)
    return cmds


def _ipv6_nd_prefix_positive_command(pfx, pdata):
    net = _network_prefix(pfx)
    parts = ["ipv6 nd prefix %s" % net]
    pdata = pdata or {}
    valid = pdata.get(RA_PREFIX_VALID)
    preferred = pdata.get(RA_PREFIX_PREFERRED)
    if valid or preferred:
        parts.append(_format_lifetime(valid) if valid else "infinite")
        parts.append(_format_lifetime(preferred) if preferred else "infinite")
    if RA_PREFIX_ENABLE_ONLINK in pdata and str(pdata[RA_PREFIX_ENABLE_ONLINK]).strip() != "":
        if not _truthy(pdata[RA_PREFIX_ENABLE_ONLINK]):
            parts.append("off-link")
    if _truthy(pdata.get(RA_PREFIX_DISABLE_AUTOCONF, "false")):
        parts.append("no-autoconfig")
    return " " + " ".join(parts)


def ipv6_nd_prefix_commands(prefix_map):
    cmds = []
    for pfx in sorted(prefix_map):
        pdata = prefix_map[pfx]
        if not has_ipv6_ra_prefix_config(pdata):
            continue
        cmds.append(_ipv6_nd_prefix_positive_command(pfx, pdata))
    return cmds


def _has_any_ipv6_ra_prefix_config(prefix_map):
    if not prefix_map:
        return False
    return any(has_ipv6_ra_prefix_config(val) for val in prefix_map.values())


def _ipv6_nd_prefix_configured_networks(prefix_map):
    nets = set()
    for pfx, pdata in (prefix_map or {}).items():
        if has_ipv6_ra_prefix_config(pdata):
            nets.add(_network_prefix(pfx))
    return nets


def ipv6_ra_frr_commands(
    ifname,
    data,
    restore_bgp_auto_ra=False,
    directory=None,
    prefix_map=None,
    removed_prefixes=None,
):
    """
    Build vtysh interface commands.

    When RA fields are present they override BGP auto-RA.
    When they are absent, restore either BGP auto-RA or zebra defaults.
    """
    if not ifname:
        return []

    prefix_map = prefix_map or {}
    has_global = has_ipv6_ra_config(data)
    has_prefix_ra = _has_any_ipv6_ra_prefix_config(prefix_map)
    cmds = []
    if ra_advertising_disabled(data):
        cmds.extend(bgp_unnumbered_stop_ra_commands(ifname, directory))
    elif directory and restore_bgp_auto_ra:
        cmds.extend(bgp_unnumbered_restore_commands(ifname, directory))
    elif (
        directory
        and (has_global or has_prefix_ra)
        and not ra_advertising_disabled(data)
    ):
        # OC / CONFIG_DB ipv6_ra_* overrides BGP auto-RA on unnumbered interfaces.
        cmds.extend(bgp_unnumbered_stop_ra_commands(ifname, directory))

    cmds.append("interface %s" % ifname)
    cmds.extend(ipv6_nd_prefix_remove_commands(removed_prefixes))
    if has_global or has_prefix_ra:
        if ra_advertising_disabled(data):
            cmds.append(" ipv6 nd suppress-ra")
        else:
            cmds.append(" no ipv6 nd suppress-ra")

        if has_global:
            interval = _effective_interval(data)
            if interval:
                cmds.append(" ipv6 nd ra-interval %s" % interval)
            if data.get(RA_LIFETIME):
                cmds.append(" ipv6 nd ra-lifetime %s" % data[RA_LIFETIME])
            if data.get(RA_REACHABLE_TIME):
                cmds.append(
                    " ipv6 nd reachable-time %s" % data[RA_REACHABLE_TIME]
                )

            _append_bool_nd_flag(
                cmds,
                data,
                RA_MANAGED,
                " ipv6 nd managed-config-flag",
                " no ipv6 nd managed-config-flag",
            )
            _append_bool_nd_flag(
                cmds,
                data,
                RA_OTHER_CONFIG,
                " ipv6 nd other-config-flag",
                " no ipv6 nd other-config-flag",
            )
        if has_prefix_ra:
            cmds.extend(ipv6_nd_prefix_commands(prefix_map))
        if removed_prefixes:
            cmds.extend(
                ipv6_nd_prefix_restore_after_explicit_removed(
                    directory, ifname, removed_prefixes, prefix_map
                )
            )
    elif restore_bgp_auto_ra:
        cmds.append(" no ipv6 nd suppress-ra")
        cmds.append(" ipv6 nd ra-interval %s" % BGP_AUTO_RA_INTERVAL)
        cmds.append(" no ipv6 nd ra-lifetime")
    else:
        cmds.append(" ipv6 nd suppress-ra")
        cmds.append(" no ipv6 nd ra-interval")
        cmds.append(" no ipv6 nd ra-lifetime")
        cmds.append(" no ipv6 nd reachable-time")
    cmds.append("exit")
    return cmds


def apply_ipv6_ra(
    cfg_mgr,
    ifname,
    data,
    restore_bgp_auto_ra=False,
    revert=False,
    directory=None,
    prefix_map=None,
    removed_prefixes=None,
):
    if not ifname:
        return False
    prefix_map = prefix_map or {}
    if (
        not has_ipv6_ra_any_config(data, prefix_map)
        and not restore_bgp_auto_ra
        and not revert
        and not removed_prefixes
    ):
        return False
    cmds = ipv6_ra_frr_commands(
        ifname,
        data,
        restore_bgp_auto_ra=restore_bgp_auto_ra,
        directory=directory,
        prefix_map=prefix_map,
        removed_prefixes=removed_prefixes,
    )
    cfg_mgr.push("\n".join(cmds))
    log_info("IPv6 RA FRR config scheduled for %s" % ifname)
    return True
