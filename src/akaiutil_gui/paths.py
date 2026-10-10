"""Where things live: the user's data folder, and (in a built app) the folder the app is in."""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "akaiutil-gui"


def user_data_dir() -> Path:
    """Per-user folder for files the app creates (outside any app bundle, which must stay read-only)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_DIR_NAME


def _compiled():
    """Nuitka's `__compiled__` object, or None when running from source.

    Nuitka (what pyside6-deploy uses) only injects `__compiled__` into modules that mention the name
    in their source, and only a plain name reference counts: looking it up through globals() or
    getattr() is invisible to it and silently gives "not compiled".
    """
    try:
        return __compiled__  # noqa: F821  (defined by Nuitka at build time)
    except NameError:
        return None


def is_frozen() -> bool:
    """True inside a standalone build made by Nuitka (pyside6-deploy)."""
    return _compiled() is not None


def app_dir() -> Path | None:
    """The folder of the running built app's executable, where its bundled files live; None from source.

    In a macOS .app this is Contents/MacOS; in the Linux AppImage, usr/bin; on Windows, the install folder.

    It's derived from the executable itself on purpose. Nuitka's `__compiled__.containing_dir` looks
    like the obvious choice but on macOS it's the folder that *holds* the .app bundle, which is
    one level outside everything the build put in the app.
    """
    if _compiled() is None:
        return None
    return Path(sys.executable or sys.argv[0]).resolve().parent
