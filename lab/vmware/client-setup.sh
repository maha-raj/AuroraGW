#!/usr/bin/env bash
set -euo pipefail

ROLE=""
IFACE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="${2:-}"; shift 2 ;;
    --iface) IFACE="${2:-}"; shift 2 ;;
    *) echo "Usage: $0 --role lan|opt1 [--iface eth0]"; exit 1 ;;
  esac
done

[[ -n "$ROLE" ]] || { echo "Missing --role"; exit 1; }
[[ "$ROLE" == "lan" || "$ROLE" == "opt1" ]] || { echo "--role must be lan or opt1"; exit 1; }

if [[ -z "$IFACE" ]]; then
  IFACE="$(ip -o link | awk -F': ' '{print $2}' | grep -Ev '^(lo|docker0|virbr0)$' | head -n1)"
fi
[[ -n "$IFACE" ]] || { echo "Could not auto-detect interface. Pass --iface."; exit 1; }

sudo apt-get update
sudo apt-get install -y curl ca-certificates iproute2 iputils-ping dnsutils netcat-openbsd jq openssl

IP_CIDR="192.168.101.10/24"
GW="192.168.101.1"
if [[ "$ROLE" == "opt1" ]]; then
  IP_CIDR="192.168.102.10/24"
  GW="192.168.102.1"
fi

sudo ip addr flush dev "$IFACE" || true
sudo ip addr add "$IP_CIDR" dev "$IFACE"
sudo ip link set "$IFACE" up
sudo ip route replace default via "$GW"

echo "Configured $IFACE:"
ip -br addr show dev "$IFACE"
ip route
