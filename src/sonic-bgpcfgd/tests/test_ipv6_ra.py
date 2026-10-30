import sys
from unittest.mock import MagicMock

# Real swsscommon exposes ConfigDBConnector on swsscommon.swsscommon; a bare
# MagicMock does not and lazy _config_db_conn() in ZebraIpv6RaMgr fails.
_swss_inner = MagicMock()
_swss_inner.ConfigDBConnector = MagicMock
_swss_pkg = MagicMock()
_swss_pkg.swsscommon = _swss_inner
sys.modules.setdefault("swsscommon", _swss_pkg)
sys.modules.setdefault("swsscommon.swsscommon", _swss_inner)
sys.modules.setdefault("sonic_py_common", MagicMock())
sys.modules.setdefault("sonic_py_common.device_info", MagicMock())

from bgpcfgd.directory import Directory
from bgpcfgd.template import TemplateFabric
from bgpcfgd.managers_ipv6_ra import ZebraIpv6RaMgr
from bgpcfgd.zebra_ipv6_ra import (
    ipv6_ra_frr_commands,
    has_ipv6_ra_config,
    has_ipv6_ra_prefix_config,
    ipv6_nd_prefix_commands,
    lookup_ipv6_ra_prefixes,
    configured_ipv6_prefix_networks,
)


def _mgr():
    cfg_mgr = MagicMock()
    common_objs = {
        "directory": Directory(),
        "cfg_mgr": cfg_mgr,
        "tf": TemplateFabric(),
        "constants": {},
    }
    m = ZebraIpv6RaMgr(common_objs, "CONFIG_DB", "INTERFACE")
    _mock_config_db(m)
    return m, cfg_mgr


def _mock_config_db(mgr, iface_entries=None, prefix_entries=None):
    iface_entries = iface_entries or {}
    prefix_entries = prefix_entries or {}
    keys = list(iface_entries.keys()) + list(prefix_entries.keys())
    db = MagicMock()

    def get_entry(_table, key):
        if key in iface_entries:
            return iface_entries[key]
        if key in prefix_entries:
            return prefix_entries[key]
        return {}

    db.get_keys.return_value = keys
    db.get_entry.side_effect = get_entry
    mgr._config_db = db


def test_has_ipv6_ra_config():
    assert has_ipv6_ra_config({"ipv6_ra_interval": "30"})
    assert has_ipv6_ra_config({"ipv6_ra_managed": "true"})
    assert not has_ipv6_ra_config({})
    assert not has_ipv6_ra_config({"ipv6_use_link_local_only": "enable"})


def test_ipv6_ra_frr_commands_interval_only_unsuppresses():
    cmds = ipv6_ra_frr_commands("Ethernet0", {"ipv6_ra_interval": "20"})
    assert " no ipv6 nd suppress-ra" in cmds
    assert " ipv6 nd ra-interval 20" in cmds


def test_ipv6_ra_frr_commands_override():
    cmds = ipv6_ra_frr_commands(
        "Ethernet1",
        {
            "ipv6_ra_interval": "30",
            "ipv6_ra_lifetime": "180",
            "ipv6_ra_suppress": "false",
        },
    )
    assert "interface Ethernet1" in cmds
    assert " no ipv6 nd suppress-ra" in cmds
    assert " ipv6 nd ra-interval 30" in cmds
    assert " ipv6 nd ra-lifetime 180" in cmds


def test_ipv6_ra_frr_commands_reachable_time():
    cmds = ipv6_ra_frr_commands(
        "Ethernet1",
        {
            "ipv6_ra_interval": "30",
            "ipv6_ra_reachable_time": "30000",
        },
    )
    assert " ipv6 nd reachable-time 30000" in cmds


def test_ipv6_ra_frr_commands_suppress():
    cmds = ipv6_ra_frr_commands("PortChannel10", {"ipv6_ra_suppress": "true"})
    assert " ipv6 nd suppress-ra" in cmds
    assert "no neighbor" not in "\n".join(cmds)


def test_ipv6_ra_frr_commands_enable_and_flags():
    cmds = ipv6_ra_frr_commands(
        "Ethernet2",
        {
            "ipv6_ra_enable": "true",
            "ipv6_ra_interval": "30",
            "ipv6_ra_managed": "true",
            "ipv6_ra_other_config": "false",
        },
    )
    joined = "\n".join(cmds)
    assert " no ipv6 nd suppress-ra" in joined
    assert " ipv6 nd ra-interval 30" in joined
    assert " ipv6 nd managed-config-flag" in joined
    assert " no ipv6 nd other-config-flag" in joined


def test_ipv6_ra_frr_commands_enable_false_stops_ra():
    cmds = ipv6_ra_frr_commands("Ethernet2", {"ipv6_ra_enable": "false"})
    assert " ipv6 nd suppress-ra" in cmds


def test_ipv6_ra_frr_commands_override_stops_bgp_unnumbered():
    d = Directory()
    d.put("CONFIG_DB", "DEVICE_METADATA", "localhost", {"bgp_asn": "65100"})
    d.put("CONFIG_DB", "BGP_NEIGHBOR", "default|Ethernet0", {"asn": "65200"})
    cmds = ipv6_ra_frr_commands(
        "Ethernet0",
        {"ipv6_ra_interval": "20"},
        directory=d,
    )
    joined = "\n".join(cmds)
    assert " no neighbor Ethernet0 interface" in joined
    assert "neighbor Ethernet0 interface peer-group" not in joined
    assert " ipv6 nd ra-interval 20" in joined


def test_ipv6_ra_frr_commands_suppress_stops_bgp_auto_ra():
    m, _cfg = _mgr()
    m.directory.put("CONFIG_DB", "DEVICE_METADATA", "localhost", {"bgp_asn": "65100"})
    m.directory.put("CONFIG_DB", "BGP_NEIGHBOR", "default|Ethernet0", {"asn": "65200"})
    cmds = ipv6_ra_frr_commands(
        "Ethernet0",
        {"ipv6_ra_suppress": "true"},
        directory=m.directory,
    )
    joined = "\n".join(cmds)
    assert "router bgp 65100" in joined
    assert " no neighbor Ethernet0 interface" in joined
    assert " ipv6 nd suppress-ra" in joined


def test_has_ipv6_ra_prefix_config():
    assert has_ipv6_ra_prefix_config(
        {"ipv6_ra_prefix_disable_autoconfig": "true"}
    )
    assert not has_ipv6_ra_prefix_config({"NULL": "NULL"})


def test_removed_explicit_prefix_restores_connected():
    d = Directory()
    d.put("CONFIG_DB", "INTERFACE", "Ethernet0", {"ipv6_ra_interval": "20"})
    d.put("CONFIG_DB", "INTERFACE", "Ethernet0|2004:db8:100::1/64", {"NULL": "NULL"})
    iface = d.get_slot("CONFIG_DB", "INTERFACE")["Ethernet0"]
    prefix_map = lookup_ipv6_ra_prefixes(d, "Ethernet0")
    removed = {"2004:db8:100::/64"}
    cmds = "\n".join(
        ipv6_ra_frr_commands(
            "Ethernet0",
            iface,
            directory=d,
            prefix_map=prefix_map,
            removed_prefixes=removed,
        )
    )
    assert " no ipv6 nd prefix 2004:db8:100::/64" in cmds
    assert " ipv6 nd prefix 2004:db8:100::/64" in cmds


def test_prefix_preferred_lifetime_does_not_suppress_other_connected():
    d = Directory()
    d.put(
        "CONFIG_DB",
        "INTERFACE",
        "Ethernet0",
        {"ipv6_ra_interval": "10", "ipv6_ra_lifetime": "800"},
    )
    d.put(
        "CONFIG_DB",
        "INTERFACE",
        "Ethernet0|2004:db8:100::/64",
        {"ipv6_ra_prefix_preferred_lifetime": "900"},
    )
    d.put(
        "CONFIG_DB",
        "INTERFACE",
        "Ethernet0|2003:db8:100::/64",
        {"NULL": "NULL"},
    )
    iface = d.get_slot("CONFIG_DB", "INTERFACE")["Ethernet0"]
    cmds = ipv6_ra_frr_commands(
        "Ethernet0",
        iface,
        directory=d,
        prefix_map=lookup_ipv6_ra_prefixes(d, "Ethernet0"),
    )
    joined = "\n".join(cmds)
    assert "ipv6 nd prefix 2004:db8:100::/64 infinite 900" in joined
    assert "2003:db8:100::/64" not in joined


def test_ipv6_nd_prefix_commands():
    cmds = ipv6_nd_prefix_commands(
        {
            "2001:db8::1/64": {
                "ipv6_ra_prefix_valid_lifetime": "1800",
                "ipv6_ra_prefix_preferred_lifetime": "600",
                "ipv6_ra_prefix_enable_onlink": "false",
                "ipv6_ra_prefix_disable_autoconfig": "true",
            }
        }
    )
    assert cmds == [
        " ipv6 nd prefix 2001:db8::/64 1800 600 off-link no-autoconfig"
    ]


def test_explicit_ipv6_ra_prefix_networks_ignores_address_only_rows():
    prefix_map = {
        "2001:db8::1/64": {"ipv6_ra_prefix_disable_autoconfig": "true"},
        "2010:db8::1/64": {"NULL": "NULL"},
        "10.0.0.1/24": {},
    }
    assert configured_ipv6_prefix_networks(prefix_map) == {"2001:db8::/64"}


def test_interval_only_does_not_emit_prefix_lines_for_addresses():
    d = Directory()
    d.put(
        "CONFIG_DB",
        "INTERFACE",
        "Ethernet0",
        {"ipv6_ra_interval": "20"},
    )
    d.put("CONFIG_DB", "INTERFACE", "Ethernet0|2004:db8:100::/64", {"NULL": "NULL"})
    d.put("CONFIG_DB", "INTERFACE", "Ethernet0|2006:db8:100::/64", {"NULL": "NULL"})
    iface = d.get_slot("CONFIG_DB", "INTERFACE")["Ethernet0"]
    joined = "\n".join(
        ipv6_ra_frr_commands(
            "Ethernet0",
            iface,
            directory=d,
            prefix_map=lookup_ipv6_ra_prefixes(d, "Ethernet0"),
        )
    )
    assert " ipv6 nd ra-interval 20" in joined
    assert "ipv6 nd prefix" not in joined


def test_mgr_applies_on_prefix_key_update():
    m, cfg_mgr = _mgr()
    pfx = ("Ethernet1", "2001:db8::1/64")
    pdata = {"ipv6_ra_prefix_disable_autoconfig": "true"}
    m.directory.put("CONFIG_DB", "INTERFACE", "Ethernet1|2001:db8::1/64", pdata)
    _mock_config_db(m, prefix_entries={pfx: pdata})
    assert m.set_handler("Ethernet1|2001:db8::1/64", pdata)
    pushed = cfg_mgr.push.call_args[0][0]
    assert "ipv6 nd prefix 2001:db8::/64" in pushed
    assert "no-autoconfig" in pushed


def test_mgr_removes_frr_prefix_when_config_db_prefix_deleted():
    m, cfg_mgr = _mgr()
    iface = {"ipv6_ra_enable": "true", "ipv6_ra_interval": "60"}
    p1 = ("Ethernet0", "2001:db8:1::/64")
    p2 = ("Ethernet0", "2001:db8:2::/64")
    d1 = {
        "ipv6_ra_prefix_valid_lifetime": "1800",
        "ipv6_ra_prefix_preferred_lifetime": "600",
        "ipv6_ra_prefix_disable_autoconfig": "true",
    }
    d2 = {"ipv6_ra_prefix_disable_autoconfig": "true"}
    m.directory.put("CONFIG_DB", "INTERFACE", "Ethernet0", iface)
    m.directory.put("CONFIG_DB", "INTERFACE", "Ethernet0|2001:db8:1::/64", d1)
    m.directory.put("CONFIG_DB", "INTERFACE", "Ethernet0|2001:db8:2::/64", d2)
    _mock_config_db(
        m,
        iface_entries={"Ethernet0": iface},
        prefix_entries={p1: d1, p2: d2},
    )
    assert m.set_handler("Ethernet0", iface)
    pushed = cfg_mgr.push.call_args[0][0]
    assert "ipv6 nd prefix 2001:db8:1::/64" in pushed
    assert "ipv6 nd prefix 2001:db8:2::/64" in pushed

    cfg_mgr.push.reset_mock()
    m.directory.remove("CONFIG_DB", "INTERFACE", "Ethernet0|2001:db8:1::/64")
    _mock_config_db(
        m,
        iface_entries={"Ethernet0": iface},
        prefix_entries={p2: d2},
    )
    assert m.del_handler("Ethernet0|2001:db8:1::/64")
    pushed = cfg_mgr.push.call_args[0][0]
    assert " no ipv6 nd prefix 2001:db8:1::/64" in pushed
    assert " no ipv6 nd prefix 2001:db8:2::/64" not in pushed

    cfg_mgr.push.reset_mock()
    m.directory.remove("CONFIG_DB", "INTERFACE", "Ethernet0|2001:db8:2::/64")
    _mock_config_db(m, iface_entries={"Ethernet0": iface}, prefix_entries={})
    assert m.del_handler("Ethernet0|2001:db8:2::/64")
    pushed = cfg_mgr.push.call_args[0][0]
    assert " no ipv6 nd prefix 2001:db8:2::/64" in pushed


def test_mgr_skips_empty_iface_without_ra():
    m, cfg_mgr = _mgr()
    _mock_config_db(m, iface_entries={"Ethernet1": {}})
    assert m.set_handler("Ethernet1", {}) is True
    cfg_mgr.push.assert_not_called()


def test_mgr_applies_and_restores_bgp_auto_ra():
    m, cfg_mgr = _mgr()
    m.directory.put("CONFIG_DB", "BGP_NEIGHBOR", "default|Ethernet1", {})
    ra_data = {"ipv6_ra_interval": "30", "ipv6_ra_suppress": "false"}
    m.directory.put("CONFIG_DB", "INTERFACE", "Ethernet1", ra_data)
    _mock_config_db(m, iface_entries={"Ethernet1": ra_data})
    assert m.set_handler("Ethernet1", ra_data)
    pushed = cfg_mgr.push.call_args[0][0]
    assert "ipv6 nd ra-interval 30" in pushed
    cfg_mgr.push.reset_mock()
    m.directory.put("CONFIG_DB", "INTERFACE", "Ethernet1", {})
    _mock_config_db(m, iface_entries={"Ethernet1": {}})
    assert m.set_handler("Ethernet1", {}) is True
    pushed = cfg_mgr.push.call_args[0][0]
    assert "ipv6 nd ra-interval 10" in pushed
    assert "no ipv6 nd suppress-ra" in pushed
