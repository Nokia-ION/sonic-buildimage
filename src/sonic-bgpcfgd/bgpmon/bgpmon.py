#!/usr/bin/env python3

""""
Description: bgpmon.py -- populating bgp related information in stateDB.
    script is started by supervisord in bgp docker when the docker is started.

    Initial creation of this daemon is to assist SNMP agent in obtaining the
    BGP related information for its MIB support. The MIB that this daemon is
    assisting is for the CiscoBgp4MIB (Neighbor state only). Also for chassis use-case
    it identify if the given BGP neighbors as i-BGP vs e-BGP. If there are other
    BGP related items that needs to be updated in a periodic manner in the
    future, then more can be added into this process.

    The script check if there are any bgp activities by monitoring the bgp
    frr.log file timestamp.  If activity is detected, then it will request bgp
    neighbor state via vtysh cli interface. This bgp activity monitoring is
    done periodically (every 15 second). When triggered, it looks specifically
    for the neighbor state in the json output of show ip bgp neighbors json
    and update the state DB for each neighbor accordingly.
    In order to not disturb and hold on to the State DB access too long and
    removal of the stale neighbors (neighbors that was there previously on
    previous get request but no longer there in the current get request), a
    "previous" neighbor dictionary will be kept and used to determine if there
    is a need to perform update or the peer is stale to be removed from the
    state DB

    In addition to the neighbor state above, this daemon publishes the BGP
    neighbor configuration as FRR actually applied it into
    BGP_NEIGH_APPLIED|<vrf>|<peer>. Those values are sourced from
    'show bgp vrf all neighbors json', which is a different command from the
    summary used for NEIGH_STATE_TABLE, because none of the applied
    configuration is present in summary output. Reporting FRR's own view is the
    point: it confirms that configuration written to CONFIG_DB was accepted by
    the daemon, which reading CONFIG_DB back cannot show.
"""
import json
import os
import sys
import syslog
from collections import namedtuple
from swsscommon import swsscommon
import time
from sonic_py_common.general import getstatusoutput_noshell

PIPE_BATCH_MAX_COUNT = 50

# One row's worth of work for the applied-config pipeline. 'fields' is the set
# to write, or None to remove the row outright; 'removed' names the fields that
# were published last time and have since gone away.
#
# The departed fields are carried explicitly because the alternative, rewriting
# a changed row as a delete followed by a set, leaves the row briefly absent.
# That is invisible to a GET but not to an on-change subscriber, which sees a
# delete before every update, and it is what kept these tables on sampling.
RowOp = namedtuple('RowOp', ['key', 'fields', 'removed'])


# Fields that were published for a row last time and are not in the new set.
# Ordered so that the command a caller builds from it is stable to compare.
def departed_fields(previous, fields):
    if not previous:
        return ()
    return tuple(sorted(set(previous) - set(fields)))


APPLIED_TABLE = "BGP_NEIGH_APPLIED"
# Single row carrying the time of the last successful applied-config scrape. A
# consumer needs it to tell "FRR has no value for this leaf" from "the producer
# has not sampled yet"; without it an absent field is ambiguous and a config
# acceptance check would report false rejections. Kept as one global row rather
# than a per-peer timestamp so the write cost stays constant per poll.
APPLIED_META_KEY = "BGP_NEIGH_APPLIED_META|global"
# FRR emits this literal for the local or peer address when no socket is bound.
UNKNOWN_ADDR = "Unknown"

# Applied route-map configuration, read from FRR rather than from CONFIG_DB.
# The field names deliberately match the CONFIG_DB ROUTE_MAP ones so a consumer
# decodes the applied state exactly as it decodes the configured state, from a
# different table.
ROUTE_MAP_TABLE = "ROUTE_MAP_APPLIED"
# A map with no rules is still a map FRR holds, and an empty one is an implicit
# deny for anything referencing it, so it gets a row of its own rather than
# vanishing from the applied view.
ROUTE_MAP_SET_TABLE = "ROUTE_MAP_SET_APPLIED"
# Its own freshness marker rather than APPLIED_META_KEY: this comes from a
# separate vtysh call, so one marker could not honestly vouch for both.
ROUTE_MAP_META_KEY = "ROUTE_MAP_APPLIED_META|global"

# Applied static routes, read from FRR rather than from CONFIG_DB. The field
# names match the CONFIG_DB STATIC_ROUTE ones and the key is spelled
# <vrf>|<prefix> exactly as that table spells it, so a consumer decodes the
# applied state the way it decodes the configured state, from another table.
STATIC_ROUTE_TABLE = "STATIC_ROUTE_APPLIED"
# Its own marker again, for the same reason: its own vtysh calls.
STATIC_ROUTE_META_KEY = "STATIC_ROUTE_APPLIED_META|global"

# Applied BGP global state per VRF, from the FRR summary rather than CONFIG_DB.
GLOBALS_APPLIED_TABLE = "BGP_GLOBALS_APPLIED"
GLOBALS_APPLIED_META_KEY = "BGP_GLOBALS_APPLIED_META|global"

# Applied peer-groups, keyed <vrf>|<peer-group> as BGP_PEER_GROUP is, with one
# row per activated address family keyed <vrf>|<peer-group>|<afi-safi> as
# BGP_PEER_GROUP_AF is. FRR reports peer-groups only through 'show bgp
# peer-group json', which carries the remote AS, the type and the activated
# families and nothing else, so that is all these rows hold.
PEER_GROUP_APPLIED_TABLE = "BGP_PEER_GROUP_APPLIED"
PEER_GROUP_AF_APPLIED_TABLE = "BGP_PEER_GROUP_AF_APPLIED"
# Its own marker, for its own vtysh calls.
PEER_GROUP_APPLIED_META_KEY = "BGP_PEER_GROUP_APPLIED_META|global"
# The families as FRR prints them in addressFamiliesConfigured, mapped onto the
# BGP_PEER_GROUP_AF spelling. Only the two families the OpenConfig model maps
# are published.
PEER_GROUP_AF_NAMES = {
    'IPv4 Unicast': 'ipv4_unicast',
    'IPv6 Unicast': 'ipv6_unicast',
}
# FRR's type for a group with a remote AS. "auto" has no OpenConfig peer-type.
PEER_GROUP_TYPES = ('internal', 'external')


def peer_group_applied_fields(name, group):
    """Map one group of 'show bgp peer-group json' onto its applied row.

    FRR reports the type even for a group with no remote AS, where it is
    derived from an AS of zero and reads "external" whatever the group will
    become, so the type is published only beside the AS it describes.
    """
    fields = {'peer_group_name': name}
    remote_as = group.get('remoteAs')
    if remote_as is not None:
        fields['peer_as'] = str(remote_as)
        peer_type = group.get('type')
        if peer_type in PEER_GROUP_TYPES:
            fields['peer_type'] = peer_type
    return fields


def peer_group_applied_families(group):
    """The activated families of one group, in the BGP_PEER_GROUP_AF spelling."""
    families = group.get('addressFamiliesConfigured')
    if not isinstance(families, list):
        return []
    return sorted(PEER_GROUP_AF_NAMES[f] for f in families if f in PEER_GROUP_AF_NAMES)


def parse_route_map_match(clause):
    """One rendered match clause to a (field, value) pair, or None.

    FRR reports clauses as the text it would print in its configuration, so
    these are parsed rather than read out of a structure.
    """
    parts = clause.split()
    if len(parts) >= 4 and parts[0] == "ip" and parts[1] == "address" and parts[2] == "prefix-list":
        return ("match_prefix_set", parts[3])
    if len(parts) >= 4 and parts[0] == "ipv6" and parts[1] == "address" and parts[2] == "prefix-list":
        return ("match_ipv6_prefix_set", parts[3])
    if len(parts) == 2:
        simple = {"metric": "match_med", "origin": "match_origin",
                  "community": "match_community", "extcommunity": "match_ext_community"}
        if parts[0] in simple:
            return (simple[parts[0]], parts[1])
        if parts[0] == "local-preference":
            return ("match_local_pref", parts[1])
    return None


def parse_route_map_set(clause):
    """One rendered set clause to a dict of fields, possibly empty.

    Two forms need more than a split. 'as-path prepend A A A' repeats the ASN,
    and the repeat count is what set_repeat_asn holds. 'metric' carries the
    action in the sign: a bare number is the plain spelling, while +N and -N are
    the add and subtract actions that live in a separate field.
    """
    parts = clause.split()
    if not parts:
        return {}
    if parts[0] == "origin" and len(parts) == 2:
        return {"set_origin": parts[1]}
    if parts[0] == "local-preference" and len(parts) == 2:
        return {"set_local_pref": parts[1]}
    if parts[0] == "ip" and len(parts) == 3 and parts[1] == "next-hop":
        # CONFIG_DB spells the self next-hop 'self', which frrcfgd renders as
        # FRR's peer-address.
        if parts[2] == "peer-address":
            return {"set_next_hop": "self"}
        return {"set_next_hop": parts[2]}
    if parts[0] == "ipv6" and len(parts) == 4 and parts[1] == "next-hop" and parts[2] == "global":
        return {"set_ipv6_next_hop_global": parts[3]}
    if parts[0] == "ipv6" and len(parts) == 3 and parts[1] == "next-hop" and parts[2] == "prefer-global":
        return {"set_ipv6_next_hop_prefer_global": "true"}
    if parts[0] == "metric" and len(parts) == 2:
        v = parts[1]
        if v.startswith("+"):
            return {"set_metric": v[1:], "set_metric_action": "METRIC_ADD_VALUE"}
        if v.startswith("-"):
            return {"set_metric": v[1:], "set_metric_action": "METRIC_SUBTRACT_VALUE"}
        return {"set_med": v}
    if parts[0] == "as-path" and len(parts) >= 3 and parts[1] == "prepend":
        asns = parts[2:]
        return {"set_asn": asns[0], "set_repeat_asn": str(len(asns))}
    if parts[0] == "community" and len(parts) >= 2:
        # Stored the way CONFIG_DB stores it, marker included: the additive
        # marker is how the ADD option is carried, there being no field for it.
        return {"set_community_inline": " ".join(parts[1:])}
    return {}


def route_map_rule_fields(rule):
    """Every published field for one rendered rule."""
    fields = {"route_operation": rule.get("type", "")}
    for clause in rule.get("matchClauses", []):
        parsed = parse_route_map_match(clause)
        if parsed:
            fields[parsed[0]] = parsed[1]
    for clause in rule.get("setClauses", []):
        fields.update(parse_route_map_set(clause))
    # No OpenConfig leaf can carry these yet, but they cost nothing to publish
    # and they are the only genuinely operational thing FRR reports here.
    fields["invoked"] = str(rule.get("invoked", 0))
    fields["cpu_time_ms"] = str(rule.get("cpuTimeMS", 0))
    return fields

def static_route_fields(entry):
    """Every published field for one route FRR reports as static.

    nexthop and ifname are comma joined in the order FRR lists the next-hops,
    which is how CONFIG_DB spells a multipath route, so one route is one row
    here as it is there. A next-hop that names only an interface, or only a
    blackhole, still takes its position in both lists, because a consumer pairs
    them by position.
    """
    ips = []
    ifnames = []
    blackhole = False
    for nexthop in entry.get("nexthops", []):
        if nexthop.get("blackhole"):
            blackhole = True
        ips.append(nexthop.get("ip", ""))
        ifnames.append(nexthop.get("interfaceName", ""))

    fields = {
        "nexthop": ",".join(ips),
        "ifname": ",".join(ifnames),
        "blackhole": "true" if blackhole else "false",
        "distance": str(entry.get("distance", "")),
    }
    # FRR carries one tag for the whole route rather than one per next-hop, so
    # this is a single value where the CONFIG_DB field can be a list.
    if entry.get("tag") is not None:
        fields["tag"] = str(entry["tag"])
    # No OpenConfig leaf carries these yet. They are published because they are
    # what makes this an applied view rather than a copy of the configuration:
    # a route FRR holds but has not installed is exactly what a state path
    # should be able to report.
    fields["installed"] = "true" if entry.get("installed") else "false"
    fields["selected"] = "true" if entry.get("selected") else "false"
    fields["metric"] = str(entry.get("metric", 0))
    return fields


# Per-session counters, keyed the same way as the applied table because they come
# from the same 'show bgp vrf all neighbors json' scrape, which covers every VRF.
# They share APPLIED_META_KEY as their freshness marker rather than carrying one
# of their own: a single scrape produces both tables, so one marker describes
# both, and two markers could disagree only by being wrong.
#
# last_established holds FRR's epoch in seconds, exactly as FRR reports it. The
# OpenConfig leaf is in nanoseconds, but converting here would invent precision
# FRR does not have; the multiplication belongs with the rest of the model
# translation in translib.
STATS_TABLE = "BGP_NEIGH_STATS"

# Per-address-family prefix counters and applied route-maps. FRR reports them
# per address family under addressFamilyInfo, so the address family joins the
# key rather than being folded into a field name, which keeps a row per (peer,
# address family) the way the
# OpenConfig afi-safi list is keyed and lets a consumer read one family without
# parsing the other's fields. Shares APPLIED_META_KEY for the same reason
# STATS_TABLE does: one scrape produces all three tables.
AF_STATS_TABLE = "BGP_NEIGH_AF_STATS"
# FRR names the address families in camel case in its neighbor output, while
# SONiC keys them the way BGP_NEIGHBOR_AF already does. Only the two families the
# OpenConfig model maps are published; a family no consumer reads would cost a
# write per poll for nothing.
AF_STATS_NAMES = {
    'ipv4Unicast': 'ipv4_unicast',
    'ipv6Unicast': 'ipv6_unicast',
}


# The field carrying the negotiated capabilities. The '@' is SONiC's mark for a
# field holding a list.
APPLIED_CAPS_FIELD = "supported_capabilities@"

# How FRR spells a capability both ends agreed on, as against one that was only
# advertised or only received.
CAP_NEGOTIATED = "advertisedAndReceived"

# The OpenConfig capability identities, each with the key in FRR's
# neighborCapabilities that evidences it.
#
# openconfig-bgp-types names six and no more, so a capability outside them
# cannot be reported however it is negotiated: extendedMessage,
# enhancedRouteRefresh, longLivedGracefulRestart, pathsLimit, hostName,
# softwareVersion and linkLocalNextHop all appear in FRR's report and none has
# an identity to be named by. They are dropped here rather than in the
# consumer, so that the field only ever holds names the model can express.
CAP_SCALAR_IDENTITIES = (
    ("routeRefresh", "ROUTE_REFRESH"),
    ("4byteAs", "ASN32"),
    ("gracefulRestart", "GRACEFUL_RESTART"),
    ("extendedNexthop", "EXTENDED_NEXTHOP_ENCODING"),
)

# The two FRR reports per address family, as an object each, with the same
# advertised-versus-negotiated distinction expressed as a flag. addPath spells
# its flag differently because it tracks the receive direction separately.
CAP_PER_FAMILY_IDENTITIES = (
    ("multiprotocolExtensions", "MPBGP", CAP_NEGOTIATED),
    ("addPath", "ADD_PATHS", "rxAdvertisedAndReceived"),
)


def negotiated_capabilities(caps):
    """The capability identities this session negotiated, sorted so that the
    field is stable to compare between scrapes."""
    if not isinstance(caps, dict):
        return []
    found = set()
    for key, identity in CAP_SCALAR_IDENTITIES:
        if caps.get(key) == CAP_NEGOTIATED:
            found.add(identity)
    for key, identity, flag in CAP_PER_FAMILY_IDENTITIES:
        families = caps.get(key)
        if not isinstance(families, dict):
            continue
        for family in families.values():
            # One family is enough: the identity is not per family in
            # OpenConfig, so it describes the session rather than a family.
            if isinstance(family, dict) and family.get(flag):
                found.add(identity)
                break
    return sorted(found)


class BgpStateGet:
    def __init__(self):
        # set peer_l stores the Neighbor peer Ip address
        # dic peer_state stores the Neighbor peer state entries
        # set new_peer_l stores the new snapshot of Neighbor peer ip address
        # dic new_peer_state stores the new snapshot of Neighbor peer states
        self.peer_l = set()
        self.peer_state = {}
        self.new_peer_l = set()
        self.new_peer_state = {}
        # dic applied_state maps (vrf, peer) to the field dictionary last
        # published to BGP_NEIGH_APPLIED, so a write can be skipped when none of
        # the applied values changed
        self.applied_state = {}
        self.new_applied_state = {}
        # dic stats_state maps (vrf, peer) to the counter fields last published
        # to BGP_NEIGH_STATS, on the same change-gated write as applied_state
        self.stats_state = {}
        self.new_stats_state = {}
        # dic af_stats_state maps (vrf, peer, afi_safi) to the prefix counter
        # and route-map fields last published to BGP_NEIGH_AF_STATS, change
        # gated the same way
        self.route_map_state = {}
        self.new_route_map_state = {}
        self.route_map_set_state = {}
        self.new_route_map_set_state = {}
        self.static_route_state = {}
        self.new_static_route_state = {}
        self.af_stats_state = {}
        self.new_af_stats_state = {}
        self.globals_applied_state = {}
        self.new_globals_applied_state = {}
        # dic peer_group_applied_state maps (vrf, peer-group) and
        # peer_group_af_applied_state maps (vrf, peer-group, afi_safi) to the
        # fields last published, change gated like applied_state
        self.peer_group_applied_state = {}
        self.new_peer_group_applied_state = {}
        self.peer_group_af_applied_state = {}
        self.new_peer_group_af_applied_state = {}
        self.cached_timestamp = 0
        self.db = swsscommon.SonicV2Connector(use_unix_socket_path=True)
        self.db.connect(self.db.STATE_DB, False)
        self.pipe = swsscommon.RedisPipeline(self.db.get_redis_client(self.db.STATE_DB))
        self.db.delete_all_by_pattern(self.db.STATE_DB, "NEIGH_STATE_TABLE|*" )
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % APPLIED_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % STATS_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % AF_STATS_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, APPLIED_META_KEY)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % ROUTE_MAP_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % ROUTE_MAP_SET_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, ROUTE_MAP_META_KEY)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % STATIC_ROUTE_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, STATIC_ROUTE_META_KEY)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % GLOBALS_APPLIED_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, GLOBALS_APPLIED_META_KEY)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % PEER_GROUP_APPLIED_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, "%s|*" % PEER_GROUP_AF_APPLIED_TABLE)
        self.db.delete_all_by_pattern(self.db.STATE_DB, PEER_GROUP_APPLIED_META_KEY)
        self.MAX_RETRY_ATTEMPTS = 3

    # A quick way to check if there are anything happening within BGP is to
    # check its log file has any activities. This is by checking its modified
    # timestamp against the cached timestamp that we keep and if there is a
    # difference, there is activity detected. In case the log file got wiped
    # out, it will default back to constant pulling every 15 seconds
    def bgp_activity_detected(self):
        try:
            timestamp = os.stat("/var/log/frr/frr.log").st_mtime
            if timestamp != self.cached_timestamp:
                self.cached_timestamp = timestamp
                return True
            else:
                return False
        except (IOError, OSError):
            return True

    def update_new_peer_states(self, peer_dict):
        peer_l = peer_dict["peers"].keys()
        self.new_peer_l.update(peer_l)
        for peer in peer_l:
            self.new_peer_state[peer] = (peer_dict["peers"][peer]["state"], 
                                         peer_dict["peers"][peer]["remoteAs"],
                                         peer_dict["peers"][peer]["localAs"])

    # Get a new snapshot of BGP neighbors and store them in the "new" location
    def get_all_neigh_states(self):
        cmd = ["vtysh", "-H", "/dev/null",  "-c", 'show bgp summary json']
        retry_attempt = 0

        while retry_attempt < self.MAX_RETRY_ATTEMPTS:
            try:
                rc, output = getstatusoutput_noshell(cmd)
                if rc:
                    if os.system('pgrep -x bgpd > /dev/null 2>&1') != 0 or os.system('pgrep -x zebra > /dev/null 2>&1') != 0:
                        syslog.syslog(syslog.LOG_WARNING, "vtysh failed (rc={}) when execute: {} Output: {} bgpd or zebra not running — container may be shutting down".format(rc, cmd, output[:200] if output else ""))
                    else:
                        syslog.syslog(syslog.LOG_ERR, "*ERROR* Failed with rc:{} when execute: {}".format(rc, cmd))
                    return
                if len(output) == 0:
                    syslog.syslog(syslog.LOG_WARNING, "*WARNING* output none when execute: {}".format(cmd))
                    return

                peer_info = json.loads(output)
                # cmd ran successfully, safe to Clean the "new" set/dict for new snapshot
                self.new_peer_l.clear()
                self.new_peer_state.clear()
                for key, value in peer_info.items():
                    if key == "ipv4Unicast" or key == "ipv6Unicast":
                        self.update_new_peer_states(value)
                return

            except json.JSONDecodeError as decode_error:
                # Log the exception and retry if within the maximum attempts
                retry_attempt += 1
                syslog.syslog(syslog.LOG_WARNING, "*WARNING* JSONDecodeError: {} when execute: {} Retry attempt: {}".format(decode_error, cmd, retry_attempt))
                time.sleep(1)
                continue
            except Exception as e:
                # Log other exceptions and return failure
                retry_attempt += 1
                syslog.syslog(syslog.LOG_WARNING, "*WARNING* An unexpected error occurred: {} when execute: {} Retry attempt: {}".format(e, cmd, retry_attempt))
                time.sleep(1)
                continue

        # Log an error if the maximum retry attempts are reached
        syslog.syslog(syslog.LOG_ERR, "*ERROR* Maximum retry attempts reached. Failed to execute: {} Output: {}".format(cmd, output))
        sys.exit(1)


    # This method will take the caller's dictionary which contains the peer state operation
    # That need to be updated in StateDB using Redis pipeline.
    # The data{} will be cleared at the end of this method before returning to caller.
    def flush_pipe(self, data):
        """Dump each entry in data{} into State DB via redis pipeline.
        Args:
            data: Neighbor state in dictionary format
            {
                'NEIGH_STATE_TABLE|ip_address_a': {'state':state},
                'NEIGH_STATE_TABLE|ip_address_b': {'state':state},
                'NEIGH_STATE_TABLE|ip_address_c': {'state':state},
                'NEIGH_STATE_TABLE|ip_address_x': None,
                'NEIGH_STATE_TABLE|ip_address_z': None
                ...
            }
        """
        for key, value in data.items():
            if value is None:
                # delete case
                command = swsscommon.RedisCommand()
                command.formatDEL(key)
                self.pipe.push(command)
            else:
                # Add or Modify case
                command = swsscommon.RedisCommand()
                command.formatHSET(key, value)
                self.pipe.push(command)

        self.pipe.flush()
        data.clear()

    def update_neigh_states(self):
        data = {}
        for peer in self.new_peer_l:
            key = "NEIGH_STATE_TABLE|%s" % peer
            if peer in self.peer_l:
                # only update the entry if state changed
                if self.peer_state[peer] != self.new_peer_state[peer][0]:
                    # state changed. Update state DB for this entry
                    state = self.new_peer_state[peer][0]
                    peerType = "i-BGP" if self.new_peer_state[peer][1] == self.new_peer_state[peer][2] else "e-BGP"
                    data[key] = {'state':state, 'peerType':peerType}
                    self.peer_state[peer] = state
                # remove this neighbor from old set since it is accounted for
                self.peer_l.remove(peer)
            else:
                # New neighbor found case. Add to dictionary and state DB
                state = self.new_peer_state[peer][0]
                peerType = "i-BGP" if self.new_peer_state[peer][1] == self.new_peer_state[peer][2] else "e-BGP"
                data[key] = {'state':state, 'peerType':peerType}
                self.peer_state[peer] = state
            if len(data) > PIPE_BATCH_MAX_COUNT:
                self.flush_pipe(data)
        # Check for stale state entries to be cleaned up
        for peer in self.peer_l:
            # remove this from the stateDB and the current neighbor state entry
            del_key = "NEIGH_STATE_TABLE|%s" % peer
            data[del_key] = None
            if peer in self.peer_state:
                del self.peer_state[peer]
            if len(data) > PIPE_BATCH_MAX_COUNT:
                self.flush_pipe(data)
        # If anything in the pipeline not yet flushed, flush them now
        if len(data) > 0:
            self.flush_pipe(data)
        # Save the new set
        self.peer_l = self.new_peer_l.copy()

    # Map one peer's neighbor-show JSON onto the BGP_NEIGH_APPLIED fields.
    # A field is left out when FRR has no value for it, so that the consumer
    # reports an absent leaf rather than inventing one.
    def build_applied_fields(self, peer_info):
        fields = {}

        # adminShutDown is emitted only when the peer is administratively shut
        # down, so its absence is what "enabled" looks like.
        fields['enabled'] = 'false' if peer_info.get('adminShutDown') else 'true'

        neighbor_addr = peer_info.get('neighborAddr')
        if neighbor_addr and neighbor_addr != UNKNOWN_ADDR:
            # Dynamically created peers are reported with a trailing '*'.
            fields['neighbor_address'] = neighbor_addr.rstrip('*')

        remote_as = peer_info.get('remoteAs')
        if remote_as is not None:
            fields['peer_as'] = str(remote_as)

        # Absent unless the peer actually belongs to a peer group.
        peer_group = peer_info.get('peerGroup')
        if peer_group:
            fields['peer_group'] = peer_group

        local_addr = peer_info.get('hostLocal')
        if local_addr and local_addr != UNKNOWN_ADDR:
            fields['local_address'] = local_addr

        remote_addr = peer_info.get('hostForeign')
        if remote_addr and remote_addr != UNKNOWN_ADDR:
            fields['remote_address'] = remote_addr

        # TCP ports from the same neighbor JSON. FRR reports 0 (or omits them)
        # when no socket is bound; leave the Redis field out in that case so
        # the consumer reports an absent leaf rather than port 0.
        local_port = peer_info.get('portLocal')
        if local_port is not None:
            try:
                port = int(local_port)
            except (TypeError, ValueError):
                port = 0
            if 1 <= port <= 65535:
                fields['local_port'] = str(port)

        remote_port = peer_info.get('portForeign')
        if remote_port is not None:
            try:
                port = int(remote_port)
            except (TypeError, ValueError):
                port = 0
            if 1 <= port <= 65535:
                fields['remote_port'] = str(port)

        # Absent until a session negotiates something, which keeps a peer that
        # never came up from reporting capabilities it only ever advertised.
        caps = negotiated_capabilities(peer_info.get('neighborCapabilities'))
        if caps:
            fields[APPLIED_CAPS_FIELD] = ','.join(caps)

        return fields

    # Map one peer's neighbor-show JSON onto the BGP_NEIGH_STATS fields, with
    # the same rule as the applied fields: a field FRR has no value for is left
    # out rather than reported as zero, because zero is a real counter value and
    # would be indistinguishable from "not reported".
    def build_stats_fields(self, peer_info):
        fields = {}

        established = peer_info.get('connectionsEstablished')
        if established is not None:
            fields['established_transitions'] = str(established)

        # FRR emits bgpTimerUpEstablishedEpoch only while the session is
        # currently Established, so the field disappears when the session drops.
        # That is the case the explicit field removal in flush_applied_pipe
        # exists for: a stale uptime left behind on a down session would read as
        # if the session were up.
        epoch = peer_info.get('bgpTimerUpEstablishedEpoch')
        if epoch is not None:
            fields['last_established'] = str(epoch)

        msg_stats = peer_info.get('messageStats')
        if isinstance(msg_stats, dict):
            updates_recv = msg_stats.get('updatesRecv')
            if updates_recv is not None:
                fields['updates_received'] = str(updates_recv)
            updates_sent = msg_stats.get('updatesSent')
            if updates_sent is not None:
                fields['updates_sent'] = str(updates_sent)

        err_hex = peer_info.get('lastErrorCodeSubcode')
        if isinstance(err_hex, str) and len(err_hex) >= 2:
            try:
                code = int(err_hex[0:2], 16)
                fields['last_notification_error_code'] = str(code)
            except ValueError:
                pass

        return fields

    # Map one address family entry of one peer onto the BGP_NEIGH_AF_STATS
    # fields, with the same absent-means-not-reported rule as the other tables.
    def build_af_stats_fields(self, af_info):
        fields = {}

        received = af_info.get('acceptedPrefixCounter')
        if received is not None:
            fields['prefixes_received'] = str(received)

        # FRR emits sentPrefixCounter only while the peer belongs to an outbound
        # update subgroup, which it joins a moment after the session establishes
        # and leaves when the session drops, so this field is legitimately
        # missing on a session that has just come up.
        sent = af_info.get('sentPrefixCounter')
        if sent is not None:
            fields['prefixes_sent'] = str(sent)

        # The route-maps FRR applies to this family. A peer-group member reports
        # the ones it inherits, which its own BGP_NEIGHBOR_AF row does not carry,
        # and FRR omits the key for a direction with no route-map. The name is
        # reported whether or not a route-map of that name exists, as FRR does.
        import_policy = af_info.get('routeMapForIncomingAdvertisements')
        if import_policy:
            fields['import_policy'] = import_policy
        export_policy = af_info.get('routeMapForOutgoingAdvertisements')
        if export_policy:
            fields['export_policy'] = export_policy

        return fields

    def update_new_af_stats_states(self, vrf, peer, peer_info):
        af_info = peer_info.get('addressFamilyInfo')
        if not isinstance(af_info, dict):
            return
        for frr_af, af_entry in af_info.items():
            afi_safi = AF_STATS_NAMES.get(frr_af)
            if afi_safi is None or not isinstance(af_entry, dict):
                continue
            fields = self.build_af_stats_fields(af_entry)
            # A family FRR reports no field at all for is left unpublished
            # rather than written as an empty row, so the removal pass below
            # deletes a row whose counters have gone away.
            if fields:
                self.new_af_stats_state[(vrf, peer, afi_safi)] = fields

    def update_new_applied_states(self, vrf, vrf_dict):
        for peer, peer_info in vrf_dict.items():
            # 'show bgp vrf all neighbors json' puts vrfId and vrfName as
            # siblings of the peer entries, so anything that is not a dictionary
            # is metadata and not a peer.
            if not isinstance(peer_info, dict):
                continue
            self.new_applied_state[(vrf, peer)] = self.build_applied_fields(peer_info)
            self.new_stats_state[(vrf, peer)] = self.build_stats_fields(peer_info)
            self.update_new_af_stats_states(vrf, peer, peer_info)

    # Take a new snapshot of the applied neighbor configuration. Returns True
    # only when the snapshot is usable, so a failed scrape cannot be mistaken
    # for "FRR reports no neighbors" and wipe the table.
    def build_globals_applied_fields(self, vrf_summary):
        """Map one vrf's summary JSON onto BGP_GLOBALS_APPLIED fields."""
        if not isinstance(vrf_summary, dict):
            return {}
        fields = {}
        router_id = vrf_summary.get('routerId')
        asn = vrf_summary.get('as')
        total_pfx = 0
        total_paths = 0
        for af_key in ('ipv4Unicast', 'ipv6Unicast'):
            af = vrf_summary.get(af_key)
            if not isinstance(af, dict):
                continue
            if not router_id:
                rid = af.get('routerId')
                if rid:
                    router_id = rid
            if asn is None:
                af_as = af.get('as')
                if af_as is not None:
                    asn = af_as
            tr = af.get('totalRoutes')
            if tr is None:
                tr = af.get('ribCount')
            if tr is not None:
                total_pfx += int(tr)
            tp = af.get('totalPaths')
            if tp is not None:
                total_paths += int(tp)
        if router_id:
            fields['router_id'] = str(router_id)
        if asn is not None:
            fields['local_asn'] = str(asn)
        if total_pfx:
            fields['total_prefixes'] = str(total_pfx)
        if total_paths:
            fields['total_paths'] = str(total_paths)
        return fields

    def get_all_globals_applied_states(self):
        cmd = ["vtysh", "-H", "/dev/null", "-c", 'show bgp vrf all summary json']
        try:
            rc, output = getstatusoutput_noshell(cmd)
            if rc:
                syslog.syslog(syslog.LOG_WARNING,
                              "*WARNING* Failed with rc:{} when execute: {}".format(rc, cmd))
                return False
            if len(output) == 0:
                syslog.syslog(syslog.LOG_WARNING, "*WARNING* output none when execute: {}".format(cmd))
                return False
            vrf_info = json.loads(output)
        except Exception as e:
            syslog.syslog(syslog.LOG_WARNING,
                          "*WARNING* An unexpected error occurred: {} when execute: {}".format(e, cmd))
            return False

        self.new_globals_applied_state.clear()
        for vrf, vrf_dict in vrf_info.items():
            if not isinstance(vrf_dict, dict):
                continue
            fields = self.build_globals_applied_fields(vrf_dict)
            if fields:
                self.new_globals_applied_state[vrf] = fields
        return True

    def update_globals_applied_states(self):
        ops = []
        for vrf, fields in self.new_globals_applied_state.items():
            previous = self.globals_applied_state.get(vrf)
            if previous != fields:
                ops.append(RowOp("%s|%s" % (GLOBALS_APPLIED_TABLE, vrf),
                                 fields, departed_fields(previous, fields)))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        for vrf in self.globals_applied_state:
            if vrf not in self.new_globals_applied_state:
                ops.append(RowOp("%s|%s" % (GLOBALS_APPLIED_TABLE, vrf), None, ()))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        ops.append(RowOp(GLOBALS_APPLIED_META_KEY, {'last_sync': str(int(time.time()))}, ()))
        self.flush_applied_pipe(ops)
        self.globals_applied_state = {vrf: dict(fields)
                                        for vrf, fields in self.new_globals_applied_state.items()}

    # One call per VRF, since 'show bgp vrf all peer-group json' answers {}: the
    # command looks the instance up by name, and the default instance has none,
    # so it is asked for without a vrf and every other one by name. The VRFs
    # are the ones the summary scrape just found; when that scrape failed there
    # is no list to go by, and the pass is skipped rather than guessed at.
    def get_all_peer_group_applied_states(self, vrfs):
        groups = {}
        for vrf in sorted(vrfs):
            if vrf == "default":
                cmd = ["vtysh", "-H", "/dev/null", "-c", 'show bgp peer-group json']
            else:
                cmd = ["vtysh", "-H", "/dev/null", "-c", 'show bgp vrf %s peer-group json' % vrf]
            try:
                rc, output = getstatusoutput_noshell(cmd)
                if rc:
                    syslog.syslog(syslog.LOG_WARNING,
                                  "*WARNING* Failed with rc:{} when execute: {}".format(rc, cmd))
                    return False
                if len(output) == 0:
                    syslog.syslog(syslog.LOG_WARNING, "*WARNING* output none when execute: {}".format(cmd))
                    return False
                groups[vrf] = json.loads(output)
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING,
                              "*WARNING* An unexpected error occurred: {} when execute: {}".format(e, cmd))
                return False

        self.new_peer_group_applied_state.clear()
        self.new_peer_group_af_applied_state.clear()
        for vrf, vrf_groups in groups.items():
            if not isinstance(vrf_groups, dict):
                continue
            for name, group in vrf_groups.items():
                if not isinstance(group, dict):
                    continue
                self.new_peer_group_applied_state[(vrf, name)] = peer_group_applied_fields(name, group)
                for afi_safi in peer_group_applied_families(group):
                    self.new_peer_group_af_applied_state[(vrf, name, afi_safi)] = {'enabled': 'true'}
        return True

    def update_peer_group_applied_states(self):
        ops = []
        for current, previous_state, table in (
                (self.new_peer_group_applied_state, self.peer_group_applied_state, PEER_GROUP_APPLIED_TABLE),
                (self.new_peer_group_af_applied_state, self.peer_group_af_applied_state, PEER_GROUP_AF_APPLIED_TABLE)):
            for entry, fields in current.items():
                previous = previous_state.get(entry)
                if previous != fields:
                    ops.append(RowOp("%s|%s" % (table, "|".join(entry)),
                                     fields, departed_fields(previous, fields)))
                if len(ops) > PIPE_BATCH_MAX_COUNT:
                    self.flush_applied_pipe(ops)
            for entry in previous_state:
                if entry not in current:
                    ops.append(RowOp("%s|%s" % (table, "|".join(entry)), None, ()))
                if len(ops) > PIPE_BATCH_MAX_COUNT:
                    self.flush_applied_pipe(ops)
        ops.append(RowOp(PEER_GROUP_APPLIED_META_KEY, {'last_sync': str(int(time.time()))}, ()))
        self.flush_applied_pipe(ops)
        self.peer_group_applied_state = {entry: dict(fields)
                                         for entry, fields in self.new_peer_group_applied_state.items()}
        self.peer_group_af_applied_state = {entry: dict(fields)
                                            for entry, fields in self.new_peer_group_af_applied_state.items()}

    def get_all_applied_states(self):
        cmd = ["vtysh", "-H", "/dev/null", "-c", 'show bgp vrf all neighbors json']
        try:
            rc, output = getstatusoutput_noshell(cmd)
            if rc:
                syslog.syslog(syslog.LOG_WARNING,
                              "*WARNING* Failed with rc:{} when execute: {}".format(rc, cmd))
                return False
            if len(output) == 0:
                syslog.syslog(syslog.LOG_WARNING, "*WARNING* output none when execute: {}".format(cmd))
                return False

            vrf_info = json.loads(output)
        except Exception as e:
            # Deliberately does not exit the process the way get_all_neigh_states
            # does: this scrape must never be able to take down the publisher of
            # NEIGH_STATE_TABLE.
            syslog.syslog(syslog.LOG_WARNING,
                          "*WARNING* An unexpected error occurred: {} when execute: {}".format(e, cmd))
            return False

        self.new_applied_state.clear()
        self.new_stats_state.clear()
        self.new_af_stats_state.clear()
        for vrf, vrf_dict in vrf_info.items():
            if isinstance(vrf_dict, dict):
                self.update_new_applied_states(vrf, vrf_dict)
        return True

    # A changed row is updated in place: the current fields are set, and any
    # field FRR has stopped reporting is removed by name. A field does have to
    # be able to disappear -- a peer taken out of its peer group for the applied
    # table, a session that dropped and so has no established epoch for the
    # stats table -- and HSET alone cannot do that, which is why this once
    # deleted the row first. Deleting it made every value change a removal
    # followed by a re-creation; naming the departed fields keeps the row
    # present throughout, so a subscriber sees the change and not a delete.
    def flush_applied_pipe(self, ops):
        for op in ops:
            if op.fields is None:
                command = swsscommon.RedisCommand()
                command.formatDEL(op.key)
                self.pipe.push(command)
                continue
            # The set goes first so the row never reads as missing a value it
            # still has. The two commands touch disjoint fields, since a
            # departed field is by definition absent from the new set, so their
            # order changes nothing about where the row ends up.
            if op.fields:
                command = swsscommon.RedisCommand()
                command.formatHSET(op.key, op.fields)
                self.pipe.push(command)
            if op.removed:
                command = swsscommon.RedisCommand()
                command.formatHDEL(op.key, list(op.removed))
                self.pipe.push(command)

        self.pipe.flush()
        del ops[:]

    def update_applied_states(self):
        ops = []
        for entry, fields in self.new_applied_state.items():
            # Compare the whole published tuple, not just one field: an operator
            # can change peer-as, peer-group or local-address while the session
            # stays Established, and gating on session state alone would leave
            # these values stale forever.
            previous = self.applied_state.get(entry)
            if previous != fields:
                ops.append(RowOp("%s|%s|%s" % (APPLIED_TABLE, entry[0], entry[1]),
                                 fields, departed_fields(previous, fields)))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        # Drop rows for peers FRR no longer reports
        for entry in self.applied_state:
            if entry not in self.new_applied_state:
                ops.append(RowOp("%s|%s|%s" % (APPLIED_TABLE, entry[0], entry[1]), None, ()))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        # Always refresh the marker, even when no row changed, since it is what
        # tells a consumer the absent fields are current rather than unsampled.
        ops.append(RowOp(APPLIED_META_KEY, {'last_sync': str(int(time.time()))}, ()))
        self.flush_applied_pipe(ops)

        self.applied_state = {entry: dict(fields)
                              for entry, fields in self.new_applied_state.items()}

    # Counters are written from the same snapshot as the applied values, so this
    # adds no vtysh call. It does add a write per poll for every session that is
    # passing UPDATEs, which is what a counter table costs; an idle session still
    # writes nothing, because the comparison below gates on the whole field set.
    def update_stats_states(self):
        ops = []
        for entry, fields in self.new_stats_state.items():
            previous = self.stats_state.get(entry)
            if previous != fields:
                ops.append(RowOp("%s|%s|%s" % (STATS_TABLE, entry[0], entry[1]),
                                 fields, departed_fields(previous, fields)))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        # Drop rows for peers FRR no longer reports
        for entry in self.stats_state:
            if entry not in self.new_stats_state:
                ops.append(RowOp("%s|%s|%s" % (STATS_TABLE, entry[0], entry[1]), None, ()))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        if len(ops) > 0:
            self.flush_applied_pipe(ops)

        self.stats_state = {entry: dict(fields)
                            for entry, fields in self.new_stats_state.items()}

    # Same snapshot again, so no extra vtysh call. The removal pass matters more
    # here than for the other tables: a family deactivated on a peer that stays
    # up leaves the peer in the scrape while its family entry disappears, and a
    # row left behind would report the prefix counts of a family no longer
    # negotiated.
    def update_af_stats_states(self):
        ops = []
        for entry, fields in self.new_af_stats_state.items():
            previous = self.af_stats_state.get(entry)
            if previous != fields:
                ops.append(RowOp("%s|%s|%s|%s" % (AF_STATS_TABLE, entry[0], entry[1], entry[2]),
                                 fields, departed_fields(previous, fields)))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        for entry in self.af_stats_state:
            if entry not in self.new_af_stats_state:
                ops.append(RowOp("%s|%s|%s|%s" % (AF_STATS_TABLE, entry[0], entry[1], entry[2]), None, ()))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        if len(ops) > 0:
            self.flush_applied_pipe(ops)

        self.af_stats_state = {entry: dict(fields)
                              for entry, fields in self.new_af_stats_state.items()}

    # Take a new snapshot of the route-maps FRR has applied. Returns True only
    # when the snapshot is usable, so a failed scrape cannot be mistaken for
    # "FRR holds no route-maps" and wipe the tables.
    def get_all_route_map_states(self):
        cmd = ["vtysh", "-H", "/dev/null", "-c", 'show route-map json']
        try:
            rc, output = getstatusoutput_noshell(cmd)
            if rc:
                syslog.syslog(syslog.LOG_WARNING,
                              "*WARNING* Failed with rc:{} when execute: {}".format(rc, cmd))
                return False
            if len(output) == 0:
                syslog.syslog(syslog.LOG_WARNING, "*WARNING* output none when execute: {}".format(cmd))
                return False
            daemons = json.loads(output)
        except Exception as e:
            syslog.syslog(syslog.LOG_WARNING,
                          "*WARNING* An unexpected error occurred: {} when execute: {}".format(e, cmd))
            return False

        # The bgpd view and only that. FRR reports a route-map under every
        # daemon that could use it, and each reports only the clauses its own
        # daemon understands: for the same statement ospfd reports no set
        # clauses where bgpd reports "local-preference 250". Reading zebra or
        # ospfd would silently drop every BGP set clause.
        maps = daemons.get("bgpd", {})
        if not isinstance(maps, dict):
            return False

        self.new_route_map_state = {}
        self.new_route_map_set_state = {}
        for name, body in maps.items():
            if not isinstance(body, dict):
                continue
            self.new_route_map_set_state[name] = {
                "invoked": str(body.get("invoked", 0)),
                "cpu_time_ms": str(body.get("cpuTimeMS", 0)),
            }
            for rule in body.get("rules", []):
                seq = str(rule.get("sequenceNumber", ""))
                if not seq:
                    continue
                self.new_route_map_state[(name, seq)] = route_map_rule_fields(rule)
        return True

    # Publish the snapshot, one row per rule plus one per map. Gated on the
    # whole field set the way the applied neighbor table is, so an unchanged
    # route-map costs no Redis write however often it is scraped.
    def update_route_map_states(self):
        ops = []
        for entry, fields in self.new_route_map_state.items():
            previous = self.route_map_state.get(entry)
            if previous != fields:
                ops.append(RowOp("%s|%s|%s" % (ROUTE_MAP_TABLE, entry[0], entry[1]),
                                 fields, departed_fields(previous, fields)))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        for entry in self.route_map_state:
            if entry not in self.new_route_map_state:
                ops.append(RowOp("%s|%s|%s" % (ROUTE_MAP_TABLE, entry[0], entry[1]), None, ()))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)

        for name, fields in self.new_route_map_set_state.items():
            previous = self.route_map_set_state.get(name)
            if previous != fields:
                ops.append(RowOp("%s|%s" % (ROUTE_MAP_SET_TABLE, name),
                                 fields, departed_fields(previous, fields)))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        for name in self.route_map_set_state:
            if name not in self.new_route_map_set_state:
                ops.append(RowOp("%s|%s" % (ROUTE_MAP_SET_TABLE, name), None, ()))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)

        # Always refreshed, so an absent field reads as "FRR has no value"
        # rather than "the producer has not sampled yet".
        ops.append(RowOp(ROUTE_MAP_META_KEY, {'last_sync': str(int(time.time()))}, ()))
        self.flush_applied_pipe(ops)

        self.route_map_state = {entry: dict(fields)
                                for entry, fields in self.new_route_map_state.items()}
        self.route_map_set_state = {name: dict(fields)
                                    for name, fields in self.new_route_map_set_state.items()}

    def get_all_static_route_states(self):
        """FRR's static routes, both families, every VRF.

        Two calls, because 'show ip route' and 'show ipv6 route' are separate
        commands, and both in their 'vrf all' form, which keys the dump by VRF
        name and so covers instances beyond default in one go. A failure in
        either abandons the whole snapshot rather than publishing half of it:
        publishing one family alone would withdraw the other family's rows as
        though FRR had dropped them.
        """
        routes = {}
        for cmd in (["vtysh", "-H", "/dev/null", "-c", 'show ip route vrf all static json'],
                    ["vtysh", "-H", "/dev/null", "-c", 'show ipv6 route vrf all static json']):
            try:
                rc, output = getstatusoutput_noshell(cmd)
                if rc:
                    syslog.syslog(syslog.LOG_WARNING,
                                  "*WARNING* Failed with rc:{} when execute: {}".format(rc, cmd))
                    return False
                if len(output) == 0:
                    syslog.syslog(syslog.LOG_WARNING, "*WARNING* output none when execute: {}".format(cmd))
                    return False
                vrfs = json.loads(output)
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING,
                              "*WARNING* An unexpected error occurred: {} when execute: {}".format(e, cmd))
                return False

            if not isinstance(vrfs, dict):
                return False
            for vrf, prefixes in vrfs.items():
                if not isinstance(prefixes, dict):
                    continue
                for prefix, entries in prefixes.items():
                    for entry in entries:
                        # The command filters to static already; this keeps a
                        # route of another protocol out of the table if it ever
                        # stops doing so.
                        if entry.get("protocol") != "static":
                            continue
                        routes[(vrf, prefix)] = static_route_fields(entry)

        self.new_static_route_state = routes
        return True

    # Publish the snapshot, one row per route, gated on the whole field set the
    # way the other applied tables are, so an unchanged route costs no Redis
    # write however often it is scraped.
    def update_static_route_states(self):
        ops = []
        for entry, fields in self.new_static_route_state.items():
            previous = self.static_route_state.get(entry)
            if previous != fields:
                ops.append(RowOp("%s|%s|%s" % (STATIC_ROUTE_TABLE, entry[0], entry[1]),
                                 fields, departed_fields(previous, fields)))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)
        for entry in self.static_route_state:
            if entry not in self.new_static_route_state:
                ops.append(RowOp("%s|%s|%s" % (STATIC_ROUTE_TABLE, entry[0], entry[1]), None, ()))
            if len(ops) > PIPE_BATCH_MAX_COUNT:
                self.flush_applied_pipe(ops)

        # Always refreshed, so an absent row reads as "FRR is not holding this
        # route" rather than "the producer has not sampled yet".
        ops.append(RowOp(STATIC_ROUTE_META_KEY, {'last_sync': str(int(time.time()))}, ()))
        self.flush_applied_pipe(ops)

        self.static_route_state = {entry: dict(fields)
                                   for entry, fields in self.new_static_route_state.items()}


def main():

    syslog.syslog(syslog.LOG_INFO, "bgpmon service started")
    bgp_state_get = None
    try:
        bgp_state_get = BgpStateGet()
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, "{}: error exit 1, reason {}".format("THIS_MODULE", str(e)))
        sys.exit(1)

    # periodically obtain the new neighbor information and update if necessary
    while True:
        time.sleep(15)
        if bgp_state_get.bgp_activity_detected():
            bgp_state_get.get_all_neigh_states()
            bgp_state_get.update_neigh_states()
        # Intentionally outside the frr.log activity gate. A configuration change
        # that FRR accepts without logging would otherwise never be scraped, and
        # the applied values would stay stale indefinitely. The write itself is
        # still change gated, so an idle system does not add Redis writes.
        if bgp_state_get.get_all_globals_applied_states():
            bgp_state_get.update_globals_applied_states()
            # The peer-groups are asked for per VRF, from the list the summary
            # scrape has just produced, and carry their own marker.
            vrfs = list(bgp_state_get.new_globals_applied_state.keys())
            if bgp_state_get.get_all_peer_group_applied_states(vrfs):
                bgp_state_get.update_peer_group_applied_states()
        if bgp_state_get.get_all_applied_states():
            # Counters go first because the freshness marker both tables share is
            # written at the end of update_applied_states. Writing the marker
            # last means it can only ever be older than the rows it vouches for,
            # never newer, so a consumer cannot see a fresh marker over counters
            # from the previous poll.
            bgp_state_get.update_stats_states()
            bgp_state_get.update_af_stats_states()
            bgp_state_get.update_applied_states()
        # The route-maps come from their own vtysh call and carry their own
        # marker, so a failure here cannot affect the neighbor tables.
        if bgp_state_get.get_all_route_map_states():
            bgp_state_get.update_route_map_states()
        # Static routes likewise: their own calls and their own marker, so a
        # failure here leaves the other tables alone.
        if bgp_state_get.get_all_static_route_states():
            bgp_state_get.update_static_route_states()

if __name__ == '__main__':
    main()
