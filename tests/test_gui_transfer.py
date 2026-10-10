"""Copying and moving files in the window (Copy/Cut/Paste and dragging onto a volume), and dragging files out."""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from akaiutil_gui import settings
from akaiutil_gui.engine.models import Volume
from akaiutil_gui.ui import edit_dialogs, file_drag, theme


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


@pytest.fixture
def answers(monkeypatch):
    class Answers:
        warnings = []

    a = Answers()
    a.warnings = []
    from akaiutil_gui.ui.main_window import MainWindow

    monkeypatch.setattr(edit_dialogs, "confirm", lambda *args, **kw: True)
    monkeypatch.setattr(MainWindow, "_warn", lambda self, title, text, details=None: a.warnings.append((title, text, details)))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kw: QMessageBox.StandardButton.Ok)
    return a


def open_volume(app, window, image, index=1, files=2):
    window.open_images([str(image)])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    select(app, window, index, files)


def select(app, window, index, files):
    window.tree.reveal_volume(Volume(0, "A", index, "", "-", "", "", ""))
    assert pump(app, lambda: window._volume is not None and window._volume.index == index and window.table.rowCount() == files)
    assert pump(app, lambda: window._pending == 0 and window._index_ready())


def names(window):
    return [window.table.item(r, 1).text() for r in range(window.table.rowCount())]


def tree_item(window, name):
    part = window.tree.topLevelItem(0).child(0)
    return next(part.child(i) for i in range(part.childCount()) if part.child(i).text(0) == name)


def internal_drop(window, item, mime, action):
    pos = window.tree.visualItemRect(item).center()
    event = QDropEvent(QPointF(pos), action, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.tree.dropEvent(event)


# ---------------------------------------------------------------------- copy / cut / paste
def test_copy_then_paste_puts_the_files_in_the_selected_volume(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    window.table.selectAll()
    window.act_copy.trigger()
    assert window._clip[0] == "copy" and len(window._clip[1]) == 2
    assert "Copied 2 files" in window.statusBar().currentMessage()
    select(qt_app, window, 2, 1)  # BASS
    window._update_edit_actions()
    assert window.act_paste.isEnabled()
    window.act_paste.trigger()
    assert pump(qt_app, lambda: names(window) == ["BASS.S3", "KICK.S3", "SNARE.S3"])
    assert window._clip is not None  # a copy can be pasted again
    assert "Copied 2 files to “BASS”" in window.statusBar().currentMessage()
    select(qt_app, window, 1, 2)
    assert names(window) == ["KICK.S3", "SNARE.S3"]  # the originals are untouched


def test_cut_then_paste_moves_them_and_empties_the_clipboard(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    window.table.selectRow(0)
    window.act_cut.trigger()
    assert window._clip[0] == "cut" and "Cut 1 file" in window.statusBar().currentMessage()
    select(qt_app, window, 3, 1)  # MY SYNTH
    window.act_paste.trigger()
    assert pump(qt_app, lambda: names(window) == ["LEAD.S3", "KICK.S3"])
    assert window._clip is None
    select(qt_app, window, 1, 1)
    assert names(window) == ["SNARE.S3"]  # gone from where it was


def test_the_menu_items_follow_what_there_is_to_copy_and_paste(qt_app, window, editable_hd, answers):
    window._update_edit_actions()
    assert not (window.act_copy.isEnabled() or window.act_cut.isEnabled() or window.act_paste.isEnabled())
    open_volume(qt_app, window, editable_hd)
    window._update_edit_actions()
    assert not window.act_copy.isEnabled() and not window.act_paste.isEnabled()  # nothing selected / nothing copied
    window.table.selectRow(0)
    window._update_edit_actions()
    assert window.act_copy.isEnabled() and window.act_cut.isEnabled() and not window.act_paste.isEnabled()
    window.act_copy.trigger()
    window._update_edit_actions()
    assert window.act_paste.isEnabled()
    window.tree.setCurrentItem(window.tree.topLevelItem(0))  # a disk isn't somewhere to paste
    assert pump(qt_app, lambda: window._volume is None)
    window._update_edit_actions()
    assert not window.act_paste.isEnabled()


def test_the_context_menus_have_copy_cut_and_paste(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    window.table.selectRow(0)
    file_menu = window._file_menu(window._selected_rows())
    copy = next(a for a in file_menu.actions() if a.text() == "Copy")
    copy.trigger()
    assert window._clip[0] == "copy"
    paste = next(a for a in window._tree_menu(window._volume).actions() if a.text() == "Paste Files")
    assert paste.isEnabled()
    window._clip = None
    assert not next(a for a in window._tree_menu(window._volume).actions() if a.text() == "Paste Files").isEnabled()


def test_pasting_into_the_same_volume_makes_a_numbered_copy(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    window.table.selectRow(0)
    window.act_copy.trigger()
    window.act_paste.trigger()
    assert pump(qt_app, lambda: names(window) == ["KICK.S3", "SNARE.S3", "KICK 2.S3"])


def test_pasting_files_the_volume_cant_hold_says_which_and_why(qt_app, window, editable_hd, images, answers):
    window.open_images([str(editable_hd), str(images["fl9"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    select(qt_app, window, 1, 2)
    window.table.selectRow(0)
    window.act_copy.trigger()
    floppy = Volume(1, "A", 1, "", "-", "", "", "")
    window.tree.reveal_volume(floppy)
    assert pump(qt_app, lambda: window._volume is not None and window._volume.disk == 1)
    window.act_paste.trigger()
    assert pump(qt_app, lambda: bool(answers.warnings))
    title, text, details = answers.warnings[-1]
    assert title == "Some files weren't transferred" and text == "1 of 1 files weren't copied."
    assert details == ["KICK.S3: S3000 files don't belong in an S900 volume"]


# ------------------------------------------------------------------------ dragging in the app
def drag_mime(window):
    window.table.selectAll()
    mime = window._file_drag_mime()
    assert isinstance(mime, file_drag.FileDragData)
    return mime


def test_dropping_dragged_files_on_a_volume_copies_them(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    mime = drag_mime(window)
    internal_drop(window, tree_item(window, "BASS"), mime, Qt.DropAction.CopyAction)
    assert pump(qt_app, lambda: "Copied 2 files to “BASS”" in window.statusBar().currentMessage())
    select(qt_app, window, 2, 3)
    assert names(window) == ["BASS.S3", "KICK.S3", "SNARE.S3"]
    select(qt_app, window, 1, 2)  # still in the source


def test_a_move_drop_takes_them_out_of_the_source(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    window.table.selectRow(1)
    mime = window._file_drag_mime()
    internal_drop(window, tree_item(window, "MY SYNTH"), mime, Qt.DropAction.MoveAction)
    assert pump(qt_app, lambda: names(window) == ["KICK.S3"])  # the list we were looking at lost it
    select(qt_app, window, 3, 2)
    assert names(window) == ["LEAD.S3", "SNARE.S3"]


def test_dropping_dragged_files_somewhere_that_isnt_a_volume_does_nothing(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    mime = drag_mime(window)
    internal_drop(window, window.tree.topLevelItem(0), mime, Qt.DropAction.CopyAction)
    qt_app.processEvents()
    assert "Drop the files on a volume" in window.statusBar().currentMessage()
    assert not window._writable and window._writing == 0


def test_an_internal_drag_is_ignored_by_the_window_itself(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd)
    mime = drag_mime(window)
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)
    qt_app.processEvents()
    assert window._writing == 0 and not window._writable
    assert window.windowTitle().startswith("edit.img")  # (and it didn't try to open the files as disk images)


def test_the_drag_isnt_offered_with_nothing_selected(qt_app, window, editable_hd):
    open_volume(qt_app, window, editable_hd)
    assert window._file_drag_mime() is None
    window.table.startDrag(Qt.DropAction.CopyAction)  # nothing happens, and nothing blocks


# ------------------------------------------------------------------------ dragging files out
def test_samples_are_dragged_out_as_wavs_and_nothing_is_written_until_asked(qt_app, window, editable_hd):
    open_volume(qt_app, window, editable_hd)
    mime = drag_mime(window)
    assert window._drag_dir is None  # lazy: no files yet
    paths = [Path(u.toLocalFile()) for u in mime.urls()]
    assert sorted(p.name for p in paths) == ["KICK.wav", "SNARE.wav"] and all(p.is_file() for p in paths)
    assert mime.urls() and window._drag_dir is not None


def test_option_drags_the_files_out_exactly_as_akai_stores_them(qt_app, window, editable_hd, monkeypatch):
    open_volume(qt_app, window, editable_hd)
    monkeypatch.setattr(QApplication, "keyboardModifiers", staticmethod(lambda: Qt.KeyboardModifier.AltModifier))
    mime = drag_mime(window)
    assert sorted(Path(u.toLocalFile()).name for u in mime.urls()) == ["KICK.S3", "SNARE.S3"]


def test_files_from_several_volumes_are_dragged_out_into_a_folder_each(qt_app, window, editable_hd):
    open_volume(qt_app, window, editable_hd)
    window.search.setText(".s3")
    assert pump(qt_app, lambda: window._in_results and window.table.rowCount() == 4)
    mime = drag_mime(window)
    folders = [Path(u.toLocalFile()) for u in mime.urls()]
    assert len(folders) == 3 and all(f.is_dir() for f in folders)
    assert sorted(f.name for f in folders[0].parent.iterdir()) == sorted(f.name for f in folders)
    assert sorted(p.name for f in folders for p in f.iterdir()) == ["BASS.wav", "KICK.wav", "LEAD.wav", "SNARE.wav"]


def test_non_samples_are_dragged_out_as_they_are(qt_app, window, images):
    window.open_images([str(images["hd"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    select(qt_app, window, 1, 2)
    kick = window._volume_files[0]
    rows = [(window._volume, kick)]
    paths = window._export_for_drag(rows, as_raw=False)
    assert [Path(p).name for p in paths] == ["KICK.wav"]
    paths = window._export_for_drag(rows, as_raw=True)
    assert [Path(p).name for p in paths] == ["KICK.S3"]


def test_the_previous_drags_files_are_cleaned_up_and_so_are_the_last_on_exit(qt_app, window, editable_hd):
    open_volume(qt_app, window, editable_hd)
    window.table.selectRow(0)
    rows = window._selected_rows()
    first = window._export_for_drag(rows, False)
    first_dir = Path(first[0]).parent
    assert first_dir.is_dir()
    second = window._export_for_drag(rows, False)
    assert not first_dir.exists() and Path(second[0]).is_file()
    last = Path(second[0]).parent
    window.close()
    assert not last.exists()
