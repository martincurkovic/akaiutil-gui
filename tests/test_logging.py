"""The log file: what it records, what it leaves out, and that a broken log never breaks the app."""

import logging
import sys
import threading

import pytest

from akaiutil_gui import logsetup
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.ui import tasks


@pytest.fixture
def log(tmp_path):
    path = logsetup.setup(directory=tmp_path / "logs")
    yield path
    logsetup.shutdown()


def text(path):
    for h in logging.getLogger("akaiutil_gui").handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


def test_setup_writes_to_a_log_file_with_a_rotating_size_limit(log, tmp_path):
    assert log == tmp_path / "logs" / "akaiutil-gui.log"
    logging.getLogger("akaiutil_gui.test").info("hello log")
    assert "INFO" in text(log) and "akaiutil_gui.test: hello log" in text(log)
    handler = logsetup._handler
    assert (handler.maxBytes, handler.backupCount) == (logsetup.MAX_BYTES, logsetup.BACKUPS)


def test_commands_are_only_logged_when_verbose_is_on(log):
    logger = logging.getLogger("akaiutil_gui.engine.session")
    logger.debug("> dir /")
    assert "> dir /" not in text(log) and not logsetup.is_verbose()
    logsetup.set_verbose(True)
    logger.debug("> dir /disk0")
    assert "> dir /disk0" in text(log) and logsetup.is_verbose()
    logsetup.set_verbose(False)
    logger.debug("> dir /disk1")
    assert "> dir /disk1" not in text(log)


def test_a_log_that_cant_be_written_is_not_an_error(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    assert logsetup.setup(directory=blocker / "logs") is None  # (a folder can't be made inside a file)
    logging.getLogger("akaiutil_gui.test").info("still fine")


def test_start_up_details_say_what_ran_on_what(log):
    logsetup.log_startup("/usr/local/bin/akaiutil")
    t = text(log)
    assert "akaiutil-gui" in t and "starting" in t and "Python" in t and "akaiutil: /usr/local/bin/akaiutil" in t


def test_an_uncaught_exception_is_logged_with_its_traceback(log):
    import contextlib
    import io

    saved = sys.excepthook, threading.excepthook
    try:
        logsetup.install_exception_hooks()
        try:
            raise ValueError("boom in the app")
        except ValueError:
            exc = sys.exc_info()
        with contextlib.redirect_stderr(io.StringIO()):  # (the hook then calls the normal one, which prints)
            sys.excepthook(*exc)
    finally:
        sys.excepthook, threading.excepthook = saved
    t = text(log)
    assert "CRITICAL" in t and "uncaught exception" in t and "ValueError: boom in the app" in t and "Traceback" in t


def test_the_crash_log_gets_a_header_for_each_run(tmp_path):
    import faulthandler

    try:
        path = logsetup.enable_crash_log(tmp_path / "logs")
        assert path == tmp_path / "logs" / "crash.log" and faulthandler.is_enabled()
        assert "started" in path.read_text(encoding="utf-8")
    finally:
        faulthandler.disable()


# ---------------------------------------------------------------- what the engine and tasks put in it
def test_the_engine_logs_what_it_does_and_every_command_when_verbose(binary, editable_hd, log):
    logsetup.set_verbose(True)
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    try:
        part = eng.list_partitions(eng.list_disks()[0])[0]
        vol = eng.list_volumes(part)[1]
        eng.rename_volume(vol, "RENAMED")
    finally:
        eng.close()
    t = text(log)
    assert "starting akaiutil read-only" in t and "starting akaiutil read/write" in t
    assert f"rename volume {vol.name}" in t and "-> RENAMED" in t
    assert "> dir /" in t and "> renvol" in t and "< " in t


def test_without_verbose_the_commands_stay_out_but_the_actions_are_there(binary, editable_hd, log):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    try:
        part = eng.list_partitions(eng.list_disks()[0])[0]
        eng.rename_volume(eng.list_volumes(part)[1], "QUIET")
    finally:
        eng.close()
    t = text(log)
    assert "-> QUIET" in t and "> renvol" not in t and "> dir" not in t


def test_a_failed_task_is_logged_briefly_for_expected_errors_and_in_full_for_bugs(qt_app, log):
    from akaiutil_gui.engine.session import EngineError

    done = []

    def boom_expected():
        raise EngineError("There is already a volume called X.")

    def boom_bug():
        raise KeyError("oops")

    tasks.run_async(boom_expected, None, done.append)
    tasks.run_async(boom_bug, None, done.append)
    import time

    end = time.time() + 5
    while len(done) < 2 and time.time() < end:
        qt_app.processEvents()
        time.sleep(0.01)
    t = text(log)
    assert "WARNING" in t and "already a volume called X" in t
    assert "ERROR" in t and "unexpected KeyError" in t and "Traceback" in t
    assert t.count("Traceback") == 1  # (only the bug got one)
