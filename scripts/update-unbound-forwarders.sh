#!/usr/bin/env bash
set -euo pipefail

CFG="/etc/auroragw/config.yaml"
OUT="/etc/unbound/unbound.conf.d/auroragw-forwarders.conf"

[[ -f "$CFG" ]] || exit 0

python3 - <<'PY'
import ipaddress
import subprocess
from pathlib import Path
import yaml

cfg_path = Path("/etc/auroragw/config.yaml")
out_path = Path("/etc/unbound/unbound.conf.d/auroragw-forwarders.conf")

cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
dns = (cfg.get("services") or {}).get("dns") or {}
if not dns.get("enabled", True) or dns.get("provider", "unbound") != "unbound":
    raise SystemExit(0)
up = (dns.get("upstream") or {})
mode = (up.get("mode") or "auto").strip().lower()
if mode != "auto":
    raise SystemExit(0)

def sh(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

servers = []
r = sh(["bash","-lc","resolvectl dns 2>/dev/null | awk '{for(i=2;i<=NF;i++) print $i}'"])
if r.returncode == 0:
    servers += [x.strip() for x in r.stdout.splitlines() if x.strip()]
if not servers:
    try:
        text = Path("/etc/resolv.conf").read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("nameserver "):
                servers.append(line.split()[1].strip())
    except Exception:
        pass

out = []
for s in servers:
    if s in ("127.0.0.53", "127.0.0.1", "::1"):
        continue
    try:
        ipaddress.ip_address(s)
        out.append(s)
    except Exception:
        continue

out = list(dict.fromkeys(out))
if not out:
    # keep existing behavior sane if WAN DNS isn't available yet
    out = ["1.1.1.1", "9.9.9.9"]

content = "forward-zone:\n  name: \".\"\n  forward-tls-upstream: no\n" + "".join([f"  forward-addr: {s}\n" for s in out])

old = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
if old != content:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    subprocess.run(["systemctl","restart","unbound"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY
