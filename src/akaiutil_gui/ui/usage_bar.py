"""Space usage under the disk tree: one continuous bar, coloured per volume, plus a summary line.

Drawn like AKAISDS's keygroup range bar: a single rounded track, with the
segments flush against each other and clipped to the track's outline.
"""

from dataclasses import dataclass, replace

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from akaiutil_gui.engine.models import DiskUsage
from akaiutil_gui.formatting import human_size, percent

from . import theme
from .volume_colors import fill_volume

# same thresholds AKAISDS uses for its sampler-memory bar
WARNING_AT = 0.70
CRITICAL_AT = 0.90

def level_color(fraction_used: float) -> QColor:
    palette = theme.current_palette()
    if fraction_used >= CRITICAL_AT:
        return QColor(palette["critical"])
    if fraction_used >= WARNING_AT:
        return QColor(palette["warning"])
    return QColor(palette["accent"])


CORNER_RADIUS = 3
MIN_VOLUME_PX = 1.0  # a volume smaller than this still shows as a hairline


@dataclass(frozen=True)
class Span:
    """A stretch of the bar, `weight` blocks long."""

    weight: float
    kind: str  # "partition" | "volume" | "system" | "free" | "outside"
    volume: int = 0  # for kind == "volume": its position on the disk, which picks the colour
    tooltip: str = ""
    divider_after: bool = False  # draw a thin line after it (partition boundary)
    used: float = 0.0  # for kind == "partition": how full it is, 0..1


# ----------------------------------------------------------------- building spans
def partition_spans(usage: DiskUsage, name: str, divider: bool = False) -> list[Span]:
    part = next(p for p in usage.partitions if p.name == name)
    label = "Direct-to-disk area" if part.is_dd else f"Partition {part.name}"
    bs = part.block_size
    used_blocks = part.size_blocks - part.free_blocks

    vols = usage.volumes_in(name)
    order = usage.volume_order()
    weights = [v.used_bytes / bs for v in vols]
    if sum(weights) > used_blocks > 0:  # shouldn't happen; never draw past what's actually used
        scale = used_blocks / sum(weights)
        weights = [w * scale for w in weights]

    spans = [
        Span(
            w,
            "volume",
            order[(name, v.index)],
            f"{v.name}  ·  volume {v.index}, {label.lower() if part.is_dd else label}\n"
            f"{human_size(v.used_bytes)} used  ·  {v.files} file{'s' if v.files != 1 else ''}",
        )
        for v, w in zip(vols, weights)
        if w > 0
    ]
    system = used_blocks - sum(weights)
    if system > 0:
        what = "Recorded takes" if part.is_dd else "Directories and file-system data"
        spans.append(Span(system, "system", tooltip=f"{label}\n{what}  ·  {human_size(system * bs)}"))
    if part.free_blocks > 0:
        spans.append(Span(part.free_blocks, "free", tooltip=f"{label}\n{human_size(part.free_bytes)} free"))
    if divider and spans:
        spans[-1] = replace(spans[-1], divider_after=True)
    return spans


def outside_blocks(usage: DiskUsage) -> int:
    inside = sum(p.size_blocks for p in usage.partitions)
    return max(0, usage.disk.total_blocks - inside)


def disk_spans(usage: DiskUsage) -> list[Span]:
    """One cell per partition, filled by how full it is. (Volumes are too small to
    read at this scale; they show once a partition is selected.)"""
    spans: list[Span] = []
    for p in usage.partitions:
        used = (p.size_blocks - p.free_blocks) / p.size_blocks if p.size_blocks else 0.0
        what = "Direct-to-disk area" if p.is_dd else f"Partition {p.name}"
        n = len(usage.volumes_in(p.name))
        volumes = f"\n{n} volume{'s' if n != 1 else ''}" if n and not p.is_dd else ""
        spans.append(
            Span(
                p.size_blocks,
                "partition",
                tooltip=f"{what}\n{human_size(p.free_bytes)} free of {human_size(p.size_bytes)}  ·  {percent(used)} used{volumes}",
                divider_after=True,
                used=used,
            )
        )
    outside = outside_blocks(usage)
    if outside > 0.01 * usage.disk.total_blocks:  # ignore a few spare blocks
        size = human_size(outside * usage.disk.block_size)
        spans.append(Span(outside, "outside", tooltip=f"{size} of the disk is outside every partition"))
    return spans


# ------------------------------------------------------------------------- widget
class UsageBar(QWidget):
    HEIGHT = 16  # same as the keygroup range bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spans: list[Span] = []
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        theme.notifier.changed.connect(self.update)

    def set_spans(self, spans: list[Span]) -> None:
        self._spans = [s for s in spans if s.weight > 0]
        self.update()

    def clear(self) -> None:
        self.set_spans([])

    @property
    def spans(self) -> list[Span]:
        return list(self._spans)

    def _track_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def span_rects(self) -> list[QRectF]:
        """Where each span sits. Widths are proportional to blocks, except that a
        volume too small to see is widened to a hairline (taken from the rest)."""
        spans = self._spans
        if not spans:
            return []
        track = self._track_rect()
        total = sum(s.weight for s in spans)
        widths = [track.width() * s.weight / total for s in spans]
        small = [i for i, s in enumerate(spans) if s.kind == "volume" and widths[i] < MIN_VOLUME_PX]
        if small:
            rest = [i for i in range(len(spans)) if i not in small]
            rest_sum = sum(widths[i] for i in rest)
            room = track.width() - MIN_VOLUME_PX * len(small)
            if rest_sum > 0 and room > 0:
                for i in rest:
                    widths[i] *= room / rest_sum
                for i in small:
                    widths[i] = MIN_VOLUME_PX
        rects, x = [], track.left()
        for w in widths:
            rects.append(QRectF(x, track.top(), w, track.height()))
            x += w
        return rects

    # --------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        palette = theme.current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not palette.get("hard_edges"))
        mono = bool(palette.get("usage_mono"))  # System 6: no warning / critical colours on the partitions, just how full each is
        track = self._track_rect()
        track_path = QPainterPath()
        track_path.addRoundedRect(track, CORNER_RADIUS, CORNER_RADIUS)

        # the same "empty track" as QProgressBar: neutral, reads as unassigned
        painter.fillPath(track_path, QColor(palette["bg_disabled"]))

        painter.save()
        painter.setClipPath(track_path)  # only the bar's true ends are rounded
        for span, rect in zip(self._spans, self.span_rects()):
            if span.kind == "partition":
                if span.used * rect.width() >= 1.0:  # under a pixel of use isn't drawn
                    fill = QRectF(rect.left(), rect.top(), rect.width() * span.used, rect.height())
                    if mono:  # (black for what is used, and a white hairline between it and the divider before it)
                        painter.fillRect(fill.adjusted(1, 0, 0, 0), QColor(palette["text"]))
                    else:
                        painter.fillRect(fill, level_color(span.used))
            elif span.kind == "volume":
                fill_volume(painter, rect, span.volume)
            elif span.kind == "system":
                painter.fillRect(rect, QColor(palette["text_disabled"]))
            elif span.kind == "outside":
                painter.fillRect(rect, QBrush(QColor(palette["border_hover"]), Qt.BrushStyle.BDiagPattern))
            # "free" is left as the empty track
        painter.setPen(QPen(QColor(palette["text" if mono else "bg_panel"]), 1))
        for span, rect in zip(self._spans, self.span_rects()):
            if span.divider_after and rect.right() < track.right() - 0.5:
                painter.drawLine(QPointF(rect.right(), track.top()), QPointF(rect.right(), track.bottom()))
        painter.restore()

        painter.setPen(QColor(palette["border_progress"]))
        painter.drawPath(track_path)
        painter.end()

    # ---------------------------------------------------------------- tooltip
    def span_at(self, pos: QPointF) -> Span | None:
        for span, rect in zip(self._spans, self.span_rects()):
            if rect.left() <= pos.x() < rect.right() or (rect.width() < 3 and abs(pos.x() - rect.center().x()) < 2):
                return span
        return None

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            span = self.span_at(QPointF(e.pos()))
            if span is not None and span.tooltip:
                QToolTip.showText(e.globalPos(), span.tooltip, self)
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)


class UsageSummary(QWidget):
    """The bar with a short text line under it. What it shows follows the selection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bar = UsageBar()
        self.label = QLabel()
        self.label.setObjectName("usageLabel")
        self.label.setWordWrap(True)
        # reserve two lines so the panel doesn't jump as the text changes
        self.label.setMinimumHeight(2 * self.label.fontMetrics().lineSpacing())
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(self.bar)
        layout.addWidget(self.label)

    def _set_text(self, text: str, level: str = "normal") -> None:
        self.label.setText(text)
        if self.label.property("level") != level:
            self.label.setProperty("level", level)
            self.label.style().unpolish(self.label)  # make the QSS [level=...] rule re-evaluate
            self.label.style().polish(self.label)

    def clear(self) -> None:
        self.bar.clear()
        self._set_text("")

    def show_loading(self) -> None:
        self.bar.clear()
        self._set_text("Calculating space used…")

    def show_disk(self, usage: DiskUsage) -> None:
        if not usage.partitions:
            self.bar.clear()
            self._set_text("No partitions found on this disk.")
            return
        bs = usage.disk.block_size
        size = sum(p.size_blocks for p in usage.partitions)
        free = sum(p.free_blocks for p in usage.partitions)
        n = len(usage.partitions)
        text = f"{human_size(free * bs)} free of {human_size(size * bs)} in {n} partition{'s' if n != 1 else ''}"
        roomiest = max(usage.partitions, key=lambda p: p.free_blocks)
        if n > 1 and roomiest.free_blocks:  # a sample has to fit inside one partition
            text += f"  ·  most free in one: {roomiest.name} ({human_size(roomiest.free_bytes)})"
        spans = disk_spans(usage)
        if any(s.kind == "outside" for s in spans):
            text += f"  ·  {human_size(outside_blocks(usage) * bs)} outside any partition"
        self._set_text(text)
        self.bar.set_spans(spans)

    def show_partition(self, usage: DiskUsage, name: str) -> None:
        part = next((p for p in usage.partitions if p.name == name), None)
        if part is None:
            self.clear()
            return
        used = part.used_bytes / part.size_bytes if part.size_bytes else 0
        what = "Direct-to-disk area" if part.is_dd else f"Partition {part.name}"
        level = "critical" if used >= CRITICAL_AT else "warning" if used >= WARNING_AT else "normal"
        self._set_text(
            f"{what}: {human_size(part.free_bytes)} free of {human_size(part.size_bytes)}  ·  {percent(used)} used",
            level,
        )
        self.bar.set_spans(partition_spans(usage, name))
