"""A waveform with draggable sample-start/end and loop in/out markers, for the Sample Settings dialog.

The marker mechanism is AKAISDS's WaveformView, carried over: a click within a few pixels grabs a marker (several on top of each
other are taken in turn by repeated clicks), the one under the cursor lights up, a drag moves it, Shift makes the drag fine, and a
marker dragged past its partner pushes that one along instead of stopping dead. Ctrl (Cmd) + wheel or a trackpad pinch zooms around
the cursor, Shift + wheel or a sideways swipe pans, so a marker can be put on an exact frame.

What differs: this sample has up to eight loops, each with its own colour, instead of AKAISDS's one. A loop's two markers push
each other; the sample's start and end push each other; the groups don't interfere.
"""

import sys

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget

from . import theme
from .volume_colors import volume_color
from .waveform_preview import CORNER_RADIUS, build_envelope

HIT_RADIUS_PX = 6  # how close a click has to land to grab a marker
HANDLE_SIZE = 5  # the little triangle at a marker's end
FINE_DRAG_DIVISOR = 8  # how much slower a Shift-held drag moves
FINE_DRAG_DIVISOR_MACOS = 12  # (macOS fights the cursor-warp trick, so it gets a plain, larger divisor instead)
MIN_ZOOM = 1.0
ZOOM_STEP = 1.6  # per wheel notch or zoom button click
MACOS = sys.platform == "darwin"
LOOPS = 8

START, END = "start", "end"


def loop_in(i: int) -> str:
    return f"l{i + 1}_in"


def loop_out(i: int) -> str:
    return f"l{i + 1}_out"


def loop_of(name: str) -> int | None:
    """The loop (0-7) a marker belongs to, or None for the sample's own start and end."""
    return int(name[1]) - 1 if name.startswith("l") else None


def frame_for_x(x: float, width: int, view_start: int, view_length: int) -> int:
    """The frame at a widget x, within the visible window [view_start, view_start + view_length)."""
    if width <= 1 or view_length <= 1:
        return view_start
    frac = min(1.0, max(0.0, x / (width - 1)))
    return view_start + int(round(frac * (view_length - 1)))


def x_for_frame(frame: float, width: int, view_start: int, view_length: int) -> float:
    if view_length <= 1:
        return 0.0
    return (frame - view_start) / (view_length - 1) * (width - 1)


def push_marker(order: tuple[str, ...], name: str, frame: int, values: dict[str, int], last: int) -> dict[str, int]:
    """Move one marker of a group (`order`, left to right) to `frame`, pushing any neighbour it would cross along with it, so
    the group stays in order with at least one frame between markers. The frame is clamped so every marker still fits within
    0..last. Returns a new dict of all the values."""
    i = order.index(name)
    frame = max(i, min(last - (len(order) - 1 - i), frame))
    new = dict(values)
    new[name] = frame
    for j in range(i - 1, -1, -1):
        if new[order[j]] >= new[order[j + 1]]:
            new[order[j]] = new[order[j + 1]] - 1
        else:
            break
    for j in range(i + 1, len(order)):
        if new[order[j]] <= new[order[j - 1]]:
            new[order[j]] = new[order[j - 1]] + 1
        else:
            break
    return new


def brighten_for_hover(color: QColor, palette: dict) -> QColor:
    """A more vivid variant of a colour, for "you can grab this": lighter on a dark UI, darker on a light one."""
    is_dark = QColor(palette["bg_input"]).lightness() < 128
    h, s, l, a = QColor(color).getHsl()
    if is_dark:
        l = int(l + (255 - l) * 0.35)
        s = min(255, int(s * 1.15) + 10)
    else:
        l = int(l * 0.65)
        s = min(255, int(s * 1.2) + 10)
    out = QColor(color)
    out.setHsl(h, s, l, a)
    return out


class MarkerWaveform(QWidget):
    markersChanged = Signal(dict)  # {name: frame} for every marker shown, continuously while one is dragged
    markerCommitted = Signal(str, dict)  # the marker that was dragged, and all of them, when the mouse is released
    loopGrabbed = Signal(int)  # a loop's marker was grabbed: which loop (0-7)
    viewChanged = Signal(int, int, int)  # view start, view length, frames: enough for a scroll bar
    previewRequested = Signal()  # a click on empty waveform (away from every marker), with audio shown: "play it"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(150)
        self.setMouseTracking(True)
        self._samples: np.ndarray | None = None
        self._frames = 0
        self._markers: dict[str, int] = {}
        self._zoom = MIN_ZOOM
        self._view_start = 0
        self._envelope: tuple[np.ndarray, np.ndarray] | None = None
        self._envelope_key: tuple | None = None
        self._hover: str | None = None
        self._dragging: str | None = None
        self._drag_anchor_x = 0.0
        self._drag_value = 0.0  # a float accumulator, so a slow drag doesn't lose its sub-frame remainder
        self._cycle: list[str] = []
        self._cycle_index = 0
        self._fine = False
        self._warp_anchor = None
        self._selected_loop: int | None = None
        self._playhead: int | None = None

    # ----------------------------------------------------------------- data
    @property
    def frames(self) -> int:
        return self._frames

    @property
    def markers(self) -> dict[str, int]:
        return dict(self._markers)

    def set_audio(self, samples: np.ndarray | None, frames: int = 0) -> None:
        """Show this audio (mono int16), or none: then only the markers are shown, on a plain background, over `frames` frames."""
        self._samples = samples if samples is not None and len(samples) else None
        self._frames = len(self._samples) if self._samples is not None else max(0, int(frames))
        self._envelope = None
        self._zoom, self._view_start = MIN_ZOOM, 0
        self.update()
        self._emit_view()

    def set_markers(self, markers: dict[str, int]) -> None:
        """Replace the markers shown (a loop that is off simply has none). Nothing is emitted: this is the dialog speaking."""
        self._markers = {k: int(v) for k, v in markers.items()}
        self._hover = self._hover if self._hover in self._markers else None
        self.update()

    def set_selected_loop(self, loop: int | None) -> None:
        self._selected_loop = loop
        self.update()

    @property
    def has_audio(self) -> bool:
        return self._samples is not None

    def set_playhead(self, frame: int | None) -> None:
        self._playhead = frame
        self.update()

    # ----------------------------------------------------------------- view
    def _view_length(self) -> int:
        if self._frames == 0:
            return 0
        return max(1, min(self._frames, int(round(self._frames / self._zoom))))

    def _max_zoom(self) -> float:
        return max(MIN_ZOOM, float(self._frames))  # one visible frame at most, whatever the size of the sample

    def _clamp_view_start(self) -> None:
        self._view_start = max(0, min(max(0, self._frames - self._view_length()), self._view_start))

    def _emit_view(self) -> None:
        self.viewChanged.emit(self._view_start, self._view_length(), self._frames)

    def view_state(self) -> tuple[int, int]:
        return self._view_start, self._view_length()

    def set_view_start(self, start: int) -> None:
        if self._frames == 0:
            return
        self._view_start = int(start)
        self._clamp_view_start()
        self._envelope = None
        self.update()
        self._emit_view()

    def set_zoom(self, zoom: float, anchor_frame: float | None = None) -> None:
        """Zoom (1 = the whole sample), keeping the frame at `anchor_frame` (default: the middle) under the same x."""
        if self._frames == 0:
            return
        old = self._view_length()
        if anchor_frame is None:
            anchor_frame = self._view_start + old / 2
        fraction = (anchor_frame - self._view_start) / (old - 1) if old > 1 else 0.0
        self._zoom = max(MIN_ZOOM, min(self._max_zoom(), zoom))
        new = self._view_length()
        self._view_start = int(round(anchor_frame - fraction * (new - 1)))
        self._clamp_view_start()
        self._envelope = None
        self.update()
        self._emit_view()

    def zoom_in(self, anchor_frame: float | None = None) -> None:
        self.set_zoom(self._zoom * ZOOM_STEP, anchor_frame)

    def zoom_out(self, anchor_frame: float | None = None) -> None:
        self.set_zoom(self._zoom / ZOOM_STEP, anchor_frame)

    def reset_zoom(self) -> None:
        self.set_zoom(MIN_ZOOM)

    @property
    def zoom(self) -> float:
        return self._zoom

    def resizeEvent(self, event) -> None:
        self._envelope = None
        super().resizeEvent(event)

    # -------------------------------------------------------------- markers
    def _order(self) -> list[str]:
        """Every marker present, in the order they are tested and painted: the sample's own, then each loop's."""
        names = [n for n in (START, END) if n in self._markers]
        for i in range(LOOPS):
            names += [n for n in (loop_in(i), loop_out(i)) if n in self._markers]
        return names

    def _group(self, name: str) -> tuple[str, ...]:
        i = loop_of(name)
        return (START, END) if i is None else (loop_in(i), loop_out(i))

    def _x_for(self, name: str) -> float:
        return x_for_frame(self._markers[name], self.width(), self._view_start, self._view_length())

    def _candidates(self, x: float, y: float | None = None) -> list[str]:
        """The markers within the hit radius of x, nearest first.

        Where the sample's own markers (start, end) and a loop's markers are close enough to overlap, the height decides: the top
        half grabs the sample's, the bottom half the loop's, matching where each kind has its handle. Anywhere else a marker is
        grabbed by clicking anywhere along its line (`y` None skips the height test)."""
        view_start, length = self._view_start, self._view_length()
        found = []
        for name in self._order():
            frame = self._markers[name]
            if frame < view_start or frame > view_start + length - 1:
                continue
            distance = abs(self._x_for(name) - x)
            if distance <= HIT_RADIUS_PX:
                found.append((distance, name))
        found.sort(key=lambda pair: pair[0])  # (stable: ties stay in the painting order)
        names = [name for _d, name in found]
        if y is not None:
            sample_markers = [n for n in names if loop_of(n) is None]
            loop_markers = [n for n in names if loop_of(n) is not None]
            if sample_markers and loop_markers:  # overlapping: pick the kind by which half was clicked
                names = sample_markers if y < self.height() / 2 else loop_markers
        return names

    def move_marker(self, name: str, frame: int) -> dict[str, int]:
        """Move a marker as a drag would (pushing a partner it crosses), and return all the markers."""
        group = tuple(n for n in self._group(name) if n in self._markers)
        if len(group) < 2:
            self._markers[name] = max(0, min(max(0, self._frames - 1), int(frame)))
            return dict(self._markers)
        values = {n: self._markers[n] for n in group}
        self._markers.update(push_marker(group, name, int(frame), values, max(0, self._frames - 1)))
        return dict(self._markers)

    # --------------------------------------------------------------- painting
    def _inner(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _ensure_envelope(self, width: int) -> tuple[np.ndarray, np.ndarray] | None:
        if self._samples is None:
            return None
        key = (self._view_start, self._view_length(), width)
        if self._envelope is None or self._envelope_key != key:
            window = self._samples[self._view_start : self._view_start + self._view_length()]
            self._envelope = build_envelope(window, width)
            self._envelope_key = key
        return self._envelope

    def _color(self, name: str, palette: dict) -> QColor:
        i = loop_of(name)
        return QColor(palette["text_disabled"]) if i is None else volume_color(i)

    def paintEvent(self, _event) -> None:
        p = theme.current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        box = self._inner()
        painter.setBrush(QColor(p["bg_input"]))
        painter.setPen(QPen(QColor(p["border_progress"])))
        painter.drawRoundedRect(box, CORNER_RADIUS, CORNER_RADIUS)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        width, height = self.width(), self.height()
        view_start, length = self._view_start, self._view_length()
        if self._frames == 0:
            painter.setPen(QColor(p["text_disabled"]))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "No waveform")
            return

        mid = height / 2
        painter.setPen(QPen(QColor(p["border"]), 1))
        painter.drawLine(0, int(mid), width, int(mid))
        envelope = self._ensure_envelope(max(1, width))
        if envelope is not None:
            mins, maxs = envelope
            scale = (height / 2 - 14) / 32768.0
            painter.setPen(QPen(QColor(p["accent"]), 1))
            for x in range(len(mins)):
                painter.drawLine(x, int(mid - maxs[x] * scale), x, int(mid - mins[x] * scale))
        else:
            painter.setPen(QColor(p["text_disabled"]))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "No waveform to show: the markers still work")

        # what plays: outside the sample's start and end markers is dimmed
        if START in self._markers and END in self._markers:
            dim = QColor(p["bg_input"])
            dim.setAlpha(150)
            x0 = x_for_frame(self._markers[START], width, view_start, length)
            x1 = x_for_frame(self._markers[END], width, view_start, length)
            painter.fillRect(QRectF(0, 1, max(0.0, min(width, x0)), height - 2), dim)
            painter.fillRect(QRectF(max(0.0, x1), 1, max(0.0, width - x1), height - 2), dim)
        # each loop's span, lightly, in its colour (the selected one a little stronger)
        for i in range(LOOPS):
            if loop_in(i) in self._markers and loop_out(i) in self._markers:
                shade = QColor(volume_color(i))
                shade.setAlpha(80 if i == self._selected_loop else 38)
                x0 = x_for_frame(self._markers[loop_in(i)], width, view_start, length)
                x1 = x_for_frame(self._markers[loop_out(i)], width, view_start, length)
                left, right = max(0.0, x0), min(float(width), x1)
                if right > left:
                    painter.fillRect(QRectF(left, 1, right - left, height - 2), shade)

        active = {n for n in (self._hover, self._dragging) if n}
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        # the sample's markers first, then the loops', so a loop's handle is never hidden behind a boundary's
        for name in self._order():
            frame = self._markers[name]
            if frame < view_start or frame > view_start + length - 1:
                continue
            x = self._x_for(name)
            is_loop = loop_of(name) is not None
            on = name in active
            color = brighten_for_hover(self._color(name, p), p) if on else self._color(name, p)
            pen = QPen(color)
            pen.setWidthF(2.0 if on else 1.5)
            pen.setStyle(Qt.PenStyle.SolidLine if on else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, 0), QPointF(x, height))
            size = HANDLE_SIZE * 1.5 if on else HANDLE_SIZE
            base_y = height if is_loop else 0
            tip_y = height - size * 1.6 if is_loop else size * 1.6
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([QPointF(x - size, base_y), QPointF(x + size, base_y), QPointF(x, tip_y)]))
            if is_loop:  # which loop: its number, beside the handle (in goes to the left, out to the right)
                label = str(loop_of(name) + 1)
                painter.setPen(color)
                fm = painter.fontMetrics()
                tx = x - fm.horizontalAdvance(label) - size - 1 if name.endswith("_in") else x + size + 2
                painter.drawText(QPointF(tx, height - 3), label)

        if self._playhead is not None and view_start <= self._playhead <= view_start + length - 1:
            painter.setPen(QPen(QColor(p["text_bright"]), 2))
            x = x_for_frame(self._playhead, width, view_start, length)
            painter.drawLine(QPointF(x, 1), QPointF(x, height - 1))

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event) -> None:
        if self._frames == 0 or event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        found = self._candidates(x, event.position().y())
        if not found:
            self._cycle, self._cycle_index, self._dragging = [], 0, None
            if self.has_audio:  # a click on empty waveform: play it (a plain toggle, as in AKAISDS)
                self.previewRequested.emit()
            return
        if found == self._cycle:  # the same pile of markers as last time: take the next one
            self._cycle_index = (self._cycle_index + 1) % len(found)
        else:
            self._cycle, self._cycle_index = found, 0
        self._dragging = found[self._cycle_index]
        self._drag_anchor_x = x
        self._drag_value = float(self._markers[self._dragging])
        loop = loop_of(self._dragging)
        if loop is not None:
            self.loopGrabbed.emit(loop)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is None:
            found = self._candidates(event.position().x(), event.position().y())
            hovered = found[0] if found else None
            if hovered != self._hover:
                self._hover = hovered
                self.setCursor(Qt.CursorShape.SizeHorCursor if hovered else Qt.CursorShape.ArrowCursor)
                self.update()
            return
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if fine and not self._fine:
            self._enter_fine()
        elif not fine and self._fine:
            self._exit_fine()
        if self._fine and MACOS:
            x = event.position().x()
            dx = (x - self._drag_anchor_x) / FINE_DRAG_DIVISOR_MACOS
            self._drag_anchor_x = x
        elif self._fine:
            current, anchor = event.globalPosition(), self._warp_anchor
            if (round(current.x()), round(current.y())) == (round(anchor.x()), round(anchor.y())):
                return  # (the synthetic move the warp itself produces)
            dx = (current.x() - anchor.x()) / FINE_DRAG_DIVISOR
            QCursor.setPos(anchor.toPoint())
        else:
            x = event.position().x()
            dx = x - self._drag_anchor_x
            self._drag_anchor_x = x
        frames_per_px = (self._view_length() - 1) / max(1, self.width() - 1)
        self._drag_value += dx * frames_per_px
        frame = int(round(self._drag_value))
        self.move_marker(self._dragging, frame)
        clamped = max(0, min(max(0, self._frames - 1), frame))
        if clamped != frame:  # only at the true ends: otherwise the sub-frame remainder must survive
            self._drag_value = clamped
        self.update()
        self.markersChanged.emit(dict(self._markers))

    def _enter_fine(self) -> None:
        self._fine = True
        if MACOS:
            return
        self._warp_anchor = QCursor.pos().toPointF()
        QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)

    def _exit_fine(self) -> None:
        self._fine = False
        if MACOS:
            return
        self._warp_anchor = None
        QApplication.restoreOverrideCursor()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging is None:
            return
        if self._fine:
            self._exit_fine()
        which, self._dragging = self._dragging, None
        self._hover = which
        self.update()
        self.markerCommitted.emit(which, dict(self._markers))

    def leaveEvent(self, event) -> None:
        if self._hover is not None and self._dragging is None:
            self._hover = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        if self._fine:  # (never leave the app with an invisible cursor)
            self._exit_fine()
        self._dragging = None
        super().hideEvent(event)

    # ------------------------------------------------------------ zoom and pan
    def wheelEvent(self, event) -> None:
        if self._frames == 0:
            event.ignore()
            return
        angle = event.angleDelta()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:  # Ctrl (Cmd on a Mac): zoom around the cursor
            delta = angle.x() if angle.x() != 0 else angle.y()
            if delta == 0:
                return
            anchor = frame_for_x(event.position().x(), self.width(), self._view_start, self._view_length())
            self.zoom_in(anchor) if delta > 0 else self.zoom_out(anchor)
            event.accept()
            return
        if angle.x() != 0:  # a sideways swipe or a mouse's horizontal wheel
            delta = angle.x()
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier and angle.y() != 0:  # Shift + wheel: sideways
            delta = angle.y()
        else:
            event.ignore()  # (plain vertical scrolling goes on to whatever is behind)
            return
        self.set_view_start(self._view_start + int(-delta / 120 * max(1, self._view_length() // 10)))
        event.accept()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            if self._frames:  # a trackpad pinch
                anchor = frame_for_x(event.position().x(), self.width(), self._view_start, self._view_length())
                self.set_zoom(self._zoom * max(0.1, 1.0 + event.value()), anchor)
            return True
        return super().event(event)
