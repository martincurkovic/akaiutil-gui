"""Disks -> partitions -> volumes, loaded lazily as nodes are expanded."""

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QStyle, QTreeWidget, QTreeWidgetItem

from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import Disk, Partition, Volume

from . import theme
from .tasks import run_async
from .volume_colors import swatch_icon

_ROLE_OBJ = Qt.ItemDataRole.UserRole
_ROLE_LOADED = Qt.ItemDataRole.UserRole + 1

_DISK_LABELS = {
    "HD": "S1000/S3000 hard disk",
    "HD9": "S900 hard disk",
    "FLL": "Floppy (DD)",
    "FLH": "Floppy (HD)",
}


class DiskTree(QTreeWidget):
    nodeSelected = Signal(object)  # Disk | Partition | Volume | None
    disksLoaded = Signal(list)  # list[Disk], once the top level is built
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(16)
        self.setAnimated(False)
        self._engine: AkaiEngine | None = None
        self._disks: dict[int, Disk] = {}
        self._volume_order: dict[int, dict[tuple[str, int], int]] = {}  # disk -> DiskUsage.volume_order()
        self._generation = 0  # bumped on every reload so stale async results are dropped
        self.itemExpanded.connect(self._on_expanded)
        self.currentItemChanged.connect(self._on_current)
        theme.notifier.changed.connect(self._refresh_swatches)  # swatch pixmaps bake the colour in

    def mouseDoubleClickEvent(self, event) -> None:
        # A second click within the double-click interval arrives as a
        # MouseButtonDblClick, which QTreeView ignores on the expand arrow, so
        # the quick second click of "expand, then collapse" was silently lost.
        # Treat it as the ordinary click it is.
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
            and pos.x() < self.visualRect(index).left()  # left of the item = branch area
        ):
            self.mousePressEvent(event)
            self.mouseReleaseEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def drawBranches(self, painter: QPainter, rect: QRect, index) -> None:
        """Paint the expand/collapse chevron ourselves.

        With the app stylesheet active, Qt stops painting the arrow of a folder
        once it has no children, even with ShowIndicator set, and the
        `:has-children` QSS selector can't be made to cover that case. Painting
        it here keeps the arrow on empty folders (pointing down once expanded,
        like Finder) and is the same glyph the stylesheet used to draw.
        """
        if not (index.isValid() and self.model().hasChildren(index)):
            super().drawBranches(painter, rect, index)
            return
        indent = self.indentation()
        # `rect` spans the whole indentation of the row; the item's own arrow
        # sits in its rightmost `indent`-wide cell
        cell = QRect(rect.right() - indent + 1, rect.top(), indent, rect.height())
        size = 12
        points = (
            [QPointF(2, 4), QPointF(6, 8), QPointF(10, 4)]  # down: expanded
            if self.isExpanded(index)
            else [QPointF(4, 2), QPointF(8, 6), QPointF(4, 10)]  # right: collapsed
        )
        pen = QPen(QColor(theme.current_palette()["text"]), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        painter.translate(cell.center().x() + 0.5 - size / 2, cell.center().y() + 0.5 - size / 2)
        painter.drawPolyline(points)
        painter.restore()

    # ------------------------------------------------------------------ API
    def load(self, engine: AkaiEngine) -> None:
        self._engine = engine
        self._generation += 1
        self._disks = {}
        self._volume_order = {}
        self.clear()
        gen = self._generation
        run_async(engine.list_disks, lambda disks: self._add_disks(gen, disks), self.failed.emit)

    def disk(self, index: int) -> Disk | None:
        return self._disks.get(index)

    def unload(self) -> None:
        self._engine = None
        self._generation += 1
        self._disks = {}
        self._volume_order = {}
        self.clear()

    # ------------------------------------------------------------- building
    def _icon(self, which: QStyle.StandardPixmap):
        return self.style().standardIcon(which)

    def _add_disks(self, gen: int, disks: list[Disk]) -> None:
        if gen != self._generation:
            return
        for d in disks:
            self._disks[d.index] = d
            floppy = d.type.startswith("FL")
            label = _DISK_LABELS.get(d.type, d.type)
            text = f"disk{d.index}  ·  {label}  ·  {d.total_mb:g} MB"
            if d.partitions == 0:
                text += "  ·  no Akai filesystem found"
            item = QTreeWidgetItem([text])
            item.setIcon(0, self._icon(QStyle.StandardPixmap.SP_DriveFDIcon if floppy else QStyle.StandardPixmap.SP_DriveHDIcon))
            item.setToolTip(0, f"{d.partitions} partition(s), {d.free_mb:g} MB free ({d.free_pct:g}%)")
            item.setData(0, _ROLE_OBJ, d)
            self.addTopLevelItem(item)
            if d.partitions:
                self._mark_expandable(item)
                item.setExpanded(True)  # triggers the partition load
        self.disksLoaded.emit(disks)
        if disks:  # start with the first disk selected, so its details and space bar are showing
            self.setCurrentItem(self.topLevelItem(0))

    def _mark_expandable(self, item: QTreeWidgetItem) -> None:
        """A folder whose children load lazily.

        ShowIndicator keeps the expand arrow visible (turned to point down)
        even if the folder turns out to be empty, like Finder does; by default
        Qt hides the arrow of an item without children.
        """
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        item.addChild(QTreeWidgetItem(["Loading…"]))

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_LOADED) or self._engine is None:
            return
        item.setData(0, _ROLE_LOADED, True)
        obj, eng, gen = item.data(0, _ROLE_OBJ), self._engine, self._generation
        if isinstance(obj, Disk):
            run_async(lambda: eng.list_partitions(obj), lambda r: self._add_partitions(gen, item, r), self.failed.emit)
        elif isinstance(obj, Partition):
            run_async(lambda: eng.list_volumes(obj), lambda r: self._add_volumes(gen, item, r), self.failed.emit)

    def _add_partitions(self, gen: int, disk_item: QTreeWidgetItem, parts: list[Partition]) -> None:
        if gen != self._generation:
            return
        disk_item.takeChildren()
        for p in parts:
            if p.is_dd:
                text = f"{p.name}  ·  direct-to-disk  ·  {p.size_mb:g} MB"
            else:
                text = f"{p.name}  ·  {p.size_mb:g} MB  ·  {p.free_pct:g}% free"
            item = QTreeWidgetItem([text])
            item.setIcon(0, self._icon(QStyle.StandardPixmap.SP_DirIcon))
            item.setData(0, _ROLE_OBJ, p)
            if not p.is_dd:  # DD partitions hold takes, not volumes
                self._mark_expandable(item)
            disk_item.addChild(item)
        if len(parts) == 1 and not parts[0].is_dd:  # a floppy: save the user a click
            disk_item.child(0).setExpanded(True)

    def _add_volumes(self, gen: int, part_item: QTreeWidgetItem, vols: list[Volume]) -> None:
        if gen != self._generation:
            return
        part_item.takeChildren()
        for v in vols:
            item = QTreeWidgetItem([v.name])
            item.setIcon(0, swatch_icon(self._position(v), self.devicePixelRatioF()))
            item.setToolTip(0, f"{v.type} volume, OS {v.os_version}")
            item.setData(0, _ROLE_OBJ, v)
            part_item.addChild(item)
        # a lone floppy image: jump straight to its files. (With several disks
        # open, which one to auto-select would be arbitrary.)
        only_partition = part_item.parent() is not None and part_item.parent().childCount() == 1
        current = self.currentItem()
        nothing_deeper_chosen = current is None or current.parent() is None  # none, or just the disk
        if len(vols) == 1 and only_partition and self.topLevelItemCount() == 1 and nothing_deeper_chosen:
            self.setCurrentItem(part_item.child(0))

    def _position(self, v: Volume) -> int | None:
        return self._volume_order.get(v.disk, {}).get((v.partition, v.index))

    def set_volume_order(self, disk: int, order: dict[tuple[str, int], int]) -> None:
        """Once a disk's volumes are all known, their swatches can take their final colours."""
        self._volume_order[disk] = order
        self._refresh_swatches()

    def _refresh_swatches(self) -> None:
        dpr = self.devicePixelRatioF()
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            obj = item.data(0, _ROLE_OBJ)
            if isinstance(obj, Volume):
                item.setIcon(0, swatch_icon(self._position(obj), dpr))
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _on_current(self, current: QTreeWidgetItem | None, _previous) -> None:
        obj = current.data(0, _ROLE_OBJ) if current is not None else None
        self.nodeSelected.emit(obj)
