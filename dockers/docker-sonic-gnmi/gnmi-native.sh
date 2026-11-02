#!/usr/bin/env bash

EXIT_TELEMETRY_VARS_FILE_NOT_FOUND=1
INCORRECT_TELEMETRY_VALUE=2
INVALID_LISTENER_MODE=3
SOCKET_DIRECTORY_ERROR=4
INVALID_TLS_CONFIGURATION=5
TELEMETRY_VARS_FILE=/usr/share/sonic/templates/telemetry_vars.j2
ESCAPE_QUOTE="'\''"

extract_field() {
    if [ -z "$1" ]; then
        return
    fi
    jq -r "$2" <<< "$1"
}

if [ ! -f "$TELEMETRY_VARS_FILE" ]; then
    echo "Telemetry vars template file not found"
    exit $EXIT_TELEMETRY_VARS_FILE_NOT_FOUND
fi

# Try to read telemetry and certs config from ConfigDB.
# Use default value if no valid config exists
TELEMETRY_VARS=$(sonic-cfggen -d -t $TELEMETRY_VARS_FILE)
TELEMETRY_VARS=${TELEMETRY_VARS//[\']/\"}
X509=$(jq -r '.x509 // empty' <<< "$TELEMETRY_VARS")
GNMI=$(jq -r '.gnmi // empty' <<< "$TELEMETRY_VARS")
CERTS=$(jq -r '.certs // empty' <<< "$TELEMETRY_VARS")

DEVICE_TYPE=$(sonic-db-cli CONFIG_DB hget "DEVICE_METADATA|localhost" "type")
SWITCH_TYPE=$(sonic-db-cli CONFIG_DB hget "DEVICE_METADATA|localhost" "switch_type")
IS_SMART_SWITCH_DPU=false
if [[ "$DEVICE_TYPE" == "SmartSwitchDPU" || "$SWITCH_TYPE" == "dpu" ]]; then
    IS_SMART_SWITCH_DPU=true
fi
DPU_EPHEMERAL_TLS=false
if [[ "$IS_SMART_SWITCH_DPU" == "true" ]]; then
    DPU_TLS_CONFIG="$CERTS"
    if [[ -z "$DPU_TLS_CONFIG" ]]; then
        DPU_TLS_CONFIG="$X509"
    fi
    if [[ -z "$DPU_TLS_CONFIG" ]] ||
       [[ -z "$(jq -r '.server_crt // empty' <<< "$DPU_TLS_CONFIG")" ]] ||
       [[ -z "$(jq -r '.server_key // empty' <<< "$DPU_TLS_CONFIG")" ]]; then
        DPU_EPHEMERAL_TLS=true
    fi
fi

# Enable GRPC GO LOG
export GRPC_GO_LOG_VERBOSITY_LEVEL=99
export GRPC_GO_LOG_SEVERITY_LEVEL=info

TELEMETRY_ARGS=" -logtostderr"
NO_TLS_FALLBACK=false
export CVL_SCHEMA_PATH=/usr/sbin/schema

if [[ "$DPU_EPHEMERAL_TLS" == "true" ]]; then
    TELEMETRY_ARGS+=" --insecure"
elif [ -n "$CERTS" ]; then
    SERVER_CRT=$(extract_field "$CERTS" '.server_crt // empty')
    SERVER_KEY=$(extract_field "$CERTS" '.server_key // empty')
    if [[ -z "$SERVER_CRT" || -z "$SERVER_KEY" ]]; then
        echo "TLS configuration requires both server_crt and server_key" >&2
        exit $INVALID_TLS_CONFIGURATION
    fi
    TELEMETRY_ARGS+=" --server_crt $SERVER_CRT --server_key $SERVER_KEY "

    CA_CRT=$(extract_field "$CERTS" '.ca_crt')
    if [ ! -z $CA_CRT ]; then
        TELEMETRY_ARGS+=" --ca_crt $CA_CRT"
    fi

elif [ -n "$X509" ]; then
    SERVER_CRT=$(extract_field "$X509" '.server_crt // empty')
    SERVER_KEY=$(extract_field "$X509" '.server_key // empty')
    if [[ -z "$SERVER_CRT" || -z "$SERVER_KEY" ]]; then
        echo "TLS configuration requires both server_crt and server_key" >&2
        exit $INVALID_TLS_CONFIGURATION
    fi
    TELEMETRY_ARGS+=" --server_crt $SERVER_CRT --server_key $SERVER_KEY "

    CA_CRT=$(extract_field "$X509" '.ca_crt')
    if [ ! -z $CA_CRT ]; then
        TELEMETRY_ARGS+=" --ca_crt $CA_CRT"
    fi
else
    TELEMETRY_ARGS+=" --noTLS --bind_address 127.0.0.1"
    NO_TLS_FALLBACK=true
fi

# If no configuration entry exists for TELEMETRY, create one default port
if [ -z "$GNMI" ]; then
    PORT=8080
else
    PORT=$(extract_field "$GNMI" '.port')
    if ! [[ $PORT =~ ^[0-9]+$ ]]; then
        echo "Incorrect port value ${PORT}, expecting positive integers" >&2
        exit $INCORRECT_TELEMETRY_VALUE
    fi
fi

case "${GNMI_LISTENER_MODE-config}" in
    config)
        ;;
    uds-only)
        PORT=0
        if ! mkdir -p /var/run/gnmi || ! chmod 0750 /var/run/gnmi; then
            echo "Failed to prepare gNMI socket directory /var/run/gnmi" >&2
            exit $SOCKET_DIRECTORY_ERROR
        fi
        TELEMETRY_ARGS+=" --unix_socket /var/run/gnmi/gnmi.sock"
        ;;
    *)
        printf "Unsupported GNMI_LISTENER_MODE %q; expected 'config' or 'uds-only'\n" "${GNMI_LISTENER_MODE}" >&2
        exit $INVALID_LISTENER_MODE
        ;;
esac

TELEMETRY_ARGS+=" --port $PORT"

# DSCP marking for served gNMI/gNOI/gNSI (observer) traffic: model-driven
# equivalent of the vendor "qos dscp" knob. Sourced from CONFIG_DB GNMI|gnmi:dscp
# (programmable via the OpenConfig grpc-server config). Default to 32 (AF41)
# when unset/invalid, matching the server's built-in default.
DSCP=$(extract_field "$GNMI" '.dscp')
if [[ $DSCP =~ ^[0-9]+$ ]] && [ "$DSCP" -ge 0 ] && [ "$DSCP" -le 63 ]; then
    TELEMETRY_ARGS+=" --dscp $DSCP"
else
    TELEMETRY_ARGS+=" --dscp 32"
fi

CLIENT_AUTH=$(extract_field "$GNMI" '.client_auth')
if [[ "$DPU_EPHEMERAL_TLS" == "true" || "$CLIENT_AUTH" == "false" ]]; then
    TELEMETRY_ARGS+=" --allow_no_client_auth"
fi

LOG_LEVEL=$(extract_field "$GNMI" '.log_level')
if [[ $LOG_LEVEL =~ ^[0-9]+$ ]]; then
    TELEMETRY_ARGS+=" -v=$LOG_LEVEL"
else
    TELEMETRY_ARGS+=" -v=2"
fi

# gRPC defaults to a 4 MiB message limit. gNSI Certz.Rotate with a 20000-CA
# trust bundle is ~52 MB, and SmartSwitch ZMQ traffic needs at least 32 MiB.
TELEMETRY_ARGS+=" -max_recv_msg_size=$((64*1024*1024)) -max_send_msg_size=$((64*1024*1024))"

# Enable ZMQ for SmartSwitch
LOCALHOST_SUBTYPE=`sonic-db-cli CONFIG_DB hget "DEVICE_METADATA|localhost" "subtype"`
if [[ x"${LOCALHOST_SUBTYPE}" == x"SmartSwitch" ]]; then
    TELEMETRY_ARGS+=" -zmq_port=8100"
fi

GNMI_VRF=$(extract_field "$GNMI" '.vrf')
if [[ -n "$GNMI_VRF" && "$GNMI_VRF" != "null" ]]; then
    if [[ "$NO_TLS_FALLBACK" == "true" && "$GNMI_VRF" != "default" ]]; then
        echo "Ignoring gNMI VRF '$GNMI_VRF': certificate-free fallback is restricted to localhost; configure TLS certificates for management-VRF access" >&2
    else
        TELEMETRY_ARGS+=" --gnmi_vrf $GNMI_VRF"
    fi
fi

# Add VRF parameter when mgmt-vrf enabled
MGMT_VRF_ENABLED=`sonic-db-cli CONFIG_DB hget  "MGMT_VRF_CONFIG|vrf_global" "mgmtVrfEnabled"`
if [[ x"${MGMT_VRF_ENABLED}" == x"true" ]]; then
    TELEMETRY_ARGS+=" --vrf mgmt"
fi

# Server will handle threshold connections consecutively
THRESHOLD_CONNECTIONS=$(extract_field "$GNMI" '.threshold')
if [[ $THRESHOLD_CONNECTIONS =~ ^[0-9]+$ ]]; then
    TELEMETRY_ARGS+=" --threshold $THRESHOLD_CONNECTIONS"
else
    if [ -z "$GNMI" ] || [[ $THRESHOLD_CONNECTIONS == "null" ]]; then
        TELEMETRY_ARGS+=" --threshold 100"
    else
        echo "Incorrect threshold value, expecting positive integers" >&2
        exit $INCORRECT_TELEMETRY_VALUE
    fi
fi

# Close idle connections after certain duration (in seconds)
IDLE_CONN_DURATION=$(extract_field "$GNMI" '.idle_conn_duration')
if [[ $IDLE_CONN_DURATION =~ ^[0-9]+$ ]]; then
    TELEMETRY_ARGS+=" --idle_conn_duration $IDLE_CONN_DURATION"
else
    if [ -z "$GNMI" ] || [[ $IDLE_CONN_DURATION == "null" ]]; then
        TELEMETRY_ARGS+=" --idle_conn_duration 5"
    else
        echo "Incorrect idle_conn_duration value, expecting positive integers" >&2
        exit $INCORRECT_TELEMETRY_VALUE
    fi
fi

USER_AUTH=$(extract_field "$GNMI" '.user_auth')
# Fail-closed default: if user_auth is unset (missing GNMI CONFIG_DB entry or
# missing field), force cert mode. Without this, --client_auth is omitted and
# authentication ends up disabled entirely.
if [ -z "$USER_AUTH" ] || [ "$USER_AUTH" == "null" ]; then
    USER_AUTH="cert"
fi
if [ -n "$USER_AUTH" ] && [ "$USER_AUTH" != "null" ]; then
    TELEMETRY_ARGS+=" --client_auth $USER_AUTH"

    if [ $USER_AUTH == "cert" ]; then
        TELEMETRY_ARGS+=" --config_table_name GNMI_CLIENT_CERT"

        ENABLE_CRL=$(echo $GNMI | jq -r '.enable_crl')
        if [ $ENABLE_CRL == "true" ]; then
            TELEMETRY_ARGS+=" --enable_crl"
        fi

        CRL_EXPIRE_DURATION=$(extract_field "$GNMI" '.crl_expire_duration')
        if [ ! -z "$CRL_EXPIRE_DURATION" ] && [ $CRL_EXPIRE_DURATION != "null" ]; then
            TELEMETRY_ARGS+=" --crl_expire_duration $CRL_EXPIRE_DURATION"
        fi
    fi
fi

# gNOI OS Install streams the image into --img_dir before the host-side
# sonic-host-service installs it with sonic-installer. That directory must be
# visible to BOTH the container and the host, so use the /var/tmp bind mount
# (/var/tmp:/mnt/host/var/tmp:rw in rules/docker-gnmi.mk): the container writes
# /mnt/host/var/tmp/<version>, which the host sees as /var/tmp/<version>
# (see GNOI_OS_HOST_IMG_DIR in host_modules/image_service.py). The default
# --img_dir (/tmp/host_tmp) is container-local and NOT reachable by the host.
TELEMETRY_ARGS+=" --img_dir /mnt/host/var/tmp"

# Factory identity in the TPM (persistent key handles and certificate NV
# indexes). gNMI serves the IDevID, or the oIDevID enrollz installs, with the
# TPM key when the configured server key file does not exist, and gNSI
# enrollz/attestz report the IDevID and IAK. The owner IAK cert that enrollz
# installs is kept in /etc/sonic/telemetry, beside the factory cert paths.
TPM_DEVICE=/dev/tpmrm0
if [ -c "$TPM_DEVICE" ]; then
    TELEMETRY_ARGS+=" --tpm_device $TPM_DEVICE"
    TELEMETRY_ARGS+=" --tpm_key_handle 0x81820000 --idevid_cert_nv_index 0x01C90000"
    TELEMETRY_ARGS+=" --iak_handle 0x81821000 --iak_cert_nv_index 0x01C91000"
    TELEMETRY_ARGS+=" --idevid_cert_file /etc/sonic/telemetry/idevid-chain.crt"
    TELEMETRY_ARGS+=" --iak_cert_file /etc/sonic/telemetry/iak-chain.crt"
fi

echo "gnmi args: $TELEMETRY_ARGS"
exec /usr/sbin/telemetry ${TELEMETRY_ARGS}
