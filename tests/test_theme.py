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


def test_system6_tells_volumes_apart_by_hatching_and_the_others_by_colour(qt_app, delete_widgets):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from akaiutil_gui.ui import volume_colors

    def painted(position):
        image = QImage(20, 8, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFF)
        painter = QPainter(image)
        volume_colors.fill_volume(painter, QRectF(0, 0, 20, 8), position)
        painter.end()
        return image

    try:
        theme.set_theme_preference(settings.THEME_DARK)
        assert volume_colors.pattern_brush(0) is None
        theme.set_theme_preference(settings.THEME_SYSTEM6)
        styles = [volume_colors.pattern_brush(i).style() for i in range(8)]
        assert len(set(styles)) == 8 and volume_colors.pattern_brush(8).style() == styles[0]  # (the 9th volume starts over)
        assert all(painted(i) != painted(j) for i in range(8) for j in range(i))
        assert volume_colors.swatch_icon(1).pixmap(16, 16).size().width() == 16
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_system6_tree_icons_are_black_and_white(qt_app, delete_widgets):
    from akaiutil_gui.ui import mono_icons

    try:
        theme.set_theme_preference(settings.THEME_SYSTEM6)
        for make in (mono_icons.folder, mono_icons.floppy, mono_icons.drive):
            image = make().pixmap(16, 16).toImage()
            colours = {image.pixelColor(x, y).name() for x in range(16) for y in range(16) if image.pixelColor(x, y).alpha()}
            assert colours == {"#000000", "#ffffff"}
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_the_details_pane_waits_for_more_room_before_using_two_columns_in_system6(qt_app, delete_widgets):
    from akaiutil_gui.ui.details_view import DetailsView

    view = DetailsView()
    view.resize(1000, 400)
    view.show()
    try:
        theme.set_theme_preference(settings.THEME_DARK)
        view._theme_changed()
        assert view.columns == 2  # (1000 px is wide enough for the usual 900)
        theme.set_theme_preference(settings.THEME_SYSTEM6)
        view._theme_changed()
        assert view.columns == 1
        view.resize(1200, 400)
        qt_app.processEvents()
        assert view.columns == 2
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_system6_question_dialog_buttons_are_all_the_same_height_and_not_squeezed(qt_app, delete_widgets):
    from PySide6.QtWidgets import QPushButton

    from akaiutil_gui.ui import edit_dialogs

    try:
        theme.set_theme_preference(settings.THEME_SYSTEM6)
        dialog = edit_dialogs.ThreeWayDialog(None, "Allow editing?", "Editing changes the disk image files themselves.", "There is no undo.",
                                             "Back Up and Edit", "Edit Without Backup", ["disk0.img"])
        dialog.show()
        qt_app.processEvents()
        buttons = dialog.findChildren(QPushButton)
        assert len(buttons) == 4 and len({b.height() for b in buttons}) == 1  # ("Show Details…" used to be taller: the ellipsis outgrows the line)
        assert all(b.width() >= b.sizeHint().width() for b in buttons)
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_system6_message_box_icons_are_grey_only_and_the_others_keep_the_styles(qt_app, delete_widgets):
    from PySide6.QtWidgets import QStyle

    kinds = (QStyle.StandardPixmap.SP_MessageBoxInformation, QStyle.StandardPixmap.SP_MessageBoxQuestion,
             QStyle.StandardPixmap.SP_MessageBoxWarning, QStyle.StandardPixmap.SP_MessageBoxCritical)

    def colours(kind):
        image = qt_app.style().standardIcon(kind).pixmap(32, 32).toImage()
        return {image.pixelColor(x, y).name() for x in range(32) for y in range(32) if image.pixelColor(x, y).alpha() > 250}

    try:
        theme.apply_to_app(qt_app, settings.THEME_SYSTEM6)
        black_and_white = {kind: colours(kind) for kind in kinds}
        for kind in kinds:
            assert all(c[1:3] == c[3:5] == c[5:7] for c in colours(kind)), kind  # (greys only: the ? has anti-aliased edges)
        theme.set_theme_preference(settings.THEME_DARK)
        assert all(colours(kind) != black_and_white[kind] for kind in kinds)  # (the style's own again: blank on the offscreen platform, coloured elsewhere)
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_system6_sliders_and_progress_bars_are_black_and_white():
    qss = theme.render_stylesheet(theme.SYSTEM6_PALETTE)
    assert "#3aa88a" not in qss[qss.index("Sliders: black") :]
    assert "color: transparent" in qss[qss.rindex("QProgressBar {") :].split("}")[0]


def test_system6_links_in_the_notices_are_dark_and_underlined(qt_app, delete_widgets):
    from akaiutil_gui.ui.notices_dialog import NoticesDialog

    try:
        theme.apply_to_app(qt_app, settings.THEME_SYSTEM6)
        dialog = NoticesDialog()
        links, block = [], dialog.view.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                if it.fragment().isValid() and fmt.isAnchor():
                    links.append((fmt.foreground().color().name(), fmt.fontUnderline()))
                it += 1
            block = block.next()
        assert links and set(links) == {("#101010", True)}
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_system6_buttons_have_a_minimum_height_not_a_fixed_one():
    qss = theme.render_stylesheet(theme.SYSTEM6_PALETTE)
    rule = qss[qss.rindex("QPushButton {") :].split("}")[0]
    assert "min-height" in rule and "max-height" not in rule  # (a fixed height cut the descenders off the g in "Image")


def test_system6_main_window_waveform_shows_its_loop_as_the_editor_does(qt_app, delete_widgets):
    import types

    import numpy as np

    from akaiutil_gui.ui.waveform_preview import PreviewWaveform

    def loop_pixel(preference):
        theme.set_theme_preference(preference)
        wave = PreviewWaveform()
        wave.resize(300, 80)
        wave.set_audio(np.zeros(3000, dtype=np.int16))  # (silence: the wash is all there is to see)
        wave.set_loop(types.SimpleNamespace(start=1000, end=2900))
        wave.show()
        qt_app.processEvents()
        return wave.grab().toImage().pixelColor(150, 20)

    try:
        grey = loop_pixel(settings.THEME_SYSTEM6)
        assert grey.red() == grey.green() == grey.blue() and 150 < grey.red() < 250  # (a light grey wash)
        warm = loop_pixel(settings.THEME_DARK)
        assert warm.red() > warm.blue()  # (the warning colour, as before)
    finally:
        theme.set_theme_preference(settings.THEME_DARK)


def test_system6_inline_code_uses_the_themes_font_and_size_and_the_others_keep_theirs(qt_app, delete_widgets):
    from akaiutil_gui.ui.notices_dialog import NoticesDialog

    def code_formats(preference):
        theme.apply_to_app(qt_app, preference)
        dialog = NoticesDialog()
        found, block = [], dialog.view.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                if it.fragment().isValid() and "LICENSE" in it.fragment().text() and it.fragment().text().strip() == "LICENSE":
                    found.append(fmt)
                it += 1
            block = block.next()
        return found

    try:
        mine = code_formats(settings.THEME_SYSTEM6)
        assert mine and all(f.fontFamilies()[0] == "ChiKareGo2" and f.font().pixelSize() == 13 and not f.fontFixedPitch() for f in mine)
        theirs = code_formats(settings.THEME_DARK)
        assert theirs and all(f.fontFixedPitch() for f in theirs)
    finally:
        theme.set_theme_preference(settings.THEME_DARK)
