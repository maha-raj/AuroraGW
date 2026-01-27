#!/usr/bin/env bash
set -euo pipefail
file="${1:-}"
[[ -n "$file" ]] || { echo "Usage: restore.sh <backup.tgz>"; exit 1; }
sudo tar xzf "$file" -C / 2>/dev/null || true
sudo auroragd validate /etc/auroragw/config.yaml
sudo auroragd apply --commit /etc/auroragw/config.yaml
echo "Restore complete."
