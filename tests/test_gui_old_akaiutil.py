"""An akaiutil older than 4.7 is mentioned once for each version, with an offer of the new one (offscreen, real akaiutil)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from akaiutil_gui import settings
from akaiutil_gui.ui.main_window import MainWindow
from test_gui_editing import pump, window  # noqa: F401  (fixture)


def old_akaiutil(folder, real, version):
    """A program that says it is akaiutil `version` (or says nothing, for None) and otherwise is the real one."""
    path = folder / f"akaiutil-{version}"
    says = f'echo "File Manager, Rev. {version}"' if version else "echo usage"
    path.write_text(f'#!/bin/sh\nif [ "$1" = "-h" ]; then {says}; exit 0; fi\nexec "{real}" "$@"\n')
    path.chmod(0o755)
    return path


@pytest.fixture
def offers(monkeypatch):
    """Stands in for the question and the download: what was asked, what is answered, and what was downloaded."""

    class Offers:
        answer = False
        asked: list = []
        downloads: list = []

    o = Offers()
    o.asked, o.downloads = [], []
    monkeypatch.setattr(MainWindow, "_ask_update_akaiutil", lambda self, version, binary: (o.asked.append((version, str(binary))), o.answer)[1])
    monkeypatch.setattr(MainWindow, "_download_akaiutil", lambda self, then: o.downloads.append(then))
    return o


def use(binary_path):
    settings.set_akaiutil_path(str(binary_path))


def opened(qt_app, window, image):  # noqa: F811
    window.open_images([str(image)])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1 and window._pending == 0)
    pump(qt_app, lambda: False, timeout=0.3)  # (the version is read on a worker: let its answer arrive)


def test_an_old_akaiutil_is_mentioned_once_and_not_again_for_the_same_version(qt_app, window, binary, editable_hd, tmp_path, offers):
    old = old_akaiutil(tmp_path, binary, "4.6.8")
    use(old)
    opened(qt_app, window, editable_hd)
    assert pump(qt_app, lambda: offers.asked) and offers.asked == [("4.6.8", str(old))]
    assert settings.get_old_akaiutil_noticed() == "4.6.8"
    opened(qt_app, window, editable_hd)
    assert len(offers.asked) == 1  # (told once is enough)
    assert offers.downloads == []  # (it was refused)


def test_another_old_version_is_mentioned_again(qt_app, window, binary, editable_hd, tmp_path, offers):
    use(old_akaiutil(tmp_path, binary, "4.6.8"))
    opened(qt_app, window, editable_hd)
    assert pump(qt_app, lambda: len(offers.asked) == 1)
    use(old_akaiutil(tmp_path, binary, "4.6.7"))
    opened(qt_app, window, editable_hd)
    assert pump(qt_app, lambda: [v for v, _ in offers.asked] == ["4.6.8", "4.6.7"])


def test_saying_yes_downloads_the_new_one_and_says_how_to_use_it(qt_app, window, binary, editable_hd, tmp_path, offers):
    offers.answer = True
    use(old_akaiutil(tmp_path, binary, "4.6.8"))
    opened(qt_app, window, editable_hd)
    assert pump(qt_app, lambda: len(offers.downloads) == 1)
    offers.downloads[0]()  # (what is done once the download is in place)
    assert "4.7 is ready" in window.statusBar().currentMessage() and "again" in window.statusBar().currentMessage()


def test_akaiutil_4_7_is_not_mentioned(qt_app, window, binary, editable_hd, offers):
    use(binary)
    opened(qt_app, window, editable_hd)
    assert offers.asked == [] and settings.get_old_akaiutil_noticed() is None


def test_a_program_that_does_not_say_its_version_is_left_alone(qt_app, window, binary, editable_hd, tmp_path, offers):
    use(old_akaiutil(tmp_path, binary, None))
    opened(qt_app, window, editable_hd)
    assert offers.asked == []  # (there is no number to name, and nothing to say it is old)


def test_the_question_names_the_versions_and_the_program(qt_app, window, monkeypatch):  # noqa: F811
    seen = []

    def exec_(self):
        seen.append((self.text(), self.informativeText(), [b.text() for b in self.buttons()]))

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[0])
    assert window._ask_update_akaiutil("4.6.8", "/opt/akaiutil") is True
    text, detail, buttons = seen[0]
    assert text == "This akaiutil is version 4.6.8."
    assert "editing tags" in detail and "4.7" in detail and "/opt/akaiutil" in detail
    assert buttons == ["Download 4.7", "Not Now"]
