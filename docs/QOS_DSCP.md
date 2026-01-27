# QoS + DSCP (v0.2)

## CAKE

- Segment shaping uses `tc qdisc ... cake bandwidth <rate> diffserv4 nat`.
- WAN SQM (optional):
  - Egress shaping on `pppoe0` using CAKE
  - Ingress shaping via IFB:
    - redirect ingress from WAN *physical* NIC to `ifb0`
    - run CAKE on `ifb0`

## PPPoE overhead

CAKE supports:
- `overhead BYTES`
- `mpu BYTES`

See `tc-cake(8)` for exact behavior and valid ranges.

## DSCP

AuroraGW preserves DSCP by default. Optional DSCP rewrite rules can be enabled
to set DSCP by protocol/port (implemented in nftables `inet mangle` prerouting).
