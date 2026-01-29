#!/usr/bin/env python3
import argparse, json, os, shutil, subprocess, tarfile, time
from pathlib import Path
import yaml
import jsonschema
import ipaddress
import secrets
import re

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

def warn(msg: str):
    log(f"WARN {msg}")

def _path_meta(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p} mode={oct(st.st_mode & 0o777)} uid={st.st_uid} gid={st.st_gid}"
    except FileNotFoundError:
        return f"{p} (missing)"
    except PermissionError:
        return f"{p} (stat permission denied)"
    except Exception as e:
        return f"{p} (stat error: {e})"

def load_cfg(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def validate_cfg(cfg: dict):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(cfg, schema)

def mac_map():
    out = sh(["ip", "-o", "link"]).stdout.splitlines()
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

_IFNAME_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")

def list_system_ifnames() -> set[str]:
    base = Path("/sys/class/net")
    if not base.exists():
        return set()
    out = set()
    for p in base.iterdir():
        name = p.name
        if name and name != "lo":
            out.add(name)
    return out

def is_safe_ifname(name: str) -> bool:
    if not name:
        return False
    if not _IFNAME_RE.match(name):
        return False
    sys_if = list_system_ifnames()
    return name in sys_if or name == "pppoe0"

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
    evebox_enabled = bool((cfg.get("services", {}) or {}).get("evebox", {}).get("enabled", False))
    grafana = (cfg.get("services", {}) or {}).get("grafana", {}) or {}
    grafana_local = bool(grafana.get("enabled", False)) and (str(grafana.get("mode", "remote")).strip().lower() == "local")

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

    # PPPoE MSS clamping helps avoid PMTUD issues with typical MTU 1492.
    # MSS = MTU - 40 (IPv4) => 1452 for MTU 1492.
    mss_clamp = ""
    if cfg["wan"]["mode"] == "pppoe":
        mss = 1452
        mss_clamp = f'tcp flags syn tcp option maxseg size set {mss}'

    return f'''flush ruleset

table inet mangle {{
  chain prerouting {{
    type filter hook prerouting priority -150; policy accept;
    {dscp_block}
  }}

  chain forward {{
    type filter hook forward priority -150; policy accept;
    {mss_clamp if mss_clamp else "# (no MSS clamping; not PPPoE)"}
  }}
}}

table inet filter {{
  chain input {{
    type filter hook input priority 0;
    policy drop;

    iif lo accept
    ct state established,related accept

    # mgmt: LAN only (ssh + web)
    iifname "{lan_if}" tcp dport {{22,8443{',9090' if cockpit_enabled else ''}{',5636' if evebox_enabled else ''}{',3000,9095' if grafana_local else ''}}} accept

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
usepeerdns
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

def discover_system_dns_servers():
    # Prefer systemd-resolved, fallback to /etc/resolv.conf.
    servers = []
    try:
        r = sh(["resolvectl", "dns"], check=False)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Formats vary slightly; safe approach: keep tokens that look like IPs.
                for tok in line.split():
                    tok = tok.strip()
                    try:
                        ipaddress.ip_address(tok)
                        servers.append(tok)
                    except Exception:
                        continue
    except Exception:
        warn("discover_system_dns_servers: resolvectl failed")
    if not servers:
        try:
            text = Path("/etc/resolv.conf").read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("nameserver "):
                    servers.append(line.split()[1].strip())
        except Exception:
            warn("discover_system_dns_servers: /etc/resolv.conf read failed")
    # Filter out local stub addresses.
    out = []
    for s in servers:
        if s in ("127.0.0.53", "127.0.0.1", "::1"):
            continue
        try:
            ipaddress.ip_address(s)
            out.append(s)
        except Exception:
            continue
    return list(dict.fromkeys(out))

def upstream_dns_servers(cfg):
    dns = (cfg.get("services", {}).get("dns", {}) or {})
    upstream = (dns.get("upstream") or {})
    mode = (upstream.get("mode") or "auto").strip().lower()
    if mode == "manual":
        servers = upstream.get("servers") or []
        out = []
        for s in servers:
            s = str(s).strip()
            if not s:
                continue
            try:
                ipaddress.ip_address(s)
                out.append(s)
            except Exception:
                continue
        return list(dict.fromkeys(out))
    # auto
    return discover_system_dns_servers()

def render_unbound_base(cfg, ifs):
    lan_if = ifname_for(cfg,"lan",ifs)
    opt_if = ifname_for(cfg,"opt1",ifs)
    ips = []
    for dev in [lan_if, opt_if]:
        if not is_safe_ifname(dev):
            continue
        r = sh(["ip", "-4", "-o", "addr", "show", "dev", dev], check=False)
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if "inet" in parts:
                try:
                    idx = parts.index("inet")
                    ips.append(parts[idx + 1])
                except Exception:
                    continue
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
include: "/etc/unbound/unbound.conf.d/auroragw-forwarders.conf"
'''

def render_unbound_forwarders(servers):
    if not servers:
        servers = ["1.1.1.1", "9.9.9.9"]
    lines = ["forward-zone:", '  name: "."', "  forward-tls-upstream: no"]
    for s in servers:
        lines.append(f"  forward-addr: {s}")
    return "\n".join(lines) + "\n"

def render_kea_dhcp4(cfg, ifs):
    import ipaddress
    upstream = upstream_dns_servers(cfg)
    subs=[]
    extra_id = 10
    for seg in cfg.get("segments", []):
        dh = (seg.get("dhcp") or {})
        if not dh.get("enabled", False):
            continue
        iface = ifname_for(cfg, seg["ifref"], ifs)
        seg_id = str(seg.get("id") or "").strip().lower()
        # Kea (2.6+) requires numeric subnet IDs.
        if seg_id == "lan":
            subnet_id = 1
        elif seg_id == "opt1":
            subnet_id = 2
        else:
            subnet_id = extra_id
            extra_id += 1
        subnet = str(ipaddress.ip_interface(seg["address"]).network)
        rs = dh.get("range_start"); re = dh.get("range_end")
        if not (rs and re):
            continue
        # Validate pool belongs to subnet (Kea will refuse to start otherwise).
        net = ipaddress.ip_interface(seg["address"]).network
        try:
            rs_ip = ipaddress.ip_address(str(rs).strip())
            re_ip = ipaddress.ip_address(str(re).strip())
        except Exception as e:
            raise RuntimeError(f"Invalid DHCP range for segment {seg.get('id','?')}: {rs} - {re} ({e})")
        if rs_ip not in net or re_ip not in net:
            raise RuntimeError(
                f"DHCP range for segment {seg.get('id','?')} must be inside {net}: {rs_ip} - {re_ip}"
            )
        if int(rs_ip) > int(re_ip):
            raise RuntimeError(f"DHCP range start must be <= end for segment {seg.get('id','?')}: {rs_ip} - {re_ip}")
        router_ip = str(ipaddress.ip_interface(seg["address"]).ip)
        dns_mode = ((dh.get("dns") or {}).get("mode") or "router").strip().lower()
        dns_servers = (dh.get("dns") or {}).get("servers") or []
        if dns_mode == "manual":
            out = []
            for s in dns_servers:
                s = str(s).strip()
                if not s:
                    continue
                try:
                    ipaddress.ip_address(s)
                    out.append(s)
                except Exception:
                    continue
            dns_data = ", ".join(out) if out else router_ip
        elif dns_mode == "inherit_wan":
            dns_data = ", ".join(upstream) if upstream else router_ip
        else:
            dns_data = router_ip
        subs.append({
            "id": subnet_id,
            "subnet": subnet,
            "interface": iface,
            "pools": [{"pool": f"{rs} - {re}"}],
            "option-data": [
                {"name":"routers", "data": router_ip},
                {"name":"domain-name-servers", "data": dns_data}
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
        name = ifname_for(cfg, t, ifs)
        return name if is_safe_ifname(name) else None
    if t == "pppoe0":
        return "pppoe0"
    # Allow only existing system interface names (prevents shell injection via config).
    return t if is_safe_ifname(t) else None

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
        if not is_safe_ifname(ifn):
            continue
        sh(["systemctl", "disable", "--now", f"auroragw-suricata@{ifn}.service"], check=False)

    if not enabled:
        return

    # IDS mode (passive sniff). Users can choose interfaces; avoid enabling on WAN by default for throughput.
    for ifn in ifnames:
        if not is_safe_ifname(ifn):
            continue
        sh(["ip", "link", "show", ifn], check=False)
        sh(["systemctl", "enable", "--now", f"auroragw-suricata@{ifn}.service"], check=False)
        sh(["systemctl", "restart", f"auroragw-suricata@{ifn}.service"], check=False)

def apply_cockpit(cfg):
    enabled = bool((cfg.get("services", {}) or {}).get("cockpit", {}).get("enabled", False))
    if enabled:
        sh(["systemctl", "enable", "--now", "cockpit.socket"], check=False)
        sh(["systemctl", "enable", "--now", "cockpit"], check=False)
    else:
        sh(["systemctl", "disable", "--now", "cockpit.socket"], check=False)
        sh(["systemctl", "disable", "--now", "cockpit"], check=False)

def apply_evebox(cfg):
    enabled = bool((cfg.get("services", {}) or {}).get("evebox", {}).get("enabled", False))
    if enabled:
        sh(["bash", "/opt/auroragw/scripts/fetch-evebox.sh"], check=False)
        sh(["systemctl", "enable", "--now", "auroragw-evebox.service"], check=False)
        sh(["systemctl", "restart", "auroragw-evebox.service"], check=False)
    else:
        sh(["systemctl", "disable", "--now", "auroragw-evebox.service"], check=False)

def apply_grafana(cfg):
    g = (cfg.get("services", {}) or {}).get("grafana", {}) or {}
    enabled = bool(g.get("enabled", False))
    mode = str(g.get("mode", "remote") or "remote").strip().lower()
    local = enabled and mode == "local"

    if not local:
        sh(["systemctl", "disable", "--now", "auroragw-observability.service"], check=False)
        return

    if sh(["docker", "--version"], check=False).returncode != 0:
        log("grafana local enabled but docker not installed (skipping start)")
        return

    sh(["systemctl", "enable", "--now", "docker"], check=False)

    obs_dir = CFG_DIR / "observability"
    obs_dir.mkdir(parents=True, exist_ok=True)

    # Ensure env file for docker-compose substitution.
    secrets_path = CFG_DIR / "secrets.yaml"
    s = {}
    if secrets_path.exists():
        try:
            s = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        except Exception:
            s = {}
    if not s.get("grafana_admin_password"):
        s["grafana_admin_password"] = secrets.token_urlsafe(20)
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        secrets_path.write_text(yaml.safe_dump(s, sort_keys=False), encoding="utf-8")
        try:
            os.chmod(secrets_path, 0o600)
        except Exception:
            pass

    env_path = obs_dir / "observability.env"
    env_path.write_text(f'GRAFANA_ADMIN_PASSWORD={s.get("grafana_admin_password")}\n', encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except Exception:
        pass

    # Prometheus scrape config (local docker -> scrape host web via host-gateway).
    tpl = BASE / "observability/local/prometheus.yml"
    if tpl.exists():
        (obs_dir / "prometheus.yml").write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")

    sh(["systemctl", "enable", "--now", "auroragw-observability.service"], check=False)
    sh(["systemctl", "restart", "auroragw-observability.service"], check=False)

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
            if not is_safe_ifname(ifn):
                continue
            sh(["tc", "qdisc", "replace", "dev", ifn, "root", "cake", "bandwidth", f"{rate}kbit", "diffserv4", "nat"], check=False)

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
        if is_safe_ifname(wan_phy):
            sh(["bash", str(script), wan_phy, "pppoe0", str(egress), str(ingress), str(overhead), str(mpu)], check=False)

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
    sh(["systemctl", "daemon-reload"], check=False)
    sh(["systemctl", "restart", "nftables"], check=False)

def health_check(cfg, ifs):
    lan_if = ifname_for(cfg,"lan",ifs)
    lan_ok = False
    if is_safe_ifname(lan_if):
        r = sh(["ip", "-4", "-br", "addr", "show", "dev", lan_if], check=False)
        # Example: "eth1             UP             192.168.101.1/24"
        lan_ok = "/" in (r.stdout or "")
    nft_ok = sh(["systemctl", "is-active", "--quiet", "nftables"], check=False).returncode == 0
    web_ok = sh(["systemctl", "is-active", "--quiet", "auroragw-web"], check=False).returncode == 0
    ppp_ok = True
    if cfg["wan"]["mode"] == "pppoe":
        ppp_ok = sh(["ip", "link", "show", "pppoe0"], check=False).returncode == 0

    dhcp = (cfg.get("services", {}).get("dhcp", {}) or {})
    dhcp_ok = True
    if dhcp.get("enabled", True) and dhcp.get("provider", "kea") == "kea":
        dhcp_ok = sh(["systemctl", "is-active", "--quiet", "kea-dhcp4-server"], check=False).returncode == 0

    dns = (cfg.get("services", {}).get("dns", {}) or {})
    dns_ok = True
    if dns.get("enabled", True) and dns.get("provider", "unbound") == "unbound":
        dns_ok = sh(["systemctl", "is-active", "--quiet", "unbound"], check=False).returncode == 0

    return lan_ok and nft_ok and web_ok and ppp_ok and dhcp_ok and dns_ok

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
            try:
                os.chmod("/etc/kea", 0o755)
            except Exception:
                pass
            kea_conf = Path("/etc/kea/kea-dhcp4.conf")
            kea_conf.write_text(render_kea_dhcp4(cfg, ifs), encoding="utf-8")
            try:
                os.chmod(kea_conf, 0o644)
            except Exception:
                pass
            test = sh(["kea-dhcp4", "-t", "/etc/kea/kea-dhcp4.conf"], check=False)
            if test.returncode != 0:
                raise RuntimeError(
                    "Kea config test failed (kea-dhcp4 -t /etc/kea/kea-dhcp4.conf).\n"
                    f"config file: {_path_meta(Path('/etc/kea/kea-dhcp4.conf'))}\n"
                    f"config dir:  {_path_meta(Path('/etc/kea'))}\n"
                    f"stdout:\n{test.stdout}\n"
                    f"stderr:\n{test.stderr}\n"
                )
            sh(["systemctl","enable","--now","kea-dhcp4-server"], check=False)
            sh(["systemctl","restart","kea-dhcp4-server"], check=False)

        if dns.get("enabled", True) and dns.get("provider","unbound") == "unbound":
            Path("/etc/unbound/unbound.conf.d").mkdir(parents=True, exist_ok=True)
            try:
                os.chmod("/etc/unbound", 0o755)
            except Exception:
                pass
            try:
                os.chmod("/etc/unbound/unbound.conf.d", 0o755)
            except Exception:
                pass
            ub_base = Path("/etc/unbound/unbound.conf.d/auroragw.conf")
            ub_fwd = Path("/etc/unbound/unbound.conf.d/auroragw-forwarders.conf")
            ub_base.write_text(render_unbound_base(cfg, ifs), encoding="utf-8")
            ub_fwd.write_text(
                render_unbound_forwarders(upstream_dns_servers(cfg)), encoding="utf-8"
            )
            for p in (ub_base, ub_fwd):
                try:
                    os.chmod(p, 0o644)
                except Exception:
                    pass
            # Ensure DNSSEC root key exists (required by default unbound.conf).
            root_key = Path("/var/lib/unbound/root.key")
            if not root_key.exists():
                try:
                    root_key.parent.mkdir(parents=True, exist_ok=True)
                    os.chmod(root_key.parent, 0o755)
                except Exception:
                    pass
                if shutil.which("unbound-anchor"):
                    sh(["unbound-anchor", "-a", str(root_key)], check=False)
                else:
                    # Fallback to packaged root key if available.
                    fallback = Path("/usr/share/dns-root-data/root.key")
                    if fallback.exists():
                        try:
                            shutil.copy2(fallback, root_key)
                        except Exception as e:
                            warn(f"failed to copy root.key from {fallback}: {e}")
                    else:
                        warn("unbound-anchor not found; /var/lib/unbound/root.key may be missing")
            if root_key.exists():
                try:
                    os.chmod(root_key, 0o644)
                except Exception:
                    pass
            utest = sh(["unbound-checkconf"], check=False)
            if utest.returncode != 0:
                raise RuntimeError(
                    "Unbound config check failed (unbound-checkconf).\n"
                    f"stdout:\n{utest.stdout}\n"
                    f"stderr:\n{utest.stderr}\n"
                )
            sh(["systemctl","enable","--now","unbound"], check=False)
            sh(["systemctl","restart","unbound"], check=False)

        # UPnP
        upnp_conf = render_miniupnpd(cfg, ifs)
        if upnp_conf:
            Path("/etc/miniupnpd").mkdir(parents=True, exist_ok=True)
            upnp_path = Path("/etc/miniupnpd/miniupnpd.conf")
            upnp_path.write_text(upnp_conf, encoding="utf-8")
            try:
                os.chmod(upnp_path, 0o644)
            except Exception:
                pass
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

        apply_evebox(cfg)

        apply_grafana(cfg)

        sh(["systemctl","enable","--now","auroragw-web"], check=False)
        sh(["systemctl","restart","auroragw-web"], check=False)

        sh(["systemctl","enable","--now","auroragw-health.timer"], check=False)

        time.sleep(1)
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
