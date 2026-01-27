#!/usr/bin/env bash
set -euo pipefail

ROUTER="192.168.102.1"
LAN_NET="192.168.101.0/24"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --router) ROUTER="${2:-}"; shift 2 ;;
    --lan-net) LAN_NET="${2:-}"; shift 2 ;;
    *) echo "Usage: $0 [--router 192.168.102.1] [--lan-net 192.168.101.0/24]"; exit 1 ;;
  esac
done

echo "== OPT1 validation against $ROUTER =="

echo "-- Ping router"
ping -c 2 "$ROUTER" >/dev/null
echo "OK: ping"

echo "-- DNS via router (Unbound)"
dig +time=2 +tries=1 @"$ROUTER" example.com A >/dev/null
echo "OK: DNS query"

echo "-- NAT / Internet reachability"
curl -fsS --max-time 5 https://example.com >/dev/null
echo "OK: outbound internet"

echo "-- OPT1 -> LAN isolation check (best-effort)"
LAN_HOST="$(echo "$LAN_NET" | awk -F'[./]' '{print $1\".\"$2\".\"$3\".1\"}')"
if ping -c 1 -W 1 "$LAN_HOST" >/dev/null 2>&1; then
  echo "WARN: able to ping $LAN_HOST (isolation may be disabled or ICMP allowed)"
else
  echo "OK: cannot ping $LAN_HOST (expected for OPT1->LAN isolation)"
fi

echo "Done."
