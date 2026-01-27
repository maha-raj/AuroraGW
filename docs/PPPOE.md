# PPPoE (v0.2)

AuroraGW uses classic `pppd` with an `/etc/ppp/peers/auroragw-wan` peer file and a systemd unit.

- Peer files + `pon/poff` are standard on Ubuntu.
- Credentials are written to `/etc/ppp/chap-secrets` (permissions 0600).

Bring up/down:
- `systemctl restart auroragw-pppoe.service`
- `pon auroragw-wan` / `poff auroragw-wan`

Logs:
- `journalctl -u auroragw-pppoe.service -e`
