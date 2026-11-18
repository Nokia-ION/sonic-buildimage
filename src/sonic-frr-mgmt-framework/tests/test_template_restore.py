"""The frr.conf the frrcfgd templates render from CONFIG_DB at boot must carry
the commands frrcfgd itself replays from that CONFIG_DB, so that restoring
through the templates (use_template_render_for_restore true) and through the
handlers (false) leave FRR in the same state."""
import os
import re
from unittest.mock import MagicMock, NonCallableMagicMock, patch

import jinja2
import netaddr

from .test_config import mock_is_interface_name_valid, mock_is_vrf_name_valid

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')

CONFIG_DB = {
    'DEVICE_METADATA': {
        'localhost': {'bgp_asn': '65200', 'docker_routing_config_mode': 'unified',
                      'use_template_render_for_restore': 'true'},
    },
    'VRF': {'Vrf_red': {}},
    'BGP_GLOBALS': {
        'default': {'local_asn': '65200', 'router_id': '11.0.0.64',
                    'load_balance_mp_relax': 'true', 'as_path_mp_as_set': 'true',
                    'always_compare_med': 'true', 'external_compare_router_id': 'true',
                    'ignore_as_path_length': 'true', 'graceful_restart_enable': 'true',
                    'gr_restart_time': '120', 'gr_stale_routes_time': '360',
                    'default_ipv4_unicast': 'false', 'default_ipv6_unicast': 'false',
                    'max_ebgp_paths': '4', 'max_ibgp_paths': '8',
                    'confed_id': '100', 'confed_peers': ['65101', '65102'],
                    'keepalive': '30', 'holdtime': '90'},
        'Vrf_red': {'local_asn': '65200', 'router_id': '11.0.1.64',
                    'default_ipv6_unicast': 'true'},
    },
    'BGP_GLOBALS_AF': {
        'default|ipv4_unicast': {'max_ebgp_paths': '4', 'max_ibgp_paths': '2',
                                 'ibgp_equal_cluster_length': 'true'},
        'default|ipv6_unicast': {'max_ebgp_paths': '3'},
        'Vrf_red|ipv4_unicast': {'max_ebgp_paths': '2'},
    },
    'BGP_GLOBALS_LISTEN_PREFIX': {
        'default|11.1.0.0/24': {'peer_group': 'PG1'},
    },
    'BGP_PEER_GROUP': {
        'default|PG1': {'asn': '65300', 'local_asn': '65201', 'admin_status': 'up',
                        'local_addr': '11.0.0.64', 'name': 'pg one',
                        'ebgp_multihop': 'true', 'ebgp_multihop_ttl': '3',
                        'keepalive': '30', 'holdtime': '90',
                        'conn_retry': '10', 'min_adv_interval': '5', 'passive_mode': 'true',
                        'bfd': 'true', 'peer_port': '1179'},
        'default|PG2': {'peer_type': 'internal'},
    },
    'BGP_NEIGHBOR': {
        'default|11.0.0.155': {'peer_group_name': 'PG1', 'admin_status': 'up'},
        'default|11.0.0.157': {'asn': '65301', 'admin_status': 'down',
                               'shutdown_message': 'maint', 'name': 'peer b',
                               'local_addr': '11.0.0.64', 'keepalive': '20', 'holdtime': '60',
                               'conn_retry': '15', 'min_adv_interval': '3',
                               'ebgp_multihop': 'true',
                               'ebgp_multihop_ttl': '2', 'passive_mode': 'true', 'bfd': 'true',
                               'peer_port': '1180', 'local_asn': '65202'},
        'default|fc00::82': {'peer_type': 'external', 'admin_status': 'up'},
        'Vrf_red|11.0.1.155': {'asn': '65400', 'admin_status': 'up'},
    },
    'BGP_PEER_GROUP_AF': {
        'default|PG1|ipv4_unicast': {'admin_status': 'up', 'allow_as_in': 'true',
                                     'allow_as_count': '3', 'send_default_route': 'true',
                                     'default_rmap': 'RM1', 'max_prefix_limit': '500',
                                     'max_prefix_warning_threshold': '80',
                                     'route_map_in': ['RM1'], 'route_map_out': ['RM2'],
                                     'weight': '100', 'as_override': 'true',
                                     'send_community': 'both',
                                     'remove_private_as_enabled': 'true',
                                     'remove_private_as_all': 'true',
                                     'replace_private_as': 'true'},
        'default|PG1|ipv6_unicast': {'admin_status': 'up', 'send_community': 'standard'},
    },
    'BGP_NEIGHBOR_AF': {
        'default|11.0.0.155|ipv4_unicast': {'admin_status': 'up'},
        'default|11.0.0.157|ipv4_unicast': {'admin_status': 'up', 'allow_as_in': 'true',
                                            'allow_as_origin': 'true',
                                            'max_prefix_limit': '100',
                                            'max_prefix_warning_only': 'true',
                                            'route_map_in': ['RM1'],
                                            'send_community': 'extended',
                                            'tx_add_paths': 'tx_all_paths', 'weight': '50',
                                            'remove_private_as_enabled': 'true'},
        'default|fc00::82|ipv6_unicast': {'admin_status': 'up'},
        'Vrf_red|11.0.1.155|ipv4_unicast': {'admin_status': 'up'},
    },
    'PREFIX_SET': {'PS4': {'mode': 'IPv4'}, 'PS6': {'mode': 'IPv6'}},
    'PREFIX': {
        'PS4|10.0.0.0/8|8..24': {'action': 'permit'},
        'PS4|11.0.0.0/8|exact': {'action': 'deny'},
        'PS6|fc00::/16|exact': {'action': 'permit'},
    },
    'COMMUNITY_SET': {
        'CS1': {'set_type': 'STANDARD', 'match_action': 'ANY',
                'community_member': ['65000:100', '65000:200']},
        'CS2': {'set_type': 'STANDARD', 'match_action': 'ANY',
                'community_member': ['65000:300']},
    },
    'EXTENDED_COMMUNITY_SET': {
        'ECS1': {'set_type': 'STANDARD', 'match_action': 'ANY',
                 'community_member': ['route-target:65000:1']},
        'ECS2': {'set_type': 'STANDARD', 'match_action': 'ANY',
                 'community_member': ['route-origin:65000:2', 'route-target:65000:3']},
    },
    'AS_PATH_SET': {'AS1': {'as_path_set_member': ['^65000_', '_65100$']}},
    'ROUTE_MAP': {
        'RM1|10': {'route_operation': 'permit', 'match_prefix_set': 'PS4',
                   'match_med': '50', 'match_origin': 'IGP',
                   'match_local_pref': '200', 'match_community': 'CS1',
                   'match_ext_community': 'ECS1', 'match_as_path': 'AS1',
                   'match_interface': 'Ethernet0', 'match_protocol': 'bgp',
                   'set_origin': 'IGP',
                   'set_next_hop': '10.10.10.1',
                   'set_metric_action': 'METRIC_SET_VALUE', 'set_metric': '20'},
        'RM1|20': {'route_operation': 'deny',
                   'set_ipv6_next_hop_global': 'fc00::1',
                   'set_community_inline': ['65000:1', 'no-export'],
                   'set_ext_community_inline': ['route-target:1:1'],
                   'call_route_map': 'RM2'},
    },
    'ROUTE_REDISTRIBUTE': {
        'default|connected|bgp|ipv4': {'route_map': ['RM1']},
        'default|static|bgp|ipv6': {},
    },
    'BGP_GLOBALS_AF_AGGREGATE_ADDR': {
        'default|ipv4_unicast|10.1.0.0/16': {'as_set': 'true', 'summary_only': 'true',
                                             'policy': 'RM1'},
    },
    'BGP_GLOBALS_AF_NETWORK': {
        'default|ipv4_unicast|10.2.0.0/16': {'policy': 'RM1'},
    },
    'STATIC_ROUTE': {
        'default|10.50.0.0/16': {'nexthop': '11.0.0.155,11.0.0.157'},
        'default|10.60.0.0/16': {'nexthop': '11.0.0.155', 'distance': '10', 'tag': '5'},
        'default|10.70.0.0/16': {'blackhole': 'true', 'nexthop': '0.0.0.0'},
        'default|fc00:50::/64': {'nexthop': 'fc00::82,fc00::86'},
        'Vrf_red|10.80.0.0/16': {'nexthop': '11.0.1.155'},
    },
}

def typed_key(key):
    parts = key.split('|')
    return tuple(parts) if len(parts) > 1 else key

def typed_db(db):
    return {table: {typed_key(key): dict(val) for key, val in rows.items()}
            for table, rows in db.items()}

def is_ip_version(value, version):
    try:
        return bool(value) and netaddr.IPNetwork(str(value)).version == version
    except Exception:
        return False

def render_restore(db):
    """frr.conf's frrcfgd sections, rendered as docker_init.sh has sonic-cfggen
    render them."""
    paths = [os.path.join(TEMPLATE_DIR, d) for d in ('bgpd', 'staticd')]
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(paths), trim_blocks=True)
    env.filters['ipv4'] = lambda v: is_ip_version(v, 4)
    env.filters['ipv6'] = lambda v: is_ip_version(v, 6)
    data = typed_db(db)
    return '\n'.join(env.get_template(t).render(**data)
                     for t in ('staticd.db.conf.j2', 'bgpd.conf.db.j2'))

CONTEXT_OPENERS = ('router bgp ', 'route-map ', 'vrf ')
CONTEXT_EXITS = ('exit', 'exit-vrf', 'end')
# Negations that change FRR's state when applied to a freshly started daemon;
# any other "no" a restore issues only clears what is not there yet.
EFFECTIVE_NO = ('bgp default ipv4-unicast', 'bgp ebgp-requires-policy',
                'bgp fast-external-failover', 'neighbor {} activate')
SEND_COMMUNITY_DEFAULT = frozenset(('standard', 'extended', 'large'))
SEND_COMMUNITY_TYPES = {'': {'standard'}, 'standard': {'standard'}, 'extended': {'extended'},
                        'large': {'large'}, 'both': {'standard', 'extended'},
                        'all': {'standard', 'extended', 'large'}}

def normalize_line(line):
    line = ' '.join(line.split())
    return re.sub(r'^(router bgp \S+) vrf default$', r'\1', line)

def config_commands(text):
    """The (context, command) sequence a frr.conf applies, context being the
    chain of nodes the command is entered under."""
    seq = []
    stack = []
    for line in text.splitlines():
        cmd = normalize_line(line)
        if not cmd or cmd.startswith('!'):
            continue
        indented = line[0] == ' '
        if not indented:
            stack = []
        if cmd == 'exit-address-family':
            stack = stack[:1]
            continue
        if cmd in CONTEXT_EXITS:
            stack = []
            continue
        seq.append((tuple(stack), cmd))
        if not indented and cmd.startswith(CONTEXT_OPENERS):
            stack = [cmd]
        elif cmd.startswith('address-family '):
            stack = stack[:1] + [cmd]
    return seq

def vtysh_commands(run_cmd):
    """The (context, command) sequence the handlers issued through vtysh."""
    seq = []
    for call in run_cmd.call_args_list:
        argv = call[0][1]
        lines = [normalize_line(a) for a in argv[2::2] if a != 'configure terminal']
        for i, line in enumerate(lines):
            if i < len(lines) - 1:
                seq.append((tuple(lines[:i]), line))
        if lines:
            seq.append((tuple(lines[:-1]), lines[-1]))
    return [(ctx[1:] if ctx[:1] == ('vrf default',) else ctx, cmd)
            for ctx, cmd in seq if cmd != 'vrf default']

def stem(cmd):
    words = cmd.split()
    return tuple(words[:3] if words[0] in ('neighbor', 'bgp', 'set', 'match') else words[:2])

def end_state(seq):
    """What the sequence leaves configured on a freshly started FRR: a "no"
    removes the commands it negates, a command supersedes earlier negations
    of the same knob, and send-community folds into the types sent."""
    state = {}
    sent = {}
    for ctx, cmd in seq:
        neg = cmd.startswith('no ')
        body = cmd[3:] if neg else cmd
        words = body.split()
        if len(words) >= 3 and words[0] == 'neighbor' and words[2] == 'send-community':
            types = sent.setdefault((ctx, words[1]), set(SEND_COMMUNITY_DEFAULT))
            change = SEND_COMMUNITY_TYPES[' '.join(words[3:])]
            if neg:
                types -= change
            else:
                types |= change
            continue
        if neg:
            for key in [k for k in state if k[0] == ctx and k[1].split()[:len(words)] == words]:
                del state[key]
            state[(ctx, cmd)] = True
        else:
            for key in [k for k in state if k[0] == ctx and k[1].startswith('no ') and
                        stem(k[1][3:]) == stem(body)]:
                del state[key]
            state[(ctx, cmd)] = True
    effective_no = [re.compile('^no ' + re.escape(p).replace(re.escape('{}'), r'\S+') + '$')
                    for p in EFFECTIVE_NO]
    cmds = {k for k in state if not k[1].startswith(('no ', 'address-family ')) or
            any(r.match(k[1]) for r in effective_no)}
    for (ctx, nbr), types in sent.items():
        if types != SEND_COMMUNITY_DEFAULT:
            cmds.add((ctx, 'neighbor {} sends community {}'.format(
                nbr, ' '.join(sorted(types)) or 'none')))
    return cmds

class FakeConfigDB(NonCallableMagicMock):
    db = {}
    def get_table(self, table):
        return typed_db({table: self.db.get(table, {})})[table]
    def get_entry(self, table, key):
        return dict(self.db.get(table, {}).get(self.serialize_key(key), {}))
    @staticmethod
    def serialize_key(key):
        return '|'.join(key) if isinstance(key, tuple) else key

def runtime_apply(db, run_cmd):
    """The commands the handlers issue as each CONFIG_DB row arrives on a
    running frrcfgd, in the order frrcfgd subscribes to the tables."""
    swss = MagicMock(isInterfaceNameValid=mock_is_interface_name_valid,
                     isVrfNameValid=mock_is_vrf_name_valid,
                     ConfigDBConnector=FakeConfigDB)
    with patch.dict('sys.modules', **{'swsscommon.swsscommon': swss}):
        import importlib
        import frrcfgd.frrcfgd as frrcfgd
        importlib.reload(frrcfgd)
        with patch.object(frrcfgd, 'g_run_command', run_cmd):
            FakeConfigDB.db = {'DEVICE_METADATA': db['DEVICE_METADATA']}
            daemon = frrcfgd.BGPConfigDaemon()
            FakeConfigDB.db = db
            for table, hdlr in daemon.table_handler_list:
                for key, data in db.get(table, {}).items():
                    hdlr(table, key, {k: sorted(v) if type(v) is list else v for k, v in data.items()})
    return vtysh_commands(run_cmd)

def show(title, cmds):
    out = [title]
    for ctx, cmd in sorted(cmds):
        out.append('  {} :: {}'.format(' > '.join(ctx) or '-', cmd))
    return '\n'.join(out)

def test_template_restore_matches_handlers():
    run_cmd = MagicMock(return_value=True)
    applied = end_state(runtime_apply(CONFIG_DB, run_cmd))
    rendered = end_state(config_commands(render_restore(CONFIG_DB)))
    missing = applied - rendered
    extra = rendered - applied
    assert not missing and not extra, '\n'.join([
        show('applied by the handlers, not rendered:', missing),
        show('rendered, not applied by the handlers:', extra)])
