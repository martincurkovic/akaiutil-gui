"""The user manual, the About window and the Help menu."""

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLabel

from akaiutil_gui import __version__, settings, updates
from akaiutil_gui.engine.models import FileEntry, Volume
from akaiutil_gui.ui import about_dialog, edit_dialogs, manual_dialog, theme
from akaiutil_gui.ui.ascii_logo import LOGO

REPO_ROOT = Path(__file__).resolve().parent.parent
MANUAL = manual_dialog.MANUAL_PATH.read_text(encoding="utf-8")


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
    w.show()
    yield w
    w.close()


# ------------------------------------------------------------------------- the markdown
def test_every_link_inside_the_manual_goes_somewhere():
    headings = {manual_dialog.heading_slug(m.group(1).strip()) for m in re.finditer(r"^#{1,6} (.+)$", MANUAL, re.M)}
    links = re.findall(r"\]\(#([^)]+)\)", MANUAL)
    assert links, "the contents list has no links"
    assert [slug for slug in links if slug not in headings] == []


def test_every_screenshot_in_the_manual_exists_and_is_a_real_picture():
    from PySide6.QtGui import QPixmap

    names = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", MANUAL)
    assert len(names) >= 7
    for name in names:
        pixmap = QPixmap(str(manual_dialog.HELP_DIR / name))
        assert not pixmap.isNull() and pixmap.width() >= 300, name


def test_the_manual_covers_every_menu_item_it_should():
    for phrase in ("Allow Editing", "Sort Files by Name", "Import Files", "New Disk Image", "Format Disk", "Add Disk Image",
                   "Close Disk Image", "Copy Files", "Paste Files", "User Manual", "Check for Updates"):
        assert phrase in MANUAL, phrase


def test_the_shortcuts_the_manual_lists_are_the_real_ones(qt_app, window):
    expected = {
        "Open": window.act_open, "Add": window.act_add, "New": window.act_new_image, "Close All": window.act_close,
        "Close Disk": window.act_close_disk, "Export": window.act_export, "Import": window.act_import,
    }
    shortcuts = {name: act.shortcut().toString(QKeySequence.SequenceFormat.PortableText) for name, act in expected.items()}
    assert shortcuts == {"Open": "Ctrl+O", "Add": "Ctrl+Shift+O", "New": "Ctrl+N", "Close All": "Ctrl+W", "Close Disk": "Ctrl+Shift+W", "Export": "Ctrl+E", "Import": "Ctrl+I"}
    for keys in ("Ctrl+O", "Ctrl+Shift+O", "Ctrl+N", "Ctrl+W / Ctrl+Shift+W", "Ctrl+E / Ctrl+Shift+E", "Ctrl+I"):
        assert keys in MANUAL


# --------------------------------------------------------------------------- the dialog
def test_the_manual_dialog_shows_the_manual_with_its_pictures_fitted(qt_app):
    d = manual_dialog.ManualDialog()
    d.show()
    assert d.loaded and "User Manual" in d.viewer.toPlainText()
    images = []
    block = d.viewer.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if it.fragment().isValid() and fmt.isImageFormat():
                images.append(fmt.toImageFormat())
            it += 1
        block = block.next()
    assert len(images) == MANUAL.count("![")
    assert all(0 < i.width() <= d._natural_size(i.name())[0] for i in images)  # never bigger than on screen
    d._fit_images(150)  # a narrow window shrinks them...
    assert all(0 < fmt.width() <= 150 for fmt in _images(d))
    d._fit_images(5000)  # ...and a wide one grows them back, to their own size and no further
    assert all(fmt.width() == d._natural_size(fmt.name())[0] for fmt in _images(d))


def _images(d):
    found = []
    block = d.viewer.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if it.fragment().isValid() and fmt.isImageFormat():
                found.append(fmt.toImageFormat())
            it += 1
        block = block.next()
    return found


def test_the_headings_can_be_jumped_to(qt_app):
    d = manual_dialog.ManualDialog()
    anchors = set()
    block = d.viewer.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            if it.fragment().isValid():
                anchors.update(it.fragment().charFormat().anchorNames())
            it += 1
        block = block.next()
    assert {"getting-started", "sorting-a-volume", "keyboard-shortcuts", "troubleshooting"} <= anchors


def test_find_in_page_highlights_steps_through_and_closes(qt_app):
    d = manual_dialog.ManualDialog()
    d.show()
    assert not d.search_bar.isVisible()
    d.open_search()
    assert d.search_bar.isVisible()
    d.search_input.setText("sampler")
    n = len(d._matches)
    assert n > 3 and d.results_label.text() == f"1 of {n}"
    assert len(d.viewer.extraSelections()) == n
    d.find_next()
    assert d.results_label.text() == f"2 of {n}"
    d.find_previous()
    d.find_previous()
    assert d.results_label.text() == f"{n} of {n}"  # it wraps round
    d.search_input.setText("zzzqqqxxx")
    assert d.results_label.text() == "0 of 0" and d.viewer.extraSelections() == []
    d.close_search()
    assert not d.search_bar.isVisible()


def test_a_missing_manual_says_so_instead_of_crashing(qt_app, monkeypatch, tmp_path):
    monkeypatch.setattr(manual_dialog, "MANUAL_PATH", tmp_path / "nope.md")
    d = manual_dialog.ManualDialog()
    assert not d.loaded and "Couldn't load the manual" in d.viewer.toPlainText()


def test_the_help_menu_opens_one_reused_manual_window(qt_app, window):
    help_menu = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Help")
    assert [a.text().replace("&", "") for a in help_menu.actions() if not a.isSeparator()] == ["User Manual…", "About akaiutil-GUI…", "Show Log File", "Check for Updates…"]
    window.act_manual.trigger()
    first = window._manual_dialog
    assert first.isVisible() and first.windowTitle() == "User Manual"
    window.act_manual.trigger()
    assert window._manual_dialog is first


# -------------------------------------------------------------------------------- about
def test_the_logo_is_the_users_file_byte_for_byte():
    source = REPO_ROOT / "akaiutil-gui.txt"
    if not source.exists():
        pytest.skip("akaiutil-gui.txt isn't in this checkout")
    assert LOGO.strip("\n") == source.read_text(encoding="utf-8").rstrip("\n")


def test_the_about_window_has_the_logo_version_and_buttons(qt_app, window):
    d = about_dialog.AboutDialog(window)
    d.show()
    assert d.logo_label.text() == LOGO.strip("\n") and "█" in d.logo_label.text()
    texts = [label.text() for label in d.findChildren(QLabel)]
    assert f"Version {__version__}" in texts
    assert any("Klaus Michael Indlekofer" in t for t in texts) and any("GPL-2.0-or-later" in t for t in texts)
    assert (updates.REPO and any(updates.REPO in t for t in texts)) or not updates.REPO
    assert d.check_updates_button.text() == "Check for Updates" and d.licences_button.text() == "Licences"
    assert d.minimumSize() == d.maximumSize()  # fixed size, like AKAISDS's


def test_the_about_window_checks_for_updates_and_shows_progress(qt_app, window, monkeypatch):
    d = about_dialog.AboutDialog(window)
    d.show()
    calls = []
    monkeypatch.setattr(d._update_runner, "start", lambda manual, on_finished=None: calls.append((manual, on_finished)))
    d.check_updates_button.click()
    assert calls[0][0] is True and not d.check_updates_button.isEnabled() and d.update_progress.isVisible()
    calls[0][1]()  # the check finishes
    assert d.check_updates_button.isEnabled() and not d.update_progress.isVisible()


def test_the_licences_button_opens_the_notices(qt_app, window, monkeypatch):
    opened = []
    monkeypatch.setattr(about_dialog.NoticesDialog, "exec", lambda self: opened.append(self.windowTitle()) or 0)
    d = about_dialog.AboutDialog(window)
    d.licences_button.click()
    assert opened == ["Third-party notices"]


def test_the_help_menu_about_item_opens_the_window(qt_app, window, monkeypatch):
    shown = []
    monkeypatch.setattr(about_dialog.AboutDialog, "exec", lambda self: shown.append(self.windowTitle()) or 0)
    window._about()
    assert shown == ["About akaiutil-GUI"]


# ------------------------------------------------------------------------- sort dialog
def test_the_sort_dialog_wraps_its_text_and_has_a_fixed_size(qt_app):
    vol = Volume(0, "A", 1, "A VOLUME WITH A QUITE LONG NAME", "-", "", "", "S3000")
    files = [FileEntry(1, "A.S3", 1, "0x1", "17"), FileEntry(2, "PROG.P3", 1, "0x2", "17")]
    d = edit_dialogs.SortDialog(None, vol, files)
    d.show()
    assert d.minimumSize() == d.maximumSize()  # fixed
    labels = [label for label in d.findChildren(QLabel)]
    assert len(labels) == 3 and all(label.wordWrap() for label in labels)
    for label in labels:  # every line is inside its label: none is cut off at the bottom
        assert label.height() >= label.heightForWidth(label.width())
        assert label.width() == edit_dialogs.SortDialog.TEXT_WIDTH
    assert max(label.heightForWidth(label.width()) for label in labels) > label.fontMetrics().height() * 2  # the long ones really wrap


def test_the_sort_dialog_without_programs_is_just_as_tidy(qt_app):
    d = edit_dialogs.SortDialog(None, Volume(0, "A", 1, "DRUMS", "-", "", "", "S3000"), [FileEntry(1, "A.S3", 1, "0x1", "17")])
    d.show()
    assert d.minimumSize() == d.maximumSize() and len(d.findChildren(QLabel)) == 1
