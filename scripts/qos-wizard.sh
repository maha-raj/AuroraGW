#!/usr/bin/env bash
set -euo pipefail
echo "AuroraGW QoS wizard"
CFG="/etc/auroragw/config.staging.yaml"
[[ -f "$CFG" ]] || { echo "Missing $CFG"; exit 1; }
command -v speedtest-cli >/dev/null 2>&1 || { echo "speedtest-cli not found (optional). Install then re-run."; exit 1; }
echo "Running speedtest-cli (3 runs)..."
DL=0; UL=0
for i in 1 2 3; do
  OUT="$(speedtest-cli --simple || true)"
  D="$(echo "$OUT" | awk '/Download:/{print $2}')"
  U="$(echo "$OUT" | awk '/Upload:/{print $2}')"
  DL=$(python3 - <<PY
dl=float("$DL"); d=float("$D") if "$D" else 0.0; print(dl+d)
PY
)
  UL=$(python3 - <<PY
ul=float("$UL"); u=float("$U") if "$U" else 0.0; print(ul+u)
PY
)
done
DL_AVG=$(python3 - <<PY
print(float("$DL")/3.0)
PY
)
UL_AVG=$(python3 - <<PY
print(float("$UL")/3.0)
PY
)
DL_KBIT=$(python3 - <<PY
print(int(float("$DL_AVG")*1000*0.95))
PY
)
UL_KBIT=$(python3 - <<PY
print(int(float("$UL_AVG")*1000*0.95))
PY
)
echo "Recommended (95%): down=${DL_KBIT} kbit, up=${UL_KBIT} kbit"
sudo sed -i "s/egress_kbit: .*/egress_kbit: ${UL_KBIT}/" "$CFG"
sudo sed -i "s/ingress_kbit: .*/ingress_kbit: ${DL_KBIT}/" "$CFG"
echo "Updated $CFG. Apply with: sudo auroragd apply --require-confirm --timeout 120 --commit $CFG"
