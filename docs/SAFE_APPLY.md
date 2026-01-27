# Safe apply + rollback (starter)

On apply, AuroraGW:
1) Validates YAML against schema
2) Backs up last-known-good rendered files under `/var/lib/auroragw/backup/<timestamp>/`
3) Renders & applies:
   - netplan
   - nftables
   - DHCP (Kea)
   - DNS (Unbound)
   - optional PPPoE (pppd peers + unit)
   - optional CAKE + IFB shaping
   - optional Suricata units
   - optional UPnP + discovery relay units
4) Health checks:
   - LAN IP still present
   - nftables + AuroraGW web UI are active
   - if WAN mode is PPPoE, `pppoe0` exists
5) If checks fail → rollback restored + services restarted.
