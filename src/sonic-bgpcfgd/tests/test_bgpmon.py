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
            "portForeign": 179,
            "connectionsEstablished": 3,
            "connectionsDropped": 2,
            "bgpTimerUpEstablishedEpoch": 1756400000,
            "messageStats": {
                "updatesRecv": 41,
                "updatesSent": 17,
                "notificationsRecv": 1,
                "notificationsSent": 0
            },
            "addressFamilyInfo": {
                "ipv4Unicast": {
                    "peerGroupMember": "PG1",
                    "acceptedPrefixCounter": 7,
                    "sentPrefixCounter": 4
                },
                "ipv6Unicast": {
                    "peerGroupMember": "PG1",
                    "acceptedPrefixCounter": 2,
                    "sentPrefixCounter": 1
                }
            }
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


def written_keys(captured):
    """Every row the pass touched, in order."""
    return [op.key for op in captured]


def written_fields(captured):
    """The fields written per row. A row being removed maps to None."""
    return {op.key: op.fields for op in captured}


def removed_fields(captured):
    """The fields named for removal per row."""
    return {op.key: op.removed for op in captured}


def row_removed(captured, key):
    """Whether the row itself was removed, as opposed to having fields cleared."""
    return any(op.key == key and op.fields is None for op in captured)


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
    keys = written_keys(captured)
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
    assert written_keys(captured) == [bgpmon.bgpmon.APPLIED_META_KEY]


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
    written = written_fields(captured)
    assert "BGP_NEIGH_APPLIED|default|11.0.0.155" in written
    assert 'peer_group' not in written["BGP_NEIGH_APPLIED|default|11.0.0.155"]


def test_applied_change_never_removes_the_row(bgp_mon):
    """The point of the whole exercise. A value change has to reach STATE_DB as
    an update of the existing row and nothing else, because a subscriber that
    sees the row disappear first is told the neighbour went away."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()

    changed = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    changed["default"]["11.0.0.155"]["remoteAs"] = 65999

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, changed)
    bgp_mon.update_applied_states()
    key = "BGP_NEIGH_APPLIED|default|11.0.0.155"
    assert written_fields(captured)[key]['peer_as'] == '65999'
    assert not row_removed(captured, key)
    assert removed_fields(captured)[key] == ()


def test_applied_departed_field_is_named_rather_than_the_row_rewritten(bgp_mon):
    """A peer taken out of its peer group loses the field. It is named for
    removal, and the row survives."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()

    ungrouped = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del ungrouped["default"]["11.0.0.155"]["peerGroup"]

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, ungrouped)
    bgp_mon.update_applied_states()
    key = "BGP_NEIGH_APPLIED|default|11.0.0.155"
    assert removed_fields(captured)[key] == ('peer_group',)
    assert not row_removed(captured, key)


def test_applied_first_write_names_nothing_for_removal(bgp_mon):
    """A row that was never published has no previous fields to retract."""
    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()
    assert removed_fields(captured)["BGP_NEIGH_APPLIED|default|11.0.0.155"] == ()


def test_departed_fields_reports_only_what_left():
    """The helper the three update passes share."""
    assert bgpmon.bgpmon.departed_fields({'a': '1', 'b': '2'}, {'a': '9'}) == ('b',)
    assert bgpmon.bgpmon.departed_fields({'a': '1'}, {'a': '1', 'b': '2'}) == ()
    assert bgpmon.bgpmon.departed_fields(None, {'a': '1'}) == ()
    assert bgpmon.bgpmon.departed_fields({}, {'a': '1'}) == ()
    # Ordered, so the command built from it is stable to compare.
    assert bgpmon.bgpmon.departed_fields({'z': '1', 'a': '2'}, {}) == ('a', 'z')


def flush_calls(bgp_mon, ops):
    """The Redis commands one flush would issue, in order."""
    with patch('bgpmon.bgpmon.swsscommon.RedisCommand') as mock_cmd:
        bgp_mon.flush_applied_pipe(ops)
        return [name for name, _, _ in mock_cmd.return_value.method_calls]


def test_flush_updates_in_place_and_retracts_departed_fields(bgp_mon):
    """A changed row is set and then has its departed fields cleared. No DEL,
    which is what an on-change subscriber would have reported as a removal."""
    op = bgpmon.bgpmon.RowOp("BGP_NEIGH_APPLIED|default|10.0.0.1", {'peer_as': '65001'}, ('peer_group',))
    assert flush_calls(bgp_mon, [op]) == ['formatHSET', 'formatHDEL']


def test_flush_omits_the_retraction_when_nothing_departed(bgp_mon):
    """The common case costs one command, as it did before."""
    op = bgpmon.bgpmon.RowOp("BGP_NEIGH_APPLIED|default|10.0.0.1", {'peer_as': '65001'}, ())
    assert flush_calls(bgp_mon, [op]) == ['formatHSET']


def test_flush_removes_the_row_when_there_are_no_fields(bgp_mon):
    """A peer FRR stopped reporting is still a whole-row delete."""
    op = bgpmon.bgpmon.RowOp("BGP_NEIGH_APPLIED|default|10.0.0.1", None, ())
    assert flush_calls(bgp_mon, [op]) == ['formatDEL']


def test_flush_does_not_set_an_empty_field_set(bgp_mon):
    """HSET needs at least one pair, so a row left with nothing to write is
    reduced to the retraction alone."""
    op = bgpmon.bgpmon.RowOp("BGP_NEIGH_STATS|default|10.0.0.1", {}, ('last_established',))
    assert flush_calls(bgp_mon, [op]) == ['formatHDEL']


def test_flush_clears_the_ops_list(bgp_mon):
    """The batching logic relies on the list being emptied in place."""
    ops = [bgpmon.bgpmon.RowOp("T|k", {'a': '1'}, ())]
    with patch('bgpmon.bgpmon.swsscommon.RedisCommand'):
        bgp_mon.flush_applied_pipe(ops)
    assert ops == []


def test_applied_stale_peer_is_deleted(bgp_mon):
    """A peer FRR stopped reporting must have its row removed."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_applied_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, {"default": {"vrfId": 0, "vrfName": "default"}})
    bgp_mon.update_applied_states()
    assert row_removed(captured, "BGP_NEIGH_APPLIED|default|11.0.0.155")


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


def test_stats_fields_from_live_shape(bgp_mon):
    """Every counter field is mapped from the same neighbor-show JSON, so the
    counters cost no additional vtysh call."""
    assert scrape(bgp_mon, APPLIED_NEIGH_JSON) is True
    assert bgp_mon.new_stats_state == {
        ("default", "11.0.0.155"): {
            'established_transitions': '3',
            'last_established': '1756400000',
            'updates_received': '41',
            'updates_sent': '17'
        }
    }


def test_stats_last_established_absent_while_session_is_down(bgp_mon):
    """FRR emits bgpTimerUpEstablishedEpoch only while the session is
    Established, so a down session must report no established epoch rather than
    the epoch of a session that has since dropped."""
    down = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del down["default"]["11.0.0.155"]["bgpTimerUpEstablishedEpoch"]
    down["default"]["11.0.0.155"]["bgpState"] = "Idle"

    assert scrape(bgp_mon, down) is True
    fields = bgp_mon.new_stats_state[("default", "11.0.0.155")]
    assert 'last_established' not in fields
    # The counters themselves survive a session drop, since FRR keeps them.
    assert fields['established_transitions'] == '3'


def test_stats_zero_counter_is_published(bgp_mon):
    """Zero is a real counter value, not a missing one, so it must be written.
    Treating it as absent would report a brand new session as unmapped."""
    fields = bgp_mon.build_stats_fields({
        "connectionsEstablished": 0,
        "messageStats": {"updatesRecv": 0, "updatesSent": 0}
    })
    assert fields['established_transitions'] == '0'
    assert fields['updates_received'] == '0'
    assert fields['updates_sent'] == '0'


def test_stats_absent_message_stats_yields_no_update_fields(bgp_mon):
    """A peer FRR reports without messageStats leaves the UPDATE counters unset
    rather than defaulting them to zero."""
    fields = bgp_mon.build_stats_fields({"connectionsEstablished": 1})
    assert 'updates_received' not in fields
    assert 'updates_sent' not in fields
    assert fields['established_transitions'] == '1'


def test_stats_first_scrape_writes_row(bgp_mon):
    """A newly seen peer gets a counter row."""
    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_stats_states()
    assert "BGP_NEIGH_STATS|default|11.0.0.155" in written_keys(captured)


def test_stats_unchanged_scrape_writes_nothing(bgp_mon):
    """Unlike the applied table, the counter table owns no marker, so an idle
    session must produce no write at all."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_stats_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_stats_states()
    assert captured == []


def test_stats_changed_counter_is_rewritten(bgp_mon):
    """A counter that moved must be republished."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_stats_states()

    changed = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    changed["default"]["11.0.0.155"]["messageStats"]["updatesRecv"] = 42

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, changed)
    bgp_mon.update_stats_states()
    written = written_fields(captured)
    assert written["BGP_NEIGH_STATS|default|11.0.0.155"]['updates_received'] == '42'


def test_stats_row_is_republished_without_the_field_that_vanished(bgp_mon):
    """When the session drops and FRR stops reporting the established epoch, the
    field has to leave the row. An HSET on its own could not do that, so the
    field is named for removal rather than the row being rewritten."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_stats_states()

    down = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del down["default"]["11.0.0.155"]["bgpTimerUpEstablishedEpoch"]

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, down)
    bgp_mon.update_stats_states()
    key = "BGP_NEIGH_STATS|default|11.0.0.155"
    assert 'last_established' not in written_fields(captured)[key]
    assert removed_fields(captured)[key] == ('last_established',)
    assert not row_removed(captured, key)


def test_stats_stale_peer_is_deleted(bgp_mon):
    """A peer FRR stopped reporting must have its counter row removed."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_stats_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, {"default": {"vrfId": 0, "vrfName": "default"}})
    bgp_mon.update_stats_states()
    assert row_removed(captured, "BGP_NEIGH_STATS|default|11.0.0.155")


def test_stats_multiple_vrfs_are_keyed_separately(bgp_mon):
    """The scrape covers every VRF, so the counter key carries the VRF the same
    way the applied key does."""
    payload = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    payload["Vrf1"] = json.loads(json.dumps(APPLIED_NEIGH_JSON["default"]))
    payload["Vrf1"]["vrfName"] = "Vrf1"
    payload["Vrf1"]["11.0.0.155"]["messageStats"]["updatesRecv"] = 99

    assert scrape(bgp_mon, payload) is True
    assert bgp_mon.new_stats_state[("default", "11.0.0.155")]['updates_received'] == '41'
    assert bgp_mon.new_stats_state[("Vrf1", "11.0.0.155")]['updates_received'] == '99'


def test_af_stats_fields_from_live_shape(bgp_mon):
    """The prefix counters come from addressFamilyInfo in the same scrape, with
    one row per address family so a consumer can read one family without parsing
    the other's fields."""
    assert scrape(bgp_mon, APPLIED_NEIGH_JSON) is True
    assert bgp_mon.new_af_stats_state == {
        ("default", "11.0.0.155", "ipv4_unicast"): {
            'prefixes_received': '7',
            'prefixes_sent': '4'
        },
        ("default", "11.0.0.155", "ipv6_unicast"): {
            'prefixes_received': '2',
            'prefixes_sent': '1'
        }
    }


def test_af_stats_sent_absent_until_update_subgroup_forms(bgp_mon):
    """FRR guards sentPrefixCounter on the peer belonging to an outbound update
    subgroup, which it joins a moment after the session establishes. The field
    is genuinely missing in that window, so it must be left unset rather than
    reported as zero prefixes sent."""
    settling = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del settling["default"]["11.0.0.155"]["addressFamilyInfo"]["ipv4Unicast"]["sentPrefixCounter"]

    assert scrape(bgp_mon, settling) is True
    fields = bgp_mon.new_af_stats_state[("default", "11.0.0.155", "ipv4_unicast")]
    assert 'prefixes_sent' not in fields
    assert fields['prefixes_received'] == '7'


def test_af_stats_zero_prefix_count_is_published(bgp_mon):
    """Zero prefixes is a real count, and the one a session under RFC 8212
    policy reports, so it must not be mistaken for an unreported counter."""
    fields = bgp_mon.build_af_stats_fields({
        "acceptedPrefixCounter": 0,
        "sentPrefixCounter": 0
    })
    assert fields['prefixes_received'] == '0'
    assert fields['prefixes_sent'] == '0'


def test_af_stats_unmapped_family_is_ignored(bgp_mon):
    """Only the two families the model maps are published; a row no consumer
    reads would cost a write per poll for nothing."""
    payload = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    payload["default"]["11.0.0.155"]["addressFamilyInfo"]["l2VpnEvpn"] = {
        "acceptedPrefixCounter": 5
    }

    assert scrape(bgp_mon, payload) is True
    families = sorted(af for _, _, af in bgp_mon.new_af_stats_state)
    assert families == ["ipv4_unicast", "ipv6_unicast"]


def test_af_stats_family_without_counters_is_not_published(bgp_mon):
    """A family FRR reports no counter at all for yields no row, rather than an
    empty one that would claim the family was sampled."""
    payload = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    payload["default"]["11.0.0.155"]["addressFamilyInfo"]["ipv6Unicast"] = {
        "peerGroupMember": "PG1"
    }

    assert scrape(bgp_mon, payload) is True
    assert ("default", "11.0.0.155", "ipv6_unicast") not in bgp_mon.new_af_stats_state
    assert ("default", "11.0.0.155", "ipv4_unicast") in bgp_mon.new_af_stats_state


def test_af_stats_missing_address_family_info_is_tolerated(bgp_mon):
    """A peer reported without addressFamilyInfo must not fail the scrape, since
    the same scrape carries the applied values and the session counters."""
    payload = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del payload["default"]["11.0.0.155"]["addressFamilyInfo"]

    assert scrape(bgp_mon, payload) is True
    assert bgp_mon.new_af_stats_state == {}
    assert ("default", "11.0.0.155") in bgp_mon.new_applied_state


def test_af_stats_first_scrape_writes_a_row_per_family(bgp_mon):
    """The address family is part of the key, the way the afi-safi list is
    keyed."""
    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_af_stats_states()
    keys = written_keys(captured)
    assert "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv4_unicast" in keys
    assert "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv6_unicast" in keys


def test_af_stats_unchanged_scrape_writes_nothing(bgp_mon):
    """This table owns no marker either, so a session whose prefix counts are
    steady must produce no write."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_af_stats_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_af_stats_states()
    assert captured == []


def test_af_stats_changed_counter_rewrites_only_its_family(bgp_mon):
    """Keying by family is what keeps a count moving in one family from
    rewriting the other family's row."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_af_stats_states()

    changed = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    changed["default"]["11.0.0.155"]["addressFamilyInfo"]["ipv4Unicast"]["acceptedPrefixCounter"] = 9

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, changed)
    bgp_mon.update_af_stats_states()
    written = written_fields(captured)
    assert written["BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv4_unicast"]['prefixes_received'] == '9'
    assert "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv6_unicast" not in written


def test_af_stats_row_is_republished_without_the_field_that_vanished(bgp_mon):
    """When the session drops the peer leaves its update subgroup and FRR stops
    reporting the sent count, so the field has to leave the row."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_af_stats_states()

    dropped = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del dropped["default"]["11.0.0.155"]["addressFamilyInfo"]["ipv4Unicast"]["sentPrefixCounter"]

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, dropped)
    bgp_mon.update_af_stats_states()
    written = written_fields(captured)
    assert 'prefixes_sent' not in written["BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv4_unicast"]


def test_af_stats_deactivated_family_row_is_deleted(bgp_mon):
    """A family deactivated on a peer that stays up leaves the peer in the
    scrape while its family entry disappears. The row has to go, or it would
    report the prefix counts of a family no longer negotiated."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_af_stats_states()

    deactivated = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    del deactivated["default"]["11.0.0.155"]["addressFamilyInfo"]["ipv6Unicast"]

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, deactivated)
    bgp_mon.update_af_stats_states()
    assert row_removed(captured, "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv6_unicast")
    assert ("BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv4_unicast", None) not in captured


def test_af_stats_stale_peer_deletes_every_family_row(bgp_mon):
    """A peer FRR stopped reporting must take all of its family rows with it."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, APPLIED_NEIGH_JSON)
    bgp_mon.update_af_stats_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, {"default": {"vrfId": 0, "vrfName": "default"}})
    bgp_mon.update_af_stats_states()
    assert row_removed(captured, "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv4_unicast")
    assert row_removed(captured, "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv6_unicast")


def test_af_stats_multiple_vrfs_are_keyed_separately(bgp_mon):
    """The same peer IP in two VRFs, in the same family, must stay distinct."""
    payload = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    payload["Vrf1"] = json.loads(json.dumps(APPLIED_NEIGH_JSON["default"]))
    payload["Vrf1"]["vrfName"] = "Vrf1"
    payload["Vrf1"]["11.0.0.155"]["addressFamilyInfo"]["ipv4Unicast"]["acceptedPrefixCounter"] = 31

    assert scrape(bgp_mon, payload) is True
    assert bgp_mon.new_af_stats_state[("default", "11.0.0.155", "ipv4_unicast")]['prefixes_received'] == '7'
    assert bgp_mon.new_af_stats_state[("Vrf1", "11.0.0.155", "ipv4_unicast")]['prefixes_received'] == '31'
