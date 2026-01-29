#!/usr/bin/env bash
set -euo pipefail

# PPPoE server for lab testing only.
# Topology:
# - NIC1: WAN-LAB (to AuroraGW WAN NIC)
# - NIC2: NAT/Internet (uplink)

WANLAB_IF="${WANLAB_IF-}"
UPLINK_IF="${UPLINK_IF-}"

have_iface() {
  ip link show dev "$1" >/dev/null 2>&1
}

list_ifaces() {
  ip -o link show | awk -F': ' '{print $2}' | grep -v '^lo$' || true
}

default_route_iface() {
  ip -o route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev"){print $(i+1); exit}}' || true
}

WANLAB_IF_WAS_SET=0
UPLINK_IF_WAS_SET=0
[[ -n "${WANLAB_IF}" ]] && WANLAB_IF_WAS_SET=1
[[ -n "${UPLINK_IF}" ]] && UPLINK_IF_WAS_SET=1

if [[ $UPLINK_IF_WAS_SET -eq 0 ]]; then
  UPLINK_IF="$(default_route_iface)"
fi
if [[ -z "${UPLINK_IF}" ]]; then
  UPLINK_IF="eth1"
fi
if [[ $WANLAB_IF_WAS_SET -eq 0 ]]; then
  WANLAB_IF="eth0"
fi

if [[ $UPLINK_IF_WAS_SET -eq 1 ]] && ! have_iface "$UPLINK_IF"; then
  echo "ERROR: UPLINK_IF='${UPLINK_IF}' not found. Available interfaces:" >&2
  list_ifaces >&2
  echo "Fix: set UPLINK_IF to your Internet/NAT NIC (usually the one with the default route)." >&2
  exit 2
fi
if [[ $WANLAB_IF_WAS_SET -eq 1 ]] && ! have_iface "$WANLAB_IF"; then
  echo "ERROR: WANLAB_IF='${WANLAB_IF}' not found. Available interfaces:" >&2
  list_ifaces >&2
  echo "Fix: set WANLAB_IF to your WAN-LAB NIC." >&2
  exit 2
fi

if ! have_iface "$UPLINK_IF"; then
  guess="$(default_route_iface)"
  if [[ -n "$guess" ]] && have_iface "$guess"; then
    UPLINK_IF="$guess"
  fi
fi
if ! have_iface "$WANLAB_IF"; then
  while read -r ifn; do
    [[ -z "$ifn" ]] && continue
    if [[ "$ifn" != "$UPLINK_IF" ]]; then
      WANLAB_IF="$ifn"
      break
    fi
  done < <(list_ifaces)
fi

if ! have_iface "$WANLAB_IF" || ! have_iface "$UPLINK_IF" || [[ "$WANLAB_IF" == "$UPLINK_IF" ]]; then
  echo "ERROR: could not determine WANLAB_IF/UPLINK_IF." >&2
  echo "Detected interfaces:" >&2
  list_ifaces >&2
  echo "Suggested:" >&2
  echo "  WANLAB_IF=<wanlab-nic> UPLINK_IF=<nat-nic> sudo ./pppoe-server-setup.sh" >&2
  exit 2
fi

PPPOE_USER="${PPPOE_USER:-testuser}"
PPPOE_PASS="${PPPOE_PASS:-testpass}"

LOCAL_IP="${LOCAL_IP:-10.0.3.1}"
REMOTE_START="${REMOTE_START:-10.0.3.10}"
REMOTE_END="${REMOTE_END:-10.0.3.200}"
SESSION_COUNT="${SESSION_COUNT:-}"

sudo apt-get update
sudo apt-get install -y ppp rp-pppoe nftables

echo "== Configuring PPPoE server on $WANLAB_IF (uplink $UPLINK_IF) =="

sudo ip link set "$WANLAB_IF" up || true
sudo ip link set "$UPLINK_IF" up || true

sudo tee /etc/ppp/pppoe-server-options >/dev/null <<EOF
require-chap
login
lcp-echo-interval 10
lcp-echo-failure 3
ms-dns 1.1.1.1
ms-dns 9.9.9.9
EOF

# Allow both CHAP and PAP (client uses CHAP in AuroraGW).
sudo python3 - <<PY
from pathlib import Path
user = ${PPPOE_USER@Q}
pw = ${PPPOE_PASS@Q}
line = f'"{user}" * "{pw}" *'
for p in (Path("/etc/ppp/chap-secrets"), Path("/etc/ppp/pap-secrets")):
    p.parent.mkdir(parents=True, exist_ok=True)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if any(l.startswith(f'"{user}"') for l in text.splitlines()):
        continue
    p.write_text(text + line + "\n", encoding="utf-8")
PY
sudo chmod 600 /etc/ppp/chap-secrets /etc/ppp/pap-secrets || true

echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-pppoe-server.conf >/dev/null
sudo sysctl --system >/dev/null

sudo nft flush ruleset || true
sudo nft add table ip nat
sudo nft 'add chain ip nat postrouting { type nat hook postrouting priority 100 ; }'
sudo nft add rule ip nat postrouting oifname "$UPLINK_IF" masquerade

echo "Starting pppoe-server..."
sudo pkill -f "pppoe-server.*${WANLAB_IF}" 2>/dev/null || true
if [[ -z "$SESSION_COUNT" ]]; then
  SESSION_COUNT="$(python3 - <<PY
import ipaddress
start=ipaddress.ip_address("${REMOTE_START}")
end=ipaddress.ip_address("${REMOTE_END}")
print(int(end) - int(start) + 1 if int(end) >= int(start) else 1)
PY
)"
fi
sudo pppoe-server -I "$WANLAB_IF" -L "$LOCAL_IP" -R "$REMOTE_START" -N "$SESSION_COUNT" || true

cat <<EOF

PPPoE server is up (lab).
Credentials:
  username: ${PPPOE_USER}
  password: ${PPPOE_PASS}

On AuroraGW, set:
  wan.mode: pppoe
  wan.pppoe.username: ${PPPOE_USER}
  wan.pppoe.password: ${PPPOE_PASS}

EOF
