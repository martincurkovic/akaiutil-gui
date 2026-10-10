"""The log file (Qt-free): what a user sends along with a bug report.

`<app data>/logs/akaiutil-gui.log` (a megabyte, three older ones kept) records what the app did: start-up details, images opened,
edits, backups, and every error. With "Log every akaiutil command" on in Settings it also records the commands sent to akaiutil
and the start of each answer. `crash.log` beside it gets the Python traceback if the program dies in native code (a segfault).

It holds file paths and Akai file names, never audio data.
"""

import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import threading
from pathlib import Path

from . import __version__, paths

LOG_NAME = "akaiutil-gui.log"
CRASH_NAME = "crash.log"
MAX_BYTES = 1_000_000
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_handler: logging.Handler | None = None
_crash_file = None


def log_dir() -> Path:
    """$AKAIUTIL_GUI_LOG_DIR if set (the tests use it), else a `logs` folder in the app's data folder."""
    override = os.environ.get("AKAIUTIL_GUI_LOG_DIR")
    return Path(override) if override else paths.user_data_dir() / "logs"


def log_path() -> Path:
    return log_dir() / LOG_NAME


def setup(verbose: bool = False, directory: Path | None = None) -> Path | None:
    """Start logging to the file (and keep it going to the console). Returns the log file, or None if it can't be written:
    the app carries on without a log file rather than failing to start."""
    global _handler
    folder = Path(directory) if directory is not None else log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder / LOG_NAME, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    logger = logging.getLogger("akaiutil_gui")
    shutdown()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    _handler = handler
    set_verbose(verbose)
    return folder / LOG_NAME


def shutdown() -> None:
    """Stop writing to the file (and let it go: the tests, and Windows, need that)."""
    global _handler
    if _handler is not None:
        logging.getLogger("akaiutil_gui").removeHandler(_handler)
        _handler.close()
        _handler = None


def set_verbose(on: bool) -> None:
    """Verbose also records every command sent to akaiutil; otherwise only what the app did and what went wrong."""
    if _handler is not None:
        _handler.setLevel(logging.DEBUG if on else logging.INFO)


def is_verbose() -> bool:
    return _handler is not None and _handler.level <= logging.DEBUG


def log_startup(akaiutil: str | None = None) -> None:
    """The first lines of a log: enough to know what ran on what."""
    log = logging.getLogger("akaiutil_gui")
    try:
        from PySide6 import __version__ as pyside
        from PySide6.QtCore import qVersion

        qt = f"PySide6 {pyside}, Qt {qVersion()}"
    except ImportError:
        qt = "PySide6 not importable"
    log.info("---- akaiutil-gui %s starting ----", __version__)
    log.info("%s, Python %s, %s", platform.platform(), platform.python_version(), qt)
    log.info("%s; akaiutil: %s", "built app" if paths.is_frozen() else "from source", akaiutil or "not found yet")


def install_exception_hooks() -> None:
    """Write uncaught exceptions (on any thread) to the log with their traceback, then carry on as normal."""
    log = logging.getLogger("akaiutil_gui")
    previous = sys.excepthook

    def hook(kind, value, trace):
        log.critical("uncaught exception", exc_info=(kind, value, trace))
        previous(kind, value, trace)

    sys.excepthook = hook
    previous_thread = threading.excepthook

    def thread_hook(args):
        log.critical("uncaught exception in thread %s", getattr(args.thread, "name", "?"), exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        previous_thread(args)

    threading.excepthook = thread_hook


def enable_crash_log(directory: Path | None = None) -> Path | None:
    """If the program dies in native code (a segfault, an abort) Python can't log it, but faulthandler can write the traceback
    of every thread to crash.log as it goes down. A header line says which run it was."""
    global _crash_file
    folder = Path(directory) if directory is not None else log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / CRASH_NAME
        if _crash_file is not None:
            faulthandler.disable()
            _crash_file.close()
        _crash_file = open(path, "a", encoding="utf-8")
        _crash_file.write(f"--- akaiutil-gui {__version__} started, {platform.platform()} ---\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
        return path
    except (OSError, RuntimeError, ValueError):
        return None
