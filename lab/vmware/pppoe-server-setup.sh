#!/usr/bin/env bash
set -euo pipefail

# PPPoE server for lab testing only.
# Topology:
# - NIC1: WAN-LAB (to AuroraGW WAN NIC)
# - NIC2: NAT/Internet (uplink)

WANLAB_IF="${WANLAB_IF:-eth0}"
UPLINK_IF="${UPLINK_IF:-eth1}"

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
sudo bash -lc "grep -q '^\"${PPPOE_USER}\"' /etc/ppp/chap-secrets 2>/dev/null || echo '\"${PPPOE_USER}\" * \"${PPPOE_PASS}\" *' >> /etc/ppp/chap-secrets"
sudo bash -lc "grep -q '^\"${PPPOE_USER}\"' /etc/ppp/pap-secrets 2>/dev/null || echo '\"${PPPOE_USER}\" * \"${PPPOE_PASS}\" *' >> /etc/ppp/pap-secrets"
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
