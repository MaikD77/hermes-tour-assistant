#!/usr/bin/env bash
# Idempotenter Start des OwnTracks Receivers inkl. TLS-Proxy.
# Usage:
#   bash scripts/owntracks-start.sh
#
# Voraussetzungen:
#   - OwnTracks Receiver installiert unter ~/.hermes/owntracks/
#   - API-Key in ~/.hermes/owntracks/.api_key
#   - TLS-Zertifikate in ~/.hermes/owntracks/tls/ (optional für HTTPS-Proxy)
set -euo pipefail

PORT="${OWNTRACKS_PORT:-9090}"
TLS_PORT="${OWNTRACKS_TLS_PORT:-9443}"
API_KEY_FILE="${HOME}/.hermes/owntracks/.api_key"
DIR="${HOME}/.hermes/owntracks"
TLS_CERT="${DIR}/tls/server.pem"
TLS_KEY="${DIR}/tls/server-key.pem"
VENV_PYTHON="python3"

# Use Hermes venv if available
if [ -f "${HOME}/.hermes/hermes-agent/venv/bin/python3" ]; then
    VENV_PYTHON="${HOME}/.hermes/hermes-agent/venv/bin/python3"
fi

if [ ! -f "$API_KEY_FILE" ]; then
    echo "FATAL: ${API_KEY_FILE} not found" >&2
    echo "Generate one with: openssl rand -hex 32 > ${API_KEY_FILE}" >&2
    exit 1
fi

# FastAPI-Server (HTTP, für Hermes intern)
if ! lsof -i "tcp:${PORT}" -P -n 2>/dev/null | grep -q LISTEN; then
    echo "Starting receiver on port ${PORT}..."
    cd "$DIR"
    export OWNTRACKS_API_KEY="$(cat "$API_KEY_FILE")"
    export OWNTRACKS_PORT="$PORT"
    nohup "$VENV_PYTHON" owntracks_receiver.py > /dev/null 2>&1 &
    sleep 2
fi

# TLS-Proxy (HTTPS, für OwnTracks-App via Tailscale/VPN)
if [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
    if ! lsof -i "tcp:${TLS_PORT}" -P -n 2>/dev/null | grep -q LISTEN; then
        echo "Starting TLS proxy on port ${TLS_PORT}..."
        cd "$DIR"
        export OWNTRACKS_TLS_CERT="$TLS_CERT"
        export OWNTRACKS_TLS_KEY="$TLS_KEY"
        export OWNTRACKS_TLS_PORT="$TLS_PORT"
        export OWNTRACKS_PORT="$PORT"
        nohup "$VENV_PYTHON" tls_proxy.py > /dev/null 2>&1 &
        echo "Both started"
    else
        echo "Already running — receiver:${PORT}, proxy:${TLS_PORT}"
    fi
else
    echo "Receiver running on port ${PORT} (no TLS proxy — TLS certs not found)"
fi