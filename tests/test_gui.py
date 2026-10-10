"""Drives the real window (offscreen) against the real binary and generated images."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from akaiutil_gui import search, settings
from akaiutil_gui.engine.models import Volume
from akaiutil_gui.ui import theme


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # keep QSettings (window geometry, last dirs, theme) out of the real user prefs
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("qs")))
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("akaiutil-gui-test")
    app.setApplicationName("test")
    return app


@pytest.fixture
def window(app, binary):
    from akaiutil_gui.ui.main_window import MainWindow

    theme.apply_to_app(app, settings.THEME_DARK)
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()


def pump(app, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_browse_hd_select_volume_and_file(app, window, images):
    window.open_images([str(images["hd"]), str(images["fl1"]), str(images["fl9"])])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 3)

    hd = window.tree.topLevelItem(0)
    assert pump(app, lambda: hd.childCount() == 18)
    part_a = hd.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: [part_a.child(i).text(0) for i in range(part_a.childCount())] == ["DRUMS", "BASS", "MY SYNTH"])

    window.tree.setCurrentItem(part_a.child(0))
    assert pump(app, lambda: window.table.rowCount() == 2)
    assert isinstance(window._volume, Volume)
    assert not window.export_btn.isEnabled()  # nothing selected yet

    window.table.selectRow(0)
    assert pump(app, lambda: "Sample rate" in window.info.toPlainText())  # the readable version of the file's details
    assert "ramname" not in window.info.toPlainText() and "srate" not in window.info.toPlainText()
    assert window.export_btn.isEnabled() and window.export_wav_btn.isEnabled()

    window.table.selectAll()
    assert pump(app, lambda: "2 files selected" in window.info.toPlainText())

    # a volume whose name contains a space
    window.tree.setCurrentItem(part_a.child(2))
    assert pump(app, lambda: window.table.rowCount() == 1 and window.table.item(0, 1).text() == "LEAD.S3")


def test_single_floppy_selects_its_volume_automatically(app, window, images):
    window.open_images([str(images["fl1"])])
    assert pump(app, lambda: window.table.rowCount() == 2)
    assert window._volume.type == "S1000"


def test_close_images_returns_to_empty_state(app, window, images):
    window.open_images([str(images["fl9"])])
    assert pump(app, lambda: window.table.rowCount() == 1)
    window.close_images()
    assert window.stack.currentIndex() == 0 and window.table.rowCount() == 0


def test_missing_image_shows_an_error(app, window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
    window.open_images([str(tmp_path / "does-not-exist.img")])
    assert pump(app, lambda: shown)
    assert "cannot open" in shown[0]
    assert window.stack.currentIndex() == 0  # back on the empty page


def test_unformatted_image_is_flagged_not_silent(app, window, tmp_path):
    junk = tmp_path / "junk.img"
    junk.write_bytes(os.urandom(4096))
    window.open_images([str(junk)])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    item = window.tree.topLevelItem(0)
    assert "no Akai filesystem" in item.text(0) and item.childCount() == 0
    assert window.statusBar().currentMessage().startswith("⚠")


@pytest.mark.parametrize("pref", [settings.THEME_DARK, settings.THEME_LIGHT])
def test_theme_switch_rerenders(app, window, pref):
    theme.set_theme_preference(pref)
    assert theme.current_palette() is (theme.DARK_PALETTE if pref == settings.THEME_DARK else theme.LIGHT_PALETTE)
    assert theme.current_palette()["accent"] in app.styleSheet()


def test_layout_details(app, window, images):
    # no resize-grip square in the status bar's corner
    assert not window.statusBar().isSizeGripEnabled()

    # the disk tree and the file table start at the same height
    window.open_images([str(images["fl1"])])
    assert pump(app, lambda: window.table.rowCount() == 2)
    app.processEvents()
    tree_top = window.tree.mapTo(window, window.tree.rect().topLeft()).y()
    table_top = window.table.mapTo(window, window.table.rect().topLeft()).y()
    assert tree_top == table_top


# ---------------------------------------------------------------- click behaviour
def _quick_click(widget, pos, first=False):
    """A click that follows the previous one inside the double-click interval,
    so Qt delivers its press as a MouseButtonDblClick (as it does for a real,
    quick second click). QTest.mouseClick() would space clicks out to avoid that."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    L, NO = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    QTest.mousePress(widget, L, NO, pos, 0 if first else 20)
    QTest.mouseRelease(widget, L, NO, pos, 20)


def _open_hd(app, window, images):
    window.open_images([str(images["hd"])])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    disk = window.tree.topLevelItem(0)
    assert pump(app, lambda: disk.childCount() == 18)
    return disk


def _glyph_pixels(app, tree, item):
    """How many pixels in the item's arrow cell differ from the cell's own background."""
    app.processEvents()
    img = tree.grab().toImage()
    r = tree.visualItemRect(item)
    x0 = r.left() - tree.indentation()
    bg = img.pixelColor(x0, r.top() + 1)

    def differs(x, y):
        c = img.pixelColor(x, y)
        return abs(c.red() - bg.red()) + abs(c.green() - bg.green()) + abs(c.blue() - bg.blue()) > 90

    return sum(differs(x, y) for x in range(x0, r.left() - 2) for y in range(r.top() + 2, r.bottom() - 2))


def test_empty_folder_keeps_its_arrow(app, window, images):
    disk = _open_hd(app, window, images)
    tree, empty = window.tree, disk.child(1)  # partition B has no volumes
    empty.setExpanded(True)
    assert pump(app, lambda: empty.childCount() == 0 and empty.data(0, 0x101))  # loaded, found nothing
    assert empty.isExpanded()
    expanded_glyph = _glyph_pixels(app, tree, empty)
    assert expanded_glyph > 15, "arrow vanished from an empty, expanded folder"

    empty.setExpanded(False)  # collapse again: still there, now pointing the other way
    assert not empty.isExpanded()
    collapsed_glyph = _glyph_pixels(app, tree, empty)
    assert collapsed_glyph > 15, "arrow vanished from an empty, collapsed folder"

    # a leaf (a volume) never gets one
    part_a = disk.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: part_a.childCount() == 3)
    assert _glyph_pixels(app, tree, part_a.child(0)) == 0


def test_second_quick_click_on_tree_arrow_is_not_lost(app, window, images):
    from PySide6.QtCore import QPoint

    disk = _open_hd(app, window, images)
    tree, item = window.tree, disk.child(5)
    r = tree.visualItemRect(item)
    arrow = QPoint(r.left() - tree.indentation() // 2, r.center().y())

    _quick_click(tree.viewport(), arrow, first=True)
    pump(app, lambda: False, 0.1)
    assert item.isExpanded()
    _quick_click(tree.viewport(), arrow)  # arrives as a double-click event
    pump(app, lambda: False, 0.1)
    assert not item.isExpanded()


def test_header_sort_reverses_on_a_quick_second_click(app, window, images):
    from PySide6.QtCore import QPoint, Qt

    window.open_images([str(images["fl1"])])
    assert pump(app, lambda: window.table.rowCount() == 2)
    hdr = window.table.horizontalHeader()
    pos = QPoint(hdr.sectionViewportPosition(1) + hdr.sectionSize(1) // 2, hdr.height() // 2)  # middle of "Name"

    _quick_click(hdr.viewport(), pos, first=True)
    pump(app, lambda: False, 0.1)
    assert (hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()) == (1, Qt.SortOrder.AscendingOrder)
    _quick_click(hdr.viewport(), pos)
    pump(app, lambda: False, 0.1)
    assert (hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()) == (1, Qt.SortOrder.DescendingOrder)
    assert [window.table.item(r, 1).text() for r in range(2)] == ["SNARE.S1", "KICK.S1"]


# ------------------------------------------------------------------ usage bar
def _disk_usage_models():
    """A small synthetic disk: partition A is busy (93%), B is mostly empty, plus space outside both."""
    from akaiutil_gui.engine.models import Disk, DiskUsage, Partition, VolumeUsage

    bs = 8192
    disk = Disk(0, "HD", 2, bs, 1000, 600, 7.8, 4.7, 60.0)
    a = Partition(0, "A", "HD", bs, 400, 28, 3.1, 0.2, 7.0)  # 372 of 400 blocks used = 93%
    b = Partition(0, "B", "HD", bs, 400, 372, 3.1, 2.9, 93.0)  # 7%
    vols = [
        VolumeUsage(0, "A", 1, "STRINGS", 300 * bs, 4),
        VolumeUsage(0, "A", 2, "PADS", 60 * bs, 1),
        VolumeUsage(0, "B", 1, "DRUMS", 20 * bs, 2),
    ]
    return DiskUsage(disk, [a, b], vols)


def test_usage_spans_for_a_disk_and_a_partition():
    from akaiutil_gui.ui.usage_bar import disk_spans, partition_spans

    usage = _disk_usage_models()
    spans = disk_spans(usage)  # the disk level shows partitions only, not the volumes inside them
    assert [s.kind for s in spans] == ["partition", "partition", "outside"]
    assert [s.weight for s in spans] == [400, 400, 200]
    assert sum(s.weight for s in spans) == usage.disk.total_blocks  # the bar covers the whole disk exactly
    assert [round(s.used, 2) for s in spans[:2]] == [0.93, 0.07]
    assert [s.divider_after for s in spans] == [True, True, False]
    assert "Partition A" in spans[0].tooltip and "93% used" in spans[0].tooltip and "2 volumes" in spans[0].tooltip
    assert "1 volume" in spans[1].tooltip and "1 volumes" not in spans[1].tooltip

    only_a = partition_spans(usage, "A")  # a partition shows its volumes, coloured by position on the disk
    assert [s.kind for s in only_a] == ["volume", "volume", "system", "free"] and not any(s.divider_after for s in only_a)
    assert [s.volume for s in only_a[:2]] == [0, 1]
    assert sum(s.weight for s in only_a) == 400
    assert "STRINGS" in only_a[0].tooltip and "4 files" in only_a[0].tooltip


def test_partition_cells_are_filled_in_the_colour_of_how_full_they_are(app):
    from akaiutil_gui.ui.usage_bar import UsageBar, disk_spans, level_color

    p = theme.current_palette()
    assert level_color(0.0).name() == p["accent"] and level_color(0.699).name() == p["accent"]
    assert level_color(0.70).name() == p["warning"] and level_color(0.899).name() == p["warning"]
    assert level_color(0.90).name() == p["critical"] and level_color(1.0).name() == p["critical"]

    bar = UsageBar()
    bar.resize(402, 16)
    bar.set_spans(disk_spans(_disk_usage_models()))  # A is 93% used (critical), B is 7% (blue)
    img = bar.grab().toImage()
    a, b, _outside = bar.span_rects()
    mid = img.height() // 2
    px = lambda x: img.pixelColor(int(x), mid).name()
    assert px(a.left() + 3) == level_color(0.93).name()  # A: used part, in the critical colour
    assert px(a.right() - 4) == p["bg_disabled"]  # ...and its free tail is empty track
    assert px(b.left() + 3) == level_color(0.07).name()  # B: used part, blue
    assert px(b.right() - 4) == p["bg_disabled"]


def test_usage_bar_geometry(app):
    from akaiutil_gui.ui.usage_bar import Span, UsageBar

    bar = UsageBar()
    bar.resize(402, 16)
    bar.set_spans([Span(100, "volume", 1, "one"), Span(100, "free", tooltip="free"), Span(200, "outside", tooltip="out")])
    rects = bar.span_rects()
    assert all(abs(a.right() - b.left()) < 1e-9 for a, b in zip(rects, rects[1:])), "segments must be flush, with no gaps"
    assert abs(rects[0].left() - 0.5) < 1e-9 and abs(rects[-1].right() - 401.5) < 1e-9  # exactly fills the track
    assert abs(rects[2].width() / rects[0].width() - 2) < 1e-6
    assert [bar.span_at(r.center()).tooltip for r in rects] == ["one", "free", "out"]

    # a volume far too small to see is widened to a hairline, taking the room from the rest
    bar.set_spans([Span(1, "volume", 3), Span(100000, "free")])
    tiny, rest = bar.span_rects()
    assert abs(tiny.width() - 1.0) < 1e-9 and abs(rest.right() - 401.5) < 1e-9

    bar.clear()
    assert bar.span_rects() == []


def test_volume_colors_are_stable_and_shared(app):
    from akaiutil_gui.ui.volume_colors import swatch_icon, volume_color

    names = [volume_color(n).name() for n in range(8)]
    assert len(set(names)) == 8  # eight distinct colours...
    assert volume_color(8).name() == names[0] and volume_color(16).name() == names[0]  # ...reused past the 8th
    assert volume_color(None).name() not in names  # not known yet: neutral grey

    img = swatch_icon(1).pixmap(16, 16).toImage()
    assert img.pixelColor(img.width() // 2, img.height() // 2).name() == volume_color(1).name()


def test_usage_label_warns_as_a_partition_fills_up(app):
    from akaiutil_gui.ui.usage_bar import UsageSummary

    usage, summary = _disk_usage_models(), UsageSummary()
    summary.show_partition(usage, "A")  # 93% used
    assert summary.label.property("level") == "critical" and "93% used" in summary.label.text()
    summary.show_partition(usage, "B")  # 7% used
    assert summary.label.property("level") == "normal"
    summary.show_disk(usage)
    assert summary.label.property("level") == "normal" and "outside any partition" in summary.label.text()

    from akaiutil_gui.engine.models import Partition

    for used_blocks, level in [(69, "normal"), (70, "warning"), (89, "warning"), (90, "critical")]:
        p = Partition(0, "C", "HD", 8192, 100, 100 - used_blocks, 0.0, 0.0, 0.0)
        u = _disk_usage_models()
        u = type(u)(u.disk, [p], [])
        summary.show_partition(u, "C")
        assert summary.label.property("level") == level, used_blocks


def test_usage_shows_up_as_soon_as_an_image_opens(app, window, images):
    """Regression: the bar and the details only appeared after you clicked something."""
    window.open_images([str(images["hd"])])
    assert pump(app, lambda: window.usage.bar.spans)  # no clicking at all
    assert "18 partitions" in window.usage.label.text()
    assert window.tree.currentItem() is window.tree.topLevelItem(0)  # the first disk got selected for you
    assert pump(app, lambda: "Disk 0" in window.info.toPlainText())


def test_usage_follows_selection(app, window, images):
    disk = _open_hd(app, window, images)
    usage = window.usage
    assert pump(app, lambda: usage.bar.spans)
    assert [s.kind for s in usage.bar.spans] == ["partition"] * 18 + ["outside"]  # the disk: partitions only
    assert "outside any partition" in usage.label.text() and "most free in one:" in usage.label.text()

    part_a = disk.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: part_a.childCount() == 3)
    window.tree.setCurrentItem(part_a)  # a partition: just that one
    assert pump(app, lambda: usage.label.text().startswith("Partition A:"))
    assert [s.kind for s in usage.bar.spans] == ["volume", "volume", "volume", "system", "free"]
    assert [s.volume for s in usage.bar.spans[:3]] == [0, 1, 2]

    window.tree.setCurrentItem(part_a.child(1))  # a volume: its partition
    assert pump(app, lambda: usage.label.text().startswith("Partition A:"))
    assert len(usage.bar.spans) == 5

    window.tree.setCurrentItem(disk.child(1))  # an empty partition: no volumes, mostly free
    assert pump(app, lambda: usage.label.text().startswith("Partition B:"))
    assert [s.kind for s in usage.bar.spans] == ["system", "free"]

    window.close_images()
    assert usage.bar.spans == [] and usage.label.text() == ""


def test_floppy_bar_has_no_outside_space(app, window, images):
    window.open_images([str(images["fl1"])])
    assert pump(app, lambda: window.table.rowCount() == 2)  # the lone floppy jumps to its volume...
    assert pump(app, lambda: window.usage.bar.spans)  # ...and its partition's space shows
    assert window.usage.label.text().startswith("Partition A:")
    assert not any(s.kind == "outside" for s in window.usage.bar.spans)


def test_volume_rows_wear_their_swatch_and_follow_the_theme(app, window, images):
    from akaiutil_gui.ui.volume_colors import volume_color

    disk = _open_hd(app, window, images)
    part_a = disk.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: part_a.childCount() == 3)

    def center_of(item):
        img = item.icon(0).pixmap(16, 16).toImage()
        return img.pixelColor(img.width() // 2, img.height() // 2).name()

    assert pump(app, lambda: center_of(part_a.child(0)) == volume_color(0).name())  # arrives once the disk's sizes are in
    for n in range(3):  # DRUMS, BASS, MY SYNTH: positions 0, 1, 2 on the disk
        assert center_of(part_a.child(n)) == volume_color(n).name()

    theme.set_theme_preference(settings.THEME_LIGHT)  # the baked-in pixmaps must be redone
    assert center_of(part_a.child(0)) == theme.LIGHT_PALETTE["volume_color_1"]
    assert center_of(part_a.child(1)) == theme.LIGHT_PALETTE["volume_color_2"]
    theme.set_theme_preference(settings.THEME_DARK)
    assert center_of(part_a.child(0)) == theme.DARK_PALETTE["volume_color_1"]


def test_every_disks_swatches_are_coloured_without_selecting_it(app, window, images):
    from akaiutil_gui.ui.volume_colors import volume_color

    window.open_images([str(images["hd"]), str(images["fl1"]), str(images["fl9"])])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 3)
    fl1 = window.tree.topLevelItem(1)  # the first disk is the one that's selected; this one isn't
    assert pump(app, lambda: fl1.childCount() == 1)
    part = fl1.child(0)
    part.setExpanded(True)
    assert pump(app, lambda: part.childCount() == 1)

    def center(item):
        img = item.icon(0).pixmap(16, 16).toImage()
        return img.pixelColor(img.width() // 2, img.height() // 2).name()

    # each disk numbers its own volumes from 0, so this floppy's only volume is colour 0
    assert pump(app, lambda: center(part.child(0)) == volume_color(0).name())


def test_percent_never_rounds_almost_full_up_to_full(app):
    from akaiutil_gui.ui.usage_bar import percent

    assert [percent(x) for x in (0.0, 0.004, 0.5, 0.73, 0.989, 0.998, 1.0)] == [
        "0%", "0.4%", "50%", "73%", "99%", "99.8%", "100%",
    ]


def test_raw_output_toggle_shows_what_akaiutil_printed(app, window, images):
    window.open_images([str(images["fl1"])])
    assert pump(app, lambda: window.table.rowCount() == 2)
    window.table.selectRow(0)
    assert pump(app, lambda: "Sample rate" in window.info.toPlainText())

    window.raw_btn.setChecked(True)
    assert 'ramname: "KICK"' in window.info.toPlainText() and "srate:" in window.info.toPlainText()
    window.raw_btn.setChecked(False)  # and back, without asking akaiutil again
    assert "Sample rate" in window.info.toPlainText() and "srate:" not in window.info.toPlainText()


def test_details_follow_the_theme(app, window, images):
    from akaiutil_gui.ui.details_view import render_html

    window.open_images([str(images["fl1"])])
    assert pump(app, lambda: window.table.rowCount() == 2)
    window.table.selectRow(0)
    assert pump(app, lambda: "Sample rate" in window.info.toPlainText())
    theme.set_theme_preference(settings.THEME_LIGHT)
    html = window.info.toHtml().lower()
    assert theme.LIGHT_PALETTE["text_bright"].lower() in html  # re-rendered with the light palette
    theme.set_theme_preference(settings.THEME_DARK)
    assert theme.DARK_PALETTE["text_bright"].lower() in window.info.toHtml().lower()
    assert "<pre" not in render_html(window.info._details, theme.DARK_PALETTE)  # a sample has no preformatted block


def test_raw_view_really_is_monospaced(app, window, images):
    from PySide6.QtGui import QFont, QFontMetrics

    from akaiutil_gui.ui.details_view import monospace_family

    m = QFontMetrics(QFont(monospace_family()))
    assert m.horizontalAdvance("i") == m.horizontalAdvance("W"), "raw output would lose akaiutil's column alignment"

    window.open_images([str(images["fl1"])])
    assert pump(app, lambda: window.table.rowCount() == 2)
    window.table.selectRow(0)
    assert pump(app, lambda: "Sample rate" in window.info.toPlainText())  # there has to be something to measure
    window.raw_btn.setChecked(True)
    # the font Qt actually applied to the raw text (not just a name in the HTML) is fixed-width
    fragment = window.info.document().begin().begin().fragment()
    applied = QFontMetrics(fragment.charFormat().font())
    assert applied.horizontalAdvance("i") == applied.horizontalAdvance("W")
    assert "disk" in window.info.toPlainText() or "KICK" in window.info.toPlainText()


def test_volume_details_show_files_and_used_space(app, window, images):
    disk = _open_hd(app, window, images)
    part_a = disk.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: part_a.childCount() == 3)
    window.tree.setCurrentItem(part_a.child(0))  # DRUMS: KICK + SNARE, 6 blocks of 8 KB each
    assert pump(app, lambda: "Space used on disk" in window.info.toPlainText())
    text = window.info.toPlainText()
    assert "2 files" in text and "of partition A" in text
    assert "86.5 KB" in text  # KICK + SNARE, 44,292 bytes each...
    assert "96.0 KB" in text  # ...which take 6 blocks of 8 KB each on the disk


def test_slow_volume_details_are_not_lost_when_the_file_list_lands_first(app, window, images):
    """Regression: selecting a volume loads its details and its files at once. The file list landing
    clears the file selection, and that used to cancel the details request still in flight."""
    import time as _time

    disk = _open_hd(app, window, images)
    part_a = disk.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: part_a.childCount() == 3)

    real = window._engine.volume_info

    def slow_volume_info(vol):
        _time.sleep(0.5)  # much slower than listing the files
        return real(vol)

    window._engine.volume_info = slow_volume_info
    window.tree.setCurrentItem(part_a.child(1))  # BASS
    assert pump(app, lambda: window.table.rowCount() == 1)  # the file list is in...
    assert "Volume number" not in window.info.toPlainText()  # ...and the details are not yet
    assert pump(app, lambda: "Volume number" in window.info.toPlainText(), timeout=5)  # but they must still arrive
    assert "BASS" in window.info.toPlainText()

    # and a file's details still win over a volume request that finishes after it
    window._engine.volume_info = slow_volume_info
    window.tree.setCurrentItem(part_a.child(0))  # DRUMS
    assert pump(app, lambda: window.table.rowCount() == 2)
    window.table.selectRow(0)
    assert pump(app, lambda: "Sample rate" in window.info.toPlainText())
    pump(app, lambda: False, 1.0)  # let the slow volume details finish; they must not replace the file's
    assert "Sample rate" in window.info.toPlainText()
    window.table.clearSelection()  # deselecting returns to the volume's own details
    assert pump(app, lambda: "Volume number" in window.info.toPlainText())


# --------------------------------------------------------------- first-run setup
@pytest.fixture
def no_akaiutil_yet(monkeypatch):
    """The app can't find akaiutil until a path has been configured."""
    from pathlib import Path

    from akaiutil_gui.ui import main_window

    monkeypatch.setattr(main_window, "find_akaiutil", lambda configured=None: Path(configured) if configured else None)
    settings.set_akaiutil_path(None)
    yield
    settings.set_akaiutil_path(None)


def test_first_run_downloads_akaiutil_then_carries_on_opening_the_image(app, window, images, binary, tmp_path, monkeypatch, no_akaiutil_yet):
    import shutil

    from akaiutil_gui.engine import fetch

    installed, calls = tmp_path / "bin" / binary.name, []

    def fake_install(dest_dir=None, build_from_source=None, progress=None):
        calls.append(1)
        progress("Compiling akaiutil…")  # reported from the worker thread
        time.sleep(0.1)
        installed.parent.mkdir()
        shutil.copy(binary, installed)
        return installed

    monkeypatch.setattr(fetch, "install", fake_install)
    monkeypatch.setattr(window, "_ask_setup", lambda: "download")
    window.open_images([str(images["fl9"])])
    assert window._setup_running  # the progress dialog is up while it works
    assert pump(app, lambda: window.table.rowCount() == 1)  # ...and then the open the user asked for went ahead
    assert calls == [1] and settings.get_akaiutil_path() == str(installed)  # remembered: it never asks again
    assert not window._setup_running and "ready" in window.statusBar().currentMessage() or window.table.rowCount() == 1


def test_first_run_cancel_does_nothing(app, window, images, monkeypatch, no_akaiutil_yet):
    from akaiutil_gui.engine import fetch

    monkeypatch.setattr(fetch, "install", lambda *a, **k: pytest.fail("must not download without being asked"))
    monkeypatch.setattr(window, "_ask_setup", lambda: "cancel")
    window.open_images([str(images["fl9"])])
    pump(app, lambda: False, 0.3)
    assert window._engine is None and window.stack.currentIndex() == 0 and not window._setup_running


def test_first_run_failure_is_reported_and_leaves_the_app_usable(app, window, images, monkeypatch, no_akaiutil_yet):
    from PySide6.QtWidgets import QMessageBox

    from akaiutil_gui.engine import fetch

    def boom(*a, **k):
        raise fetch.FetchError("No C compiler was found")

    shown = []
    monkeypatch.setattr(fetch, "install", boom)
    monkeypatch.setattr(window, "_ask_setup", lambda: "download")
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: shown.append(text))
    window.open_images([str(images["fl9"])])
    assert pump(app, lambda: shown)
    assert "No C compiler was found" in shown[0] and "get_akaiutil.py" in shown[0]  # and how to recover
    assert window._engine is None and not window._setup_running and settings.get_akaiutil_path() is None


def test_first_run_can_locate_an_existing_akaiutil_instead(app, window, images, binary, monkeypatch, no_akaiutil_yet):
    def locate():
        settings.set_akaiutil_path(str(binary))
        return binary

    monkeypatch.setattr(window, "_ask_setup", lambda: "locate")
    monkeypatch.setattr(window, "locate_binary", locate)
    window.open_images([str(images["fl9"])])
    assert pump(app, lambda: window.table.rowCount() == 1)


def test_the_setup_dialogs_buttons_mean_what_they_say(app, window):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPushButton

    def press_later(label):
        def press():
            box = QApplication.activeModalWidget()
            next(b for b in box.findChildren(QPushButton) if b.text().replace("&", "") == label).click()

        def watchdog():  # never leave a test hanging on a modal dialog
            if QApplication.activeModalWidget() is not None:
                QApplication.activeModalWidget().reject()

        QTimer.singleShot(100, press)
        QTimer.singleShot(5000, watchdog)

    for label, expected in [("Download and set up", "download"), ("Locate existing…", "locate"), ("Cancel", "cancel")]:
        press_later(label)
        assert window._ask_setup() == expected


# ------------------------------------------------------- packaging: notices + smoke test
def test_the_notices_dialog_shows_the_licence_information(app, window):
    from akaiutil_gui.ui.notices_dialog import NoticesDialog

    dialog = NoticesDialog(window)
    text = dialog.view.toPlainText()
    assert "akaiutil 4.6.8" in text and "Klaus Michael Indlekofer" in text and "GNU General Public License" in text
    from akaiutil_gui.ui.about_dialog import AboutDialog

    assert AboutDialog(window).licences_button.text() == "Licences"  # reached from Help > About, as in AKAISDS


def test_smoke_test_passes_and_writes_its_report(app, binary, tmp_path):
    from akaiutil_gui import main as main_module

    report = tmp_path / "report.txt"
    assert main_module.smoke_test(str(report)) == 0
    text = report.read_text()
    for check in ("stylesheets", "svg images", "notices", "bundled akaiutil", "akaiutil round trip", "main window"):
        assert f"ok    {check}" in text, check
    assert text.strip().endswith("SMOKE TEST PASSED")


def test_smoke_test_fails_loudly_when_akaiutil_is_missing(app, tmp_path, monkeypatch):
    from akaiutil_gui import main as main_module

    monkeypatch.setattr(main_module, "find_akaiutil", lambda configured=None: None)
    report = tmp_path / "report.txt"
    assert main_module.smoke_test(str(report)) == 1
    text = report.read_text()
    assert "FAIL  bundled akaiutil: AssertionError: no akaiutil binary found" in text
    assert "FAIL  akaiutil round trip" in text and "ok    stylesheets" in text  # every check still runs
    assert text.strip().endswith("SMOKE TEST FAILED")


def test_the_launcher_runs_the_smoke_test_and_exits_with_its_result(binary, tmp_path):
    """Exactly how CI runs a built app: the real entry point, offscreen, report in a file, exit code."""
    import subprocess
    import sys
    from pathlib import Path

    report = tmp_path / "report.txt"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    for entry in (["-m", "akaiutil_gui"], [str(Path(__file__).resolve().parent.parent / "src" / "main.py")]):
        result = subprocess.run([sys.executable, *entry, f"--smoke-test={report}"], env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SMOKE TEST PASSED" in report.read_text() and "SMOKE TEST PASSED" in result.stdout
        report.unlink()


def test_a_built_apps_smoke_test_demands_the_bundled_akaiutil(app, binary, tmp_path, monkeypatch):
    """Regression: a built app once passed its smoke test using the akaiutil on the machine's PATH,
    hiding the fact that its own bundled copy wasn't being found."""
    from akaiutil_gui import main as main_module
    from akaiutil_gui import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: True)

    # a PATH copy exists (that's `binary`), but nothing is bundled: this must fail
    monkeypatch.setattr(main_module, "bundled_binary", lambda: tmp_path / "bin" / binary.name)
    monkeypatch.setattr(main_module, "find_akaiutil", lambda configured=None: binary)
    report = tmp_path / "missing.txt"
    assert main_module.smoke_test(str(report)) == 1
    assert "no akaiutil is bundled in this app" in report.read_text()

    # bundled and working: passes, and says it used the bundled one
    monkeypatch.setattr(main_module, "bundled_binary", lambda: binary)
    report = tmp_path / "ok.txt"
    assert main_module.smoke_test(str(report)) == 0
    assert f"{binary} (bundled)" in report.read_text()

    # bundled but not executable
    if os.name != "nt":
        stub = tmp_path / "stub"
        stub.write_text("#!/bin/sh\n")
        stub.chmod(0o644)
        monkeypatch.setattr(main_module, "bundled_binary", lambda: stub)
        report = tmp_path / "noexec.txt"
        assert main_module.smoke_test(str(report)) == 1 and "isn't executable" in report.read_text()


# ------------------------------------------------------------------ audio preview, end to end
@pytest.fixture(scope="module")
def loop_image(images, tmp_path_factory):
    """The generated hard disk with real loops written into two samples' headers."""
    import shutil

    import imagegen

    path = tmp_path_factory.mktemp("loops") / "loops.img"
    shutil.copy(images["hd"], path)
    imagegen.add_loop(path, "KICK", loop_end=20000, loop_length=5000, time_ms=None, hold_tune=5)  # Hold
    imagegen.add_loop(path, "SNARE", loop_end=15000, loop_length=1000, time_ms=300, slot=2)  # a timed loop, slot 3
    return path


def open_drums(app, window, image):
    """Open an image and select its first volume (DRUMS: KICK and SNARE)."""
    window.open_images([str(image)])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    disk = window.tree.topLevelItem(0)
    assert pump(app, lambda: disk.childCount() == 18)
    part_a = disk.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: part_a.childCount() == 3)
    window.tree.setCurrentItem(part_a.child(0))
    assert pump(app, lambda: window.table.rowCount() == 2)


def select_sample(app, window, row):
    window.table.selectRow(row)
    assert pump(app, lambda: window.preview.sample is not None and window.preview.sample.name == window.table.item(row, 1).text())


def stream_of(dev, n=256):
    import numpy as np

    out = b""
    while True:
        try:
            chunk = dev.gen.send(n)
        except StopIteration:
            return np.frombuffer(out, dtype="<i2")
        out += chunk


def test_selecting_a_sample_loads_it_and_plays_it_through_the_device(app, window, images, fake_audio):
    import numpy as np

    window.open_images([str(images["fl1"])])  # a floppy: KICK.S1 and SNARE.S1, no loops
    assert pump(app, lambda: window.table.rowCount() == 2)
    assert window.preview.isHidden()  # nothing selected yet
    select_sample(app, window, 0)
    assert not window.preview.isHidden()
    sample = window.preview.sample
    assert (sample.rate, sample.frames, sample.info.generation) == (44100, 22050, "S1000/S3000") and not sample.info.loops
    assert not window.preview.loop_check.isEnabled()

    window.preview.play_button.click()
    dev = fake_audio.opened[-1]
    audio = stream_of(dev)
    assert dev.sample_rate == 44100 and (audio[:22050] == sample.samples).all() and not audio[22050:].any()
    assert np.abs(sample.samples).max() > 5000, "a real sine, not silence"  # the WAV was really decoded


def test_a_sample_with_a_loop_plays_its_loop(app, window, loop_image, fake_audio):
    import numpy as np

    open_drums(app, window, loop_image)
    select_sample(app, window, 0)  # KICK: loop frames 15000..20000, Hold, hold tune +5
    panel = window.preview
    assert panel.loop_check.isEnabled() and panel.loop_check.isChecked() and panel.loop_combo.isHidden()
    assert "loop 1" in panel.info_label.text() and "frames 15,000–20,000" in panel.info_label.text()

    panel.play_button.click()
    dev = fake_audio.opened[-1]
    samples = panel.sample.samples
    head = np.frombuffer(b"".join(dev.gen.send(500) for _ in range(41)), dtype="<i2")  # 20,500 frames
    assert (head[:20001] == samples[:20001]).all()  # the attack, up to the loop's end
    assert panel.player._live_loop_tune_cents == 5 and panel.is_playing()  # Hold: still going
    panel.stop()


def test_a_timed_loop_uses_its_own_dwell(app, window, loop_image, fake_audio):
    open_drums(app, window, loop_image)
    select_sample(app, window, 1)  # SNARE: loop 3, 300 ms
    assert "300 ms" in window.preview.info_label.text()
    window.preview.play_button.click()
    audio = stream_of(fake_audio.opened[-1])  # ends by itself: attack + loop for ~300 ms + tail
    samples = window.preview.sample.samples
    loop_frames = 1001  # [14000, 15000]
    passes = -(-round(0.3 * 44100) // loop_frames)  # whole passes, the last one finished
    assert len(audio) >= 15001 + passes * loop_frames + (len(samples) - 15000)


def test_selecting_several_files_or_a_volume_hides_the_preview_and_silences_it(app, window, loop_image, fake_audio):
    open_drums(app, window, loop_image)
    select_sample(app, window, 0)
    window.preview.play_button.click()
    dev = fake_audio.opened[-1]
    assert window.preview.is_playing()
    window.table.selectAll()  # two files
    assert window.preview.isHidden() and dev.closed and not window.preview.is_playing()
    select_sample(app, window, 0)
    assert not window.preview.isHidden()
    window.tree.setCurrentItem(window.tree.topLevelItem(0).child(0))  # a partition: no files, no preview
    assert window.preview.isHidden() and window.preview.sample is None


def test_closing_the_images_stops_and_hides_the_preview(app, window, loop_image, fake_audio):
    open_drums(app, window, loop_image)
    select_sample(app, window, 0)
    window.preview.play_button.click()
    dev = fake_audio.opened[-1]
    window.close_images()
    assert dev.closed and window.preview.isHidden() and window.preview.sample is None


def test_auto_play_plays_as_soon_as_a_sample_is_selected(app, window, loop_image, fake_audio):
    settings.set_auto_play(True)
    open_drums(app, window, loop_image)
    assert not fake_audio.opened
    window.table.selectRow(0)
    assert pump(app, lambda: fake_audio.opened and window.preview.is_playing())  # nobody pressed Play
    settings.set_auto_play(False)
    window.table.selectRow(1)
    assert pump(app, lambda: window.preview.sample is not None and window.preview.sample.name == "SNARE.S3")
    assert not window.preview.is_playing()  # and with it off, selecting only loads


def test_space_plays_and_stops_from_the_file_list(app, window, loop_image, fake_audio):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    open_drums(app, window, loop_image)
    select_sample(app, window, 0)
    window.activateWindow()
    window.table.setFocus()
    QTest.keyClick(window.table, Qt.Key.Key_Space)
    assert window.preview.is_playing()
    QTest.keyClick(window.table, Qt.Key.Key_Space)
    assert not window.preview.is_playing()


def test_a_sample_is_converted_once_and_replays_from_memory(app, window, loop_image, fake_audio, monkeypatch):
    open_drums(app, window, loop_image)
    calls = []
    real = window._engine.export_sample_wav
    monkeypatch.setattr(window._engine, "export_sample_wav", lambda *a, **k: calls.append(a[1].name) or real(*a, **k))
    select_sample(app, window, 0)
    select_sample(app, window, 1)
    select_sample(app, window, 0)  # back to the first
    assert calls == ["KICK.S3", "SNARE.S3"]


def test_selecting_quickly_never_shows_the_wrong_sample(app, window, loop_image, fake_audio, monkeypatch):
    import time as _time

    open_drums(app, window, loop_image)
    real = window._engine.export_sample_wav

    def slow_for_kick(vol, f, dest):
        if f.name == "KICK.S3":
            _time.sleep(0.6)  # still converting when the user has already moved on
        return real(vol, f, dest)

    monkeypatch.setattr(window._engine, "export_sample_wav", slow_for_kick)
    window.table.selectRow(0)
    window.table.selectRow(1)
    assert pump(app, lambda: window.preview.sample is not None)
    pump(app, lambda: False, 1.0)  # long enough for KICK's slow conversion to finish too
    assert window.preview.sample.name == "SNARE.S3"


def test_a_sample_that_cant_be_converted_says_why(app, window, loop_image, fake_audio, monkeypatch):
    from akaiutil_gui.engine.session import EngineError

    open_drums(app, window, loop_image)

    def broken(*a, **k):
        raise EngineError("akaiutil said no")

    monkeypatch.setattr(window._engine, "export_sample_wav", broken)
    window.table.selectRow(0)
    assert pump(app, lambda: "akaiutil said no" in window.preview.info_label.text())
    assert not window.preview.isHidden() and not window.preview.play_button.isEnabled()


def test_the_converted_wavs_are_cleaned_up(app, window, loop_image, fake_audio):
    import os

    open_drums(app, window, loop_image)
    select_sample(app, window, 0)
    folder = window._preview_dir
    assert folder and os.path.isdir(folder) and os.listdir(folder) == []  # the audio is in memory; the file's gone
    window.close()
    assert not os.path.exists(folder)


def test_the_buffer_size_from_settings_is_used(app, window, loop_image, fake_audio, monkeypatch):
    monkeypatch.setattr("akaiutil_gui.audio.preview.settings.get_audio_buffer_samples", lambda: 256)
    open_drums(app, window, loop_image)
    select_sample(app, window, 0)
    window.preview.play_button.click()
    assert fake_audio.opened[-1].buffersize_msec == round(256 / 44100 * 1000)


# ------------------------------------------------------------------------- search, end to end
@pytest.fixture
def three_disks(app, window, images):
    """The generated hard disk and both floppies, open, with the search index ready.
    Files: disk0 DRUMS KICK.S3 SNARE.S3, BASS BASS.S3, MY SYNTH LEAD.S3; disk1 KICK.S1 SNARE.S1; disk2 KICK.S9."""
    window.open_images([str(images["hd"]), str(images["fl1"]), str(images["fl9"])])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 3 and window._index_ready() and len(window._catalog) == 7)
    return window


def results(window):
    return {(window.table.item(r, 1).text(), window.table.item(r, 6).text()) for r in range(window.table.rowCount())}


def search_for(app, window, text, kind=None):
    window.search.setText(text)
    if kind is not None:
        window.search_kind.setCurrentIndex(window.search_kind.findData(kind))
    assert pump(app, lambda: not window._search_timer.isActive())  # the debounce has fired


def test_search_finds_files_across_every_open_disk(app, three_disks):
    w = three_disks
    assert w.search.isEnabled()
    search_for(app, w, "kick")
    assert results(w) == {("KICK.S3", "disk0 / A / DRUMS"), ("KICK.S1", "disk1 / A / NOT NAMED"), ("KICK.S9", "disk2 / A / VOLUME 001")}
    assert w.files_title.text() == "3 results for “kick”" and not w.table.isColumnHidden(6)
    search_for(app, w, "kick drums")  # the volume's name counts, words in any order
    assert results(w) == {("KICK.S3", "disk0 / A / DRUMS")} and w.files_title.text() == "1 result for “kick drums”"
    search_for(app, w, "nothing matches this")
    assert w.table.rowCount() == 0 and w.files_title.text().startswith("0 results")


def test_the_type_filter_can_browse_everything_of_a_kind_without_a_query(app, three_disks):
    w = three_disks
    search_for(app, w, "", kind=search.SAMPLES)
    assert w.table.rowCount() == 7 and w.files_title.text() == "7 results for samples"
    search_for(app, w, "", kind=search.PROGRAMS)
    assert w.table.rowCount() == 0


def test_clearing_the_search_puts_the_volume_back(app, three_disks):
    w = three_disks
    hd = w.tree.topLevelItem(0)
    part_a = hd.child(0)
    part_a.setExpanded(True)
    assert pump(app, lambda: part_a.childCount() == 3)
    w.tree.setCurrentItem(part_a.child(0))  # DRUMS
    assert pump(app, lambda: w.table.rowCount() == 2 and w._volume_files)
    search_for(app, w, "kick")
    assert w.table.rowCount() == 3
    w.search.clear()
    assert pump(app, lambda: not w._in_results)
    assert w.table.rowCount() == 2 and w.files_title.text() == f"Files in {w._volume.path}" and w.table.isColumnHidden(6)
    assert {w.table.item(r, 1).text() for r in range(2)} == {"KICK.S3", "SNARE.S3"}


def test_a_result_can_be_previewed_inspected_and_exported_like_any_file(app, three_disks, fake_audio):
    w = three_disks
    search_for(app, w, "kick")
    row = next(r for r in range(w.table.rowCount()) if w.table.item(r, 1).text() == "KICK.S1")
    w.table.selectRow(row)
    assert pump(app, lambda: w.preview.sample is not None and w.preview.sample.name == "KICK.S1")  # loads from ITS volume
    assert w.preview.sample.rate == 44100 and not w.preview.isHidden()
    assert pump(app, lambda: "KICK" in w.info.toPlainText() and "S1000 sample" in w.info.toPlainText())
    assert w.export_btn.isEnabled() and w.export_wav_btn.isEnabled()
    w.preview.play_button.click()
    assert fake_audio.opened and w.preview.is_playing()


def test_double_clicking_a_result_shows_it_in_its_volume(app, three_disks):
    w = three_disks
    search_for(app, w, "kick")
    row = next(r for r in range(w.table.rowCount()) if w.table.item(r, 1).text() == "KICK.S9")  # on the other floppy
    w.table.selectRow(row)
    w.table.itemActivated.emit(w.table.item(row, 0))
    assert pump(app, lambda: w._volume is not None and w._volume.disk == 2 and not w._in_results)
    assert w.search.text() == "" and w.files_title.text() == "Files in /disk2/A/VOLUME 001"
    assert pump(app, lambda: w.table.rowCount() == 1 and [f.name for f in w.table.selected_files()] == ["KICK.S9"])
    assert w.tree.currentItem().data(0, 0x100) == w._volume  # and the tree is showing it


def test_revealing_a_result_deep_in_a_hard_disk_expands_the_tree_to_it(app, three_disks):
    w = three_disks
    search_for(app, w, "lead")  # LEAD.S3 in "MY SYNTH", the third volume of partition A
    w.table.selectRow(0)
    w.table.itemActivated.emit(w.table.item(0, 0))
    assert pump(app, lambda: w._volume is not None and w._volume.name == "MY SYNTH" and w.table.rowCount() == 1)
    assert [f.name for f in w.table.selected_files()] == ["LEAD.S3"]


def test_revealing_a_result_in_the_volume_already_open_just_selects_it(app, three_disks):
    w = three_disks
    hd = w.tree.topLevelItem(0)
    hd.child(0).setExpanded(True)
    assert pump(app, lambda: hd.child(0).childCount() == 3)
    w.tree.setCurrentItem(hd.child(0).child(0))  # DRUMS
    assert pump(app, lambda: w.table.rowCount() == 2 and w._volume_files)
    search_for(app, w, "snare drums")
    w.table.selectRow(0)
    w.table.itemActivated.emit(w.table.item(0, 0))
    assert pump(app, lambda: not w._in_results and [f.name for f in w.table.selected_files()] == ["SNARE.S3"])
    assert w.table.rowCount() == 2


def test_picking_something_in_the_tree_ends_the_search(app, three_disks):
    w = three_disks
    search_for(app, w, "kick")
    w.tree.setCurrentItem(w.tree.topLevelItem(1))  # disk1
    assert w.search.text() == "" and w.search_kind.currentData() == search.ALL and not w._in_results


def test_search_waits_for_the_index_instead_of_showing_nothing(app, three_disks, monkeypatch):
    w = three_disks
    monkeypatch.setattr(w, "_index_ready", lambda: False)
    search_for(app, w, "kick")
    assert w.files_title.text() == "Indexing the disks…" and not w._in_results


def test_a_long_result_list_says_it_was_cut(app, three_disks, monkeypatch):
    w = three_disks
    original = search.filter_catalog
    monkeypatch.setattr(search, "filter_catalog", lambda c, q, k=search.ALL, limit=3: original(c, q, k, limit=3))
    search_for(app, w, "", kind=search.SAMPLES)
    assert w.table.rowCount() == 3 and "narrow the search" in w.files_title.text() and "3+ results" in w.files_title.text()


def test_search_is_off_until_a_disk_is_open_and_after_it_closes(app, window, images):
    assert not window.search.isEnabled() and not window.search_kind.isEnabled()
    window.open_images([str(images["fl9"])])
    assert pump(app, lambda: window.search.isEnabled())
    window.search.setText("kick")
    window.close_images()
    assert not window.search.isEnabled() and window.search.text() == "" and window._catalog == []


def test_find_and_escape_shortcuts(app, three_disks):
    from PySide6.QtGui import QKeySequence, QShortcut

    w = three_disks
    assert any(s.key() == QKeySequence(QKeySequence.StandardKey.Find) for s in w.findChildren(QShortcut))
    w._focus_search()
    w.search.setText("kick")
    w.show()
    w._escape_search()
    assert w.search.text() == "" and w.search_kind.currentData() == search.ALL


def test_exporting_results_from_several_volumes_gives_each_its_own_folder(app, three_disks, tmp_path):
    w = three_disks
    settings.set_default_export_folder(str(tmp_path))
    settings.set_ask_export_folder(False)
    search_for(app, w, "kick")
    w.table.selectAll()
    assert pump(app, lambda: w.export_wav_btn.isEnabled())  # export waits for the details load that selecting started
    w.export_wav_btn.click()
    assert pump(app, lambda: "Exported" in w.statusBar().currentMessage())
    found = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.wav"))
    assert found == ["disk0 A - DRUMS/KICK.wav", "disk1 A - NOT NAMED/KICK.wav", "disk2 A - VOLUME 001/KICK.wav"], "same-named files must not collide"
    assert all(p.read_bytes()[:4] == b"RIFF" for p in tmp_path.rglob("*.wav"))
    assert "3 file(s)" in w.statusBar().currentMessage()


def test_exporting_results_from_one_volume_goes_straight_into_the_folder(app, three_disks, tmp_path):
    w = three_disks
    settings.set_default_export_folder(str(tmp_path))
    settings.set_ask_export_folder(False)
    search_for(app, w, "lead")
    w.table.selectAll()
    assert pump(app, lambda: w.export_btn.isEnabled())
    w.export_btn.click()
    assert pump(app, lambda: "Exported" in w.statusBar().currentMessage())
    assert [p.name for p in tmp_path.iterdir()] == ["LEAD.S3"]  # no sub-folder


def test_the_export_folder_name_is_safe(app):
    from akaiutil_gui.engine.models import Volume
    from akaiutil_gui.ui.main_window import export_folder_name

    odd = Volume(3, "B", 1, 'A/B:C*"D', "-", "0x0", "-", "S3000")
    assert export_folder_name(odd, False) == "B - A_B_C__D" and export_folder_name(odd, True) == "disk3 B - A_B_C__D"
    assert export_folder_name(Volume(0, "A", 1, "  ", "-", "0x0", "-", "S3000"), False) == "A -"
