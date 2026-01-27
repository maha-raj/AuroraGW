#!/usr/bin/env bash
set -euo pipefail
exec /opt/auroragw/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8443 --ssl-keyfile /etc/auroragw/tls/key.pem --ssl-certfile /etc/auroragw/tls/cert.pem
