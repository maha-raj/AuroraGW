# AuroraGW (Full Build Kit v1.0)

This ZIP is a **complete build kit** for a pfSense-like Ubuntu router/firewall appliance you can test in **VMware** and later deploy on bare metal.

Included (implemented, not “piecemeal”):
- Interactive **installer / reconfigure** script (LAN-only web UI by default)
- YAML config model (dynamic segments, VLAN-ready)
- Control plane: `auroragd` (validate/apply/confirm rollback/backup/restore)
- Web UI (LAN-only by default): status, config editor, apply+confirm, backups, logs
- nftables firewall + NAT + port forwards
- WAN: DHCP bootstrap OR PPPoE (pppd)
- DHCP: **Kea**
- DNS: **Unbound**
- QoS/SQM: CAKE + IFB ingress for PPPoE (optional) + DSCP hooks + auto-rate wizard
- Home compatibility: UPnP (miniupnpd nftables backend) + mDNS/SSDP relay (Chromecast/FireTV/Samsung TV discovery across segments)
- IDS: Suricata optional (installed, OFF by default unless enabled)
- Monitoring + self-heal: periodic health checks and service restarts
- Logging/auditing: audit log + journald access in UI
- Updates: “safe update” helper (backup then apt upgrade)

> Security defaults: WAN inbound blocked; web/ssh allowed only from LAN; TLS on UI (self-signed).

## Quick start (VMware)

### Suggested VMware NIC layout
- NIC1 = WAN-LAB
- NIC2 = LAN
- NIC3 = OPT1

### Install
On a fresh Ubuntu Server:
```bash
cd auroragw
sudo ./install/auroragw-install.sh
```

After install:
- Web UI (LAN-only): `https://<LAN-IP>:8443/`  (self-signed cert)
- Login: HTTP Basic `admin` + the password you set in installer

Optional:
- Cockpit system console (LAN-only, if enabled in installer): `https://<LAN-IP>:9090/`

### Reconfigure later
```bash
sudo /opt/auroragw/install/auroragw-install.sh --reconfigure
```

### Safe apply model
Installer applies a **pending** config requiring confirmation:
- Confirm in UI (Status page → Confirm) **within 120s**
- Or:
```bash
sudo auroragd confirm
```
If you don’t confirm, the rollback timer restores last-known-good configs.

## Where things live
- Active config: `/etc/auroragw/config.yaml`
- Staging config: `/etc/auroragw/config.staging.yaml`
- Audit log: `/var/log/auroragw/audit.log`
- Backups: `/var/lib/auroragw/backups/`

## Docs
- Product spec: `docs/AuroraGW-Master-Plan.md`
- Implementation plan: `docs/PROJECT_PLAN.md`
- Home usage how-to: `docs/HOWTO.md`
- VMware lab guide: `docs/VMWARE_LAB.md`
