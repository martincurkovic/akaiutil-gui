"""The list of files in the selected volume."""

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from akaiutil_gui.engine.models import CatalogEntry, FileEntry, Volume
from akaiutil_gui.formatting import human_size

_ROLE_FILE = Qt.ItemDataRole.UserRole
_ROLE_SORT = Qt.ItemDataRole.UserRole + 1
_ROLE_VOLUME = Qt.ItemDataRole.UserRole + 2

COLUMNS = ["#", "Name", "Type", "Size", "Start block", "OS ver", "Tags", "Location"]
LOCATION = COLUMNS.index("Location")  # only shown for search results, which span volumes


class _SortItem(QTableWidgetItem):
    """Sorts by a hidden key (so '10' sorts after '9' and sizes sort by bytes)."""

    def __lt__(self, other):
        a, b = self.data(_ROLE_SORT), other.data(_ROLE_SORT)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)


class FileTable(QTableWidget):
    selectionFilesChanged = Signal(list)  # list[FileEntry]

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSortingEnabled(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        hdr = self.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Name takes the slack
        self.setColumnHidden(LOCATION, True)
        for col, width in {0: 48, 2: 130, 3: 90, 4: 100, 5: 80, 6: 110, LOCATION: 180}.items():
            self.setColumnWidth(col, width)
        self.itemSelectionChanged.connect(lambda: self.selectionFilesChanged.emit(self.selected_files()))
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.mime_provider: Callable[[], object | None] | None = None  # the window says what a drag carries

    def set_files(self, files: list[FileEntry], volume: Volume | None = None) -> None:
        """Show one volume's files."""
        self._fill([(volume, f) for f in files], results=False)

    def set_results(self, entries: list[CatalogEntry]) -> None:
        """Show search results: files from any number of volumes, with where each one lives."""
        self._fill([(e.volume, e.file) for e in entries], results=True)

    def _fill(self, rows: list[tuple[Volume | None, FileEntry]], results: bool) -> None:
        self.setSortingEnabled(False)  # otherwise rows move while we fill them
        self.setRowCount(len(rows))
        self.setColumnHidden(LOCATION, not results)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for row, (vol, f) in enumerate(rows):
            where = f"disk{vol.disk} / {vol.partition} / {vol.name}" if vol is not None else ""
            cells = [
                (str(f.index), f.index, right),
                (f.name, f.name.lower(), None),
                (f.kind, f.kind, None),
                (human_size(f.size), f.size, right),
                (f.start_block, int(f.start_block, 16), right),
                (f.os_version or "—", f.os_version, right),
                (", ".join(f.tag_labels), ", ".join(f.tag_labels).lower(), None),
                (where, where.lower(), None),
            ]
            for col, (text, key, align) in enumerate(cells):
                item = _SortItem(text)
                item.setData(_ROLE_SORT, key)
                if align is not None:
                    item.setTextAlignment(align)
                if col == 0:
                    item.setData(_ROLE_FILE, f)
                    item.setData(_ROLE_VOLUME, vol)
                self.setItem(row, col, item)
        self.setSortingEnabled(True)
        self.sortByColumn(1 if results else 0, Qt.SortOrder.AscendingOrder)  # results by name, a volume by file number
        self.selectionFilesChanged.emit([])

    def startDrag(self, supported_actions) -> None:
        mime = self.mime_provider() if self.mime_provider is not None else None
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._drag_pixmap(len(mime.rows)))
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)

    def _drag_pixmap(self, count: int) -> QPixmap:
        text = f"{count} file{'s' if count != 1 else ''}"
        metrics = QFontMetrics(self.font())
        pixmap = QPixmap(metrics.horizontalAdvance(text) + 20, metrics.height() + 10)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pixmap.rect(), 6, 6)
        painter.setPen(QColor("white"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return pixmap

    def clear_files(self) -> None:
        self.set_files([])

    def selected_rows(self) -> list[tuple[Volume | None, FileEntry]]:
        """The selected rows as (volume, file), the volume being where that file lives (None if unknown)."""
        rows = sorted({i.row() for i in self.selectedItems()})
        pairs = [(self.item(r, 0).data(_ROLE_VOLUME), self.item(r, 0).data(_ROLE_FILE)) for r in rows]
        return sorted(pairs, key=lambda p: ((p[0].disk, p[0].partition, p[0].index) if p[0] else (0, "", 0), p[1].index))

    def selected_files(self) -> list[FileEntry]:
        return [f for _, f in self.selected_rows()]

    def select_file(self, volume: Volume | None, file_index: int) -> bool:
        """Select (and scroll to) the row for a file; False if it isn't in the table."""
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            vol, f = item.data(_ROLE_VOLUME), item.data(_ROLE_FILE)
            same_volume = volume is None or vol is None or (vol.disk, vol.partition, vol.index) == (volume.disk, volume.partition, volume.index)
            if f.index == file_index and same_volume:
                self.selectRow(row)
                self.scrollToItem(item)
                return True
        return False
