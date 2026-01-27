#!/usr/bin/env bash
set -euo pipefail
out="${1:-/tmp/auroragw-backup-$(date +%Y%m%d-%H%M%S).tgz}"
sudo tar czf "$out" \
  /etc/auroragw \
  /etc/netplan/99-auroragw.yaml \
  /etc/nftables.conf \
  /etc/kea/kea-dhcp4.conf \
  /etc/unbound/unbound.conf.d/auroragw.conf \
  /etc/miniupnpd/miniupnpd.conf \
  2>/dev/null || true
echo "Backup written to: $out"
