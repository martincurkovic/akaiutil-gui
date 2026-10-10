"""One colour per volume, shared by the usage bar and the swatches in the tree
so a volume's swatch always matches its segment (same idea as AKAISDS's keygroups)."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from . import theme

SLOTS = 8


def volume_color(position: int | None) -> QColor:
    """`position` is the volume's place in DiskUsage.volume_order(); colours are
    reused past the 8th. None (not known yet) gives a neutral grey."""
    palette = theme.current_palette()
    if position is None:
        return QColor(palette["border_hover"])
    return QColor(palette[f"volume_color_{position % SLOTS + 1}"])


def swatch_icon(position: int | None, dpr: float = 1.0, box: int = 16, size: int = 10) -> QIcon:
    """A small rounded square in the volume's colour, for tree rows."""
    pm = QPixmap(round(box * dpr), round(box * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(volume_color(position))
    offset = (box - size) / 2
    painter.drawRoundedRect(QRectF(offset, offset, size, size), 2.5, 2.5)
    painter.end()
    return QIcon(pm)
