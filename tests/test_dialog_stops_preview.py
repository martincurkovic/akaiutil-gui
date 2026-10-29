"""A dialog opening over the window stops whatever the preview is playing."""

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from akaiutil_gui.ui import edit_dialogs
from test_gui_editing import answers, window  # noqa: F401  (fixtures)


@pytest.fixture
def stops(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.preview, "stop", lambda: calls.append("stop"))
    return calls


def test_a_modal_dialog_taking_the_focus_stops_the_preview(window, stops, monkeypatch, qt_app):
    dialog = QDialog()
    dialog.setModal(True)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: dialog))
    window._on_focus_window(None)
    assert stops == ["stop"]


def test_the_window_or_a_non_modal_window_taking_the_focus_does_not(window, stops, monkeypatch, qt_app):
    other = QDialog()
    for active in (window, other):
        monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda a=active: a))
        window._on_focus_window(None)
    assert stops == []


def test_opening_the_sample_settings_dialog_stops_the_preview(window, stops, monkeypatch):
    monkeypatch.setattr(edit_dialogs.SampleSettingsDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    from akaiutil_gui import akai_header

    settings = akai_header.read_settings(akai_header.build_header("S3000", 44100, 1000))
    from akaiutil_gui.engine.models import FileEntry

    assert window._ask_sample_settings(FileEntry(1, "KICK.S3", 100, "0x0005", "17.00"), settings) is None
    assert stops == ["stop"]
