#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root."; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ACTION="${1:-}"
RECONFIGURE=0
if [[ "$ACTION" == "--reconfigure" || "$ACTION" == "reconfigure" ]]; then
  RECONFIGURE=1
fi
export DEBIAN_FRONTEND=noninteractive

apt_install_one_of() {
  # Usage: apt_install_one_of "human label" pkg1 pkg2 ...
  local label="$1"; shift
  local ok=1
  set +e
  for pkg in "$@"; do
    apt-get install -y --no-install-recommends "$pkg"
    if [[ $? -eq 0 ]]; then ok=0; break; fi
  done
  set -e
  if [[ $ok -ne 0 ]]; then
    echo "ERROR: could not install ${label}. Tried: $*" >&2
    echo "Tip (Ubuntu): ensure 'universe' is enabled in your apt sources, then run: sudo apt-get update" >&2
    return 1
  fi
}

have_pppoe_plugin() {
  compgen -G "/usr/lib/pppd/*/rp-pppoe.so" >/dev/null || compgen -G "/usr/lib/*/pppd/*/rp-pppoe.so" >/dev/null
}

if [[ $RECONFIGURE -eq 0 ]]; then
  apt-get update

  # Core packages (fail loudly if these can't be installed).
  apt-get install -y --no-install-recommends \
    ca-certificates curl jq \
    nftables iproute2 iputils-ping tcpdump ethtool \
    ppp \
    python3 python3-venv python3-pip python3-yaml python3-jsonschema \
    unbound \
    openssl rsync unzip

  # Kea DHCP server package naming varies by distro/repo.
  apt_install_one_of "Kea DHCP server" kea-dhcp4-server isc-kea kea

  # PPPoE client plugin package naming varies by distro/repo.
  # AuroraGW uses rp-pppoe.so via ppp; commonly provided by 'pppoe' or 'rp-pppoe'.
  apt_install_one_of "PPPoE plugin" pppoe rp-pppoe || true

  # Optional packages (best-effort; availability varies by distro/repo).
  apt-get install -y --no-install-recommends \
    whiptail cockpit \
    suricata \
    python3-netifaces \
    speedtest-cli \
    docker.io docker-compose-plugin \
    || true

  # Optional UPnP package naming varies; try nftables-optimized first.
  apt_install_one_of "miniupnpd" miniupnpd-nftables miniupnpd || true

  # Some distros need a version-specific venv package (ex: python3.13-venv).
  if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    PY_MM="$(python3 -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")' 2>/dev/null || true)"
    if [[ -n "$PY_MM" ]]; then
      apt-get install -y --no-install-recommends "python${PY_MM}-venv" || true
    fi
  fi
else
  echo "== Reconfigure mode: skipping apt install =="
fi

UI=plain
command -v whiptail >/dev/null 2>&1 && UI=whiptail

have_iface() {
  [[ -n "${1:-}" ]] && [[ -e "/sys/class/net/$1/address" ]]
}

iface_mac() {
  cat "/sys/class/net/$1/address" 2>/dev/null || true
}

iface_state() {
  cat "/sys/class/net/$1/operstate" 2>/dev/null || echo "unknown"
}

iface_speed() {
  local s
  # Some virtual drivers can block or be slow reading speed; keep this best-effort and fast.
  s="$(timeout 0.2s cat "/sys/class/net/$1/speed" 2>/dev/null || true)"
  [[ -n "$s" && "$s" != "-1" ]] && echo "${s}Mb" || echo "?Mb"
}

default_route_iface() {
  ip -o route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev"){print $(i+1); exit}}' || true
}

list_ifaces() {
  ip -o link show | awk -F': ' '{print $2}' | grep -v '^lo$' || true
}

list_nics() {
  list_ifaces | while read -r ifn; do
    [[ -z "$ifn" ]] && continue
    mac="$(iface_mac "$ifn")"
    [[ -z "$mac" ]] && continue
    # Keep this lightweight; speed is nice but can be slow on some drivers.
    echo "$ifn $mac $(iface_state "$ifn") $(iface_speed "$ifn")"
  done
}

pick_iface() {
  local title="$1"
  local default_if="$2"

  if [[ "$UI" == "whiptail" ]]; then
    local opts=()
    while read -r ifn mac st spd; do
      opts+=("$ifn" "${mac}  ${st}  ${spd}")
    done < <(list_nics)

    while true; do
      local choice=""
      if [[ -n "${default_if:-}" ]]; then
        choice="$(whiptail --title "$title" --ok-button "Select" --cancel-button "Exit" --default-item "${default_if}" --menu "$title (use arrows + Enter)" 20 78 10 "${opts[@]}" 3>&1 1>&2 2>&3)" || true
      else
        choice="$(whiptail --title "$title" --ok-button "Select" --cancel-button "Exit" --menu "$title (use arrows + Enter)" 20 78 10 "${opts[@]}" 3>&1 1>&2 2>&3)" || true
      fi

      if have_iface "$choice"; then
        echo "$choice"
        return 0
      fi

      if whiptail --yesno "No interface selected. Do you want to exit the installer?" 10 78; then
        exit 2
      fi
    done
  fi

  echo "== $title =="
  echo "Available interfaces:"
  local i=1
  local ifs=()
  while read -r ifn mac st spd; do
    ifs+=("$ifn")
    printf "  [%d] %s  %s  %s  %s\n" "$i" "$ifn" "$mac" "$st" "$spd"
    i=$((i+1))
  done < <(list_nics)

  if [[ "${#ifs[@]}" -eq 0 ]]; then
    echo "ERROR: no interfaces detected." >&2
    exit 2
  fi

  local prompt="Select interface"
  [[ -n "${default_if:-}" ]] && prompt="$prompt [default $default_if]"
  prompt="$prompt (number or name): "

  while true; do
    read -r -p "$prompt" ans
    ans="${ans:-}"
    if [[ -z "$ans" && -n "${default_if:-}" && "$(have_iface "$default_if" && echo ok || true)" == "ok" ]]; then
      echo "$default_if"
      return
    fi
    if [[ "$ans" =~ ^[0-9]+$ ]]; then
      local idx=$((ans-1))
      if [[ $idx -ge 0 && $idx -lt ${#ifs[@]} ]]; then
        echo "${ifs[$idx]}"
        return
      fi
    fi
    if have_iface "$ans"; then
      echo "$ans"
      return
    fi
    echo "Invalid selection. Try again." >&2
  done
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

DEF_WAN_IF="$(default_route_iface)"
WAN_IF="$(pick_iface "WAN interface (to ISP / WAN-LAB)" "${DEF_WAN_IF}")"
LAN_IF="$(pick_iface "LAN interface" "")"
OPT1_IF="$(pick_iface "OPT1 interface" "")"

if [[ "$WAN_IF" == "$LAN_IF" || "$WAN_IF" == "$OPT1_IF" || "$LAN_IF" == "$OPT1_IF" ]]; then
  echo "ERROR: WAN/LAN/OPT1 must be different interfaces." >&2
  echo "Selected: WAN=$WAN_IF LAN=$LAN_IF OPT1=$OPT1_IF" >&2
  exit 2
fi

WAN_MAC="$(iface_mac "$WAN_IF")"
LAN_MAC="$(iface_mac "$LAN_IF")"
OPT1_MAC="$(iface_mac "$OPT1_IF")"

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

  if ! have_pppoe_plugin; then
    echo "== PPPoE plugin not detected; attempting install =="
    if [[ $RECONFIGURE -eq 0 ]]; then
      apt_install_one_of "PPPoE plugin" pppoe rp-pppoe
    else
      echo "ERROR: PPPoE plugin missing. Install 'pppoe' (or 'rp-pppoe') and re-run." >&2
      exit 2
    fi
  fi
fi

install -d /opt/auroragw
if [[ ! -f "${REPO_DIR}/web/requirements.txt" ]]; then
  echo "ERROR: install source not found at ${REPO_DIR} (missing web/requirements.txt)" >&2
  echo "Tip: run from a git clone of AuroraGW (or re-run via /opt/auroragw/install/auroragw-install.sh)." >&2
  exit 2
fi
rsync -a --delete "${REPO_DIR}/" /opt/auroragw/ --exclude ".git" --exclude "*.zip" >/dev/null 2>&1 || cp -a "${REPO_DIR}/." /opt/auroragw/

python3 -m venv /opt/auroragw/venv
/opt/auroragw/venv/bin/pip install --upgrade pip >/dev/null
REQ_FILE="/opt/auroragw/web/requirements.txt"
if [[ ! -f "$REQ_FILE" ]]; then
  echo "ERROR: missing $REQ_FILE after install copy." >&2
  echo "Did you run the installer from the correct repo path? (It copies from the script location now.)" >&2
  exit 2
fi
/opt/auroragw/venv/bin/pip install -r "$REQ_FILE" >/dev/null

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
if [[ ${SURICATA_ENABLE:-0} -eq 1 ]]; then
  bash /opt/auroragw/scripts/fetch-evebox.sh || true
fi

# Ensure scripts are runnable even if checked out on a filesystem that drops exec bits.
chmod 755 /opt/auroragw/web/run.sh 2>/dev/null || true
chmod 755 /opt/auroragw/auroragd/auroragd.py 2>/dev/null || true
chmod 755 /opt/auroragw/scripts/*.sh 2>/dev/null || true
chmod 755 /opt/auroragw/discovery/*.sh 2>/dev/null || true

# Install an auroragd wrapper to avoid relying on exec permissions/noexec on /opt.
# Note: older installs created /usr/local/sbin/auroragd as a symlink to /opt/auroragw/auroragd/auroragd.py.
# If we overwrite that symlink with a shell wrapper, we'd corrupt the Python file. Remove first.
rm -f /usr/local/sbin/auroragd
cat > /usr/local/sbin/auroragd <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec python3 /opt/auroragw/auroragd/auroragd.py "$@"
EOF
chmod 755 /usr/local/sbin/auroragd

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
