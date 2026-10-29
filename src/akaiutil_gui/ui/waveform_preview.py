"""A read-only waveform with the play markers, the loop and a moving playhead, for previewing a sample.

Much smaller than AKAISDS's editing waveform: nothing here is draggable. It uses the same min/max-per-pixel
envelope idea (one pair per pixel column, computed once per load or resize rather than once per paint), done with
numpy so a minute-long sample stays instant.
"""

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from akaiutil_gui.engine.models import Loop

from . import theme

CORNER_RADIUS = 4
PLACEHOLDER = "Select a sample to preview it"


def build_envelope(samples: np.ndarray, width: int) -> tuple[np.ndarray, np.ndarray]:
    """(min, max) per pixel column across `samples`, `width` columns wide.

    Column x covers samples [x*n//width, (x+1)*n//width), and always at least one sample, the same rule AKAISDS's
    WaveformView uses. Ranges are then stretched just enough to touch their neighbour's, so a quiet passage that
    jumps between columns doesn't draw as disconnected dashes.
    """
    n, width = len(samples), max(1, int(width))
    if n == 0:
        zeros = np.zeros(width, dtype=np.int32)
        return zeros, zeros.copy()
    x = np.arange(width)
    lo = (x * n) // width
    if n >= width:  # every column gets its own run of samples, so one reduceat does it
        mins = np.minimum.reduceat(samples, lo).astype(np.int32)
        maxs = np.maximum.reduceat(samples, lo).astype(np.int32)
    else:  # fewer samples than pixels: each column is just one sample
        mins = maxs = samples[lo].astype(np.int32)
        maxs = mins.copy()
    mins, maxs = mins.copy(), maxs.copy()
    for i in range(1, width):  # bridge gaps: extend the edge nearer the gap to touch the previous column
        if mins[i] > maxs[i - 1]:
            mins[i] = maxs[i - 1]
        elif maxs[i] < mins[i - 1]:
            maxs[i] = mins[i - 1]
    return mins, maxs


def x_for_frame(frame: int, width: int, frames: int) -> float:
    if frames <= 1:
        return 0.0
    return frame / (frames - 1) * (width - 1)


def frame_for_x(x: float, width: int, frames: int) -> int:
    if width <= 1 or frames <= 1:
        return 0
    return int(round(min(1.0, max(0.0, x / (width - 1))) * (frames - 1)))


class PreviewWaveform(QWidget):
    seekRequested = Signal(int)  # the frame the user clicked: "play from here"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples: np.ndarray | None = None
        self._start = self._end = 0
        self._loop: Loop | None = None
        self._playhead: int | None = None
        self._envelope: tuple[np.ndarray, np.ndarray] | None = None
        self._envelope_width = 0
        self.setMinimumHeight(88)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(96)
        theme.notifier.changed.connect(self.update)

    # ------------------------------------------------------------------ data
    def set_audio(self, samples: np.ndarray | None, play_start: int = 0, play_end: int | None = None) -> None:
        self._samples = samples
        n = 0 if samples is None else len(samples)
        self._start, self._end = play_start, (n - 1 if play_end is None else play_end)
        self._loop = None
        self._playhead = None
        self._envelope = None
        self.update()

    def clear(self) -> None:
        self.set_audio(None)

    def set_loop(self, loop: Loop | None) -> None:
        self._loop = loop
        self.update()

    def set_playhead(self, frame: int | None) -> None:
        self._playhead = frame
        self.update()

    @property
    def frames(self) -> int:
        return 0 if self._samples is None else len(self._samples)

    def _inner(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _ensure_envelope(self, width: int) -> tuple[np.ndarray, np.ndarray]:
        if self._envelope is None or self._envelope_width != width:
            self._envelope = build_envelope(self._samples, width)
            self._envelope_width = width
        return self._envelope

    # -------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        p = theme.current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        box = self._inner()
        painter.setBrush(QColor(p["bg_input"]))
        painter.setPen(QPen(QColor(p["border_progress"])))
        painter.drawRoundedRect(box, CORNER_RADIUS, CORNER_RADIUS)
        # everything after the rounded border is straight lines on whole pixels: antialiasing would smear each
        # 1px line across two pixel columns at 50% strength
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        if self._samples is None or len(self._samples) == 0:
            painter.setPen(QColor(p["text_disabled"]))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, PLACEHOLDER)
            return

        width, height, n = int(box.width()), box.height(), len(self._samples)
        left, mid = box.left(), box.center().y()
        painter.setPen(QPen(QColor(p["border"]), 1))
        painter.drawLine(int(left), int(mid), int(box.right()), int(mid))

        # the waveform: one vertical line per pixel column
        mins, maxs = self._ensure_envelope(max(1, width))
        scale = (height / 2 - 4) / 32768.0
        painter.setPen(QPen(QColor(p["accent"]), 1))
        for x in range(len(mins)):
            painter.drawLine(int(left + x), int(mid - maxs[x] * scale), int(left + x), int(mid - mins[x] * scale))

        # what plays: the area outside the play markers is dimmed
        dim = QColor(p["bg_input"])
        dim.setAlpha(150)
        x_start, x_end = left + x_for_frame(self._start, width, n), left + x_for_frame(self._end, width, n)
        painter.fillRect(QRectF(left, box.top() + 1, max(0.0, x_start - left), height - 2), dim)
        painter.fillRect(QRectF(x_end, box.top() + 1, max(0.0, box.right() - x_end), height - 2), dim)

        if self._loop is not None:
            loop_color = QColor(p["warning"])
            shade = QColor(loop_color)
            shade.setAlpha(70)
            x0, x1 = left + x_for_frame(self._loop.start, width, n), left + x_for_frame(min(self._loop.end, n - 1), width, n)
            painter.fillRect(QRectF(x0, box.top() + 1, max(1.0, x1 - x0), height - 2), shade)
            painter.setPen(QPen(loop_color, 1))
            for x in (x0, x1):
                painter.drawLine(int(x), int(box.top() + 1), int(x), int(box.bottom() - 1))

        if self._playhead is not None:
            painter.setPen(QPen(QColor(p["text_bright"]), 2))
            x = left + x_for_frame(min(max(0, self._playhead), n - 1), width, n)
            painter.drawLine(int(x), int(box.top() + 1), int(x), int(box.bottom() - 1))

    # ----------------------------------------------------------------- mouse
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._samples is not None and len(self._samples):
            x = event.position().x() - self._inner().left()
            self.seekRequested.emit(frame_for_x(x, int(self._inner().width()), len(self._samples)))
