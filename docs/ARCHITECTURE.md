# AuroraGW architecture (summary)

AuroraGW is a three-plane gateway appliance:

- **Data plane**: Linux routing/NAT + `nftables` + `tc` (CAKE/IFB) for QoS.
- **Control plane**: `auroragd` validates config, renders system configs, applies safely, and supports rollback.
- **Management plane**: LAN-only Web UI/API (TLS) for status + config + apply/confirm + backups + logs.

Authoritative docs:
- Product spec: `docs/AuroraGW-Master-Plan.md`
- Implementation plan: `docs/PROJECT_PLAN.md`
