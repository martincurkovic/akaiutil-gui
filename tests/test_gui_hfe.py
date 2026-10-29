"""HFE images through the window: opened as a decoded working copy, written back after each edit, saved as a new HFE."""

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from akaiutil_gui import hfe
from test_gui_editing import answers, delete_widgets, pump, tree_volumes, window  # noqa: F401  (fixtures)


@pytest.fixture
def floppy_hfe(images, tmp_path):
    """The small S1000 floppy as an HFE file of its own."""
    path = tmp_path / "KICKS.hfe"
    path.write_bytes(hfe.encode(Path(images["fl1"]).read_bytes()))
    return path


def open_hfe(app, window, path):
    window.open_images([str(path)])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1 and bool(tree_volumes(window)))
    assert pump(app, lambda: window._pending == 0)


def test_an_hfe_opens_as_itself_and_akaiutil_is_given_a_decoded_copy(qt_app, window, floppy_hfe, images):
    open_hfe(qt_app, window, floppy_hfe)
    assert window._images == [str(floppy_hfe)] and "KICKS.hfe" in window.windowTitle()
    assert window._disk_files == {0: str(floppy_hfe.resolve())}  # (the tree names the HFE, not the working copy)
    assert window.tree.disk_path(0) == str(floppy_hfe.resolve())
    working = Path(window._hfe.working_path(floppy_hfe))
    assert working != floppy_hfe and working.read_bytes() == Path(images["fl1"]).read_bytes()
    assert window.tree.disk(0).type == "FLH"


def test_an_edit_goes_back_into_the_hfe_straight_away(qt_app, window, floppy_hfe, answers):
    open_hfe(qt_app, window, floppy_hfe)
    before = floppy_hfe.read_bytes()
    answers.name = "NEW NAME"
    volume = window.tree.volumes_in(0, "A")[0]
    window._rename_volume(volume)  # (asks to allow editing first: confirm says yes)
    assert pump(qt_app, lambda: window._writable and "NEW NAME" in tree_volumes(window))
    assert pump(qt_app, lambda: window._writing == 0 and hfe.read(floppy_hfe).image != hfe.decode(before).image)
    after = floppy_hfe.read_bytes()
    assert after[:1024] == before[:1024] and len(after) == len(before)  # header and track list as they were
    assert hfe.read(floppy_hfe).image == Path(window._hfe.working_path(floppy_hfe)).read_bytes()
    assert window._hfe.pending() == []


def test_closing_the_images_removes_the_working_copy_and_keeps_the_hfe(qt_app, window, floppy_hfe):
    open_hfe(qt_app, window, floppy_hfe)
    working = Path(window._hfe.working_path(floppy_hfe))
    assert working.exists()
    window.act_close.trigger()
    assert pump(qt_app, lambda: not working.parent.exists())
    assert floppy_hfe.exists() and not window._images


def test_a_damaged_hfe_is_refused_with_its_reason(qt_app, window, floppy_hfe, answers):
    floppy_hfe.write_bytes(b"HXCHFEV3" + bytes(5000))
    window.open_images([str(floppy_hfe)])
    assert pump(qt_app, lambda: bool(answers.warnings))
    assert "v3" in answers.warnings[-1][1] and not window._images


def test_adding_a_damaged_hfe_leaves_the_open_images_open(qt_app, window, editable_hd, floppy_hfe, answers):
    window.open_images([str(editable_hd)])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    bad = floppy_hfe.parent / "BAD.hfe"
    bad.write_bytes(floppy_hfe.read_bytes()[:20_000])  # (cut short)
    window.add_images([str(bad)])
    assert pump(qt_app, lambda: any(w[0] == "Couldn't open the HFE image" for w in answers.warnings))
    assert window._images == [str(editable_hd)] and window.tree.topLevelItemCount() == 1


def test_a_write_back_that_fails_is_reported_and_kept_until_it_can_be_written(qt_app, window, floppy_hfe, answers):
    open_hfe(qt_app, window, floppy_hfe)
    answers.name = "NEW NAME"
    window._rename_volume(window.tree.volumes_in(0, "A")[0])
    assert pump(qt_app, lambda: window._writable and "NEW NAME" in tree_volumes(window))
    assert pump(qt_app, lambda: window._writing == 0)
    folder = floppy_hfe.parent
    folder.chmod(0o500)
    try:
        if os.access(folder, os.W_OK):
            pytest.skip("this account can write to read-only folders")
        answers.name = "OTHER"
        window._rename_volume(window.tree.volumes_in(0, "A")[0])
        assert pump(qt_app, lambda: any(w[0] == "Couldn't write the HFE file" for w in answers.warnings))
        assert window._hfe.pending() == [str(floppy_hfe)]
    finally:
        folder.chmod(0o700)
    assert window._hfe_written() and window._hfe.pending() == []  # (closing tries again, and now it works)
    assert hfe.read(floppy_hfe).image == Path(window._hfe.working_path(floppy_hfe)).read_bytes()


def test_closing_with_edits_that_cannot_be_written_asks_before_losing_them(qt_app, window, floppy_hfe, answers, monkeypatch):
    open_hfe(qt_app, window, floppy_hfe)
    working = window._hfe.working_path(floppy_hfe)
    data = bytearray(Path(working).read_bytes())
    data[5 * 1024 : 5 * 1024 + 16] = b"changed behind it"[:16]
    Path(working).write_bytes(bytes(data))  # (an edit nobody has written back yet)
    floppy_hfe.parent.chmod(0o500)
    try:
        if os.access(floppy_hfe.parent, os.W_OK):
            pytest.skip("this account can write to read-only folders")
        asked = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: asked.append(a[1:3]) or QMessageBox.StandardButton.Cancel)
        assert not window._hfe_written() and asked and "KICKS.hfe" in asked[-1][1]
        window._close_asked()
        assert window._images  # (still open: the answer was Cancel)
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Discard)
        assert window._hfe_written()
    finally:
        floppy_hfe.parent.chmod(0o700)


def test_save_disk_as_hfe_writes_a_floppy_in_either_kind_of_file(qt_app, window, images, tmp_path, monkeypatch):
    target = tmp_path / "out" / "COPY.hfe"
    target.parent.mkdir()
    monkeypatch.setattr(type(window), "_ask_hfe_path", lambda self, suggestion: (asked.append(suggestion), str(target))[1])
    asked = []
    window.open_images([str(images["fl1"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert pump(qt_app, lambda: window.act_save_hfe.isEnabled())
    window.act_save_hfe.trigger()
    assert pump(qt_app, lambda: target.exists() and "Saved COPY.hfe" in window.statusBar().currentMessage())
    assert Path(asked[0]).name == "s1000_floppy.hfe"
    assert hfe.read(target).image == Path(images["fl1"]).read_bytes()


def test_save_as_hfe_is_for_floppies_only(qt_app, window, images):
    window.open_images([str(images["hd"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    qt_app.processEvents()
    window._update_actions()
    assert not window.act_save_hfe.isEnabled()
