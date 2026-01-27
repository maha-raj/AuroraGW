# AuroraGW (Bootstrap Build Kit)

This ZIP contains a **working starter implementation** of AuroraGW that you can deploy on a **fresh Ubuntu Server** VM (VMware) to validate:

- Interactive installer (TUI-ish)
- MAC-based interface role binding (stable in VMware)
- nftables firewall + NAT baseline
- dnsmasq DHCP/DNS baseline (LAN + OPT1)
- mDNS/Bonjour **reflector** for LAN↔OPT1 discovery (AirPrint + Chromecast discovery)
- UPnP IGD + NAT-PMP + PCP via **miniupnpd nftables backend** (LAN default; OPT1 optional)
- Backup/restore + auditing scaffolding

> VM performance is for **correctness**, not your final 5+Gbps PPPoE target.

## Requirements

- Ubuntu Server (recommended: latest interim release you plan to run on the appliance)
- 3 NICs in VMware:
  - WAN (WAN-LAB / ISP simulator network)
  - LAN (192.168.101.0/24)
  - OPT1 (192.168.102.0/24)

## Quick start (VMware lab)

1. Create your VM networks (example: VMnet2=WAN-LAB, VMnet3=LAN, VMnet4=OPT1).
2. Boot a fresh Ubuntu Server on the AuroraGW VM.
3. Copy/unzip this project to the VM, then:

```bash
cd auroragw
sudo ./auroragw-install.sh
```

4. After install, access the (placeholder) management endpoint on LAN:
- http://192.168.101.1:8080/status

5. Run smoke tests:

```bash
sudo ./scripts/smoke-tests.sh
```

## What gets installed

Core:
- nftables
- dnsmasq
- ppp (pppd) + rp-pppoe (PPPoE client)
- avahi-daemon (mDNS reflector; `enable-reflector` option documented here: https://linux.die.net/man/5/avahi-daemon.conf)
- miniupnpd-nftables (UPnP/PCP/NAT-PMP using nftables backend; Ubuntu package: https://launchpad.net/ubuntu/noble/amd64/miniupnpd-nftables)
- python3 + pyyaml + jsonschema

Optional modules (installed only if selected):
- unbound
- kea-dhcp4-server / kea-dhcp6-server
- suricata (installed but disabled by default)

Automatic security updates:
- can enable `unattended-upgrades` (Ubuntu Server docs: https://documentation.ubuntu.com/server/how-to/software/automatic-updates/)

## Reconfigure

```bash
sudo /opt/auroragw/auroragw-install.sh --reconfigure
```

or edit `/etc/auroragw/config.staging.yaml` and:

```bash
sudo auroragd validate /etc/auroragw/config.staging.yaml
sudo auroragd apply --commit /etc/auroragw/config.staging.yaml
```


## v0.2 additions (this build)

- **PPPoE bring-up** via `pppd` peer file + `pon/poff`, managed by `systemd` service.
- **WAN SQM**: CAKE on PPPoE egress + IFB-based ingress shaping (optional; enable in config).
- **DSCP marking hooks** via nftables `inet mangle` (optional mapping; safe defaults preserve DSCP).
- **Safe apply + rollback** (starter): backups, apply, health checks, auto rollback on failure.
- **Suricata IDS wiring** (optional): runs Suricata in IDS mode on selected interfaces (alert-only).

References (for deeper reading):
- CAKE qdisc overhead/mpu: https://man7.org/linux/man-pages/man8/tc-cake.8.html
- IFB ingress redirect: https://serverfault.com/questions/350023/tc-ingress-policing-and-ifb-mirroring
- Ubuntu PPPoE peers + pon/poff: https://help.ubuntu.com/community/ADSLPPPoE
- Suricata quickstart: https://docs.suricata.io/en/suricata-8.0.1/quickstart.html
- nftables quick reference: https://wiki.nftables.org/wiki-nftables/index.php/Quick_reference-nftables_in_10_minutes
