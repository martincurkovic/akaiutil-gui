#!/usr/bin/env bash
set -e

# make_icns.sh - builds the macOS icon, assets/icon/akaiutil-GUI-macos.icns, with all ten sizes
# (16, 32, 128, 256 and 512 points, each at 1x and 2x) from assets/icon/AppIcon1024.png.
#
# AppIcon1024.png is the master: 1024x1024, already in macOS icon form (rounded square about 824px wide,
# centred, soft shadow, transparent corners), as exported by IconKitchen or similar. The sizes are scaled
# down from it, never up, which is why it has to be the 1024px version and not the 512px one.
# Needs macOS (sips and iconutil ship with it).
#
#   scripts/make_icns.sh [output.icns]

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ICON_DIR="$ROOT_DIR/assets/icon"
MASTER="$ICON_DIR/AppIcon1024.png"
OUTPUT="${1:-$ICON_DIR/akaiutil-GUI-macos.icns}"

[ -f "$MASTER" ] || { echo "Missing $MASTER"; exit 1; }
WIDTH=$(sips -g pixelWidth "$MASTER" | awk '/pixelWidth/ {print $2}')
HEIGHT=$(sips -g pixelHeight "$MASTER" | awk '/pixelHeight/ {print $2}')
if [ "$WIDTH" != "1024" ] || [ "$HEIGHT" != "1024" ]; then
    echo "AppIcon1024.png must be exactly 1024x1024 (it is ${WIDTH}x${HEIGHT})"
    exit 1
fi

WORK="$(mktemp -d)"
ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
for POINTS in 16 32 128 256 512; do
    sips -z $POINTS $POINTS "$MASTER" --out "$ICONSET/icon_${POINTS}x${POINTS}.png" >/dev/null
    DOUBLE=$((POINTS * 2))
    if [ "$DOUBLE" = "1024" ]; then
        cp "$MASTER" "$ICONSET/icon_${POINTS}x${POINTS}@2x.png"
    else
        sips -z $DOUBLE $DOUBLE "$MASTER" --out "$ICONSET/icon_${POINTS}x${POINTS}@2x.png" >/dev/null
    fi
done
iconutil -c icns "$ICONSET" -o "$OUTPUT"
rm -rf "$WORK"

echo "Wrote $OUTPUT"
