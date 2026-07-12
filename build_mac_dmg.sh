#!/usr/bin/env bash
# Build TG Reaction Ranker macOS .app and .dmg (run on macOS only).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_NAME="TGReactionRanker"
APP_PATH="dist/${APP_NAME}.app"
DMG_PATH="dist/${APP_NAME}.dmg"
STAGING="dist/dmg-staging"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

echo "==> Preparing Python environment"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
.venv/bin/python -m pip install -q -U pip
.venv/bin/python -m pip install -q -r requirements-dev.txt

echo "==> Generating app icons"
if [[ ! -f assets/TGReactionRanker-icon-1024.png ]]; then
  python tools/generate_app_icon.py
fi
bash scripts/generate_icns.sh

echo "==> Building .app with PyInstaller"
pyinstaller TGReactionRanker-mac.spec --noconfirm --clean

if [[ ! -d "$APP_PATH" ]]; then
  echo "Expected app bundle not found: $APP_PATH" >&2
  exit 1
fi

echo "==> Creating DMG"
rm -rf "$STAGING" "$DMG_PATH"
mkdir -p "$STAGING"
cp -R "$APP_PATH" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

hdiutil create \
  -volname "TG Reaction Ranker" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG_PATH" >/dev/null

rm -rf "$STAGING"

echo ""
echo "Done."
echo "  App: $ROOT/$APP_PATH"
echo "  DMG: $ROOT/$DMG_PATH"
