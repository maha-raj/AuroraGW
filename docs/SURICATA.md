# Suricata IDS (v0.2)

If Suricata is installed and enabled in AuroraGW config, AuroraGW will run it in IDS mode
(alert-only) on selected interfaces.

Implementation:
- systemd template unit `auroragw-suricata@.service` enabled per interface.

Logs:
- `/var/log/suricata/`
