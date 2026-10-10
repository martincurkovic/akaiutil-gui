"""The Settings dialog and how the main window uses what it saves."""

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QPushButton

from akaiutil_gui import settings
from akaiutil_gui.audio import preview
from akaiutil_gui.ui import main_window, settings_dialog, theme
from akaiutil_gui.ui.settings_dialog import SettingsDialog

DEVICES = [{"name": "Built-in Speakers", "id": "spk"}, {"name": "USB Interface", "id": "usb"}]


@pytest.fixture(autouse=True)
def two_devices(monkeypatch):
    monkeypatch.setattr(preview, "list_output_devices", lambda: DEVICES)
    monkeypatch.setattr(preview, "device_id_string", lambda d: d["id"])


@pytest.fixture
def dialog(qt_app):
    return SettingsDialog()


class FakeRunner:
    def __init__(self):
        self.calls = []

    def start(self, manual, on_finished=None):
        self.calls.append((manual, on_finished))


def test_a_fresh_install_shows_the_defaults(dialog):
    assert dialog.combo_theme.currentData() == settings.THEME_SYSTEM
    assert dialog.combo_audio_output.currentData() is None and dialog.combo_audio_output.itemText(0) == "(System Default)"
    assert [dialog.combo_audio_output.itemText(i) for i in (1, 2)] == ["Built-in Speakers", "USB Interface"]
    assert dialog.combo_audio_buffer.currentData() == 1024
    assert not dialog.check_auto_play.isChecked() and dialog.check_preview_loops.isChecked()
    assert dialog.edit_export_folder.text() == "" and dialog.check_ask_folder.isChecked() and dialog.check_updates.isChecked()


def test_ok_saves_everything(qt_app, tmp_path):
    d = SettingsDialog()
    d.combo_audio_output.setCurrentIndex(d.combo_audio_output.findData("usb"))
    d.combo_audio_buffer.setCurrentIndex(d.combo_audio_buffer.findData(256))
    d.check_auto_play.setChecked(True)
    d.check_preview_loops.setChecked(False)
    d.edit_export_folder.setText(str(tmp_path))
    d.check_ask_folder.setChecked(False)
    d.check_updates.setChecked(False)
    d.accept()

    assert settings.get_audio_output_device() == "usb" and settings.get_audio_buffer_samples() == 256
    assert settings.get_auto_play() is True and settings.get_preview_loops() is False
    assert settings.get_default_export_folder() == str(tmp_path) and settings.get_ask_export_folder() is False
    assert settings.get_check_updates_on_startup() is False

    again = SettingsDialog()  # and a new dialog shows what was saved
    assert again.combo_audio_output.currentData() == "usb" and again.combo_audio_buffer.currentData() == 256
    assert again.check_auto_play.isChecked() and not again.check_preview_loops.isChecked()
    assert again.edit_export_folder.text() == str(tmp_path) and not again.check_ask_folder.isChecked()


def test_cancel_saves_nothing(dialog):
    dialog.check_auto_play.setChecked(True)
    dialog.combo_audio_buffer.setCurrentIndex(0)
    dialog.reject()
    assert settings.get_auto_play() is False and settings.get_audio_buffer_samples() == 1024


def test_a_saved_device_that_has_gone_shows_as_the_default(qt_app):
    settings.set_audio_output_device("unplugged-interface")
    assert SettingsDialog().combo_audio_output.currentData() is None


def test_an_unusual_saved_buffer_size_is_kept_not_replaced(qt_app):
    settings.set_audio_buffer_samples(777)
    d = SettingsDialog()
    assert d.combo_audio_buffer.currentData() == 777
    d.accept()
    assert settings.get_audio_buffer_samples() == 777


def test_the_theme_applies_when_picked_not_on_ok_and_not_on_open(dialog):
    chosen = []
    dialog.themeChosen.connect(chosen.append)
    assert chosen == []  # opening the dialog (which sets the combo to the saved theme) isn't a choice
    dialog.combo_theme.setCurrentIndex(dialog.combo_theme.findData(settings.THEME_LIGHT))
    assert chosen == []  # changing it programmatically isn't either
    dialog.combo_theme.activated.emit(dialog.combo_theme.currentIndex())  # what a click does
    assert chosen == [settings.THEME_LIGHT]


def test_clear_empties_the_default_folder(dialog, tmp_path):
    dialog.edit_export_folder.setText(str(tmp_path))
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Clear").click()
    assert dialog.edit_export_folder.text() == ""


def test_choosing_a_default_folder(dialog, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Choose…").click()
    assert dialog.edit_export_folder.text() == str(tmp_path)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")  # cancelled
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Choose…").click()
    assert dialog.edit_export_folder.text() == str(tmp_path)


def test_the_akaiutil_card_shows_which_copy_is_used_and_can_reset_it(qt_app, tmp_path, monkeypatch):
    stub = tmp_path / "akaiutil"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    settings.set_akaiutil_path(str(stub))
    located = []
    d = SettingsDialog(locate=lambda: located.append(True))
    assert d.label_akaiutil.text() == str(stub)
    next(b for b in d.findChildren(QPushButton) if b.text() == "Locate…").click()
    assert located == [True]
    next(b for b in d.findChildren(QPushButton) if b.text() == "Use Default").click()
    assert settings.get_akaiutil_path() is None and d.label_akaiutil.text() != str(stub)


def test_check_now_runs_a_manual_check_and_reenables_itself(qt_app):
    runner = FakeRunner()
    d = SettingsDialog(update_runner=runner)
    assert d.label_last_check.text() == "Never checked"
    d.button_check_now.click()
    assert not d.button_check_now.isEnabled() and runner.calls[0][0] is True  # manual
    settings.set_last_update_check(time.time())
    runner.calls[0][1]()  # the runner reports it finished
    assert d.button_check_now.isEnabled() and d.label_last_check.text().startswith("Last checked ")


# ------------------------------------------------------------ the main window uses it
@pytest.fixture
def win(qt_app):
    w = main_window.MainWindow()
    yield w
    w.close()


def test_settings_and_update_checks_are_in_the_menus(win):
    from PySide6.QtGui import QAction

    file_actions = {a.text().replace("&", ""): a for a in next(a.menu() for a in win.menuBar().actions() if a.text() == "&File").actions()}
    assert file_actions["Settings…"].menuRole() == QAction.MenuRole.PreferencesRole  # macOS puts it in the app menu
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    assert "Check for Updates…" in [a.text().replace("&", "") for a in help_menu.actions()]


def test_changing_the_theme_in_settings_updates_the_view_menu(win):
    win._set_theme(settings.THEME_LIGHT)
    assert win._theme_actions[settings.THEME_LIGHT].isChecked() and settings.get_theme() == settings.THEME_LIGHT
    win._set_theme(settings.THEME_DARK)
    assert win._theme_actions[settings.THEME_DARK].isChecked() and not win._theme_actions[settings.THEME_LIGHT].isChecked()
    theme.set_theme_preference(settings.THEME_DARK)


def test_exports_go_straight_to_the_default_folder_when_asking_is_off(win, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: pytest.fail("must not ask"))
    settings.set_default_export_folder(str(tmp_path))
    settings.set_ask_export_folder(False)
    assert win._export_destination() == str(tmp_path)


def test_exports_ask_starting_from_the_default_folder_otherwise(win, tmp_path, monkeypatch):
    asked = []
    other = tmp_path / "chosen"
    other.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda parent, title, start: asked.append(start) or str(other))
    settings.set_default_export_folder(str(tmp_path))  # asking is still on
    assert win._export_destination() == str(other) and asked == [str(tmp_path)]
    assert settings.get_last_dir("export") == str(other)  # remembered for next time


def test_a_default_folder_that_no_longer_exists_falls_back_to_asking(win, tmp_path, monkeypatch):
    gone = tmp_path / "deleted"
    asked = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda parent, title, start: asked.append(start) or "")
    settings.set_default_export_folder(str(gone))
    settings.set_ask_export_folder(False)
    assert win._export_destination() is None and len(asked) == 1 and asked[0] != str(gone)


@pytest.mark.parametrize("enabled, hours_ago, scheduled", [(True, 48, True), (True, 2, False), (False, 48, False), (True, None, True)])
def test_the_startup_check_is_throttled_and_can_be_turned_off(win, monkeypatch, enabled, hours_ago, scheduled):
    timers = []
    monkeypatch.setattr(main_window.QTimer, "singleShot", staticmethod(lambda ms, fn: timers.append(ms)))
    settings.set_check_updates_on_startup(enabled)
    if hours_ago is not None:
        settings.set_last_update_check(time.time() - hours_ago * 3600)
    win.schedule_update_check()
    assert bool(timers) is scheduled
    if scheduled:
        assert timers == [2000]  # a moment after startup, never blocking it


def test_backup_and_log_settings_have_defaults_and_keep_what_is_saved():
    assert settings.get_backup_keep_days() == 14 and settings.get_verbose_log() is False
    settings.set_backup_keep_days(30)
    settings.set_verbose_log(True)
    assert settings.get_backup_keep_days() == 30 and settings.get_verbose_log() is True
    settings.set_backup_keep_days(0)
    assert settings.get_backup_keep_days() == 0  # (never delete)
    settings.set_backup_keep_days(-5)
    assert settings.get_backup_keep_days() == 0


def test_the_settings_dialog_shows_and_saves_the_backup_and_log_options(qt_app, monkeypatch):
    from akaiutil_gui import logsetup

    shown = []
    monkeypatch.setattr(settings_dialog.reveal, "show_log_file", lambda: shown.append(1))
    dialog = SettingsDialog()
    assert dialog.spin_keep_days.value() == 14 and not dialog.check_verbose_log.isChecked()
    dialog.spin_keep_days.setValue(0)
    assert dialog.spin_keep_days.text() == "Never"
    dialog.check_verbose_log.setChecked(True)
    dialog.accept()
    assert settings.get_backup_keep_days() == 0 and settings.get_verbose_log()
    again = SettingsDialog()
    assert again.spin_keep_days.value() == 0 and again.check_verbose_log.isChecked()
    next(b for b in again.findChildren(QPushButton) if b.text() == "Show Log File").click()
    assert shown == [1]
    dialog.deleteLater()
    again.deleteLater()


def test_recent_images_are_remembered_newest_first_without_repeats(tmp_path):
    a, b, c = (tmp_path / n for n in ("a.img", "b.img", "c.img"))
    for p in (a, b, c):
        p.write_bytes(b"x")
    assert settings.get_recent_images() == []
    for p in (a, b, c, a):  # (a again: it moves to the top rather than appearing twice)
        settings.add_recent_image(str(p))
    import os

    assert settings.get_recent_images() == [os.path.realpath(p) for p in (a, c, b)]
    settings.remove_recent_image(os.path.realpath(c))
    assert settings.get_recent_images() == [os.path.realpath(p) for p in (a, b)]
    settings.clear_recent_images()
    assert settings.get_recent_images() == []


def test_only_the_newest_few_recent_images_are_kept(tmp_path):
    for i in range(settings.MAX_RECENT_IMAGES + 5):
        settings.add_recent_image(str(tmp_path / f"{i}.img"))
    recent = settings.get_recent_images()
    assert len(recent) == settings.MAX_RECENT_IMAGES and recent[0].endswith(f"{settings.MAX_RECENT_IMAGES + 4}.img")


def test_a_damaged_recent_list_is_just_empty():
    from PySide6.QtCore import QSettings

    QSettings().setValue("recent/images", "this is not json")
    assert settings.get_recent_images() == []
    QSettings().setValue("recent/images", '[1, "ok.img", null]')
    assert settings.get_recent_images() == ["ok.img"]
