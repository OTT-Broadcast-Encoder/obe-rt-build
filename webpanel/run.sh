#!/usr/bin/env bash
set -euo pipefail
PANEL_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OBE_PANEL_PORT=${OBE_PANEL_PORT:-18088}
OBE_PANEL_IMAGE=${OBE_PANEL_IMAGE:-obe-rt-build:srt-2026-10-21}
if ! [[ "$OBE_PANEL_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$OBE_PANEL_PORT < 1 || 10#$OBE_PANEL_PORT > 65535)); then
    echo 'OBE_PANEL_PORT deve essere una porta tra 1 e 65535.' >&2
    exit 2
fi
if ! docker image inspect "$OBE_PANEL_IMAGE" >/dev/null 2>&1; then
    echo 'Immagine builder non disponibile. Seguire la build isolata nel README.' >&2
    exit 1
fi
exec docker run --rm --init --name obe-webpanel --cpus=6 --memory=8g \
    -p "127.0.0.1:${OBE_PANEL_PORT}:8088" \
    --mount "type=bind,source=$PANEL_ROOT,target=/work" -w /work \
    -e PYTHONDONTWRITEBYTECODE=1 "$OBE_PANEL_IMAGE" \
    python3 -u webpanel/server.py --bind 0.0.0.0 --port 8088 \
    --root /work --state-dir /work/webpanel/.state
