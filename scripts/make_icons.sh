#!/usr/bin/env bash
set -e

# make_icons.sh - turns assets/icon/icon_source.png (a square PNG, 1024x1024 or larger) into the
# two files the builds actually use:
#   assets/icon/icon_512x512.png        Linux AppImage + Windows installer
#   assets/icon/akaiutil-GUI-macos.icns macOS app
# Needs macOS (uses sips and iconutil, which ship with it). Commit the results with the source.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ICON_DIR="$ROOT_DIR/assets/icon"
SOURCE="$ICON_DIR/icon_source.png"

[ -f "$SOURCE" ] || { echo "Missing $SOURCE"; exit 1; }
WIDTH=$(sips -g pixelWidth "$SOURCE" | awk '/pixelWidth/ {print $2}')
HEIGHT=$(sips -g pixelHeight "$SOURCE" | awk '/pixelHeight/ {print $2}')
if [ "$WIDTH" != "$HEIGHT" ] || [ "$WIDTH" -lt 1024 ]; then
    echo "icon_source.png must be square and at least 1024x1024 (it is ${WIDTH}x${HEIGHT})"
    exit 1
fi

sips -z 512 512 "$SOURCE" --out "$ICON_DIR/icon_512x512.png" >/dev/null

ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for SIZE in 16 32 128 256 512; do
    sips -z $SIZE $SIZE "$SOURCE" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null
    sips -z $((SIZE * 2)) $((SIZE * 2)) "$SOURCE" --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ICON_DIR/akaiutil-GUI-macos.icns"
rm -rf "$(dirname "$ICONSET")"

echo "Wrote:"
ls -l "$ICON_DIR"
