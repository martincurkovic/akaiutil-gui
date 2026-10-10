"""Several disk images at once: adding one, closing one, telling them apart."""

import os
import shutil
import stat
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from akaiutil_gui import settings
from akaiutil_gui.engine.models import Volume
from akaiutil_gui.ui import edit_dialogs, reveal, theme


def pump(app, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def delete_widgets(qt_app):
    yield
    from PySide6.QtCore import QCoreApplication, QEvent

    for widget in qt_app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


@pytest.fixture
def window(qt_app, binary):
    from akaiutil_gui.ui.main_window import MainWindow

    theme.apply_to_app(qt_app, settings.THEME_DARK)
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()


def names(window):
    return [Path(p).name for p in window._images]


def disks_loaded(app, window, count):
    return pump(app, lambda: window.tree.topLevelItemCount() == count and window._pending == 0)


def select_volume(app, window, disk, part, index, files=None):
    window.tree.reveal_volume(Volume(disk, part, index, "", "-", "", "", ""))
    assert pump(app, lambda: window._volume is not None and (window._volume.disk, window._volume.index) == (disk, index))
    if files is not None:
        assert pump(app, lambda: window.table.rowCount() == files)


# ------------------------------------------------------------------ telling them apart
def test_each_disk_row_says_which_file_it_came_from(qt_app, window, images):
    window.open_images([str(images["hd"]), str(images["fl1"])])
    assert disks_loaded(qt_app, window, 2)
    first, second = window.tree.topLevelItem(0), window.tree.topLevelItem(1)
    assert first.text(0).startswith("disk0  ·  s3000_hd.img  ·  S1000/S3000 hard disk")
    assert second.text(0).startswith("disk1  ·  s1000_floppy.img  ·  Floppy")
    assert str(images["hd"].resolve()) in first.toolTip(0) and "partition(s)" in first.toolTip(0)
    assert window.tree.disk_path(1) == str(images["fl1"].resolve())


# ----------------------------------------------------------------------- adding
def test_adding_an_image_keeps_the_others_and_what_you_were_looking_at(qt_app, window, images):
    window.open_images([str(images["hd"])])
    assert disks_loaded(qt_app, window, 1)
    select_volume(qt_app, window, 0, "A", 1, files=2)
    engine_before = window._engine

    window.add_images([str(images["fl1"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2 and window._engine is not engine_before)
    assert names(window) == ["s3000_hd.img", "s1000_floppy.img"]
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "DRUMS" and window.table.rowCount() == 2)
    assert window.tree.current_object().name == "DRUMS"  # same volume, still selected
    assert window.windowTitle() == "s3000_hd.img, s1000_floppy.img — akaiutil-gui"
    assert pump(qt_app, lambda: window._index_ready())  # search covers both


def test_adding_an_image_that_is_already_open_does_nothing(qt_app, window, images):
    window.open_images([str(images["hd"])])
    assert disks_loaded(qt_app, window, 1)
    engine = window._engine
    window.add_images([str(images["hd"]), str(Path(images["hd"]).parent / ".." / images["hd"].parent.name / images["hd"].name)])
    qt_app.processEvents()
    assert window._engine is engine and names(window) == ["s3000_hd.img"]
    assert "already open" in window.statusBar().currentMessage()


def test_adding_with_nothing_open_just_opens(qt_app, window, images):
    window.add_images([str(images["fl1"])])
    assert disks_loaded(qt_app, window, 1) and names(window) == ["s1000_floppy.img"]


def test_adding_several_at_once_skips_repeats_within_the_list(qt_app, window, images):
    window.open_images([str(images["hd"])])
    assert disks_loaded(qt_app, window, 1)
    window.add_images([str(images["fl1"]), str(images["fl9"]), str(images["fl1"])])
    assert disks_loaded(qt_app, window, 3)
    assert names(window) == ["s3000_hd.img", "s1000_floppy.img", "s900_floppy.img"]


def test_dropping_a_file_on_the_window_adds_it(qt_app, window, images):
    window.open_images([str(images["hd"])])
    assert disks_loaded(qt_app, window, 1)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(images["fl9"]))])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)
    assert disks_loaded(qt_app, window, 2) and names(window) == ["s3000_hd.img", "s900_floppy.img"]


def test_editing_stays_on_when_an_image_is_added(qt_app, window, editable_hd, images, monkeypatch):
    monkeypatch.setattr(edit_dialogs, "confirm", lambda *a, **k: True)
    window.open_images([str(editable_hd)])
    assert disks_loaded(qt_app, window, 1)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    window.add_images([str(images["fl1"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2 and window._writable)
    assert window._engine.is_writable and window.mode_label.text() == "Editing" and window.act_allow_edit.isChecked()


def test_editing_goes_off_if_the_added_image_cant_be_written(qt_app, window, editable_hd, images, tmp_path, monkeypatch):
    monkeypatch.setattr(edit_dialogs, "confirm", lambda *a, **k: True)
    locked = tmp_path / "locked.img"
    shutil.copy(images["fl1"], locked)
    locked.chmod(stat.S_IRUSR)
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("this account can write to read-only files")
        window.open_images([str(editable_hd)])
        assert disks_loaded(qt_app, window, 1)
        window.act_allow_edit.trigger()
        assert pump(qt_app, lambda: window._writable)
        window.add_images([str(locked)])
        assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
        assert not window._writable and not window._engine.is_writable and window.mode_label.text() == "Read-only"
        assert "locked.img can't be written to" in window.statusBar().currentMessage()
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_a_new_disk_image_joins_whats_open(qt_app, window, images, tmp_path, monkeypatch):
    from akaiutil_gui.engine import formats
    from akaiutil_gui.ui.main_window import MainWindow

    window.open_images([str(images["hd"])])
    assert disks_loaded(qt_app, window, 1)
    monkeypatch.setattr(MainWindow, "_ask_new_image", lambda self: (formats.by_key("fl1l"), 0, 0))
    monkeypatch.setattr(MainWindow, "_ask_save_path", lambda self, suggestion: str(tmp_path / "fresh.img"))
    window._new_image()
    assert pump(qt_app, lambda: names(window) == ["s3000_hd.img", "fresh.img"] and window.tree.topLevelItemCount() == 2)


# ----------------------------------------------------------------------- closing
@pytest.fixture
def three(qt_app, window, images):
    window.open_images([str(images["hd"]), str(images["fl1"]), str(images["fl9"])])
    assert disks_loaded(qt_app, window, 3)
    return window


def test_closing_one_disk_keeps_the_others_and_renumbers_them(qt_app, three, images):
    window = three
    select_volume(qt_app, window, 2, "A", 1, files=1)  # the S900 floppy's volume, on disk2
    window.close_disk(window.tree.disk(0))
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2 and window._engine.is_open and names(window) == ["s1000_floppy.img", "s900_floppy.img"])
    # the floppy that was disk2 is disk1 now, and still what is selected
    assert pump(qt_app, lambda: window._volume is not None and window._volume.disk == 1 and window.table.rowCount() == 1)
    assert window.table.item(0, 1).text() == "KICK.S9"
    assert window.tree.topLevelItem(1).text(0).startswith("disk1  ·  s900_floppy.img")
    assert window.tree.disk_path(0) == str(images["fl1"].resolve())


def test_closing_the_selected_disk_falls_back_to_the_first(qt_app, three):
    window = three
    window.tree.setCurrentItem(window.tree.topLevelItem(1))
    assert pump(qt_app, lambda: window.tree.current_object() is not None and window.tree.current_object().index == 1)
    window.close_disk(window.tree.disk(1))
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    assert names(window) == ["s3000_hd.img", "s900_floppy.img"]
    assert pump(qt_app, lambda: window.tree.current_object() is not None and window.tree.current_object().index == 0)


def test_closing_the_last_disk_goes_back_to_the_empty_state(qt_app, window, images):
    window.open_images([str(images["fl1"])])
    assert disks_loaded(qt_app, window, 1)
    window.close_disk(window.tree.disk(0))
    assert window._images == [] and window._engine is None
    assert window.stack.currentIndex() == 0 and window.windowTitle() == "akaiutil-gui"


def test_the_menu_item_closes_the_selected_disk(qt_app, three):
    window = three
    window.tree.setCurrentItem(window.tree.topLevelItem(2))
    assert pump(qt_app, lambda: window._selected_disk() is not None and window._selected_disk().index == 2)
    window._update_actions()
    assert window.act_close_disk.isEnabled()
    window.act_close_disk.trigger()
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    assert names(window) == ["s3000_hd.img", "s1000_floppy.img"]


def test_close_disk_is_off_with_nothing_open(qt_app, window):
    window._update_actions()
    assert not window.act_close_disk.isEnabled() and window.act_add.isEnabled()


def test_the_other_disks_selection_survives_closing_a_later_one(qt_app, three):
    window = three
    select_volume(qt_app, window, 0, "A", 3, files=1)  # MY SYNTH on the hard disk
    window.close_disk(window.tree.disk(2))
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "MY SYNTH" and window.table.rowCount() == 1)


# ----------------------------------------------------------------------- the menu
def test_a_disks_context_menu_offers_reveal_close_and_format(qt_app, three):
    window = three
    texts = [a.text() for a in window._tree_menu(window.tree.disk(1)).actions() if not a.isSeparator()]
    assert texts == ["Export Samples as WAV…", reveal.label(), "Close Disk Image", "Format Disk…"]


def test_reveal_goes_to_the_disks_file(qt_app, three, monkeypatch):
    shown = []
    monkeypatch.setattr(reveal, "reveal", lambda path: shown.append(path) or True)
    three._reveal_disk(three.tree.disk(1))
    assert [Path(p).name for p in shown] == ["s1000_floppy.img"]
    monkeypatch.setattr(reveal, "reveal", lambda path: False)
    three._reveal_disk(three.tree.disk(1))
    assert "Couldn't open the folder of s1000_floppy.img" in three.statusBar().currentMessage()


@pytest.mark.parametrize(
    "platform, expected",
    [
        ("darwin", [["open", "-R", "/x/disk.img"]]),
        ("win32", [["explorer", "/select,/x/disk.img"]]),
        ("linux", [["xdg-open", "/x"]]),
    ],
)
def test_reveal_uses_each_systems_file_manager(platform, expected, monkeypatch):
    calls = []
    monkeypatch.setattr(reveal.sys, "platform", platform)
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(reveal.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert reveal.reveal("/x/disk.img") is True
    assert [[str(a) for a in c] for c in calls] == expected
    assert reveal.label() == {"darwin": "Reveal in Finder", "win32": "Show in Explorer", "linux": "Show in File Manager"}[platform]


def test_reveal_falls_back_to_opening_the_folder(monkeypatch):
    opened = []
    monkeypatch.setattr(reveal.sys, "platform", "linux")
    monkeypatch.setattr(reveal.shutil, "which", lambda name: None)
    monkeypatch.setattr(reveal.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    assert reveal.reveal("/x/disk.img") is True and opened == ["/x"]
