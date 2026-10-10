"""Run an update check, then react. Shared by the Help menu, the Settings dialog and the startup check, so the
check/throttle/skip-list policy is written once. A port of AKAISDS's ui/update_helper.py."""

import logging
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from akaiutil_gui import __version__, settings, updates

from .update_dialog import UpdateAvailableDialog

log = logging.getLogger(__name__)
CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # how often the automatic startup check is allowed to run


class UpdateCheckWorker(QThread):
    """Runs updates.check_for_update() off the GUI thread. One-shot and throwaway."""

    succeeded = Signal(object)  # UpdateInfo | None
    unavailable = Signal(str)  # this build can't check (no repository configured)
    failed = Signal(str)

    def run(self) -> None:
        try:
            info = updates.check_for_update()
        except updates.UpdatesNotConfigured as exc:
            self.unavailable.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - network errors, bad JSON...
            log.error("update check failed", exc_info=True)  # also logged so "it never works for me" has a trace
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(info)


class UpdateCheckRunner:
    """Owns one in-flight worker for a single window. `parent` parents the thread and any dialogs."""

    def __init__(self, parent):
        self._parent = parent
        self._worker: UpdateCheckWorker | None = None
        self._dialog = None  # keeps the UpdateAvailableDialog alive while it's shown

    def is_running(self) -> bool:
        return self._worker is not None

    def wait(self) -> None:
        """Block until the current check's thread has stopped: call before the owner can be garbage collected, since
        a QThread whose Python wrapper is collected mid-run crashes the process."""
        if self._worker is not None:
            self._worker.wait()

    def start(self, manual: bool, on_finished=None) -> None:
        if self._worker is not None:
            return  # a check is already in flight
        if updates.is_dev_build():
            if manual:
                QMessageBox.information(
                    self._parent,
                    "Development Build",
                    f"This is a development build ({__version__}), so there's no release to compare it with.",
                )
            if on_finished:
                on_finished()
            return

        worker = UpdateCheckWorker(self._parent)
        worker.succeeded.connect(lambda info: self._on_succeeded(info, manual))
        worker.unavailable.connect(lambda message: self._on_unavailable(message, manual))
        worker.failed.connect(lambda message: self._on_failed(message, manual))

        def cleanup() -> None:
            self._worker = None
            if on_finished:
                on_finished()

        worker.finished.connect(cleanup)
        self._worker = worker
        worker.start()

    def _on_succeeded(self, info, manual: bool) -> None:
        settings.set_last_update_check(time.time())
        if info is None:
            if manual:
                QMessageBox.information(self._parent, "No Updates Available", f"You're up to date (version {__version__}).")
            return
        if not manual and info.version == settings.get_skipped_update_version():
            return  # the user said "skip this version"; only a manual check brings it back
        self._dialog = UpdateAvailableDialog(info, self._parent)
        self._dialog.show()

    def _on_unavailable(self, message: str, manual: bool) -> None:
        if manual:
            QMessageBox.information(self._parent, "Update Checking Isn't Set Up", message)

    def _on_failed(self, message: str, manual: bool) -> None:
        if manual:
            QMessageBox.warning(self._parent, "Update Check Failed", f"Could not check for updates:\n{message}")
        # silent on an automatic check: no need to nag about a flaky connection
