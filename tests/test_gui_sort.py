"""Sorting a volume's files in the window, and sorting after adding files."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile
from PySide6.QtWidgets import QMessageBox

from akaiutil_gui import importing, settings, sorting
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import FileEntry, Volume
from akaiutil_gui.ui import edit_dialogs, theme
from akaiutil_gui.ui.settings_dialog import SettingsDialog


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
        scope = sorting.ALL
        asked = []
        warnings = []

    a = Answers()
    a.asked, a.warnings = [], []
    from akaiutil_gui.ui.main_window import MainWindow

    monkeypatch.setattr(edit_dialogs, "confirm", lambda *args, **kw: True)
    monkeypatch.setattr(MainWindow, "_ask_sort", lambda self, vol, files: (a.asked.append((vol.name, [f.name for f in files])), a.scope)[1])
    monkeypatch.setattr(MainWindow, "_ask_import", lambda self, vol, sources: ("S3000", False, importing.STEREO_SPLIT, importing.ImportOptions()))
    monkeypatch.setattr(MainWindow, "_warn", lambda self, title, text, details=None: a.warnings.append((title, text, details)))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kw: QMessageBox.StandardButton.Ok)
    return a


@pytest.fixture
def scrambled(binary, editable_hd):
    """The editable image with MY SYNTH holding LEAD, ZED, ALPHA, MID 10, MID 2 (in the order they were added)."""
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    try:
        vols = {v.name: v for v in eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])}
        kick = next(f for f in eng.list_files(vols["DRUMS"]) if f.name == "KICK.S3")
        for name in ["ZED.S3", "ALPHA.S3", "MID 10.S3", "MID 2.S3"]:
            eng.copy_file(vols["DRUMS"], kick, vols["MY SYNTH"], name)
    finally:
        eng.close()
    return editable_hd


def open_volume(app, window, image, index, files):
    window.open_images([str(image)])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.reveal_volume(Volume(0, "A", index, "", "-", "", "", ""))
    assert pump(app, lambda: window._volume is not None and window._volume.index == index and window.table.rowCount() == files)
    assert pump(app, lambda: window._pending == 0 and window._index_ready())


def names(window):
    return [window.table.item(r, 1).text() for r in range(window.table.rowCount())]


def test_sort_from_the_menu_puts_the_volume_in_order(qt_app, window, scrambled, answers):
    open_volume(qt_app, window, scrambled, 3, 5)
    assert names(window) == ["LEAD.S3", "ZED.S3", "ALPHA.S3", "MID 10.S3", "MID 2.S3"]
    window._update_edit_actions()
    assert window.act_sort.isEnabled()
    window.act_sort.trigger()
    assert pump(qt_app, lambda: names(window) == ["ALPHA.S3", "LEAD.S3", "MID 2.S3", "MID 10.S3", "ZED.S3"])
    assert answers.asked == [("MY SYNTH", ["LEAD.S3", "ZED.S3", "ALPHA.S3", "MID 10.S3", "MID 2.S3"])]
    assert "Sorted “MY SYNTH”: 4 files moved." in window.statusBar().currentMessage()
    assert window._volume.name == "MY SYNTH"
    assert [window.table.item(r, 0).text() for r in range(5)] == ["1", "2", "3", "4", "5"]


def test_the_volume_context_menu_can_sort_any_volume(qt_app, window, scrambled, answers):
    open_volume(qt_app, window, scrambled, 1, 2)
    synth = next(v for v in window.tree.volumes_in(0, "A") if v.name == "MY SYNTH")
    action = next(a for a in window._tree_menu(synth).actions() if a.text() == "Sort Files by Name…")
    action.trigger()
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "MY SYNTH" and names(window)[:2] == ["ALPHA.S3", "LEAD.S3"])


def test_a_volume_already_in_order_says_so(qt_app, window, editable_hd, answers):
    open_volume(qt_app, window, editable_hd, 1, 2)
    window.act_sort.trigger()
    assert pump(qt_app, lambda: "was already in order" in window.statusBar().currentMessage())
    assert names(window) == ["KICK.S3", "SNARE.S3"]


def test_cancelling_the_dialog_sorts_nothing(qt_app, window, scrambled, answers, monkeypatch):
    from akaiutil_gui.ui.main_window import MainWindow

    open_volume(qt_app, window, scrambled, 3, 5)
    monkeypatch.setattr(MainWindow, "_ask_sort", lambda self, vol, files: None)
    window.act_sort.trigger()
    assert pump(qt_app, lambda: window._writable)
    qt_app.processEvents()
    assert names(window) == ["LEAD.S3", "ZED.S3", "ALPHA.S3", "MID 10.S3", "MID 2.S3"] and window._writing == 0


def test_an_empty_volume_has_nothing_to_sort(qt_app, window, editable_hd, answers):
    window.open_images([str(editable_hd)])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    pump(qt_app, lambda: window._pending == 0)
    part = window._engine.list_partitions(window._engine.list_disks()[0])[0]
    window._engine.set_writable(True)
    new = window._engine.create_volume(part, 9, "EMPTY", "S3000")
    window._writable = True
    window._sort_volume(new)
    assert pump(qt_app, lambda: "has no files to sort" in window.statusBar().currentMessage())


def test_menu_item_is_off_without_a_volume(qt_app, window, editable_hd):
    window._update_edit_actions()
    assert not window.act_sort.isEnabled()
    window.open_images([str(editable_hd)])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert pump(qt_app, lambda: window._volume is None and window.tree.current_object() is not None)
    window._update_edit_actions()
    assert not window.act_sort.isEnabled()


# --------------------------------------------------------------------- sort after adding
def audio_file(folder, name):
    path = folder / name
    t = np.arange(4000) / 44100
    soundfile.write(str(path), 0.3 * np.sin(2 * np.pi * 330 * t), 44100, subtype="PCM_16")
    return path


def test_with_the_setting_on_importing_leaves_the_volume_sorted(qt_app, window, editable_hd, tmp_path, answers):
    settings.set_sort_after_adding(True)
    open_volume(qt_app, window, editable_hd, 1, 2)
    folder = tmp_path / "wavs"
    folder.mkdir()
    window.import_files([audio_file(folder, "snare 2.wav"), audio_file(folder, "aaa.wav")], window._volume)
    assert pump(qt_app, lambda: names(window) == ["AAA.S3", "KICK.S3", "SNARE.S3", "SNARE 2.S3"])


def test_with_the_setting_off_files_stay_in_the_order_added(qt_app, window, editable_hd, tmp_path, answers):
    open_volume(qt_app, window, editable_hd, 1, 2)
    folder = tmp_path / "wavs"
    folder.mkdir()
    window.import_files([audio_file(folder, "aaa.wav")], window._volume)
    assert pump(qt_app, lambda: names(window) == ["KICK.S3", "SNARE.S3", "AAA.S3"])


def test_with_the_setting_on_pasting_sorts_the_target_volume_too(qt_app, window, scrambled, answers):
    settings.set_sort_after_adding(True)
    open_volume(qt_app, window, scrambled, 3, 5)  # (not in order yet: nothing has sorted it)
    drums = next(v for v in window.tree.volumes_in(0, "A") if v.name == "DRUMS")
    kick = window._engine.list_files(drums)[0]
    window.transfer_files([(drums, kick)], window._volume, move=False)
    assert pump(qt_app, lambda: names(window) == ["ALPHA.S3", "KICK.S3", "LEAD.S3", "MID 2.S3", "MID 10.S3", "ZED.S3"])


def test_a_sort_that_fails_after_adding_says_so_and_keeps_the_import(qt_app, window, editable_hd, tmp_path, answers, monkeypatch):
    settings.set_sort_after_adding(True)
    monkeypatch.setattr(AkaiEngine, "sort_files", lambda self, vol, scope=sorting.ALL: (_ for _ in ()).throw(RuntimeError("no room")))
    open_volume(qt_app, window, editable_hd, 1, 2)
    folder = tmp_path / "wavs"
    folder.mkdir()
    window.import_files([audio_file(folder, "aaa.wav")], window._volume)
    assert pump(qt_app, lambda: any(w[0] == "Couldn't sort the volume" for w in answers.warnings))
    assert [w for w in answers.warnings if w[0] == "Couldn't sort the volume"][0][1] == "no room"
    assert pump(qt_app, lambda: names(window) == ["KICK.S3", "SNARE.S3", "AAA.S3"])


def test_samples_only_is_used_after_adding_when_a_volume_has_other_files():
    seen = []

    class FakeEngine:
        def list_files(self, vol):
            return [FileEntry(1, "A.S3", 1, "0x1", "17"), FileEntry(2, "PROG.P3", 1, "0x2", "17")]

        def sort_files(self, vol, scope):
            seen.append(scope)

    from akaiutil_gui.ui.main_window import MainWindow

    assert MainWindow._sort_after(FakeEngine(), object()) == ""
    FakeEngine.list_files = lambda self, vol: [FileEntry(1, "A.S3", 1, "0x1", "17")]
    MainWindow._sort_after(FakeEngine(), object())
    assert seen == [sorting.SAMPLES, sorting.ALL]


# ---------------------------------------------------------------------------- dialogs
def files(*names):
    return [FileEntry(i + 1, n, 1, "0x1", "17") for i, n in enumerate(names)]


def test_the_sort_dialog_defaults_to_samples_only_when_there_are_programs(qt_app):
    vol = Volume(0, "A", 1, "DRUMS", "-", "", "", "S3000")
    d = edit_dialogs.SortDialog(None, vol, files("A.S3", "B.S3", "PROG.P3"))
    assert d.scope == sorting.SAMPLES and d.only_samples.isChecked()
    assert "2 samples" in d.only_samples.text() and "All 3 files" == d.everything.text()
    d.everything.setChecked(True)
    assert d.scope == sorting.ALL


def test_the_sort_dialog_has_no_choice_when_everything_is_a_sample(qt_app):
    vol = Volume(0, "A", 1, "DRUMS", "-", "", "", "S3000")
    d = edit_dialogs.SortDialog(None, vol, files("A.S3", "B.S3"))
    d.show()
    assert d.scope == sorting.ALL and not d.only_samples.isVisible() and not d.everything.isVisible()


def test_the_setting_is_in_the_settings_dialog(qt_app):
    assert settings.get_sort_after_adding() is False
    d = SettingsDialog(None)
    assert not d.check_sort_after.isChecked()
    d.check_sort_after.setChecked(True)
    d.accept()
    assert settings.get_sort_after_adding() is True
    assert SettingsDialog(None).check_sort_after.isChecked()
