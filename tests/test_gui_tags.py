"""Tag editing through the window: Edit > Tag Files…, the file menu, the Details pane's link, and the dialog itself."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt

from akaiutil_gui import describe
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import FileEntry
from akaiutil_gui.ui import edit_dialogs
from akaiutil_gui.ui.main_window import MainWindow
from test_gui_editing import answers, delete_widgets, open_and_show, pump, vol, window  # noqa: F401  (fixtures)


@pytest.fixture
def asked_tags(monkeypatch):
    """Stand-in for the Tag Files dialog: records what it was shown and answers (add, remove), or None for Cancel."""

    class Asked:
        answer = None
        shown: list = []

    a = Asked()
    a.shown = []
    monkeypatch.setattr(MainWindow, "_ask_tags", lambda self, files, names: (a.shown.append(([f.name for f in files], names)), a.answer)[1])
    return a


def entry(tags="", names=()):
    return FileEntry(1, "KICK.S3", 100, "0x0009", "17.00", tags, tuple(names))


# --------------------------------------------------------------------------------- the dialog
def test_the_dialog_shows_which_tags_are_on_all_some_or_none_of_the_files(qt_app):
    files = [entry("01 03"), entry("03")]
    d = edit_dialogs.TagFilesDialog(None, files, {1: "DRUMS", 3: "", 4: "BASS"})
    assert d._boxes[3].checkState() == Qt.CheckState.Checked
    assert d._boxes[1].checkState() == Qt.CheckState.PartiallyChecked
    assert d._boxes[2].checkState() == Qt.CheckState.Unchecked
    assert d._boxes[1].text() == "01  DRUMS" and d._boxes[3].text() == "03  Tag 3" and d._boxes[2].text() == "02  Tag 2"
    assert d.add == [] and d.remove == [] and not d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()  # nothing changed yet
    d.deleteLater()


def test_clicking_a_tag_cycles_between_ticked_and_empty_and_a_dashed_one_gets_ticked(qt_app):
    d = edit_dialogs.TagFilesDialog(None, [entry("01"), entry("")], {})
    box = d._boxes[1]
    assert box.checkState() == Qt.CheckState.PartiallyChecked
    box.click()
    assert box.checkState() == Qt.CheckState.Checked and d.add == [1] and d.remove == []
    box.click()
    assert box.checkState() == Qt.CheckState.Unchecked and d.add == [] and d.remove == [1]
    box.click()
    assert box.checkState() == Qt.CheckState.Checked
    d.deleteLater()


def test_a_dashed_tag_left_alone_changes_nothing(qt_app):
    d = edit_dialogs.TagFilesDialog(None, [entry("05"), entry("")], {})
    d._boxes[7].click()
    assert d.add == [7] and d.remove == [] and 5 not in d.add + d.remove
    assert d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()
    d.deleteLater()


def test_the_dialog_refuses_a_fifth_tag(qt_app):
    d = edit_dialogs.TagFilesDialog(None, [entry("01 02 03 04"), entry("")], {})
    d._boxes[9].click()
    ok = d.buttons.button(d.buttons.StandardButton.Ok)
    assert not ok.isEnabled() and "KICK.S3" in d.problem.text() and "4" in d.problem.text()
    d._boxes[1].click()  # (dashed -> ticked: still no room for a fifth on the first file)
    d._boxes[1].click()  # (-> empty: takes tag 1 off, which frees a place)
    assert d.remove == [1] and ok.isEnabled() and d.problem.text() == ""
    d.deleteLater()


# --------------------------------------------------------------------------------- the details pane
def test_the_details_of_a_file_list_its_tags_and_offer_to_edit_them():
    f = entry("02 03", ("KICKS", ""))
    text = "/disk0/A/DRUMS/KICK.S3\n"
    rows = {r.label: r for b in describe.describe_file(text, f, None, tags_editable=True).blocks for r in b.rows}
    assert rows["Tags"].value == "KICKS, Tag 3" and rows["Tags"].link_text == "Edit Tags…" and rows["Tags"].link_id == describe.TAGS_LINK
    plain = {r.label: r for b in describe.describe_file(text, f, None).blocks for r in b.rows}
    assert plain["Tags"].value == "KICKS, Tag 3" and plain["Tags"].link_text == ""  # shown, but not editable here
    untagged = {r.label for b in describe.describe_file(text, entry(""), None).blocks for r in b.rows}
    assert "Tags" not in untagged  # nothing to say, and nothing to offer
    assert "None" == {r.label: r for b in describe.describe_file(text, entry(""), None, tags_editable=True).blocks for r in b.rows}["Tags"].value


def test_the_tags_link_is_drawn_and_reports_its_click(qt_app):
    from akaiutil_gui.ui.details_view import DetailsView, render_html
    from akaiutil_gui.ui import theme

    d = describe.describe_file("/disk0/A/DRUMS/KICK.S3\n", entry("01"), None, tags_editable=True)
    assert 'href="tags"' in render_html(d, theme.current_palette())
    view = DetailsView()
    got = []
    view.linkClicked.connect(got.append)
    from PySide6.QtCore import QUrl

    view.anchorClicked.emit(QUrl("tags"))
    assert got == ["tags"]
    view.deleteLater()


# --------------------------------------------------------------------------------- through the window
def tags_of(window, name):
    return next(f.tags for f in window._engine.list_files(window._volume) if f.name == name)


def test_tag_files_from_the_edit_menu(qt_app, window, editable_hd, answers, asked_tags):
    open_and_show(qt_app, window, editable_hd)
    window.table.selectAll()
    assert pump(qt_app, lambda: len(window._selected_rows()) == 2)
    window._update_edit_actions()
    assert window.act_tag_files.isEnabled()
    answers.confirm = True
    asked_tags.answer = ([3, 7], [])
    window.act_tag_files.trigger()
    assert pump(qt_app, lambda: window._writable and "Changed the tags of 2 files" in window.statusBar().currentMessage())
    assert asked_tags.shown[0][0] == ["KICK.S3", "SNARE.S3"] and asked_tags.shown[0][1][1] == "TAG A"  # the partition's names went in
    assert pump(qt_app, lambda: tags_of(window, "KICK.S3") == "03 07" and tags_of(window, "SNARE.S3") == "03 07")
    assert answers.warnings == []


def test_one_file_comes_back_selected_after_its_tags_change(qt_app, window, editable_hd, answers, asked_tags):
    open_and_show(qt_app, window, editable_hd)
    kick = next(f for f in window._volume_files if f.name == "KICK.S3")
    window.table.select_file(window._volume, kick.index)
    answers.confirm = True
    asked_tags.answer = ([5], [])
    window._tag_selected()
    assert pump(qt_app, lambda: "Changed the tags of 1 file." in window.statusBar().currentMessage())
    assert pump(qt_app, lambda: [f.name for _, f in window.table.selected_rows()] == ["KICK.S3"])
    assert tags_of(window, "KICK.S3") == "05" and tags_of(window, "SNARE.S3") == ""


def test_cancelling_the_tag_dialog_writes_nothing(qt_app, window, editable_hd, answers, asked_tags):
    open_and_show(qt_app, window, editable_hd)
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    answers.confirm = True
    asked_tags.answer = None
    window._tag_selected()
    assert pump(qt_app, lambda: asked_tags.shown)
    before = editable_hd.read_bytes()
    assert pump(qt_app, lambda: window._pending == 0)
    assert editable_hd.read_bytes() == before


def test_files_that_cant_take_a_tag_are_reported_and_the_rest_are_tagged(qt_app, window, editable_hd, answers, asked_tags):
    open_and_show(qt_app, window, editable_hd)
    kick = next(f for f in window._volume_files if f.name == "KICK.S3")
    window.table.select_file(window._volume, kick.index)
    assert pump(qt_app, lambda: len(window._selected_rows()) == 1)
    answers.confirm = True
    asked_tags.answer = ([1, 2, 3, 4], [])
    window._tag_selected()
    assert pump(qt_app, lambda: tags_of(window, "KICK.S3") == "01 02 03 04")
    assert pump(qt_app, lambda: [f.name for _, f in window.table.selected_rows()] == ["KICK.S3"] and window._pending == 0)

    window.table.selectAll()
    assert pump(qt_app, lambda: len(window._selected_rows()) == 2)
    asked_tags.answer = ([5], [])  # (the stand-in doesn't stop a fifth tag as the real dialog does: the engine does)
    window._tag_selected()
    assert pump(qt_app, lambda: any(w[0] == "Some tags weren't changed" for w in answers.warnings))
    warning = next(w for w in answers.warnings if w[0] == "Some tags weren't changed")
    assert len(warning[2]) == 1 and warning[2][0].startswith("KICK.S3:")
    assert tags_of(window, "KICK.S3") == "01 02 03 04" and tags_of(window, "SNARE.S3") == "05"


def test_the_file_menu_has_the_entry_and_s900_files_dont(qt_app, window, editable_hd, images, answers):
    open_and_show(qt_app, window, editable_hd)
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    menu = window._file_menu(window._selected_rows())
    assert next(a for a in menu.actions() if a.text() == "Tags…").isEnabled()
    window.table.selectAll()
    assert pump(qt_app, lambda: len(window._selected_rows()) == 2)
    assert next(a for a in window._file_menu(window._selected_rows()).actions() if a.text() == "Tag Files…").isEnabled()

    window.close_images()
    window.open_images([str(images["fl9"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    window.tree.reveal_volume(vol(1))
    assert pump(qt_app, lambda: window._volume is not None and window.table.rowCount() >= 1)
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    window._update_edit_actions()
    assert not window.act_tag_files.isEnabled()
    assert not next(a for a in window._file_menu(window._selected_rows()).actions() if a.text() == "Tags…").isEnabled()


def test_an_old_akaiutil_leaves_tag_editing_off_with_a_reason(qt_app, window, editable_hd, answers, monkeypatch):
    open_and_show(qt_app, window, editable_hd)
    monkeypatch.setattr(AkaiEngine, "can_edit_tags", property(lambda self: False))
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    window._update_edit_actions()
    assert not window.act_tag_files.isEnabled() and "4.7" in window.act_tag_files.toolTip()


def test_the_details_link_opens_the_tag_dialog(qt_app, window, editable_hd, answers, asked_tags):
    open_and_show(qt_app, window, editable_hd)
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    assert pump(qt_app, lambda: "Edit Tags…" in window.info.toPlainText())
    answers.confirm = True
    asked_tags.answer = ([2], [])
    window._on_details_link(describe.TAGS_LINK)
    assert pump(qt_app, lambda: "Changed the tags of 1 file." in window.statusBar().currentMessage())
    assert pump(qt_app, lambda: "Tag 2" in window.info.toPlainText() or "TAG B" in window.info.toPlainText())


# --------------------------------------------------------------------------------- renaming a partition's tags
@pytest.fixture
def asked_names(monkeypatch):
    """Stand-in for the Rename Tags dialog: records the names it was shown and answers {number: name}, or None for Cancel."""

    class Asked:
        answer = None
        shown: list = []

    a = Asked()
    a.shown = []
    monkeypatch.setattr(MainWindow, "_ask_tag_names", lambda self, part, names: (a.shown.append((part.name, dict(names))), a.answer)[1])
    return a


def test_the_names_dialog_lists_the_names_and_reports_what_was_changed(qt_app):
    names = {n: f"TAG {chr(64 + n)}" for n in range(1, 27)}
    names[3] = ""
    d = edit_dialogs.TagNamesDialog(None, "A", names)
    ok = d.buttons.button(d.buttons.StandardButton.Ok)
    assert d._edits[1].text() == "TAG A" and d._edits[3].text() == "" and d._edits[3].placeholderText() == "Tag 3"
    assert d.changes == {} and not ok.isEnabled()
    d._edits[1].setText("kicks")  # (setText isn't the user typing, so nothing tidies it: the dialog tidies on the way out)
    d._edits[3].setText("bass_line")
    assert d.changes == {1: "KICKS", 3: "BASS LINE"} and ok.isEnabled()
    d._edits[1].setText("tag a")  # back to what it was
    assert d.changes == {3: "BASS LINE"}
    d.deleteLater()


def test_the_names_dialog_wont_take_a_blank_or_bad_name(qt_app):
    d = edit_dialogs.TagNamesDialog(None, "A", {n: f"TAG {chr(64 + n)}" for n in range(1, 27)})
    ok = d.buttons.button(d.buttons.StandardButton.Ok)
    d._edits[2].setText("")
    assert not ok.isEnabled() and d.problem.text().startswith("Tag 2:")
    d._edits[2].setText("FINE")
    assert ok.isEnabled() and d.problem.text() == ""
    d._edits[4].setText("a/b")
    assert not ok.isEnabled() and "Tag 4" in d.problem.text()
    d.deleteLater()


def test_default_names_fill_in_tag_a_to_z(qt_app):
    d = edit_dialogs.TagNamesDialog(None, "A", {n: "X" for n in range(1, 27)})
    next(b for b in d.buttons.buttons() if b.text() == "Default Names").click()
    assert d._edits[1].text() == "TAG A" and d._edits[26].text() == "TAG Z"
    assert len(d.changes) == 26
    d.deleteLater()


def test_rename_tags_from_the_edit_menu(qt_app, window, editable_hd, answers, asked_names):
    open_and_show(qt_app, window, editable_hd)  # (a volume is selected: its partition's tags are the ones renamed)
    window._update_edit_actions()
    assert window.act_rename_tags.isEnabled()
    answers.confirm = True
    asked_names.answer = {1: "KICKS", 2: "SNARES"}
    window.act_rename_tags.trigger()
    assert pump(qt_app, lambda: window._writable and "Renamed 2 tags of partition A" in window.statusBar().currentMessage())
    assert asked_names.shown[0][0] == "A" and asked_names.shown[0][1][1] == "TAG A" and len(asked_names.shown[0][1]) == 26
    assert pump(qt_app, lambda: window._engine.partition_tag_names(window.tree.partition(0, "A"))[1] == "KICKS")
    assert answers.warnings == []


def test_a_tagged_file_is_listed_under_the_new_name(qt_app, window, editable_hd, answers, asked_tags, asked_names):
    open_and_show(qt_app, window, editable_hd)
    window.table.selectRow(0)
    assert pump(qt_app, lambda: window._selected_rows())
    answers.confirm = True
    asked_tags.answer = ([2], [])
    window._tag_selected()
    assert pump(qt_app, lambda: "Changed the tags of 1 file." in window.statusBar().currentMessage())
    assert pump(qt_app, lambda: window._pending == 0 and window._writing == 0)
    asked_names.answer = {2: "SNARES"}
    assert pump(qt_app, lambda: window.tree.current_object() is not None)  # (the tree puts its selection back once it has reloaded)
    window._rename_tags()
    assert pump(qt_app, lambda: "Renamed 1 tag of partition A" in window.statusBar().currentMessage())
    assert pump(qt_app, lambda: any("SNARES" in window.table.item(r, c).text() for r in range(window.table.rowCount()) for c in range(window.table.columnCount()) if window.table.item(r, c)))


def test_the_partition_menu_offers_rename_tags_only_where_there_are_tags(qt_app, window, editable_hd, images, answers):
    open_and_show(qt_app, window, editable_hd)
    texts = lambda menu: [a.text() for a in menu.actions() if not a.isSeparator()]  # noqa: E731
    assert "Rename Tags…" in texts(window._tree_menu(window.tree.partition(0, "A")))
    window.close_images()
    window.open_images([str(images["fl1"])])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    assert pump(qt_app, lambda: window._pending == 0)
    part = window._engine.list_partitions(window._engine.list_disks()[0])[0]  # (a floppy's one partition isn't shown in the tree)
    assert part.type.startswith("FL") and "Rename Tags…" not in texts(window._tree_menu(part))
