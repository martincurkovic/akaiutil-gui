"""HFE images through the window: opened as a decoded working copy, written back after each edit, saved as a new HFE."""

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from akaiutil_gui import floppy_map, hfe
from test_gui_editing import answers, delete_widgets, pump, tree_volumes, vol, window  # noqa: F401  (fixtures)


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


def block_position(block: int, per_track: int = 10) -> tuple[int, int, int]:
    """(track, side, sector) of a block of a floppy."""
    return block // (2 * per_track), (block // per_track) % 2, block % per_track + 1


def first_file_block(binary, images, n=3):
    """A block in the middle of the first file of the small floppy, and that file's name."""
    from akaiutil_gui.engine.api import AkaiEngine

    eng = AkaiEngine(binary)
    try:
        eng.open([images["fl1"]])
        entry = eng.disk_usage(eng.list_disks()[0]).catalog[0].file
    finally:
        eng.close()
    image = Path(images["fl1"]).read_bytes()
    return floppy_map.chain(image, int(entry.start_block, 16), entry.size)[n], entry.name


def damage_sector(data: bytes, track: int, side: int, sector: int, how: str) -> bytes:
    """`data` with one sector damaged: 'flip' (one cell), 'garbage' (a stretch of noise) or 'slip' (a lost cell)."""
    import random

    t = hfe._tracks(data)[track]
    cells = hfe._side_cells(data, t, side)
    start = hfe._find_sectors(cells)[0][sector][1]
    if how == "flip":
        at = start + 2 * 3000 + 1
        cells = cells[:at] + ("1" if cells[at] == "0" else "0") + cells[at + 1 :]
    elif how == "slip":
        at = start + 2 * 3000 + 1
        cells = cells[:at] + cells[at + 1 :] + cells[-1]
    else:
        noise = "".join(random.Random(1).choice("01") for _ in range(600))
        cells = cells[: start + 4000] + noise + cells[start + 4600 :]
    out = bytearray(data)
    hfe._store_side(out, t, side, cells)
    return bytes(out)


class DamagedFile(type(Path())):
    """The path of a damaged HFE file that also knows which block was damaged and which file that block belongs to."""

    block = 0
    file_name = ""


@pytest.fixture
def damaged_hfe(binary, images, tmp_path):
    """The small floppy as an HFE with a sector of its first file damaged (one cell flipped: it can be repaired)."""
    block, name = first_file_block(binary, images)
    path = DamagedFile(tmp_path / "BROKEN.hfe")
    path.write_bytes(damage_sector(hfe.encode(Path(images["fl1"]).read_bytes()), *block_position(block), "flip"))
    path.block, path.file_name = block, name
    return path


def test_a_damaged_hfe_opens_read_only_with_a_warning_naming_the_sectors_and_files(qt_app, window, damaged_hfe, answers):
    open_hfe(qt_app, window, damaged_hfe)
    assert not window._writable and window.tree.disk(0).type == "FLH"
    assert pump(qt_app, lambda: any(w[0] == "Damaged HFE image" for w in answers.warnings))
    title, text, details = next(w for w in answers.warnings if w[0] == "Damaged HFE image")
    assert "BROKEN.hfe" in text and "1 damaged sector," in text and "viewing only" in text and "Save Repaired Copy" in text
    assert f"Affected: {damaged_hfe.file_name}." in text
    track, side, sector = block_position(damaged_hfe.block)
    assert details == [f"BROKEN.hfe: track {track} side {side}, sector {sector}"]


def test_the_warning_is_given_once_not_on_every_reopen(qt_app, window, damaged_hfe, editable_hd, answers):
    open_hfe(qt_app, window, damaged_hfe)
    assert pump(qt_app, lambda: any(w[0] == "Damaged HFE image" for w in answers.warnings))
    window.add_images([str(editable_hd)])  # (restarts akaiutil on both)
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    assert pump(qt_app, lambda: window._pending == 0)
    assert [w[0] for w in answers.warnings].count("Damaged HFE image") == 1


def test_editing_stays_off_while_a_damaged_hfe_is_open(qt_app, window, damaged_hfe, answers):
    open_hfe(qt_app, window, damaged_hfe)
    window.act_allow_edit.trigger()
    assert not window._writable and not window.act_allow_edit.isChecked()
    assert any(w[0] == "Can't edit this image" and "BROKEN.hfe" in w[1] for w in answers.warnings)
    window.open_images([str(damaged_hfe)], writable=True)  # (a reopen that asks for editing, as adding an image does)
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1) and not window._writable and not window._engine.is_writable


def test_a_damaged_hfe_cant_be_saved_as_another_hfe(qt_app, window, damaged_hfe, answers, tmp_path, monkeypatch):
    open_hfe(qt_app, window, damaged_hfe)
    target = tmp_path / "copy.hfe"
    monkeypatch.setattr(type(window), "_ask_hfe_path", lambda self, suggestion: str(target))
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert pump(qt_app, lambda: window.act_save_hfe.isEnabled())
    window.act_save_hfe.trigger()
    assert pump(qt_app, lambda: any("couldn't be read" in w[1] for w in answers.warnings if w[0] == "Couldn't save the HFE file"))
    assert not target.exists()


# ------------------------------------------------------------------------------------------------------------- low density
def test_a_low_density_hfe_opens_edits_and_saves_a_copy(qt_app, window, images, answers, tmp_path, monkeypatch):
    flat = Path(images["fl9"]).read_bytes()
    path = tmp_path / "S900.hfe"
    path.write_bytes(hfe.encode(flat))
    open_hfe(qt_app, window, path)
    assert window.tree.disk(0).type == "FLL"
    before = path.read_bytes()
    window.tree.reveal_volume(vol(1))  # (an S900 floppy's volume can't be renamed, so rename a file in it)
    assert pump(qt_app, lambda: window._volume is not None and any(f.name == "KICK.S9" for f in window._volume_files))
    answers.name = "thump"
    window._rename_file(window._volume, next(f for f in window._volume_files if f.name == "KICK.S9"))
    assert pump(qt_app, lambda: window._writable and "thump.S9" in [window.table.item(r, 1).text() for r in range(window.table.rowCount())])  # (S900 names keep their case)
    assert pump(qt_app, lambda: window._writing == 0 and hfe.read(path).image != flat)
    assert path.read_bytes()[:1024] == before[:1024] and len(path.read_bytes()) == len(before)
    target = tmp_path / "copy.hfe"
    monkeypatch.setattr(type(window), "_ask_hfe_path", lambda self, suggestion: str(target))
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert pump(qt_app, lambda: window.act_save_hfe.isEnabled())
    window.act_save_hfe.trigger()
    assert pump(qt_app, lambda: target.exists() and "Saved copy.hfe" in window.statusBar().currentMessage())
    assert hfe.read(target).image == hfe.read(path).image


# ------------------------------------------------------------------------------------------------------ repairing a copy
@pytest.fixture
def repair_answers(monkeypatch, window, answers):
    """Stand-ins for the repair questions and the summary; each test says what is answered."""

    class Repairing:
        rewrite = False
        path = None
        open_copy = False
        asked, results = [], []

    r = Repairing()
    r.asked, r.results = [], []
    monkeypatch.setattr(type(window), "_ask_repair", lambda self, name: (r.asked.append(name), r.rewrite)[1])
    monkeypatch.setattr(type(window), "_ask_repair_path", lambda self, suggestion: (r.asked.append(suggestion), str(r.path))[1])
    monkeypatch.setattr(type(window), "_show_repair_result", lambda self, path, rep, files: (r.results.append((path, rep, files)), r.open_copy)[1])
    return r


def open_selected(qt_app, window, path):
    open_hfe(qt_app, window, path)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert pump(qt_app, lambda: window._pending == 0)
    window._update_actions()


def test_save_repaired_copy_is_only_for_a_damaged_hfe(qt_app, window, floppy_hfe, damaged_hfe, images, answers):
    open_selected(qt_app, window, floppy_hfe)
    assert not window.act_repair.isEnabled()
    window.open_images([str(images["fl1"])])  # (a flat image)
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1 and window._pending == 0)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    window._update_actions()
    assert not window.act_repair.isEnabled()
    open_selected(qt_app, window, damaged_hfe)
    assert window.act_repair.isEnabled()


def test_a_repaired_copy_is_saved_and_the_original_is_left_alone(qt_app, window, damaged_hfe, images, answers, repair_answers, tmp_path):
    open_selected(qt_app, window, damaged_hfe)
    original = damaged_hfe.read_bytes()
    repair_answers.path = tmp_path / "FIXED.hfe"
    window.act_repair.trigger()
    assert pump(qt_app, lambda: bool(repair_answers.results))
    assert damaged_hfe.read_bytes() == original  # (never touched)
    path, repaired, files = repair_answers.results[0]
    assert [f.result for f in repaired.fixes] == [hfe.FIXED] and files == []
    assert hfe.read(repair_answers.path).image == Path(images["fl1"]).read_bytes()  # (the copy is the good disk again)
    assert repair_answers.asked[0] == "BROKEN.hfe" and repair_answers.asked[1].endswith("BROKEN (repaired).hfe")


def test_what_cant_be_fixed_is_left_alone_unless_the_user_asks_to_rewrite_it(qt_app, window, binary, images, answers, repair_answers, tmp_path):
    block, name = first_file_block(binary, images)
    broken = tmp_path / "GARBLED.hfe"
    broken.write_bytes(damage_sector(hfe.encode(Path(images["fl1"]).read_bytes()), *block_position(block), "garbage"))
    open_selected(qt_app, window, broken)

    repair_answers.path = tmp_path / "LEFT.hfe"
    window.act_repair.trigger()
    assert pump(qt_app, lambda: bool(repair_answers.results))
    _, repaired, files = repair_answers.results[0]
    assert [f.result for f in repaired.fixes] == [hfe.LOST] and files == [name]  # (the file it belongs to is named)
    assert len(hfe.read(repair_answers.path, strict=False).missing) == 1

    repair_answers.rewrite, repair_answers.path = True, tmp_path / "REWRITTEN.hfe"
    window.act_repair.trigger()
    assert pump(qt_app, lambda: len(repair_answers.results) == 2)
    assert [f.result for f in repair_answers.results[1][1].fixes] == [hfe.REWRITTEN]
    assert hfe.read(repair_answers.path).problems == []  # (it reads without an error now)


def test_the_repaired_copy_can_be_opened_straight_away(qt_app, window, damaged_hfe, answers, repair_answers, tmp_path):
    open_selected(qt_app, window, damaged_hfe)
    repair_answers.path, repair_answers.open_copy = tmp_path / "FIXED.hfe", True
    window.act_repair.trigger()
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2 and window._pending == 0)
    assert [Path(p).name for p in window._images] == ["BROKEN.hfe", "FIXED.hfe"]
    assert list(window._hfe.damaged()) == [str(damaged_hfe.resolve())]  # (only the original is still damaged)


def test_cancelling_a_question_saves_nothing(qt_app, window, damaged_hfe, answers, repair_answers, tmp_path, monkeypatch):
    open_selected(qt_app, window, damaged_hfe)
    monkeypatch.setattr(type(window), "_ask_repair", lambda self, name: None)
    window.act_repair.trigger()
    repair_answers.path = None
    monkeypatch.setattr(type(window), "_ask_repair", lambda self, name: False)
    monkeypatch.setattr(type(window), "_ask_repair_path", lambda self, suggestion: None)
    window.act_repair.trigger()
    qt_app.processEvents()
    assert repair_answers.results == [] and [p.name for p in tmp_path.iterdir()] == ["BROKEN.hfe"]


def test_the_repair_question_says_what_rewriting_costs_and_leaves_it_off(qt_app):
    from PySide6.QtWidgets import QLabel

    from akaiutil_gui.ui import edit_dialogs

    dialog = edit_dialogs.RepairDialog(None, "BROKEN.hfe")
    labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert not dialog.rewrite and "may hold wrong data" in labels and "original isn't changed" in labels
    dialog.rewrite_box.setChecked(True)
    assert dialog.rewrite
    dialog.deleteLater()


def test_the_summary_is_short_and_plain(qt_app, window, monkeypatch):
    shown = []
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append((self.text(), self.detailedText())) or 0)
    fixes = [hfe.Fix(1, 0, 2, hfe.FIXED, "bit"), hfe.Fix(1, 0, 3, hfe.FIXED_PROBABLY), hfe.Fix(2, 1, 4, hfe.LOST), hfe.Fix(3, 0, 5, hfe.REWRITTEN)]
    assert window._show_repair_result("/x/COPY.hfe", hfe.Repair(b"", fixes), ["KICK.S3"]) is False
    text, details = shown[0]
    assert text.splitlines()[0] == "Saved COPY.hfe." and "Fixed 2 of 4 sectors." in text
    assert "1 of them is probably right" in text and "1 sector couldn't be fixed and still won't read." in text
    assert "1 sector couldn't be fixed and was rewritten. It may hold wrong data." in text and "Still affected: KICK.S3." in text
    assert details.splitlines()[0] == "Track 1, side 0, sector 2: fixed"
    shown.clear()
    window._show_repair_result("/x/COPY.hfe", hfe.Repair(b"", fixes[:1] + fixes[1:2]), [])
    assert "Fixed all 2 sectors." in shown[0][0]
