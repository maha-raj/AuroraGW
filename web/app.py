#!/usr/bin/env python3
import base64, hmac, time, subprocess, json, uuid, traceback, logging
import threading
from collections import deque
from pathlib import Path
from typing import List
import ipaddress
import yaml
from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import JSONResponse

CFG_ACTIVE = Path("/etc/auroragw/config.yaml")
CFG_STAGING = Path("/etc/auroragw/config.staging.yaml")
SECRETS = Path("/etc/auroragw/secrets.yaml")
AUDIT = Path("/var/log/auroragw/audit.log")
WEB_ERRORS = Path("/var/log/auroragw/web-errors.log")
MONITOR_CFG = Path("/etc/auroragw/monitoring.yaml")

templates = Jinja2Templates(directory="/opt/auroragw/web/templates")
app = FastAPI(title="AuroraGW")
app.mount("/static", StaticFiles(directory="/opt/auroragw/web/static"), name="static")

REQS = Counter("auroragw_http_requests_total", "HTTP requests", ["path","method","code"])
NET_RX_MBPS = Gauge("auroragw_net_rx_mbps", "Interface RX rate (Mbps)", ["iface"])
NET_TX_MBPS = Gauge("auroragw_net_tx_mbps", "Interface TX rate (Mbps)", ["iface"])
NET_RX_BYTES = Gauge("auroragw_net_rx_bytes_total", "Interface RX bytes (counter)", ["iface"])
NET_TX_BYTES = Gauge("auroragw_net_tx_bytes_total", "Interface TX bytes (counter)", ["iface"])
NET_RX_DROPPED = Gauge("auroragw_net_rx_dropped_total", "Interface RX dropped (counter)", ["iface"])
NET_TX_DROPPED = Gauge("auroragw_net_tx_dropped_total", "Interface TX dropped (counter)", ["iface"])
NET_RX_ERRORS = Gauge("auroragw_net_rx_errors_total", "Interface RX errors (counter)", ["iface"])
NET_TX_ERRORS = Gauge("auroragw_net_tx_errors_total", "Interface TX errors (counter)", ["iface"])
MON_MODE = Gauge("auroragw_monitoring_mode", "Monitoring mode (off=0 basic=1 advanced=2)")
PPPOE_UP = Gauge("auroragw_pppoe_up", "PPPoE link present (1/0)")

_logger = logging.getLogger("auroragw.web")
if not _logger.handlers:
    WEB_ERRORS.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(WEB_ERRORS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)

def sh(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def load_secrets():
    if not SECRETS.exists():
        return {"admin_password": "admin"}
    return yaml.safe_load(SECRETS.read_text(encoding="utf-8")) or {"admin_password":"admin"}

AUTH_FAIL_LIMIT = 5
AUTH_FAIL_WINDOW_S = 10 * 60
AUTH_LOCKOUT_S = 5 * 60
_auth_lock = threading.Lock()
_auth_state = {}  # key -> {fails:int, first_ts:float, locked_until:float}

def _client_ip(request: Request) -> str:
    try:
        return (request.client.host or "").strip() or "unknown"
    except Exception:
        return "unknown"

def _auth_key(ip: str, user: str) -> str:
    return f"{ip}|{user}"

def _auth_is_locked(key: str, now: float) -> float:
    with _auth_lock:
        st = _auth_state.get(key) or {}
        locked_until = float(st.get("locked_until", 0.0) or 0.0)
        if locked_until > now:
            return locked_until
        return 0.0

def _auth_note_success(key: str):
    with _auth_lock:
        _auth_state.pop(key, None)

def _auth_note_failure(key: str, now: float) -> float:
    with _auth_lock:
        st = _auth_state.get(key) or {"fails": 0, "first_ts": now, "locked_until": 0.0}
        if now - float(st.get("first_ts", now) or now) > AUTH_FAIL_WINDOW_S:
            st = {"fails": 0, "first_ts": now, "locked_until": 0.0}
        st["fails"] = int(st.get("fails", 0) or 0) + 1
        if st["fails"] >= AUTH_FAIL_LIMIT:
            st["locked_until"] = now + AUTH_LOCKOUT_S
            st["fails"] = 0
            st["first_ts"] = now
        _auth_state[key] = st
        return float(st.get("locked_until", 0.0) or 0.0)

def ok_auth(request: Request) -> bool:
    auth = request.headers.get("authorization","")
    if not auth.lower().startswith("basic "):
        return False
    raw = base64.b64decode(auth.split()[1]).decode("utf-8", errors="ignore")
    if ":" not in raw:
        return False
    user, pw = raw.split(":",1)
    now = time.time()
    key = _auth_key(_client_ip(request), user)
    if _auth_is_locked(key, now):
        return False
    exp = (load_secrets().get("admin_password","admin"))
    ok = (user == "admin" and hmac.compare_digest(pw, exp))
    if ok:
        _auth_note_success(key)
        return True
    locked_until = _auth_note_failure(key, now)
    if locked_until:
        audit(f"auth lock user={user} ip={_client_ip(request)} until={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(locked_until))}")
    return False

def require_auth(request: Request):
    ip = _client_ip(request)
    auth = request.headers.get("authorization","")
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth.split()[1]).decode("utf-8", errors="ignore")
            user = raw.split(":", 1)[0] if ":" in raw else "admin"
        except Exception:
            user = "admin"
        now = time.time()
        locked_until = _auth_is_locked(_auth_key(ip, user), now)
        if locked_until:
            retry_after = max(1, int(locked_until - now))
            raise HTTPException(
                status_code=401,
                headers={"WWW-Authenticate": "Basic realm=AuroraGW", "Retry-After": str(retry_after)},
                detail=f"Locked out. Retry in ~{retry_after}s.",
            )
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

NAV_ITEMS = [
    {"label": "Status", "path": "/status"},
    {"label": "Config", "path": "/config"},
    {"label": "DNS", "path": "/dns"},
    {"label": "Firewall", "path": "/firewall"},
    {"label": "Suricata", "path": "/suricata"},
    {"label": "EveBox", "path": "/evebox"},
    {"label": "Grafana", "path": "/grafana"},
    {"label": "Traffic", "path": "/traffic"},
    {"label": "Backups", "path": "/backups"},
    {"label": "Logs", "path": "/logs"},
]

def wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept or "*/*" in accept

def render(request: Request, template_name: str, ctx: dict, *, status_code: int = 200):
    merged = dict(ctx)
    merged.update(
        {
            "request": request,
            "nav_items": NAV_ITEMS,
            "active_path": request.url.path,
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err"),
        }
    )
    return templates.TemplateResponse(template_name, merged, status_code=status_code)

def cmd_error_page(request: Request, *, action: str, cmd: list[str], rc: int, output: str):
    error_id = uuid.uuid4().hex[:12]
    request_id = getattr(request.state, "request_id", "")
    _logger.error(
        f"error_id={error_id} request_id={request_id} action={action} rc={rc} cmd={cmd}\n{output}"
    )
    audit(f"{action} rc={rc} error_id={error_id}")
    return render(
        request,
        "error.html",
        {
            "message": f"{action} failed. Retry; if it persists, share the Error ID.",
            "error_id": error_id,
            "request_id": request_id,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        status_code=500,
    )

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
        self._source = "config"
        self._override_mtime = 0.0
        self._override = None

    def _load_override(self) -> dict | None:
        if not MONITOR_CFG.exists():
            self._override_mtime = 0.0
            self._override = None
            return None
        try:
            m = MONITOR_CFG.stat().st_mtime
            if m != self._override_mtime:
                self._override = load_yaml(MONITOR_CFG)
                self._override_mtime = m
            return self._override or {}
        except Exception:
            return None

    def _desired(self) -> tuple[str, float, list[str] | None, str]:
        override = self._load_override()
        if override is not None:
            mon = override
            source = "override"
        else:
            cfg = active_cfg()
            mon = ((cfg.get("services") or {}).get("monitoring") or {})
            source = "config"

        mode = (mon.get("mode") or "off").strip().lower()
        if mode not in ("off", "basic", "advanced"):
            mode = "off"
        interval = 5.0 if mode == "basic" else (1.0 if mode == "advanced" else 2.0)
        ifaces = mon.get("interfaces")
        if ifaces:
            ifaces = [str(x).strip() for x in ifaces if str(x).strip()]
        else:
            ifaces = None
        return mode, interval, ifaces, source

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "interval_s": self._interval,
                "source": self._source,
                "interfaces": sorted(self._rates.keys()),
                "rates": self._rates,
                "history": list(self._history) if self._mode == "advanced" else [],
            }

    def run_forever(self):
        while True:
            mode, interval, ifaces, source = self._desired()
            now = time.time()

            if mode == "off":
                with self._lock:
                    self._mode = "off"
                    self._interval = interval
                    self._source = source
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
                self._source = source

            time.sleep(interval)

TRAFFIC = TrafficMonitor()
threading.Thread(target=TRAFFIC.run_forever, daemon=True).start()

def parse_dns_list(raw: str) -> list[str]:
    parts = []
    for tok in (raw or "").replace(",", " ").split():
        tok = tok.strip()
        if not tok:
            continue
        try:
            ipaddress.ip_address(tok)
            parts.append(tok)
        except Exception:
            continue
    # unique, keep order
    return list(dict.fromkeys(parts))

@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex[:12]
    resp = await call_next(request)
    resp.headers["X-AuroraGW-Request-ID"] = request.state.request_id
    REQS.labels(path=request.url.path, method=request.method, code=str(resp.status_code)).inc()
    return resp

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    error_id = uuid.uuid4().hex[:12]
    request_id = getattr(request.state, "request_id", "")
    _logger.error(f"error_id={error_id} request_id={request_id} path={request.url.path} validation={exc.errors()}")
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "validation_error", "error_id": error_id, "detail": exc.errors()}, status_code=422)
    return render(
        request,
        "error.html",
        {"message": "Invalid input. Check the fields and try again.", "error_id": error_id, "request_id": request_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
        status_code=422,
    )

@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return Response(content="", status_code=401, headers=exc.headers or {})
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "http_error", "status_code": exc.status_code, "detail": exc.detail}, status_code=exc.status_code, headers=exc.headers or {})
    if wants_html(request):
        error_id = uuid.uuid4().hex[:12]
        request_id = getattr(request.state, "request_id", "")
        _logger.info(f"error_id={error_id} request_id={request_id} path={request.url.path} http={exc.status_code} detail={exc.detail}")
        return render(
            request,
            "error.html",
            {"message": str(exc.detail), "error_id": error_id, "request_id": request_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
            status_code=exc.status_code,
        )
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=exc.headers or {})

@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    error_id = uuid.uuid4().hex[:12]
    request_id = getattr(request.state, "request_id", "")
    _logger.error(f"error_id={error_id} request_id={request_id} path={request.url.path}\n{traceback.format_exc()}")
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "internal_error", "error_id": error_id}, status_code=500)
    return render(
        request,
        "error.html",
        {"message": "Internal error. Please retry; if it persists, share the Error ID.", "error_id": error_id, "request_id": request_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
        status_code=500,
    )

@app.get("/metrics")
def metrics():
    snap = TRAFFIC.snapshot()
    mode = (snap.get("mode") or "off").strip().lower()
    MON_MODE.set(2 if mode == "advanced" else (1 if mode == "basic" else 0))
    PPPOE_UP.set(1 if (Path("/sys/class/net/pppoe0").exists()) else 0)
    rates = snap.get("rates") or {}
    for iface, v in rates.items():
        try:
            NET_RX_MBPS.labels(iface=iface).set(float(v.get("rx_mbps") or 0.0))
            NET_TX_MBPS.labels(iface=iface).set(float(v.get("tx_mbps") or 0.0))
            NET_RX_BYTES.labels(iface=iface).set(float(v.get("rx_bytes") or 0.0))
            NET_TX_BYTES.labels(iface=iface).set(float(v.get("tx_bytes") or 0.0))
            NET_RX_DROPPED.labels(iface=iface).set(float(v.get("rx_dropped") or 0.0))
            NET_TX_DROPPED.labels(iface=iface).set(float(v.get("tx_dropped") or 0.0))
            NET_RX_ERRORS.labels(iface=iface).set(float(v.get("rx_errors") or 0.0))
            NET_TX_ERRORS.labels(iface=iface).set(float(v.get("tx_errors") or 0.0))
        except Exception:
            continue
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    require_auth(request)
    return render(request, "index.html", {})

@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    require_auth(request)
    ip_addr = sh(["bash","-lc","ip -br addr"]).stdout
    routes = sh(["bash","-lc","ip route"]).stdout
    nft = sh(["bash","-lc","nft list ruleset | head -n 140"]).stdout
    ppp = sh(["bash","-lc","ip link show pppoe0 2>/dev/null || true"]).stdout
    pending = None
    try:
        p = Path("/run/auroragw/pending.json")
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            timeout = int(data.get("timeout", 0) or 0)
            ts = float(data.get("ts", 0) or 0)
            seconds_left = None
            if timeout and ts:
                seconds_left = max(0, int(timeout - (time.time() - ts)))
            pending = {"timeout": timeout, "ts": ts, "seconds_left": seconds_left}
    except Exception:
        pending = {"timeout": None, "ts": None, "seconds_left": None}
    return render(request, "status.html", {"ip_addr": ip_addr, "routes": routes, "nft": nft, "ppp": ppp, "pending": pending})

@app.get("/config", response_class=HTMLResponse)
def config_get(request: Request):
    require_auth(request)
    active = CFG_ACTIVE.read_text(encoding="utf-8") if CFG_ACTIVE.exists() else ""
    staging = CFG_STAGING.read_text(encoding="utf-8") if CFG_STAGING.exists() else ""
    return render(request, "config.html", {"active": active, "staging": staging})

@app.post("/config")
def config_post(request: Request, staging: str = Form(...)):
    require_auth(request)
    CFG_STAGING.parent.mkdir(parents=True, exist_ok=True)
    CFG_STAGING.write_text(staging, encoding="utf-8")
    audit("updated staging config")
    return RedirectResponse(url="/config?msg=Saved+staging+config", status_code=303)

@app.post("/apply")
def apply(request: Request, require_confirm: str | None = Form(None), timeout: int = Form(120)):
    require_auth(request)
    want_confirm = bool(require_confirm)
    args = ["auroragd","apply","--require-confirm","--timeout",str(timeout),"--commit","/etc/auroragw/config.staging.yaml"] if want_confirm else ["auroragd","apply","--commit","/etc/auroragw/config.staging.yaml"]
    r = sh(args)
    if r.returncode != 0:
        return cmd_error_page(request, action="Apply", cmd=args, rc=r.returncode, output=r.stdout)
    audit(f"apply rc={r.returncode}")
    return RedirectResponse(url="/status?msg=Apply+started", status_code=303)

@app.post("/confirm")
def confirm(request: Request):
    require_auth(request)
    r = sh(["auroragd","confirm"])
    if r.returncode != 0:
        return cmd_error_page(request, action="Confirm", cmd=["auroragd","confirm"], rc=r.returncode, output=r.stdout)
    audit(f"confirm rc={r.returncode}")
    return RedirectResponse(url="/status?msg=Confirmed", status_code=303)

@app.get("/backups", response_class=HTMLResponse)
def backups(request: Request):
    require_auth(request)
    out = sh(["bash","-lc","ls -1 /var/lib/auroragw/backups 2>/dev/null || true"]).stdout
    return render(request, "backups.html", {"backups": out})

@app.get("/firewall", response_class=HTMLResponse)
def firewall_get(request: Request):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    pf = (((cfg.get("firewall") or {}).get("port_forwards")) or [])
    return render(request, "firewall.html", {"port_forwards": pf})

@app.get("/traffic", response_class=HTMLResponse)
def traffic_page(request: Request):
    require_auth(request)
    snap = TRAFFIC.snapshot()
    override_enabled = MONITOR_CFG.exists()
    if override_enabled:
        settings = load_yaml(MONITOR_CFG)
    else:
        cfg = active_cfg()
        settings = ((cfg.get("services") or {}).get("monitoring") or {})
    mode = (settings.get("mode") or snap.get("mode") or "off").strip().lower()
    selected = [str(x) for x in (settings.get("interfaces") or [])]
    return render(
        request,
        "traffic.html",
        {"traffic": snap, "ifaces": list_ifaces(), "mode": mode, "selected_ifaces": selected, "override_enabled": override_enabled},
    )

@app.get("/api/traffic")
def api_traffic(request: Request):
    require_auth(request)
    return TRAFFIC.snapshot()

@app.post("/traffic/settings")
def traffic_settings(
    request: Request,
    mode: str = Form("off"),
    interfaces: List[str] = Form([]),
):
    require_auth(request)
    mode = (mode or "off").strip().lower()
    if mode not in ("off", "basic", "advanced"):
        mode = "off"
    ifaces = [str(x).strip() for x in interfaces if str(x).strip()]
    data = {"mode": mode}
    if ifaces:
        data["interfaces"] = ifaces
    MONITOR_CFG.parent.mkdir(parents=True, exist_ok=True)
    MONITOR_CFG.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    audit(f"monitoring override saved mode={mode} interfaces={ifaces or 'ALL'}")
    return RedirectResponse(url="/traffic?msg=Saved+monitoring+settings", status_code=303)

@app.post("/traffic/reset")
def traffic_reset(request: Request):
    require_auth(request)
    try:
        if MONITOR_CFG.exists():
            MONITOR_CFG.unlink()
    except Exception as e:
        return cmd_error_page(request, action="Reset monitoring settings", cmd=["unlink", str(MONITOR_CFG)], rc=1, output=str(e))
    audit("monitoring override reset")
    return RedirectResponse(url="/traffic?msg=Reset+to+config+defaults", status_code=303)

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
    try:
        ip_obj = ipaddress.ip_address((lan_ip or "").strip())
    except Exception:
        raise HTTPException(status_code=400, detail="LAN IP must be a valid IP address")

    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    cfg.setdefault("firewall", {})
    cfg["firewall"].setdefault("port_forwards", [])

    # Prevent duplicate WAN port mappings (proto+port).
    for r in cfg["firewall"]["port_forwards"]:
        if (r.get("proto") or "").lower() == proto and int(r.get("wan_port") or 0) == int(wan_port):
            raise HTTPException(status_code=400, detail=f"duplicate rule: {proto} WAN port {wan_port} already exists")

    # Basic safety: ensure lan_ip is inside one of the defined segment subnets.
    seg_ok = False
    for seg in (cfg.get("segments") or []):
        addr = (seg.get("address") or "").strip()
        if not addr:
            continue
        try:
            net = ipaddress.ip_interface(addr).network
            if ip_obj in net:
                seg_ok = True
                break
        except Exception:
            continue
    if not seg_ok:
        raise HTTPException(status_code=400, detail="LAN IP must be inside a configured segment subnet")

    cfg["firewall"]["port_forwards"].append(
        {"proto": proto, "wan_port": int(wan_port), "lan_ip": lan_ip.strip(), "lan_port": int(lan_port)}
    )
    save_yaml(CFG_STAGING, cfg)
    audit(f"firewall add port_forward {proto}:{wan_port}->{lan_ip}:{lan_port}")
    return RedirectResponse(url="/firewall?msg=Port+forward+added", status_code=303)

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
    return RedirectResponse(url="/firewall?msg=Port+forward+deleted", status_code=303)

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

    return render(request, "suricata.html", {"enabled": enabled, "interfaces": interfaces, "iface_tokens": iface_tokens, "status": status, "logs": logs})

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
    return RedirectResponse(url="/suricata?msg=Saved", status_code=303)

@app.get("/evebox", response_class=HTMLResponse)
def evebox_get(request: Request):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    e = ((cfg.get("services") or {}).get("evebox") or {})
    enabled = bool(e.get("enabled", False))

    status = sh(["bash", "-lc", "systemctl --no-pager --plain status auroragw-evebox.service 2>/dev/null || true"]).stdout
    eve_tail = sh(["bash", "-lc", "tail -n 120 /var/log/suricata/eve.json 2>/dev/null || true"]).stdout

    # Best-effort: link back to this router's host (from Host header).
    host = (request.headers.get("host", "192.168.101.1").split(":", 1)[0]).strip() or "192.168.101.1"
    return render(request, "evebox.html", {"enabled": enabled, "status": status, "eve_tail": eve_tail, "host": host})

@app.post("/evebox")
def evebox_post(request: Request, enabled: str = Form("0")):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    cfg.setdefault("services", {})
    cfg["services"].setdefault("evebox", {})
    cfg["services"]["evebox"]["enabled"] = (enabled == "1")
    save_yaml(CFG_STAGING, cfg)
    audit(f"evebox updated enabled={cfg['services']['evebox']['enabled']}")
    return RedirectResponse(url="/evebox?msg=Saved", status_code=303)

@app.get("/grafana", response_class=HTMLResponse)
def grafana_get(request: Request):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    g = ((cfg.get("services") or {}).get("grafana") or {})
    enabled = bool(g.get("enabled", False))
    mode = (g.get("mode") or "remote").strip().lower()
    if mode not in ("local", "remote"):
        mode = "remote"
    remote_url = (g.get("remote_url") or "").strip()

    status = sh(["bash", "-lc", "systemctl --no-pager --plain status auroragw-observability.service 2>/dev/null || true"]).stdout
    ps = sh(["bash", "-lc", "cd /opt/auroragw/observability/local 2>/dev/null && docker compose ps 2>/dev/null || true"]).stdout

    host = (request.headers.get("host", "192.168.101.1").split(":", 1)[0]).strip() or "192.168.101.1"
    return render(
        request,
        "grafana.html",
        {"enabled": enabled, "mode": mode, "remote_url": remote_url, "status": status, "ps": ps, "host": host},
    )

@app.post("/grafana")
def grafana_post(
    request: Request,
    enabled: str = Form("0"),
    mode: str = Form("remote"),
    remote_url: str = Form(""),
):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    cfg.setdefault("services", {})
    cfg["services"].setdefault("grafana", {})
    mode = (mode or "remote").strip().lower()
    if mode not in ("local", "remote"):
        mode = "remote"
    cfg["services"]["grafana"]["enabled"] = (enabled == "1")
    cfg["services"]["grafana"]["mode"] = mode
    cfg["services"]["grafana"]["remote_url"] = (remote_url or "").strip()
    save_yaml(CFG_STAGING, cfg)
    audit(f"grafana updated enabled={cfg['services']['grafana']['enabled']} mode={mode}")
    return RedirectResponse(url="/grafana?msg=Saved", status_code=303)

@app.get("/dns", response_class=HTMLResponse)
def dns_get(request: Request):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)

    dns = ((cfg.get("services") or {}).get("dns") or {})
    upstream = (dns.get("upstream") or {})
    up_mode = (upstream.get("mode") or "auto").strip().lower()
    up_servers = upstream.get("servers") or []
    up_servers_text = ", ".join([str(x) for x in up_servers])

    seg_rows = []
    for seg in (cfg.get("segments") or []):
        dh = (seg.get("dhcp") or {})
        dh_enabled = bool(dh.get("enabled", False))
        dns_cfg = (dh.get("dns") or {})
        seg_rows.append(
            {
                "id": seg.get("id", ""),
                "label": seg.get("label", ""),
                "address": seg.get("address", ""),
                "dhcp_enabled": dh_enabled,
                "dns_mode": (dns_cfg.get("mode") or "router").strip().lower(),
                "dns_servers": ", ".join([str(x) for x in (dns_cfg.get("servers") or [])]),
            }
        )

    return render(request, "dns.html", {"up_mode": up_mode, "up_servers": up_servers_text, "segments": seg_rows})

@app.post("/dns")
def dns_post(
    request: Request,
    upstream_mode: str = Form("auto"),
    upstream_servers: str = Form(""),
    seg_id: List[str] = Form([]),
    seg_dns_mode: List[str] = Form([]),
    seg_dns_servers: List[str] = Form([]),
):
    require_auth(request)
    cfg = load_yaml(CFG_STAGING if CFG_STAGING.exists() else CFG_ACTIVE)
    cfg.setdefault("services", {})
    cfg["services"].setdefault("dns", {})
    cfg["services"]["dns"].setdefault("upstream", {})

    upstream_mode = (upstream_mode or "auto").strip().lower()
    if upstream_mode not in ("auto", "manual"):
        upstream_mode = "auto"
    cfg["services"]["dns"]["upstream"]["mode"] = upstream_mode
    cfg["services"]["dns"]["upstream"]["servers"] = parse_dns_list(upstream_servers) if upstream_mode == "manual" else []

    by_id = {str(s.get("id")): s for s in (cfg.get("segments") or [])}
    for i, sid in enumerate(seg_id):
        seg = by_id.get(str(sid))
        if not seg:
            continue
        seg.setdefault("dhcp", {})
        seg["dhcp"].setdefault("dns", {})
        mode = (seg_dns_mode[i] if i < len(seg_dns_mode) else "router").strip().lower()
        if mode not in ("router", "inherit_wan", "manual"):
            mode = "router"
        seg["dhcp"]["dns"]["mode"] = mode
        seg["dhcp"]["dns"]["servers"] = parse_dns_list(seg_dns_servers[i]) if (mode == "manual" and i < len(seg_dns_servers)) else []

    save_yaml(CFG_STAGING, cfg)
    audit("dns settings updated")
    return RedirectResponse(url="/dns?msg=Saved", status_code=303)

@app.post("/backup")
def backup(request: Request):
    require_auth(request)
    r = sh(["auroragd","backup"])
    if r.returncode != 0:
        return cmd_error_page(request, action="Backup", cmd=["auroragd","backup"], rc=r.returncode, output=r.stdout)
    audit(f"backup rc={r.returncode}")
    return RedirectResponse(url="/backups?msg=Backup+created", status_code=303)

@app.get("/logs", response_class=HTMLResponse)
def logs(request: Request):
    require_auth(request)
    audit_log = AUDIT.read_text(encoding="utf-8")[-8000:] if AUDIT.exists() else ""
    web_errors = WEB_ERRORS.read_text(encoding="utf-8")[-8000:] if WEB_ERRORS.exists() else ""
    journal = sh(["bash","-lc","journalctl -u auroragw-web -u auroragw-health -u auroragw-pppoe -n 200 --no-pager 2>/dev/null || true"]).stdout
    return render(request, "logs.html", {"audit": audit_log, "web_errors": web_errors, "journal": journal})

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
