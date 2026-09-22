#!/bin/bash

PLATFORM=$(sonic-db-cli CONFIG_DB HGET 'DEVICE_METADATA|localhost' platform)

# Parse the device specific asic conf file, if it exists
ASIC_CONF=/usr/share/sonic/device/$PLATFORM/asic.conf
if [ -f "$ASIC_CONF" ]; then
    source $ASIC_CONF
fi

# On Multi NPU platforms we need to start  the rsyslog server on the docker0 ip address
# for the syslogs from the containers in the namespaces to work.
# on Single NPU platforms we continue to use loopback adddres

if [[ ($NUM_ASIC -gt 1) ]]; then
    udp_server_ip=$(ip -o -4 addr list docker0 | awk '{print $4}' | cut -d/ -f1)
else
    udp_server_ip=$(ip -j -4 addr list lo scope host | jq -r -M '.[0].addr_info[0].local')
fi

bridged_syslog_features="dhcp_server"
for feature in $bridged_syslog_features; do
    if [ -n "$(sonic-db-cli CONFIG_DB keys "FEATURE|$feature")" ]; then
        docker0_ip=$(ip -o -4 addr list docker0 | awk '{print $4}' | cut -d/ -f1)
        break
    fi
done

syslog_with_osversion=$(sonic-db-cli CONFIG_DB hget "DEVICE_METADATA|localhost" "syslog_with_osversion")
if [ -z "$syslog_with_osversion" ]; then
    syslog_with_osversion="false"
fi

os_version=$(sonic-cfggen -y /etc/sonic/sonic_version.yml -v build_version)
if [ -z "$os_version" ]; then
    os_version="Unknown"
fi

syslog_counter=$(sonic-db-cli CONFIG_DB hget "DEVICE_METADATA|localhost" "syslog_counter")
if [ -z "$syslog_counter" ]; then
    syslog_counter="false"
fi

# Resolve the global syslog source interface (SYSLOG_CONFIG|GLOBAL:src_intf,
# e.g. Loopback0) to an IP address. This is used as the default source address
# for remote syslog forwarding when a server has no per-server source of its
# own (EOS 'logging source-interface').
syslog_src_intf=$(sonic-db-cli CONFIG_DB hget "SYSLOG_CONFIG|GLOBAL" "src_intf")
syslog_src_ip=""
syslog_src_ip6=""
if [ -n "$syslog_src_intf" ]; then
    syslog_src_ip=$(ip -j -4 addr list "$syslog_src_intf" 2>/dev/null | jq -r -M '[.[].addr_info[]?.local][0] // empty')
    syslog_src_ip6=$(ip -j -6 addr list "$syslog_src_intf" scope global 2>/dev/null | jq -r -M '[.[].addr_info[]?.local][0] // empty')
fi

# Profiles whose PEMs host-services copied to /etc/sonic/ssl-certs/<id>/.
# The Jinja template only emits TLS omfwd for ids listed here so missing certs
# never fall back to cleartext.
ssl_profiles_json="{"
tls_first=1
if [ -d /etc/sonic/ssl-certs ]; then
    for d in /etc/sonic/ssl-certs/*/; do
        [ -d "$d" ] || continue
        id=$(basename "$d")
        if [ -f "${d}ca-bundle.pem" ] && [ -f "${d}cert.pem" ] && [ -f "${d}key.pem" ]; then
            if [ "$tls_first" -eq 0 ]; then
                ssl_profiles_json="${ssl_profiles_json},"
            fi
            ssl_profiles_json="${ssl_profiles_json}\"${id}\": true"
            tls_first=0
        fi
    done
fi
ssl_profiles_json="${ssl_profiles_json}}"

ssl_profile_ready() {
    local id="$1"
    local d="/etc/sonic/ssl-certs/${id}"
    [ -n "$id" ] && [ -f "${d}/ca-bundle.pem" ] && [ -f "${d}/cert.pem" ] && [ -f "${d}/key.pem" ]
}

# Mirror hostcfgd RSyslogCfg._publish_tls_apply_state so Certz PEM install
# (rsyslog-config restart without a CONFIG_DB SYSLOG_SERVER change) still
# updates STATE_DB SYSLOG_SERVER|<addr> applied_transport_security.
publish_syslog_tls_apply_state() {
    local apply_ok="$1"
    local key proto ts profile applied reason proto_lc ts_lc k found

    mapfile -t cfg_keys < <(sonic-db-cli CONFIG_DB KEYS "SYSLOG_SERVER|*" 2>/dev/null || true)

    while IFS= read -r k; do
        [ -n "$k" ] || continue
        found=0
        for key in "${cfg_keys[@]}"; do
            if [ "$k" = "$key" ]; then
                found=1
                break
            fi
        done
        if [ "$found" -eq 0 ]; then
            sonic-db-cli STATE_DB DEL "$k" >/dev/null 2>&1 || true
        fi
    done < <(sonic-db-cli STATE_DB KEYS "SYSLOG_SERVER|*" 2>/dev/null || true)

    for key in "${cfg_keys[@]}"; do
        [ -n "$key" ] || continue
        proto=$(sonic-db-cli CONFIG_DB HGET "$key" protocol 2>/dev/null || true)
        ts=$(sonic-db-cli CONFIG_DB HGET "$key" transport_security 2>/dev/null || true)
        profile=$(sonic-db-cli CONFIG_DB HGET "$key" tls_profile_id 2>/dev/null || true)
        applied="false"
        reason="NONE"
        proto_lc=$(printf '%s' "$proto" | tr '[:upper:]' '[:lower:]')
        ts_lc=$(printf '%s' "$ts" | tr '[:upper:]' '[:lower:]')
        if [ "$proto_lc" = "tls" ] || [ "$ts_lc" = "true" ] || [ "$ts_lc" = "tls" ] || \
           [ "$ts_lc" = "enabled" ] || [ "$ts_lc" = "1" ]; then
            if [ -z "$profile" ]; then
                reason="NO_PROFILE"
            elif ! ssl_profile_ready "$profile"; then
                reason="NO_CERTIFICATE"
            elif [ "$apply_ok" != "true" ]; then
                reason="RSYSLOG_APPLY_FAILED"
            else
                applied="true"
                reason="NONE"
            fi
        fi
        sonic-db-cli STATE_DB HSET "$key" applied_transport_security "$applied" >/dev/null 2>&1 || true
        sonic-db-cli STATE_DB HSET "$key" failure_reason "$reason" >/dev/null 2>&1 || true
    done
}

# Generate temporary config to validate output before overwriting
# /etc/rsyslog.conf
TMPFILE=$(mktemp /tmp/rsyslog.conf.XXXXXX)
trap 'rm -f "$TMPFILE"' EXIT

# Build the -a argument with jq so that runtime values (which can
# contain quotes, backslashes, or other JSON-breaking characters) are
# safely encoded, rather than interpolated as raw strings into a
# hand-built JSON literal. The rsyslog.conf.j2 template also strips
# newlines/quotes/backslashes/percent signs from values rendered into
# $template directives; this jq encoding ensures the JSON itself is
# well-formed so sonic-cfggen can parse it in the first place.
json_args=$(jq -n \
    --arg udp_server_ip "$udp_server_ip" \
    --arg docker0_ip "$docker0_ip" \
    --arg forward_with_osversion "$syslog_with_osversion" \
    --arg os_version "$os_version" \
    --arg syslog_counter "$syslog_counter" \
    --arg syslog_src_ip "$syslog_src_ip" \
    --arg syslog_src_ip6 "$syslog_src_ip6" \
    --argjson ssl_profiles "$ssl_profiles_json" \
    '{udp_server_ip: $udp_server_ip, docker0_ip: $docker0_ip, forward_with_osversion: $forward_with_osversion, os_version: $os_version, syslog_counter: $syslog_counter, syslog_src_ip: $syslog_src_ip, syslog_src_ip6: $syslog_src_ip6, ssl_profiles: $ssl_profiles}')

sonic-cfggen -d -t /usr/share/sonic/templates/rsyslog.conf.j2 \
    -a "$json_args" \
    > "$TMPFILE"

if ! cp "$TMPFILE" /etc/rsyslog.conf; then
    echo "Failed to update /etc/rsyslog.conf; not restarting rsyslog" >&2
    publish_syslog_tls_apply_state false
    exit 1
fi

systemctl restart rsyslog
publish_syslog_tls_apply_state true
