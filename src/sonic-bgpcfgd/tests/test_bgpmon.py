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
            'remote_address': '11.0.0.155',
            'local_port': '48810',
            'remote_port': '179',
            'local_as': '65200',
            'bfd': 'false'
        }
    }


def test_applied_session_parameters(bgp_mon):
    """The link type, description, timers in effect, session MSS, multihop TTL
    and BFD are mapped from the neighbor-show JSON."""
    fields = bgp_mon.build_applied_fields({
        "remoteAs": 65300, "localAs": 65111, "nbrExternalLink": True, "nbrDesc": "core link",
        "bgpTimerHoldTimeMsecs": 90000, "bgpTimerKeepAliveIntervalMsecs": 30000,
        "connectRetryTimer": 10, "minBtwnAdvertisementRunsTimerMsecs": 0,
        "bgpTcpMssSynced": 1448, "externalBgpNbrMaxHopsAway": 5, "peerBfdInfo": {"type": "single hop"}})
    assert {k: fields[k] for k in ('local_as', 'peer_type', 'description', 'hold_time', 'keepalive_interval',
                                   'connect_retry', 'min_adv_interval', 'tcp_mss', 'ebgp_multihop_ttl', 'bfd')} == {
        'local_as': '65111', 'peer_type': 'external', 'description': 'core link', 'hold_time': '90',
        'keepalive_interval': '30', 'connect_retry': '10', 'min_adv_interval': '0', 'tcp_mss': '1448',
        'ebgp_multihop_ttl': '5', 'bfd': 'true'}


def test_applied_internal_peer_has_no_multihop_ttl(bgp_mon):
    """An iBGP peer reports its link as internal and has no eBGP TTL."""
    fields = bgp_mon.build_applied_fields({"nbrInternalLink": True, "internalBgpNbrMaxHopsAway": 255})
    assert fields['peer_type'] == 'internal'
    assert 'ebgp_multihop_ttl' not in fields


def test_applied_tcp_mss_absent_until_connected(bgp_mon):
    """FRR reports a synced MSS of 0 while no socket is connected."""
    fields = bgp_mon.build_applied_fields({"bgpTcpMssSynced": 0})
    assert 'tcp_mss' not in fields


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


def test_applied_zero_ports_are_dropped(bgp_mon):
    """FRR reports TCP port 0 when no socket is bound."""
    fields = bgp_mon.build_applied_fields({
        "portLocal": 0,
        "portForeign": 0
    })
    assert 'local_port' not in fields
    assert 'remote_port' not in fields


def test_applied_ports_are_stringified(bgp_mon):
    """portLocal/portForeign arrive as JSON numbers but Redis fields are strings."""
    fields = bgp_mon.build_applied_fields({
        "portLocal": 48810,
        "portForeign": 179
    })
    assert fields['local_port'] == '48810'
    assert fields['remote_port'] == '179'


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


def test_stats_last_notification_sent(bgp_mon):
    """A reset by a NOTIFICATION sent carries its code, subcode, direction and
    the epoch it happened at."""
    with patch('bgpmon.bgpmon.time.time', return_value=1000000.4):
        fields = bgp_mon.build_stats_fields({"lastErrorCodeSubcode": "0602", "lastResetCode": 14,
                                             "lastResetTimerMsecs": 5000})
    assert {k: fields[k] for k in ('last_notification_error_code', 'last_notification_error_subcode',
                                   'last_notification_direction', 'last_notification_time')} == {
        'last_notification_error_code': '6', 'last_notification_error_subcode': '2',
        'last_notification_direction': 'sent', 'last_notification_time': '999995'}


def test_stats_last_notification_received(bgp_mon):
    """A reset by a NOTIFICATION received is told apart by its reset code."""
    fields = bgp_mon.build_stats_fields({"lastErrorCodeSubcode": "0401", "lastResetCode": 13,
                                         "lastResetTimerMsecs": 0})
    assert fields['last_notification_direction'] == 'received'
    assert fields['last_notification_error_code'] == '4'


def test_stats_reset_without_notification_has_no_direction(bgp_mon):
    """A reset FRR did not tear down with a NOTIFICATION reports none."""
    fields = bgp_mon.build_stats_fields({"lastResetCode": 27, "lastResetTimerMsecs": 9000})
    assert not any(k.startswith('last_notification') for k in fields)


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


def test_af_stats_send_community_and_prefix_limit(bgp_mon):
    """The community types sent and the prefix limit FRR enforces are mapped
    from the address family entry."""
    fields = bgp_mon.build_af_stats_fields({"commAttriSentToNbr": "standardAndlarge", "prefixAllowedMax": 100,
                                            "prefixAllowedMaxWarning": True, "prefixAllowedWarningThresh": 80})
    assert fields == {'send_community': 'standard,large', 'max_prefixes': '100',
                      'prevent_teardown': 'true', 'warning_threshold_pct': '80'}


def test_af_stats_no_prefix_limit_and_no_communities(bgp_mon):
    """A family with no limit and no community sent reports neither."""
    fields = bgp_mon.build_af_stats_fields({"commAttriSentToNbr": "", "acceptedPrefixCounter": 0})
    assert fields == {'prefixes_received': '0'}


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


def with_route_maps(payload, family, incoming=None, outgoing=None):
    """A copy of payload with route-maps applied to one family of the peer, in
    the keys FRR reports them under."""
    payload = json.loads(json.dumps(payload))
    af_entry = payload["default"]["11.0.0.155"]["addressFamilyInfo"][family]
    if incoming is not None:
        af_entry["inboundPathPolicyConfig"] = True
        af_entry["routeMapForIncomingAdvertisements"] = incoming
    if outgoing is not None:
        af_entry["outboundPathPolicyConfig"] = True
        af_entry["routeMapForOutgoingAdvertisements"] = outgoing
    return payload


def test_af_stats_route_maps_from_live_shape(bgp_mon):
    """The route-maps FRR applies are published on the family's row, and only on
    the family they are applied to."""
    payload = with_route_maps(APPLIED_NEIGH_JSON, "ipv4Unicast", incoming="RM_IN", outgoing="RM_OUT")

    assert scrape(bgp_mon, payload) is True
    v4 = bgp_mon.new_af_stats_state[("default", "11.0.0.155", "ipv4_unicast")]
    assert v4['import_policy'] == 'RM_IN'
    assert v4['export_policy'] == 'RM_OUT'
    v6 = bgp_mon.new_af_stats_state[("default", "11.0.0.155", "ipv6_unicast")]
    assert 'import_policy' not in v6
    assert 'export_policy' not in v6


def test_af_stats_route_map_absent_direction_is_not_published(bgp_mon):
    """FRR omits the key for a direction with no route-map, which must leave the
    field out rather than publish an empty name."""
    payload = with_route_maps(APPLIED_NEIGH_JSON, "ipv4Unicast", incoming="RM_IN")

    assert scrape(bgp_mon, payload) is True
    fields = bgp_mon.new_af_stats_state[("default", "11.0.0.155", "ipv4_unicast")]
    assert fields['import_policy'] == 'RM_IN'
    assert 'export_policy' not in fields


def test_af_stats_family_with_only_route_maps_is_published(bgp_mon):
    """A family FRR reports no counter for yet still applies a route-map to has
    something to report, so it gets a row."""
    payload = json.loads(json.dumps(APPLIED_NEIGH_JSON))
    payload["default"]["11.0.0.155"]["addressFamilyInfo"]["ipv6Unicast"] = {
        "peerGroupMember": "PG1",
        "routeMapForIncomingAdvertisements": "RM_IN"
    }

    assert scrape(bgp_mon, payload) is True
    assert bgp_mon.new_af_stats_state[("default", "11.0.0.155", "ipv6_unicast")] == {
        'import_policy': 'RM_IN'
    }


def test_af_stats_route_map_change_updates_in_place(bgp_mon):
    """A route-map swapped on one direction and removed from the other is an
    update of the row that names the departed field, never a removal of the row,
    which an on-change subscriber would see as the policies going away."""
    capture_applied_ops(bgp_mon)
    scrape(bgp_mon, with_route_maps(APPLIED_NEIGH_JSON, "ipv4Unicast", incoming="RM_IN", outgoing="RM_OUT"))
    bgp_mon.update_af_stats_states()

    captured = capture_applied_ops(bgp_mon)
    scrape(bgp_mon, with_route_maps(APPLIED_NEIGH_JSON, "ipv4Unicast", incoming="RM_IN2"))
    bgp_mon.update_af_stats_states()
    key = "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv4_unicast"
    assert not row_removed(captured, key)
    assert written_fields(captured)[key]['import_policy'] == 'RM_IN2'
    assert removed_fields(captured)[key] == ('export_policy',)
    assert "BGP_NEIGH_AF_STATS|default|11.0.0.155|ipv6_unicast" not in written_fields(captured)


# 'show bgp peer-group json' as FRR 10.5 prints it: a group with a remote AS and
# a member, one with two families activated, and one with neither.
PEER_GROUP_JSON = {
    "PG1": {
        "remoteAs": 65300,
        "type": "external",
        "addressFamiliesConfigured": [],
        "members": {"11.0.0.155": {"status": "Established"}}
    },
    "PGI": {
        "remoteAs": 65200,
        "type": "internal",
        "addressFamiliesConfigured": ["IPv4 Unicast", "IPv6 Unicast"]
    },
    "PGN": {
        "type": "external",
        "addressFamiliesConfigured": []
    }
}


def scrape_peer_groups(bgp_mon, by_vrf):
    """Run the peer-group scrape with one reply per VRF, recording the calls."""
    replies = {}
    for vrf, payload in by_vrf.items():
        if vrf == "default":
            replies['show bgp peer-group json'] = payload
        else:
            replies['show bgp vrf %s peer-group json' % vrf] = payload

    def _run(cmd):
        return (0, json.dumps(replies[cmd[-1]]))

    with patch('bgpmon.bgpmon.getstatusoutput_noshell', side_effect=_run) as mocked_cmd:
        result = bgp_mon.get_all_peer_group_applied_states(list(by_vrf.keys()))
    return result, [c.args[0][-1] for c in mocked_cmd.call_args_list]


def test_peer_group_fields_from_live_shape(bgp_mon):
    """The remote AS, the type and the activated families are mapped; the
    members are not, having no OpenConfig leaf under the peer-group."""
    result, _ = scrape_peer_groups(bgp_mon, {"default": PEER_GROUP_JSON})
    assert result is True
    assert bgp_mon.new_peer_group_applied_state == {
        ("default", "PG1"): {'peer_group_name': 'PG1', 'peer_as': '65300', 'peer_type': 'external'},
        ("default", "PGI"): {'peer_group_name': 'PGI', 'peer_as': '65200', 'peer_type': 'internal'},
        ("default", "PGN"): {'peer_group_name': 'PGN'},
    }
    assert bgp_mon.new_peer_group_af_applied_state == {
        ("default", "PGI", "ipv4_unicast"): {'enabled': 'true'},
        ("default", "PGI", "ipv6_unicast"): {'enabled': 'true'},
    }


def test_peer_group_type_needs_a_remote_as(bgp_mon):
    """FRR calls a group with no remote AS external; that says nothing about the
    group, so no type is published without the AS."""
    scrape_peer_groups(bgp_mon, {"default": {"PGN": PEER_GROUP_JSON["PGN"]}})
    assert 'peer_type' not in bgp_mon.new_peer_group_applied_state[("default", "PGN")]


def test_peer_group_auto_type_is_not_published(bgp_mon):
    """FRR's auto type has no OpenConfig peer-type to report it as."""
    scrape_peer_groups(bgp_mon, {"default": {"PGA": {"remoteAs": 65400, "type": "auto"}}})
    assert bgp_mon.new_peer_group_applied_state[("default", "PGA")] == {
        'peer_group_name': 'PGA', 'peer_as': '65400'}


def test_peer_group_unmapped_family_is_not_published(bgp_mon):
    """Only the families the model maps get a row."""
    scrape_peer_groups(bgp_mon, {"default": {"PGV": {
        "remoteAs": 65400, "type": "external",
        "addressFamiliesConfigured": ["IPv4 Unicast", "L2VPN EVPN"]}}})
    assert list(bgp_mon.new_peer_group_af_applied_state) == [("default", "PGV", "ipv4_unicast")]


def test_peer_group_vrfs_are_asked_for_by_name(bgp_mon):
    """The vrf all and vrf default forms answer {}, so the default instance is
    asked for without a vrf and every other one by name, each keyed apart."""
    result, cmds = scrape_peer_groups(bgp_mon, {
        "default": {"PG1": PEER_GROUP_JSON["PG1"]},
        "Vrf_red": {"PG1": {"remoteAs": 65500, "type": "external"}}})
    assert result is True
    assert sorted(cmds) == ['show bgp peer-group json', 'show bgp vrf Vrf_red peer-group json']
    assert bgp_mon.new_peer_group_applied_state[("Vrf_red", "PG1")]['peer_as'] == '65500'
    assert bgp_mon.new_peer_group_applied_state[("default", "PG1")]['peer_as'] == '65300'


@patch('syslog.syslog')
def test_peer_group_failed_vrf_fails_the_scrape(mocked_syslog, bgp_mon):
    """One VRF failing leaves the previous snapshot alone rather than publishing
    the others as though that VRF had no groups."""
    scrape_peer_groups(bgp_mon, {"default": PEER_GROUP_JSON})
    before = dict(bgp_mon.new_peer_group_applied_state)
    with patch('bgpmon.bgpmon.getstatusoutput_noshell', side_effect=[(0, json.dumps(PEER_GROUP_JSON)), (1, "")]):
        assert bgp_mon.get_all_peer_group_applied_states(["default", "Vrf_red"]) is False
    assert bgp_mon.new_peer_group_applied_state == before


def test_peer_group_first_pass_writes_rows_and_marker(bgp_mon):
    captured = capture_applied_ops(bgp_mon)
    scrape_peer_groups(bgp_mon, {"default": PEER_GROUP_JSON})
    bgp_mon.update_peer_group_applied_states()
    keys = written_keys(captured)
    assert "BGP_PEER_GROUP_APPLIED|default|PG1" in keys
    assert "BGP_PEER_GROUP_AF_APPLIED|default|PGI|ipv4_unicast" in keys
    assert keys[-1] == "BGP_PEER_GROUP_APPLIED_META|global"


def test_peer_group_unchanged_pass_writes_only_marker(bgp_mon):
    capture_applied_ops(bgp_mon)
    scrape_peer_groups(bgp_mon, {"default": PEER_GROUP_JSON})
    bgp_mon.update_peer_group_applied_states()
    captured = capture_applied_ops(bgp_mon)
    scrape_peer_groups(bgp_mon, {"default": PEER_GROUP_JSON})
    bgp_mon.update_peer_group_applied_states()
    assert written_keys(captured) == ["BGP_PEER_GROUP_APPLIED_META|global"]


def test_peer_group_change_updates_in_place(bgp_mon):
    """A group losing its remote AS keeps its row and names the fields that
    went, and a family deactivated loses its own row only."""
    capture_applied_ops(bgp_mon)
    scrape_peer_groups(bgp_mon, {"default": PEER_GROUP_JSON})
    bgp_mon.update_peer_group_applied_states()

    changed = json.loads(json.dumps(PEER_GROUP_JSON))
    changed["PG1"] = {"type": "external", "addressFamiliesConfigured": []}
    changed["PGI"]["addressFamiliesConfigured"] = ["IPv4 Unicast"]
    captured = capture_applied_ops(bgp_mon)
    scrape_peer_groups(bgp_mon, {"default": changed})
    bgp_mon.update_peer_group_applied_states()

    key = "BGP_PEER_GROUP_APPLIED|default|PG1"
    assert not row_removed(captured, key)
    assert removed_fields(captured)[key] == ('peer_as', 'peer_type')
    assert row_removed(captured, "BGP_PEER_GROUP_AF_APPLIED|default|PGI|ipv6_unicast")
    assert "BGP_PEER_GROUP_AF_APPLIED|default|PGI|ipv4_unicast" not in written_keys(captured)


def test_peer_group_removed_group_loses_its_rows(bgp_mon):
    capture_applied_ops(bgp_mon)
    scrape_peer_groups(bgp_mon, {"default": PEER_GROUP_JSON})
    bgp_mon.update_peer_group_applied_states()
    captured = capture_applied_ops(bgp_mon)
    scrape_peer_groups(bgp_mon, {"default": {"PG1": PEER_GROUP_JSON["PG1"]}})
    bgp_mon.update_peer_group_applied_states()
    assert row_removed(captured, "BGP_PEER_GROUP_APPLIED|default|PGI")
    assert row_removed(captured, "BGP_PEER_GROUP_AF_APPLIED|default|PGI|ipv4_unicast")
    assert row_removed(captured, "BGP_PEER_GROUP_APPLIED|default|PGN")
    assert not row_removed(captured, "BGP_PEER_GROUP_APPLIED|default|PG1")


# The next-hop set clauses as FRR renders them in 'show route-map json', and
# the CONFIG_DB fields they come from.
@pytest.mark.parametrize("clause,fields", [
    ("ip next-hop 10.10.10.1", {"set_next_hop": "10.10.10.1"}),
    # frrcfgd renders CONFIG_DB's 'self' as peer-address, so it is reported
    # back in the CONFIG_DB spelling.
    ("ip next-hop peer-address", {"set_next_hop": "self"}),
    ("ipv6 next-hop global 2001:db8::1", {"set_ipv6_next_hop_global": "2001:db8::1"}),
    ("ipv6 next-hop prefer-global", {"set_ipv6_next_hop_prefer_global": "true"}),
])
def test_route_map_next_hop_set_clauses(clause, fields):
    assert bgpmon.bgpmon.parse_route_map_set(clause) == fields



# The match clauses as FRR renders them, and the CONFIG_DB fields they come from.
@pytest.mark.parametrize("clause,field", [
    ("as-path AS-SET-A", ("match_as_path", "AS-SET-A")),
    ("interface Ethernet0", ("match_interface", "Ethernet0")),
])
def test_route_map_match_clauses(clause, field):
    assert bgpmon.bgpmon.parse_route_map_match(clause) == field


def test_route_map_rule_ext_community_call_and_source_protocol(bgp_mon):
    """Route targets and sites of origin join in the CONFIG_DB spelling, the
    called route-map and the source protocol are published, and only bgpd's
    view is read."""
    rule = {"sequenceNumber": 10, "type": "permit", "matchClauses": ["as-path AS-SET-A", "source-protocol bgp"],
            "setClauses": ["extcommunity rt 1:1 2:2", "extcommunity soo 3:3"], "callClause": "RM-CALLED"}
    zebra_rule = {"sequenceNumber": 10, "type": "permit", "matchClauses": ["source-protocol bgp"], "setClauses": []}
    output = json.dumps({"bgpd": {"RM1": {"invoked": 0, "rules": [rule]}},
                         "zebra": {"RM2": {"rules": [zebra_rule]}}})
    with patch("bgpmon.bgpmon.getstatusoutput_noshell", return_value=(0, output)):
        assert bgp_mon.get_all_route_map_states()
    fields = bgp_mon.new_route_map_state[("RM1", "10")]
    assert fields["set_ext_community_inline"] == "route-target:1:1,route-target:2:2,route-origin:3:3"
    assert fields["call_route_map"] == "RM-CALLED"
    assert fields["match_as_path"] == "AS-SET-A"
    assert fields["match_protocol"] == "bgp"
    assert ("RM2", "10") not in bgp_mon.new_route_map_state

# A bgpd running configuration in the shape FRR writes it, with an aggregate
# in each family of the default instance and one in a named VRF.
AGGREGATE_CONFIG = """Building configuration...

Current configuration:
!
frr version 10.5.4
frr defaults traditional
!
router bgp 65200
 bgp router-id 11.0.0.64
 neighbor 11.0.0.155 remote-as 65300
 !
 address-family ipv4 unicast
  aggregate-address 10.10.0.0/16
  aggregate-address 10.20.0.0/16 as-set summary-only route-map AGG-V4
  neighbor 11.0.0.155 activate
 exit-address-family
 !
 address-family ipv6 unicast
  aggregate-address 2001:db8::/32 summary-only route-map AGG-V6 origin igp
 exit-address-family
exit
!
router bgp 65200 vrf Vrf_red
 !
 address-family ipv4 unicast
  aggregate-address 10.30.0.0/16 route-map AGG-RED
 exit-address-family
exit
!
end
"""


def scrape_aggregates(bgp_mon, config):
    with patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(0, config)) as mocked_cmd:
        result = bgp_mon.get_all_aggregate_applied_states()
    return result, [c.args[0][-1] for c in mocked_cmd.call_args_list]


def test_aggregate_fields_from_running_config(bgp_mon):
    result, cmds = scrape_aggregates(bgp_mon, AGGREGATE_CONFIG)
    assert result is True
    assert cmds == ['show running-config bgpd']
    assert bgp_mon.new_aggregate_applied_state == {
        ("default", "ipv4_unicast", "10.10.0.0/16"): {'as_set': 'false', 'summary_only': 'false'},
        ("default", "ipv4_unicast", "10.20.0.0/16"): {'as_set': 'true', 'summary_only': 'true', 'policy': 'AGG-V4'},
        ("default", "ipv6_unicast", "2001:db8::/32"): {'as_set': 'false', 'summary_only': 'true', 'policy': 'AGG-V6'},
        ("Vrf_red", "ipv4_unicast", "10.30.0.0/16"): {'as_set': 'false', 'summary_only': 'false', 'policy': 'AGG-RED'},
    }


def test_aggregate_outside_a_mapped_family_is_ignored(bgp_mon):
    config = "router bgp 65200\n address-family l2vpn evpn\n  aggregate-address 10.1.0.0/16\n exit-address-family\nexit\n"
    scrape_aggregates(bgp_mon, config)
    assert bgp_mon.new_aggregate_applied_state == {}


@patch('syslog.syslog')
def test_aggregate_failed_scrape_preserves_previous_snapshot(mocked_syslog, bgp_mon):
    scrape_aggregates(bgp_mon, AGGREGATE_CONFIG)
    before = dict(bgp_mon.new_aggregate_applied_state)
    with patch('bgpmon.bgpmon.getstatusoutput_noshell', return_value=(1, "")):
        assert bgp_mon.get_all_aggregate_applied_states() is False
    assert bgp_mon.new_aggregate_applied_state == before


def test_aggregate_first_pass_writes_rows_and_marker(bgp_mon):
    captured = capture_applied_ops(bgp_mon)
    scrape_aggregates(bgp_mon, AGGREGATE_CONFIG)
    bgp_mon.update_aggregate_applied_states()
    keys = written_keys(captured)
    assert "BGP_GLOBALS_AF_AGGREGATE_ADDR_APPLIED|default|ipv4_unicast|10.10.0.0/16" in keys
    assert "BGP_GLOBALS_AF_AGGREGATE_ADDR_APPLIED|Vrf_red|ipv4_unicast|10.30.0.0/16" in keys
    assert keys[-1] == "BGP_GLOBALS_AF_AGGREGATE_ADDR_APPLIED_META|global"


def test_aggregate_unchanged_pass_writes_only_marker(bgp_mon):
    capture_applied_ops(bgp_mon)
    scrape_aggregates(bgp_mon, AGGREGATE_CONFIG)
    bgp_mon.update_aggregate_applied_states()
    captured = capture_applied_ops(bgp_mon)
    scrape_aggregates(bgp_mon, AGGREGATE_CONFIG)
    bgp_mon.update_aggregate_applied_states()
    assert written_keys(captured) == ["BGP_GLOBALS_AF_AGGREGATE_ADDR_APPLIED_META|global"]


def test_aggregate_policy_removed_updates_in_place_and_removed_aggregate_goes(bgp_mon):
    capture_applied_ops(bgp_mon)
    scrape_aggregates(bgp_mon, AGGREGATE_CONFIG)
    bgp_mon.update_aggregate_applied_states()

    changed = AGGREGATE_CONFIG.replace(" route-map AGG-V4", "").replace("  aggregate-address 10.10.0.0/16\n", "")
    captured = capture_applied_ops(bgp_mon)
    scrape_aggregates(bgp_mon, changed)
    bgp_mon.update_aggregate_applied_states()

    key = "BGP_GLOBALS_AF_AGGREGATE_ADDR_APPLIED|default|ipv4_unicast|10.20.0.0/16"
    assert not row_removed(captured, key)
    assert removed_fields(captured)[key] == ('policy',)
    assert row_removed(captured, "BGP_GLOBALS_AF_AGGREGATE_ADDR_APPLIED|default|ipv4_unicast|10.10.0.0/16")
