# dmgbuild settings for the akaiutil-GUI macOS DMG.
# Used by scripts/build_macos.sh:
#
#   dmgbuild -s scripts/dmg_settings.py -D app=src/akaiutil-GUI.app \
#            -D background=assets/dmg/dmg_background.tiff "akaiutil-GUI" out.dmg
#
# dmgbuild writes the window layout directly (no Finder / AppleScript), so it
# works on a headless CI runner. This file is exec'd by dmgbuild, which
# provides `defines`.
#
# The window size and icon positions must match
# assets/dmg/create_dmg_background.py, which draws the arrow between them.

app_path = defines["app"]  # noqa: F821 - provided by dmgbuild
background_path = defines["background"]  # noqa: F821 - provided by dmgbuild

app_name = app_path.rstrip("/").split("/")[-1]  # eg. "akaiutil-GUI.app"

format = "UDZO"
files = [app_path]
symlinks = {"Applications": "/Applications"}

background = background_path
window_rect = ((200, 120), (660, 400))  # (position on screen), (width, height)
icon_size = 128
text_size = 14
icon_locations = {
    app_name: (170, 210),
    "Applications": (490, 210),
}

show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
default_view = "icon-view"
arrange_by = None
