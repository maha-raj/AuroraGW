# Grafana / Prometheus (Observability)

AuroraGW exposes a Prometheus endpoint at:
- `https://<auroragw-lan-ip>:8443/metrics`

The Grafana module supports two modes:
- **local**: run Prometheus + Grafana on the AuroraGW box (LAN-only firewall access)
- **remote**: run Prometheus + Grafana on a LAN VM/NAS and just link to it from AuroraGW

## 1) Remote mode (recommended)

This keeps the router fast and moves dashboards to a separate host.

On your LAN VM/NAS:
1) Copy `observability/vm/` somewhere (or `git clone` AuroraGW).
2) Edit `prometheus.yml` and set the target to your AuroraGW LAN IP:
   - `192.168.101.1:8443` (example)
3) Start the stack:
```bash
cd observability/vm
export GRAFANA_ADMIN_PASSWORD='change-me'
docker compose up -d
```

Then open:
- Grafana: `http://<vm-ip>:3000/`
- Prometheus: `http://<vm-ip>:9090/`

In AuroraGW Web UI:
- `/grafana` → enable → `mode=remote` → set `remote_url` to your Grafana URL.

## 2) Local mode (on AuroraGW)

Local mode runs Docker containers on the router:
- Prometheus: `http://<auroragw-lan-ip>:9095/` (uses `9095` to avoid conflicting with Cockpit’s `9090`)
- Grafana: `http://<auroragw-lan-ip>:3000/`

Requirements:
- Docker + Docker Compose plugin (`docker compose`).

Enable:
1) In AuroraGW Web UI: `/grafana` → enable → `mode=local`
2) Apply + Confirm from `/status`

Grafana password:
- Stored in `/etc/auroragw/secrets.yaml` under `grafana_admin_password`

## 3) Useful metrics

AuroraGW exports:
- `auroragw_http_requests_total{path,method,code}`
- `auroragw_net_rx_mbps{iface}`, `auroragw_net_tx_mbps{iface}`
- `auroragw_net_rx_bytes_total{iface}`, `auroragw_net_tx_bytes_total{iface}`
- `auroragw_net_rx_dropped_total{iface}`, `auroragw_net_tx_dropped_total{iface}`
- `auroragw_net_rx_errors_total{iface}`, `auroragw_net_tx_errors_total{iface}`
- `auroragw_monitoring_mode` (off=0 basic=1 advanced=2)
- `auroragw_pppoe_up` (1/0)

Tip: ensure traffic monitoring is not `off` (see `/traffic`), or interface rate gauges will be empty.
