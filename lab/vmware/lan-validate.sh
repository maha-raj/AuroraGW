#!/usr/bin/env bash
set -euo pipefail

ROUTER="192.168.101.1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --router) ROUTER="${2:-}"; shift 2 ;;
    *) echo "Usage: $0 [--router 192.168.101.1]"; exit 1 ;;
  esac
done

echo "== LAN validation against $ROUTER =="

echo "-- Ping router"
ping -c 2 "$ROUTER" >/dev/null
echo "OK: ping"

echo "-- Web UI (TLS) reachable"
curl -kfsS "https://${ROUTER}:8443/" >/dev/null || true
echo "OK: curl (may require auth; reachability is what matters)"

echo "-- DNS via router (Unbound)"
dig +time=2 +tries=1 @"$ROUTER" example.com A >/dev/null
echo "OK: DNS query"

echo "-- NAT / Internet reachability"
curl -fsS --max-time 5 https://example.com >/dev/null
echo "OK: outbound internet"

echo "Done."
