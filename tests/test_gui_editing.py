"""Editing through the window: Allow Editing, rename, delete, new volume. Offscreen, real akaiutil, a copy of a small image."""

import os
import stat
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from akaiutil_gui import settings
from akaiutil_gui.engine import formats
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
    """Delete every widget a test made (dialogs and fields it never gave a parent): left to Python's exit, Qt can
    destroy a parent before its children and crash the interpreter on the way out."""
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


def vol(index, name="", part="A"):
    """A Volume with just enough in it to find the real one in the tree."""
    return Volume(0, part, index, name, "-", "0x0000", "17.00", "S3000")


def tree_volumes(window, part="A"):
    return [v.name for v in window.tree.volumes_in(0, part)]


def open_and_show(app, window, image, volume_index=1, volume_name="DRUMS", files=2):
    window.open_images([str(image)])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.reveal_volume(vol(volume_index))
    assert pump(app, lambda: window._volume is not None and window._volume.index == volume_index and window.table.rowCount() == files)
    assert window._volume.name == volume_name
    assert pump(app, lambda: window._pending == 0 and window._index_ready())


@pytest.fixture
def answers(monkeypatch):
    """Stand-ins for the questions the window asks; each test sets what is answered, and can see what was asked."""

    class Answers:
        confirm = True
        name = None
        new_volume = None
        copy_volume = None
        revert = None
        sample_settings = None
        format = None
        new_image = None
        save_path = None
        asked = []
        warnings = []

    a = Answers()
    a.asked, a.warnings = [], []

    def confirm(parent, title, text, detail="", action="Delete", details_lines=(), other_action=None, default_is_action=False):
        a.asked.append((title, text, detail, action, list(details_lines)))
        return a.confirm

    monkeypatch.setattr(edit_dialogs, "confirm", confirm)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kw: a.warnings.append(args[1:3]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kw: a.warnings.append(args[1:3]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kw: a.warnings.append(args[1:3]) or QMessageBox.StandardButton.Ok)
    from akaiutil_gui.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_ask_name", lambda self, what, current, vtype, taken, suffix="": (a.asked.append(("name", what, current, list(taken), suffix)), a.name)[1])
    monkeypatch.setattr(MainWindow, "_ask_new_volume", lambda self, *args: (a.asked.append(("new", args)), a.new_volume)[1])
    monkeypatch.setattr(MainWindow, "_ask_copy_volume", lambda self, vol, count, need, targets: (a.asked.append(("copy", vol.name, count, [t.label for t in targets])), a.copy_volume(targets) if callable(a.copy_volume) else None)[1])
    monkeypatch.setattr(MainWindow, "_ask_revert", lambda self, entries: (a.asked.append(("revert", [(p.name, b.created) for p, b in entries])), a.revert(entries) if callable(a.revert) else None)[1])
    monkeypatch.setattr(MainWindow, "_ask_sample_settings", lambda self, f, current, audio=None: (a.asked.append(("settings", f.name, None if audio is None else len(audio))), a.sample_settings(current) if callable(a.sample_settings) else None)[1])
    monkeypatch.setattr(MainWindow, "_ask_format", lambda self, disk: (a.asked.append(("format", disk.index, disk.type)), a.format)[1])
    monkeypatch.setattr(MainWindow, "_ask_new_image", lambda self: (a.asked.append(("newimage",)), a.new_image)[1])
    monkeypatch.setattr(MainWindow, "_ask_save_path", lambda self, suggestion: (a.asked.append(("save", suggestion)), a.save_path)[1])
    monkeypatch.setattr(MainWindow, "_warn", lambda self, title, text, details=None: a.warnings.append((title, text, details)))
    return a


# ------------------------------------------------------------------- the mode
def test_images_open_read_only_and_the_edit_menu_says_so(qt_app, window, editable_hd):
    open_and_show(qt_app, window, editable_hd)
    assert not window._writable
    assert window.mode_label.text() == "Read-only"
    assert not window.act_allow_edit.isChecked() and window.act_allow_edit.isEnabled()
    assert window.act_new_volume.isEnabled()  # asking to is how you switch editing on
    assert not window.act_rename.isEnabled() or window.act_rename.text() == "&Rename Volume…"


def test_allow_editing_asks_first_and_then_switches_the_mode(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.confirm = False
    window.act_allow_edit.trigger()
    assert not window._writable and not window.act_allow_edit.isChecked()
    assert answers.asked[-1][0] == "Allow editing?" and answers.asked[-1][3] == "Back Up and Edit"
    assert answers.asked[-1][4] == [f"edit.img  ·  {window._images and __import__('pathlib').Path(window._images[0]).stat().st_size / 2**20:.1f} MB"]

    answers.confirm = True
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    assert window.act_allow_edit.isChecked() and window.mode_label.text() == "Editing"
    assert window.mode_label.property("editing") is True
    assert window._engine.is_writable

    window.act_allow_edit.trigger()  # untick: no question needed to make things safer
    assert pump(qt_app, lambda: not window._writable)
    assert window.mode_label.text() == "Read-only" and not window._engine.is_writable


def test_the_view_survives_switching_editing_on(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    assert window._volume.name == "DRUMS" and window.table.rowCount() == 2  # nothing was reloaded or lost
    assert window.tree.current_object().name == "DRUMS"


def test_every_image_opens_read_only_again(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    window.open_images([str(editable_hd)])
    assert not window._writable and window.mode_label.text() == "Read-only"
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    assert not window._engine.is_writable
    window.close_images()
    assert not window._writable and not window.act_allow_edit.isEnabled()


def test_a_file_that_cant_be_written_is_named_and_editing_stays_off(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    editable_hd.chmod(stat.S_IRUSR)
    try:
        if os.access(editable_hd, os.W_OK):
            pytest.skip("this account can write to read-only files")
        window.act_allow_edit.trigger()
        assert not window._writable and not window.act_allow_edit.isChecked()
        assert answers.warnings and "edit.img" in answers.warnings[-1][1]
        assert not any(a[0] == "Allow editing?" for a in answers.asked)  # no point asking
    finally:
        editable_hd.chmod(stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------- volumes
def test_renaming_a_volume_from_read_only_asks_then_does_it(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.name = "KICK KIT"
    window._rename_volume(window._volume)
    assert pump(qt_app, lambda: window._writable and "KICK KIT" in tree_volumes(window))
    assert [a[0] for a in answers.asked][0] == "Allow editing?"
    assert tree_volumes(window) == ["KICK KIT", "BASS", "MY SYNTH"]
    # still looking at the same volume, now under its new name, files and all
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "KICK KIT" and window.table.rowCount() == 2)
    assert window.tree.currentItem().text(0) == "KICK KIT"
    assert window.files_title.text() == "Files in /disk0/A/KICK KIT"
    assert "Renamed “DRUMS” to “KICK KIT”" in window.statusBar().currentMessage()


def test_the_rename_question_knows_the_other_names_in_the_partition(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.name = None  # cancelled
    window._rename_volume(window._volume)
    assert pump(qt_app, lambda: any(a[0] == "name" for a in answers.asked))
    kind, what, current, taken, suffix = next(a for a in answers.asked if a[0] == "name")
    assert (what, current, sorted(taken), suffix) == ("volume", "DRUMS", ["BASS", "MY SYNTH"], "")
    assert tree_volumes(window) == ["DRUMS", "BASS", "MY SYNTH"]  # cancelled: nothing happened


def test_a_refused_rename_says_why_and_leaves_the_disk_as_it_was(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    answers.name = "BASS"  # the dialog would have stopped this; the engine must too
    window._rename_volume(window._volume)
    assert pump(qt_app, lambda: bool(answers.warnings))
    assert "already a volume called “BASS”" in answers.warnings[-1][1]
    assert pump(qt_app, lambda: tree_volumes(window) == ["DRUMS", "BASS", "MY SYNTH"])  # reloaded, as it was
    assert pump(qt_app, lambda: window._volume is not None and window.table.rowCount() == 2)


def test_deleting_a_volume_asks_how_many_files_go_with_it(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window._delete_volume(window._volume)
    assert pump(qt_app, lambda: "DRUMS" not in tree_volumes(window) and tree_volumes(window))
    title, text, detail, action, _ = next(a for a in answers.asked if a[0] == "Delete volume?")
    assert text == "Delete the volume “DRUMS”?" and "2 files" in detail and action == "Delete"
    assert tree_volumes(window) == ["BASS", "MY SYNTH"]
    # nothing is left selected that doesn't exist: the partition it was in is
    assert pump(qt_app, lambda: window._volume is None and window.tree.current_object() is not None)
    assert window.tree.current_object().name == "A"
    assert window.table.rowCount() == 0


def test_saying_no_to_a_delete_changes_nothing(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    answers.confirm = False
    window._delete_volume(window._volume)
    qt_app.processEvents()
    assert tree_volumes(window) == ["DRUMS", "BASS", "MY SYNTH"] and window._pending == 0


def test_a_new_volume_appears_selected_in_the_tree(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    part = window.tree.partition(0, "A")
    answers.new_volume = ("FRESH KIT", 5, "S3000", 9)
    window._new_volume(part)
    assert pump(qt_app, lambda: "FRESH KIT" in tree_volumes(window))
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "FRESH KIT")
    assert window._volume.index == 5 and window._volume.load_number == "009"
    assert pump(qt_app, lambda: window.table.rowCount() == 0 and window._pending == 0)
    assert tree_volumes(window) == ["DRUMS", "BASS", "MY SYNTH", "FRESH KIT"]
    used, limit, types, default, taken = next(a for a in answers.asked if a[0] == "new")[1][1:6]
    assert (used, limit, types, default, sorted(taken)) == ([1, 2, 3], 100, ["S3000", "S1000", "CD3000"], "S3000", ["BASS", "DRUMS", "MY SYNTH"])


def test_the_suggested_volume_type_is_what_the_partition_mostly_holds(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    part = window.tree.partition(0, "A")
    assert window._volume_types(part) == (["S3000", "S1000", "CD3000"], "S3000")
    assert window._volume_types(window.tree.partition(0, "B")) == (["S3000", "S1000", "CD3000"], "S3000")  # empty: the usual


def test_new_volume_is_only_offered_where_a_volume_can_go(qt_app, window, images, editable_hd, answers):
    window.open_images([str(editable_hd), str(images["fl1"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    assert pump(qt_app, lambda: window.tree.partition(0, "A") is not None and window.tree.partition(1, "A") is not None)
    hd_part, floppy_part = window.tree.partition(0, "A"), window.tree.partition(1, "A")
    assert window._can_hold_volumes(hd_part) and not window._can_hold_volumes(floppy_part)
    names = lambda menu: [a.text() for a in menu.actions() if not a.isSeparator()]  # noqa: E731
    assert "New Volume…" in names(window._tree_menu(hd_part))
    assert "New Volume…" not in names(window._tree_menu(floppy_part))
    assert [a.text() for a in window._tree_menu(window.tree.disk(0)).actions() if not a.isSeparator()][-1] == "Format Disk…"


# ----------------------------------------------------------------------- files
def test_renaming_a_file_keeps_it_selected_under_its_new_name(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    kick = next(f for f in window._volume_files if f.name == "KICK.S3")
    answers.name = "thump"
    window._rename_file(window._volume, kick)
    assert pump(qt_app, lambda: window._writable and "THUMP.S3" in [window.table.item(r, 1).text() for r in range(window.table.rowCount())])
    asked = next(a for a in answers.asked if a[0] == "name")
    assert asked[1:] == ("file", "KICK", ["SNARE"], ".S3")  # the type isn't part of what's typed; other names of that type are taken
    assert pump(qt_app, lambda: [f.name for _, f in window.table.selected_rows()] == ["THUMP.S3"])
    assert pump(qt_app, lambda: 'THUMP' in window.info.toPlainText())  # its details were read again, not left from before
    assert "Renamed “KICK.S3” to “THUMP.S3”" in window.statusBar().currentMessage()


def test_deleting_selected_files_asks_once_for_all_of_them(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.table.selectAll()
    rows = window._selected_rows()
    window._delete_files(rows)
    assert pump(qt_app, lambda: window.table.rowCount() == 0 and window._writable)
    title, text, detail, action, lines = next(a for a in answers.asked if a[0] == "Delete files?")
    assert text == "Delete 2 files from DRUMS?" and sorted(lines) == ["DRUMS: KICK.S3", "DRUMS: SNARE.S3"]
    assert "Deleted 2 files." in window.statusBar().currentMessage()
    # the volume is still there, empty, and the space it used is free again
    assert tree_volumes(window) == ["DRUMS", "BASS", "MY SYNTH"]
    assert pump(qt_app, lambda: window._index_ready() and not [e for e in window._catalog if e.volume.name == "DRUMS"])


def test_deleting_one_file_names_it(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    snare = next(f for f in window._volume_files if f.name == "SNARE.S3")
    window._delete_files([(window._volume, snare)])
    assert pump(qt_app, lambda: window._writable and window.table.rowCount() == 1)
    assert next(a for a in answers.asked if a[0] == "Delete files?")[1] == "Delete “SNARE.S3” from DRUMS?"
    assert window.table.item(0, 1).text() == "KICK.S3"


def test_the_usage_bar_and_tree_notice_the_space_that_deleting_freed(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    free_before = window.tree.partition(0, "A").free_blocks
    window._delete_volume(window._volume)
    assert pump(qt_app, lambda: window.tree.partition(0, "A") is not None and window.tree.partition(0, "A").free_blocks > free_before)
    assert pump(qt_app, lambda: window.tree.disk(0).free_blocks > 0 and window._index_ready())


# ----------------------------------------------------------------------- search
def test_editing_a_search_result_keeps_the_search_showing(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.search.setText("kick")
    assert pump(qt_app, lambda: window._in_results and window.table.rowCount() == 1)
    v, f = window._selected_rows()[0] if window._selected_rows() else (None, None)
    window.table.selectRow(0)
    (v, f), = window._selected_rows()
    answers.name = "thump"
    window._rename_file(v, f)
    assert pump(qt_app, lambda: window._writable and window._in_results and window.table.rowCount() == 0)  # "kick" no longer matches
    assert window.search.text() == "kick"
    window.search.setText("thump")
    assert pump(qt_app, lambda: window._in_results and window.table.rowCount() == 1 and window.table.item(0, 1).text() == "THUMP.S3")
    window.search.clear()
    assert pump(qt_app, lambda: not window._in_results and window.table.rowCount() == 2)
    assert sorted(window.table.item(r, 1).text() for r in range(2)) == ["SNARE.S3", "THUMP.S3"]


def test_deleting_search_results_from_several_volumes(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.search.setText(".s3")
    assert pump(qt_app, lambda: window._in_results and window.table.rowCount() == 4)
    window.table.selectAll()
    window._delete_files(window._selected_rows())
    assert pump(qt_app, lambda: window._writable and window._in_results and window.table.rowCount() == 0)
    assert next(a for a in answers.asked if a[0] == "Delete files?")[1] == "Delete 4 files from 3 volumes?"
    window.search.clear()
    assert pump(qt_app, lambda: not window._in_results)
    assert window.table.rowCount() == 0 and tree_volumes(window) == ["DRUMS", "BASS", "MY SYNTH"]


# ----------------------------------------------------------------- menus & keys
def test_the_edit_menu_says_what_rename_and_delete_would_act_on(qt_app, window, editable_hd, answers, monkeypatch):
    open_and_show(qt_app, window, editable_hd)

    monkeypatch.setattr(type(window), "_focus_in", lambda self, widget: widget is self.tree)
    window._update_edit_actions()
    assert (window.act_rename.text(), window.act_delete.text()) == ("&Rename Volume…", "&Delete Volume…")
    assert window.act_rename.isEnabled() and window.act_delete.isEnabled()

    monkeypatch.setattr(type(window), "_focus_in", lambda self, widget: widget is self.table)
    window._update_edit_actions()
    assert not window.act_rename.isEnabled() and not window.act_delete.isEnabled()  # nothing selected in the list
    window.table.selectRow(0)
    window._update_edit_actions()
    assert (window.act_rename.text(), window.act_delete.text()) == ("&Rename File…", "&Delete File…")
    window.table.selectAll()
    window._update_edit_actions()
    assert window.act_rename.text() == "&Rename…" and not window.act_rename.isEnabled()  # which of two?
    assert window.act_delete.text() == "&Delete 2 Files…" and window.act_delete.isEnabled()


def test_the_edit_menu_is_off_without_an_image(qt_app, window):
    window._update_edit_actions()
    assert not any(a.isEnabled() for a in (window.act_rename, window.act_delete, window.act_new_volume, window.act_allow_edit))


def test_context_menus_offer_what_fits_what_was_clicked(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    texts = lambda menu: [a.text().replace("&", "") for a in menu.actions() if not a.isSeparator()]  # noqa: E731
    assert texts(window._tree_menu(window._volume)) == ["Import Files…", "Paste Files", "Sort Files by Name…", "Select Unused Samples", "Export Samples as WAV…", "Copy Volume To…", "Rename…", "Delete…"]
    assert texts(window._tree_menu(window.tree.partition(0, "A"))) == ["New Volume…", "Export Samples as WAV…"]

    window.table.selectRow(0)
    rows = window._selected_rows()
    menu = window._file_menu(rows)
    assert texts(menu) == ["Export…", "Export as WAV…", "Copy", "Cut", "Rename…", "Sample Settings…", "Delete…"]
    window.table.selectAll()
    menu = window._file_menu(window._selected_rows())
    rename = next(a for a in menu.actions() if a.text() == "Rename…")
    assert not rename.isEnabled()
    assert "Delete 2 Files…" in texts(menu)


def test_edit_keys_do_nothing_when_there_is_nothing_to_act_on(qt_app, window, editable_hd, answers, monkeypatch):
    window._rename_focused()  # no image open
    window._delete_focused()
    open_and_show(qt_app, window, editable_hd)
    monkeypatch.setattr(type(window), "_focus_in", lambda self, widget: widget is self.table)
    window._delete_focused()  # focus in the list, nothing selected there
    qt_app.processEvents()
    assert not answers.asked and not window._writable


def test_the_edit_keys_act_on_the_focused_pane(qt_app, window, editable_hd, answers, monkeypatch):
    open_and_show(qt_app, window, editable_hd)
    answers.name = None
    monkeypatch.setattr(type(window), "_focus_in", lambda self, widget: widget is self.table)
    window.table.selectRow(0)
    window._rename_focused()
    assert pump(qt_app, lambda: any(a[0] == "name" for a in answers.asked))
    assert next(a for a in answers.asked if a[0] == "name")[1] == "file"

    answers.asked.clear()
    monkeypatch.setattr(type(window), "_focus_in", lambda self, widget: widget is self.tree)
    window._rename_focused()
    assert pump(qt_app, lambda: any(a[0] == "name" for a in answers.asked))
    assert next(a for a in answers.asked if a[0] == "name")[1] == "volume"


def test_the_f2_and_delete_keys_are_wired_to_both_panes(qt_app, window, editable_hd):
    from PySide6.QtGui import QKeySequence, QShortcut

    for widget in (window.table, window.tree):
        keys = {s.key().toString() for s in widget.findChildren(QShortcut)}
        assert {"F2", "Del", "Backspace"} <= keys


# ----------------------------------------------------------------- the dialogs
def ok_enabled(dialog):
    return dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_the_rename_dialog_only_accepts_a_real_change_to_a_good_unused_name(qt_app):
    d = edit_dialogs.RenameDialog(None, "volume", "DRUMS", "S3000", taken=["BASS"])
    assert d.edit.text() == "DRUMS" and not ok_enabled(d)  # unchanged
    d.edit.setText("drum kit")
    assert ok_enabled(d) and d.name == "DRUM KIT" and d.problem.text() == ""
    d.edit.setText("bass")
    assert not ok_enabled(d) and "already one called “BASS”" in d.problem.text()
    d.edit.setText("A/B")
    assert not ok_enabled(d) and "Not allowed" in d.problem.text()
    d.edit.setText("")
    assert not ok_enabled(d)
    d.edit.setText("THIS IS TOO LONG")  # (typing can't get here: the field cuts at 12; this is the dialog's own check)
    assert not ok_enabled(d) and "At most 12" in d.problem.text()


def test_the_rename_dialog_shows_a_files_type_next_to_the_box(qt_app):
    from PySide6.QtWidgets import QLabel

    d = edit_dialogs.RenameDialog(None, "file", "KICK", "S3000", suffix=".S3")
    assert ".S3" in [lab.text() for lab in d.findChildren(QLabel)]
    d.edit.setText("snare")
    assert d.name == "SNARE" and ok_enabled(d)


def test_the_new_volume_dialog_suggests_the_first_free_number_and_wants_a_name(qt_app):
    d = edit_dialogs.NewVolumeDialog(None, "A", used=[1, 2, 4], limit=100, types=["S3000", "S1000", "CD3000"], default_type="S1000", taken=["DRUMS"])
    assert d.volume_number == 3 and d.volume_type == "S1000" and d.load_number is None
    assert not ok_enabled(d)  # no name yet
    d.name_edit.setText("pads")
    assert ok_enabled(d) and d.name == "PADS"
    d.name_edit.setText("drums")
    assert not ok_enabled(d) and "already a volume called “DRUMS”" in d.problem.text()
    d.name_edit.setText("pads")
    d.number.setValue(4)
    assert not ok_enabled(d) and "Volume number 4 is already used" in d.problem.text()
    d.number.setValue(5)
    d.load.setValue(12)
    assert ok_enabled(d) and d.load_number == 12
    d.load.setValue(0)
    assert d.load_number is None and d.load.text() == "Off"


def test_the_new_volume_dialog_for_s900_has_only_one_type_and_a_shorter_name(qt_app):
    d = edit_dialogs.NewVolumeDialog(None, "A", used=[], limit=10, types=["S900"], default_type="S900")
    assert not d.type_combo.isEnabled() and d.volume_type == "S900"
    d.name_edit.setText("ELEVEN CHRS")
    assert not ok_enabled(d) and "At most 10" in d.problem.text()


def test_a_full_partition_has_no_free_number_to_suggest(qt_app):
    d = edit_dialogs.NewVolumeDialog(None, "A", used=[1, 2], limit=2, types=["S3000"], default_type="S3000")
    d.name_edit.setText("one more")
    assert not ok_enabled(d)  # the only number is taken


def test_the_confirm_question_defaults_to_cancel_and_names_the_action(qt_app, monkeypatch):
    seen = {}

    def fake_exec(self):
        seen["default"] = self.defaultButton().text()
        seen["escape"] = self.escapeButton().text()
        seen["buttons"] = sorted(b.text() for b in self.buttons() if b.text() != "Show Details...")
        seen["detail"] = self.detailedText()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert edit_dialogs.confirm(None, "Delete files?", "Delete 2 files?", "No undo.", details_lines=["a", "b"]) is False
    assert seen["default"] == "Cancel" and seen["escape"] == "Cancel"
    assert seen["buttons"] == ["Cancel", "Delete"] and seen["detail"] == "a\nb"


# ------------------------------------------------------- formatting and new images
def test_formatting_a_disk_erases_it_after_saying_what_will_be_lost(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert pump(qt_app, lambda: window.tree.current_object() is not None and window._pending == 0)
    answers.format = (formats.by_key("hd1"), 10)
    window._format_disk()
    assert pump(qt_app, lambda: window._writable and window.tree.topLevelItem(0).childCount() == 2)
    title, text, detail, action, _ = next(a for a in answers.asked if a[0] == "Format disk?")
    assert text == "Erase everything on “edit.img” and format it as S1000 hard disk?"
    assert "4 files in 3 volumes" in detail and "can't be undone" in detail and action == "Erase and Format"
    assert next(a for a in answers.asked if a[0] == "format")[1:] == (0, "HD")
    top = window.tree.topLevelItem(0)
    assert [top.child(i).text(0).split("  ·")[0] for i in range(top.childCount())] == ["A", "B"]
    assert window.tree.current_object().index == 0 and "S1000 hard disk" in window.statusBar().currentMessage()
    assert pump(qt_app, lambda: window._index_ready() and window._catalog == [])
    assert pump(qt_app, lambda: window.table.rowCount() == 0)


def test_saying_no_to_a_format_changes_nothing(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    pump(qt_app, lambda: window._pending == 0)
    answers.format = (formats.by_key("hd3"), 60)
    answers.confirm = False
    window._format_disk()
    qt_app.processEvents()
    assert window.tree.topLevelItem(0).childCount() == 4
    assert len(window._engine.list_partitions(window._engine.list_disks()[0])) == 4  # and the disk itself is as it was


def test_formatting_a_blank_image_gives_it_a_filesystem(qt_app, window, tmp_path, answers):
    blank = tmp_path / "blank.img"
    blank.write_bytes(bytes(8192 * 2500))
    window.open_images([str(blank)])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    assert "no Akai filesystem found" in window.tree.topLevelItem(0).text(0)
    answers.format = (formats.by_key("hd3"), 5)
    window._format_disk(window.tree.disk(0))
    assert pump(qt_app, lambda: window._writable and window.tree.topLevelItem(0).childCount() == 4)
    assert pump(qt_app, lambda: "no Akai filesystem found" not in window.tree.topLevelItem(0).text(0))
    top = window.tree.topLevelItem(0)
    assert [top.child(i).text(0).split("  ·")[0] for i in range(4)] == ["A", "B", "C", "D"]


def test_format_disk_is_in_the_edit_menu_and_the_disks_context_menu(qt_app, window, editable_hd, answers):
    window._update_edit_actions()
    assert not window.act_format.isEnabled()  # nothing open
    open_and_show(qt_app, window, editable_hd)
    window._update_edit_actions()
    assert window.act_format.isEnabled()  # any selection on a disk will do
    assert window._selected_disk().index == 0


def test_a_new_image_is_created_and_opened(qt_app, window, tmp_path, answers):
    target = tmp_path / "brand new.img"
    answers.new_image = (formats.by_key("hd3"), 10, 5)
    answers.save_path = str(target)
    window._new_image()
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1 and window._images == [str(target)])
    assert target.stat().st_size == 10 * formats.MB
    assert pump(qt_app, lambda: window.tree.topLevelItem(0).childCount() == 2)
    assert window.windowTitle().startswith("brand new.img")
    assert next(a for a in answers.asked if a[0] == "save")[1].endswith("S3000 hard disk.img")
    assert not window._writable  # it opens like any other image


def test_a_new_image_joins_the_ones_already_open(qt_app, window, editable_hd, tmp_path, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.new_image = (formats.by_key("fl1h"), 0, 0)
    answers.save_path = str(tmp_path / "floppy")  # no extension typed: .img is added
    window._new_image()
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2)
    assert [__import__("pathlib").Path(p).name for p in window._images] == ["edit.img", "floppy.img"]
    assert (tmp_path / "floppy.img").stat().st_size == 1600 * 1024


def test_cancelling_either_question_creates_nothing(qt_app, window, tmp_path, answers):
    answers.new_image = None
    window._new_image()
    answers.new_image = (formats.by_key("hd3"), 10, 5)
    answers.save_path = None
    window._new_image()
    qt_app.processEvents()
    assert list(tmp_path.iterdir()) == [] and window.tree.topLevelItemCount() == 0


def test_a_new_image_cant_replace_one_that_is_open(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.new_image = (formats.by_key("hd3"), 10, 5)
    answers.save_path = str(editable_hd)
    before = editable_hd.stat().st_size
    window._new_image()
    qt_app.processEvents()
    assert answers.warnings and answers.warnings[-1][0] == "That image is open"
    assert editable_hd.stat().st_size == before


def test_a_new_image_that_cant_be_created_says_so(qt_app, window, tmp_path, answers):
    answers.new_image = (formats.by_key("hd3"), 10, 5)
    answers.save_path = str(tmp_path / "missing folder" / "x.img")
    window._new_image()
    assert pump(qt_app, lambda: bool(answers.warnings))
    assert "Couldn't create x.img" in answers.warnings[-1][1]
    assert window.tree.topLevelItemCount() == 0


def test_the_disk_format_dialog_for_a_new_image(qt_app):
    d = edit_dialogs.DiskFormatDialog(None)
    assert [d.type_combo.itemData(i) for i in range(d.type_combo.count())] == [f.key for f in formats.FORMATS]
    assert d.format.key == "hd3" and d.size_mb == 128 and d.partition_mb == 60
    assert d.summary.text() == "3 partitions of up to 60 MB, 128 MB in all."
    d.size_spin.setValue(20)
    d.partition_spin.setValue(5)
    assert d.summary.text() == "4 partitions of up to 5 MB, 20 MB in all."
    d.partition_spin.setValue(1)
    assert "leaving 2 MB unused (at most 18 partitions)" in d.summary.text()
    d.type_combo.setCurrentIndex(d.type_combo.findData("fl1h"))
    assert d.summary.text() == "A 1.6 MB floppy image."
    assert d.size_row[1].isHidden() and d.partition_row[1].isHidden()  # a floppy has one size
    d.type_combo.setCurrentIndex(d.type_combo.findData("hd9"))
    assert not d.size_row[1].isHidden() and d.partition_row[1].isHidden()  # an S900 disk has one partition
    assert d.size_spin.maximum() == 64
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).text() == "Create…"


def test_the_disk_format_dialog_for_an_existing_disk_keeps_its_size_and_density(qt_app, window, images, editable_hd):
    from akaiutil_gui.engine.models import Disk

    hd = Disk(0, "HD", 4, 8192, 2500, 2400, 19.5, 18.0, 90.0)
    d = edit_dialogs.DiskFormatDialog(None, hd)
    assert d.windowTitle() == "Format disk0" and d.size_row[1].isHidden()
    assert [d.type_combo.itemData(i) for i in range(d.type_combo.count())] == ["hd3", "hd1", "hd3cd", "hd9"]
    d.partition_spin.setValue(10)
    assert d.summary.text() == "2 partitions of up to 10 MB, 19.5 MB in all."
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).text() == "Format…"

    low = Disk(1, "FLL", 1, 1024, 800, 796, 0.8, 0.8, 99.5)
    f = edit_dialogs.DiskFormatDialog(None, low)
    assert [f.type_combo.itemData(i) for i in range(f.type_combo.count())] == ["fl3l", "fl1l", "fl9l"]


# ------------------------------------------------------------- typing a name
def test_typing_a_name_is_cleaned_as_you_go(qt_app):
    from PySide6.QtTest import QTest

    edit = edit_dialogs.AkaiNameEdit()
    notes = []
    edit.rejected.connect(notes.append)
    QTest.keyClicks(edit, "my_synth 2")
    assert edit.text() == "MY SYNTH 2" and notes == []
    edit.clear()
    for key in "a/b*c":
        QTest.keyClick(edit, key)
        qt_app.processEvents()
    assert edit.text() == "ABC"
    assert notes == ["“/” can't be used in a name.", "“*” can't be used in a name."]


def test_a_name_cant_grow_past_the_limit_and_a_paste_is_cut_with_a_note(qt_app):
    edit = edit_dialogs.AkaiNameEdit()
    notes = []
    edit.rejected.connect(notes.append)
    edit.insert("a very long name indeed")
    qt_app.processEvents()
    assert edit.text() == "A VERY LONG " and len(edit.text()) == 12
    assert notes and "at most 12 characters" in notes[-1]
    from PySide6.QtTest import QTest

    QTest.keyClicks(edit, "x")  # typing more does nothing at the limit
    assert edit.text() == "A VERY LONG "


def test_a_refused_keystroke_leaves_the_cursor_where_it_was(qt_app):
    from PySide6.QtTest import QTest

    edit = edit_dialogs.AkaiNameEdit("abc")
    edit.setCursorPosition(1)
    QTest.keyClicks(edit, "/")
    assert edit.text() == "ABC" and edit.cursorPosition() == 1
    QTest.keyClicks(edit, "x")  # and a good one goes in at the cursor
    assert edit.text() == "AXBC" and edit.cursorPosition() == 2


def test_an_s900_name_field_keeps_case_and_is_shorter(qt_app):
    edit = edit_dialogs.AkaiNameEdit("", "S900")
    edit.insert("Kick Drum Loud")
    assert edit.text() == "Kick Drum "
    edit.set_volume_type("S3000")  # changing the type tidies what's there by the new rules
    assert edit.text() == "KICK DRUM "
    edit.set_volume_type("S900")
    assert edit.text() == "KICK DRUM "


def test_the_dialogs_show_what_was_left_out(qt_app):
    from PySide6.QtTest import QTest

    d = edit_dialogs.RenameDialog(None, "volume", "DRUMS", "S3000")
    d.edit.clear()
    QTest.keyClicks(d.edit, "a/")
    qt_app.processEvents()
    assert d.edit.text() == "A" and "“/” can't be used" in d.problem.text()
    QTest.keyClicks(d.edit, "c")
    assert d.problem.text() == ""  # the next good keystroke clears it

    n = edit_dialogs.NewVolumeDialog(None, "A", [], 100, ["S3000"], "S3000")
    n.name_edit.insert("pads?")
    qt_app.processEvents()
    assert n.name == "PADS" and "“?” can't be used" in n.problem.text()


def test_the_new_volume_dialogs_name_field_follows_the_volume_type(qt_app):
    d = edit_dialogs.NewVolumeDialog(None, "A", [], 100, ["S900", "S3000"], "S3000")
    d.name_edit.insert("twelve chars")
    assert d.name_edit.text() == "TWELVE CHARS"
    d.type_combo.setCurrentIndex(d.type_combo.findText("S900"))
    assert d.name_edit.text() == "TWELVE CHAR" or len(d.name_edit.text()) == 10
    assert d.name_edit.placeholderText() == "up to 10 characters"


# ------------------------------------------------------------------- copying a volume
def test_a_volume_is_copied_from_its_context_menu(qt_app, window, images, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    texts = [a.text() for a in window._tree_menu(window._volume).actions() if not a.isSeparator()]
    assert "Copy Volume To…" in texts
    answers.copy_volume = lambda targets: (targets[0].partition, "DRUMS COPY")
    window._copy_volume(window._volume)
    assert pump(qt_app, lambda: window._writable and "Copied “DRUMS”" in window.statusBar().currentMessage())
    asked = next(a for a in answers.asked if a[0] == "copy")
    assert asked[1:3] == ("DRUMS", 2) and asked[3][0].startswith("disk0")
    assert pump(qt_app, lambda: window._volume is not None and window._volume.name == "DRUMS COPY" and window.table.rowCount() == 2)
    assert answers.warnings == []


def test_cancelling_the_copy_dialog_changes_nothing(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window._copy_volume(window._volume)
    assert pump(qt_app, lambda: any(a[0] == "copy" for a in answers.asked))
    assert [v.name for v in window.tree.volumes_in(0, "A")] == ["DRUMS", "BASS", "MY SYNTH"]


def test_a_copy_that_cant_be_done_says_why(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.copy_volume = lambda targets: (targets[0].partition, "BASS")  # a name the partition already has
    window._copy_volume(window._volume)
    assert pump(qt_app, lambda: any("already a volume called" in str(w) for w in answers.warnings))


def test_the_copy_dialog_suggests_a_free_name_and_checks_the_room(qt_app, editable_hd):
    from akaiutil_gui.engine.models import Partition, Volume
    from akaiutil_gui.ui import edit_dialogs

    vol = Volume(0, "A", 1, "DRUMS", "-", "0x0003", "17.00", "S3000")
    big = Partition(0, "A", "HD", 8192, 7680, 2000, 60.0, 15.6, 26.0)
    full = Partition(0, "B", "HD", 8192, 7680, 4, 60.0, 0.03, 0.1)
    targets = [edit_dialogs.CopyTarget(big, ("DRUMS", "DRUMS COPY")), edit_dialogs.CopyTarget(full, ("OTHER",))]
    dialog = edit_dialogs.CopyVolumeDialog(None, vol, 2, 1_000_000, targets)
    assert dialog.where.currentIndex() == 1  # (not back into the same partition, if there is another)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert "only has" in dialog.problem.text() and not ok.isEnabled()  # (the second one is full)
    dialog.where.setCurrentIndex(0)
    assert dialog.name_edit.text() == "DRUMS COPY 2" and ok.isEnabled()  # a name that is free there
    dialog.name_edit.setText("drums")
    assert not ok.isEnabled() and "already a volume called" in dialog.problem.text()
    dialog.name_edit.setText("kit")
    assert ok.isEnabled() and dialog.name == "KIT" and dialog.target.partition is big


# ------------------------------------------------------------------- backups and revert
def backups_of(image):
    from akaiutil_gui import backup

    return backup.list_backups(backup.default_root(), image)


def test_switching_editing_on_backs_the_image_up_first_when_asked_to(qt_app, window, editable_hd, answers):
    before = editable_hd.read_bytes()
    open_and_show(qt_app, window, editable_hd)
    assert backups_of(editable_hd) == []
    answers.confirm = True  # "Back Up and Allow Editing"
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    (made,) = backups_of(editable_hd)
    assert made.path.read_bytes() == before
    assert "Backed up edit.img" in window.statusBar().currentMessage() or window._writable
    window.act_allow_edit.trigger()  # off...
    assert pump(qt_app, lambda: not window._writable)
    window.act_allow_edit.trigger()  # ...and on again: it was backed up already, so the plain question comes back
    assert pump(qt_app, lambda: window._writable)
    assert len(backups_of(editable_hd)) == 1 and answers.asked[-1][3] == "Allow Editing"


def test_editing_can_be_switched_on_without_a_backup_and_then_isnt_asked_again(qt_app, window, editable_hd, answers):
    from akaiutil_gui.ui import edit_dialogs

    open_and_show(qt_app, window, editable_hd)
    answers.confirm = edit_dialogs.OTHER
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    assert backups_of(editable_hd) == []
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: not window._writable)
    answers.confirm = True
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable) and backups_of(editable_hd) == []  # (it stays declined for the session)


def test_cancelling_the_question_makes_no_backup_and_leaves_editing_off(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.confirm = False
    window.act_allow_edit.trigger()
    assert not window._writable and backups_of(editable_hd) == []


def test_a_backup_that_cant_be_made_keeps_editing_off_and_says_why(qt_app, window, editable_hd, answers, monkeypatch):
    from akaiutil_gui import backup

    def full(root, image, *a, **k):
        raise backup.BackupError("There isn't room for a backup of edit.img.")

    monkeypatch.setattr(backup, "make_backup", full)
    open_and_show(qt_app, window, editable_hd)
    answers.confirm = True
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: any("isn't room" in str(w) for w in answers.warnings))
    assert not window._writable and "Editing is still off" in str(answers.warnings[-1])


def test_revert_puts_the_backup_back_and_keeps_what_was_there(qt_app, window, editable_hd, answers):
    original = editable_hd.read_bytes()
    open_and_show(qt_app, window, editable_hd)
    answers.confirm = True
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    answers.name = "thump"
    window._rename_file(window._volume, next(f for f in window._volume_files if f.name == "KICK.S3"))
    assert pump(qt_app, lambda: "THUMP.S3" in [window.table.item(r, 1).text() for r in range(window.table.rowCount())])
    assert editable_hd.read_bytes() != original
    answers.revert = lambda entries: entries[-1]  # (the oldest: the one taken before editing)
    window._revert()
    assert pump(qt_app, lambda: "Reverted edit.img" in window.statusBar().currentMessage())
    assert editable_hd.read_bytes() == original
    assert len(backups_of(editable_hd)) == 2  # the one from before, and one of the edited state
    assert not window._writable  # reopened read-only
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)


def test_revert_with_no_backups_says_so(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window._revert()
    assert any("no backups" in str(w) for w in answers.warnings) and not any(a[0] == "revert" for a in answers.asked)


def test_cancelling_the_revert_changes_nothing(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.confirm = True
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    before = editable_hd.read_bytes()
    window._revert()  # answers.revert is None: the dialog was cancelled
    assert any(a[0] == "revert" for a in answers.asked) and editable_hd.read_bytes() == before and window._writable


def test_a_new_image_needs_no_backup_but_one_added_while_editing_is_asked_about(qt_app, window, editable_hd, images, answers):
    open_and_show(qt_app, window, editable_hd)
    answers.confirm = True
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    window.add_images([str(images["fl1"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2 and window._writable)
    assert answers.asked[-1][0] == "Back up the new image?" and len(backups_of(images["fl1"])) == 1
    window._backup_handled.add(str(images["fl9"].resolve()))  # (as a freshly made image is)
    window.add_images([str(images["fl9"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 3 and window._writable)
    assert answers.asked[-1][0] == "Back up the new image?" and backups_of(images["fl9"]) == []  # (not asked again)


# ------------------------------------------------------------------- the backup question
def three_way(lines=("HD5.img  ·  512.0 MB",)):
    from akaiutil_gui.ui import edit_dialogs

    return edit_dialogs.ThreeWayDialog(None, "Allow editing?", "Editing changes the disk image files themselves.",
                                       "There is no undo. Back up this image first?", "Back Up and Edit", "Edit Without Backup", list(lines))


def test_the_question_is_tall_enough_for_its_wrapped_text(qt_app):
    from akaiutil_gui.ui import edit_dialogs

    d = edit_dialogs.ThreeWayDialog(None, "Incompatible Files", "12 incompatible files for S1000 volume “VOLUME 0001”. Add anyway? " * 2,
                                    "These are S3000 files and the destination volume is an S1000 one. " * 6, "Add Anyway", "Skip", ["a.S3", "b.S3"])
    d.show()
    assert d.height() >= d.layout().heightForWidth(d.width())  # (a fixed width and a height for one line of text cut the text off)
    d.details_button.click()
    assert d.height() >= d.layout().heightForWidth(d.width()) and not d.details.isHidden()
    d.close()


def test_the_backup_question_has_its_buttons_in_this_order_and_the_last_is_the_default(qt_app):
    d = three_way()
    d.show()
    buttons = sorted((b for b in d.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)), key=lambda b: b.mapTo(d, b.rect().topLeft()).x())
    assert [b.text() for b in buttons] == ["Show Details…", "Cancel", "Edit Without Backup", "Back Up and Edit"]
    assert buttons[-1].isDefault() and not any(b.isDefault() for b in buttons[:-1])
    assert d.width() == d.WIDTH and d.WIDTH < 600  # (narrow: the text wraps)
    d.deleteLater()


def test_the_backup_question_answers_true_other_or_false(qt_app):
    from akaiutil_gui.ui import edit_dialogs
    from PySide6.QtCore import QTimer

    def answer(click):
        d = three_way()
        QTimer.singleShot(0, lambda: getattr(d, click).click() if click else d.reject())
        out = d.ask()
        d.deleteLater()
        return out

    assert answer("go_button") is True
    assert answer("other_button") == edit_dialogs.OTHER
    assert answer("cancel_button") is False
    assert answer(None) is False  # (Escape, or closing the window)


def test_the_details_stay_hidden_until_asked_for_and_can_be_hidden_again(qt_app):
    d = three_way()
    d.show()
    assert d.details.isHidden() and d.details_button.text() == "Show Details…"
    d.details_button.click()
    assert not d.details.isHidden() and d.details_button.text() == "Hide Details" and "HD5.img" in d.details.toPlainText()
    d.details_button.click()
    assert d.details.isHidden()
    d.deleteLater()
    assert three_way(lines=()).details_button.isHidden()  # nothing to show: no button


# ------------------------------------------------------------------- a sample's settings
def sample_dialog(root=60, loops=((3000, 4000),), frames=22050, audio=None):
    import struct

    from akaiutil_gui import akai_header
    from akaiutil_gui.ui import edit_dialogs

    h = bytearray(akai_header.build_header("S3000", 44100, frames, root=root, loops=list(loops)))
    h[26:30] = struct.pack("<I", frames)
    h[34:38] = struct.pack("<I", frames - 1)
    return edit_dialogs.SampleSettingsDialog(None, "KICK.S3", akai_header.read_settings(bytes(h)), audio)


def test_the_sample_dialog_shows_the_settings_and_gives_them_back_unchanged(qt_app):
    from akaiutil_gui import akai_header

    d = sample_dialog(root=60)
    assert (d.root.value(), d.root_name.text(), d.semitone.value(), d.cents.value()) == (60, "C3", 0, 0)
    assert (d.start.value(), d.end.value()) == (0, 22049)
    on = [row[0].isChecked() for row in d.loop_rows]
    assert on == [True] + [False] * 7
    assert d.result_settings == d._current and d.save_button.isEnabled()
    d.deleteLater()


def test_editing_in_the_sample_dialog_changes_the_result(qt_app):
    from akaiutil_gui import akai_header

    d = sample_dialog()
    d.root.setValue(72)
    assert d.root_name.text() == "C4"
    d.semitone.setValue(-3)
    d.cents.setValue(20)
    d.play_mode.setCurrentIndex(d.play_mode.findData(2))
    d.start.setValue(100)
    on, start, end, time, hold = d.loop_rows[1]
    on.setChecked(True)
    assert start.isEnabled() and not time.isEnabled()  # (hold is on, so the time is fixed)
    hold.setChecked(False)
    assert time.isEnabled()
    start.setValue(500)
    end.setValue(900)
    time.setValue(250)
    r = d.result_settings
    assert (r.root, r.semitone, r.cents, r.play_mode, r.start) == (72, -3, 20, 2, 100)
    assert [(l.start, l.end, l.time) for l in r.loops[:2]] == [(3000, 4000, akai_header.LOOP_HOLD), (500, 900, 250)]
    d.deleteLater()


def test_the_sample_dialog_wont_save_settings_that_cant_be_written(qt_app):
    d = sample_dialog()
    d.start.setValue(5000)
    d.end.setValue(100)  # the end before the start
    assert not d.save_button.isEnabled() and "start marker" in d.problem.text()
    d.end.setValue(9000)
    assert d.save_button.isEnabled() and d.problem.text() == ""
    on, start, end, time, hold = d.loop_rows[0]
    start.setValue(4000)
    end.setValue(3000)
    assert not d.save_button.isEnabled() and "Loop 1" in d.problem.text()
    on.setChecked(False)  # (a loop that is off isn't checked)
    assert d.save_button.isEnabled()
    d.deleteLater()


def test_a_loop_that_was_off_with_junk_in_it_comes_back_untouched(qt_app):
    import dataclasses

    from akaiutil_gui import akai_header

    d = sample_dialog()
    junk = akai_header.LoopSetting(1473175, 1, 0, 0, bytes(range(1, 11)) + b"\x00\x00")
    d._current = dataclasses.replace(d._current, loops=(d._current.loops[0], junk, *d._current.loops[2:]))
    assert d.result_settings.loops[1] == junk
    d.deleteLater()


def test_sample_settings_are_edited_from_the_edit_menu_and_the_file_menu(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    window._update_edit_actions()
    assert window.act_sample_settings.isEnabled()
    menu = window._file_menu(window._selected_rows())
    entry = next(a for a in menu.actions() if a.text() == "Sample Settings…")
    assert entry.isEnabled()
    window.table.selectAll()
    window._update_edit_actions()
    assert not window.act_sample_settings.isEnabled()  # (one sample at a time)


def test_changing_a_samples_settings_through_the_window(qt_app, window, editable_hd, answers):
    import dataclasses

    open_and_show(qt_app, window, editable_hd)
    kick = next(f for f in window._volume_files if f.name == "KICK.S3")
    answers.confirm = True
    answers.sample_settings = lambda current: dataclasses.replace(current, root=40)
    window._sample_settings(window._volume, kick)
    assert pump(qt_app, lambda: window._writable and "Changed the settings of “KICK.S3”" in window.statusBar().currentMessage())
    assert pump(qt_app, lambda: [f.name for _, f in window.table.selected_rows()] == ["KICK.S3"])
    assert pump(qt_app, lambda: window._engine.read_sample_settings(window._volume, kick).root == 40)
    assert answers.warnings == []
    asked = next(a for a in answers.asked if a[0] == "settings")
    assert asked[2] == 22050  # (the dialog was given the sample's audio for its waveform)


def test_cancelling_or_changing_nothing_in_the_sample_dialog_writes_nothing(qt_app, window, editable_hd, answers):
    open_and_show(qt_app, window, editable_hd)
    kick = next(f for f in window._volume_files if f.name == "KICK.S3")
    answers.confirm = True
    answers.sample_settings = None  # cancelled
    window._sample_settings(window._volume, kick)
    assert pump(qt_app, lambda: any(a[0] == "settings" for a in answers.asked))
    before = editable_hd.read_bytes()
    answers.asked.clear()
    answers.sample_settings = lambda current: current  # nothing changed
    window._sample_settings(window._volume, kick)
    assert pump(qt_app, lambda: any(a[0] == "settings" for a in answers.asked))
    assert editable_hd.read_bytes() == before


def test_s900_samples_have_no_settings_entry(qt_app, window, images):
    window.open_images([str(images["fl9"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.reveal_volume(vol(1))
    assert pump(qt_app, lambda: window._volume is not None and window.table.rowCount() == 1)
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    assert window._editable_sample(window._selected_rows()) is None


# ------------------------------------------------------------ the waveform in the sample dialog
def waveform_dialog(loops=((3000, 4000),)):
    import numpy as np

    d = sample_dialog(loops=loops, audio=np.zeros(22050, dtype=np.int16))
    d.resize(700, 900)
    d.show()
    return d


def test_the_dialogs_waveform_shows_the_markers_of_the_samples_start_end_and_loops_that_are_on(qt_app):
    from akaiutil_gui.ui import marker_waveform as mw

    d = waveform_dialog(loops=((3000, 4000), (5000, 6000)))
    assert d.wave.markers == {mw.START: 0, mw.END: 22049, mw.loop_in(0): 3000, mw.loop_out(0): 4000, mw.loop_in(1): 5000, mw.loop_out(1): 6000}
    on, *_ = d.loop_rows[1]
    on.setChecked(False)  # (loop 2 off: its markers go)
    assert mw.loop_in(1) not in d.wave.markers
    d.loop_rows[3][0].setChecked(True)
    assert mw.loop_in(3) in d.wave.markers and mw.loop_out(3) in d.wave.markers
    d.deleteLater()


def test_dragging_a_marker_on_the_waveform_changes_the_numbers_beside_it(qt_app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from akaiutil_gui.ui import marker_waveform as mw

    d = waveform_dialog()
    w = d.wave
    x = lambda frame: round(mw.x_for_frame(frame, w.width(), *w.view_state()))  # noqa: E731
    QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x(4000), 60))
    QTest.mouseMove(w, QPoint(x(7000), 60))
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x(7000), 60))
    on, start, end, time, hold = d.loop_rows[0]
    assert abs(end.value() - 7000) <= 25 and start.value() == 3000
    assert abs(d.result_settings.loops[0].end - 7000) <= 25
    assert d.save_button.isEnabled()
    d.deleteLater()


def test_a_marker_pushing_its_partner_on_the_waveform_updates_both_numbers(qt_app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from akaiutil_gui import akai_header
    from akaiutil_gui.ui import marker_waveform as mw

    d = waveform_dialog()
    w = d.wave
    x = lambda frame: round(mw.x_for_frame(frame, w.width(), *w.view_state()))  # noqa: E731
    QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x(4000), 60))
    QTest.mouseMove(w, QPoint(x(1000), 60))  # the loop's end dragged to before its start
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x(1000), 60))
    on, start, end, time, hold = d.loop_rows[0]
    assert start.value() < end.value() and abs(end.value() - 1000) <= 25
    assert not akai_header.problems(d.result_settings)  # (a drag can't make an invalid loop)
    d.deleteLater()


def test_typing_a_marker_number_moves_it_on_the_waveform(qt_app):
    from akaiutil_gui.ui import marker_waveform as mw

    d = waveform_dialog()
    d.start.setValue(1234)
    d.loop_rows[0][2].setValue(9876)
    assert d.wave.markers[mw.START] == 1234 and d.wave.markers[mw.loop_out(0)] == 9876
    d.deleteLater()


def test_grabbing_a_loop_marker_emphasises_that_loops_row(qt_app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from akaiutil_gui.ui import marker_waveform as mw

    d = waveform_dialog(loops=((3000, 4000), (8000, 9000)))
    w = d.wave
    x = round(mw.x_for_frame(9000, w.width(), *w.view_state()))
    QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))
    assert [label.font().bold() for label in d.loop_labels[:3]] == [False, True, False]
    d.deleteLater()


def test_each_loop_row_is_numbered_in_its_markers_colour(qt_app):
    from akaiutil_gui.ui.volume_colors import volume_color

    d = waveform_dialog()
    for i, label in enumerate(d.loop_labels):
        assert volume_color(i).name() in label.styleSheet()
    d.deleteLater()


def test_the_scroll_bar_and_zoom_buttons_appear_with_zoom_and_go_with_fit(qt_app):
    d = waveform_dialog()
    assert d.view_scroll.isHidden()
    d.zoom_in_button.click()
    d.zoom_in_button.click()
    assert not d.view_scroll.isHidden() and d.wave.zoom > 1
    d.view_scroll.setValue(d.view_scroll.maximum())
    assert d.wave.view_state()[0] == d.view_scroll.maximum()
    d.zoom_out_button.click()
    d.fit_button.click()
    assert d.wave.zoom == 1 and d.view_scroll.isHidden()
    d.deleteLater()


def test_the_dialog_works_without_audio_and_still_has_draggable_markers(qt_app):
    from akaiutil_gui.ui import marker_waveform as mw

    d = sample_dialog(audio=None)
    d.show()
    assert d.wave.frames == 22050 and mw.START in d.wave.markers
    d.wave.grab()
    d.deleteLater()


# ------------------------------------------------------------ preview from the sample dialog
def preview_dialog(loops=((3000, 4000),), play_mode=0, audio=True):
    import dataclasses

    import numpy as np

    from akaiutil_gui import akai_header
    from akaiutil_gui.ui import edit_dialogs

    base = sample_dialog(loops=loops)
    current = dataclasses.replace(base._current, play_mode=play_mode)
    base.deleteLater()
    ramp = (np.arange(22050) % 20000).astype(np.int16) if audio else None
    d = edit_dialogs.SampleSettingsDialog(None, "KICK.S3", current, ramp)
    d.resize(700, 900)
    d.show()
    return d


def click_empty_waveform(d, frame=8000):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from akaiutil_gui.ui import marker_waveform as mw

    x = round(mw.x_for_frame(frame, d.wave.width(), *d.wave.view_state()))
    QTest.mousePress(d.wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))
    QTest.mouseRelease(d.wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))


def test_clicking_the_waveform_plays_the_sample_and_clicking_again_stops_it(qt_app, fake_audio):
    d = preview_dialog(loops=(), play_mode=2)  # (no looping)
    assert not hasattr(d, "play_button")  # (a click on the waveform is the only control: no button that changes width)
    click_empty_waveform(d)
    assert d.player.is_playing()
    assert fake_audio.opened[-1].sample_rate == 44100
    click_empty_waveform(d)
    assert not d.player.is_playing() and d.wave._playhead is None
    d.deleteLater()


def test_a_click_on_a_marker_drags_it_and_does_not_play(qt_app, fake_audio):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from akaiutil_gui.ui import marker_waveform as mw

    d = preview_dialog()
    x = round(mw.x_for_frame(3000, d.wave.width(), *d.wave.view_state()))
    QTest.mousePress(d.wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))
    QTest.mouseRelease(d.wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))
    assert not d.player.is_playing()
    d.deleteLater()


def test_the_preview_plays_what_the_dialog_says_the_loop_and_the_markers(qt_app, fake_audio):
    d = preview_dialog(loops=((3000, 4000),), play_mode=0)
    d.start.setValue(1000)
    d.end.setValue(8000)
    plan, slot = d._preview_plan()
    assert (plan.start, plan.end, plan.loop_start, plan.loop_end, plan.looping, slot) == (1000, 8000, 3000, 4000, True, 0)
    assert plan.dwell_ms is None  # (hold: until stopped)
    d.loop_rows[0][4].setChecked(False)  # not hold: 500 ms
    assert d._preview_plan()[0].dwell_ms == 500
    d.play_mode.setCurrentIndex(d.play_mode.findData(2))  # no looping: plays once, start to end
    plan, slot = d._preview_plan()
    assert (plan.looping, slot, plan.start, plan.end) == (False, None, 1000, 8000)
    d.deleteLater()


def test_a_loop_that_is_grabbed_is_the_one_that_plays(qt_app, fake_audio):
    d = preview_dialog(loops=((3000, 4000), (8000, 9000)))
    assert d._preview_plan()[1] == 0
    d._select_loop(1)
    plan, slot = d._preview_plan()
    assert slot == 1 and (plan.loop_start, plan.loop_end) == (8000, 9000)
    d.deleteLater()


def test_dragging_the_playing_loops_marker_changes_the_loop_while_it_plays(qt_app, fake_audio):
    d = preview_dialog(loops=((3000, 4000),))
    click_empty_waveform(d)
    assert d.player.is_playing()
    seen = []
    d.player.update_loop_points = lambda a, b: seen.append((a, b))
    d.loop_rows[0][2].setValue(4500)  # the loop's end typed in while it plays
    assert seen and seen[-1] == (3000, 4500)
    d.player.stop()
    d.deleteLater()


def test_playback_stops_when_the_dialog_closes(qt_app, fake_audio):
    d = preview_dialog()
    click_empty_waveform(d)
    assert d.player.is_playing()
    d.reject()
    assert not d.player.is_playing()
    d.deleteLater()


def test_the_playhead_follows_the_player_and_goes_when_it_finishes(qt_app, fake_audio):
    d = preview_dialog(loops=(), play_mode=2)
    click_empty_waveform(d)
    d.player.position_changed.emit(5000)
    assert d.wave._playhead == 5000
    d.player.stop()
    assert d.wave._playhead is None
    d.deleteLater()


def test_nothing_plays_without_audio_or_with_audio_turned_off(qt_app, fake_audio):
    no_audio = preview_dialog(audio=False)
    click_empty_waveform(no_audio)
    assert not no_audio.player.is_playing()
    no_audio.deleteLater()


def test_a_click_plays_nothing_when_audio_is_disabled(qt_app):
    d = preview_dialog()  # (the tests run with audio disabled unless they ask for the fake device)
    click_empty_waveform(d)
    assert not d.player.is_playing()
    d.deleteLater()


def test_the_scroll_bars_room_is_kept_so_zooming_doesnt_move_anything(qt_app):
    d = waveform_dialog()
    assert d.view_scroll.sizePolicy().retainSizeWhenHidden() and d.view_scroll.isHidden()
    before = (d.sizeHint(), d.wave.geometry(), d.zoom_in_button.geometry().top() - d.wave.geometry().bottom())
    d.zoom_in_button.click()
    d.zoom_in_button.click()
    assert not d.view_scroll.isHidden()
    after = (d.sizeHint(), d.wave.geometry(), d.zoom_in_button.geometry().top() - d.wave.geometry().bottom())
    assert before == after  # (the layout is the same with the bar showing or not)
    d.deleteLater()


# ------------------------------------------------------------------- dialog layout and styling
def import_dialog_for(window_or_none=None, sources=3):
    import numpy as np
    import soundfile

    from akaiutil_gui import importing
    from akaiutil_gui.engine.models import Volume
    from akaiutil_gui.ui import edit_dialogs

    import tempfile
    from pathlib import Path

    folder = Path(tempfile.mkdtemp())
    paths = []
    for n in ("BASS_LOW", "CLAP", "HAT_CLOSED")[:sources]:
        p = folder / f"{n}.wav"
        soundfile.write(str(p), np.sin(np.arange(4410) / 10).astype("float32") * 0.5, 44100, subtype="PCM_16")
        paths.append(p)
    vol = Volume(0, "A", 1, "DRUMS", "-", "0x3", "17.00", "S3000")
    return edit_dialogs.ImportDialog(None, vol, [importing.inspect(p) for p in paths])


def test_the_import_dialogs_widgets_never_overlap_even_when_shown_at_its_smallest(qt_app):
    from PySide6.QtWidgets import QWidget

    d = import_dialog_for()
    d.show()
    qt_app.processEvents()
    d.resize(d.minimumWidth(), d.minimumHeight())  # (as small as it can be made)
    qt_app.processEvents()
    holder = d.type_combo.parentWidget()
    list_bottom = d.files.mapTo(d, d.files.rect().bottomLeft()).y()
    row_top = holder.mapTo(d, holder.rect().topLeft()).y()
    assert row_top > list_bottom  # (the list ends before the "Sample type" row begins)
    last = d.buttons.mapTo(d, d.buttons.rect().bottomLeft()).y()
    assert last <= d.height()
    assert d.minimumHeight() >= d.layout().minimumHeightForWidth(max(d.minimumWidth(), d.sizeHint().width()))
    d.deleteLater()


def test_the_combo_boxs_holder_doesnt_paint_a_box_of_its_own(qt_app):
    d = import_dialog_for()
    assert d.type_combo.parentWidget().objectName() == "transparentContainer"
    d.deleteLater()


def test_the_radio_button_strips_in_the_sort_dialog_have_rounded_ends_in_both_themes(qt_app):
    from akaiutil_gui.ui import theme

    for palette in (theme.DARK_PALETTE, theme.LIGHT_PALETTE):
        css = theme.render_stylesheet(palette)
        rule = css[css.index("QRadioButton {") :].split("}")[0]
        assert "border-radius: 11px" in rule and palette["bg"] in rule
