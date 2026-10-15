"""Importing audio and Akai files through the window: dropping them, the dialog, and what is left on screen."""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from akaiutil_gui import importing, settings
from akaiutil_gui.engine.models import Volume
from akaiutil_gui.ui import edit_dialogs, theme


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
        confirm = True
        import_answer = ("S3000", False, importing.STEREO_SPLIT, importing.ImportOptions())
        asked = []
        warnings = []
        picked = []

    a = Answers()
    a.asked, a.warnings, a.picked = [], [], []
    from akaiutil_gui.ui.main_window import MainWindow

    monkeypatch.setattr(edit_dialogs, "confirm", lambda *args, **kw: a.asked.append(("confirm", args[2])) or a.confirm)
    monkeypatch.setattr(MainWindow, "_ask_import", lambda self, vol, sources: (a.asked.append(("import", vol.name, [s.path.name for s in sources])), a.import_answer)[1])
    monkeypatch.setattr(MainWindow, "_ask_import_files", lambda self: a.picked)
    monkeypatch.setattr(MainWindow, "_warn", lambda self, title, text, details=None: a.warnings.append((title, text, details)))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kw: a.warnings.append(("info", args[2])) or QMessageBox.StandardButton.Ok)
    return a


def tone(rate=44100, seconds=0.3, channels=1):
    t = np.arange(int(rate * seconds)) / rate
    wave = 0.5 * np.sin(2 * np.pi * 330 * t)
    return wave if channels == 1 else np.stack([wave, wave * 0.5], 1)


@pytest.fixture
def audio(tmp_path):
    folder = tmp_path / "samples"
    folder.mkdir()
    return folder


def make(path, data, rate=44100, subtype="PCM_16"):
    soundfile.write(str(path), data, rate, subtype=subtype)
    return path


def open_volume(app, window, image, index=1, files=2):
    window.open_images([str(image)])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.reveal_volume(Volume(0, "A", index, "", "-", "", "", ""))
    assert pump(app, lambda: window._volume is not None and window._volume.index == index and window.table.rowCount() == files)
    assert pump(app, lambda: window._pending == 0 and window._index_ready())


def drop(window, paths, at=QPointF(10, 10)):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    window.dropEvent(QDropEvent(at, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def names(window):
    return [window.table.item(r, 1).text() for r in range(window.table.rowCount())]


# ------------------------------------------------------------------------- dropping
def test_dropped_audio_is_converted_and_added_in_alphabetical_order(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd)
    paths = [
        make(audio / "snare 10.flac", tone(48000), 48000, "PCM_24"),
        make(audio / "Snare 2.wav", tone()),
        make(audio / "hat.aiff", tone()),
    ]
    drop(window, paths)
    assert pump(qt_app, lambda: window._writable and window.table.rowCount() == 5)
    assert [a for a in answers.asked if a[0] == "import"] == [("import", "DRUMS", ["hat.aiff", "Snare 2.wav", "snare 10.flac"])]
    assert pump(qt_app, lambda: names(window) == ["KICK.S3", "SNARE.S3", "HAT.S3", "SNARE 2.S3", "SNARE 10.S3"])
    assert "Imported 3 samples into “DRUMS”" in window.statusBar().currentMessage()
    assert window._volume.name == "DRUMS"  # still looking at it
    assert pump(qt_app, lambda: window._index_ready() and any(e.file.name == "HAT.S3" for e in window._catalog))  # and search knows


def test_what_was_chosen_in_the_dialog_is_what_is_made(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd)
    answers.import_answer = ("S900", True, importing.STEREO_MIX, importing.ImportOptions())
    drop(window, [make(audio / "pad.wav", tone(channels=2))])
    assert pump(qt_app, lambda: "PAD.S9C" in names(window))
    assert "PAD.S9C" in names(window) and not any("-L" in n for n in names(window))


def test_cancelling_the_dialog_imports_nothing(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd)
    answers.import_answer = None
    drop(window, [make(audio / "x.wav", tone())])
    qt_app.processEvents()
    assert pump(qt_app, lambda: window._writable)  # (editing was switched on to get as far as the question)
    assert names(window) == ["KICK.S3", "SNARE.S3"] and window._writing == 0


def test_a_failure_is_reported_and_the_rest_still_import(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd)
    junk = audio / "b broken.wav"
    junk.write_bytes(b"not audio")
    drop(window, [make(audio / "a good.wav", tone()), junk])
    assert pump(qt_app, lambda: "A GOOD.S3" in names(window) and answers.warnings)
    title, text, details = answers.warnings[-1]
    assert title == "Some files weren't imported" and text == "1 of 2 files couldn't be imported."
    assert details and "b broken.wav: can't read it as audio" in details[0]
    assert "Imported 1 sample into" in window.statusBar().currentMessage() and "1 failed" in window.statusBar().currentMessage()


def test_akai_files_are_imported_as_they_are(qt_app, window, editable_hd, tmp_path, answers):
    open_volume(qt_app, window, editable_hd)
    kick = window._volume_files[0]
    exported = tmp_path / "raw"
    exported.mkdir()
    window._engine.export_files(window._volume, [kick], exported)
    window.tree.reveal_volume(Volume(0, "A", 2, "", "-", "", "", ""))
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "BASS" and window.table.rowCount() == 1)
    drop(window, [exported / "KICK.S3"])
    assert pump(qt_app, lambda: names(window) == ["BASS.S3", "KICK.S3"])


def test_a_folder_can_be_dropped(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd)
    make(audio / "b.wav", tone())
    make(audio / "a.wav", tone())
    (audio / "readme.txt").write_text("not for the sampler")
    drop(window, [audio])
    assert pump(qt_app, lambda: names(window) == ["KICK.S3", "SNARE.S3", "A.S3", "B.S3"])
    assert [a[2] for a in answers.asked if a[0] == "import"] == [["a.wav", "b.wav"]]  # the .txt in the folder never reaches the dialog


def test_disk_images_are_still_opened_by_dropping_them(qt_app, window, images, tmp_path, answers):
    drop(window, [images["fl1"]])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    drop(window, [images["fl9"]])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    assert not [a for a in answers.asked if a[0] == "import"]


def test_samples_dropped_with_a_disk_image_are_imported_and_the_image_ignored(qt_app, window, editable_hd, images, audio, answers):
    open_volume(qt_app, window, editable_hd)
    drop(window, [images["fl9"], make(audio / "s.wav", tone())])
    assert pump(qt_app, lambda: "S.S3" in names(window))
    assert window.tree.topLevelItemCount() == 1
    assert [a[2] for a in answers.asked if a[0] == "import"] == [["s.wav"]]


# ---------------------------------------------------------- where the files go
def test_dropping_with_no_volume_selected_says_what_to_do(qt_app, window, editable_hd, audio, answers):
    window.open_images([str(editable_hd)])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert pump(qt_app, lambda: window.tree.current_object() is not None and window._volume is None)
    drop(window, [make(audio / "x.wav", tone())])
    assert answers.warnings and answers.warnings[-1][0] == "info" and "Select the volume" in answers.warnings[-1][1]
    assert not window._writable


def test_dropping_with_nothing_open_says_so(qt_app, window, audio, answers):
    window.import_files([make(audio / "x.wav", tone())])
    assert "Open a disk image first" in window.statusBar().currentMessage()


def test_dropping_on_a_volume_in_the_tree_imports_into_that_volume_and_selects_it(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd, index=1, files=2)
    bass = next(v for v in window.tree.volumes_in(0, "A") if v.name == "BASS")
    window.tree.filesDropped.emit(bass, [str(make(audio / "to bass.wav", tone()))])
    assert pump(qt_app, lambda: any(a[0] == "import" for a in answers.asked))
    assert [a[1] for a in answers.asked if a[0] == "import"] == ["BASS"]
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "BASS" and "TO BASS.S3" in names(window))


def test_a_drop_on_a_tree_row_reaches_the_window(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd)
    item = next(window.tree.topLevelItem(0).child(0).child(i) for i in range(3) if window.tree.topLevelItem(0).child(0).child(i).text(0) == "MY SYNTH")
    pos = window.tree.visualItemRect(item).center()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(make(audio / "lead 2.wav", tone())))])
    window.tree.dropEvent(QDropEvent(QPointF(pos), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert pump(qt_app, lambda: any(a[0] == "import" for a in answers.asked))
    assert [a[1] for a in answers.asked if a[0] == "import"] == ["MY SYNTH"]
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "MY SYNTH" and "LEAD 2.S3" in names(window))


def test_the_tree_marks_the_volume_a_drop_would_go_to(qt_app, window, editable_hd):
    open_volume(qt_app, window, editable_hd)
    part = window.tree.topLevelItem(0).child(0)
    bass = next(part.child(i) for i in range(3) if part.child(i).text(0) == "BASS")
    window.tree._highlight(bass)
    assert bass.background(0).style() != Qt.BrushStyle.NoBrush
    window.tree._highlight(None)
    assert bass.background(0).style() == Qt.BrushStyle.NoBrush
    window.tree._highlight(part)  # a partition isn't a place to import into: only volumes light up
    window.tree._highlight(None)


def test_the_import_menu_item_follows_the_selection(qt_app, window, editable_hd, audio, answers):
    window._update_actions()
    assert not window.act_import.isEnabled()
    open_volume(qt_app, window, editable_hd)
    window._update_actions()
    assert window.act_import.isEnabled()
    answers.picked = [str(make(audio / "picked.wav", tone()))]
    window.act_import.trigger()
    assert pump(qt_app, lambda: "PICKED.S3" in names(window))


def test_the_volume_context_menu_offers_import(qt_app, window, editable_hd, audio, answers):
    open_volume(qt_app, window, editable_hd)
    menu = window._tree_menu(window._volume)
    importer = next(a for a in menu.actions() if a.text() == "Import Files…")
    answers.picked = [str(make(audio / "via menu.wav", tone()))]
    importer.trigger()
    assert pump(qt_app, lambda: "VIA MENU.S3" in names(window))


# --------------------------------------------------------------- the dialog itself
def vol(vtype="S3000"):
    return Volume(0, "A", 1, "DRUMS", "-", "", "", vtype)


def test_the_import_dialog_lists_each_file_and_what_will_happen_to_it(qt_app, audio):
    sources = [
        importing.inspect(make(audio / "a.flac", tone(48000, channels=2), 48000, "PCM_24")),
        importing.inspect(make(audio / "b.wav", tone())),
    ]
    d = edit_dialogs.ImportDialog(None, vol(), sources)
    lines = [d.files.item(i).text() for i in range(d.files.count())]
    assert lines[0] == "a.flac   —   FLAC · 48 kHz · 24-bit · stereo  →  converted to WAV, converted to 16-bit, resampled from 48 to 44.1 kHz"
    assert lines[1] == "b.wav   —   WAV · 44.1 kHz · 16-bit · mono  →  used as it is"
    assert [d.type_combo.itemText(i) for i in range(d.type_combo.count())] == ["S3000", "S1000", "S900"]
    assert d.sample_type == "S3000" and d.stereo == importing.STEREO_SPLIT and d.importable == 2
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_the_import_dialog_follows_the_type_and_stereo_choices(qt_app, audio):
    sources = [importing.inspect(make(audio / "s.wav", tone(channels=2)))]
    d = edit_dialogs.ImportDialog(None, vol(), sources)
    d.show()
    assert not d.compress.isVisible() and d.stereo_row[1].isVisible()
    d.type_combo.setCurrentIndex(d.type_combo.findText("S900"))
    assert d.compress.isVisible() and not d.compress_s900
    d.compress.setChecked(True)
    assert d.compress_s900
    assert "resampled from 44.1 to 40 kHz" in d.files.item(0).text()  # an S900 can't play 44.1 kHz
    d.type_combo.setCurrentIndex(d.type_combo.findText("S1000"))
    assert not d.compress_s900  # (only an S900 sample can be compressed)
    d.stereo_combo.setCurrentIndex(1)
    assert d.stereo == importing.STEREO_MIX and "mixed to mono" in d.files.item(0).text()


def test_the_dialog_only_offers_what_the_volume_can_hold(qt_app, audio):
    sources = [importing.inspect(make(audio / "m.wav", tone()))]
    s900 = edit_dialogs.ImportDialog(None, vol("S900"), sources)
    assert [s900.type_combo.itemText(i) for i in range(s900.type_combo.count())] == ["S900"] and not s900.type_combo.isEnabled()
    s1000 = edit_dialogs.ImportDialog(None, vol("S1000"), sources)
    assert [s1000.type_combo.itemText(i) for i in range(s1000.type_combo.count())] == ["S1000"]


def test_the_stereo_choice_only_appears_for_stereo_files(qt_app, audio):
    d = edit_dialogs.ImportDialog(None, vol(), [importing.inspect(make(audio / "m.wav", tone()))])
    d.show()
    assert not d.stereo_row[1].isVisible()


def test_akai_only_drops_have_no_conversion_choices(qt_app, audio):
    akai = audio / "KICK.S3"
    akai.write_bytes(b"\x03" + bytes(300))
    d = edit_dialogs.ImportDialog(None, vol(), [importing.inspect(akai)])
    d.show()
    assert not d.type_combo.isVisible() and not d.stereo_row[1].isVisible()
    assert d.files.item(0).text() == "KICK.S3   —   Akai file, imported as it is"
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_the_dialog_offers_add_anyway_only_for_files_it_cant_check(qt_app, audio):
    wav = importing.inspect(make(audio / "ok.wav", tone()))
    d = edit_dialogs.ImportDialog(None, vol(), [wav])
    d.show()
    assert not d.add_anyway.isVisible() and not d.options.add_anyway
    image = audio / "S2K PATCH.C"
    image.write_bytes(bytes(1000))
    d = edit_dialogs.ImportDialog(None, vol(), [importing.inspect(image)])
    d.show()
    assert d.add_anyway.isVisible() and not d.add_anyway.isChecked()
    assert d.importable == 0 and not d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert d.files.item(0).text().endswith("(tick “Add anyway” to import it as it is)")


def test_ticking_add_anyway_makes_the_file_importable(qt_app, audio):
    image = audio / "S2K PATCH.C"
    image.write_bytes(bytes(1000))
    junk = audio / "notes.txt"
    junk.write_bytes(b"hello")
    d = edit_dialogs.ImportDialog(None, vol(), [importing.inspect(image), importing.inspect(junk)])
    d.show()
    d.add_anyway.setChecked(True)
    assert d.options.add_anyway and d.importable == 1  # (the text file has no Akai type to go in as)
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert d.files.item(0).text() == "S2K PATCH.C   —   added anyway: an Akai file, imported as it is (not checked)"
    assert d.files.item(1).text().startswith("notes.txt   —   skipped: can't read it as audio")
    assert "Add anyway" not in d.files.item(1).text()


def test_add_anyway_is_not_remembered_for_the_next_import(qt_app, audio):
    image = audio / "S2K PATCH.C"
    image.write_bytes(bytes(1000))
    sources = [importing.inspect(image)]
    d = edit_dialogs.ImportDialog(None, vol(), sources)
    d.add_anyway.setChecked(True)
    d.accept()
    assert not edit_dialogs.ImportDialog(None, vol(), sources).add_anyway.isChecked()


def test_with_nothing_importable_the_button_is_off(qt_app, audio):
    junk = audio / "x.wav"
    junk.write_bytes(b"nope")
    d = edit_dialogs.ImportDialog(None, vol(), [importing.inspect(junk)])
    assert d.importable == 0 and not d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert d.files.item(0).text().startswith("x.wav   —   skipped: can't read it as audio")
