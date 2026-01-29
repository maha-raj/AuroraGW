#!/usr/bin/env bash
set -euo pipefail

ROLE=""
IFACE=""
MODE="static"   # static | dhcp
PERSIST=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="${2:-}"; shift 2 ;;
    --iface) IFACE="${2:-}"; shift 2 ;;
    --mode) MODE="${2:-}"; shift 2 ;;
    --persist) PERSIST=1; shift ;;
    *) echo "Usage: $0 --role lan|opt1 [--iface eth0] [--mode static|dhcp] [--persist]"; exit 1 ;;
  esac
done

[[ -n "$ROLE" ]] || { echo "Missing --role"; exit 1; }
[[ "$ROLE" == "lan" || "$ROLE" == "opt1" ]] || { echo "--role must be lan or opt1"; exit 1; }
[[ "$MODE" == "static" || "$MODE" == "dhcp" ]] || { echo "--mode must be static or dhcp"; exit 1; }

if [[ -z "$IFACE" ]]; then
  IFACE="$(ip -o link | awk -F': ' '{print $2}' | grep -Ev '^(lo|docker0|virbr0)$' | head -n1)"
fi
[[ -n "$IFACE" ]] || { echo "Could not auto-detect interface. Pass --iface."; exit 1; }

sudo apt-get update
sudo apt-get install -y curl ca-certificates iproute2 iputils-ping dnsutils netcat-openbsd jq openssl

IP_CIDR="192.168.101.10/24"
GW="192.168.101.1"
DNS="192.168.101.1"
if [[ "$ROLE" == "opt1" ]]; then
  IP_CIDR="192.168.102.10/24"
  GW="192.168.102.1"
  DNS="192.168.102.1"
fi

if [[ $PERSIST -eq 1 ]]; then
  echo "Writing persistent netplan for $IFACE ($MODE) ..."
  sudo mkdir -p /etc/netplan
  if [[ "$MODE" == "dhcp" ]]; then
    sudo tee /etc/netplan/99-auroragw-client.yaml >/dev/null <<EOF
network:
  version: 2
  renderer: networkd
  ethernets:
    ${IFACE}:
      dhcp4: true
      optional: true
EOF
  else
    sudo tee /etc/netplan/99-auroragw-client.yaml >/dev/null <<EOF
network:
  version: 2
  renderer: networkd
  ethernets:
    ${IFACE}:
      dhcp4: false
      addresses: [${IP_CIDR}]
      routes:
        - to: default
          via: ${GW}
      nameservers:
        addresses: [${DNS}]
      optional: true
EOF
  fi
  sudo netplan apply
else
  sudo ip addr flush dev "$IFACE" || true
  sudo ip link set "$IFACE" up
  if [[ "$MODE" == "dhcp" ]]; then
    if command -v dhclient >/dev/null 2>&1; then
      sudo dhclient -r "$IFACE" 2>/dev/null || true
      sudo dhclient -v "$IFACE" || true
    else
      echo "dhclient not installed; rely on systemd-networkd/NetworkManager for DHCP."
    fi
  else
    sudo ip addr add "$IP_CIDR" dev "$IFACE"
    sudo ip route replace default via "$GW"
  fi
fi

echo "Configured $IFACE:"
ip -br addr show dev "$IFACE"
ip route
