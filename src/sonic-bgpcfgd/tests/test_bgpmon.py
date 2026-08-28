import pytest
import json
from unittest.mock import MagicMock, patch, call
import syslog
import bgpmon.bgpmon
from bgpmon.bgpmon import BgpStateGet


@pytest.fixture
@patch('bgpmon.bgpmon.swsscommon.RedisPipeline')
@patch('bgpmon.bgpmon.swsscommon.SonicV2Connector')
def bgp_mon(mock_conn, mock_pipe):
    mock_db = mock_conn.return_value
    mock_db.STATE_DB = 'STATE_DB'
    m = BgpStateGet()
    mock_conn.assert_called_once_with(use_unix_socket_path=True)
    mock_db.connect.assert_called_once_with('STATE_DB', False)
    return m


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, ""))
@patch('os.system', return_value=1)
@patch('syslog.syslog')
def test_vtysh_fail_both_daemons_down(mocked_syslog, mocked_os_system, mocked_cmd, bgp_mon):
    """When vtysh fails and neither bgpd nor zebra is running, log WARNING."""
    bgp_mon.get_all_neigh_states()
    mocked_syslog.assert_called_once()
    level, msg = mocked_syslog.call_args[0]
    assert level == syslog.LOG_WARNING
    assert "bgpd or zebra not running" in msg
    assert "container may be shutting down" in msg


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, ""))
@patch('os.system', return_value=0)
@patch('syslog.syslog')
def test_vtysh_fail_both_daemons_running(mocked_syslog, mocked_os_system, mocked_cmd, bgp_mon):
    """When vtysh fails but bgpd and zebra ARE running, log ERR."""
    bgp_mon.get_all_neigh_states()
    mocked_syslog.assert_called_once()
    level, msg = mocked_syslog.call_args[0]
    assert level == syslog.LOG_ERR
    assert "*ERROR* Failed with rc:1 when execute" in msg


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, ""))
@patch('os.system', side_effect=[1, 0])
@patch('syslog.syslog')
def test_vtysh_fail_only_bgpd_down(mocked_syslog, mocked_os_system, mocked_cmd, bgp_mon):
    """When vtysh fails and only bgpd is down (first pgrep fails), log WARNING."""
    bgp_mon.get_all_neigh_states()
    mocked_syslog.assert_called_once()
    level, msg = mocked_syslog.call_args[0]
    assert level == syslog.LOG_WARNING
    assert "bgpd or zebra not running" in msg


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, ""))
@patch('os.system', side_effect=[0, 1])
@patch('syslog.syslog')
def test_vtysh_fail_only_zebra_down(mocked_syslog, mocked_os_system, mocked_cmd, bgp_mon):
    """When vtysh fails and only zebra is down (second pgrep fails), log WARNING."""
    bgp_mon.get_all_neigh_states()
    mocked_syslog.assert_called_once()
    level, msg = mocked_syslog.call_args[0]
    assert level == syslog.LOG_WARNING
    assert "bgpd or zebra not running" in msg


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, "some vtysh error output here"))
@patch('os.system', return_value=1)
@patch('syslog.syslog')
def test_vtysh_fail_warning_includes_output(mocked_syslog, mocked_os_system, mocked_cmd, bgp_mon):
    """WARNING message includes truncated vtysh output."""
    bgp_mon.get_all_neigh_states()
    level, msg = mocked_syslog.call_args[0]
    assert level == syslog.LOG_WARNING
    assert "some vtysh error output here" in msg


# Shaped after live 'show bgp vrf all neighbors json' output. Note that vrfId and
# vrfName sit alongside the peer entries rather than in a nested object.
APPLIED_NEIGH_JSON = {
    "default": {
        "vrfId": 0,
        "vrfName": "default",
        "11.0.0.155": {
            "remoteAs": 65300,
            "localAs": 65200,
            "bgpState": "Established",
            "peerGroup": "PG1",
            "neighborAddr": "11.0.0.155",
            "hostLocal": "11.0.0.154",
            "portLocal": 48810,
            "hostForeign": "11.0.0.155",
            "portForeign": 179
        }
    }
}


def capture_applied_ops(bgp_mon):
    """Collect what update_applied_states would write, preserving the real
    in-place clearing of the ops list that the batching logic relies on."""
    captured = []

    def _record(ops):
        captured.extend(ops)
        del ops[:]

    bgp_mon.flush_applied_pipe = _record
    return captured


def scrape(bgp_mon, payload):
    with patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(0, json.dumps(payload))):
        return bgp_mon.get_all_applied_states()


def test_applied_fields_from_live_shape(bgp_mon):
    """Every applied-config field is mapped from the neighbor-show JSON."""
    assert scrape(bgp_mon, APPLIED_NEIGH_JSON) is True
    assert bgp_mon.new_applied_state == {
        ("default", "11.0.0.155"): {
            'enabled': 'true',
            'neighbor_address': '11.0.0.155',
            'peer_as': '65300',
            'peer_group': 'PG1',
            'local_address': '11.0.0.154',
            'remote_address': '11.0.0.155'
        }
    }


def test_applied_vrf_metadata_is_not_a_peer(bgp_mon):
    """vrfId and vrfName must not be mistaken for peers."""
    assert scrape(bgp_mon, APPLIED_NEIGH_JSON) is True
    peers = [peer for _, peer in bgp_mon.new_applied_state]
    assert peers == ["11.0.0.155"]


def test_applied_enabled_true_when_admin_shutdown_absent(bgp_mon):
    """adminShutDown is emitted only when shut, so absence means enabled."""
    fields = bgp_mon.build_applied_fields({"remoteAs": 65300})
    assert fields['enabled'] == 'true'


def test_applied_enabled_false_when_admin_shutdown_set(bgp_mon):
    """adminShutDown true must invert to enabled false."""
    fields = bgp_mon.build_applied_fields({"adminShutDown": True})
    assert fields['enabled'] == 'false'


def test_applied_peer_group_absent_when_not_grouped(bgp_mon):
    """A peer outside any peer group must not get a peer_group field."""
    fields = bgp_mon.build_applied_fields({"remoteAs": 65300})
    assert 'peer_group' not in fields


def test_applied_unknown_addresses_are_dropped(bgp_mon):
    """FRR reports the literal 'Unknown' when no socket is bound."""
    fields = bgp_mon.build_applied_fields({
        "hostLocal": "Unknown",
        "hostForeign": "Unknown"
    })
    assert 'local_address' not in fields
    assert 'remote_address' not in fields


def test_applied_dynamic_peer_star_is_stripped(bgp_mon):
    """Dynamically created peers are reported with a trailing '*'."""
    fields = bgp_mon.build_applied_fields({"neighborAddr": "11.0.0.155*"})
    assert fields['neighbor_address'] == '11.0.0.155'


def test_applied_peer_as_is_stringified(bgp_mon):
    """remoteAs arrives as a JSON number but Redis fields are strings."""
    fields = bgp_mon.build_applied_fields({"remoteAs": 65300})
    assert fields['peer_as'] == '65300'


def test_applied_multiple_vrfs_are_keyed_separately(bgp_mon):
    """The same peer IP in two VRFs must produce two distinct rows."""
    payload = {
        "default": {"vrfId": 0, "vrfName": "default", "11.0.0.155": {"remoteAs": 65300}},
        "Vrf1": {"vrfId": 12, "vrfName": "Vrf1", "11.0.0.155": {"remoteAs": 65400}}
    }
    assert scrape(bgp_mon, payload) is True
    assert bgp_mon.new_applied_state[("default", "11.0.0.155")]['peer_as'] == '65300'
    assert bgp_mon.new_applied_state[("Vrf1", "11.0.0.155")]['peer_as'] == '65400'


def test_applied_first_scrape_writes_row_and_marker(bgp_mon):
    """A newly seen peer is written, along with the freshness marker."""
    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()
    keys = [key for key, _ in captured]
    assert "BGP_NEIGH_APPLIED|default|11.0.0.155" in keys
    assert bgpmon.bgpmon.APPLIED_META_KEY in keys


def test_applied_unchanged_scrape_writes_only_marker(bgp_mon):
    """An idle system must not rewrite unchanged peer rows."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()
    assert [key for key, _ in captured] == [bgpmon.bgpmon.APPLIED_META_KEY]


def test_applied_changed_value_is_rewritten(bgp_mon):
    """A changed applied value must be published even though the session state
    did not change, which is what the old gating would have missed."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()

    changed = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del changed["default"]["11.0.0.155"]["peerGroup"]

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, changed)
    bgp_mon.update_applied_states()
    written = dict(captured)
    assert "BGP_NEIGH_APPLIED|default|11.0.0.155" in written
    assert 'peer_group' not in written["BGP_NEIGH_APPLIED|default|11.0.0.155"]


def test_applied_stale_peer_is_deleted(bgp_mon):
    """A peer FRR stopped reporting must have its row removed."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, {"default": {"vrfId": 0, "vrfName": "default"}})
    bgp_mon.update_applied_states()
    assert ("BGP_NEIGH_APPLIED|default|11.0.0.155", None) in captured


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, ""))
@patch('syslog.syslog')
def test_applied_scrape_failure_returns_false(mocked_syslog, mocked_cmd, bgp_mon):
    """A failed vtysh must not be reported as a usable snapshot."""
    assert bgp_mon.get_all_applied_states() is False
    level, _ = mocked_syslog.call_args[0]
    assert level == syslog.LOG_WARNING


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(0, ""))
@patch('syslog.syslog')
def test_applied_empty_output_returns_false(mocked_syslog, mocked_cmd, bgp_mon):
    """Empty vtysh output is not an empty neighbor set."""
    assert bgp_mon.get_all_applied_states() is False


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(0, "not json at all"))
@patch('syslog.syslog')
def test_applied_bad_json_does_not_exit(mocked_syslog, mocked_cmd, bgp_mon):
    """Unparsable output must be survivable: this scrape may never take down the
    publisher of NEIGH_STATE_TABLE."""
    assert bgp_mon.get_all_applied_states() is False


@patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(0, "{}"))
@patch('syslog.syslog')
def test_applied_failed_scrape_preserves_previous_snapshot(mocked_syslog, mocked_cmd, bgp_mon):
    """A failed scrape must leave the previous snapshot intact rather than
    clearing it, so rows are not deleted on a transient vtysh error."""
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()
    with patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, "")):
        assert bgp_mon.get_all_applied_states() is False
    assert ("default", "11.0.0.155") in bgp_mon.applied_state


def test_applied_scrape_uses_vrf_all_neighbors(bgp_mon):
    """The applied values are not in summary output, so the command must be the
    vrf-all neighbors form."""
    with patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(0, "{}")) as mocked_cmd:
        bgp_mon.get_all_applied_states()
    cmd = mocked_cmd.call_args[0][0]
    assert 'show bgp vrf all neighbors json' in cmd
