# AuroraGW: Enterprise-Grade Open-Source Router/Firewall Appliance
## Complete Product Plan & Technical Specification

**Version**: 1.0  
**Date**: January 25, 2026  
**Location**: Stittsville, Ontario, Canada  
**Status**: Ready for Development  
**Intended Audience**: Development Team, Product Stakeholders, Future Contributors

---

## Executive Summary

**AuroraGW** is a free, open-source, high-performance internet-edge router/firewall appliance designed as a **pfSense competitor** for users who prioritize **maximum PPPoE throughput** and modern QoS/bufferbloat mitigation on commodity x86 hardware.

### Why AuroraGW?
- **pfSense Limitation**: Traffic shaping (FQ_CODEL) overhead bottlenecks 3–5 Gb/s PPPoE on i5-class CPUs.
- **AuroraGW Solution**: Linux kernel (6.14+) + nftables (atomic updates) + CAKE (purpose-built bufferbloat AQM) achieves 5–10 Gb/s line-rate PPPoE on the same hardware.
- **Competitor Advantage**: Modern, modular, fully open-source, supports DHCP→PPPoE switching (the "bootstrap problem" solved).

### Key Differentiators
1. **Two-Stage Lifecycle**: Install via DHCP bootstrap (using your existing internet), then switch to PPPoE production mode via Web UI.
2. **Declarative Config Model**: Single YAML file is the source of truth; all generated configs re-hydrate from it (safe upgrades, portable backups).
3. **All Open-Source, Baked In**: Kea DHCP + Unbound DNS + Suricata + EveBox + WireGuard—all included, no "premium" feature lockout.
4. **Modular Installation**: Interactive TUI installer detects hardware, asks questions, installs only what you need.

---

## 1. Product Vision & Goals

### 1.1 Vision Statement
Build a **transparent, secure, performance-first router OS** on Ubuntu Linux that lets power users and small ISPs deploy high-speed PPPoE gateways without sacrificing throughput or security.

### 1.2 Primary Goals
- **Performance**: Sustain 5–10 Gb/s PPPoE on i5-class hardware (4-core, ~3.2 GHz).
- **Usability**: "Appliance-like" ease of setup (Web UI wizard + TUI installer).
- **Security**: Default-deny firewall, RBAC, audit logs, encrypted backups, management isolation.
- **Flexibility**: Pluggable modules (DHCP on/off, QoS modes, IDS integration).
- **Open**: 100% open-source stack; no license restrictions.

### 1.3 Non-Goals (Intentional Scope Limits)
- Enterprise BGP/OSPF routing (later add-on if demand exists).
- Inline IDS/IPS by default (deferred; optional SPAN/TAP mode provided).
- "One-click" mesh/clustering (single-appliance focus for MVP).

---

## 2. Hardware & Performance Baseline

### 2.1 Reference Hardware (MVP Target)
| Component | Spec | Notes |
|-----------|------|-------|
| CPU | Intel i5-6500 (Skylake) or equivalent | 4 cores @ 3.2 GHz; adequate for 10G routing + QoS. |
| RAM | 8 GB | Sufficient for kernel, tc queues, dnsmasq, suricata. |
| WAN NIC | Intel X540-AT2 (2× 10GBase-T) | Native ixgbe driver; hardware offload (TSO/LRO/checksum). |
| LAN NIC | Intel I350 (1G quad-port) or dual igb | 1 Gb/s ports for LANs. |
| Storage | SSD (256 GB minimum) | OS only; no large data workloads. |

### 2.2 Performance Targets (Acceptance Criteria)
| Scenario | Target | Notes |
|----------|--------|-------|
| Raw PPPoE (no QoS) | 8–10 Gb/s throughput | Sustained, multi-stream (iperf3 -P10). |
| PPPoE + CAKE QoS | 9+ Gb/s @ 95% line rate | Bufferbloat: A/A grade on DSLReports. |
| Latency (idle) | <10 ms | Router-only latency (no WAN variable). |
| CPU Load (10G PPPoE) | 40–60% | vs. pfSense 80–95%. |

### 2.3 Supported Hardware (Roadmap)
- **MVP**: i5-6500 + X540-AT2 (the "reference").
- **v1**: i5/i7 (6th–10th gen), AMD Ryzen 3000/5000 series.
- **Future**: ARM64 routers (SolidRun HoneyComb, Pine64) if demand exists.

---

## 3. OS & Kernel Strategy

### 3.1 Base OS: Ubuntu (Latest Non-LTS Track)
**Decision**: Use **Ubuntu 25.10** (or latest interim release), NOT 24.04 LTS.

**Rationale**:
- **Newer Kernel (6.14+)**: Improvements in ixgbe driver interrupt handling, BPF JIT, DualPI2 AQM support.
- **Faster userspace updates**: dnsmasq, suricata, wireguard get faster patches.
- **Trade-off**: 9-month support window; requires planned annual upgrades.

**Product Implication**: In-place upgrade mechanism is a **first-class feature** (A/B partitions, rollback, config migration scripts).

### 3.2 Kernel Tuning (Kernel 6.14+)
Leverage modern kernel features:
- **NAPI polling optimization**: Reduces PPPoE encapsulation overhead.
- **BPF JIT acceleration**: Faster nftables rule evaluation.
- **DualPI2 AQM** (optional turbo mode): Lighter CPU overhead than CAKE at high throughput.

### 3.3 Target Kernel Version
- **Minimum**: Linux 6.10 (Ubuntu 24.10 base).
- **Recommended**: Linux 6.14+ (Ubuntu 25.04+ base).

---

## 4. Architecture: Layered Design

### 4.1 Three-Plane Architecture

```
┌─────────────────────────────────────────────┐
│      Management Plane (Web UI + API)        │
│  (FastAPI + React, LAN-only, TLS, RBAC)    │
└────────────────────┬────────────────────────┘
                     │
         ┌───────────┴───────────┐
         ↓                       ↓
┌─────────────────────┐  ┌──────────────────────┐
│   Config Store      │  │  auroragw-daemon     │
│ /etc/auroragw/      │  │  (Control Plane)     │
│ config.yaml         │  │  systemd service     │
│ (YAML + secrets)    │  │  - Read config       │
└─────────────────────┘  │  - Apply to kernel   │
                         │  - Health monitoring │
                         │  - Rollback logic    │
                         └──────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ↓                    ↓                    ↓
┌──────────────────┐ ┌────────────────┐ ┌────────────────┐
│  Network Stack   │ │   nftables     │ │   tc (qdisc)   │
│  (netplan +      │ │  (rules gen)   │ │  (CAKE/DualPI2)│
│  networkd)       │ └────────────────┘ │  (ingress IFB) │
└──────────────────┘                     └────────────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ↓
                  ┌──────────────────────────┐
                  │   Linux Kernel           │
                  │  (Routing, NAT, QoS)     │
                  └──────────────────────────┘
```

### 4.2 Config Model: "Declarative + Re-hydration"

**Golden File**: `/etc/auroragw/config.yaml`
```yaml
interfaces:
  wan:  { mac: "aa:bb:cc:dd:ee:ff" }
  lan:  { mac: "11:22:33:44:55:66" }
  opt1: { mac: "77:88:99:aa:bb:cc" }

segments:
  - id: lan
    label: LAN
    ifref: lan
    address: 192.168.101.1/24
    dhcp: { enabled: true, range_start: 192.168.101.50, range_end: 192.168.101.250 }

  - id: opt1
    label: OPT1
    ifref: opt1
    address: 192.168.102.1/24
    dhcp: { enabled: true, range_start: 192.168.102.50, range_end: 192.168.102.250 }

wan:
  mode: dhcp  # Stage 1 (Bootstrap)
  # mode: pppoe # Stage 2 (Production) - user toggles via Web UI
  pppoe:
    username: "b1234567890"
    password: "secret"
    mtu: 1492

services:
  dhcp: { provider: kea, enabled: true }
  dns:  { provider: unbound, enabled: true }
  qos:
    enabled: true
    # Bandwidth is expressed as kbit/s in the current implementation.
    segments:
      lan:  { rate_kbit: 950000 }
      opt1: { rate_kbit: 950000 }
    wan:
      enabled: false
      egress_kbit: 9500000
      ingress_kbit: 9500000
      overhead_bytes: 34
      mpu_bytes: 64

  suricata: { installed: true, enabled: false, interfaces: [lan] }

firewall:
  port_forwards: []
```

**Generated Files** (re-created from YAML on apply):
- `/etc/netplan/99-auroragw.yaml` (network config)
- `/etc/nftables.conf` (firewall rules)
- `/etc/unbound/unbound.conf.d/auroragw.conf` (DNS)
- `/etc/kea/kea-dhcp4.conf` (DHCP)
- `/etc/ppp/peers/auroragw-wan` + `/etc/ppp/chap-secrets` (PPPoE)
- `tc` qdisc config (QoS)

**Benefit**: On upgrades or restores, the system re-hydrates deterministically from the YAML. No "config drift."

---

## 5. Two-Stage Deployment Lifecycle

### 5.1 Stage 1: Bootstrap (Day 0)
**State**: "Appliance Builder Mode"
- **WAN role**: DHCP client (gets IP from your existing router/modem).
- **Task**: Download packages, run installer, full system setup.
- **Duration**: ~30 minutes.
- **Network**: Your old router + new router in series (new router gets internet via old router).

### 5.2 Stage 2: Takeover (Day 1)
**State**: "Router Mode"
- **WAN role**: Switches from DHCP to PPPoE client.
- **Trigger**: User clicks "Switch to PPPoE" in Web UI.
- **Process** (Atomic, with rollback):
  1. Validate PPPoE credentials.
  2. Stop DHCP client on WAN.
  3. Bring up pppoe0 interface.
  4. Wait for authentication (PADO timeout ~60 sec).
  5. If success: update routing, commit config.
  6. If fail: revert to DHCP, alert user.
- **Result**: New router is now the ISP gateway; old router unplugged.

### 5.3 Transition Workflow (UX)

**TUI Installer (Stage 1)**:
```
┌─────────────────────────────────────────┐
│  AuroraGW Bootstrap Installer v1.0       │
├─────────────────────────────────────────┤
│  Step 1: System Check                   │
│  ✓ CPU: Intel i5-6500 (4 cores)         │
│  ✓ NIC: ixgbe (X540-AT2) detected       │
│  ✓ WAN (selected by MAC): DHCP → 192.168.1.55 │
│                                          │
│  Step 2: Network Setup                  │
│  LAN (selected by MAC) IP: 192.168.101.1/24 │
│  [Edit]                                 │
│                                          │
│  Step 3: PPPoE Credentials (Optional)   │
│  Username: b1234567890                  │
│  Password: ••••••••                     │
│  (Will apply after installation)        │
│                                          │
│  Step 4: Modules                        │
│  ✓ DNS (Unbound)                        │
│  ✓ DHCP (Kea)                           │
│  ✓ QoS (CAKE)                           │
│  ☐ IDS (Suricata) [Disabled by default] │
│                                          │
│  [Install]                              │
│                                          │
│  Installing... (5 min)                  │
│  ✓ Network config                       │
│  ✓ Firewall (nftables)                  │
│  ✓ Services (DHCP, DNS, QoS)            │
│                                          │
│  DONE! Connect PC to LAN port and visit │
│  https://192.168.101.1:8443             │
│  (Login: admin / password set in installer) │
└─────────────────────────────────────────┘
```

**Web UI Dashboard (Stage 2)**:
```
┌──────────────────────────────────────────┐
│  AuroraGW Dashboard                      │
├──────────────────────────────────────────┤
│  WAN Status: DHCP (192.168.1.55)        │
│                                          │
│  [ Switch to PPPoE Mode ]                │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ Switch WAN to PPPoE?               │  │
│  │                                    │  │
│  │ Username: b1234567890             │  │
│  │ Password: ••••••••                 │  │
│  │ VLAN ID: [blank]                   │  │
│  │                                    │  │
│  │ [ Cancel ]  [ Apply ]              │  │
│  └────────────────────────────────────┘  │
│                                          │
│  [Applying... 30 sec timeout]            │
│  ✓ Authenticating with ISP...           │
│  ✓ Assigned IP: 142.189.247.110         │
│                                          │
│  WAN Status: PPPoE (142.189.247.110)    │
│  Latency: 8 ms | Speed: 9.8 Gb/s        │
└──────────────────────────────────────────┘
```

---

## 6. Component Selection (All Open-Source)

### 6.1 Core Routing & Firewall
| Layer | Component | Why |
|-------|-----------|-----|
| Kernel | Linux 6.14+ | Modern networking; PPPoE optimizations; ixgbe improvements. |
| Network Config | Netplan + systemd-networkd | Deterministic boot order; YAML-driven (not graphical). |
| Firewall | nftables | Atomic rule updates; faster than iptables; supports counters/logging. |
| NAT | nftables postrouting | Integrated; masquerade + port forwarding in one ruleset. |

### 6.2 WAN & PPPoE
| Component | Version | Notes |
|-----------|---------|-------|
| rp-pppoe | Latest | Light, fast PPPoE daemon. |
| pppd | Standard | PPP link protocol; well-tested. |
| systemd service | Native | Auto-restart, status monitoring, logging. |

### 6.3 DHCP & DNS (Best-of-Breed)
| Service | Baseline | Optional fallback | Why |
|---------|----------|------------------|-----|
| DHCP | Kea (ISC) | dnsmasq | Kea is more enterprise-grade, scalable, API-friendly. |
| DNS Resolver | Unbound | dnsmasq | Unbound is a full validating recursive resolver (DNSSEC, DoT/DoH ready). |

**Decision**: Bake in **Kea + Unbound** (v1); allow downgrade to **dnsmasq** on resource-constrained systems.

### 6.4 QoS & Bufferbloat
| Mode | Default | Alt | CPU Impact |
|------|---------|-----|------------|
| Safe (Fair) | CAKE | FQ_CoDel | Medium; best latency fairness. |
| Turbo (Raw) | DualPI2 | none | Low; best throughput (less fair). |

**Both baked in**; user selects at install.

### 6.5 IDS/IPS (Optional Module)
| Component | Purpose | Status |
|-----------|---------|--------|
| Suricata | IDS/IPS engine | Installed but disabled by default (no throughput impact). |
| EveBox | Alert UI | Lightweight web viewer for Suricata EVE JSON. |
| ET Open Rules | Ruleset | Community maintained; free. |

**Deployment Modes**:
1. **Disabled (Default)**: Max PPPoE throughput; no IDS overhead.
2. **Passive (SPAN/TAP)**: Mirror WAN traffic to Suricata; non-blocking.
3. **Inline IPS (Advanced)**: Warn user about throughput impact; require CPU check.

### 6.6 VPN
| Protocol | Component | Notes |
|----------|-----------|-------|
| WireGuard | wg-quick + systemd | Modern, fast, kernel-native; replaces OpenVPN for site-to-site + clients. |

Included in v1; deferred for MVP if time-constrained.

### 6.7 Observability & Logs
| Tool | Purpose | Notes |
|------|---------|-------|
| systemd-journald | System logs | Built-in; JSON output. |
| rsyslog | Syslog aggregation | Optional; useful for SPAN/remote logging. |
| prometheus + node_exporter | Metrics (optional) | Future: Grafana dashboard. |

---

## 7. Installer Architecture: Interactive, Modular, Safe

### 7.1 Installer Technology Stack
- **Language**: Python 3.10+ (robust logic, easy to maintain/extend).
- **TUI Library**: `rich` or `urwid` (terminal UI; Cockpit is not suitable for bootstrap).
- **Network Detection**: `ip`, `ethtool`, `lspci` (shell commands + parsing).
- **Config Generation**: Jinja2 templates.
- **Validation**: `jsonschema` + custom validators (YAML syntax, IP overlaps, interface conflicts).

### 7.2 Installer Flow
```
install.py
├─ Phase 1: Preflight Checks
│  ├─ OS version check (Ubuntu 25.04+)
│  ├─ CPU detection (warn if <4 cores)
│  ├─ NIC detection (list ixgbe, igb, others)
│  ├─ Internet connectivity (ping 8.8.8.8)
│  └─ Disk space (>20 GB free)
│
├─ Phase 2: Interactive Questionnaire (TUI)
│  ├─ WAN Setup
│  │  ├─ Select NIC by MAC (showing ifname + driver)
│  │  ├─ Mode: DHCP (Stage 1) [default]
│  │  └─ Collect PPPoE creds (optional; apply later)
│  ├─ LAN Setup
│  │  ├─ Select NIC by MAC (showing ifname + driver)
│  │  ├─ IP address [default 192.168.101.1/24]
│  │  └─ Enable DHCP? [Y/n]
│  ├─ Performance Tuning
│  │  ├─ Target speed: 1G / 2.5G / 5G / 10G [auto-detect]
│  │  └─ QoS mode: Safe (CAKE) / Turbo (DualPI2) / None
│  └─ Feature Flags
│     ├─ DNS: Unbound / dnsmasq / none
│     ├─ DHCP: Kea / dnsmasq / none
│     ├─ IDS: Suricata (off by default)
│     └─ VPN: WireGuard (on/off)
│
├─ Phase 3: Execution (Automated)
│  ├─ Create /etc/auroragw/config.yaml (from answers)
│  ├─ Generate configs (netplan, nftables, etc.)
│  ├─ apt install (packages, filtered by feature flags)
│  ├─ Create systemd services
│  ├─ Tune kernel (sysctl)
│  ├─ Health check (LAN IP reachable, services running)
│  └─ Generate initial login credentials
│
└─ Phase 4: Completion
   ├─ Print summary (LAN IP, Web UI URL, credentials)
   ├─ Create rollback snapshot (if ZFS available)
   └─ Exit (System ready for Stage 2 switchover)
```

### 7.3 Safety Mechanisms
- **Dry-run mode** (`--dry-run`): Shows what would be installed; no changes.
- **Validate-only mode** (`--validate-config`): Check syntax; exit with status.
- **Rollback snapshot** (ZFS): On first boot after install, create a recovery point.
- **Idempotent**: Running installer twice should be safe (not install things twice).

---

## 8. Config Backup & Restore Strategy

### 8.1 Backup Contents
A backup is a `.tar.gz` file containing:
```
backup-2026-01-25.tar.gz
├─ config.yaml                    (The golden file)
├─ secrets/
│  ├─ wireguard-priv.key          (WireGuard private key)
│  ├─ web-ui-tls.key              (Web UI HTTPS key)
│  └─ ssh_host_key_rsa            (SSH server key - optional)
├─ metadata.json                   (Version, timestamp, checksum)
└─ firewall-rules.txt             (Human-readable export)
```

### 8.2 Backup Encryption
- **Optional**: Encrypt `.tar.gz` with AES-256 (user-provided passphrase).
- **At-rest**: If cloud storage, ensure TLS in transit.

### 8.3 Restore Workflow
1. **Upload** `.tar.gz` via Web UI.
2. **Validate**:
   - Schema version compatibility.
   - Hardware mapping (user confirms NIC changes).
3. **Dry-run**: Render all configs to temp dir; syntax check.
4. **Stage**: Unpack to `/tmp/restore/`.
5. **Apply**:
   - Backup current state.
   - Overwrite `/etc/auroragw/config.yaml`.
   - Run config re-hydration (generate all `/etc/` files).
   - Reload services.
6. **Verify**:
   - Services up.
   - Network connectivity (LAN + WAN if applicable).
   - Fallback to backup if any checks fail (auto-rollback).

### 8.4 Git-Backed Config Versioning
Every config change is committed to a local Git repo (`/etc/auroragw/.git`):
```bash
git log --oneline /etc/auroragw/config.yaml
# Output:
# a1b2c3d (30 min ago) Updated firewall rule for IoT network
# f4e5d6c (2 days ago) Enabled QoS + CAKE
# 9z8y7x6 (1 week ago) Initial install
```

**User benefit**: "Undo" button in Web UI (revert to any prior state).

---

## 9. Web UI & API

### 9.1 Technology Stack
- **Backend**: Python/FastAPI (async, modern, type-safe).
- **Frontend**: React 18+ (or Vue 3 if team prefers) + TypeScript.
- **Authentication**: Local user database + optional 2FA (TOTP).
- **Transport**: HTTPS only (self-signed cert on LAN by default).
- **RBAC**: Admin / Monitor / Guest roles.

### 9.2 Web UI Pages (MVP)

**Dashboard**:
- WAN status (IP, speed, latency, uptime).
- LAN overview (connected devices count).
- CPU/Memory/Disk usage.
- Service status (DHCP, DNS, QoS, IDS).
- Alerts (failover, rule violations, etc.).

**Interfaces**:
- Physical NICs: detection, speed, errors, TX/RX bytes.
- Virtual interfaces (pppoe0, br-*, etc.): status, IP config.
- Ability to toggle interfaces (down/up).

**WAN Configuration**:
- WAN mode selector: DHCP (Stage 1) ↔ PPPoE (Stage 2).
- PPPoE toggle: "Switch to Production Mode" → (validate → apply → revert-on-fail).
- Static route editor.

**Firewall Rules**:
- Rule builder (visual; no raw nft syntax required).
- Import/export rules as JSON.
- Enable/disable without delete.

**DHCP & DNS**:
- DHCP pool configuration per interface.
- DNS zone editor (local overrides).
- Conditional forwarding (e.g., `*.home.arpa` → 192.168.101.1).

**QoS Settings**:
- QoS mode: Safe / Turbo / Off.
- Bandwidth limits (download/upload).
- Per-device/port rules (advanced).

**System & Security**:
- User management (create/delete/change password).
- SSH key upload.
- TLS cert management.
- Auto-update schedule (enable/disable).

**Backup & Restore**:
- Download config (unencrypted or encrypted).
- Upload config + restore.
- Undo history (Git revert).

**Logs & Monitoring**:
- Firewall drop logs (real-time tail).
- Service logs (systemd journal).
- PPP connection log.
- Speed test history (if benchmarking feature added).

### 9.3 REST API
Expose a REST API so automation tools (Ansible, Terraform) can configure the router:
```bash
GET /api/v1/system/status
GET /api/v1/interfaces/list
POST /api/v1/wan/switch-mode  # { "mode": "pppoe", "user": "...", "pass": "..." }
GET /api/v1/firewall/rules
POST /api/v1/firewall/rules   # { "name": "...", "match": "...", "action": "..." }
GET /api/v1/config/backup
POST /api/v1/config/restore   # Upload tar.gz
```

---

## 10. Module System (Pluggable Features)

### 10.1 Module Interface
Each module is a Python package with a standard interface:
```python
# modules/ids/module.py
class IDS_Module:
    name = "Suricata IDS"
    depends = ["base-network"]  # Must install after base
    package_list = ["suricata", "evebox", "suricata-update"]
    
    def preflight(self) -> bool:
        """Check system capabilities (CPU, RAM)."""
        # Return False if unsupported (e.g., CPU < 6 cores for inline IDS)
        pass
    
    def install(self, config: dict) -> bool:
        """Install packages, render configs, start services."""
        pass
    
    def validate_config(self, config: dict) -> list:
        """Return list of errors if config is invalid."""
        pass
    
    def apply(self, config: dict) -> bool:
        """Apply config changes; return success."""
        pass
    
    def status(self) -> dict:
        """Return { 'running': bool, 'alerts': [...] }."""
        pass
    
    def uninstall(self) -> bool:
        """Remove packages, clean configs."""
        pass
```

### 10.2 Modules (MVP)
- **base-network**: Core routing (always installed).
- **dhcp**: Kea DHCP server.
- **dns**: Unbound DNS resolver.
- **qos**: tc + CAKE/DualPI2.
- **ids**: Suricata + EveBox.
- **vpn**: WireGuard.

### 10.3 Future Modules (v2+)
- **monitoring**: Prometheus + Grafana.
- **ldap**: LDAP/AD auth integration.
- **ipv6**: DHCPv6-PD, prefix delegation.
- **multiwan**: Dual WAN failover + load-balance.

---

## 11. Security Hardening Baseline

### 11.1 OS-Level Hardening
- **Default-deny firewall**: Inbound and forward policies are DROP by default.
- **Kernel hardening**: ASLR, DEP, stack canaries enabled by default.
- **Auto-updates**: Critical security patches applied automatically (via `unattended-upgrades`).
- **SSH keys only**: No password auth on SSH; keys in `/root/.ssh/authorized_keys`.

### 11.2 Management Plane Security
- **LAN-only by default**: Web UI and SSH reachable only from designated LAN segment/subnet(s).
- **HTTPS + self-signed cert**: Web UI always uses TLS (cert auto-generated on first boot).
- **RBAC**: Admin (full access), Monitor (read-only), Guest (limited).
- **Audit logging**: All config changes logged to `/var/log/auroragw-audit.log`.
- **Session expiration**: 15 minutes of inactivity → logout.

### 11.3 Data Protection
- **Secrets encryption**: PPPoE passwords, WireGuard keys stored encrypted in config (AES-256).
- **Backup encryption**: Optional user-provided passphrase for backup archives.
- **No telemetry**: No phone-home, no crash reporting without explicit user consent.

---

## 12. Testing & Validation

### 12.1 Test Categories

**Functional Tests**:
- PPPoE connect/disconnect cycles (100 iterations).
- DHCP lease assignment (per interface).
- DNS resolution (local + upstream).
- Firewall rule application (stateful, NAT, port forwarding).
- WAN mode switch (DHCP ↔ PPPoE) + rollback.

**Performance Tests**:
- Raw PPPoE throughput (iperf3 multi-stream, 30 min sustained).
- QoS enabled: CAKE at specified bandwidth; measure latency under load.
- Rule scaling: 10, 100, 500, 1000 rules; measure pps/CPU.
- Packet loss: sustained high throughput; check for drops.

**Security Tests**:
- External WAN scan: verify only configured ports respond.
- Web UI brute-force: verify rate-limiting.
- Config injection: invalid YAML/nft syntax; verify rejection.
- Backup encryption: verify encrypted backups cannot be read without passphrase.

**Integration Tests**:
- Upgrade path: old config → new version → old config restored.
- Backup/restore: config → backup → fresh install → restore → identical state.
- Module install/uninstall: add IDS, then remove; verify no residual files.

### 12.2 CI/CD
- **GitHub Actions**: Run unit tests + simple integration tests on PR.
- **Nightly builds**: ISO image generated from HEAD.
- **Release process**: Tag version → sign ISO → upload to GitHub Releases.

---

## 13. Deployment & Packaging

### 13.1 Distribution Formats

**Option 1: Bootable ISO (Recommended)**
- Pre-installed Ubuntu 25.10 + AuroraGW base.
- User boots from USB, runs interactive installer.
- ~15 min to functional router.

**Option 2: Installer Script**
- `curl https://install.auroragw.io | bash`
- Suitable for "bring your own Ubuntu" deployments.

### 13.2 Release Schedule
- **MVP (v0.9)**: Q2 2026 (4 months from now).
- **v1.0**: Q3 2026 (8 months from now).
- **Ongoing**: Quarterly bugfix/feature releases; security patches as needed.

---

## 14. Roadmap (Phases)

### Phase 1: MVP (v0.9) — Core Router
**Deliverables**:
- TUI installer (Phases 1–4).
- Basic Web UI dashboard.
- DHCP (Kea) + DNS (Unbound).
- nftables firewall (rules builder).
- QoS (CAKE).
- Backup/restore.

**Hardware**: i5-6500 + X540-AT2 reference box.

**Performance**: 3–5 Gb/s PPPoE with CAKE; A/A bufferbloat.

**Timeline**: 4 months.

### Phase 2: v1.0 — Production-Ready
**Additions**:
- WireGuard VPN.
- Module system solidified.
- Full RBAC in Web UI.
- In-place upgrade mechanism.
- Comprehensive docs + YouTube demos.

**Timeline**: 4 months after MVP.

### Phase 3: v1.1 — Advanced Features
**Additions**:
- Suricata + EveBox integration (full IDS UI).
- Per-interface QoS rules.
- DHCPv6-PD support.
- Prometheus metrics export.

**Timeline**: 6 months after v1.0.

### Phase 4+: Future (v2.0+)
- Multi-WAN + failover.
- BGP routing (for ISP partners).
- ARM64 support (embedded routers).
- Community package marketplace.

---

## 15. Success Metrics (Acceptance)

| Metric | Target | Validation |
|--------|--------|-----------|
| PPPoE throughput (CAKE enabled) | 9+ Gb/s | iperf3 -P10 sustained test. |
| Bufferbloat score | A/A grade | DSLReports test (idle + loaded). |
| Web UI install-to-online | <20 min | Timed walkthrough on reference HW. |
| Config backup restore | <5 min | Restore on fresh Ubuntu, verify connectivity. |
| Firewall rule latency | <10 μs per packet | iperf3 with 100 rules; measure overhead. |
| Mean uptime (MTTR) | >30 days | Long-running stability test. |
| Test coverage (code) | >80% | Python unit + integration tests. |

---

## 16. Known Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Ubuntu 25.10 EOL in 9 months | Must upgrade or lose support | Automate upgrade path in v1.0. Test annually. |
| ISP-specific PPPoE quirks (VLAN, custom MTU) | May not work for some users | Provide diagnostic tools; capture logs; community wiki. |
| Complex firewall rules causing performance drop | User enables 1000 rules; throughput tanks | Add rule complexity warnings; recommend max ~200 rules. |
| Suricata inline IDS CPU usage | Kills PPPoE throughput | Default to "off" and passive mode; warn on enable. |
| Network hardware not widely tested | Works on reference HW only | Expand testing matrix (different NICs, CPUs); collect reports. |

---

## 17. Development Team Structure (Recommended)

| Role | FTE | Responsibilities |
|------|-----|------------------|
| **Technical Lead** | 1 | Architecture, kernel tuning, PPPoE, performance. |
| **Backend Developer** | 1–2 | Python (FastAPI), systemd integration, modules. |
| **Frontend Developer** | 1 | React Web UI, UX design. |
| **QA/Testing** | 1 | Test plan, automation, performance benchmarks. |
| **Documentation** | 0.5 | Docs, diagrams, deployment guides, FAQ. |

**Total**: ~4–5 FTE for 8 months (MVP → v1.0).

---

## 18. Success Story (Post-Launch Vision)

**Year 1 (2026)**: Community adoption. Small ISPs testing AuroraGW as a pfSense replacement on commodity hardware. GitHub stars >1,000. Active forums. Annual meetup.

**Year 2 (2027)**: Integrated IDS/IPS with Suricata fully baked in. BGP routing module released. ARM64 support added. AuroraGW on Raspberry Pi.

**Year 3 (2028)**: Multi-WAN, VRRP failover. EVPN support for advanced users. Trademark + foundation established. Commercial support optionally available from partner companies.

---

## 19. Appendix: Decision Log

### Decision 1: Ubuntu vs. CentOS vs. Debian
- **Chosen**: Ubuntu 25.10 (latest non-LTS).
- **Rationale**: Faster kernel updates + better hardware support for Intel NICs.

### Decision 2: CAKE vs. DualPI2 as default
- **Chosen**: CAKE (safe default) with DualPI2 turbo option.
- **Rationale**: CAKE is battle-tested; DualPI2 is newer and lighter but less understood by users.

### Decision 3: Kea + Unbound vs. dnsmasq
- **Chosen**: Support both; default to Kea + Unbound (v1), allow dnsmasq fallback.
- **Rationale**: Kea is more enterprise, future-proof; dnsmasq is simpler for MVP.

### Decision 4: nftables vs. iptables
- **Chosen**: nftables only (no iptables).
- **Rationale**: Atomic rule updates, modern, required for future DPDK integration.

### Decision 5: Monolithic installer vs. modular
- **Chosen**: Modular (plugins), but with sane defaults (all open-source features baked in).
- **Rationale**: Extensibility + simplicity for most users.

### Decision 6: Free vs. freemium model
- **Chosen**: 100% free, open-source. No commercial lockout.
- **Rationale**: True community product; sustain via sponsorships + consulting if needed.

---

## 20. Conclusion

**AuroraGW** is positioned to fill the gap between "pfSense (proven but slow on PPPoE)" and "build-your-own-router (too complex)." By leveraging modern Ubuntu, nftables, CAKE, and a declarative config model, we deliver a **production-ready, open-source router** that can sustain 5–10 Gb/s PPPoE on commodity hardware—something pfSense struggles with today.

The **two-stage lifecycle** (DHCP bootstrap → PPPoE takeover) solves the classic "chicken-and-egg" problem elegantly. The **modular installer** makes it accessible to non-experts. The **Web UI** provides pfSense-like familiarity. And the **declarative YAML config** ensures upgrades and backups are atomic and portable.

With proper execution, AuroraGW can attract:
- Power users frustrated with pfSense performance on PPPoE.
- Small ISPs looking for a flexible, open-source CPE option.
- Home labbers who want to run their own edge.

**Ready to build.** All decisions are documented. The spec is sound. Let's ship v0.9.

---

**Document Version**: 1.0  
**Last Updated**: January 25, 2026  
**Next Review**: April 25, 2026 (MVP completion)

**Prepared By**: [Your Name]  
**Approved By**: [Stakeholder]
