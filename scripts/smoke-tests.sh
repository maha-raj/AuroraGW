#!/usr/bin/env bash
set -euo pipefail
echo "== AuroraGW smoke tests =="
systemctl --no-pager --plain is-active nftables auroragw-web >/dev/null
echo "OK: nftables + auroragw-web active"
systemctl --no-pager --plain is-active kea-dhcp4-server unbound >/dev/null
echo "OK: kea-dhcp4-server + unbound active"
nft list ruleset | grep -q "table inet filter"
echo "OK: nftables ruleset present"
ss -ltn | grep -q ":8443"
echo "OK: web UI listening (8443)"
echo "Done."

# PPPoE optional check
if grep -q "mode: pppoe" /etc/auroragw/config.yaml 2>/dev/null; then
  systemctl --no-pager --plain is-active auroragw-pppoe.service >/dev/null && echo "OK: PPPoE service active" || echo "WARN: PPPoE service not active"
fi
