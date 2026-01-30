#!/usr/bin/env bash
set -euo pipefail

WAN_IF="${WAN_IF-}"
UPLINK_IF="${UPLINK_IF-}"

have_iface() {
  ip link show dev "$1" >/dev/null 2>&1
}

list_ifaces() {
  ip -o link show | awk -F': ' '{print $2}' | grep -v '^lo$' || true
}

get_upstream_dns() {
  local out=()
  local extract_ips
  extract_ips() {
    grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}|([0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]+' || true
  }
  if command -v resolvectl >/dev/null 2>&1; then
    if [[ -n "${UPLINK_IF:-}" ]]; then
      while read -r tok; do
        [[ -z "$tok" ]] && continue
        out+=("$tok")
      done < <(resolvectl dns "$UPLINK_IF" 2>/dev/null | extract_ips)
    fi
    if [[ ${#out[@]} -eq 0 ]]; then
      while read -r tok; do
        [[ -z "$tok" ]] && continue
        out+=("$tok")
      done < <(resolvectl dns 2>/dev/null | extract_ips)
    fi
  fi
  if [[ ${#out[@]} -eq 0 ]] && [[ -f /etc/resolv.conf ]]; then
    while read -r line; do
      [[ "$line" =~ ^nameserver[[:space:]]+ ]] || continue
      out+=("$(echo "$line" | awk '{print $2}')")
    done < /etc/resolv.conf
  fi
  # Filter duplicates and local stubs
  local uniq=()
  for s in "${out[@]}"; do
    [[ -z "$s" ]] && continue
    [[ "$s" == "127.0.0.53" || "$s" == "127.0.0.1" || "$s" == "::1" ]] && continue
    if [[ ! " ${uniq[*]} " =~ " ${s} " ]]; then uniq+=("$s"); fi
  done
  echo "${uniq[@]}"
}

default_route_iface() {
  ip -o route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev"){print $(i+1); exit}}' || true
}

WAN_IF_WAS_SET=0
UPLINK_IF_WAS_SET=0
[[ -n "${WAN_IF}" ]] && WAN_IF_WAS_SET=1
[[ -n "${UPLINK_IF}" ]] && UPLINK_IF_WAS_SET=1

# Sensible defaults (Ubuntu often uses ens* instead of eth*).
if [[ $UPLINK_IF_WAS_SET -eq 0 ]]; then
  UPLINK_IF="$(default_route_iface)"
fi
if [[ -z "${UPLINK_IF}" ]]; then
  UPLINK_IF="eth1"
fi
if [[ $WAN_IF_WAS_SET -eq 0 ]]; then
  WAN_IF="eth0"
fi

if [[ $UPLINK_IF_WAS_SET -eq 1 ]] && ! have_iface "$UPLINK_IF"; then
  echo "ERROR: UPLINK_IF='${UPLINK_IF}' not found. Available interfaces:" >&2
  list_ifaces >&2
  echo "Fix: set UPLINK_IF to your Internet/NAT NIC (usually the one with the default route)." >&2
  exit 2
fi
if [[ $WAN_IF_WAS_SET -eq 1 ]] && ! have_iface "$WAN_IF"; then
  echo "ERROR: WAN_IF='${WAN_IF}' not found. Available interfaces:" >&2
  list_ifaces >&2
  echo "Fix: set WAN_IF to your WAN-LAB NIC." >&2
  exit 2
fi

# If defaults don't exist, auto-pick based on the default-route uplink.
if ! have_iface "$UPLINK_IF"; then
  guess="$(default_route_iface)"
  if [[ -n "$guess" ]] && have_iface "$guess"; then
    UPLINK_IF="$guess"
  fi
fi
if ! have_iface "$WAN_IF"; then
  # pick the first non-lo iface that's not the uplink
  while read -r ifn; do
    [[ -z "$ifn" ]] && continue
    if [[ "$ifn" != "$UPLINK_IF" ]]; then
      WAN_IF="$ifn"
      break
    fi
  done < <(list_ifaces)
fi

if ! have_iface "$WAN_IF" || ! have_iface "$UPLINK_IF" || [[ "$WAN_IF" == "$UPLINK_IF" ]]; then
  echo "ERROR: could not determine WAN_IF/UPLINK_IF." >&2
  echo "Detected interfaces:" >&2
  list_ifaces >&2
  echo "Suggested:" >&2
  echo "  WAN_IF=<wanlab-nic> UPLINK_IF=<nat-nic> sudo ./isp-sim-setup.sh" >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y dnsmasq nftables

echo "== Configuring ISP simulator: WAN_IF=${WAN_IF} (WAN-LAB) UPLINK_IF=${UPLINK_IF} (uplink) =="

# Persist interface choices for reboot.
sudo tee /etc/auroragw-isp-sim.env >/dev/null <<EOF
WAN_IF=${WAN_IF}
UPLINK_IF=${UPLINK_IF}
EOF
sudo chmod 600 /etc/auroragw-isp-sim.env || true

sudo ip addr add 10.0.2.1/24 dev "$WAN_IF" || true
sudo ip link set "$WAN_IF" up

UPSTREAM_DNS=($(get_upstream_dns))
if [[ ${#UPSTREAM_DNS[@]} -eq 0 ]]; then
  UPSTREAM_DNS=("1.1.1.1" "9.9.9.9")
fi

sudo tee /etc/dnsmasq.d/wanlab.conf >/dev/null <<EOF
interface=${WAN_IF}
listen-address=10.0.2.1
bind-interfaces
no-resolv
# Some lab networks (including certain NAT setups) block outbound UDP/53 but allow TCP/53.
# Force upstream queries over TCP so downstream clients can resolve reliably.
force-tcp
$(for s in "${UPSTREAM_DNS[@]}"; do echo "server=${s}"; done)
dhcp-range=10.0.2.10,10.0.2.200,255.255.255.0,12h
dhcp-option=3,10.0.2.1
dhcp-option=6,10.0.2.1
EOF

sudo systemctl enable --now dnsmasq
sudo systemctl restart dnsmasq

echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-wanlab.conf >/dev/null
sudo sysctl --system >/dev/null

sudo nft flush ruleset || true
sudo nft add table ip nat
sudo nft 'add chain ip nat postrouting { type nat hook postrouting priority 100 ; }'
sudo nft add rule ip nat postrouting oifname "$UPLINK_IF" masquerade

sudo tee /usr/local/sbin/auroragw-isp-sim-apply.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/etc/auroragw-isp-sim.env"
WAN_IF=""
UPLINK_IF=""

have_iface() { ip link show dev "$1" >/dev/null 2>&1; }
list_ifaces() { ip -o link show | awk -F': ' '{print $2}' | grep -v '^lo$' || true; }
default_route_iface() { ip -o route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev"){print $(i+1); exit}}' || true; }

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE" || true
fi

WAN_IF="${WAN_IF:-}"
UPLINK_IF="${UPLINK_IF:-}"

if [[ -z "$UPLINK_IF" ]]; then
  UPLINK_IF="$(default_route_iface)"
fi

if [[ -z "$WAN_IF" ]]; then
  while read -r ifn; do
    [[ -z "$ifn" ]] && continue
    if [[ "$ifn" != "$UPLINK_IF" ]]; then
      WAN_IF="$ifn"
      break
    fi
  done < <(list_ifaces)
fi

if ! have_iface "$WAN_IF" || ! have_iface "$UPLINK_IF" || [[ "$WAN_IF" == "$UPLINK_IF" ]]; then
  echo "ERROR: auroragw-isp-sim-apply could not determine interfaces." >&2
  echo "Detected interfaces:" >&2
  list_ifaces >&2
  echo "Have env file? $ENV_FILE" >&2
  exit 2
fi

echo "== AuroraGW ISP-sim apply: WAN_IF=${WAN_IF} UPLINK_IF=${UPLINK_IF} =="

ip link set "$WAN_IF" up || true
ip addr add 10.0.2.1/24 dev "$WAN_IF" 2>/dev/null || true

echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-wanlab.conf
sysctl --system >/dev/null || true

nft flush ruleset || true
nft add table ip nat
nft 'add chain ip nat postrouting { type nat hook postrouting priority 100 ; }'
nft add rule ip nat postrouting oifname "$UPLINK_IF" masquerade

systemctl enable --now dnsmasq >/dev/null 2>&1 || true
systemctl restart dnsmasq >/dev/null 2>&1 || true
EOF
sudo chmod 755 /usr/local/sbin/auroragw-isp-sim-apply.sh

sudo tee /etc/systemd/system/auroragw-isp-sim.service >/dev/null <<'EOF'
[Unit]
Description=AuroraGW VMware ISP simulator (WAN-LAB DHCP + NAT)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/auroragw-isp-sim-apply.sh

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now auroragw-isp-sim.service

echo "ISP simulator ready."
