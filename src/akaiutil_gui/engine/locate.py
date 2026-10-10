"""Finding the akaiutil executable."""

import os
import shutil
import sys
from pathlib import Path

EXE_NAME = "akaiutil.exe" if sys.platform == "win32" else "akaiutil"

# <repo>/build/bin, where scripts/get_akaiutil.py puts it. Only meaningful
# when running from a source checkout.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def default_install_dir() -> Path:
    """Where fetch.install() puts the binary, and the second place find_akaiutil() looks."""
    return _REPO_ROOT / "build" / "bin"


def candidates(configured: str | None = None) -> list[Path]:
    found: list[Path] = []
    if configured:
        found.append(Path(configured))
    if os.environ.get("AKAIUTIL_BIN"):
        found.append(Path(os.environ["AKAIUTIL_BIN"]))
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
