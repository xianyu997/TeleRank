#!/usr/bin/env bash
# Generate assets/TGReactionRanker.icns from the 1024px PNG (macOS only).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PNG="$ROOT/assets/TGReactionRanker-icon-1024.png"
ICONSET="$ROOT/assets/TGReactionRanker.iconset"
ICNS="$ROOT/assets/TGReactionRanker.icns"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "generate_icns.sh must run on macOS (needs sips + iconutil)." >&2
  exit 1
fi

if [[ ! -f "$PNG" ]]; then
  echo "Missing $PNG — run: python tools/generate_app_icon.py" >&2
  exit 1
fi

rm -rf "$ICONSET"
mkdir -p "$ICONSET"

sips -z 16 16     "$PNG" --out "$ICONSET/icon_16x16.png"      >/dev/null
sips -z 32 32     "$PNG" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
sips -z 32 32     "$PNG" --out "$ICONSET/icon_32x32.png"      >/dev/null
sips -z 64 64     "$PNG" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
sips -z 128 128   "$PNG" --out "$ICONSET/icon_128x128.png"    >/dev/null
sips -z 256 256   "$PNG" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "$PNG" --out "$ICONSET/icon_256x256.png"    >/dev/null
sips -z 512 512   "$PNG" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$PNG" --out "$ICONSET/icon_512x512.png"    >/dev/null
cp "$PNG" "$ICONSET/icon_512x512@2x.png"

iconutil -c icns "$ICONSET" -o "$ICNS"
rm -rf "$ICONSET"
echo "wrote $ICNS"
