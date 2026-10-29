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


def volume_shade(position: int | None) -> QColor | None:
    """A theme without colour (System 6) tells volumes apart by shades of grey: this volume's, or None if the theme uses colours.
    `position` None (not known yet) gives the lightest."""
    shades = theme.current_palette().get("volume_shades")
    if not shades:
        return None
    return QColor(shades[-1] if position is None else shades[position % len(shades)])


def fill_volume(painter: QPainter, rect: QRectF, position: int | None) -> None:
    """Fill `rect` as this volume looks in the usage bar and the tree: its colour, or its shade of grey."""
    painter.fillRect(rect, volume_shade(position) or volume_color(position))


def swatch_icon(position: int | None, dpr: float = 1.0, box: int = 16, size: int = 10) -> QIcon:
    """A small rounded square in the volume's colour (or an outlined square in its grey), for tree rows."""
    pm = QPixmap(round(box * dpr), round(box * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    offset = (box - size) / 2
    if theme.current_palette().get("volume_shades"):
        rect = QRectF(offset, offset, size, size)
        fill_volume(painter, rect, position)
        painter.setPen(QColor(theme.current_palette()["text"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()
        icon = QIcon(pm)
        # a selected row is inverted (black), and so is its swatch: Qt's own tinting would leave a black one invisible
        inverted = pm.toImage()
        inverted.invertPixels()
        icon.addPixmap(QPixmap.fromImage(inverted), QIcon.Mode.Selected)
        return icon
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(volume_color(position))
    painter.drawRoundedRect(QRectF(offset, offset, size, size), 2.5, 2.5)
    painter.end()
    return QIcon(pm)
