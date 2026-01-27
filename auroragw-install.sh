#!/usr/bin/env bash
set -euo pipefail

need_root() { [[ $EUID -eq 0 ]] || { echo "Run as root."; exit 1; }; }
need_root

echo "== AuroraGW installer (starter build) =="

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  nftables dnsmasq ppp rp-pppoe iproute2 iputils-ping tcpdump \
  avahi-daemon \
  python3 python3-yaml python3-jsonschema \
  miniupnpd-nftables || true

if command -v whiptail >/dev/null 2>&1; then UI=whiptail; else UI=plain; fi

INSTALL_UNBOUND=0
INSTALL_KEA=0
INSTALL_SURICATA=0


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

WAN_MAC=$(pick_mac "WAN interface")
LAN_MAC=$(pick_mac "LAN interface")
OPT1_MAC=$(pick_mac "OPT1 interface")

LAN_ADDR="192.168.101.1/24"
OPT1_ADDR="192.168.102.1/24"

if [[ "$UI" == "whiptail" ]]; then
  LAN_ADDR=$(whiptail --inputbox "LAN address/CIDR" 10 78 "$LAN_ADDR" 3>&1 1>&2 2>&3)
  OPT1_ADDR=$(whiptail --inputbox "OPT1 address/CIDR" 10 78 "$OPT1_ADDR" 3>&1 1>&2 2>&3)
else
  read -r -p "LAN address/CIDR [$LAN_ADDR]: " x; [[ -n "${x:-}" ]] && LAN_ADDR="$x"
  read -r -p "OPT1 address/CIDR [$OPT1_ADDR]: " x; [[ -n "${x:-}" ]] && OPT1_ADDR="$x"
fi

WAN_MODE="dhcp"
if [[ "$UI" == "whiptail" ]]; then
  WAN_MODE=$(whiptail --title "WAN mode" --menu "Select WAN mode" 15 70 3 \
    "dhcp" "Bootstrap DHCP" \
    "pppoe" "PPPoE (requires creds)" 3>&1 1>&2 2>&3)
else
  read -r -p "WAN mode [dhcp/pppoe] (default dhcp): " x; [[ -n "${x:-}" ]] && WAN_MODE="$x"
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

MDNS_REFLECT=1
UPNP_LAN=1
UPNP_OPT1=0

if [[ "$UI" == "whiptail" ]]; then
  whiptail --yesno "Install Unbound (optional DNS resolver module)?" 10 78 && INSTALL_UNBOUND=1 || INSTALL_UNBOUND=0
  whiptail --yesno "Install Kea DHCP (optional DHCP module)?" 10 78 && INSTALL_KEA=1 || INSTALL_KEA=0
  whiptail --yesno "Install Suricata (optional IDS module; disabled by default)?" 10 78 && INSTALL_SURICATA=1 || INSTALL_SURICATA=0
fi

OPT_PKGS=()
[[ $INSTALL_UNBOUND -eq 1 ]] && OPT_PKGS+=("unbound")
[[ $INSTALL_KEA -eq 1 ]] && OPT_PKGS+=("kea-dhcp4-server" "kea-dhcp6-server")
[[ $INSTALL_SURICATA -eq 1 ]] && OPT_PKGS+=("suricata")
if [[ ${#OPT_PKGS[@]} -gt 0 ]]; then
  apt-get install -y --no-install-recommends "${OPT_PKGS[@]}" || true
fi

if [[ "$UI" == "whiptail" ]]; then
  whiptail --yesno "Enable mDNS reflector LAN↔OPT1 (AirPrint/Chromecast discovery)?" 10 78 && MDNS_REFLECT=1 || MDNS_REFLECT=0
  whiptail --yesno "Enable UPnP on LAN?" 10 78 && UPNP_LAN=1 || UPNP_LAN=0
  whiptail --yesno "Also enable UPnP on OPT1? (optional)" 10 78 && UPNP_OPT1=1 || UPNP_OPT1=0
fi

install -d /opt/auroragw
rsync -a --delete ./ /opt/auroragw/ --exclude ".git" --exclude "*.zip" >/dev/null 2>&1 || cp -a ./ /opt/auroragw/
ln -sf /opt/auroragw/auroragd/auroragd.py /usr/local/sbin/auroragd

install -d /etc/systemd/system
cp -f /opt/auroragw/systemd/auroragd-status.service /etc/systemd/system/auroragd-status.service
cp -f /opt/auroragw/systemd/auroragw-pppoe.service /etc/systemd/system/auroragw-pppoe.service 2>/dev/null || true
cp -f /opt/auroragw/systemd/auroragw-suricata@.service /etc/systemd/system/auroragw-suricata@.service 2>/dev/null || true
systemctl daemon-reload

install -d /etc/auroragw

UPNP_SEGMENTS="[]"
if [[ $UPNP_LAN -eq 1 && $UPNP_OPT1 -eq 1 ]]; then UPNP_SEGMENTS='[lan, opt1]';
elif [[ $UPNP_LAN -eq 1 ]]; then UPNP_SEGMENTS='[lan]';
elif [[ $UPNP_OPT1 -eq 1 ]]; then UPNP_SEGMENTS='[opt1]'; fi

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
    dhcp: { range_start: 192.168.101.100, range_end: 192.168.101.200 }

  - id: opt1
    label: OPT1
    ifref: opt1
    address: ${OPT1_ADDR}
    dhcp: { range_start: 192.168.102.100, range_end: 192.168.102.200 }

wan:
  mode: ${WAN_MODE}
  pppoe: { username: "${PPPOE_USER}", password: "${PPPOE_PASS}", mtu: 1492 }

services:
  dnsmasq: { enabled: true }
  mdns: { enabled: true, reflector: { enabled: $( [[ $MDNS_REFLECT -eq 1 ]] && echo true || echo false ) } }
  upnp: { enabled: $( [[ $UPNP_LAN -eq 1 || $UPNP_OPT1 -eq 1 ]] && echo true || echo false ), segments_enabled: ${UPNP_SEGMENTS} }
EOF

auroragd validate /etc/auroragw/config.staging.yaml
auroragd apply --commit /etc/auroragw/config.staging.yaml

systemctl enable --now auroragd-status.service

echo "Done. Status: http://$(echo "$LAN_ADDR" | cut -d/ -f1):8080/status"
