"""The list of files in the selected volume."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from akaiutil_gui.engine.models import FileEntry
from akaiutil_gui.formatting import human_size

_ROLE_FILE = Qt.ItemDataRole.UserRole
_ROLE_SORT = Qt.ItemDataRole.UserRole + 1

COLUMNS = ["#", "Name", "Type", "Size", "Start block", "OS ver"]


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
        for col, width in {0: 48, 2: 130, 3: 90, 4: 100, 5: 80}.items():
            self.setColumnWidth(col, width)
        self.itemSelectionChanged.connect(lambda: self.selectionFilesChanged.emit(self.selected_files()))

    def set_files(self, files: list[FileEntry]) -> None:
        self.setSortingEnabled(False)  # otherwise rows move while we fill them
        self.setRowCount(len(files))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for row, f in enumerate(files):
            cells = [
                (str(f.index), f.index, right),
                (f.name, f.name.lower(), None),
                (f.kind, f.kind, None),
                (human_size(f.size), f.size, right),
                (f.start_block, int(f.start_block, 16), right),
                (f.os_version or "—", f.os_version, right),
            ]
            for col, (text, key, align) in enumerate(cells):
                item = _SortItem(text)
                item.setData(_ROLE_SORT, key)
                if align is not None:
                    item.setTextAlignment(align)
                if col == 0:
                    item.setData(_ROLE_FILE, f)
                self.setItem(row, col, item)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.selectionFilesChanged.emit([])

    def clear_files(self) -> None:
        self.set_files([])

    def selected_files(self) -> list[FileEntry]:
        rows = sorted({i.row() for i in self.selectedItems()})
        files = [self.item(r, 0).data(_ROLE_FILE) for r in rows]
        return sorted(files, key=lambda f: f.index)
