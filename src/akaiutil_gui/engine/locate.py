"""Finding the akaiutil executable."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from akaiutil_gui import paths

EXE_NAME = "akaiutil.exe" if sys.platform == "win32" else "akaiutil"

# <repo>/build/bin, where scripts/get_akaiutil.py puts it. Only meaningful
# when running from a source checkout.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def default_install_dir() -> Path:
    """Where fetch.install() puts the binary.

    From a source checkout: <repo>/build/bin. In a built app (which must never be written to):
    a per-user folder.
    """
    if paths.is_frozen():
        return paths.user_data_dir() / "bin"
    return _REPO_ROOT / "build" / "bin"


def bundled_binary() -> Path | None:
    """The akaiutil shipped inside a built app (<app folder>/bin/akaiutil), if there is one."""
    folder = paths.app_dir()
    if folder is None:
        return None
    path = folder / "bin" / EXE_NAME
    if path.is_file() and not os.access(path, os.X_OK):
        try:  # file-copying build steps don't always keep the executable bit
            path.chmod(path.stat().st_mode | 0o111)
        except OSError:
            pass
    return path


def candidates(configured: str | None = None) -> list[Path]:
    """Where to look, in order: a path the user chose, $AKAIUTIL_BIN, the copy bundled with
    the app, one the app downloaded, then whatever is on PATH."""
    found: list[Path] = []
    if configured:
        found.append(Path(configured))
    if os.environ.get("AKAIUTIL_BIN"):
        found.append(Path(os.environ["AKAIUTIL_BIN"]))
    bundled = bundled_binary()
    if bundled is not None:
        found.append(bundled)
    found.append(default_install_dir() / EXE_NAME)
    on_path = shutil.which("akaiutil")
    if on_path:
        found.append(Path(on_path))
    return found


def find_akaiutil(configured: str | None = None) -> Path | None:
    for path in candidates(configured):
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def binary_version(path: Path) -> str | None:
    """The version akaiutil says it is ("4.6.8"), read from the banner `akaiutil -h` prints before it exits; None if
    it can't be run or says nothing recognisable."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash on Windows
    try:
        result = subprocess.run([str(path), "-h"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"Rev\.\s*([\w.]+)", result.stdout + result.stderr)
    return found.group(1) if found else None
