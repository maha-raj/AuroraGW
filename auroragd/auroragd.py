#!/usr/bin/env python3
import argparse, json, os, shutil, subprocess, tarfile, time
from pathlib import Path
import yaml
import jsonschema

BASE = Path("/opt/auroragw")
SCHEMA_PATH = BASE / "config/schema.json"

CFG_DIR = Path("/etc/auroragw")
ACTIVE = CFG_DIR / "config.yaml"
STAGING = CFG_DIR / "config.staging.yaml"

BACKUP_ROOT = Path("/var/lib/auroragw/backup")
ARCHIVE_ROOT = Path("/var/lib/auroragw/backups")
PENDING = Path("/run/auroragw/pending.json")

AUDIT = Path("/var/log/auroragw/audit.log")

def sh(cmd, check=True):
    return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def log(msg: str):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

def load_cfg(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def validate_cfg(cfg: dict):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(cfg, schema)

def mac_map():
    out = sh(["bash","-lc","ip -o link"]).stdout.splitlines()
    m = {}
    for line in out:
        parts = line.split()
        ifname = parts[1].rstrip(":")
        if "link/ether" in parts:
            mac = parts[parts.index("link/ether")+1].lower()
            m[mac] = ifname
    return m

def ifname_for(cfg, ifref: str, ifs: dict):
    mac = cfg["interfaces"][ifref]["mac"].lower()
    return ifs[mac]

def render_netplan(cfg, ifs):
    wan_if = ifname_for(cfg,"wan",ifs)
    dhcp4 = "true" if cfg["wan"]["mode"] == "dhcp" else "false"
    ethernets = {
        wan_if: {"dhcp4": dhcp4, "dhcp6": "false", "optional": "true"},
        ifname_for(cfg,"lan",ifs): {"dhcp4": "false", "optional": "true"},
        ifname_for(cfg,"opt1",ifs): {"dhcp4": "false", "optional": "true"},
    }

    import ipaddress
    vlans = {}
    for seg in cfg.get("segments", []):
        addr = seg["address"]
        ifref = seg["ifref"]
        vlan = seg.get("vlan")
        if vlan and vlan.get("id") is not None:
            parent = ifname_for(cfg, vlan.get("parent_ifref", ifref), ifs)
            vid = int(vlan["id"])
            name = f"{parent}.{vid}"
            vlans[name] = {"id": vid, "link": parent, "addresses": [addr], "optional": True}
        else:
            base = ifname_for(cfg, ifref, ifs)
            ethernets.setdefault(base, {"dhcp4":"false","optional":"true"})
            ethernets[base].setdefault("addresses", [])
            ethernets[base]["addresses"].append(addr)

    # simple YAML emit
    lines = ["network:","  version: 2","  renderer: networkd","  ethernets:"]
    for ifn, v in ethernets.items():
        lines.append(f"    {ifn}:")
        for k,val in v.items():
            if isinstance(val, list):
                lines.append(f"      {k}: [{', '.join(val)}]")
            else:
                lines.append(f"      {k}: {val}")
    if vlans:
        lines.append("  vlans:")
        for ifn, v in vlans.items():
            lines.append(f"    {ifn}:")
            lines.append(f"      id: {v['id']}")
            lines.append(f"      link: {v['link']}")
            lines.append(f"      addresses: [{', '.join(v['addresses'])}]")
            lines.append("      optional: true")
    return "\n".join(lines) + "\n"

def render_nft(cfg, ifs):
    wan_if = ifname_for(cfg,"wan",ifs)
    lan_if = ifname_for(cfg,"lan",ifs)
    opt_if = ifname_for(cfg,"opt1",ifs)
    nat_oif = "pppoe0" if cfg["wan"]["mode"] == "pppoe" else wan_if
    discovery_enabled = bool((cfg.get("services", {}) or {}).get("discovery_relay", {}).get("enabled", False))
    cockpit_enabled = bool((cfg.get("services", {}) or {}).get("cockpit", {}).get("enabled", False))

    dscp_rules = (cfg.get("services", {}).get("qos", {}) or {}).get("dscp_rules", []) or []
    dscp_lines=[]
    for r in dscp_rules:
        proto=r.get("proto"); dport=r.get("dport"); dscp=r.get("dscp")
        if proto in ("tcp","udp") and isinstance(dport,int) and isinstance(dscp,str):
            dscp_lines.append(f"{proto} dport {dport} ip dscp set {dscp}")
    dscp_block = "\n    ".join(dscp_lines) if dscp_lines else "# (no DSCP rewrite rules configured)"

    pf = cfg.get("firewall", {}).get("port_forwards", []) or []
    dnat_lines=[]
    for r in pf:
        proto=r["proto"]; wp=int(r["wan_port"]); lip=r["lan_ip"]; lp=int(r["lan_port"])
        dnat_lines.append(f"{proto} dport {wp} dnat to {lip}:{lp}")
    dnat_block = "\n    ".join(dnat_lines) if dnat_lines else "# (no port forwards configured)"
    port_fwd_enabled = bool(dnat_lines)

    return f'''flush ruleset

table inet mangle {{
  chain prerouting {{
    type filter hook prerouting priority -150; policy accept;
    {dscp_block}
  }}
}}

table inet filter {{
  chain input {{
    type filter hook input priority 0;
    policy drop;

    iif lo accept
    ct state established,related accept

    # mgmt: LAN only (ssh + web)
    iifname "{lan_if}" tcp dport {{22,8443{',9090' if cockpit_enabled else ''}}} accept

    # DHCP/DNS to router (LAN/OPT1)
    iifname "{lan_if}" udp dport {{53,67,547,5353,1900}} accept
    iifname "{lan_if}" tcp dport 53 accept
    iifname "{opt_if}" udp dport {{53,67,547,5353,1900}} accept
    iifname "{opt_if}" tcp dport 53 accept

    # ICMP
    iifname "{lan_if}" icmp type echo-request accept
    iifname "{opt_if}" icmp type echo-request accept
  }}

  chain forward {{
    type filter hook forward priority 0;
    policy drop;

    ct state established,related accept

    iifname "{lan_if}" oifname "{nat_oif}" accept
    iifname "{opt_if}" oifname "{nat_oif}" accept

    # Allow inbound forwarded traffic only when a DNAT rule exists.
    # Without DNAT, inbound to the router hits the input chain (policy drop).
    {'iifname "' + nat_oif + '" oifname "' + lan_if + '" ct state new accept' if port_fwd_enabled else '# (no port forwards; WAN->LAN forwarding disabled)'}
    {'iifname "' + nat_oif + '" oifname "' + opt_if + '" ct state new accept' if port_fwd_enabled else '# (no port forwards; WAN->OPT1 forwarding disabled)'}

    # Home layout default:
    # - LAN -> OPT1 allowed (PCs/controllers reach printers/IoT)
    # - OPT1 -> LAN blocked by default (IoT isolation)
    iifname "{lan_if}" oifname "{opt_if}" accept
    {'iifname "' + opt_if + '" oifname "' + lan_if + '" udp dport {5353,1900} accept' if discovery_enabled else '# (discovery relay disabled; OPT1 -> LAN remains blocked)'}
  }}
}}

table ip nat {{
  chain prerouting {{
    type nat hook prerouting priority -100;
    iifname "{nat_oif}" {dnat_block}
  }}
  chain postrouting {{
    type nat hook postrouting priority 100;
    oifname "{nat_oif}" masquerade
  }}
}}
'''

def render_pppoe(cfg, ifs):
    if cfg["wan"]["mode"] != "pppoe":
        return None, None
    wan_phy = ifname_for(cfg,"wan",ifs)
    ppp = cfg.get("wan", {}).get("pppoe", {}) or {}
    user = (ppp.get("username","") or "").strip()
    pwd = ppp.get("password","") or ""
    mtu = int(ppp.get("mtu", 1492))
    if not user or not pwd:
        raise RuntimeError("PPPoE selected but username/password empty.")
    peer = f'''noipdefault
defaultroute
replacedefaultroute
hide-password
lcp-echo-interval 20
lcp-echo-failure 3
noauth
persist
mtu {mtu}
mru {mtu}
plugin rp-pppoe.so
{wan_phy}
user "{user}"
'''
    chap = f'"{user}" * "{pwd}"'
    return peer, chap

def render_unbound(cfg, ifs):
    lan_if = ifname_for(cfg,"lan",ifs)
    opt_if = ifname_for(cfg,"opt1",ifs)
    ips = sh(["bash","-lc", f"ip -4 -o addr show dev {lan_if} | awk '{{print $4}}'"]).stdout.strip().splitlines()
    ips += sh(["bash","-lc", f"ip -4 -o addr show dev {opt_if} | awk '{{print $4}}'"]).stdout.strip().splitlines()
    addrs = [ip.split('/')[0] for ip in ips if ip]
    listen = "\n".join([f"  interface: {a}" for a in addrs]) if addrs else "  interface: 0.0.0.0"
    return f'''server:
  verbosity: 1
{listen}
  port: 53
  do-ip6: no
  hide-identity: yes
  hide-version: yes
  harden-glue: yes
  harden-dnssec-stripped: yes
  qname-minimisation: yes
  prefetch: yes

forward-zone:
  name: "."
  forward-tls-upstream: no
  forward-addr: 1.1.1.1
  forward-addr: 9.9.9.9
'''

def render_kea_dhcp4(cfg, ifs):
    import ipaddress
    subs=[]
    for seg in cfg.get("segments", []):
        dh = (seg.get("dhcp") or {})
        if not dh.get("enabled", False):
            continue
        iface = ifname_for(cfg, seg["ifref"], ifs)
        subnet = str(ipaddress.ip_interface(seg["address"]).network)
        rs = dh.get("range_start"); re = dh.get("range_end")
        if not (rs and re):
            continue
        router_ip = str(ipaddress.ip_interface(seg["address"]).ip)
        subs.append({
            "subnet": subnet,
            "interface": iface,
            "pools": [{"pool": f"{rs} - {re}"}],
            "option-data": [
                {"name":"routers", "data": router_ip},
                {"name":"domain-name-servers", "data": router_ip}
            ]
        })
    kea = {
      "Dhcp4": {
        "interfaces-config": {"interfaces": list({s["interface"] for s in subs})},
        "lease-database": {"type":"memfile", "persist": True, "name": "/var/lib/kea/kea-leases4.csv"},
        "valid-lifetime": 43200,
        "renew-timer": 21600,
        "rebind-timer": 37800,
        "subnet4": subs
      }
    }
    return json.dumps(kea, indent=2)

def render_miniupnpd(cfg, ifs):
    upnp = cfg.get("services", {}).get("upnp", {}) or {}
    if not upnp.get("enabled", False):
        return None
    wan_if = ifname_for(cfg,"wan",ifs)
    ext = "pppoe0" if cfg["wan"]["mode"] == "pppoe" else wan_if
    segs = upnp.get("segments_enabled", ["lan"])
    internal=[]
    for s in segs:
        if s in ("lan","opt1"):
            internal.append(ifname_for(cfg, s, ifs))
    conf = "\n".join([f"ext_ifname={ext}"] + [f"internal_iface={i}" for i in internal] + [
      "secure_mode=yes",
      "system_uptime=yes",
    ]) + "\n"
    return conf

def render_discovery_relay(cfg, ifs):
    d = cfg.get("services", {}).get("discovery_relay", {}) or {}
    if not d.get("enabled", False):
        return None
    segs = d.get("segments", ["lan","opt1"])
    ifaces=[]
    for s in segs:
        if s in ("lan","opt1"):
            ifaces.append(ifname_for(cfg, s, ifs))
    return ifaces

def resolve_iface_token(cfg, ifs, token: str):
    t = (token or "").strip()
    if not t:
        return None
    if t in ("wan", "lan", "opt1"):
        return ifname_for(cfg, t, ifs)
    if t == "pppoe0":
        return "pppoe0"
    return t

def apply_suricata(cfg, ifs):
    s = (cfg.get("services", {}).get("suricata", {}) or {})
    if not s.get("installed", False):
        return
    enabled = bool(s.get("enabled", False))
    iface_tokens = s.get("interfaces", []) or []
    ifnames = []
    for tok in iface_tokens:
        ifname = resolve_iface_token(cfg, ifs, str(tok))
        if ifname:
            ifnames.append(ifname)
    ifnames = list(dict.fromkeys(ifnames))

    # Best-effort: stop any previously enabled instances we know about.
    for ifn in set(ifnames + [ifname_for(cfg, "lan", ifs), ifname_for(cfg, "opt1", ifs), ifname_for(cfg, "wan", ifs), "pppoe0"]):
        sh(["bash","-lc", f"systemctl disable --now 'auroragw-suricata@{ifn}.service' 2>/dev/null || true"], check=False)

    if not enabled:
        return

    # IDS mode (passive sniff). Users can choose interfaces; avoid enabling on WAN by default for throughput.
    for ifn in ifnames:
        sh(["bash","-lc", f"ip link show '{ifn}' >/dev/null 2>&1 || true"], check=False)
        sh(["bash","-lc", f"systemctl enable --now 'auroragw-suricata@{ifn}.service'"], check=False)
        sh(["bash","-lc", f"systemctl restart 'auroragw-suricata@{ifn}.service'"], check=False)

def apply_cockpit(cfg):
    enabled = bool((cfg.get("services", {}) or {}).get("cockpit", {}).get("enabled", False))
    if enabled:
        sh(["bash","-lc","systemctl enable --now cockpit.socket 2>/dev/null || systemctl enable --now cockpit || true"], check=False)
    else:
        sh(["bash","-lc","systemctl disable --now cockpit.socket cockpit 2>/dev/null || true"], check=False)

def apply_qos(cfg, ifs):
    qos = cfg.get("services", {}).get("qos", {}) or {}
    if not qos.get("enabled", False):
        return
    seg_cfg = qos.get("segments", {}) or {}
    for seg_id, v in seg_cfg.items():
        if seg_id not in ("lan","opt1"):
            continue
        ifn = ifname_for(cfg, seg_id, ifs)
        rate = int(v.get("rate_kbit", 0))
        if rate > 0:
            sh(["bash","-lc", f"tc qdisc replace dev {ifn} root cake bandwidth {rate}kbit diffserv4 nat"], check=False)

    wanq = qos.get("wan", {}) or {}
    if not wanq.get("enabled", False):
        return
    if cfg["wan"]["mode"] != "pppoe":
        return
    wan_phy = ifname_for(cfg,"wan",ifs)
    egress = int(wanq.get("egress_kbit", 0))
    ingress = int(wanq.get("ingress_kbit", 0))
    overhead = int(wanq.get("overhead_bytes", 0))
    mpu = int(wanq.get("mpu_bytes", 0))
    if egress <= 0 or ingress <= 0:
        return
    script = BASE / "scripts/qos-apply.sh"
    if script.exists():
        sh(["bash","-lc", f"{script} {wan_phy} pppoe0 {egress} {ingress} {overhead} {mpu}"], check=False)

def install_units():
    unit_dir = BASE / "systemd"
    Path("/etc/systemd/system").mkdir(parents=True, exist_ok=True)
    for u in unit_dir.glob("*"):
        dst = Path("/etc/systemd/system") / u.name
        dst.write_text(u.read_text(encoding="utf-8"), encoding="utf-8")
    sh(["systemctl","daemon-reload"], check=False)

def backup_rendered(paths):
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = BACKUP_ROOT / ts
    dst.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.exists():
            rel = str(p).lstrip("/")
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)
    return dst

def rollback_from(backup_dir: Path):
    for p in backup_dir.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(backup_dir))
            dest = Path("/") / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    sh(["bash","-lc","systemctl daemon-reload || true"], check=False)
    sh(["bash","-lc","systemctl restart nftables 2>/dev/null || true"], check=False)

def health_check(cfg, ifs):
    lan_if = ifname_for(cfg,"lan",ifs)
    lan_ok = sh(["bash","-lc", f"ip -4 -br addr show dev {lan_if} | awk '{{print $3}}' | grep -q /"], check=False).returncode == 0
    nft_ok = sh(["bash","-lc","systemctl is-active --quiet nftables"], check=False).returncode == 0
    web_ok = sh(["bash","-lc","systemctl is-active --quiet auroragw-web"], check=False).returncode == 0
    ppp_ok = True
    if cfg["wan"]["mode"] == "pppoe":
        ppp_ok = sh(["bash","-lc","ip link show pppoe0 >/dev/null 2>&1"], check=False).returncode == 0
    return lan_ok and nft_ok and web_ok and ppp_ok

def write_pending(backup_dir: Path, timeout: int):
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps({"backup_dir": str(backup_dir), "timeout": timeout, "ts": time.time()}, indent=2), encoding="utf-8")
    sh(["systemctl","restart","auroragw-rollback.timer"], check=False)

def clear_pending():
    if PENDING.exists():
        PENDING.unlink()
    sh(["systemctl","stop","auroragw-rollback.timer"], check=False)

def cmd_validate(path: str):
    cfg = load_cfg(Path(path))
    validate_cfg(cfg)
    print("OK: validated")

def cmd_apply(path: str, commit: bool, require_confirm: bool, timeout: int):
    cfg_path = Path(path)
    cfg = load_cfg(cfg_path)
    validate_cfg(cfg)
    ifs = mac_map()
    for k in ["wan","lan","opt1"]:
        mac = cfg["interfaces"][k]["mac"].lower()
        if mac not in ifs:
            raise SystemExit(f"Missing interface for {k} MAC: {mac}")

    install_units()

    critical = [
        Path("/etc/netplan/99-auroragw.yaml"),
        Path("/etc/nftables.conf"),
        Path("/etc/ppp/peers/auroragw-wan"),
        Path("/etc/ppp/chap-secrets"),
        Path("/etc/kea/kea-dhcp4.conf"),
        Path("/etc/unbound/unbound.conf.d/auroragw.conf"),
        Path("/etc/miniupnpd/miniupnpd.conf"),
    ]
    backup_dir = backup_rendered(critical)

    try:
        Path("/etc/netplan").mkdir(parents=True, exist_ok=True)
        Path("/etc/netplan/99-auroragw.yaml").write_text(render_netplan(cfg, ifs), encoding="utf-8")

        Path("/etc/sysctl.d").mkdir(parents=True, exist_ok=True)
        Path("/etc/sysctl.d/99-auroragw.conf").write_text("net.ipv4.ip_forward=1\nnet.ipv6.conf.all.forwarding=1\n", encoding="utf-8")

        Path("/etc/nftables.conf").write_text(render_nft(cfg, ifs), encoding="utf-8")

        peer, chap = render_pppoe(cfg, ifs)
        if peer and chap:
            Path("/etc/ppp/peers").mkdir(parents=True, exist_ok=True)
            Path("/etc/ppp/peers/auroragw-wan").write_text(peer, encoding="utf-8")
            os.chmod("/etc/ppp/peers/auroragw-wan", 0o600)
            chap_path = Path("/etc/ppp/chap-secrets")
            existing = chap_path.read_text(encoding="utf-8") if chap_path.exists() else ""
            if chap not in existing:
                chap_path.write_text(existing + chap + "\n", encoding="utf-8")
                os.chmod(chap_path, 0o600)
            sh(["systemctl","enable","--now","auroragw-pppoe.service"], check=False)
            sh(["systemctl","restart","auroragw-pppoe.service"], check=False)
        else:
            sh(["systemctl","disable","--now","auroragw-pppoe.service"], check=False)

        sh(["sysctl","--system"], check=False)
        sh(["netplan","apply"], check=False)

        sh(["systemctl","enable","--now","nftables"], check=False)
        sh(["systemctl","restart","nftables"], check=False)

        # DHCP/DNS
        dhcp = (cfg.get("services", {}).get("dhcp", {}) or {})
        dns  = (cfg.get("services", {}).get("dns", {}) or {})

        if dhcp.get("enabled", True) and dhcp.get("provider","kea") == "kea":
            Path("/etc/kea").mkdir(parents=True, exist_ok=True)
            Path("/etc/kea/kea-dhcp4.conf").write_text(render_kea_dhcp4(cfg, ifs), encoding="utf-8")
            sh(["systemctl","enable","--now","kea-dhcp4-server"], check=False)
            sh(["systemctl","restart","kea-dhcp4-server"], check=False)

        if dns.get("enabled", True) and dns.get("provider","unbound") == "unbound":
            Path("/etc/unbound/unbound.conf.d").mkdir(parents=True, exist_ok=True)
            Path("/etc/unbound/unbound.conf.d/auroragw.conf").write_text(render_unbound(cfg, ifs), encoding="utf-8")
            sh(["systemctl","enable","--now","unbound"], check=False)
            sh(["systemctl","restart","unbound"], check=False)

        # UPnP
        upnp_conf = render_miniupnpd(cfg, ifs)
        if upnp_conf:
            Path("/etc/miniupnpd").mkdir(parents=True, exist_ok=True)
            Path("/etc/miniupnpd/miniupnpd.conf").write_text(upnp_conf, encoding="utf-8")
            sh(["systemctl","enable","--now","miniupnpd"], check=False)
            sh(["systemctl","restart","miniupnpd"], check=False)
        else:
            sh(["systemctl","disable","--now","miniupnpd"], check=False)

        # Discovery relay service (if enabled)
        ifaces = render_discovery_relay(cfg, ifs)
        if ifaces and len(ifaces) >= 2:
            svc = f'''[Unit]
Description=AuroraGW Discovery Relay (mDNS + SSDP)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/auroragw/discovery/multicast-relay.py --interfaces {" ".join(ifaces)} --wait --foreground --verbose
Restart=on-failure

[Install]
WantedBy=multi-user.target
'''
            Path("/etc/systemd/system/auroragw-discovery-relay.service").write_text(svc, encoding="utf-8")
            sh(["systemctl","daemon-reload"], check=False)
            sh(["systemctl","enable","--now","auroragw-discovery-relay.service"], check=False)
            sh(["systemctl","restart","auroragw-discovery-relay.service"], check=False)
        else:
            sh(["systemctl","disable","--now","auroragw-discovery-relay.service"], check=False)

        apply_qos(cfg, ifs)

        apply_suricata(cfg, ifs)

        apply_cockpit(cfg)

        sh(["systemctl","enable","--now","auroragw-web"], check=False)
        sh(["systemctl","restart","auroragw-web"], check=False)

        sh(["systemctl","enable","--now","auroragw-health.timer"], check=False)

        if not health_check(cfg, ifs):
            raise RuntimeError("Health check failed after apply.")
    except Exception:
        rollback_from(backup_dir)
        raise

    log(f"applied {cfg_path}")

    if commit:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        ACTIVE.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")

    if require_confirm:
        write_pending(backup_dir, timeout)
        print(f"OK: applied (pending confirm for {timeout}s)")
    else:
        clear_pending()
        print("OK: applied")

def cmd_confirm():
    clear_pending()
    log("confirmed pending apply")
    print("OK: confirmed")

def cmd_rollback_pending():
    if not PENDING.exists():
        print("OK: no pending apply")
        return
    data = json.loads(PENDING.read_text(encoding="utf-8"))
    bdir = Path(data["backup_dir"])
    rollback_from(bdir)
    clear_pending()
    log(f"rolled back pending apply from {bdir}")
    print("OK: rolled back pending apply")

def cmd_backup():
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = ARCHIVE_ROOT / f"auroragw-backup-{ts}.tgz"
    with tarfile.open(out, "w:gz") as tf:
        for p in [CFG_DIR, Path("/etc/netplan/99-auroragw.yaml"), Path("/etc/nftables.conf")]:
            if p.exists():
                tf.add(str(p), arcname=str(p).lstrip("/"))
    log(f"backup created {out}")
    print(str(out))

def cmd_restore(path: str):
    in_path = Path(path)
    if not in_path.exists():
        raise SystemExit("backup file not found")
    with tarfile.open(in_path, "r:gz") as tf:
        tf.extractall(path="/")
    cmd_apply(str(ACTIVE), commit=True, require_confirm=False, timeout=0)
    log(f"restore from {in_path}")
    print("OK: restored")

def main():
    ap = argparse.ArgumentParser(prog="auroragd")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v=sub.add_parser("validate"); v.add_argument("config")
    a=sub.add_parser("apply"); a.add_argument("config", nargs="?", default=str(STAGING))
    a.add_argument("--commit", action="store_true")
    a.add_argument("--require-confirm", action="store_true")
    a.add_argument("--timeout", type=int, default=120)

    sub.add_parser("confirm")
    sub.add_parser("rollback-pending")
    sub.add_parser("backup")
    r=sub.add_parser("restore"); r.add_argument("tgz")

    args = ap.parse_args()
    if args.cmd == "validate":
        cmd_validate(args.config)
    elif args.cmd == "apply":
        cmd_apply(args.config, commit=args.commit, require_confirm=args.require_confirm, timeout=args.timeout)
    elif args.cmd == "confirm":
        cmd_confirm()
    elif args.cmd == "rollback-pending":
        cmd_rollback_pending()
    elif args.cmd == "backup":
        cmd_backup()
    elif args.cmd == "restore":
        cmd_restore(args.tgz)

if __name__ == "__main__":
    main()
