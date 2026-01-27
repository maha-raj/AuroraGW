# Security defaults
- WAN inbound denied by default (nftables input policy drop)
- Mgmt allowed from LAN only (SSH 22, Web UI 8443; optional Cockpit 9090 / status API 8080 if enabled)
- Segment isolation: target default is OPT1 → LAN blocked unless explicitly allowed by presets/modules (starter builds may be more permissive for discovery during early development)
