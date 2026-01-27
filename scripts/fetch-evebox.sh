#!/usr/bin/env bash
set -euo pipefail

# Fetch EveBox binary for the current architecture.
# Source: https://evebox.org/docs/

DEST_DIR="/opt/auroragw/bin"
DEST="${DEST_DIR}/evebox"

VERSION="${EVEBOX_VERSION:-0.23.0}"

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) pkg="evebox-${VERSION}-linux-x64.zip" ;;
  aarch64|arm64) pkg="evebox-${VERSION}-linux-arm64.zip" ;;
  *) echo "Unsupported arch: $arch"; exit 1 ;;
esac

url="https://evebox.org/files/release/latest/${pkg}"

mkdir -p "$DEST_DIR"
if [[ -x "$DEST" ]]; then
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

command -v curl >/dev/null 2>&1 || { echo "curl not found"; exit 1; }
command -v unzip >/dev/null 2>&1 || { echo "unzip not found"; exit 1; }

echo "Downloading EveBox ${VERSION} (${arch})..."
curl -fsSL "$url" -o "$tmp/evebox.zip"
unzip -q "$tmp/evebox.zip" -d "$tmp/out"

if [[ ! -f "$tmp/out/evebox" ]]; then
  echo "evebox binary not found in zip"
  exit 1
fi

install -m 0755 "$tmp/out/evebox" "$DEST"
echo "Installed: $DEST"
