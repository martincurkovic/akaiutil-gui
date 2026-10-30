"""A direct-to-disk partition shows its takes in the window (offscreen, real akaiutil, the generated DD image)."""

import os
import shutil
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
import numpy as np
import soundfile
from PySide6.QtWidgets import QApplication, QMessageBox

from akaiutil_gui import settings
from akaiutil_gui.ui import edit_dialogs, theme
from akaiutil_gui.ui.disk_tree import _ROLE_OBJ


@pytest.fixture(scope="module")
def app(tmp_path_factory):
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


def open_dd(app, window, images):
    window.open_images([str(images["dd"])])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    disk = window.tree.topLevelItem(0)
    assert pump(app, lambda: disk.childCount() == 3)
    return disk


def test_selecting_the_dd_partition_swaps_the_file_table_for_the_take_table(app, window, images):
    disk = open_dd(app, window, images)
    assert window.table.isVisible() and not window.takes.isVisible()

    window.tree.setCurrentItem(disk.child(2))  # DD
    assert pump(app, lambda: window.takes.rowCount() == 2)
    assert window.takes.isVisible() and not window.table.isVisible()
    assert [window.takes.item(r, 1).text() for r in range(2)] == ["MONO", "STEREO"]
    assert [window.takes.item(r, 2).text() for r in range(2)] == ["Mono", "Stereo"]
    assert [window.takes.item(r, 4).text() for r in range(2)] == ["1.00 s", "1.00 s"]
    assert window._files_title_text.startswith("Takes in")
    assert pump(app, lambda: "Direct-to-disk area" in window.info.toPlainText())

    window.tree.setCurrentItem(disk.child(0))  # a sampler partition: back to files
    assert window.table.isVisible() and not window.takes.isVisible()
    assert window.takes.rowCount() == 0


def test_a_selected_take_shows_its_settings_and_none_goes_back_to_the_partition(app, window, images):
    disk = open_dd(app, window, images)
    window.tree.setCurrentItem(disk.child(2))
    assert pump(app, lambda: window.takes.rowCount() == 2)

    window.takes.selectRow(1)
    assert pump(app, lambda: "Direct-to-disk take 2" in window.info.toPlainText())
    text = window.info.toPlainText()
    assert "Stereo" in text and "Fade in" in text and "tnr:" not in text  # rows, not akaiutil's raw lines

    window.takes.clearSelection()
    assert pump(app, lambda: "Direct-to-disk area" in window.info.toPlainText())


def test_searching_while_a_dd_partition_is_selected_shows_the_results_table(app, window, images):
    disk = open_dd(app, window, images)
    window.tree.setCurrentItem(disk.child(2))
    assert pump(app, lambda: window.takes.isVisible())

    window.search.setText("mono")
    assert pump(app, lambda: window.table.isVisible() and not window.takes.isVisible())

    window.search.setText("")  # search over: the takes come back
    assert pump(app, lambda: window.takes.isVisible() and not window.table.isVisible())


def test_selected_takes_export_from_the_menu_actions(app, window, images, tmp_path, monkeypatch):
    disk = open_dd(app, window, images)
    window.tree.setCurrentItem(disk.child(2))
    assert pump(app, lambda: window.takes.rowCount() == 2)
    assert not window.act_export.isEnabled() and not window.act_export_wav.isEnabled()  # nothing selected

    window.takes.selectRow(1)
    assert pump(app, lambda: window.act_export.isEnabled() and window.act_export_wav.isEnabled())
    monkeypatch.setattr(type(window), "_export_destination", lambda self: str(tmp_path))
    window.act_export_wav.trigger()
    assert pump(app, lambda: (tmp_path / "STEREO.wav").exists())
    window.act_export.trigger()
    assert pump(app, lambda: (tmp_path / "STEREO.TK").exists())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["STEREO.TK", "STEREO.wav"]  # just the selected take


def test_the_partition_menu_exports_every_take_as_wav(app, window, images, tmp_path, monkeypatch):
    disk = open_dd(app, window, images)
    dd = disk.child(2).data(0, _ROLE_OBJ)  # the Partition the tree item stands for
    menu = window._tree_menu(dd)
    labels = [a.text() for a in menu.actions()]
    assert "Export Takes as WAV…" in labels and "Export Samples as WAV…" not in labels
    monkeypatch.setattr(type(window), "_export_destination", lambda self: str(tmp_path))
    next(a for a in menu.actions() if a.text() == "Export Takes as WAV…").trigger()
    assert pump(app, lambda: sorted(p.name for p in tmp_path.iterdir()) == ["MONO.wav", "STEREO.wav"])


# ------------------------------------------------------------------ editing
@pytest.fixture
def editable(window, app, images, tmp_path, monkeypatch):
    """The window on a copy of the DD image with the DD partition selected, and the questions it asks answered."""
    from akaiutil_gui.ui.main_window import MainWindow

    class Answers:
        confirm = True
        name = None
        asked = []
        warnings = []

    a = Answers()
    a.asked, a.warnings = [], []
    monkeypatch.setattr(edit_dialogs, "confirm", lambda parent, title, text, detail="", action="Delete", details_lines=(), **kw: (a.asked.append((title, text, list(details_lines))), a.confirm)[1])
    monkeypatch.setattr(MainWindow, "_ask_name", lambda self, what, current, vtype, taken, suffix="": (a.asked.append(("name", what, current, list(taken))), a.name)[1])
    monkeypatch.setattr(MainWindow, "_warn", lambda self, title, text, details=None: a.warnings.append((title, text, details)))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kw: a.warnings.append(args[1:3]) or QMessageBox.StandardButton.Ok)
    image = tmp_path / "edit.img"
    shutil.copy(images["dd"], image)
    disk = open_dd(app, window, {"dd": image})
    window.tree.setCurrentItem(disk.child(2))
    assert pump(app, lambda: window.takes.rowCount() == 2)
    a.window, a.tmp = window, tmp_path
    return a


def names(window):
    return [window.takes.item(r, 1).text() for r in range(window.takes.rowCount())]


def audio(path, rate=44100, seconds=0.5, channels=1):
    soundfile.write(str(path), 0.3 * np.sin(np.arange(int(rate * seconds)) * 0.05)[:, None].repeat(channels, axis=1), rate, subtype="PCM_16")
    return path


def test_the_edit_menu_speaks_of_takes_when_takes_are_showing(app, editable):
    w = editable.window
    assert w.act_import.text() == "&Import Takes…" and w.act_import.isEnabled()  # no take needs to be selected to import
    assert not w.act_rename.isEnabled() and not w.act_delete.isEnabled()
    w.takes.selectRow(0)
    assert pump(app, lambda: w.act_rename.text() == "&Rename Take…" and w.act_delete.text() == "&Delete Take…")
    w.takes.selectAll()
    assert pump(app, lambda: w.act_delete.text() == "&Delete 2 Takes…")
    assert not w.act_rename.isEnabled() and w.act_delete.isEnabled()
    w.tree.setCurrentItem(w.tree.topLevelItem(0).child(0))  # a sampler partition: back to files
    assert pump(app, lambda: w.act_import.text() == "&Import Files…")


def test_a_take_is_renamed_from_the_edit_menu(app, editable):
    w = editable.window
    w.takes.selectRow(1)
    assert pump(app, lambda: w.act_rename.isEnabled())
    editable.name = "MY PAD"
    w.act_rename.trigger()
    assert pump(app, lambda: names(w) == ["MONO", "MY PAD"])
    assert ("name", "take", "STEREO", []) in editable.asked  # two takes may share a name, so nothing is "taken"
    assert w.takes.isVisible() and w.mode_label.text() == "Editing"  # still on the DD partition, and editing is on now
    assert pump(app, lambda: "Renamed" in w.statusBar().currentMessage())


def test_takes_are_deleted_after_asking(app, editable):
    w = editable.window
    w.act_allow_edit.trigger()  # (the "no" below is meant for the delete question, so editing goes on first)
    assert pump(app, lambda: w._writable)
    w.takes.selectRow(0)
    assert pump(app, lambda: w.act_delete.isEnabled())
    editable.confirm = False
    w.act_delete.trigger()
    assert names(w) == ["MONO", "STEREO"] and editable.asked[-1][:2] == ("Delete takes?", "Delete “MONO”?")  # said no
    editable.confirm = True
    w.act_delete.trigger()
    assert pump(app, lambda: names(w) == ["STEREO"])
    assert w.takes.item(0, 0).text() == "2"  # the numbers don't move up


def test_audio_and_tk_files_import_as_takes_and_what_cannot_is_reported(app, editable):
    w = editable.window
    good = audio(editable.tmp / "loop one.wav")
    fast = audio(editable.tmp / "FAST.wav", rate=96000, seconds=0.1)
    w.import_takes([good, fast])
    assert pump(app, lambda: names(w) == ["MONO", "STEREO", "LOOP ONE"])
    assert [w.takes.item(2, c).text() for c in (0, 2, 3, 4)] == ["3", "Mono", "44,100 Hz", "500 ms"]
    assert pump(app, lambda: editable.warnings)
    title, text, details = editable.warnings[-1]
    assert title == "Some files weren't imported" and "FAST.wav" in details[0] and "96,000 Hz" in details[0]


def test_files_dropped_on_the_window_become_takes_while_a_dd_partition_is_open(app, editable):
    w = editable.window
    w.handle_drop([str(audio(editable.tmp / "DROPPED.wav", channels=2))])
    assert pump(app, lambda: names(w) == ["MONO", "STEREO", "DROPPED"])
    assert w.takes.item(2, 2).text() == "Stereo"


def test_a_folder_is_searched_for_audio_and_tk_files_only(app, editable):
    w = editable.window
    folder = editable.tmp / "pile"
    folder.mkdir()
    audio(folder / "A ONE.wav")
    (folder / "readme.txt").write_text("not a take")
    w.import_takes([folder])
    assert pump(app, lambda: names(w) == ["MONO", "STEREO", "A ONE"])
    assert editable.warnings == []  # the text file was not an error: it was not looked at
