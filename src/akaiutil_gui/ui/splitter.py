"""A QSplitter whose dividers are drawn as a thin line that lights up under the mouse.

QSS can't do this evenly: a vertical splitter's handle is sized and painted differently from a horizontal one's (margins
count twice), so the handles are painted here, from the same palette as the stylesheet.
"""

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QSplitter, QSplitterHandle

from . import theme

HANDLE_WIDTH = 7  # the grab area; the line drawn in it is one pixel


class _Handle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._active = False  # under the mouse, or being dragged

    def _set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def enterEvent(self, event) -> None:
        self._set_active(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if QApplication.mouseButtons() == Qt.MouseButton.NoButton:  # (still dragging: stay lit)
            self._set_active(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._set_active(self.underMouse())

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self.update()
        return super().event(event)

    def sizeHint(self):
        size = super().sizeHint()
        if self.orientation() == Qt.Orientation.Horizontal:
            size.setWidth(HANDLE_WIDTH)
        else:
            size.setHeight(HANDLE_WIDTH)
        return size

    def paintEvent(self, event) -> None:
        colors = theme.current_palette()
        rect = self.rect()
        mid = HANDLE_WIDTH // 2
        if self.orientation() == Qt.Orientation.Horizontal:
            line = QRect(rect.left() + mid, rect.top(), 1, rect.height())
        else:
            line = QRect(rect.left(), rect.top() + mid, rect.width(), 1)
        QPainter(self).fillRect(line, QColor(colors["accent" if self._active else "border"]))


class Splitter(QSplitter):
    def __init__(self, orientation):
        super().__init__(orientation)
        self.setHandleWidth(HANDLE_WIDTH)
        self.setChildrenCollapsible(False)

    def createHandle(self) -> QSplitterHandle:
        return _Handle(self.orientation(), self)
