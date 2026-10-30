"""A direct-to-disk partition shows its takes in the window (offscreen, real akaiutil, the generated DD image)."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from akaiutil_gui import settings
from akaiutil_gui.ui import theme
from akaiutil_gui.ui.disk_tree import _ROLE_OBJ


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("qs")))
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("akaiutil-gui-test")
    app.setApplicationName("test")
    return app


@pytest.fixture
def window(app, binary):
    from akaiutil_gui.ui.main_window import MainWindow

    theme.apply_to_app(app, settings.THEME_DARK)
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()


def pump(app, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def open_dd(app, window, images):
    window.open_images([str(images["dd"])])
    assert pump(app, lambda: window.tree.topLevelItemCount() == 1)
    disk = window.tree.topLevelItem(0)
    assert pump(app, lambda: disk.childCount() == 3)
    return disk


def test_selecting_the_dd_partition_swaps_the_file_table_for_the_take_table(app, window, images):
    disk = open_dd(app, window, images)
    assert window.table.isVisible() and not window.takes.isVisible()

    window.tree.setCurrentItem(disk.child(2))  # DD
    assert pump(app, lambda: window.takes.rowCount() == 2)
    assert window.takes.isVisible() and not window.table.isVisible()
    assert [window.takes.item(r, 1).text() for r in range(2)] == ["MONO", "STEREO"]
    assert [window.takes.item(r, 2).text() for r in range(2)] == ["Mono", "Stereo"]
    assert [window.takes.item(r, 4).text() for r in range(2)] == ["1.00 s", "1.00 s"]
    assert window._files_title_text.startswith("Takes in")
    assert pump(app, lambda: "Direct-to-disk area" in window.info.toPlainText())

    window.tree.setCurrentItem(disk.child(0))  # a sampler partition: back to files
    assert window.table.isVisible() and not window.takes.isVisible()
    assert window.takes.rowCount() == 0


def test_a_selected_take_shows_its_settings_and_none_goes_back_to_the_partition(app, window, images):
    disk = open_dd(app, window, images)
    window.tree.setCurrentItem(disk.child(2))
    assert pump(app, lambda: window.takes.rowCount() == 2)

    window.takes.selectRow(1)
    assert pump(app, lambda: "Direct-to-disk take 2" in window.info.toPlainText())
    text = window.info.toPlainText()
    assert "Stereo" in text and "Fade in" in text and "tnr:" not in text  # rows, not akaiutil's raw lines

    window.takes.clearSelection()
    assert pump(app, lambda: "Direct-to-disk area" in window.info.toPlainText())


def test_searching_while_a_dd_partition_is_selected_shows_the_results_table(app, window, images):
    disk = open_dd(app, window, images)
    window.tree.setCurrentItem(disk.child(2))
    assert pump(app, lambda: window.takes.isVisible())

    window.search.setText("mono")
    assert pump(app, lambda: window.table.isVisible() and not window.takes.isVisible())

    window.search.setText("")  # search over: the takes come back
    assert pump(app, lambda: window.takes.isVisible() and not window.table.isVisible())


def test_selected_takes_export_from_the_menu_actions(app, window, images, tmp_path, monkeypatch):
    disk = open_dd(app, window, images)
    window.tree.setCurrentItem(disk.child(2))
    assert pump(app, lambda: window.takes.rowCount() == 2)
    assert not window.act_export.isEnabled() and not window.act_export_wav.isEnabled()  # nothing selected

    window.takes.selectRow(1)
    assert pump(app, lambda: window.act_export.isEnabled() and window.act_export_wav.isEnabled())
    monkeypatch.setattr(type(window), "_export_destination", lambda self: str(tmp_path))
    window.act_export_wav.trigger()
    assert pump(app, lambda: (tmp_path / "STEREO.wav").exists())
    window.act_export.trigger()
    assert pump(app, lambda: (tmp_path / "STEREO.TK").exists())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["STEREO.TK", "STEREO.wav"]  # just the selected take


def test_the_partition_menu_exports_every_take_as_wav(app, window, images, tmp_path, monkeypatch):
    disk = open_dd(app, window, images)
    dd = disk.child(2).data(0, _ROLE_OBJ)  # the Partition the tree item stands for
    menu = window._tree_menu(dd)
    labels = [a.text() for a in menu.actions()]
    assert "Export Takes as WAV…" in labels and "Export Samples as WAV…" not in labels
    monkeypatch.setattr(type(window), "_export_destination", lambda self: str(tmp_path))
    next(a for a in menu.actions() if a.text() == "Export Takes as WAV…").trigger()
    assert pump(app, lambda: sorted(p.name for p in tmp_path.iterdir()) == ["MONO.wav", "STEREO.wav"])
