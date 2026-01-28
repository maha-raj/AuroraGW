#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root."; exit 1; }
ACTION="${1:-}"
RECONFIGURE=0
if [[ "$ACTION" == "--reconfigure" || "$ACTION" == "reconfigure" ]]; then
  RECONFIGURE=1
fi
export DEBIAN_FRONTEND=noninteractive

if [[ $RECONFIGURE -eq 0 ]]; then
  apt-get update
  apt-get install -y --no-install-recommends \
    curl ca-certificates jq \
    nftables iproute2 iputils-ping tcpdump ethtool \
    ppp rp-pppoe \
    python3 python3-venv python3-pip python3-yaml python3-jsonschema \
  isc-kea unbound \
  miniupnpd-nftables \
  cockpit \
  unzip \
  python3-netifaces \
  speedtest-cli \
  docker.io docker-compose-plugin \
  || true
  apt-get install -y --no-install-recommends suricata openssl whiptail rsync || true
else
  echo "== Reconfigure mode: skipping apt install =="
fi

UI=plain
command -v whiptail >/dev/null 2>&1 && UI=whiptail

list_nics() {
  ip -o link | awk -F': ' '{print $2}' | while read -r ifn; do
    mac=$(cat /sys/class/net/"$ifn"/address 2>/dev/null || true)
    [[ -n "$mac" ]] && echo "$ifn $mac"
  done
}

pick_mac() {
  local title="$1"
  if [[ "$UI" == "whiptail" ]]; then
    local opts=()
    while read -r ifn mac; do opts+=("$mac" "$ifn"); done < <(list_nics)
    whiptail --title "$title" --menu "$title (select by MAC)" 20 78 10 "${opts[@]}" 3>&1 1>&2 2>&3
  else
    echo "$title"; list_nics
    read -r -p "Enter MAC for $title: " mac; echo "$mac"
  fi
}

ADMIN_PASS="admin"
if [[ $RECONFIGURE -eq 1 && -f /etc/auroragw/secrets.yaml ]]; then
  ADMIN_PASS="$(awk -F': ' '/^admin_password:/{gsub(/"/,"",$2); print $2}' /etc/auroragw/secrets.yaml 2>/dev/null || echo admin)"
  [[ -n "$ADMIN_PASS" ]] || ADMIN_PASS="admin"
fi
WAN_MODE="dhcp"
LAN_ADDR="192.168.101.1/24"
OPT1_ADDR="192.168.102.1/24"
UPNP_OPT1=0
DISCOVERY_RELAY=1
SURICATA_ENABLE=0
COCKPIT_ENABLE=0
MONITOR_MODE="basic"

WAN_MAC=$(pick_mac "WAN interface")
LAN_MAC=$(pick_mac "LAN interface")
OPT1_MAC=$(pick_mac "OPT1 interface")

if [[ "$UI" == "whiptail" ]]; then
  if [[ $RECONFIGURE -eq 1 ]]; then
    if whiptail --yesno "Change admin password for Web UI?" 10 78; then
      ADMIN_PASS=$(whiptail --passwordbox "Set admin password for Web UI (user: admin)" 10 78 "" 3>&1 1>&2 2>&3)
    fi
  else
    ADMIN_PASS=$(whiptail --passwordbox "Set admin password for Web UI (user: admin)" 10 78 "" 3>&1 1>&2 2>&3)
  fi
  LAN_ADDR=$(whiptail --inputbox "LAN address/CIDR" 10 78 "$LAN_ADDR" 3>&1 1>&2 2>&3)
  OPT1_ADDR=$(whiptail --inputbox "OPT1 address/CIDR" 10 78 "$OPT1_ADDR" 3>&1 1>&2 2>&3)
  WAN_MODE=$(whiptail --title "WAN mode" --menu "Select WAN mode" 15 70 3 \
    "dhcp" "Bootstrap DHCP" \
    "pppoe" "PPPoE (requires creds)" 3>&1 1>&2 2>&3)
  whiptail --yesno "Enable UPnP on OPT1 too? (LAN is enabled by default)" 10 78 && UPNP_OPT1=1 || UPNP_OPT1=0
  whiptail --yesno "Enable discovery relay (mDNS+SSDP) LAN<->OPT1 for TVs/Cast devices?" 10 78 && DISCOVERY_RELAY=1 || DISCOVERY_RELAY=0
  whiptail --yesno "Enable Suricata IDS now? (Installed; default OFF for performance)" 10 78 && SURICATA_ENABLE=1 || SURICATA_ENABLE=0
  whiptail --yesno "Enable Cockpit system console on LAN? (https://<LAN-IP>:9090)" 10 78 && COCKPIT_ENABLE=1 || COCKPIT_ENABLE=0
  MONITOR_MODE=$(whiptail --title "Traffic monitoring" --menu "Select monitoring mode" 15 70 3 \
    "off" "Disabled" \
    "basic" "Low-rate sampling (recommended)" \
    "advanced" "Higher-rate sampling + history (troubleshooting)" 3>&1 1>&2 2>&3) || MONITOR_MODE="basic"
else
  read -r -s -p "Admin password (user: admin) [default admin]: " x; echo; [[ -n "${x:-}" ]] && ADMIN_PASS="$x"
fi

PPPOE_USER=""; PPPOE_PASS=""
if [[ "$WAN_MODE" == "pppoe" ]]; then
  if [[ "$UI" == "whiptail" ]]; then
    PPPOE_USER=$(whiptail --inputbox "PPPoE username" 10 78 "" 3>&1 1>&2 2>&3)
    PPPOE_PASS=$(whiptail --passwordbox "PPPoE password" 10 78 "" 3>&1 1>&2 2>&3)
  else
    read -r -p "PPPoE username: " PPPOE_USER
    read -r -s -p "PPPoE password: " PPPOE_PASS; echo
  fi
fi

install -d /opt/auroragw
rsync -a --delete ./ /opt/auroragw/ --exclude ".git" --exclude "*.zip" >/dev/null 2>&1 || cp -a ./ /opt/auroragw/

python3 -m venv /opt/auroragw/venv
/opt/auroragw/venv/bin/pip install --upgrade pip >/dev/null
/opt/auroragw/venv/bin/pip install -r /opt/auroragw/web/requirements.txt >/dev/null

install -d /etc/auroragw/tls
if [[ ! -f /etc/auroragw/tls/cert.pem ]]; then
  openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
    -keyout /etc/auroragw/tls/key.pem -out /etc/auroragw/tls/cert.pem \
    -subj "/CN=auroragw.local" >/dev/null 2>&1 || true
fi

install -d /etc/auroragw
cat > /etc/auroragw/secrets.yaml <<EOF
admin_password: "${ADMIN_PASS}"
EOF
chmod 600 /etc/auroragw/secrets.yaml

UPNP_SEGMENTS="[lan]"
[[ $UPNP_OPT1 -eq 1 ]] && UPNP_SEGMENTS="[lan, opt1]"

cat > /etc/auroragw/config.staging.yaml <<EOF
interfaces:
  wan:  { mac: "${WAN_MAC}" }
  lan:  { mac: "${LAN_MAC}" }
  opt1: { mac: "${OPT1_MAC}" }

segments:
  - id: lan
    label: LAN
    ifref: lan
    address: ${LAN_ADDR}
    dhcp: { enabled: true, range_start: 192.168.101.100, range_end: 192.168.101.200 }

  - id: opt1
    label: OPT1
    ifref: opt1
    address: ${OPT1_ADDR}
    dhcp: { enabled: true, range_start: 192.168.102.100, range_end: 192.168.102.200 }

wan:
  mode: ${WAN_MODE}
  pppoe: { username: "${PPPOE_USER}", password: "${PPPOE_PASS}", mtu: 1492 }

services:
  dhcp: { provider: kea, enabled: true }
  dns:  { provider: unbound, enabled: true }
  upnp: { enabled: true, segments_enabled: ${UPNP_SEGMENTS} }
  discovery_relay: { enabled: $( [[ $DISCOVERY_RELAY -eq 1 ]] && echo true || echo false ), segments: [lan, opt1] }
  qos:
    enabled: true
    segments:
      lan:  { rate_kbit: 950000 }
      opt1: { rate_kbit: 950000 }
    wan:
      enabled: false
      egress_kbit: 2800000
      ingress_kbit: 2800000
      overhead_bytes: 34
      mpu_bytes: 64
    dscp_rules: []
  wireguard: { enabled: false }
  suricata: { installed: true, enabled: $( [[ $SURICATA_ENABLE -eq 1 ]] && echo true || echo false ), interfaces: [lan] }
  cockpit: { enabled: $( [[ $COCKPIT_ENABLE -eq 1 ]] && echo true || echo false ) }
  monitoring: { mode: ${MONITOR_MODE} }
  evebox: { enabled: false }
  grafana: { enabled: false, mode: remote, remote_url: "" }

firewall:
  port_forwards: []
EOF

bash /opt/auroragw/discovery/fetch_multicast_relay.sh || true
bash /opt/auroragw/scripts/fetch-evebox.sh || true
ln -sf /opt/auroragw/auroragd/auroragd.py /usr/local/sbin/auroragd

cp -f /opt/auroragw/systemd/* /etc/systemd/system/ || true
systemctl daemon-reload

systemctl enable --now auroragw-web.service || true
systemctl enable --now nftables || true
systemctl enable --now auroragw-health.timer || true

auroragd validate /etc/auroragw/config.staging.yaml
auroragd apply --require-confirm --timeout 120 --commit /etc/auroragw/config.staging.yaml

echo "Install complete."
echo "Web UI: https://$(echo "$LAN_ADDR" | cut -d/ -f1):8443/ (user: admin)"
echo "Confirm within 120s: UI -> Confirm, or: sudo auroragd confirm"
