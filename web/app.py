#!/usr/bin/env python3
import base64, hmac, time, subprocess
import threading
from collections import deque
from pathlib import Path
from typing import List
import yaml
from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

CFG_ACTIVE = Path("/etc/auroragw/config.yaml")
CFG_STAGING = Path("/etc/auroragw/config.staging.yaml")
SECRETS = Path("/etc/auroragw/secrets.yaml")
AUDIT = Path("/var/log/auroragw/audit.log")

templates = Jinja2Templates(directory="/opt/auroragw/web/templates")
app = FastAPI(title="AuroraGW")

REQS = Counter("auroragw_http_requests_total", "HTTP requests", ["path","method","code"])

def sh(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def load_secrets():
    if not SECRETS.exists():
        return {"admin_password": "admin"}
    return yaml.safe_load(SECRETS.read_text(encoding="utf-8")) or {"admin_password":"admin"}

def ok_auth(request: Request) -> bool:
    auth = request.headers.get("authorization","")
    if not auth.lower().startswith("basic "):
        return False
    raw = base64.b64decode(auth.split()[1]).decode("utf-8", errors="ignore")
    if ":" not in raw:
        return False
    user, pw = raw.split(":",1)
    exp = (load_secrets().get("admin_password","admin"))
    return user == "admin" and hmac.compare_digest(pw, exp)

def require_auth(request: Request):
    if not ok_auth(request):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate":"Basic realm=AuroraGW"})

def audit(msg: str):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] ui {msg}\n")

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def save_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

def active_cfg() -> dict:
    if CFG_ACTIVE.exists():
        return load_yaml(CFG_ACTIVE)
    if CFG_STAGING.exists():
        return load_yaml(CFG_STAGING)
    return {}

def list_ifaces() -> list[str]:
    base = Path("/sys/class/net")
    if not base.exists():
        return []
    out = []
    for p in base.iterdir():
        name = p.name
        if name == "lo":
            continue
        out.append(name)
    return sorted(out)

def read_iface_stats(iface: str) -> dict:
    base = Path("/sys/class/net") / iface / "statistics"
    def r(name: str) -> int:
        try:
            return int((base / name).read_text(encoding="utf-8").strip())
        except Exception:
            return 0
    return {
        "rx_bytes": r("rx_bytes"),
        "tx_bytes": r("tx_bytes"),
        "rx_packets": r("rx_packets"),
        "tx_packets": r("tx_packets"),
        "rx_errors": r("rx_errors"),
        "tx_errors": r("tx_errors"),
        "rx_dropped": r("rx_dropped"),
        "tx_dropped": r("tx_dropped"),
    }

class TrafficMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._mode = "off"
        self._interval = 5.0
        self._last = {}  # iface -> (ts, rx_bytes, tx_bytes)
        self._rates = {}  # iface -> dict
        self._history = deque(maxlen=300)  # advanced mode: last ~5min at 1s

    def _desired(self) -> tuple[str, float, list[str] | None]:
        cfg = active_cfg()
        mon = ((cfg.get("services") or {}).get("monitoring") or {})
        mode = (mon.get("mode") or "off").strip().lower()
        if mode not in ("off", "basic", "advanced"):
            mode = "off"
        interval = 5.0 if mode == "basic" else (1.0 if mode == "advanced" else 2.0)
        ifaces = mon.get("interfaces")
        if ifaces:
            ifaces = [str(x).strip() for x in ifaces if str(x).strip()]
        else:
            ifaces = None
        return mode, interval, ifaces

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "interval_s": self._interval,
                "interfaces": sorted(self._rates.keys()),
                "rates": self._rates,
                "history": list(self._history) if self._mode == "advanced" else [],
            }

    def run_forever(self):
        while True:
            mode, interval, ifaces = self._desired()
            now = time.time()

            if mode == "off":
                with self._lock:
                    self._mode = "off"
                    self._interval = interval
                    self._last = {}
                    self._rates = {}
                    self._history.clear()
                time.sleep(interval)
                continue

            if iface_list := (ifaces or list_ifaces()):
                for iface in iface_list:
                    stats = read_iface_stats(iface)
                    with self._lock:
                        prev = self._last.get(iface)
                        rx_mbps = tx_mbps = 0.0
                        if prev:
                            prev_ts, prev_rx, prev_tx = prev
                            dt = max(now - prev_ts, 0.001)
                            rx_mbps = (stats["rx_bytes"] - prev_rx) * 8.0 / dt / 1_000_000.0
                            tx_mbps = (stats["tx_bytes"] - prev_tx) * 8.0 / dt / 1_000_000.0
                        self._last[iface] = (now, stats["rx_bytes"], stats["tx_bytes"])
                        self._rates[iface] = {
                            **stats,
                            "rx_mbps": round(rx_mbps, 3),
                            "tx_mbps": round(tx_mbps, 3),
                            "ts": int(now),
                        }
                        if mode == "advanced":
                            self._history.append({"ts": int(now), "iface": iface, "rx_mbps": rx_mbps, "tx_mbps": tx_mbps})

            with self._lock:
                self._mode = mode
                self._interval = interval

            time.sleep(interval)

TRAFFIC = TrafficMonitor()
threading.Thread(target=TRAFFIC.run_forever, daemon=True).start()

@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    resp = await call_next(request)
    REQS.labels(path=request.url.path, method=request.method, code=str(resp.status_code)).inc()
    return resp

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    require_auth(request)
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    require_auth(request)
    ip_addr = sh(["bash","-lc","ip -br addr"]).stdout
    routes = sh(["bash","-lc","ip route"]).stdout
    nft = sh(["bash","-lc","nft list ruleset | head -n 140"]).stdout
    ppp = sh(["bash","-lc","ip link show pppoe0 2>/dev/null || true"]).stdout
    return templates.TemplateResponse("status.html", {"request":request, "ip_addr":ip_addr, "routes":routes, "nft":nft, "ppp":ppp})

@app.get("/config", response_class=HTMLResponse)
def config_get(request: Request):
    require_auth(request)
    active = CFG_ACTIVE.read_text(encoding="utf-8") if CFG_ACTIVE.exists() else ""
    staging = CFG_STAGING.read_text(encoding="utf-8") if CFG_STAGING.exists() else ""
    return templates.TemplateResponse("config.html", {"request":request, "active":active, "staging":staging})

@app.post("/config")
def config_post(request: Request, staging: str = Form(...)):
    require_auth(request)
    CFG_STAGING.parent.mkdir(parents=True, exist_ok=True)
    CFG_STAGING.write_text(staging, encoding="utf-8")
    audit("updated staging config")
    return RedirectResponse(url="/config", status_code=303)

@app.post("/apply")
def apply(request: Request, require_confirm: str = Form("1"), timeout: int = Form(120)):
    require_auth(request)
    args = ["auroragd","apply","--require-confirm","--timeout",str(timeout),"--commit","/etc/auroragw/config.staging.yaml"] if require_confirm else            ["auroragd","apply","--commit","/etc/auroragw/config.staging.yaml"]
    r = sh(args)
    audit(f"apply rc={r.returncode}")
    if r.returncode != 0:
        return PlainTextResponse(r.stdout, status_code=500)
    return RedirectResponse(url="/status", status_code=303)

@app.post("/confirm")
def confirm(request: Request):
    require_auth(request)
    r = sh(["auroragd","confirm"])
    audit(f"confirm rc={r.returncode}")
    if r.returncode != 0:
        return PlainTextResponse(r.stdout, status_code=500)
    return RedirectResponse(url="/status", status_code=303)

@app.get("/backups", response_class=HTMLResponse)
def backups(request: Request):
    require_auth(request)
    out = sh(["bash","-lc","ls -1 /var/lib/auroragw/backups 2>/dev/null || true"]).stdout
    return templates.TemplateResponse("backups.html", {"request":request, "backups":out})

@app.get("/firewall", response_class=HTMLResponse)
def firewall_get(request: Request):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    pf = (((cfg.get("firewall") or {}).get("port_forwards")) or [])
    return templates.TemplateResponse("firewall.html", {"request": request, "port_forwards": pf})

@app.get("/traffic", response_class=HTMLResponse)
def traffic_page(request: Request):
    require_auth(request)
    snap = TRAFFIC.snapshot()
    return templates.TemplateResponse("traffic.html", {"request": request, "traffic": snap})

@app.get("/api/traffic")
def api_traffic(request: Request):
    require_auth(request)
    return TRAFFIC.snapshot()

@app.post("/firewall/add")
def firewall_add(
    request: Request,
    proto: str = Form(...),
    wan_port: int = Form(...),
    lan_ip: str = Form(...),
    lan_port: int = Form(...),
):
    require_auth(request)
    proto = (proto or "").strip().lower()
    if proto not in ("tcp", "udp"):
        raise HTTPException(status_code=400, detail="proto must be tcp or udp")
    if wan_port < 1 or wan_port > 65535 or lan_port < 1 or lan_port > 65535:
        raise HTTPException(status_code=400, detail="invalid port")
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    cfg.setdefault("firewall", {})
    cfg["firewall"].setdefault("port_forwards", [])
    cfg["firewall"]["port_forwards"].append(
        {"proto": proto, "wan_port": int(wan_port), "lan_ip": lan_ip.strip(), "lan_port": int(lan_port)}
    )
    save_yaml(CFG_STAGING, cfg)
    audit(f"firewall add port_forward {proto}:{wan_port}->{lan_ip}:{lan_port}")
    return RedirectResponse(url="/firewall", status_code=303)

@app.post("/firewall/delete")
def firewall_delete(request: Request, idx: int = Form(...)):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    cfg.setdefault("firewall", {})
    cfg["firewall"].setdefault("port_forwards", [])
    if idx < 0 or idx >= len(cfg["firewall"]["port_forwards"]):
        raise HTTPException(status_code=400, detail="invalid index")
    removed = cfg["firewall"]["port_forwards"].pop(idx)
    save_yaml(CFG_STAGING, cfg)
    audit(f"firewall delete port_forward {removed}")
    return RedirectResponse(url="/firewall", status_code=303)

@app.get("/suricata", response_class=HTMLResponse)
def suricata_get(request: Request):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    s = ((cfg.get("services") or {}).get("suricata") or {})
    enabled = bool(s.get("enabled", False))
    interfaces = [str(x) for x in (s.get("interfaces") or [])]

    # Keep config portable: use role tokens (resolved to ifnames during apply).
    iface_tokens = ["lan", "opt1", "wan", "pppoe0"]

    status = sh(["bash", "-lc", "systemctl --no-pager --plain status 'auroragw-suricata@*.service' 2>/dev/null || true"]).stdout
    logs = sh(["bash", "-lc", "journalctl -u 'auroragw-suricata@*.service' -n 200 --no-pager 2>/dev/null || true"]).stdout
    fast = sh(["bash", "-lc", "tail -n 120 /var/log/suricata/fast.log 2>/dev/null || true"]).stdout
    if fast.strip():
        logs = logs + "\n\n== /var/log/suricata/fast.log (tail) ==\n" + fast

    return templates.TemplateResponse(
        "suricata.html",
        {
            "request": request,
            "enabled": enabled,
            "interfaces": interfaces,
            "iface_tokens": iface_tokens,
            "status": status,
            "logs": logs,
        },
    )

@app.post("/suricata")
def suricata_post(
    request: Request,
    enabled: str = Form("0"),
    interfaces: List[str] = Form([]),
):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    cfg.setdefault("services", {})
    cfg["services"].setdefault("suricata", {})

    cfg["services"]["suricata"]["installed"] = True
    cfg["services"]["suricata"]["enabled"] = (enabled == "1")
    cfg["services"]["suricata"]["interfaces"] = [str(x).strip() for x in interfaces if str(x).strip()]

    save_yaml(CFG_STAGING, cfg)
    audit(f"suricata updated enabled={cfg['services']['suricata']['enabled']} interfaces={cfg['services']['suricata']['interfaces']}")
    return RedirectResponse(url="/suricata", status_code=303)

@app.post("/backup")
def backup(request: Request):
    require_auth(request)
    r = sh(["auroragd","backup"])
    audit(f"backup rc={r.returncode}")
    if r.returncode != 0:
        return PlainTextResponse(r.stdout, status_code=500)
    return RedirectResponse(url="/backups", status_code=303)

@app.get("/logs", response_class=HTMLResponse)
def logs(request: Request):
    require_auth(request)
    audit_log = AUDIT.read_text(encoding="utf-8")[-8000:] if AUDIT.exists() else ""
    journal = sh(["bash","-lc","journalctl -u auroragw-web -u auroragw-health -u auroragw-pppoe -n 200 --no-pager 2>/dev/null || true"]).stdout
    return templates.TemplateResponse("logs.html", {"request":request, "audit":audit_log, "journal":journal})

@app.get("/api/status")
def api_status(request: Request):
    require_auth(request)
    return {
        "ip": sh(["bash","-lc","ip -br addr"]).stdout,
        "routes": sh(["bash","-lc","ip route"]).stdout,
        "pppoe": sh(["bash","-lc","ip -br link show pppoe0 2>/dev/null || true"]).stdout,
        "uptime": sh(["bash","-lc","uptime -p"]).stdout.strip(),
        "traffic": TRAFFIC.snapshot(),
    }
