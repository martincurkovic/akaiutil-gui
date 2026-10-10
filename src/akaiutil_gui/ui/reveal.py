"""Show a file in the system's file manager (Finder / Explorer / whatever the Linux desktop uses)."""

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def label() -> str:
    if sys.platform == "darwin":
        return "Reveal in Finder"
    if sys.platform == "win32":
        return "Show in Explorer"
    return "Show in File Manager"


def reveal(path: str) -> bool:
    """Open the folder holding `path` with the file selected, where the system can do that. False if nothing could be opened."""
    target = Path(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
            return True
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{target}"])  # (explorer exits 1 even when it works: don't check)
            return True
        if shutil.which("xdg-open"):  # QDesktopServices can report success on Linux while doing nothing
            subprocess.Popen(["xdg-open", str(target.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    except OSError:
        pass
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))


def show_log_file() -> None:
    """Show the log in the file manager (or the folder it will be in, if nothing has been logged yet)."""
    from akaiutil_gui import logsetup

    path = logsetup.log_path()
    if not (path.exists() and reveal(str(path))):
        path.parent.mkdir(parents=True, exist_ok=True)
        reveal(str(path.parent / "."))
