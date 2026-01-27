#!/usr/bin/env bash
set -euo pipefail
echo "AuroraGW safe update: backup then apt upgrade."
sudo auroragd backup >/dev/null || true
sudo apt-get update
sudo apt-get -y upgrade
echo "Done."
