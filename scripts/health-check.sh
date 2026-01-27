#!/usr/bin/env bash
set -euo pipefail
CFG="/etc/auroragw/config.yaml"
[[ -f "$CFG" ]] || exit 0
WAN_MODE=$(awk '/mode:/{print $2; exit}' "$CFG" 2>/dev/null || echo "dhcp")
if [[ "$WAN_MODE" == "pppoe" ]]; then
  ip link show pppoe0 >/dev/null 2>&1 || systemctl restart auroragw-pppoe.service || true
fi
systemctl is-active --quiet nftables || systemctl restart nftables || true
systemctl is-active --quiet auroragw-web || systemctl restart auroragw-web || true
