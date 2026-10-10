"""The list of files in the selected volume."""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableWidget, QTableWidgetItem

from akaiutil_gui import settings

from akaiutil_gui.engine.models import CatalogEntry, FileEntry, Volume
from akaiutil_gui.formatting import human_size

_ROLE_FILE = Qt.ItemDataRole.UserRole
_ROLE_SORT = Qt.ItemDataRole.UserRole + 1
_ROLE_VOLUME = Qt.ItemDataRole.UserRole + 2

COLUMNS = ["#", "Name", "Type", "Size", "Start block", "OS ver", "Tags", "Location"]
LOCATION = COLUMNS.index("Location")  # only shown for search results, which span volumes
DEFAULT_WIDTHS = {"#": 48, "Name": 200, "Type": 100, "Size": 80, "Start block": 95, "OS ver": 72, "Tags": 90, "Location": 180}
ALWAYS_SHOWN = {"Name"}  # the column that says what a row is can't be hidden
MENU_LABELS = {"#": "#  (file number)"}


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
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # every column can be dragged to any width...
        hdr.setStretchLastSection(True)  # ...and the last one takes up the slack, so there is no empty strip at the right
        hdr.setMinimumSectionSize(32)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._header_menu)
        hdr.sectionResized.connect(self._section_resized)
        self._widths = dict(DEFAULT_WIDTHS)
        self._restoring = True
        self.setColumnHidden(LOCATION, True)
        self._apply_saved_columns()
        self._restoring = False
        self._save_timer = QTimer(self)  # (so dragging a column edge doesn't write the settings on every pixel)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_widths)
        self.itemSelectionChanged.connect(lambda: self.selectionFilesChanged.emit(self.selected_files()))
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.mime_provider: Callable[[], object | None] | None = None  # the window says what a drag carries

    # ------------------------------------------------------------------ columns
    def column_visible(self, name: str) -> bool:
        return not self.isColumnHidden(COLUMNS.index(name))

    def _apply_saved_columns(self) -> None:
        saved = settings.get_file_column_widths()
        hidden = settings.get_hidden_file_columns() - ALWAYS_SHOWN
        for col, name in enumerate(COLUMNS):
            self._widths[name] = saved.get(name, DEFAULT_WIDTHS[name])
            self.setColumnWidth(col, self._widths[name])
            if col != LOCATION:  # (Location comes and goes with search results, not with the user's choice)
                self.setColumnHidden(col, name in hidden)

    def set_column_visible(self, name: str, visible: bool) -> None:
        if name in ALWAYS_SHOWN or name == COLUMNS[LOCATION]:
            return
        col = COLUMNS.index(name)
        self.setColumnHidden(col, not visible)
        if visible:
            self._restoring = True
            self.setColumnWidth(col, self._widths.get(name, DEFAULT_WIDTHS[name]))
            self._restoring = False
        settings.set_hidden_file_columns({n for n in COLUMNS if n != COLUMNS[LOCATION] and not self.column_visible(n)})

    def reset_columns(self) -> None:
        """Every column shown, at its original width."""
        self._restoring = True
        for col, name in enumerate(COLUMNS):
            self._widths[name] = DEFAULT_WIDTHS[name]
            self.setColumnWidth(col, DEFAULT_WIDTHS[name])
            if col != LOCATION:
                self.setColumnHidden(col, False)
        self._restoring = False
        settings.set_hidden_file_columns(set())
        settings.set_file_column_widths(self._widths)

    def header_menu(self) -> QMenu:
        """The menu a right-click on a column heading shows: which columns are shown."""
        menu = QMenu(self)
        for name in COLUMNS:
            if name == COLUMNS[LOCATION]:
                continue
            action = menu.addAction(MENU_LABELS.get(name, name))
            action.setCheckable(True)
            action.setChecked(self.column_visible(name))
            action.setEnabled(name not in ALWAYS_SHOWN)
            action.toggled.connect(lambda on, n=name: self.set_column_visible(n, on))
        menu.addSeparator()
        menu.addAction("Reset Columns", self.reset_columns)
        return menu

    def _header_menu(self, pos) -> None:
        self.header_menu().exec(self.horizontalHeader().mapToGlobal(pos))

    def _section_resized(self, col: int, _old: int, new: int) -> None:
        if self._restoring or self.isColumnHidden(col) or new <= 0:
            return
        hdr = self.horizontalHeader()
        last = max((c for c in range(len(COLUMNS)) if not self.isColumnHidden(c)), key=hdr.visualIndex, default=None)
        if col == last:
            return  # (the stretched one: its width isn't the user's choice)
        self._widths[COLUMNS[col]] = new
        self._save_timer.start()

    def _save_widths(self) -> None:
        settings.set_file_column_widths(self._widths)

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
