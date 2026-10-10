"""
Theme palettes and stylesheet rendering.

Two full palettes (DARK_PALETTE / LIGHT_PALETTE) live here as plain
dicts - the single source of truth for every color in the app.
style.qss.template holds the actual QSS RULES (selectors, properties,
layout) with ${key} placeholders standing in for colors; this module
fills those in at load time.

To add a new themed color:
    1. Use ${your_key} somewhere in style.qss.template
    2. Add "your_key" to BOTH palettes below
Forgetting either one raises a clear KeyError at startup rather than
silently rendering something wrong - much safer than hand-maintaining
two separate .qss files that can quietly drift apart over time.
"""

import logging
import os
import sys
from string import Template

from PySide6.QtCore import QObject, Qt, Signal

from akaiutil_gui import settings

DARK_PALETTE = {
    "bg": "#1e1e24",
    "bg_panel": "#202028",
    "bg_dialog": "#232330",
    "bg_statusbar": "#17171c",
    "bg_input": "#2a2a35",
    "bg_button": "#2d2d38",
    "bg_button_hover": "#383846",
    "bg_button_pressed": "#232330",
    "bg_disabled": "#26262e",
    "bg_selected": "#2f3f57",
    "text": "#e4e4ea",
    "text_bright": "#f0f0f4",
    "text_disabled": "#6a6a76",
    "text_statusbar": "#c8c8d0",
    "border": "#40404c",
    "border_hover": "#4f4f5e",
    "border_disabled": "#333340",
    "border_list": "#33333e",
    "border_item": "#2a2a34",
    "border_checkbox": "#55555f",
    "border_progress": "#3a3a44",
    "accent": "#4a90d9",
    "text_on_accent": "#ffffff",
    "warning": "#d9a94a",
    "critical": "#d9544a",
    # alternating-row tint for tables/trees. Deliberately close to bg_panel
    # rather than bg_input/bg_selected - a stripe should read as a faint
    # banding cue, not as an editable field or a selection highlight.
    "bg_zebra": "#25252d",
    # categorical colours for volumes (the disk usage bar and the swatch beside
    # each volume in the tree). Same dataviz-validated 8-slot set AKAISDS uses
    # for keygroups, in the same fixed order. Not referenced by the QSS template:
    # read directly by custom-painted widgets. Never cycle the assignment, only
    # reuse a colour past the 8th volume.
    "volume_color_1": "#3987e5",
    "volume_color_2": "#d95926",
    "volume_color_3": "#199e70",
    "volume_color_4": "#c98500",
    "volume_color_5": "#d55181",
    "volume_color_6": "#008300",
    "volume_color_7": "#9085e9",
    "volume_color_8": "#e66767",
}

LIGHT_PALETTE = {
    # soft lavender-tinted neutrals, echoing the dark theme's cool
    # violet-grey cast instead of going plain/neutral grey
    "bg": "#f5f3fa",
    "bg_panel": "#ffffff",
    "bg_dialog": "#faf8ff",
    "bg_statusbar": "#ece7f5",
    "bg_input": "#ffffff",
    "bg_button": "#eeeaf7",
    "bg_button_hover": "#e1d8f0",
    "bg_button_pressed": "#d2c5e9",
    "bg_disabled": "#eeedf2",
    "bg_selected": "#e3d8f7",
    "text": "#2a2438",
    "text_bright": "#191320",
    "text_disabled": "#9a92aa",
    "text_statusbar": "#5c5470",
    "border": "#d6cce8",
    "border_hover": "#b9a4d9",
    "border_disabled": "#e3ddee",
    "border_list": "#ddd0ee",
    "border_item": "#ece4f7",
    "border_checkbox": "#b0a0d0",
    "border_progress": "#ddd0ee",
    "accent": "#8b5cf6",  # a genuine violet, distinct from dark theme's
    # blue accent - ties into the same purple-ish
    # family as the dark theme's neutrals without
    # just being a flat grey/blue inversion
    "text_on_accent": "#ffffff",
    "warning": "#f57927",
    "critical": "#d54439",
    # same reasoning as DARK_PALETTE's bg_zebra - close to this theme's own
    # bg_panel ("#ffffff"), just a faint step down rather than a strong tint
    "bg_zebra": "#f6f3fb",
    # same 8-slot categorical set as DARK_PALETTE, stepped for the light surface
    "volume_color_1": "#2a78d6",
    "volume_color_2": "#eb6834",
    "volume_color_3": "#1baf7a",
    "volume_color_4": "#eda100",
    "volume_color_5": "#e87ba4",
    "volume_color_6": "#008300",
    "volume_color_7": "#4a3aa7",
    "volume_color_8": "#e34948",
}


def _user_data_dir():
    # returns per-user location OUTSIDE the app bundle for runtime generated files
    # writing inside the app bundle is fragile and seems to cause problems on macOS specifically
    # will also cause issues in the future if code signing becomes feasable
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    path = os.path.join(base, "akaiutil-gui")
    os.makedirs(path, exist_ok=True)
    return path


_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "style.qss.template")
_ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")
_GENERATED_ICONS_DIR = os.path.join(_user_data_dir(), "generated_icons")


def _icon_path(filename):
    return os.path.join(_ICONS_DIR, filename).replace(os.sep, "/")


def _write_colored_chevron(direction, color, filename):
    # filename gets the color baked in (see render_stylesheet) so a given
    # file's contents NEVER change - Qt's stylesheet engine caches images
    # by path, so rewriting the same "down_arrow.svg" with a different
    # color on a live theme switch could keep serving the old one
    # generate a chevron svg with the color theme baked in and write it to disk
    # this is the arrow for the dropdown menus/combo boxes
    if direction == "down":
        path_d = "M2 4 L6 8 L10 4"
    else:  # "up"
        path_d = "M2 8 L6 4 L10 8"

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12">'
        f'<path d="{path_d}" stroke="{color}" stroke-width="1.6" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )

    os.makedirs(_GENERATED_ICONS_DIR, exist_ok=True)
    path = os.path.join(_GENERATED_ICONS_DIR, filename)
    with open(path, "w") as f:
        f.write(svg)
    return path.replace(os.sep, "/")


def render_stylesheet(palette):
    """Render the QSS template with the given palette dict.

    Raises KeyError with a clear, actionable message if the template
    references a color that's missing from this palette - this is the
    thing that keeps the two themes from silently drifting apart.
    """
    values = dict(palette)
    values["checkmark_icon"] = _icon_path("checkmark.svg")
    text_hex = palette["text"].lstrip("#")
    disabled_hex = palette["text_disabled"].lstrip("#")
    values["down_arrow_icon"] = _write_colored_chevron(
        "down", palette["text"], f"down_arrow_{text_hex}.svg"
    )
    values["down_arrow_icon_disabled"] = _write_colored_chevron(
        "down", palette["text_disabled"], f"down_arrow_disabled_{disabled_hex}.svg"
    )
    values["up_arrow_icon"] = _write_colored_chevron(
        "up", palette["text"], f"up_arrow_{text_hex}.svg"
    )

    with open(_TEMPLATE_PATH, "r") as f:
        template = Template(f.read())
    try:
        return template.substitute(**values)
    except KeyError as e:
        raise KeyError(
            f"style.qss.template references ${{{e.args[0]}}}, which isn't in this "
            f"palette - add it to both DARK_PALETTE and LIGHT_PALETTE in ui/theme.py"
        ) from e


_active_palette = None  # set by _render_for_current_scheme() - see current_palette()
_app = None  # the QApplication apply_to_app() was given
_preference = settings.THEME_SYSTEM
_connected_app = None  # whose colorSchemeChanged apply_to_app() already hooked

#: (label, value) in the order the Settings > Appearance combo shows them
CHOICES = [
    ("System", settings.THEME_SYSTEM),
    ("Light", settings.THEME_LIGHT),
    ("Dark", settings.THEME_DARK),
]


class _ThemeNotifier(QObject):
    # emitted after every real theme change (OS scheme change under "System",
    # or a new preference) - for widgets that bake a palette color into
    # themselves when they're BUILT (an inline setStyleSheet, a list-row
    # swatch) rather than reading current_palette() at paint time, and so
    # would otherwise keep the previous theme's color until rebuilt
    changed = Signal()


notifier = _ThemeNotifier()


def _palette_for_scheme(scheme):
    return LIGHT_PALETTE if scheme == Qt.ColorScheme.Light else DARK_PALETTE


def _effective_scheme():
    """The color scheme the app is actually showing right now.

    A pinned theme is answered from the preference itself, NOT by asking Qt
    what scheme it reports after set_theme_preference() overrode it: that
    override is only honored where the platform supports it (Qt older than
    6.8 has none, and the offscreen/test platform ignores it - colorScheme()
    just keeps reporting Unknown), and the palette must follow the user's
    choice everywhere. Only "System" asks the OS.
    """
    from PySide6.QtGui import QGuiApplication

    if _preference == settings.THEME_LIGHT:
        return Qt.ColorScheme.Light
    if _preference == settings.THEME_DARK:
        return Qt.ColorScheme.Dark
    return QGuiApplication.styleHints().colorScheme()


def current_preference():
    """The theme choice in effect: "system", "light" or "dark"."""
    return _preference


def current_palette():
    """Returns whichever palette (DARK_PALETTE/LIGHT_PALETTE) the app is
    ACTUALLY styled with right now, for custom-painted widgets (QPainter,
    not QSS) that need a themed color - e.g. a future waveform preview - so
    they can never disagree with the stylesheet.

    Deliberately doesn't re-derive this from the OS color scheme on every
    call - _render_for_current_scheme() records the palette it actually
    rendered into _active_palette, and this just echoes that back. Two
    independent "ask the OS what scheme we're in" queries (one for the
    stylesheet, one for each custom-painted widget) can disagree - wrong
    Qt/platform version, a stylesheet applied by hand for a preview/test, a
    live scheme change caught mid-flight - so there is exactly one place
    that decides, and everything else just reads its answer.
    """
    if _active_palette is not None:
        return _active_palette
    # apply_to_app() hasn't run (e.g. a widget previewed standalone without
    # going through it) - fall back to asking directly
    return _palette_for_scheme(_effective_scheme())


def _render_for_current_scheme():
    # re-renders the stylesheet if (and only if) the palette the app should
    # now be using differs from the one it already has - so the OS firing
    # colorSchemeChanged for a scheme we already show, or choosing "Dark"
    # while the OS is already dark, does no redundant app-wide restyle
    global _active_palette
    palette = _palette_for_scheme(_effective_scheme())
    if palette is _active_palette:
        return
    _active_palette = palette
    try:
        _app.setStyleSheet(render_stylesheet(palette))
    except (OSError, KeyError) as e:
        # runs on every launch and every live theme change, in a packaged
        # GUI app with no attached console, so log rather than print
        logging.getLogger(__name__).error(
            "couldn't apply stylesheet (%s) - keeping the current one", e, exc_info=True
        )
    notifier.changed.emit()


def set_theme_preference(preference):
    """Switches the live app to "system", "light" or "dark".

    Also sets Qt's own app-wide color scheme override where it exists
    (6.8+), so native chrome (a macOS title bar, native dialogs) follows
    too, not just this app's stylesheet; "system" removes the override and
    hands control back to the OS. That override is best-effort only - the
    palette itself comes from _effective_scheme(), and the stylesheet is
    re-rendered here directly rather than waiting on colorSchemeChanged,
    which isn't guaranteed to fire for every platform/Qt combination.
    """
    from PySide6.QtGui import QGuiApplication

    global _preference
    _preference = (
        preference if preference in settings.THEME_VALUES else settings.THEME_SYSTEM
    )
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):
        if _preference == settings.THEME_SYSTEM:
            hints.unsetColorScheme()
        else:
            hints.setColorScheme(
                Qt.ColorScheme.Light
                if _preference == settings.THEME_LIGHT
                else Qt.ColorScheme.Dark
            )
    if _app is not None:
        _render_for_current_scheme()


def apply_to_app(app, preference=None):
    """Sets the Fusion style and this app's stylesheet for the saved theme
    choice (or `preference`, if given), live-matched to the OS color scheme
    while that choice is "System". Shared by every entry point (main.py, and
    any window launched standalone for dev work) so they always look the
    same.
    """
    from PySide6.QtGui import QGuiApplication

    global _app, _connected_app
    _app = app
    app.setStyle("Fusion")
    if preference is None:
        preference = settings.get_theme()
    set_theme_preference(preference)
    _render_for_current_scheme()  # also covers the very first apply
    # live switch if the user changes their system theme while the app is
    # running - harmless under a pinned theme: Qt keeps reporting the pinned
    # scheme, so the palette comparison in _render_for_current_scheme()
    # finds nothing to do
    if _connected_app is not app:
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _scheme: _render_for_current_scheme()
        )
        _connected_app = app
