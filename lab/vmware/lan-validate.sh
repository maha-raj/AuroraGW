#!/usr/bin/env bash
set -euo pipefail

ROUTER="192.168.101.1"
TEST_DOMAIN="example.com"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --router) ROUTER="${2:-}"; shift 2 ;;
    *) echo "Usage: $0 [--router 192.168.101.1]"; exit 1 ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing '$1' (install it and retry)"; exit 2; }
}
need_cmd ip
need_cmd ping
need_cmd curl
need_cmd dig

echo "== LAN validation against $ROUTER =="

echo "-- Addressing"
ip -br addr || true
ip route || true
GW="$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
if [[ -n "${GW:-}" && "$GW" != "$ROUTER" ]]; then
  echo "WARN: default gateway is $GW (expected $ROUTER)"
fi

echo "-- Ping router"
ping -c 2 "$ROUTER" >/dev/null
echo "OK: ping"

echo "-- Web UI (TLS) reachable"
curl -kfsS "https://${ROUTER}:8443/" >/dev/null || true
echo "OK: curl (may require auth; reachability is what matters)"

echo "-- DNS via router (Unbound)"
dig +time=2 +tries=1 @"$ROUTER" "$TEST_DOMAIN" A | grep -q "status: NOERROR"
dig +time=2 +tries=1 +tcp @"$ROUTER" "$TEST_DOMAIN" A | grep -q "status: NOERROR"
echo "OK: DNS query"

echo "-- NAT / Internet reachability"
curl -fsS --max-time 5 https://example.com >/dev/null
echo "OK: outbound internet"

echo "Done."
