#!/usr/bin/env bash
set -euo pipefail
WAN_PHY_IF="${1:-}"
PPPOE_IF="${2:-pppoe0}"
EGRESS_KBIT="${3:-}"
INGRESS_KBIT="${4:-}"
OVERHEAD="${5:-0}"
MPU="${6:-0}"
if [[ -z "$WAN_PHY_IF" || -z "$EGRESS_KBIT" || -z "$INGRESS_KBIT" ]]; then
  echo "Usage: $0 <WAN_PHY_IF> <PPPOE_IF> <EGRESS_KBIT> <INGRESS_KBIT> <OVERHEAD> <MPU>"
  exit 1
fi
modprobe ifb numifbs=1 || true
ip link show ifb0 >/dev/null 2>&1 || ip link add ifb0 type ifb
ip link set ifb0 up
tc qdisc del dev "$WAN_PHY_IF" ingress 2>/dev/null || true
tc qdisc del dev ifb0 root 2>/dev/null || true
tc qdisc del dev "$PPPOE_IF" root 2>/dev/null || true
tc qdisc add dev "$WAN_PHY_IF" handle ffff: ingress
tc filter add dev "$WAN_PHY_IF" parent ffff: protocol all u32 match u32 0 0 action mirred egress redirect dev ifb0
tc qdisc replace dev ifb0 root cake bandwidth "${INGRESS_KBIT}kbit" diffserv4 nat overhead "$OVERHEAD" mpu "$MPU"
tc qdisc replace dev "$PPPOE_IF" root cake bandwidth "${EGRESS_KBIT}kbit" diffserv4 nat overhead "$OVERHEAD" mpu "$MPU"
echo "QoS applied."
