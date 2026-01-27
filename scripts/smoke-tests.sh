#!/usr/bin/env bash
set -euo pipefail
echo "== AuroraGW smoke tests =="
systemctl --no-pager --plain is-active nftables dnsmasq >/dev/null
echo "OK: nftables + dnsmasq active"
nft list ruleset | grep -q "table inet filter"
echo "OK: nftables ruleset present"
ss -ltn | grep -q ":8080"
echo "OK: status server listening"
echo "Done."

# PPPoE optional check
if grep -q "mode: pppoe" /etc/auroragw/config.yaml 2>/dev/null; then
  systemctl --no-pager --plain is-active auroragw-pppoe.service >/dev/null && echo "OK: PPPoE service active" || echo "WARN: PPPoE service not active"
fi
