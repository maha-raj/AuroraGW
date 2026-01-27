#!/usr/bin/env bash
set -euo pipefail

WAN_IF="${WAN_IF:-eth0}"
UPLINK_IF="${UPLINK_IF:-eth1}"

sudo apt-get update
sudo apt-get install -y dnsmasq nftables

sudo ip addr add 10.0.2.1/24 dev "$WAN_IF" || true
sudo ip link set "$WAN_IF" up

sudo tee /etc/dnsmasq.d/wanlab.conf >/dev/null <<EOF
interface=${WAN_IF}
bind-interfaces
dhcp-range=10.0.2.10,10.0.2.200,255.255.255.0,12h
dhcp-option=3,10.0.2.1
dhcp-option=6,1.1.1.1,9.9.9.9
EOF

sudo systemctl enable --now dnsmasq
sudo systemctl restart dnsmasq

echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-wanlab.conf >/dev/null
sudo sysctl --system >/dev/null

sudo nft flush ruleset || true
sudo nft add table ip nat
sudo nft 'add chain ip nat postrouting { type nat hook postrouting priority 100 ; }'
sudo nft add rule ip nat postrouting oifname "$UPLINK_IF" masquerade

echo "ISP simulator ready."
