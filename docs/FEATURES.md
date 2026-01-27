# Features in this build

- WAN: DHCP bootstrap or PPPoE
- Multi-segment LAN/OPT1 routing
- nftables: default deny inbound, NAT, port forwards, DSCP hooks
- DHCP: Kea
- DNS: Unbound
- QoS: CAKE (LAN egress + optional PPPoE WAN SQM via IFB)
- UPnP: miniupnpd (nft backend)
- Discovery relay: mDNS + SSDP between LAN segments
- Suricata: optional IDS
- Web UI: config + status + apply/confirm + backups + logs
- Backup/restore + safe apply rollback timer
