"""The list of takes in the selected direct-to-disk partition (the counterpart of FileTable for a DD area)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget

from akaiutil_gui.engine.models import Take
from akaiutil_gui.formatting import human_duration, human_size

from .file_table import _ROLE_SORT, _SortItem

_ROLE_TAKE = Qt.ItemDataRole.UserRole

COLUMNS = ["#", "Name", "Type", "Rate", "Length", "Size"]
WIDTHS = {"#": 48, "Name": 200, "Type": 70, "Rate": 100, "Length": 90, "Size": 90}


class TakeTable(QTableWidget):
    selectionTakesChanged = Signal(list)  # list[Take]

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
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setMinimumSectionSize(32)
        for col, name in enumerate(COLUMNS):
            self.setColumnWidth(col, WIDTHS[name])
        self.itemSelectionChanged.connect(lambda: self.selectionTakesChanged.emit(self.selected_takes()))

    def set_takes(self, takes: list[Take]) -> None:
        self.setSortingEnabled(False)  # otherwise rows move while we fill them
        self.setRowCount(len(takes))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for row, t in enumerate(takes):
            cells = [
                (str(t.index), t.index, right),
                (t.name, t.name.lower(), None),
                ("Stereo" if t.stereo else "Mono", t.channels, None),
                (f"{t.rate:,} Hz", t.rate, right),
                (human_duration(t.seconds), t.seconds, right),
                (human_size(t.audio_bytes), t.audio_bytes, right),
            ]
            for col, (text, key, align) in enumerate(cells):
                item = _SortItem(text)
                item.setData(_ROLE_SORT, key)
                if align is not None:
                    item.setTextAlignment(align)
                if col == 0:
                    item.setData(_ROLE_TAKE, t)
                self.setItem(row, col, item)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)  # in take-number order, as akaiutil lists them
        self.selectionTakesChanged.emit([])

    def clear_takes(self) -> None:
        self.set_takes([])

    def selected_takes(self) -> list[Take]:
        rows = sorted({i.row() for i in self.selectedItems()})
        return sorted((self.item(r, 0).data(_ROLE_TAKE) for r in rows), key=lambda t: t.index)
