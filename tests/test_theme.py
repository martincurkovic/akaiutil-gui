"""Both palettes must satisfy every ${placeholder} in the stylesheet template."""

import pytest

from akaiutil_gui.ui import theme


@pytest.mark.parametrize("palette", [theme.DARK_PALETTE, theme.LIGHT_PALETTE], ids=["dark", "light"])
def test_stylesheet_renders(palette):
    qss = theme.render_stylesheet(palette)
    assert "${" not in qss and palette["accent"] in qss


def test_palettes_have_same_keys():
    assert theme.DARK_PALETTE.keys() == theme.LIGHT_PALETTE.keys()


# ------------------------------------------------------------------ the Macintosh System 6 theme
import re

from akaiutil_gui import settings
from test_gui_editing import delete_widgets  # noqa: E402,F401  (fixture: clears leftover widgets before the app-wide restyles below)


def test_system6_has_every_key_the_other_palettes_have():
    assert theme.SYSTEM6_PALETTE.keys() >= theme.DARK_PALETTE.keys()


def test_system6_stylesheet_renders_with_its_own_parts_only_there():
    qss = theme.render_stylesheet(theme.SYSTEM6_PALETTE)
    assert "${" not in qss and "ChiKareGo2" in qss and "s6_check_x.svg" in qss
    assert set(re.findall(r"border-radius:\s*(\d+px)", qss)) <= {"0px", "6px", "8px"}  # (square, but push buttons and radio buttons)
    for palette in (theme.DARK_PALETTE, theme.LIGHT_PALETTE):
        other = theme.render_stylesheet(palette)
        assert "ChiKareGo2" not in other and "s6_" not in other


def test_the_bundled_font_and_icons_are_there():
    import os

    assert os.path.isfile(os.path.join(theme._FONTS_DIR, "ChiKareGo2.ttf"))
    for name in re.findall(r"s6_[a-z_]+\.svg", open(theme._EXTRA_TEMPLATE_PATH).read()):
        assert os.path.isfile(os.path.join(theme._ICONS_DIR, name)), name


def test_choosing_system6_switches_the_palette_and_back_restores_qt_s_own(qt_app, delete_widgets):
    from PySide6.QtGui import QColor, QPalette

    from akaiutil_gui.ui import volume_colors

    try:
        theme.apply_to_app(qt_app, settings.THEME_DARK)
        dark_window = qt_app.palette().color(QPalette.ColorRole.Window)
        theme.set_theme_preference(settings.THEME_SYSTEM6)
        assert theme.current_preference() == settings.THEME_SYSTEM6 and theme.current_palette() is theme.SYSTEM6_PALETTE
        assert qt_app.palette().color(QPalette.ColorRole.Window) == QColor("#ffffff")
        assert {volume_colors.volume_color(i).name() for i in range(8)} != {volume_colors.volume_color(0).name()}  # (eight different greys)
        assert ("Macintosh System 6", settings.THEME_SYSTEM6) in theme.CHOICES
        theme.set_theme_preference(settings.THEME_DARK)
        assert theme.current_palette() is theme.DARK_PALETTE
        assert qt_app.palette().color(QPalette.ColorRole.Window) == dark_window
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_system6_selected_rows_have_white_text_on_black():
    qss = theme.render_stylesheet(theme.SYSTEM6_PALETTE)
    rule = qss[qss.rindex("QTreeView::item:selected") :].split("}")[0]  # (the last one: System 6's own, after the shared rule)
    assert "background-color: #000000" in rule and "color: #ffffff" in rule
    assert "selection-color: #ffffff" in qss[qss.rindex("QTreeView, QTableView") :].split("}")[0]
