#!/usr/bin/env bash
set -euo pipefail
PANEL_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OBE_PANEL_PORT=${OBE_PANEL_PORT:-18088}
OBE_PANEL_IMAGE=${OBE_PANEL_IMAGE:-obe-rt-build:srt-2026-10-21}
OBE_PANEL_LAN_IP=${OBE_PANEL_LAN_IP:-}
OBE_PANEL_NETWORK_MODE=${OBE_PANEL_NETWORK_MODE:-bridge}
if ! [[ "$OBE_PANEL_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$OBE_PANEL_PORT < 1 || 10#$OBE_PANEL_PORT > 65535)); then
    echo 'OBE_PANEL_PORT deve essere una porta tra 1 e 65535.' >&2
    exit 2
fi
if [[ "$OBE_PANEL_NETWORK_MODE" != bridge && "$OBE_PANEL_NETWORK_MODE" != host ]]; then
    echo 'OBE_PANEL_NETWORK_MODE deve essere bridge oppure host.' >&2
    exit 2
fi
publish=(-p "127.0.0.1:${OBE_PANEL_PORT}:8088")
panel_bind=0.0.0.0
panel_port=8088
allowed_hosts=()
tls_args=()
if [[ -n "${OBE_PANEL_TLS_CERT:-}" || -n "${OBE_PANEL_TLS_KEY:-}" ]]; then
    if [[ -z "${OBE_PANEL_TLS_CERT:-}" || -z "${OBE_PANEL_TLS_KEY:-}" ]]; then
        echo 'Specificare sia OBE_PANEL_TLS_CERT che OBE_PANEL_TLS_KEY (percorsi interni al container).' >&2
        exit 2
    fi
    tls_args=(--tls-cert "$OBE_PANEL_TLS_CERT" --tls-key "$OBE_PANEL_TLS_KEY")
fi
if [[ -n "$OBE_PANEL_LAN_IP" ]]; then
    octet='(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
    if ! [[ "$OBE_PANEL_LAN_IP" =~ ^${octet}\.${octet}\.${octet}\.${octet}$ ]] || [[ "$OBE_PANEL_LAN_IP" == 0.0.0.0 ]]; then
        echo 'OBE_PANEL_LAN_IP deve essere un indirizzo IPv4 specifico del server.' >&2
        exit 2
    fi
    if [[ "$OBE_PANEL_LAN_IP" != 127.0.0.1 ]]; then
        publish+=(-p "${OBE_PANEL_LAN_IP}:${OBE_PANEL_PORT}:8088")
        allowed_hosts+=(--allow-host "$OBE_PANEL_LAN_IP")
    fi
fi
if [[ "$OBE_PANEL_NETWORK_MODE" == host ]]; then
    # Multicast must reach the physical LAN; Docker's default bridge does not route it.
    publish=(--network host)
    panel_port=$OBE_PANEL_PORT
    panel_bind=127.0.0.1
    if [[ -n "$OBE_PANEL_LAN_IP" && "$OBE_PANEL_LAN_IP" != 127.0.0.1 ]]; then
        panel_bind=0.0.0.0
    fi
fi
if ! docker image inspect "$OBE_PANEL_IMAGE" >/dev/null 2>&1; then
    echo 'Immagine builder non disponibile. Seguire la build isolata nel README.' >&2
    exit 1
fi
# This container is intentionally independent of obe-webpanel.service. Restarting
# the panel must not close SRT sessions or release the encoder's UDP ingress.
transport_layout="$PANEL_ROOT|$OBE_PANEL_NETWORK_MODE|$OBE_PANEL_LAN_IP|$OBE_PANEL_PORT"
if docker container inspect obe-transport >/dev/null 2>&1; then
    existing_layout=$(docker inspect -f '{{index .Config.Labels "obe.transport.layout"}}' obe-transport)
    if [[ "$existing_layout" != "$transport_layout" ]]; then
        echo 'Il distributore esistente usa un’altra configurazione di rete: pianificare la sua riconfigurazione prima di avviare il pannello.' >&2
        exit 1
    fi
    docker start obe-transport >/dev/null
else
    docker run -d --init --restart unless-stopped --name obe-transport --cpus=2 --memory=512m \
        --label "obe.transport.layout=$transport_layout" "${publish[@]}" \
        --mount "type=bind,source=$PANEL_ROOT,target=/work" -w /work \
        -e PYTHONDONTWRITEBYTECODE=1 "$OBE_PANEL_IMAGE" \
        python3 -u webpanel/transport_service.py --root /work \
        --state-dir /work/webpanel/.state/transport >/dev/null
fi
# The control plane uses a private Unix socket; media stays on loopback in the
# same network namespace. Host mode also preserves that namespace on a broker restart.
if [[ "$OBE_PANEL_NETWORK_MODE" == host ]]; then
    panel_network=(--network host)
else
    panel_network=(--network container:obe-transport)
fi
transport_ready=false
for attempt in {1..30}; do
    if docker exec -w /work/webpanel obe-transport python3 -B -c \
        'from transport_client import Client; import sys; sys.exit(not Client("/work/webpanel/.state/transport/control.sock", "main").state()["service_available"])' \
        >/dev/null 2>&1; then
        transport_ready=true
        break
    fi
    sleep 0.2
done
if [[ "$transport_ready" != true ]]; then
    echo 'Il servizio delle uscite non è pronto; controllare il container obe-transport.' >&2
    exit 1
fi
exec docker run --rm --init --name obe-webpanel --cpus=6 --memory=8g \
    "${panel_network[@]}" \
    --mount "type=bind,source=$PANEL_ROOT,target=/work" -w /work \
    -e PYTHONDONTWRITEBYTECODE=1 "$OBE_PANEL_IMAGE" \
    python3 -u webpanel/server.py --bind "$panel_bind" --port "$panel_port" \
    --root /work --state-dir /work/webpanel/.state \
    --transport-socket /work/webpanel/.state/transport/control.sock "${allowed_hosts[@]}" "${tls_args[@]}"
