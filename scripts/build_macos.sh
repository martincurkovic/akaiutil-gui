#!/usr/bin/env bash
set -e

# build_macos.sh - builds the akaiutil-GUI.app bundle and the DMG that installs it
# Can be run from anywhere. Handles uv sync, and fetching + compiling the akaiutil it bundles.

APP_NAME="akaiutil-GUI"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"

cd "$ROOT_DIR"
echo "Syncing environment with uv..."
uv sync
uv pip install pip

# the app contains a compiled copy of akaiutil (pinned release, checksum-verified). Build it for macOS 14, the oldest the app
# supports, rather than for whatever the build machine runs (which would make it refuse to start on anything older)
export MACOSX_DEPLOYMENT_TARGET=14.0
echo "Fetching akaiutil..."
uv run python scripts/get_akaiutil.py

cd "$SRC_DIR"

# derive version from nearest git tag - falls back to dev placeholder if no tags exist yet
RAW_TAG=$(git -C "$ROOT_DIR" describe --tags --abbrev=0 2>/dev/null || true)
if [ -z "$RAW_TAG" ]; then
    VERSION="0.0.0-dev"
else
    VERSION=$(echo "$RAW_TAG" | sed 's/^v//')
fi
echo "Building version: $VERSION"
echo "APP_VERSION = \"$VERSION\"" >"$SRC_DIR/akaiutil_gui/_version.py"

if [ ! -f "pysidedeploy.spec" ]; then
    echo "No spec file found - generating from defaults"

    uv run pyside6-deploy --init main.py

    sed -i '' "s/^title = .*/title = $APP_NAME/" pysidedeploy.spec
    sed -i '' "s|^icon = .*|icon = ../assets/icon/$APP_NAME-macos.icns|" pysidedeploy.spec
    sed -i '' 's/^mode = .*/mode = standalone/' pysidedeploy.spec

    echo "Spec generated and configured."
fi

# Data the app reads at run time, paths relative to src/ (where this runs), as SRC=DEST inside the app:
#   the stylesheet template, icons and notices, the licence, and the akaiutil binary (found next to the executable)
DATA_ARGS="--include-data-dir=akaiutil_gui/ui/icons=akaiutil_gui/ui/icons --include-data-files=akaiutil_gui/ui/style.qss.template=akaiutil_gui/ui/style.qss.template --include-data-files=akaiutil_gui/ui/style_system6.qss.template=akaiutil_gui/ui/style_system6.qss.template --include-data-dir=akaiutil_gui/ui/fonts=akaiutil_gui/ui/fonts --include-data-dir=akaiutil_gui/ui/help=akaiutil_gui/ui/help --include-data-files=../LICENSE=LICENSE --include-data-files=../build/bin/akaiutil=bin/akaiutil"

# re-generate extra_args on EVERY run, that way version number can update correctly
sed -i '' "s|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --macos-app-name=$APP_NAME --macos-app-version=$VERSION $DATA_ARGS|" pysidedeploy.spec

echo "Building $APP_NAME.app..."
uv run pyside6-deploy -c pysidedeploy.spec

# Nuitka has no option for a minimum macOS version, so set it in Info.plist: on anything older macOS then says the app can't be
# opened with this version instead of letting it crash. Nuitka's ad-hoc signature seals Info.plist, so sign again the way Nuitka does.
MIN_MACOS="14.0"
plutil -replace LSMinimumSystemVersion -string "$MIN_MACOS" "$APP_NAME.app/Contents/Info.plist"
/usr/bin/codesign -s - --force --deep --preserve-metadata=entitlements "$APP_NAME.app"

# The DMG: dmgbuild (a build dependency, pure Python) writes the window layout directly - no Finder/AppleScript, so it works on a
# headless CI runner. It opens as a window with the app, an Applications shortcut and a background image with an arrow and
# "Drag $APP_NAME to the Applications folder". Layout: scripts/dmg_settings.py; background: assets/dmg/dmg_background.tiff
# (regenerate with assets/dmg/create_dmg_background.py). The name is what the workflows upload and the Homebrew cask downloads.
case "$(uname -m)" in
arm64) DMG_ARCH="arm" ;;
*) DMG_ARCH="intel" ;;
esac
DMG_NAME="$APP_NAME-macos-$DMG_ARCH.dmg"
echo "Creating $DMG_NAME..."
rm -f "$ROOT_DIR/$DMG_NAME"
uv run dmgbuild \
    -s "$ROOT_DIR/scripts/dmg_settings.py" \
    -D app="$APP_NAME.app" \
    -D background="$ROOT_DIR/assets/dmg/dmg_background.tiff" \
    "$APP_NAME" "$ROOT_DIR/$DMG_NAME"

echo ""
echo "Done - check the output above for exactly where the .app landed."
echo "DMG: $ROOT_DIR/$DMG_NAME"
