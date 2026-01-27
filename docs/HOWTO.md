# AuroraGW How-To (Home pfSense Replacement)

This guide targets your home layout:
- **LAN** (PCs/consoles): `192.168.101.0/24`
- **OPT1** (IoT/printers/TVs): `192.168.102.0/24`

Defaults (recommended for home security):
- **LAN → OPT1 allowed**
- **OPT1 → LAN blocked** (except discovery traffic when you enable discovery relay)

## 1) Install (Ubuntu Server VM or bare metal)

On a fresh Ubuntu Server:
```bash
git clone https://github.com/maha-raj/AuroraGW.git
cd AuroraGW
sudo ./install/auroragw-install.sh
```

Installer prompts:
- Select NICs by **MAC** for `wan/lan/opt1`
- Choose WAN mode: `dhcp` (bootstrap) or `pppoe`
- Enable/disable: UPnP on OPT1, discovery relay (mDNS+SSDP), Suricata IDS, Cockpit

After install:
- Web UI: `https://<LAN-IP>:8443/` (self-signed)
- Optional Cockpit (if enabled): `https://<LAN-IP>:9090/`

## 2) Reconfigure later

```bash
sudo /opt/auroragw/install/auroragw-install.sh --reconfigure
```

## 3) Web UI basics

- `/config`: edits **staging** YAML (`/etc/auroragw/config.staging.yaml`)
- `/status`: Apply/Confirm
  - “Apply staging” runs a safe apply and (by default) requires confirmation within 120s
  - “Confirm” commits the pending apply
- `/firewall`: add/remove **port forwards** (WAN → LAN/OPT1)
- `/suricata`: enable/disable IDS + select interfaces (then Apply/Confirm)
- `/traffic`: interface rates/counters (optional; see section 11)

## 4) WAN mode: DHCP ↔ PPPoE

Edit staging config:
```yaml
wan:
  mode: pppoe
  pppoe:
    username: "your-isp-user"
    password: "your-isp-pass"
    mtu: 1492
```

Then Apply + Confirm in `/status`.

PPPoE service:
```bash
systemctl status auroragw-pppoe.service
journalctl -u auroragw-pppoe.service -e
```

## 5) Gaming (UPnP / NAT-PMP / PCP)

Enable UPnP (LAN default; OPT1 optional) in config:
```yaml
services:
  upnp:
    enabled: true
    segments_enabled: [lan]
```

Apply + Confirm. MiniUPnP service:
```bash
systemctl status miniupnpd
```

## 6) Printer + IoT discovery across LAN ↔ OPT1

Enable discovery relay (mDNS + SSDP) **only** if you need it:
```yaml
services:
  discovery_relay:
    enabled: true
    segments: [lan, opt1]
```

This keeps OPT1 isolated, but allows limited OPT1 → LAN discovery traffic required for reflection.

## 7) Suricata (IDS, not inline IPS)

Enable Suricata IDS on selected interfaces (example: OPT1 only):
```yaml
services:
  suricata:
    installed: true
    enabled: true
    interfaces: [opt1]
```

Apply + Confirm.

Check status/logs:
```bash
systemctl status "auroragw-suricata@*.service"
journalctl -u "auroragw-suricata@*.service" -e
ls -la /var/log/suricata 2>/dev/null || true
```

## 8) QoS (CAKE)

Segment shaping (typical home):
```yaml
services:
  qos:
    enabled: true
    segments:
      lan:  { rate_kbit: 950000 }
      opt1: { rate_kbit: 950000 }
```

Optional WAN SQM for PPPoE (IFB ingress) when in PPPoE mode:
```yaml
services:
  qos:
    wan:
      enabled: true
      egress_kbit: 2800000
      ingress_kbit: 2800000
      overhead_bytes: 34
      mpu_bytes: 64
```

Apply + Confirm, then inspect:
```bash
tc -s qdisc
```

## 9) Backup / restore

Create a backup:
```bash
sudo auroragd backup
```

Restore a backup:
```bash
sudo auroragd restore /var/lib/auroragw/backups/<file>.tgz
```

## 10) Quick smoke test (on AuroraGW)

```bash
sudo ./scripts/smoke-tests.sh
```

## 11) Traffic monitoring modes (off/basic/advanced)

Traffic monitoring is for troubleshooting and visibility.

Configure in staging YAML:
```yaml
services:
  monitoring:
    mode: off        # off | basic | advanced
    # interfaces: [wan, lan, opt1]   # optional; defaults to all ifaces
```

- **off**: no sampling, `/traffic` is empty
- **basic**: low-rate sampling (recommended)
- **advanced**: higher-rate sampling + short history shown in `/traffic`
