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

Note: you can also run it from the `install/` directory; the installer copies files based on its own location.

Installer prompts:
- Select NICs from the detected interface list for `wan/lan/opt1` (the config stores MACs for portability)
  - Tip: in the terminal menu, use arrow keys and press **Enter** to select.
- Choose WAN mode: `dhcp` (bootstrap) or `pppoe`
- Enable/disable: UPnP on OPT1, discovery relay (mDNS+SSDP), Suricata IDS, Cockpit

After install:
- Web UI: `https://<LAN-IP>:8443/` (self-signed)
- Optional Cockpit (if enabled): `https://<LAN-IP>:9090/`

Installer logging:
- The installer writes a full log to `/var/log/auroragw/install-<timestamp>.log`.
- If install fails, re-run and/or review the log file for the first error.

If install fails during virtualenv creation with an `ensurepip is not available` message, install the venv package and re-run:
```bash
sudo apt-get update
sudo apt-get install -y python3-venv || true
sudo apt-get install -y "python$(python3 -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")')-venv" || true
sudo ./install/auroragw-install.sh
```

## 2) Reconfigure later

```bash
sudo /opt/auroragw/install/auroragw-install.sh --reconfigure
```

## 3) Web UI basics

- `/config`: edits **staging** YAML (`/etc/auroragw/config.staging.yaml`)
- `/status`: Apply/Confirm
  - “Apply staging” runs a safe apply and (by default) requires confirmation within 120s
  - “Confirm” commits the pending apply
- `/dns`: router upstream DNS + per-segment DHCP DNS (then Apply/Confirm)
- `/firewall`: add/remove **port forwards** (WAN → LAN/OPT1)
- `/suricata`: enable/disable IDS + select interfaces (then Apply/Confirm)
- `/evebox`: enable/disable EveBox (then Apply/Confirm) and open EveBox UI on port 5636
- `/grafana`: optional Grafana module (local or remote; then Apply/Confirm)
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

Note: when in PPPoE mode, AuroraGW enables a conservative TCP MSS clamp to help avoid PMTUD issues (common with PPPoE MTU 1492).

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

You can configure it either:
- **Runtime (recommended for troubleshooting):** Web UI `Traffic` page (`/traffic`) writes `/etc/auroragw/monitoring.yaml` and takes effect immediately (no Apply required).
- **Config default:** `services.monitoring` in the main config (takes effect after Apply + Confirm).

Configure in staging YAML (config default):
```yaml
services:
  monitoring:
    mode: off        # off | basic | advanced
    # interfaces: [wan, lan, opt1]   # optional; defaults to all ifaces
```

- **off**: no sampling, `/traffic` is empty
- **basic**: low-rate sampling (recommended)
- **advanced**: higher-rate sampling + short history shown in `/traffic`
- Sampling is in-memory inside `auroragw-web`; it continues even if you close the browser tab and resets on service restart.

Runtime override file example:
```yaml
mode: advanced
interfaces: [pppoe0]   # optional; omit/empty = all
```

## 11.1) Web UI errors (for troubleshooting)

If the Web UI shows an error page, it includes an **Error ID** and **Request ID**.
Share those IDs when reporting issues, and optionally include:
```bash
sudo journalctl -u auroragw-web -n 200 --no-pager
sudo tail -n 200 /var/log/auroragw/web-errors.log 2>/dev/null || true
```

## 11.2) Web UI auth lockout (brute-force protection)

The Web UI uses HTTP Basic Auth and includes a simple lockout:
- After **5 invalid attempts**, the source IP/user is locked out for **~5 minutes**.
- Lockout state is in-memory inside `auroragw-web` (resets on service restart).

## 11.3) Grafana / Prometheus (optional)

AuroraGW exposes Prometheus metrics at:
- `https://<auroragw-lan-ip>:8443/metrics`

You can run Grafana/Prometheus:
- **on a LAN VM/NAS (recommended)**, or
- **locally on AuroraGW** (uses Docker; LAN-only by firewall policy).

See `docs/GRAFANA.md`.

## 12) DNS configuration (WAN-learned defaults + per-segment overrides)

AuroraGW uses **Unbound** for DNS by default. By default, Unbound forwards to the DNS servers learned from WAN
(DHCP in bootstrap mode, PPPoE in production mode).

Configure in Web UI:
- `DNS` page: `/dns`
  - **Router upstream DNS**: `auto` (WAN-learned) or `manual` (pin resolver IPs)
  - **DHCP DNS per segment**:
    - `router`: clients use AuroraGW (recommended)
    - `inherit_wan`: clients use the WAN-learned DNS directly
    - `manual`: clients use your provided DNS servers

YAML examples:
```yaml
services:
  dns:
    provider: unbound
    enabled: true
    upstream:
      mode: auto
      servers: []

segments:
  - id: lan
    dhcp:
      enabled: true
      dns: { mode: router }

  - id: opt1
    dhcp:
      enabled: true
      dns: { mode: manual, servers: ["1.1.1.1", "9.9.9.9"] }
```
