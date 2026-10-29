# One-off utility to generate the background image shown in the macOS DMG
# window (an arrow from akaiutil-GUI.app to the Applications shortcut, plus a
# "drag to install" instruction). The output is committed, so the build
# doesn't depend on this script or on any font being installed.
#
# Needs macOS: it uses the system Helvetica font and the built-in `tiffutil`
# to combine the 1x and 2x renders into one hi-DPI TIFF, which Finder picks
# the right half of automatically (sharp on Retina, correct on non-Retina).
#
# Run it by doing:
# uv run python assets/dmg/create_dmg_background.py
#
# Output: assets/dmg/dmg_background.tiff
# Do not rename it - scripts/dmg_settings.py refers to it by name.
#
# The window size and icon positions below must match
# scripts/dmg_settings.py - change them together.

import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "dmg_background.tiff")
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"

# window content size in points, and where the two icons' centres sit
WIDTH, HEIGHT = 660, 400
APP_ICON_X, APPLICATIONS_ICON_X, ICON_Y = 170, 490, 210

# A mid-tone background, so it reads the same under Finder's light and dark appearance (Finder draws the icon names near-black
# either way, so the background has to stay light enough for them). The arrow is the dark theme's accent blue
# (#4a90d9, in src/akaiutil_gui/ui/theme.py) taken a little deeper to stand out on it.
BACKGROUND = (0xB4, 0xBA, 0xC8)
TEXT_COLOR = (0x1E, 0x1E, 0x24)
ARROW_COLOR = (0x2E, 0x6F, 0xB8)


def draw_at(scale):
    img = Image.new("RGB", (WIDTH * scale, HEIGHT * scale), BACKGROUND)
    draw = ImageDraw.Draw(img)

    title_font = ImageFont.truetype(FONT_PATH, 26 * scale)
    draw.text(
        (WIDTH * scale // 2, 55 * scale),
        "Drag akaiutil-GUI to the Applications folder",
        fill=TEXT_COLOR,
        font=title_font,
        anchor="mm",
    )

    # arrow centred between the two icons (128pt icons: edges at x=106..234
    # and 426..554), leaving a gap on each side
    arrow_start, arrow_tip = 255 * scale, 405 * scale
    head_len, head_half = 38 * scale, 24 * scale
    y = ICON_Y * scale
    draw.line(
        [(arrow_start, y), (arrow_tip - head_len, y)], fill=ARROW_COLOR, width=8 * scale
    )
    draw.polygon(
        [
            (arrow_tip - head_len, y - head_half),
            (arrow_tip, y),
            (arrow_tip - head_len, y + head_half),
        ],
        fill=ARROW_COLOR,
    )
    return img


# Pillow doesn't anti-alias lines/polygons and hints small text harshly, so
# drawing straight at 1x/2x gives jagged arrow edges and soft-looking text.
# Instead draw once at 4x the 1x size (2x the 2x size) and downscale with a
# high-quality filter to get smooth edges at both resolutions.
SUPERSAMPLE = 4


def render(scale):
    big = draw_at(SUPERSAMPLE)
    return big.resize((WIDTH * scale, HEIGHT * scale), Image.Resampling.LANCZOS)


with tempfile.TemporaryDirectory() as tmp:
    one_x = os.path.join(tmp, "bg.tiff")
    two_x = os.path.join(tmp, "bg@2x.tiff")
    # Finder treats the second image as the 2x rep because it is exactly
    # twice the pixel size - set the DPI so tiffutil/Finder agree on that
    render(1).save(one_x, dpi=(72, 72))
    render(2).save(two_x, dpi=(144, 144))
    subprocess.run(
        ["tiffutil", "-cathidpicheck", one_x, two_x, "-out", OUTPUT], check=True
    )

print(f"Wrote {OUTPUT}")
