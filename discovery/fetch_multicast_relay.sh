#!/usr/bin/env bash
set -euo pipefail
DST_DIR="/opt/auroragw/discovery"
DST="${DST_DIR}/multicast-relay.py"
TAG="${TAG:-1.3.1}"
URL_A="https://raw.githubusercontent.com/alsmith/multicast-relay/${TAG}/multicast-relay.py"
URL_B="https://raw.githubusercontent.com/alsmith/multicast-relay/v${TAG}/multicast-relay.py"
mkdir -p "$DST_DIR"
if ! curl -fsSL "$URL_A" -o "$DST"; then
  curl -fsSL "$URL_B" -o "$DST"
fi
chmod +x "$DST"
echo "Fetched: $DST"
