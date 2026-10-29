"""One colour per volume, shared by the usage bar and the swatches in the tree
so a volume's swatch always matches its segment (same idea as AKAISDS's keygroups)."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap

from . import theme

SLOTS = 8


def volume_color(position: int | None) -> QColor:
    """`position` is the volume's place in DiskUsage.volume_order(); colours are
    reused past the 8th. None (not known yet) gives a neutral grey."""
    palette = theme.current_palette()
    if position is None:
        return QColor(palette["border_hover"])
    return QColor(palette[f"volume_color_{position % SLOTS + 1}"])


def pattern_brush(position: int | None) -> QBrush | None:
    """A theme without colour (System 6) tells volumes apart by hatching: the brush for this volume, black on white, or None
    if the theme uses colours. `position` None (not known yet) gives an empty one."""
    names = theme.current_palette().get("volume_patterns")
    if not names:
        return None
    style = Qt.BrushStyle.NoBrush if position is None else getattr(Qt.BrushStyle, names[position % len(names)])
    return QBrush(QColor(theme.current_palette()["text"]), style)


def fill_volume(painter: QPainter, rect: QRectF, position: int | None) -> None:
    """Fill `rect` as this volume looks in the usage bar and the tree: its colour, or its hatching on white."""
    brush = pattern_brush(position)
    if brush is None:
        painter.fillRect(rect, volume_color(position))
        return
    painter.fillRect(rect, QColor(theme.current_palette()["bg_panel"]))
    painter.fillRect(rect, brush)


def swatch_icon(position: int | None, dpr: float = 1.0, box: int = 16, size: int = 10) -> QIcon:
    """A small rounded square in the volume's colour (or a hatched, outlined one), for tree rows."""
    pm = QPixmap(round(box * dpr), round(box * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    offset = (box - size) / 2
    if theme.current_palette().get("volume_patterns"):
        palette = theme.current_palette()
        rect = QRectF(offset, offset, size, size)
        fill_volume(painter, rect, position)
        painter.setPen(QColor(palette["text"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()
        icon = QIcon(pm)
        icon.addPixmap(pm, QIcon.Mode.Selected)  # (a selected row is black: keep the swatch as it is, not Qt's tinted version)
        return icon
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(volume_color(position))
    painter.drawRoundedRect(QRectF(offset, offset, size, size), 2.5, 2.5)
    painter.end()
    return QIcon(pm)
