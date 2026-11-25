"""Save Partition to File… and Restore Partition from File… in the window (offscreen, real akaiutil)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from akaiutil_gui.ui.main_window import MainWindow
from test_gui_editing import answers, idle, open_and_show, pump, window  # noqa: F401  (fixtures)


@pytest.fixture
def asked_paths(monkeypatch):
    """Stand-ins for the file dialogs: what the save dialog was offered, and the paths they answer with."""

    class Paths:
        save = None
        open = None
        offered = None

    p = Paths()

    def save(self, suggestion):
        p.offered = suggestion
        return p.save

    monkeypatch.setattr(MainWindow, "_ask_partition_save_path", save)
    monkeypatch.setattr(MainWindow, "_ask_partition_file", lambda self: p.open)
    return p


def labels(menu):
    return [a.text() for a in menu.actions() if a.text()]


def saved_a(qt_app, window, asked_paths, tmp_path):  # noqa: F811
    part = window.tree.partition(0, "A")
    asked_paths.save = str(tmp_path / "A.part")
    window._save_partition(part)
    assert pump(qt_app, lambda: (tmp_path / "A.part").exists() and idle(window))
    return tmp_path / "A.part"


def test_the_partition_menu_offers_both_for_a_hard_disk_partition_and_not_for_a_floppy(qt_app, window, editable_hd, images, answers):
    window.open_images([str(editable_hd), str(images["fl1"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 2 and window.tree.partition(0, "A") is not None)
    assert labels(window._tree_menu(window.tree.partition(0, "A")))[-2:] == ["Save Partition to File…", "Restore Partition from File…"]
    floppy = window._engine.list_partitions(next(d for d in window._engine.list_disks() if d.type.startswith("FL")))[0]
    assert "Save Partition to File…" not in labels(window._tree_menu(floppy))


def test_saving_a_partition_offers_a_name_and_writes_the_file(qt_app, window, editable_hd, asked_paths, tmp_path):
    open_and_show(qt_app, window, editable_hd)
    part = window.tree.partition(0, "A")
    asked_paths.save = str(tmp_path / "mine.part")
    window._save_partition(part)
    assert asked_paths.offered.endswith("edit partition A.part")
    assert pump(qt_app, lambda: (tmp_path / "mine.part").exists() and "Saved partition A" in window.statusBar().currentMessage())
    assert (tmp_path / "mine.part").stat().st_size == part.size_bytes
    assert "mine.part" in window.statusBar().currentMessage()


def test_cancelling_the_save_dialog_writes_nothing(qt_app, window, editable_hd, asked_paths, tmp_path):
    open_and_show(qt_app, window, editable_hd)
    asked_paths.save = None
    window._save_partition(window.tree.partition(0, "A"))
    assert pump(qt_app, lambda: idle(window))
    assert list(tmp_path.iterdir()) == [editable_hd]


def test_a_save_that_fails_says_why(qt_app, window, editable_hd, asked_paths, answers, tmp_path):
    open_and_show(qt_app, window, editable_hd)
    asked_paths.save = str(tmp_path / "no such folder" / "A.part")
    window._save_partition(window.tree.partition(0, "A"))
    assert pump(qt_app, lambda: answers.warnings)
    assert answers.warnings[0][0] == "Couldn't save the partition" and "A.part" in answers.warnings[0][1]


def test_restoring_asks_first_and_puts_the_partition_over_another(qt_app, window, editable_hd, asked_paths, answers, tmp_path):
    open_and_show(qt_app, window, editable_hd)
    saved = saved_a(qt_app, window, asked_paths, tmp_path)
    answers.confirm = True
    window.act_allow_edit.trigger()  # (editing on: the first question is the usual "Allow editing?")
    assert pump(qt_app, lambda: window._writable)
    asked_paths.open = str(saved)
    target = window.tree.partition(0, "B")
    window._restore_partition(target)
    assert pump(qt_app, lambda: "Restored partition B from A.part" in window.statusBar().currentMessage())
    title, text, detail, action, _ = answers.asked[-1]
    assert (title, action) == ("Replace partition?", "Replace Partition") and "partition B" in text and "A.part" in text and "can't be undone" in detail
    assert pump(qt_app, lambda: idle(window))
    assert [v.name for v in window._engine.list_volumes(window.tree.partition(0, "B"))] == ["DRUMS", "BASS", "MY SYNTH"]


def test_saying_no_leaves_the_partition_alone(qt_app, window, editable_hd, asked_paths, answers, tmp_path):
    open_and_show(qt_app, window, editable_hd)
    saved = saved_a(qt_app, window, asked_paths, tmp_path)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    before = editable_hd.read_bytes()
    answers.confirm = False
    asked_paths.open = str(saved)
    window._restore_partition(window.tree.partition(0, "B"))
    assert pump(qt_app, lambda: idle(window))
    assert answers.asked[-1][0] == "Replace partition?" and editable_hd.read_bytes() == before


def test_a_file_that_does_not_fit_is_refused_before_anything_is_asked(qt_app, window, editable_hd, asked_paths, answers, tmp_path):
    open_and_show(qt_app, window, editable_hd)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    part = window.tree.partition(0, "B")
    wrong = tmp_path / "WRONG.part"
    wrong.write_bytes(bytes(part.size_bytes * 2))
    before, asked = editable_hd.read_bytes(), len(answers.asked)
    asked_paths.open = str(wrong)
    window._restore_partition(part)
    assert pump(qt_app, lambda: answers.warnings)
    assert answers.warnings[-1][0] == "Can't restore the partition" and "exactly as big" in answers.warnings[-1][1]
    assert len(answers.asked) == asked and editable_hd.read_bytes() == before  # (no "Replace partition?" for a file that can't go in)


def test_cancelling_the_open_dialog_does_nothing(qt_app, window, editable_hd, asked_paths, answers):
    open_and_show(qt_app, window, editable_hd)
    window.act_allow_edit.trigger()
    assert pump(qt_app, lambda: window._writable)
    before, asked = editable_hd.read_bytes(), len(answers.asked)
    asked_paths.open = None
    window._restore_partition(window.tree.partition(0, "B"))
    assert pump(qt_app, lambda: idle(window))
    assert len(answers.asked) == asked and editable_hd.read_bytes() == before
