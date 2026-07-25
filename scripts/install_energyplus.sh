#!/usr/bin/env bash
# Installs the pinned EnergyPlus build to ~/opt/EnergyPlus-<version>. No sudo required.
set -euo pipefail

VERSION="26.1.0"
BUILD="6f2e40d102"
TARGET="$HOME/opt/EnergyPlus-${VERSION//./-}"

if [ -f "$TARGET/pyenergyplus/api.py" ]; then
  echo "EnergyPlus $VERSION already installed at $TARGET"
  exit 0
fi

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  ASSET="Darwin-macOS13-arm64" ;;
  Darwin-x86_64) ASSET="Darwin-macOS12.1-x86_64" ;;
  Linux-x86_64)  ASSET="Linux-Ubuntu24.04-x86_64" ;;
  Linux-aarch64) ASSET="Linux-Ubuntu24.04-arm64" ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

STEM="EnergyPlus-${VERSION}-${BUILD}-${ASSET}"
URL="https://github.com/NREL/EnergyPlus/releases/download/v${VERSION}/${STEM}.tar.gz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "downloading $STEM"
curl -fsSL -o "$WORK/eplus.tar.gz" "$URL"
tar xzf "$WORK/eplus.tar.gz" -C "$WORK"

mkdir -p "$HOME/opt"
mv "$WORK/$STEM" "$TARGET"
echo "installed EnergyPlus $VERSION to $TARGET"
