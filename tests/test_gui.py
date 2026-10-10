"""Drives the real window (offscreen) against the real binary and generated images."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from akaiutil_gui import settings
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
