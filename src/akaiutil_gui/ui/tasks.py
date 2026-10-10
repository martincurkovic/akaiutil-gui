"""Run blocking engine calls on a worker thread and get the result back on
the GUI thread.

Callbacks are delivered through slots of a QObject that lives in the GUI
thread, which is what makes Qt queue them across threads (a bare lambda
connected to a signal would run on the worker thread instead).
"""

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)
_live: set["_Relay"] = set()  # keeps relays alive until their result arrives


class _Relay(QObject):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, on_done, on_error):
        super().__init__()
        self._on_done = on_done
        self._on_error = on_error
        self.done.connect(self._finish_ok)
        self.failed.connect(self._finish_err)

    @Slot(object)
    def _finish_ok(self, result):
        _live.discard(self)
        if self._on_done is not None:
            self._on_done(result)

    @Slot(str)
    def _finish_err(self, message):
        _live.discard(self)
        if self._on_error is not None:
            self._on_error(message)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], relay: _Relay):
        super().__init__()
        self._fn = fn
        self._relay = relay

    def run(self):
        try:
            result = self._fn()
        except Exception as e:  # reported to the UI, never allowed to kill the pool thread
            log.debug("task failed:\n%s", traceback.format_exc())
            self._relay.failed.emit(str(e) or e.__class__.__name__)
        else:
            self._relay.done.emit(result)


def run_async(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Call from the GUI thread. on_done / on_error also run on the GUI thread."""
    relay = _Relay(on_done, on_error)
    _live.add(relay)
    QThreadPool.globalInstance().start(_Task(fn, relay))
