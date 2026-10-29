"""Black-and-white 16 px icons for the tree in the Macintosh System 6 theme, painted here (no files, hard edges)."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPolygonF

from . import theme

SIZE = 16


def _icon(draw, dpr: float) -> QIcon:
    palette = theme.current_palette()
    pm = QPixmap(round(SIZE * dpr), round(SIZE * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    ink, paper = QColor(palette["text"]), QColor(palette["bg_panel"])
    painter.setPen(ink)
    painter.setBrush(paper)
    draw(painter, ink)
    painter.end()
    icon = QIcon(pm)
    icon.addPixmap(pm, QIcon.Mode.Selected)  # (a selected row is black: keep the icon as it is)
    return icon


def _folder(p: QPainter, ink) -> None:
    p.drawPolygon(QPolygonF([QPointF(x + 0.5, y + 0.5) for x, y in ((1, 3), (6, 3), (8, 5), (14, 5), (14, 13), (1, 13))]))


def _floppy(p: QPainter, ink) -> None:
    p.drawRect(QRectF(1.5, 1.5, 12, 12))
    p.fillRect(QRectF(4, 2, 7, 5), ink)  # the shutter
    p.drawRect(QRectF(3.5, 9.5, 8, 4))  # the label


def _drive(p: QPainter, ink) -> None:
    p.drawRect(QRectF(0.5, 4.5, 14, 8))
    p.drawLine(QPointF(1, 8.5), QPointF(14, 8.5))
    p.fillRect(QRectF(10, 10, 3, 1), ink)


def folder(dpr: float = 1.0) -> QIcon:
    return _icon(_folder, dpr)


def floppy(dpr: float = 1.0) -> QIcon:
    return _icon(_floppy, dpr)


def drive(dpr: float = 1.0) -> QIcon:
    return _icon(_drive, dpr)


def warning_pixmap(size: int = 48, dpr: float = 1.0) -> QPixmap:
    """The warning triangle of a question dialog: an outlined triangle with a bar and a dot."""
    palette = theme.current_palette()
    ink, paper = QColor(palette["text"]), QColor(palette["bg_panel"])
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    unit = size / 48
    painter.scale(unit, unit)
    pen = painter.pen()
    pen.setColor(ink)
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(paper)
    painter.drawPolygon(QPolygonF([QPointF(24, 4), QPointF(45, 43), QPointF(3, 43)]))
    painter.fillRect(QRectF(22, 16, 4, 14), ink)
    painter.fillRect(QRectF(22, 34, 4, 4), ink)
    painter.end()
    return pm


def _message_pixmap(kind: str, size: int) -> QPixmap:
    """A message box icon at `size` px: "information" (a circle with an i), "question" (a ?), "critical" (an octagon with an X)."""
    palette = theme.current_palette()
    ink, paper = QColor(palette["text"]), QColor(palette["bg_panel"])
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.scale(size / 48, size / 48)
    pen = painter.pen()
    pen.setColor(ink)
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(paper)
    if kind == "critical":
        points = [(15, 3), (33, 3), (45, 15), (45, 33), (33, 45), (15, 45), (3, 33), (3, 15)]
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        pen.setWidth(4)
        painter.setPen(pen)
        painter.drawLine(QPointF(16, 16), QPointF(32, 32))
        painter.drawLine(QPointF(32, 16), QPointF(16, 32))
    else:
        painter.drawEllipse(QRectF(3, 3, 42, 42))
        if kind == "information":
            painter.fillRect(QRectF(22, 20, 5, 17), ink)
            painter.fillRect(QRectF(22, 11, 5, 5), ink)
        else:
            font = QFont(painter.font())
            font.setPixelSize(34)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(3, 3, 42, 42), Qt.AlignmentFlag.AlignCenter, "?")
    painter.end()
    return pm


def message_icon(kind: str) -> QIcon:
    """The icon for a QMessageBox ("information", "question", "warning" or "critical"), at the sizes it is asked for."""
    icon = QIcon()
    for size in (16, 32, 48, 64, 96):
        icon.addPixmap(warning_pixmap(size) if kind == "warning" else _message_pixmap(kind, size))
    return icon
