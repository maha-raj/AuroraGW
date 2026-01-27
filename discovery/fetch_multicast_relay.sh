#!/usr/bin/env bash
set -euo pipefail
DST_DIR="/opt/auroragw/discovery"
DST="${DST_DIR}/multicast-relay.py"
TAG="${TAG:-1.3.1}"
URL="https://raw.githubusercontent.com/alsmith/multicast-relay/${TAG}/multicast-relay.py"
mkdir -p "$DST_DIR"
curl -fsSL "$URL" -o "$DST"
chmod +x "$DST"
echo "Fetched: $DST"
