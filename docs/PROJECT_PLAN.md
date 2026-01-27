# AuroraGW Project Plan v1.3 (Ubuntu pfSense Replacement + Home Device Compatibility + VMware Lab)

> **Context:** AuroraGW is a high-performance, secure, pfSense-like router/firewall built on **latest Ubuntu Server (interim releases)**. It targets PPPoE performance, modern QoS (CAKE), and a safe, auditable configuration model—while also supporting “home stuff” like **AirPrint**, **Chromecast/Cast**, **Samsung TV/DLNA**, and game-console friendliness via **UPnP/PCP/NAT-PMP**.  
> **New in v1.3:** First-class **VMware lab support** (MAC-based NIC role binding, lab harness, and VM-focused acceptance criteria).

---

## 0) Product framing

**Working name:** AuroraGW  
**Vision:** A fast, secure, internet-edge appliance on Ubuntu with pfSense-like UX (web UI, DHCP/DNS, VPN, firewall rules, QoS) and a modern “safe apply” workflow.

**Differentiators**
- Performance-first PPPoE gateway on commodity x86 (i5-6500 class + Intel X540-AT2 baseline).
- Modern QoS/bufferbloat control using **CAKE** (shaping + AQM + flow isolation + DiffServ).
- Safe apply config model: staged config, schema+semantic validation, atomic apply, auto rollback.
- Hardened management plane by default: LAN-only access, RBAC, audit logs, default deny from WAN.
- **Home-device compatibility as explicit modules** (mDNS, SSDP relay, UPnP/PCP/NAT-PMP) with tight scoping.
- **VMware-ready**: stable interface role binding and a repeatable VM lab harness.

---

## 1) Scope and principles

### 1.1 Core principles
- **Fast data plane:** Linux routing + nftables + tc; UI/control plane never sits inline.
- **Safe apply:** staged config → validate → apply with rollback.
- **Least privilege:** services bind only where needed; mgmt is LAN-only by default.
- **Deterministic config:** no templating/eval in config files.
- **Dynamic segments:** not hard-coded to LAN/OPT1; use stable IDs + editable labels.
- **Stable NIC identity:** bind interface roles by **MAC address** (and optionally PCI path) to avoid renumbering issues.

### 1.2 Supported architectures
- Deployable on Ubuntu-supported architectures where packages exist (amd64/arm64 first).
- Performance tiering (UI shows expected throughput class).

---

## 2) Hardware mapping (your pfSense replacement)

Your pfSense topology:
- **LAN:** 192.168.101.0/24
- **OPT1:** 192.168.102.0/24 (printers + streaming devices)
- **WAN physical:** Intel X540 10G port
- **WAN logical:** PPPoE interface

Important constraints:
- Your inside links are **1G + 1G**, so single-client is capped at 1G, while aggregate can approach ~2G with simultaneous LAN+OPT1 load.
- You can later add NICs and/or use the 2nd X540 port for higher LAN capacity.

---

## 3) VMware VM testing strategy (new)

### 3.1 What VM testing is for
VMs are used to validate:
- correctness (routing/NAT/firewall, DHCP/DNS, VPN, QoS/DSCP, discovery modules)
- “safe apply” and rollback behavior
- installer and UX flows
- backup/restore, audit logs, monitoring/self-heal logic

### 3.2 What VM testing is **not** for
VM results are **not a proxy** for bare-metal PPPoE throughput targets (e.g., 5+ Gbps).  
Final performance acceptance must be done on the reference hardware (i5-6500 + X540-AT2) with real PPPoE.

### 3.3 VMware lab topology (recommended)
Use three routed segments plus an ISP simulator:
- **WAN-LAB** network (AuroraGW WAN)
- **LAN** network (192.168.101.0/24)
- **OPT1** network (192.168.102.0/24)
- **ISP Simulator VM** provides DHCP/NAT for bootstrap; optional PPPoE server for PPP testing

### 3.4 NIC role binding requirement (critical)
AuroraGW must bind roles by **MAC address** (not Linux `ens*` names):
- WAN role uses `interfaces.wan.mac`
- LAN role uses `interfaces.lan.mac`
- OPT1 role uses `interfaces.opt1.mac`

The apply engine resolves current Linux ifnames from MACs at apply time, ensuring stability across:
- VMware NIC renumbering
- kernel updates
- hardware changes

---

## 4) Install + bootstrap workflow (fresh Ubuntu Server)

### 4.1 Stage 0 — Bootstrap mode (WAN DHCP for initial downloads)
Requirement: WAN NIC is temporarily connected to an upstream router/modem (or ISP simulator VM) and pulls DHCP to fetch packages.

**Behavior**
- WAN physical: DHCP enabled temporarily
- LAN/OPT segments: static IPs
- Firewall: default deny inbound WAN
- Mgmt plane: LAN-only + mgmt subnet allowlist
- Installer downloads dependencies and enables selected modules

### 4.2 Stage 1 — Go-live mode (WAN PPPoE)
- Switch WAN from DHCP → PPPoE via safe apply
- Bring up PPPoE with `pppd`
- Apply nftables + tc configuration
- Health checks; commit or rollback

### 4.3 Re-runnable configuration
- TUI installer: `auroragw-install --reconfigure`
- Web wizard (LAN-only) can be re-run; both drive the same backend validation/apply pipeline

---

## 5) Architecture

### 5.1 Planes

**Data plane**
- Linux forwarding (routing/NAT)
- nftables for filtering/NAT; optional **flowtable** fastpath in Throughput mode
- tc qdiscs (CAKE) for QoS

**Control plane**
- `auroragd` service:
  - validates config schema + semantics
  - renders netplan/nft/tc/pppd/module configs
  - applies changes safely (rollback timers)
  - monitors health + self-heal actions

**Management plane**
- AuroraGW Web UI + API (LAN-only by default)
- Optional Cockpit (LAN-only system console)
- RBAC + audit logs

### 5.2 “Safe apply” model
- Active: `/etc/auroragw/config.yaml`
- Staged: `/etc/auroragw/config.staging.yaml`
- Render: `/run/auroragw/…`
- Apply steps:
  1) Schema validate  
  2) Semantic validate  
  3) Render artifacts  
  4) Preflight (`nft --check`, netplan generate)  
  5) Apply with watchdog  
  6) Health checks (LAN reachable, PPP up, default route, DNS)  
  7) Commit or rollback  

---

## 6) Configuration model (dynamic segments + stable interfaces)

### 6.1 Interfaces (MAC binding)
```yaml
interfaces:
  wan:
    mac: "aa:bb:cc:dd:ee:ff"
  lan:
    mac: "11:22:33:44:55:66"
  opt1:
    mac: "77:88:99:aa:bb:cc"
```

### 6.2 Segments (dynamic)
```yaml
segments:
  - id: lan
    label: "LAN"
    role: lan
    ifref: lan
    address: 192.168.101.1/24

  - id: opt1
    label: "OPT1"
    role: dmz
    ifref: opt1
    address: 192.168.102.1/24
```

`ifref` points to an entry in `interfaces` (MAC-bound).

---

## 7) Core routing/firewall/services (MVP “Install Now”)

### 7.1 Network management
- netplan + systemd-networkd
- Interface role resolution via MAC binding

### 7.2 WAN PPPoE
- PPPoE via `pppd`
- MTU 1492 default
- Optional VLAN tagging (ISP dependent)

### 7.3 Firewall/NAT
- nftables: stateful firewall + NAT
- Default deny inbound from WAN
- Segment-to-segment policies (OPT1 blocked to LAN by default)

### 7.4 DHCP/DNS
- Install-now baseline: **dnsmasq** (DHCP + DNS)
- Roadmap: Kea (DHCP) + Unbound (DNS resolver + local zones)

Note: **dnsmasq remains the default** DHCP/DNS service for the simplest initial setup; Kea/Unbound are available as optional modules.

### 7.5 VPN
- Install-now: WireGuard

---

## 8) QoS + DSCP (Install Now)

### 8.1 Segment SQM
- CAKE per inside segment interface (LAN and OPT1) at ~95% of link speed (wizard default)
- Default CAKE mode: `diffserv4` when DSCP marking is enabled

### 8.2 DSCP marking
- nftables mangle rules mark DSCP based on UI-defined rules (segment/proto/ports/IPs)
- CAKE uses DSCP to prioritize traffic

---

## 9) Home-device compatibility (Install Now + Roadmap)

Devices split across **LAN ↔ OPT1**:
- AirPrint printers (HP/Epson) on OPT1
- Chromecast/Cast devices on OPT1
- Samsung TV / DLNA / FireTV type devices on OPT1
- UPnP port mapping from LAN (OPT1 optional)

### 9.1 mDNS/Bonjour (Install Now)
- Install `avahi-daemon`
- Enable reflector mode scoped **only** between LAN ↔ OPT1

**Firewall preset: “LAN → OPT1: Printers + Cast”**
- mDNS discovery: UDP 5353 to/from reflector
- Printing baseline: TCP 631 (IPP/IPPS)
- Cast baseline: TCP 8008–8009 (configurable preset + advanced overrides)

### 9.2 SSDP discovery relay (Roadmap)
- Add “Discovery Relay” module (SSDP UDP 1900) for DLNA/TV/FireTV discovery
- OFF by default; enable only between selected segment pairs

### 9.3 UPnP IGD + NAT-PMP + PCP port mapping (Install Now)
- Install `miniupnpd` with nftables backend where available
- Enabled on **LAN** by default; OPT1 optional
- Security defaults: allowlist subnets, port range limits (≥1024 default), lease limits, audit mappings

---

## 10) Monitoring, uptime/downtime, self-heal (Install Now)

- PPPoE session status + uptime
- Link state per interface
- Bandwidth usage per interface (basic counters)
- QoS stats from `tc -s qdisc`
- Self-heal: PPPoE restart with backoff, route/DNS recovery attempts
- Incident timeline in UI

---

## 11) Logging, auditing, backup/restore (Install Now)

- Audit log: who/what/when + diff + apply result
- Backup: `/etc/auroragw/` config + secrets + module enablement
- Restore: validate → safe apply (rollback on failed health checks)
- UI: download backup + upload restore

---

## 12) Updates and security maintenance (Install Now + Roadmap)

- Install-now: unattended security updates enabled and verified
- Roadmap: appliance-grade upgrades with rollback (A/B or snapshots)

---

## 13) Lab automation / test harness (new deliverable)

### 13.1 Repo deliverable
Add `lab/` with:
- VMware network/port-group layout documentation (Workstation/ESXi variants)
- Example VM definitions (NIC mapping per role)
- Bring-up checklist
- Traffic/test scripts

### 13.2 Smoke test requirements
A single script (or suite) that verifies:
- WAN bootstrap DHCP works + internet reachability
- Web UI reachable from LAN only (and blocked from WAN)
- NAT/segmentation correctness (OPT1 blocked to LAN by default)
- DHCP/DNS per segment
- CAKE attached on LAN and OPT1
- DSCP marking rules active
- mDNS reflector works (LAN discovers OPT1 services)
- UPnP mappings work on LAN (OPT1 optional) + mapping logs exist
- Backup/restore validate-only + apply succeeds
- WAN failure simulation triggers self-heal and logs incident

---

# Feature Capability Matrix (v1.2)

| Feature / Capability | Install Now | Future Roadmap |
|---|:---:|:---:|
| VMware lab support (documented topology + acceptance criteria) | Yes | Yes |
| Stable NIC role binding by MAC (no reliance on ens* naming) | Yes | Yes |
| Lab automation harness (`lab/` + smoke tests) | Yes | Yes |
| Fresh Ubuntu Server deployment | Yes | Yes |
| Deployable on multiple architectures (amd64/arm64 first) | Yes | Yes |
| Interactive first-boot installer (TUI) | Yes | Yes |
| Re-runnable installer / reconfigure mode | Yes | Yes |
| Web-based setup wizard (LAN-only) | Yes | Yes |
| AuroraGW Web Admin UI (pfSense-like) | Yes | Yes |
| Optional Cockpit system console (LAN-only) | Yes | Yes |
| Source-of-truth config + staging + safe apply/rollback | Yes | Yes |
| WAN bootstrap via DHCP for initial downloads | Yes | Yes |
| PPPoE WAN via `pppd` | Yes | Yes |
| IPv4 routing + NAT | Yes | Yes |
| Stateful firewall (nftables) | Yes | Yes |
| nftables flowtable fastpath option | Yes | Yes |
| Dynamic segments (LAN/OPT1/extra NICs) | Yes | Yes |
| DHCP/DNS baseline (dnsmasq) | Yes | No |
| DHCP upgrade (Kea) | Yes (Optional module; default OFF) | Yes |
| DNS resolver upgrade (Unbound) | Yes (Optional module; default OFF) | Yes |
| WireGuard VPN | Yes | Yes |
| OpenVPN VPN | No | Yes |
| QoS: CAKE per segment | Yes | Yes |
| DSCP marking rules (nft mangle) | Yes | Yes |
| WAN SQM (CAKE on PPPoE + IFB ingress) | No | Yes |
| Monitoring: PPPoE uptime/link state/basic bandwidth | Yes | Yes |
| Self-heal: PPPoE restart + incident timeline | Yes | Yes |
| Audit logging of config changes | Yes | Yes |
| Backup/restore (CLI + UI) | Yes | Yes |
| mDNS/Bonjour (Avahi) | Yes | Yes |
| mDNS reflector scoped to LAN↔OPT1 | Yes | Yes |
| AirPrint support across segments via mDNS (and print ports) | Yes | Yes |
| Chromecast/Cast discovery across segments via mDNS | Yes | Yes |
| SSDP relay module for DLNA/TV/FireTV discovery (UDP 1900) | No | Yes |
| Streaming “preset” ruleset (Cast + printing scoped LAN→OPT1) | Yes | Yes |
| UPnP IGD + NAT-PMP + PCP (LAN default; OPT1 optional) | Yes | Yes |
| Suricata IDS (alert-only) | Yes (Optional module; default OFF) | Yes |
| Prometheus monitoring + scheduled speed tests | No | Yes |
| Appliance-grade OS upgrades w/ rollback | No | Yes |

---

## References

- CAKE qdisc manual: https://man7.org/linux/man-pages/man8/tc-cake.8.html  
- Netfilter flowtable docs: https://docs.kernel.org/networking/nf_flowtable.html  
- Ubuntu Server automatic security updates: https://documentation.ubuntu.com/server/how-to/software/automatic-updates/  
- WireGuard quick start: https://www.wireguard.com/quickstart/  
- Apple AirPrint DNS-SD records guidance: https://support.apple.com/en-ca/guide/deployment/dep3b4cf515/web  
- Google Cast discovery: https://developers.google.com/cast/docs/discovery  
- Avahi config (reflector): https://manpages.debian.org/unstable/avahi-daemon/avahi-daemon.conf.5.en.html  
- SSDP port registry (1900/udp): https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xhtml?search=1900  
- multicast-relay (SSDP/mDNS relay tool): https://github.com/alsmith/multicast-relay  
- miniupnpd nftables backend package: https://packages.debian.org/sid/miniupnpd-nftables  
