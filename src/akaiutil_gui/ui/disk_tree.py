"""Disks -> partitions -> volumes, loaded lazily as nodes are expanded."""

from pathlib import Path

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
_ROLE_TOKEN = Qt.ItemDataRole.UserRole + 2  # numbers the load that is wanted for an item; older answers are ignored

_DISK_LABELS = {
    "HD": "S1000/S3000 hard disk",
    "HD9": "S900 hard disk",
    "FLL": "Floppy (DD)",
    "FLH": "Floppy (HD)",
}


def _disk_text(d: Disk, path: str | None = None) -> str:
    name = f"  ·  {Path(path).name}" if path else ""
    text = f"disk{d.index}{name}  ·  {_DISK_LABELS.get(d.type, d.type)}  ·  {d.total_mb:g} MB"
    if d.partitions == 0:
        text += "  ·  no Akai filesystem found"
    return text


class DiskTree(QTreeWidget):
    nodeSelected = Signal(object)  # Disk | Partition | Volume | None
    contextRequested = Signal(object, object)  # (Disk | Partition | Volume | None, global QPoint): a right-click
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
        # what to select once the levels down to it have loaded: (disk, partition or None, volume number or None)
        self._reveal: tuple[int, str | None, int | None] | None = None
        self._expand_after_load: set[tuple[int, str]] = set()  # partitions to open again when a disk is reloaded
        self._refreshing = False  # a disk's children are being thrown away: the selection jumping about is noise
        self._files: dict[int, str] = {}  # disk number -> the image file it came from
        self._announce = True  # False: the next selection made by a reload is silent (nodeSelected isn't emitted)
        self._load_seq = 0
        self._generation = 0  # bumped on every reload so stale async results are dropped
        self.itemExpanded.connect(self._on_expanded)
        self.currentItemChanged.connect(self._on_current)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
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
    def load(self, engine: AkaiEngine, select: tuple[int, str | None, int | None] | None = None, files: dict[int, str] | None = None) -> None:
        """Show the disks of an engine. `select` is a (disk, partition, volume number) to land on once loaded (default:
        the first disk); `files` says which image file each disk came from."""
        self._engine = engine
        self._generation += 1
        self._disks = {}
        self._volume_order = {}
        self._files = dict(files or {})
        self._reveal = select
        self.clear()
        gen = self._generation
        run_async(engine.list_disks, lambda disks: self._add_disks(gen, disks), self.failed.emit)

    def disk_indexes(self) -> list[int]:
        return sorted(self._disks)

    def reveal_volume(self, vol: Volume) -> None:
        """Expand down to a volume and select it. The tree loads lazily, so this may have to wait for a level
        to arrive: it is re-tried each time a level finishes loading."""
        self._reveal = (vol.disk, vol.partition, vol.index)
        self._try_reveal()

    def _disk_item(self, disk_index: int) -> QTreeWidgetItem | None:
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            obj = item.data(0, _ROLE_OBJ)
            if isinstance(obj, Disk) and obj.index == disk_index:
                return item
        return None

    def _try_reveal(self) -> None:
        target = self._reveal
        if target is None:
            return
        disk_index, part_name, vnr = target
        disk_item = self._disk_item(disk_index)
        if disk_item is None:
            return
        if part_name is None:  # just the disk
            self._reveal = None
            if self.currentItem() is disk_item:
                if self._announce:
                    self.nodeSelected.emit(disk_item.data(0, _ROLE_OBJ))  # already current, so no signal would say it changed
                self._announce = True
            else:
                self._select(disk_item)
            return
        if not disk_item.isExpanded():
            disk_item.setExpanded(True)
        part_item = next((c for c in map(disk_item.child, range(disk_item.childCount())) if isinstance(c.data(0, _ROLE_OBJ), Partition) and c.data(0, _ROLE_OBJ).name == part_name), None)
        if part_item is None:
            return  # the partitions are still loading
        if vnr is None:  # just the partition
            self._reveal = None
            self._select(part_item)
            return
        if not part_item.isExpanded():
            part_item.setExpanded(True)
        vol_item = next(
            (c for c in map(part_item.child, range(part_item.childCount()))
             if isinstance(c.data(0, _ROLE_OBJ), Volume) and (c.data(0, _ROLE_OBJ).disk, c.data(0, _ROLE_OBJ).partition, c.data(0, _ROLE_OBJ).index) == (disk_index, part_name, vnr)),
            None,
        )
        if vol_item is None:
            loading = part_item.childCount() == 1 and part_item.child(0).data(0, _ROLE_OBJ) is None
            if loading or not part_item.data(0, _ROLE_LOADED):
                return  # the volumes are still loading
            self._reveal = None  # it's gone (deleted): the partition it was in is the next best thing
            self._select(part_item)
            return
        self._reveal = None
        self._select(vol_item)
        self.scrollToItem(vol_item)

    def _select(self, item: QTreeWidgetItem) -> None:
        if self._announce:
            self.setCurrentItem(item)
            return
        self._announce = True
        self._refreshing = True  # selecting without telling anyone: the window already shows what's wanted
        try:
            self.setCurrentItem(item)
        finally:
            self._refreshing = False

    def refresh_disk(self, disk_index: int, select: Disk | Partition | Volume | None = None, announce: bool = True) -> None:
        """Read a disk's partitions and volumes again (after it was changed), keeping the same partitions open and
        selecting `select` (default: whatever is selected now) once it has loaded, or the nearest thing that's left.
        With announce=False that selection doesn't emit nodeSelected."""
        disk_item = self._disk_item(disk_index)
        engine = self._engine
        if disk_item is None or engine is None:
            return
        current = select if select is not None else self.current_object()
        self._expand_after_load = {
            (disk_index, c.data(0, _ROLE_OBJ).name)
            for c in map(disk_item.child, range(disk_item.childCount()))
            if isinstance(c.data(0, _ROLE_OBJ), Partition) and c.isExpanded()
        }
        if isinstance(current, Volume) and current.disk == disk_index:
            self._reveal = (disk_index, current.partition, current.index)
        elif isinstance(current, Partition) and current.disk == disk_index:
            self._reveal = (disk_index, current.name, None)
        elif isinstance(current, Disk) and current.index == disk_index:
            self._reveal = (disk_index, None, None)
        else:
            self._reveal = None
        self._announce = announce if self._reveal is not None else True
        gen = self._generation
        self._refreshing = True
        try:
            disk_item.takeChildren()
            disk_item.setExpanded(False)
            self._mark_expandable(disk_item)
            disk_item.setData(0, _ROLE_LOADED, False)
        finally:
            self._refreshing = False
        disk_item.setExpanded(True)  # loads the partitions again; they reopen their volumes and the selection follows
        run_async(engine.list_disks, lambda disks: self._update_disks(gen, disks), self.failed.emit)

    def _update_disks(self, gen: int, disks: list[Disk]) -> None:
        """Sizes and free space of the disks as they are now (the labels and tooltips)."""
        if gen != self._generation:
            return
        for d in disks:
            item = self._disk_item(d.index)
            if item is not None:
                self._disks[d.index] = d
                item.setData(0, _ROLE_OBJ, d)
                item.setText(0, _disk_text(d, self._files.get(d.index)))
                item.setToolTip(0, self._disk_tooltip(d))

    def _disk_tooltip(self, d: Disk) -> str:
        path = self._files.get(d.index)
        return (f"{path}\n" if path else "") + f"{d.partitions} partition(s), {d.free_mb:g} MB free ({d.free_pct:g}%)"

    def disk_path(self, index: int) -> str | None:
        return self._files.get(index)

    def current_object(self):
        item = self.currentItem()
        return item.data(0, _ROLE_OBJ) if item is not None else None

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is not None:
            self.setCurrentItem(item)  # as in Finder, a right-click selects what it's about
        obj = item.data(0, _ROLE_OBJ) if item is not None else None
        self.contextRequested.emit(obj, self.viewport().mapToGlobal(pos))

    def partition(self, disk_index: int, name: str) -> Partition | None:
        disk_item = self._disk_item(disk_index)
        if disk_item is None:
            return None
        return next((p for p in (c.data(0, _ROLE_OBJ) for c in map(disk_item.child, range(disk_item.childCount()))) if isinstance(p, Partition) and p.name == name), None)

    def volumes_in(self, disk_index: int, partition: str) -> list[Volume]:
        """The volumes shown in a partition (as far as they have been loaded)."""
        disk_item = self._disk_item(disk_index)
        if disk_item is None:
            return []
        for c in map(disk_item.child, range(disk_item.childCount())):
            part = c.data(0, _ROLE_OBJ)
            if isinstance(part, Partition) and part.name == partition:
                return [v for v in (g.data(0, _ROLE_OBJ) for g in map(c.child, range(c.childCount()))) if isinstance(v, Volume)]
        return []

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
            item = QTreeWidgetItem([_disk_text(d, self._files.get(d.index))])
            item.setIcon(0, self._icon(QStyle.StandardPixmap.SP_DriveFDIcon if floppy else QStyle.StandardPixmap.SP_DriveHDIcon))
            item.setToolTip(0, self._disk_tooltip(d))
            item.setData(0, _ROLE_OBJ, d)
            self.addTopLevelItem(item)
            if d.partitions:
                self._mark_expandable(item)
                item.setExpanded(True)  # triggers the partition load
        wanted = self._reveal is not None
        self._try_reveal()
        self.disksLoaded.emit(disks)
        if disks and not wanted:  # start with the first disk selected, so its details and space bar are showing
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
        self._load_seq += 1
        token = self._load_seq
        item.setData(0, _ROLE_TOKEN, token)
        obj, eng, gen = item.data(0, _ROLE_OBJ), self._engine, self._generation
        if isinstance(obj, Disk):
            run_async(lambda: eng.list_partitions(obj), lambda r: self._add_partitions(gen, item, r, token), self.failed.emit)
        elif isinstance(obj, Partition):
            run_async(lambda: eng.list_volumes(obj), lambda r: self._add_volumes(gen, item, r, token), self.failed.emit)

    def _stale(self, gen: int, item: QTreeWidgetItem, token: int) -> bool:
        """True if this answer is to a question that no longer matters: the tree was reloaded, or the item was
        thrown away or asked again since."""
        return gen != self._generation or item.treeWidget() is not self or item.data(0, _ROLE_TOKEN) != token

    def _add_partitions(self, gen: int, disk_item: QTreeWidgetItem, parts: list[Partition], token: int) -> None:
        if self._stale(gen, disk_item, token):
            return
        disk_item.takeChildren()
        if not parts:  # a disk with nothing on it (not formatted, or just erased): nothing to open
            disk_item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)
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
        disk_index = disk_item.data(0, _ROLE_OBJ).index
        for i in range(disk_item.childCount()):  # the partitions that were open before a reload
            child = disk_item.child(i)
            obj = child.data(0, _ROLE_OBJ)
            if (disk_index, obj.name) in self._expand_after_load and child.childCount():
                child.setExpanded(True)
        self._expand_after_load = {k for k in self._expand_after_load if k[0] != disk_index}
        self._try_reveal()

    def _add_volumes(self, gen: int, part_item: QTreeWidgetItem, vols: list[Volume], token: int) -> None:
        if self._stale(gen, part_item, token):
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
        self._try_reveal()
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
        if self._refreshing:
            return
        obj = current.data(0, _ROLE_OBJ) if current is not None else None
        self.nodeSelected.emit(obj)
