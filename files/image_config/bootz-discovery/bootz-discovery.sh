#!/bin/bash
#
# bootz-discovery.sh - Discover a bootz server over DHCP and provision.
#
# Requests the SZTP redirect option (RFC 8572): option 143 for IPv4 and
# option 136 for IPv6. The option value is a sequence of 2-byte big-endian
# URI lengths each followed by the URI bytes.

readonly LOGFILE="/var/log/bootz-discovery.log"
readonly LEASE_FILE="/tmp/bootz-dhcp-lease.sh"
readonly BOOTZ_CONFIG_TMP="/tmp/bootz_config.json"
readonly BOOTZ_CONFIG="/host/bootz_config.json"
# Written by the bootz wrapper: after provisioning succeeds, and once the
# server has accepted the SUCCESS report.
readonly BOOTZ_PENDING_REPORT="/host/bootz_pending_report.json"
readonly BOOTZ_PROVISIONED="/host/bootz_provisioned"
readonly BOOTZ="/usr/bin/bootz"
readonly CHASSIS_SCRIPT="/usr/local/bin/bootz-get-chassis.py"
readonly INVENTORY="/run/inventory.json"
readonly RETRY_SECONDS=20

log() {
  echo "$(date) - $1" | tee -a "$LOGFILE"
}

#######################################
# Import platform identity from /proc/cmdline, falling back to machine.conf.
# Globals:
#   ONIE_PLATFORM, ONIE_MACHINE, ONIE_ARCH, ONIE_DHCP
#######################################
import_cmdline() {
  local x
  for x in $(cat /proc/cmdline); do
    case "$x" in
      onie_platform=*) ONIE_PLATFORM="${x#*=}" ;;
      onie_machine=*)  ONIE_MACHINE="${x#*=}" ;;
      onie_arch=*)     ONIE_ARCH="${x#*=}" ;;
    esac
  done

  if [[ -f /host/machine.conf ]]; then
    # shellcheck source=/dev/null
    . /host/machine.conf
    [[ -z "$ONIE_PLATFORM" ]] && ONIE_PLATFORM="$onie_platform"
    [[ -z "$ONIE_MACHINE" ]] && ONIE_MACHINE="$onie_machine"
    [[ -z "$ONIE_ARCH" ]] && ONIE_ARCH="$onie_arch"
    ONIE_DHCP="$onie_dhcp"
  fi

  log "platform=${ONIE_PLATFORM} machine=${ONIE_MACHINE} arch=${ONIE_ARCH}"
}

#######################################
# Write the chassis inventory consumed by bootz-client.
# The client reads this file by default (--chassis-script SELF) and cannot
# identify the device without it.
# Returns:
#   0 on success, 1 if the descriptor lacks a usable serial number
#######################################
write_inventory() {
  if [[ ! -x "$CHASSIS_SCRIPT" ]]; then
    log "chassis script $CHASSIS_SCRIPT not present"
    return 1
  fi

  local json
  if ! json="$("$CHASSIS_SCRIPT" 2>>"$LOGFILE")"; then
    log "chassis script failed"
    return 1
  fi

  local serial
  serial=$(echo "$json" | jq -r '.chassis_serial_number // ""')
  if [[ -z "$serial" || "$serial" == "Unknown" ]]; then
    log "chassis script returned no serial number; refusing to provision"
    return 1
  fi

  echo "$json" > "$INVENTORY"
  log "wrote $INVENTORY for serial $serial"
}

#######################################
# Decode a bootz-servers option value into a list of URIs.
# dhclient writes the option as an octal-escaped quoted string, e.g.
#   option bootz-servers "\000\023bootz://1.1.1.1:443";
# Arguments:
#   $1: Raw octal-escaped option value
# Returns:
#   Space-separated URIs on stdout
#######################################
parse_bootz_servers() {
  local raw="$1"
  local uris="" pos=0
  local hex total

  hex=$(printf '%b' "$raw" | hexdump -v -e '/1 "%02X"')
  total=${#hex}

  while [[ $pos -lt $total ]]; do
    [[ $((pos + 4)) -gt $total ]] && break
    local uri_len=$(( 16#${hex:$pos:4} ))
    pos=$((pos + 4))

    [[ $((pos + uri_len * 2)) -gt $total ]] && break

    local uri_hex="${hex:$pos:$((uri_len * 2))}"
    local uri=""
    if [[ -n "$uri_hex" ]]; then
      uri=$(echo -n "$uri_hex" | sed 's/\(..\)/\\x\1/g')
      uri=$(printf '%b' "$uri")
    fi
    uris="${uris:+$uris }$uri"

    pos=$((pos + uri_len * 2))
  done

  echo "$uris"
}

#######################################
# Write the bootz config consumed by the bootz wrapper, in config_db format.
# Arguments:
#   $1: IP address with prefix length (CIDR)
#   $2: Default gateway
#   $3: Bootz servers (space-separated URIs)
#   $4: NTP servers (space-separated)
#   $5: Interface name
#   $6: Hostname
#   $7: Syslog servers (space-separated)
#   $8: Output path
#######################################
save_bootz_config() {
  local ip_addr="$1"
  local gateway="$2"
  local bootz_servers="$3"
  local ntp_servers="$4"
  local iface="$5"
  local hostname="$6"
  local syslog_servers="$7"
  local output="$8"

  local ntp_json syslog_json bootz_urls_json
  ntp_json=$(echo "$ntp_servers" | tr ' ' '\n' | grep -v '^$' | \
    jq -Rn '[inputs] | map({(.): {}}) | add // {}')
  syslog_json=$(echo "$syslog_servers" | tr ' ' '\n' | grep -v '^$' | \
    jq -Rn '[inputs] | map({(.): {}}) | add // {}')
  bootz_urls_json=$(echo "$bootz_servers" | tr ' ' '\n' | grep -v '^$' | \
    jq -Rn '[inputs]')

  jq -n \
    --arg hostname      "$hostname" \
    --arg iface_cidr    "${iface}|${ip_addr}" \
    --arg gw            "$gateway" \
    --argjson bootz_urls "$bootz_urls_json" \
    --argjson ntp        "$ntp_json" \
    --argjson syslog     "$syslog_json" \
    '{
      "DEVICE_METADATA": {"localhost": {"hostname": $hostname}},
      "NTP_SERVER": $ntp,
      "SYSLOG_SERVER": $syslog,
      "BOOTZ_SERVER_METADATA": {
        "server_info": {"server_urls": $bootz_urls, "status": "initiated"}
      },
      "MGMT_INTERFACE": {($iface_cidr): {"gwaddr": $gw}}
    }' > "$output"

  log "saved bootz config to $output"
}

# Preserve the lease databases across the reboot into the provisioned image.
save_lease_to_host() {
  if [[ -f /var/lib/dhcp/dhclient.leases ]]; then
    cp /var/lib/dhcp/dhclient.leases /host/dhclient.leases
  fi
  if [[ -f /var/lib/dhcp/dhclient6.leases ]]; then
    cp /var/lib/dhcp/dhclient6.leases /host/dhclient6.leases
  fi
}

#######################################
# Run dhclient over IPv4 and record the results in LEASE_FILE.
# Arguments:
#   $1: Interface
# Returns:
#   0 if a global address was obtained, 1 otherwise
#######################################
try_dhclient() {
  local iface="$1"
  command -v dhclient >/dev/null || return 1
  log "trying dhclient (IPv4) on $iface"

  local lease_db="/var/lib/dhcp/dhclient.leases"
  mkdir -p /var/lib/dhcp
  rm -f "$lease_db"

  local cf="/etc/dhcp/dhclient_bootz.conf"
  mkdir -p /etc/dhcp
  {
    echo "option bootz-servers code 143 = text;"
    echo "request subnet-mask, broadcast-address, time-offset, routers, domain-name, domain-name-servers, host-name, ntp-servers, log-servers, bootz-servers;"
  } > "$cf"

  dhclient -4 -1 -v -cf "$cf" -lf "$lease_db" -pf /var/run/dhclient.pid "$iface" || return 1
  ip -4 addr show dev "$iface" | grep -q "global" || return 1
  [[ -f "$lease_db" ]] || return 1

  local bootz_raw
  bootz_raw=$(grep "option bootz-servers" "$lease_db" | tail -n 1 | awk -F'"' '{print $2}')
  if [[ -n "$bootz_raw" ]]; then
    local parsed
    parsed="$(parse_bootz_servers "$bootz_raw")"
    log "found bootz-servers (IPv4): $parsed"
    echo "DHCP_BOOTZ_SERVERS='$parsed'" >> "$LEASE_FILE"
  fi

  local ntp_raw syslog_raw
  ntp_raw=$(grep "option ntp-servers" "$lease_db" | tail -n 1 | sed 's/.*ntp-servers //;s/;//' | tr ',' ' ')
  [[ -n "$ntp_raw" ]] && echo "DHCP_NTP_SERVERS='$ntp_raw'" >> "$LEASE_FILE"
  syslog_raw=$(grep "option log-servers" "$lease_db" | tail -n 1 | sed 's/.*log-servers //;s/;//' | tr ',' ' ')
  [[ -n "$syslog_raw" ]] && echo "DHCP_SYSLOG_SERVERS='$syslog_raw'" >> "$LEASE_FILE"

  local dhcp_ip dhcp_gw dhcp_hostname
  dhcp_ip=$(ip -4 -o addr show dev "$iface" | grep "scope global" | awk '{print $4}' | head -n 1)
  dhcp_gw=$(grep "routers" "$lease_db" | tail -n 1 | awk '{print $3}' | tr -d ';')
  dhcp_hostname=$(grep "option host-name" "$lease_db" | tail -n 1 | awk -F'"' '{print $2}')
  {
    echo "DHCP_IP='$dhcp_ip'"
    echo "DHCP_GW='$dhcp_gw'"
    echo "DHCP_IFACE='$iface'"
    echo "DHCP_HOSTNAME='$dhcp_hostname'"
  } >> "$LEASE_FILE"

  return 0
}

#######################################
# Run dhclient over IPv6 and record the results in LEASE_FILE.
# Arguments:
#   $1: Interface
# Returns:
#   0 if a global address was obtained, 1 otherwise
#######################################
try_dhclient6() {
  local iface="$1"
  command -v dhclient >/dev/null || return 1
  log "trying dhclient (IPv6) on $iface"

  local cf="/etc/dhcp/dhclient6_bootz.conf"
  mkdir -p /etc/dhcp
  cat > "$cf" <<EOF
option dhcp6.bootz-servers code 136 = text;
option dhcp6.sntp-servers code 31 = array of ip6-address;

request dhcp6.name-servers, dhcp6.domain-search, dhcp6.ia-na, dhcp6.bootz-servers, dhcp6.sntp-servers;
EOF

  local lease_db="/var/lib/dhcp/dhclient6.leases"
  mkdir -p /var/lib/dhcp
  rm -f "$lease_db"

  dhclient -6 -1 -v -cf "$cf" -lf "$lease_db" -pf /var/run/dhclient6.pid "$iface" || return 1

  # Wait for DAD to complete before the address is usable.
  local wait_count=0
  while ip -6 addr show dev "$iface" | grep -q "tentative" && [[ $wait_count -lt 10 ]]; do
    sleep 1
    wait_count=$((wait_count + 1))
  done

  ip -6 addr show dev "$iface" | grep -q "global" || return 1
  [[ -f "$lease_db" ]] || return 1

  local bootz_raw
  bootz_raw=$(grep -i "bootz-servers" "$lease_db" | tail -n 1 | awk -F'"' '{print $2}')
  if [[ -n "$bootz_raw" ]]; then
    local parsed
    parsed="$(parse_bootz_servers "$bootz_raw")"
    log "found bootz-servers (IPv6): $parsed"
    echo "DHCP_BOOTZ_SERVERS='$parsed'" >> "$LEASE_FILE"
  fi

  local ntp_raw
  ntp_raw=$(grep -i "sntp-servers" "$lease_db" | tail -n 1 | sed 's/.*sntp-servers //;s/;//' | tr ',' ' ')
  [[ -n "$ntp_raw" ]] && echo "DHCP_NTP_SERVERS='$ntp_raw'" >> "$LEASE_FILE"

  local v6_ip v6_gw v6_hostname
  v6_ip=$(ip -6 -o addr show dev "$iface" | grep "scope global" | awk '{print $4}' | head -n 1)
  v6_gw=$(ip -6 route show | grep "^default via" | head -n 1 | awk '{print $3}')
  v6_hostname=$(grep "option host-name" "$lease_db" | tail -n 1 | awk -F'"' '{print $2}')
  {
    echo "DHCP_IP='$v6_ip'"
    echo "DHCP_GW='$v6_gw'"
    echo "DHCP_IFACE='$iface'"
    echo "DHCP_HOSTNAME='$v6_hostname'"
  } >> "$LEASE_FILE"

  return 0
}

#######################################
# Stop the dhclient daemons started by try_dhclient and try_dhclient6.
# dhclient -1 stays running once it holds a lease, and every retry would
# start another one.
# Arguments:
#   $1: Interface
#######################################
release_dhclient() {
  local iface="$1"
  command -v dhclient >/dev/null || return 0
  dhclient -4 -x -pf /var/run/dhclient.pid "$iface" >/dev/null 2>&1
  dhclient -6 -x -pf /var/run/dhclient6.pid "$iface" >/dev/null 2>&1
  return 0
}

# Load LEASE_FILE, dropping values from an earlier lease.
load_lease() {
  unset DHCP_BOOTZ_SERVERS DHCP_NTP_SERVERS DHCP_SYSLOG_SERVERS \
    DHCP_IP DHCP_GW DHCP_IFACE DHCP_HOSTNAME
  [[ -f "$LEASE_FILE" ]] || return 1
  # shellcheck source=/dev/null
  . "$LEASE_FILE"
}

#######################################
# Run one discovery cycle: DHCP, then provision if a server was offered.
# Returns:
#   0 if provisioning succeeded, 1 otherwise
#######################################
discover_and_provision() {
  local iface="$1"

  rm -f "$LEASE_FILE"
  try_dhclient6 "$iface"
  load_lease

  # A DHCPv6 lease without option 136 says nothing about DHCPv4 option 143.
  if [[ -z "${DHCP_BOOTZ_SERVERS:-}" ]]; then
    rm -f "$LEASE_FILE"
    try_dhclient "$iface"
    load_lease || return 1
  fi

  if [[ -z "${DHCP_BOOTZ_SERVERS:-}" ]]; then
    log "no bootz-servers offered"
    return 1
  fi

  save_bootz_config "${DHCP_IP:-}" "${DHCP_GW:-}" "$DHCP_BOOTZ_SERVERS" \
    "${DHCP_NTP_SERVERS:-}" "${DHCP_IFACE:-$iface}" "${DHCP_HOSTNAME:-}" \
    "${DHCP_SYSLOG_SERVERS:-}" "$BOOTZ_CONFIG_TMP"

  # bootz-client reboots as soon as provisioning succeeds, so whatever the
  # next boot needs has to be on /host before it runs.
  cp "$BOOTZ_CONFIG_TMP" "$BOOTZ_CONFIG"
  save_lease_to_host

  if ! "$BOOTZ" provision; then
    log "bootz provision failed"
    rm -f "$BOOTZ_CONFIG" /host/dhclient.leases /host/dhclient6.leases
    return 1
  fi

  log "bootz provision succeeded"
  log "rebooting"
  reboot
  return 0
}

main() {
  # The pending report is sent later in this boot by bootz.service. Remove
  # BOOTZ_PROVISIONED to provision again.
  if [[ -f "$BOOTZ_PROVISIONED" || -f "$BOOTZ_PENDING_REPORT" ]]; then
    log "already provisioned by bootz, skipping discovery"
    exit 0
  fi

  log "starting bootz discovery"
  import_cmdline

  local iface="${ONIE_DHCP:-eth0}"
  ip link set dev "$iface" up

  while true; do
    if write_inventory && discover_and_provision "$iface"; then
      exit 0
    fi
    release_dhclient "$iface"
    log "discovery cycle failed, retrying in ${RETRY_SECONDS}s"
    sleep "$RETRY_SECONDS"
  done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
