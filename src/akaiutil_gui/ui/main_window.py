"""The main window."""

import os
import re
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSettings, QThreadPool

from akaiutil_gui import __version__, akai_names, describe, search, settings
from akaiutil_gui.audio.sample import LoadedSample, load_wav
from akaiutil_gui.formatting import human_size
from akaiutil_gui.engine import fetch, formats
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.locate import candidates, find_akaiutil
from akaiutil_gui.engine.models import CatalogEntry, Disk, DiskUsage, ExportResult, FileEntry, Partition, Volume

from . import edit_dialogs, reveal, theme
from .details_view import DetailsView
from .disk_tree import DiskTree
from .notices_dialog import NoticesDialog
from .preview_panel import PreviewPanel
from .settings_dialog import SettingsDialog
from .file_table import FileTable
from .tasks import run_async
from .update_helper import CHECK_INTERVAL_SECONDS, UpdateCheckRunner
from .usage_bar import UsageSummary

IMAGE_FILTER = "Disk images (*.img *.iso *.bin *.dsk *.hfe *.fdi *.raw);;All files (*)"


def export_folder_name(vol: Volume, with_disk: bool) -> str:
    """A folder name for one volume's files when several volumes are exported together."""
    name = f"{vol.partition} - {vol.name}"
    if with_disk:
        name = f"disk{vol.disk} {name}"
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip(" .") or "volume"


class _TrafficRelay(QObject):
    """Engine traffic is produced on worker threads; a signal hops it to the GUI thread."""

    text = Signal(str)

    def feed(self, direction: str, text: str) -> None:
        try:
            self.text.emit(text)
        except RuntimeError:
            pass  # window already destroyed while a worker thread was still talking


class _ProgressRelay(QObject):
    """Setup progress is reported from a worker thread; a signal hops it to the GUI thread."""

    message = Signal(str)


class _OverwriteRequest:
    def __init__(self, path: Path):
        self.path = path
        self.answer = False
        self.done = threading.Event()


class _OverwriteAsker(QObject):
    """Lets the export worker thread ask the user a question and wait for the answer."""

    request = Signal(object)

    def __init__(self, parent_window: QWidget):
        super().__init__(parent_window)
        self._window = parent_window
        self._mode: str | None = None  # "all" | "none" once the user chose
        self.request.connect(self._show)

    def ask(self, path: Path) -> bool:  # called on the worker thread
        if self._mode is not None:
            return self._mode == "all"
        req = _OverwriteRequest(path)
        self.request.emit(req)
        req.done.wait()
        return req.answer

    @Slot(object)
    def _show(self, req: _OverwriteRequest) -> None:  # runs on the GUI thread
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("File exists")
        box.setText(f"“{req.path.name}” already exists in {req.path.parent}.")
        box.setInformativeText("Replace it with the file from the disk image?")
        replace = box.addButton("Replace", QMessageBox.ButtonRole.AcceptRole)
        replace_all = box.addButton("Replace All", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        skip_all = box.addButton("Skip All", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(skip)
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace_all:
            self._mode = "all"
        elif clicked is skip_all:
            self._mode = "none"
        req.answer = clicked in (replace, replace_all)
        req.done.set()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("akaiutil-gui")
        self.setAcceptDrops(True)
        self.resize(1180, 720)

        self._engine: AkaiEngine | None = None
        self._engine_binary: Path | None = None
        self._images: list[str] = []
        self._disk_files: dict[int, str] = {}  # disk number -> the image file akaiutil says it came from
        self._open_select: tuple[int, str | None, int | None] | None = None  # where to land once a reopen has loaded
        self._volume: Volume | None = None
        self._volume_details: describe.Details | None = None
        self._pending = 0
        self._setup_running = False  # akaiutil is being downloaded right now
        self._update_runner = UpdateCheckRunner(self)
        self._catalog: list[CatalogEntry] = []  # every file on every open disk, for searching
        self._in_results = False  # the file table is showing search results, not a volume's files
        self._volume_files: list[FileEntry] = []  # the selected volume's files, to put back when a search ends
        self._files_title_text = "Files"
        self._pending_reveal: tuple[Volume, int] | None = None  # select this file once its volume has loaded
        self._preview_token = 0  # numbers each preview load; only the newest may show its result
        self._preview_cache: OrderedDict[tuple, LoadedSample] = OrderedDict()  # decoded samples, so replaying is instant
        self._preview_dir: str | None = None  # scratch folder the WAVs are converted into
        self._theme_actions: dict[str, QAction] = {}
        # Details requests run on worker threads and can finish in any order, so each gets a number
        # and only _info_current may show its result (0: nothing in flight is wanted any more).
        self._info_seq = 0
        self._info_current = 0
        self._volume_token = 0  # the request for the selected volume's own details
        self._files_token = 0
        self._usage_token = 0
        self._usage_cache: dict[int, DiskUsage] = {}  # dropped for a disk whenever it is edited
        self._usage_waiting: dict[int, list] = {}  # disk -> callbacks for a load that's under way
        self._usage_epoch: dict[int, int] = {}  # bumped when a disk is changed: a load that started earlier is out of date
        self._writable = False  # akaiutil is running read/write (Edit > Allow Editing)
        self._writing = 0  # edits to a disk that are under way (only one at a time)
        self._status_hold = 0.0  # until then the status bar keeps the message about an edit (a file count would replace it)

        self._traffic = _TrafficRelay(self)
        self._traffic.text.connect(self._append_console)

        self._build_ui()
        self._build_menus()
        self._update_actions()
        self._restore_geometry()

    # ----------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        # left: disk tree
        self.tree = DiskTree()
        self.tree.nodeSelected.connect(self._on_node_selected)
        self.tree.disksLoaded.connect(self._prefetch_usage)
        self.tree.failed.connect(self._report_error)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        self.disks_title = self._title("Disks")
        lv.addWidget(self.disks_title)
        lv.addWidget(self.tree)
        self.usage = UsageSummary()
        lv.addWidget(self.usage)

        # right top: file list + export buttons
        self.files_title = self._title("Files")
        self.export_btn = QPushButton("Export…")
        self.export_btn.setToolTip("Copy the selected files out of the disk image, unchanged")
        self.export_btn.clicked.connect(lambda: self._export(as_wav=False))
        self.export_wav_btn = QPushButton("Export as WAV…")
        self.export_wav_btn.setToolTip("Convert the selected samples to WAV files")
        self.export_wav_btn.clicked.connect(lambda: self._export(as_wav=True))
        self.table = FileTable()
        self.table.selectionFilesChanged.connect(self._on_files_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context)
        self.tree.contextRequested.connect(self._on_tree_context)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(6)
        header = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all files…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(200)
        self.search.setMaximumWidth(300)
        self.search_kind = QComboBox()
        for value, label in search.KINDS:
            self.search_kind.addItem(label, value)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)  # so typing doesn't refilter the table on every keystroke
        self._search_timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.search_kind.currentIndexChanged.connect(lambda _i: self._search_timer.start())
        header.addWidget(self.files_title, 1)
        header.addWidget(self.search)
        header.addWidget(self.search_kind)
        header.addWidget(self.export_btn)
        header.addWidget(self.export_wav_btn)
        tv.addLayout(header)
        # The right-hand header row is as tall as its tallest widget (the search box, filter and buttons); give the
        # left title the same height (the text stays vertically centred) so the tree and the file table start at
        # the same y.
        self.disks_title.setMinimumHeight(max(w.sizeHint().height() for w in (self.export_btn, self.search, self.search_kind)))
        tv.addWidget(self.table)
        self.preview = PreviewPanel()
        self.preview.hide()  # appears when a single sample is selected
        tv.addWidget(self.preview)
        # Space plays/stops, like in a DAW or Finder's Quick Look
        space = QShortcut(QKeySequence(Qt.Key.Key_Space), self.table)
        space.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        space.activated.connect(self.preview.toggle)
        self.table.itemActivated.connect(lambda _item: self._activate_result())  # double-click or Enter
        for widget in (self.table, self.tree):  # F2 and Delete work where the thing to rename or delete is
            rename_key = QShortcut(QKeySequence(Qt.Key.Key_F2), widget)
            rename_key.setContext(Qt.ShortcutContext.WidgetShortcut)
            rename_key.activated.connect(self._rename_focused)
            for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                delete_key = QShortcut(QKeySequence(key), widget)
                delete_key.setContext(Qt.ShortcutContext.WidgetShortcut)
                delete_key.activated.connect(self._delete_focused)
        find = QShortcut(QKeySequence.StandardKey.Find, self)
        find.activated.connect(self._focus_search)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search)
        escape.setContext(Qt.ShortcutContext.WidgetShortcut)
        escape.activated.connect(self._escape_search)

        # right bottom: details (readable by default; the raw akaiutil text on request)
        self.info = DetailsView()
        self.raw_btn = QPushButton("Raw output")
        self.raw_btn.setCheckable(True)
        self.raw_btn.setToolTip("Show exactly what akaiutil printed")
        self.raw_btn.toggled.connect(self.info.set_raw_mode)
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(6)
        details_header = QHBoxLayout()
        details_header.addWidget(self._title("Details"), 1)
        details_header.addWidget(self.raw_btn)
        bv.addLayout(details_header)
        bv.addWidget(self.info)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(top)
        right.addWidget(bottom)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 1)  # the readable details run long, give them room
        right.setChildrenCollapsible(False)

        browser = QSplitter(Qt.Orientation.Horizontal)
        browser.addWidget(left)
        browser.addWidget(right)
        browser.setStretchFactor(0, 1)
        browser.setStretchFactor(1, 3)
        browser.setSizes([340, 840])
        browser.setChildrenCollapsible(False)
        browser_page = QWidget()
        bl = QVBoxLayout(browser_page)
        bl.setContentsMargins(12, 12, 12, 8)
        bl.addWidget(browser)

        # empty state
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel("Open an Akai disk image to get started")
        label.setObjectName("emptyState")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("or drop image files onto this window")
        hint.setObjectName("mutedLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        open_btn = QPushButton("Open Disk Image…")
        open_btn.clicked.connect(self.open_dialog)
        ev.addWidget(label)
        ev.addSpacing(8)
        ev.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignCenter)
        ev.addSpacing(8)
        ev.addWidget(hint)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(browser_page)
        self.setCentralWidget(self.stack)

        # console dock
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(mono)
        self.console.setMaximumBlockCount(5000)
        self.console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.console_dock = QDockWidget("akaiutil console (raw traffic)", self)
        self.console_dock.setWidget(self.console)
        self.console_dock.setObjectName("consoleDock")
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)
        self.console_dock.hide()

        # status bar
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)  # indeterminate
        self.busy_bar.setFixedWidth(110)
        self.busy_bar.setFixedHeight(12)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.hide()
        self.mode_label = QLabel("Read-only")
        self.mode_label.setObjectName("modeLabel")
        self.statusBar().setSizeGripEnabled(False)  # the corner square; macOS windows have no grip
        self.statusBar().addPermanentWidget(self.busy_bar)
        self.statusBar().addPermanentWidget(self.mode_label)

    def _title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("panelTitle")
        return label

    def _build_menus(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        self.act_open = self._action("&Open Disk Image…", self.open_dialog, QKeySequence.StandardKey.Open)
        self.act_add = self._action("&Add Disk Image…", self.add_dialog, QKeySequence("Ctrl+Shift+O"))
        self.act_close_disk = self._action("Close &Disk Image", self._close_selected_disk, QKeySequence("Ctrl+Shift+W"))
        self.act_close = self._action("Close All &Images", self.close_images, QKeySequence("Ctrl+W"))
        self.act_export = self._action("&Export…", lambda: self._export(False), QKeySequence("Ctrl+E"))
        self.act_export_wav = self._action("Export as &WAV…", lambda: self._export(True), QKeySequence("Ctrl+Shift+E"))
        self.act_new_image = self._action("&New Disk Image…", self._new_image, QKeySequence.StandardKey.New)
        act_quit = self._action("&Quit", self.close, QKeySequence.StandardKey.Quit)
        act_quit.setMenuRole(QAction.MenuRole.QuitRole)
        file_menu.addActions([self.act_new_image, self.act_open, self.act_add])
        file_menu.addSeparator()
        file_menu.addActions([self.act_close_disk, self.act_close])
        file_menu.addSeparator()
        file_menu.addActions([self.act_export, self.act_export_wav])
        file_menu.addSeparator()
        settings_action = self._action("&Settings…", self._open_settings, QKeySequence.StandardKey.Preferences)
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)  # macOS moves it to the app menu
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        self.edit_menu = mb.addMenu("&Edit")
        self.act_allow_edit = QAction("Allow &Editing", self, checkable=True)
        self.act_allow_edit.setToolTip("Let the app change the disk images (rename, delete, create). Off: they are only read.")
        self.act_allow_edit.triggered.connect(self._toggle_editing)
        self.act_new_volume = self._action("New &Volume…", self._new_volume)
        self.act_rename = self._action("&Rename…", self._rename_focused)
        self.act_delete = self._action("&Delete…", self._delete_focused)
        self.act_format = self._action("&Format Disk…", self._format_disk)
        self.edit_menu.addAction(self.act_allow_edit)
        self.edit_menu.addSeparator()
        self.edit_menu.addActions([self.act_new_volume, self.act_rename, self.act_delete])
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.act_format)
        self.edit_menu.aboutToShow.connect(self._update_edit_actions)

        view_menu = mb.addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        current = theme.current_preference()
        for label, value in theme.CHOICES:
            act = QAction(label, self, checkable=True)
            act.setChecked(value == current)
            act.triggered.connect(lambda _checked=False, v=value: self._set_theme(v))
            self._theme_actions[value] = act
            group.addAction(act)
            theme_menu.addAction(act)
        view_menu.addSeparator()
        toggle = self.console_dock.toggleViewAction()
        toggle.setText("&Console")
        toggle.setShortcut(QKeySequence("Ctrl+J"))
        view_menu.addAction(toggle)

        tools_menu = mb.addMenu("&Tools")
        tools_menu.addAction(self._action("Locate &akaiutil…", self.locate_binary))

        help_menu = mb.addMenu("&Help")
        about = self._action("&About akaiutil-gui", self._about)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        help_menu.addAction(about)
        help_menu.addAction(self._action("Check for &Updates…", lambda: self._update_runner.start(manual=True)))
        help_menu.addAction(self._action("Third-party &notices…", lambda: NoticesDialog(self).exec()))

    def _action(self, text, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        act.triggered.connect(lambda _checked=False: slot())
        if shortcut is not None:
            act.setShortcut(shortcut)
        return act

    def _update_actions(self) -> None:
        has_images = bool(self._images)
        rows = self.table.selected_rows()
        can_export = bool(rows) and all(v is not None for v, _ in rows) and self._pending == 0
        can_wav = can_export and any(f.is_sample for _, f in rows)
        self.act_close.setEnabled(has_images)
        self.act_add.setEnabled(True)
        self.act_close_disk.setEnabled(has_images and self._selected_disk() is not None and self._writing == 0)
        self.search.setEnabled(has_images)
        self.search_kind.setEnabled(has_images)
        for w in (self.act_export, self.export_btn):
            w.setEnabled(can_export)
        for w in (self.act_export_wav, self.export_wav_btn):
            w.setEnabled(can_wav)
        self._update_edit_actions()
        self._sync_mode()

    # ------------------------------------------------------------- opening
    def open_dialog(self) -> None:
        start = settings.get_last_dir("open") or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Open disk image(s)", start, IMAGE_FILTER)
        if paths:
            settings.set_last_dir("open", str(Path(paths[0]).parent))
            self.open_images(paths)

    def open_images(self, paths: list[str], writable: bool = False, select: tuple[int, str | None, int | None] | None = None) -> None:
        """Open these disk images (replacing whatever is open). Read-only unless `writable` and every file allows it.
        `select` is a (disk, partition, volume number) to land on instead of the first disk."""
        binary = find_akaiutil(settings.get_akaiutil_path())
        if binary is None:
            # first run: offer to get akaiutil, then carry on with what the user asked for
            self._set_up_akaiutil(then=lambda: self.open_images(paths, writable, select))
            return
        if self._engine is not None:
            self._engine.close()
        self._engine = AkaiEngine(binary, on_traffic=self._traffic.feed)
        self._engine_binary = binary
        self._writable = False  # until akaiutil says otherwise (see _opened)
        self._reset_views()
        self._images = [str(p) for p in paths]
        self._open_select = select
        note = ""
        if writable:
            locked = [Path(p).name for p in self._images if not os.access(p, os.W_OK)]
            if locked:
                writable, note = False, f"Editing is off: {', '.join(locked)} can't be written to."
        engine = self._engine
        self.statusBar().showMessage("Opening…")
        self._run(
            lambda: engine.open(self._images, writable=writable),
            lambda banner: self._opened(engine, banner, note),
            self._open_failed,
        )

    def _opened(self, engine: AkaiEngine, banner: str, note: str = "") -> None:
        if engine is not self._engine:
            return  # superseded by a newer open
        self._writable = engine.is_writable
        m = re.search(r"Rev\.\s*([\w.]+)", banner)
        names = ", ".join(Path(p).name for p in self._images)
        self.setWindowTitle(f"{names} — akaiutil-gui")
        message = f"Opened {names}  ·  akaiutil {m.group(1) if m else '?'}  ·  {self._engine_binary}"
        # akaiutil reports unreadable/unformatted disks only in its start-up chatter
        problems = [ln.strip() for ln in banner.splitlines() if re.search(r"invalid format|cannot|error", ln, re.I)]
        if problems:
            message = f"⚠ {problems[0]}  ·  {message}"
        if note:
            message = f"{note}  ·  {message}"
        self.statusBar().showMessage(message)
        self._disk_files = {int(n): path for n, path in re.findall(r'^disk(\d+): "(.*)"$', banner, re.M)}
        if not self._disk_files:  # (akaiutil numbers them in the order they were given)
            self._disk_files = {i: str(Path(p).resolve()) for i, p in enumerate(self._images)}
        self.stack.setCurrentIndex(1)
        select, self._open_select = self._open_select, None
        self.tree.load(engine, select=select, files=self._disk_files)
        self._update_actions()

    # ----------------------------------------------- adding and closing single disks
    def add_dialog(self) -> None:
        start = settings.get_last_dir("open") or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Add disk image(s)", start, IMAGE_FILTER)
        if paths:
            settings.set_last_dir("open", str(Path(paths[0]).parent))
            self.add_images(paths)

    def add_images(self, paths: list[str]) -> None:
        """Open more disk images next to the ones already open. (akaiutil takes its images when it starts, so this restarts it
        on all of them; what you were looking at, and the editing mode, carry over.)"""
        if not self._images:
            self.open_images(paths)
            return
        have = {Path(p).resolve() for p in self._images}
        new: list[str] = []
        for p in paths:
            resolved = Path(p).resolve()
            if resolved not in have:
                have.add(resolved)
                new.append(str(p))
        if not new:
            self.statusBar().showMessage("That image is already open.", 6000)
            return
        if self._busy():
            return
        self._reopen([*self._images, *new])

    def _reopen(self, paths: list[str], disk_map=None) -> None:
        """Restart on another set of images, keeping the editing mode and the selection (`disk_map` says what number the
        selected disk has now, or None if it is gone)."""
        obj = self.tree.current_object()
        select = None
        if obj is not None:
            old = obj.index if isinstance(obj, Disk) else obj.disk
            new = disk_map(old) if disk_map is not None else old
            if new is not None:
                partition = None if isinstance(obj, Disk) else obj.name if isinstance(obj, Partition) else obj.partition
                select = (new, partition, obj.index if isinstance(obj, Volume) else None)
        self.open_images(paths, writable=self._writable, select=select)

    def _close_selected_disk(self) -> None:
        disk = self._selected_disk()
        if disk is not None:
            self.close_disk(disk)

    def close_disk(self, disk: Disk) -> None:
        """Close one image and keep the others open. The disks after it get new numbers, since akaiutil numbers them in order."""
        if self._engine is None or self._busy():
            return
        path = self._disk_files.get(disk.index)
        if path is not None:
            remaining = [p for p in self._images if Path(p).resolve() != Path(path).resolve()]
        else:
            remaining = [p for i, p in enumerate(self._images) if i != disk.index]
        if not remaining:
            self.close_images()
            return
        gone = disk.index
        self._reopen(remaining, disk_map=lambda old: None if old == gone else old - (old > gone))

    def _reveal_disk(self, disk: Disk) -> None:
        path = self.tree.disk_path(disk.index)
        if path is not None and not reveal.reveal(path):
            self.statusBar().showMessage(f"Couldn't open the folder of {Path(path).name}.", 6000)

    def _open_failed(self, message: str) -> None:
        self.close_images()
        QMessageBox.critical(self, "Couldn't open disk image", message)

    def close_images(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            run_async(engine.close)  # can take a moment; keep the GUI responsive
        self._images = []
        self._disk_files = {}
        self._writable = False
        self._reset_views()
        self.stack.setCurrentIndex(0)
        self.setWindowTitle("akaiutil-gui")
        self.statusBar().clearMessage()
        self._update_actions()

    def _reset_views(self) -> None:
        self._volume = None
        self._volume_details = None
        self._info_current = 0
        self._files_token += 1
        self._catalog = []
        self._volume_files = []
        self._pending_reveal = None
        self._reset_search_ui()
        self._preview_token += 1
        self._preview_cache.clear()
        self.preview.clear()
        self.preview.hide()
        self._usage_token += 1
        self._usage_cache.clear()
        self._usage_waiting.clear()
        self._usage_epoch.clear()
        self.usage.clear()
        self.tree.unload()
        self.table.clear_files()
        self._set_files_title("Files")
        self.info.show_details(None)

    # --------------------------------------------------------- binary lookup
    def _set_up_akaiutil(self, then) -> None:
        """akaiutil isn't installed: offer to fetch it (or point at one), then call `then`."""
        if self._setup_running:
            return
        choice = self._ask_setup()
        if choice == "download":
            self._download_akaiutil(then)
        elif choice == "locate" and self.locate_binary() is not None:
            then()

    def _ask_setup(self) -> str:
        """The first-run question. Returns "download", "locate" or "cancel"."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Set up akaiutil")
        box.setText("akaiutil-gui needs the akaiutil program to read disk images, and it isn't installed yet.")
        box.setInformativeText(
            f"It can {fetch.describe_plan()}. The download is checked against a known checksum before it is used, "
            "and nothing else on your system is changed.\n\nOr, if you already have akaiutil, point the app at it."
        )
        looked = "\n".join(str(c) for c in candidates(settings.get_akaiutil_path()))
        box.setDetailedText(f"Looked for akaiutil in:\n{looked}")
        download = box.addButton("Download and set up", QMessageBox.ButtonRole.AcceptRole)
        locate = box.addButton("Locate existing…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(download)
        box.exec()
        clicked = box.clickedButton()
        return "download" if clicked is download else "locate" if clicked is locate else "cancel"

    def _download_akaiutil(self, then) -> None:
        relay = _ProgressRelay(self)
        dialog = QProgressDialog("Starting…", "", 0, 0, self)  # 0, 0: an indeterminate bar
        dialog.setWindowTitle("Setting up akaiutil")
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        relay.message.connect(dialog.setLabelText)
        dialog.show()
        self._setup_running = True

        def finish() -> None:
            self._setup_running = False
            dialog.close()
            relay.deleteLater()

        def done(path: Path) -> None:
            finish()
            settings.set_akaiutil_path(str(path))  # remembered, so this only ever happens once
            self.statusBar().showMessage(f"akaiutil {fetch.VERSION} is ready ({path})", 8000)
            then()

        def failed(message: str) -> None:
            finish()
            QMessageBox.critical(
                self,
                "Couldn't set up akaiutil",
                f"{message}\n\nYou can also run:  uv run python scripts/get_akaiutil.py\n"
                "or use Tools ▸ Locate akaiutil… if you already have it.",
            )

        run_async(lambda: fetch.install(progress=relay.message.emit), done, failed)

    def locate_binary(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(self, "Locate the akaiutil executable")
        if not path:
            return None
        settings.set_akaiutil_path(path)
        found = find_akaiutil(path)
        if found is None:
            QMessageBox.warning(self, "Not executable", f"{path} isn't an executable file.")
            return None
        self.statusBar().showMessage(f"Using akaiutil at {found}", 5000)
        return found

    # ----------------------------------------------------------- selection
    def _prefetch_usage(self, disks: list[Disk]) -> None:
        """Volume sizes (and so the colours of the tree swatches) come from one listing per disk."""
        for disk in disks:
            self._ensure_usage(disk)

    def _ensure_usage(self, disk: Disk, then=None) -> None:
        """Call `then(DiskUsage)` as soon as the disk's usage is known (immediately if cached)."""
        engine = self._engine
        if engine is None:
            return
        cached = self._usage_cache.get(disk.index)
        if cached is not None:
            if then is not None:
                then(cached)
            return
        started = disk.index in self._usage_waiting
        waiting = self._usage_waiting.setdefault(disk.index, [])
        if then is not None:
            waiting.append(then)
        if started:
            return
        epoch = self._usage_epoch.get(disk.index, 0)

        def done(usage: DiskUsage) -> None:
            if engine is not self._engine or epoch != self._usage_epoch.get(disk.index, 0):
                return  # the image was closed or replaced meanwhile, or the disk was edited since this was asked
            self._usage_cache[disk.index] = usage
            self._rebuild_catalog()
            self.tree.set_volume_order(disk.index, usage.volume_order())
            for callback in self._usage_waiting.pop(disk.index, []):
                callback(usage)

        def failed(message: str) -> None:
            if engine is self._engine and epoch == self._usage_epoch.get(disk.index, 0):
                self._usage_waiting.pop(disk.index, None)
                self._report_error(message)

        run_async(lambda: engine.disk_usage(disk), done, failed)

    def _update_usage(self, obj) -> None:
        """Space bar under the tree: the whole disk, or the partition that was selected."""
        self._usage_token += 1
        token = self._usage_token
        disk = self.tree.disk(obj.index if isinstance(obj, Disk) else obj.disk) if obj is not None else None
        if self._engine is None or disk is None:
            self.usage.clear()
            return

        def show(usage: DiskUsage) -> None:
            if token != self._usage_token:
                return  # the selection moved on while this was loading
            if isinstance(obj, Disk):
                self.usage.show_disk(usage)
            else:
                self.usage.show_partition(usage, obj.name if isinstance(obj, Partition) else obj.partition)

        if disk.index not in self._usage_cache:
            self.usage.show_loading()
        self._ensure_usage(disk, show)

    # ----------------------------------------------------------------- search
    def _searching(self) -> bool:
        return bool(self.search.text().strip()) or self.search_kind.currentData() != search.ALL

    def _reset_search_ui(self) -> None:
        """Clear the search box and type filter without reacting to it."""
        self._search_timer.stop()
        self._in_results = False
        for widget, clear in ((self.search, lambda: self.search.clear()), (self.search_kind, lambda: self.search_kind.setCurrentIndex(0))):
            widget.blockSignals(True)
            clear()
            widget.blockSignals(False)
        self.files_title.setText(self._files_title_text)

    def _rebuild_catalog(self) -> None:
        self._catalog = [entry for index in sorted(self._usage_cache) for entry in self._usage_cache[index].catalog]
        if self._searching():
            self._apply_search()  # the index just grew: results may have too

    def _index_ready(self) -> bool:
        return set(self.tree.disk_indexes()) <= set(self._usage_cache)

    def _focus_search(self) -> None:
        if self.search.isEnabled():
            self.search.setFocus()
            self.search.selectAll()

    def _escape_search(self) -> None:
        self.search.clear()
        self.search_kind.setCurrentIndex(0)
        self.table.setFocus()

    def _apply_search(self) -> None:
        if self._engine is None:
            return
        if not self._searching():
            self._leave_search()
            return
        if not self._index_ready():
            self.files_title.setText("Indexing the disks…")
            return
        query = self.search.text().strip()
        results, truncated = search.filter_catalog(self._catalog, query, self.search_kind.currentData())
        self._in_results = True
        self.table.set_results(results)
        what = f"“{query}”" if query else self.search_kind.currentText().lower()
        title = f"{len(results):,}{'+' if truncated else ''} result{'s' if len(results) != 1 else ''} for {what}"
        self.files_title.setText(title + (f"  ·  showing the first {len(results):,}, narrow the search" if truncated else ""))
        self._update_actions()

    def _leave_search(self) -> None:
        """Back to the selected volume's files."""
        if not self._in_results:
            self.files_title.setText(self._files_title_text)
            return
        self._in_results = False
        self.files_title.setText(self._files_title_text)
        self.table.set_files(self._volume_files, self._volume)
        self._select_pending_file()
        self._update_actions()

    def _activate_result(self) -> None:
        """Double-click or Enter on a search result: show it in its volume."""
        if not self._in_results:
            return
        rows = [(v, f) for v, f in self.table.selected_rows() if v is not None]
        if not rows:
            return
        vol, f = rows[0]
        self._pending_reveal = (vol, f.index)
        current = self._volume
        self._search_timer.stop()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search_kind.blockSignals(True)
        self.search_kind.setCurrentIndex(0)
        self.search_kind.blockSignals(False)
        if current is not None and (current.disk, current.partition, current.index) == (vol.disk, vol.partition, vol.index):
            self._leave_search()  # already the selected volume: just put its files back and pick the file
        else:
            self._in_results = False  # the tree selection below loads the volume (and ends the search state)
            self.tree.reveal_volume(vol)

    def _on_node_selected(self, obj) -> None:
        self._update_usage(obj)
        self._volume = obj if isinstance(obj, Volume) else None
        self._volume_details = None
        self._info_current = 0
        self._files_token += 1
        self._volume_files = []
        self._reset_search_ui()  # picking something in the tree ends a search
        self.table.clear_files()
        engine = self._engine
        if engine is None or obj is None:
            self._set_files_title("Files")
            self.info.show_details(None)
            self._update_actions()
            return

        if isinstance(obj, Volume):
            self._set_files_title(f"Files in {obj.path}")
            token = self._files_token
            self._run(lambda: engine.list_files(obj), lambda files: self._files_loaded(token, files))
            disk = self.tree.disk(obj.disk)
            cached = self._usage_cache.get(obj.disk)  # filled for the usage bar; else it's one quick listing

            def volume_details(text: str) -> describe.Details:
                usage = cached or (engine.disk_usage(disk) if disk is not None else None)
                return describe.describe_volume(text, usage)

            self._show_info(lambda: engine.volume_info(obj), volume_details, remember=True)
        elif isinstance(obj, Partition):
            self._set_files_title("Select a volume to see its files")
            if obj.is_dd:
                self._show_info(
                    lambda: engine.list_takes_text(obj), lambda t: describe.describe_takes(f"Direct-to-disk area {obj.name}", t)
                )
            else:
                self._show_info(lambda: engine.partition_info(obj), describe.describe_partition)
        elif isinstance(obj, Disk):
            self._set_files_title("Select a volume to see its files")
            self._show_info(lambda: engine.disk_info(obj), describe.describe_disk)
        self._update_actions()

    def _files_loaded(self, token: int, files: list[FileEntry]) -> None:
        if token != self._files_token:
            return
        self._volume_files = files
        if not self._in_results:  # a search that's under way keeps the table
            self.table.set_files(files, self._volume)
        n = len(files)
        total = human_size(sum(f.size for f in files))
        if time.monotonic() >= self._status_hold:
            self.statusBar().showMessage(f"{n} file{'s' if n != 1 else ''}, {total}", 8000)
        self._select_pending_file()
        self._update_actions()

    def _select_pending_file(self) -> None:
        """After revealing a search result: select its file once the volume's list is showing."""
        if self._pending_reveal is None or self._in_results:
            return
        vol, file_index = self._pending_reveal
        if self._volume is not None and (self._volume.disk, self._volume.partition, self._volume.index) == (vol.disk, vol.partition, vol.index):
            self._pending_reveal = None
            self.table.select_file(self._volume, file_index)

    def _set_files_title(self, text: str) -> None:
        self._files_title_text = text
        if not self._in_results:
            self.files_title.setText(text)

    def _update_preview(self, rows: list[tuple[Volume | None, FileEntry]]) -> None:
        """Show the preview panel when exactly one sample is selected, and load it."""
        self._preview_token += 1
        self.preview.stop()
        engine = self._engine
        if engine is None or len(rows) != 1 or rows[0][0] is None or not rows[0][1].is_sample:
            self.preview.clear()
            self.preview.hide()
            return
        vol, f = rows[0]
        key = (vol.disk, vol.partition, vol.index, f.index, f.name)
        self.preview.show()
        cached = self._preview_cache.get(key)
        if cached is not None:
            self._preview_cache.move_to_end(key)
            self._show_preview(cached)
            return
        self.preview.show_loading(f.name)
        token = self._preview_token

        def convert() -> LoadedSample:
            info = engine.sample_info(vol, f)  # loops and play markers, from the header
            wav = engine.export_sample_wav(vol, f, self._preview_directory())
            try:
                return load_wav(wav, f.name, info)
            finally:
                wav.unlink(missing_ok=True)  # the audio is in memory now

        def done(sample: LoadedSample) -> None:
            self._preview_cache[key] = sample
            while len(self._preview_cache) > 16:
                self._preview_cache.popitem(last=False)
            if token == self._preview_token:
                self._show_preview(sample)

        def failed(message: str) -> None:
            if token == self._preview_token:
                self.preview.show_error(message)

        run_async(convert, done, failed)

    def _show_preview(self, sample: LoadedSample) -> None:
        self.preview.set_sample(sample)
        if settings.get_auto_play():
            self.preview.play()

    def _preview_directory(self) -> str:
        if self._preview_dir is None:
            self._preview_dir = tempfile.mkdtemp(prefix="akaiutil-gui-preview-")
        return self._preview_dir

    def _on_files_selected(self, _files: list[FileEntry]) -> None:
        engine = self._engine
        rows = self.table.selected_rows()
        self._update_preview(rows)
        self._update_actions()
        if engine is None:
            return
        if len(rows) == 1 and rows[0][0] is not None:
            vol, f = rows[0]
            self._show_info(lambda: engine.file_info(vol, f), describe.describe_file)
        elif len(rows) > 1:
            self._info_current = 0
            self.info.show_details(describe.describe_selection([f for _, f in rows]))
        elif self._in_results:  # a search with nothing selected: there is no volume to fall back to
            self._info_current = 0
            self.info.show_details(None)
        elif self._volume_details is not None:  # nothing selected: back to the volume's own details
            self._info_current = 0
            self.info.show_details(self._volume_details)
        elif self._volume is not None:
            # ...which are still on their way. Let that request show itself when it arrives (the file
            # list landing and clearing the selection must not cancel it), but not a file's details.
            self._info_current = self._volume_token

    def _show_info(self, fetch, describe_it, remember: bool = False) -> None:
        """Ask akaiutil (fetch), turn its text into readable Details (describe_it), show them."""
        self._info_seq += 1
        token = self._info_current = self._info_seq
        if remember:
            self._volume_token = token

        def done(details: describe.Details) -> None:
            if remember:
                self._volume_details = details
            if token == self._info_current:
                self.info.show_details(details)

        self._run(lambda: describe_it(fetch()), done)

    # -------------------------------------------------------------- export
    def _export(self, as_wav: bool) -> None:
        engine = self._engine
        rows = [(v, f) for v, f in self.table.selected_rows() if v is not None]
        if engine is None or not rows:
            return
        if as_wav:
            rows = [(v, f) for v, f in rows if f.is_sample]
            if not rows:
                QMessageBox.information(self, "Export as WAV", "None of the selected files are samples.")
                return
        dest = self._export_destination()
        if not dest:
            return

        groups: dict[tuple, tuple[Volume, list[FileEntry]]] = {}
        for vol, f in rows:
            groups.setdefault((vol.disk, vol.partition, vol.index), (vol, []))[1].append(f)
        several_volumes = len(groups) > 1
        several_disks = len({k[0] for k in groups}) > 1
        asker = _OverwriteAsker(self)
        self.statusBar().showMessage(f"Exporting {len(rows)} file(s)…")

        def work() -> ExportResult:
            merged = ExportResult()
            for vol, files in groups.values():
                # files from several volumes get a folder each, so same-named files can't overwrite each other
                target = Path(dest) / export_folder_name(vol, several_disks) if several_volumes else Path(dest)
                target.mkdir(parents=True, exist_ok=True)
                res = engine.export_files(vol, files, target, as_wav, confirm_overwrite=asker.ask)
                merged.exported += res.exported
                merged.skipped += res.skipped
                merged.failed += res.failed
            return merged

        self._run(work, lambda res: self._export_done(res, dest))

    def _export_destination(self) -> str | None:
        """Where to export to: the default folder from Settings (without asking, if that's what's set), or a
        folder the user picks, starting from the default folder or the last one used."""
        default = settings.get_default_export_folder()
        if default and not settings.get_ask_export_folder():
            if Path(default).is_dir():
                return default
            self.statusBar().showMessage(f"The default export folder {default} doesn't exist; choose another.", 8000)
        start = default if default and Path(default).is_dir() else settings.get_last_dir("export") or str(Path.home())
        dest = QFileDialog.getExistingDirectory(self, "Export to folder", start)
        if dest:
            settings.set_last_dir("export", dest)
        return dest or None

    def _export_done(self, res: ExportResult, dest: str) -> None:
        parts = [f"Exported {len(res.exported)} file(s) to {dest}"]
        if res.skipped:
            parts.append(f"{len(res.skipped)} skipped")
        if res.failed:
            parts.append(f"{len(res.failed)} failed")
        self.statusBar().showMessage("  ·  ".join(parts), 10000)
        if res.failed or res.skipped:
            lines = [f"Skipped (already exists): {p}" for p in res.skipped] + [f"Failed: {m}" for m in res.failed]
            box = QMessageBox(QMessageBox.Icon.Warning if res.failed else QMessageBox.Icon.Information,
                              "Export finished", "  ·  ".join(parts), parent=self)
            box.setDetailedText("\n".join(lines))
            box.exec()

    # -------------------------------------------------------------- editing
    def _sync_mode(self) -> None:
        """The status bar's Read-only / Editing label and the Allow Editing tick."""
        self.mode_label.setText("Editing" if self._writable else "Read-only")
        self.mode_label.setToolTip(
            "Changes are written to the disk image straight away. There is no undo."
            if self._writable
            else "The disk images are only read. Edit > Allow Editing lets the app change them."
        )
        if self.mode_label.property("editing") != self._writable:
            self.mode_label.setProperty("editing", self._writable)
            self.mode_label.style().unpolish(self.mode_label)
            self.mode_label.style().polish(self.mode_label)
        self.act_allow_edit.setEnabled(self._engine is not None)
        if self.act_allow_edit.isChecked() != self._writable:
            self.act_allow_edit.setChecked(self._writable)

    def _toggle_editing(self, checked: bool) -> None:
        if checked:
            self._ensure_editing(lambda: None)
        else:
            self._set_editing(False)
        self._sync_mode()  # a question that was answered "no" puts the tick back

    def _ensure_editing(self, then) -> None:
        """Run `then` once akaiutil is able to write, asking first if it isn't yet."""
        if self._engine is None:
            return
        if self._writable:
            then()
            return
        locked = [Path(p).name for p in self._images if not os.access(p, os.W_OK)]
        if locked:
            QMessageBox.warning(
                self,
                "Can't edit this image",
                "These files can't be written to, so they can't be edited:\n\n" + "\n".join(locked)
                + "\n\nChange their permissions (or copy them somewhere writable) and open them again.",
            )
            self._sync_mode()
            return
        if not edit_dialogs.confirm(
            self,
            "Allow editing?",
            "Editing changes the disk image files themselves.",
            "There is no undo. Keep a copy of anything you can't afford to lose.",
            action="Allow Editing",
        ):
            self._sync_mode()
            return
        self._set_editing(True, then)

    def _set_editing(self, on: bool, then=None) -> None:
        engine = self._engine
        if engine is None:
            return

        def done(_banner: str) -> None:
            if engine is not self._engine:
                return
            self._writable = on
            self._sync_mode()
            self.statusBar().showMessage(
                "Editing is on: changes are written to the disk image straight away." if on else "Editing is off.", 8000
            )
            if then is not None:
                then()

        def failed(message: str) -> None:
            if engine is not self._engine:
                return
            QMessageBox.critical(self, "Couldn't change the editing mode", message)
            if not engine.is_open:
                self.close_images()
                return
            self._writable = engine.is_writable
            self._sync_mode()

        self._run(lambda: engine.set_writable(on), done, failed)

    def _busy(self) -> bool:
        """True (and says so) while another edit is still being written."""
        if self._writing:
            self.statusBar().showMessage("Wait for the current change to finish.", 4000)
        return self._writing > 0

    def _run_edit(self, fn, on_done, on_error) -> None:
        """_run for work that changes a disk: while it runs, no other edit can start."""
        self._writing += 1
        self._update_actions()

        def finish(handler):
            def wrapped(result):
                self._writing -= 1
                self._update_actions()
                handler(result)

            return wrapped

        self._run(fn, finish(on_done), finish(on_error))

    def _selected_volume(self) -> Volume | None:
        obj = self.tree.current_object()
        return obj if isinstance(obj, Volume) else None

    def _selected_rows(self) -> list[tuple[Volume, FileEntry]]:
        return [(v, f) for v, f in self.table.selected_rows() if v is not None]

    def _focus_in(self, widget: QWidget) -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and (focus is widget or widget.isAncestorOf(focus))

    def _edit_target(self) -> tuple[str, object] | None:
        """What Rename and Delete would act on: the files selected in the list if that is where you are,
        the volume selected in the tree if that is where you are, else whichever of them there is."""
        rows, volume = self._selected_rows(), self._selected_volume()
        if self._focus_in(self.table):
            return ("files", rows) if rows else None
        if self._focus_in(self.tree):
            return ("volume", volume) if volume else None
        if rows:
            return ("files", rows)
        return ("volume", volume) if volume else None

    def _selected_disk(self) -> Disk | None:
        obj = self.tree.current_object()
        index = obj.index if isinstance(obj, Disk) else getattr(obj, "disk", None)
        return self.tree.disk(index) if index is not None else None

    def _partition_for_new_volume(self) -> Partition | None:
        obj = self.tree.current_object()
        if isinstance(obj, Volume):
            obj = self.tree.partition(obj.disk, obj.partition)
        return obj if isinstance(obj, Partition) and self._can_hold_volumes(obj) else None

    @staticmethod
    def _can_hold_volumes(part: Partition) -> bool:
        return not part.is_dd and not part.type.startswith("FL")  # a floppy's one volume is always there

    def _update_edit_actions(self) -> None:
        """Edit menu: say what Rename and Delete would do right now, and switch off what can't be done."""
        has = self._engine is not None and self._writing == 0
        target = self._edit_target() if has else None
        rename_text, delete_text, can_rename, can_delete = "&Rename…", "&Delete…", False, False
        if target is not None:
            kind, what = target
            if kind == "volume":
                rename_text, delete_text, can_rename, can_delete = "&Rename Volume…", "&Delete Volume…", True, True
            else:
                n = len(what)
                rename_text = "&Rename File…" if n == 1 else "&Rename…"
                delete_text = "&Delete File…" if n == 1 else f"&Delete {n} Files…"
                can_rename, can_delete = n == 1, True
        self.act_rename.setText(rename_text)
        self.act_delete.setText(delete_text)
        self.act_rename.setEnabled(can_rename)
        self.act_delete.setEnabled(can_delete)
        self.act_new_volume.setEnabled(has and self._partition_for_new_volume() is not None)
        self.act_format.setEnabled(has and self._selected_disk() is not None)

    def _rename_focused(self) -> None:
        target = self._edit_target()
        if target is None:
            return
        kind, what = target
        if kind == "volume":
            self._rename_volume(what)
        elif len(what) == 1:
            self._rename_file(*what[0])

    def _delete_focused(self) -> None:
        target = self._edit_target()
        if target is None:
            return
        kind, what = target
        if kind == "volume":
            self._delete_volume(what)
        else:
            self._delete_files(what)

    def _ask_name(self, what: str, current: str, volume_type: str, taken: list[str], suffix: str = "") -> str | None:
        dialog = edit_dialogs.RenameDialog(self, what, current, volume_type, taken, suffix)
        return dialog.name if dialog.exec() == dialog.DialogCode.Accepted else None

    def _rename_volume(self, vol: Volume) -> None:
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_rename_volume(vol))

    def _do_rename_volume(self, vol: Volume) -> None:
        engine = self._engine
        taken = [v.name for v in self.tree.volumes_in(vol.disk, vol.partition) if v.index != vol.index]
        name = self._ask_name("volume", vol.name, vol.type, taken)
        if engine is None or name is None:
            return
        self._run_edit(
            lambda: engine.rename_volume(vol, name),
            lambda new: self._edited(vol.disk, select=new, message=f"Renamed “{vol.name}” to “{new.name}”."),
            lambda message: self._edit_failed(vol.disk, message),
        )

    def _rename_file(self, vol: Volume, f: FileEntry) -> None:
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_rename_file(vol, f))

    def _do_rename_file(self, vol: Volume, f: FileEntry) -> None:
        engine = self._engine
        base, ext = akai_names.split_extension(f.name)
        taken: list[str] = []
        v = self._volume
        if not self._in_results and v is not None and (v.disk, v.partition, v.index) == (vol.disk, vol.partition, vol.index):
            taken = [  # the other files of the same type in this volume (the type is part of a file's name)
                akai_names.split_extension(o.name)[0]
                for o in self._volume_files
                if o.index != f.index and akai_names.split_extension(o.name)[1].casefold() == ext.casefold()
            ]
        name = self._ask_name("file", base, vol.type, taken, suffix=ext)
        if engine is None or name is None:
            return

        def done(new: FileEntry) -> None:
            self._pending_reveal = (vol, new.index) if not self._in_results else None
            self._edited(vol.disk, message=f"Renamed “{f.name}” to “{new.name}”.")

        self._run_edit(lambda: engine.rename_file(vol, f, name), done, lambda message: self._edit_failed(vol.disk, message))

    def _delete_files(self, rows: list[tuple[Volume, FileEntry]]) -> None:
        if self._engine is None or not rows or self._busy():
            return
        self._ensure_editing(lambda: self._do_delete_files(rows))

    def _do_delete_files(self, rows: list[tuple[Volume, FileEntry]]) -> None:
        engine = self._engine
        groups: dict[tuple, tuple[Volume, list[FileEntry]]] = {}
        for vol, f in rows:
            groups.setdefault((vol.disk, vol.partition, vol.index), (vol, []))[1].append(f)
        if len(rows) == 1:
            vol, f = rows[0]
            text = f"Delete “{f.name}” from {vol.name}?"
        elif len(groups) == 1:
            text = f"Delete {len(rows)} files from {rows[0][0].name}?"
        else:
            text = f"Delete {len(rows)} files from {len(groups)} volumes?"
        lines = [f"{vol.name}: {f.name}" for vol, f in rows] if len(rows) > 1 else []
        if engine is None or not edit_dialogs.confirm(self, "Delete files?", text, "This can't be undone.", details_lines=lines):
            return

        def work() -> list[str]:
            failed: list[str] = []
            for vol, files in groups.values():
                failed += engine.delete_files(vol, files)
            return failed

        disks = sorted({vol.disk for vol, _ in groups.values()})

        def done(failed: list[str]) -> None:
            for disk in disks:
                self._edited(disk)
            n = len(rows) - len(failed)
            self._status_hold = time.monotonic() + 8
            self.statusBar().showMessage(f"Deleted {n} file{'s' if n != 1 else ''}.", 8000)
            if failed:
                self._warn("Some files weren't deleted", f"{len(failed)} of {len(rows)} files couldn't be deleted.", failed)

        self._run_edit(work, done, lambda message: self._edit_failed(disks[0], message))

    def _delete_volume(self, vol: Volume) -> None:
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_delete_volume(vol))

    def _do_delete_volume(self, vol: Volume) -> None:
        engine = self._engine
        usage = self._usage_cache.get(vol.disk)
        count = next((u.files for u in usage.volumes if (u.partition, u.index) == (vol.partition, vol.index)), None) if usage else None
        detail = "Every file in it goes too. This can't be undone."
        if count is not None:
            detail = f"It holds {count} file{'s' if count != 1 else ''}, and they go too. This can't be undone."
        if engine is None or not edit_dialogs.confirm(self, "Delete volume?", f"Delete the volume “{vol.name}”?", detail):
            return
        self._run_edit(
            lambda: engine.delete_volume(vol),
            lambda _none: self._edited(vol.disk, message=f"Deleted the volume “{vol.name}”."),
            lambda message: self._edit_failed(vol.disk, message),
        )

    def _new_volume(self, part: Partition | None = None) -> None:
        part = part or self._partition_for_new_volume()
        if self._engine is None or part is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_new_volume(part))

    def _volume_types(self, part: Partition) -> tuple[list[str], str]:
        """The kinds of volume a partition can hold, and the one to suggest (what it mostly holds already)."""
        if part.type == "HD9":
            return ["S900"], "S900"
        types = ["S3000", "S1000", "CD3000"]
        existing = [v.type for v in self.tree.volumes_in(part.disk, part.name)]
        common = max((t for t in types if t in existing), key=existing.count, default="S3000")
        return types, common

    def _do_new_volume(self, part: Partition) -> None:
        engine = self._engine
        if engine is None:
            return

        def show(slots: tuple[list[int], int]) -> None:
            used, limit = slots
            if len(used) >= limit:
                QMessageBox.information(self, "New Volume", f"Partition {part.name} already holds the most volumes it can ({limit}).")
                return
            types, default = self._volume_types(part)
            taken = [v.name for v in self.tree.volumes_in(part.disk, part.name)]
            answer = self._ask_new_volume(part, used, limit, types, default, taken)
            if answer is None:
                return
            name, number, volume_type, load_number = answer
            self._run_edit(
                lambda: engine.create_volume(part, number, name, volume_type, load_number),
                lambda new: self._edited(part.disk, select=new, announce=True, message=f"Created the volume “{new.name}”."),
                lambda message: self._edit_failed(part.disk, message),
            )

        self._run(lambda: engine.volume_slots(part), show)

    def _ask_new_volume(self, part, used, limit, types, default, taken) -> tuple[str, int, str, int | None] | None:
        dialog = edit_dialogs.NewVolumeDialog(self, part.name, used, limit, types, default, taken)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.name, dialog.volume_number, dialog.volume_type, dialog.load_number

    def _ask_format(self, disk: Disk) -> tuple[formats.DiskFormat, int] | None:
        dialog = edit_dialogs.DiskFormatDialog(self, disk)
        return (dialog.format, dialog.partition_mb) if dialog.exec() == dialog.DialogCode.Accepted else None

    def _format_disk(self, disk: Disk | None = None) -> None:
        disk = disk or self._selected_disk()
        if self._engine is None or disk is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_format_disk(disk))

    def _do_format_disk(self, disk: Disk) -> None:
        engine = self._engine
        answer = self._ask_format(disk)
        if engine is None or answer is None:
            return
        fmt, partition_mb = answer
        usage = self._usage_cache.get(disk.index)
        held = ""
        if usage is not None:
            files = sum(v.files for v in usage.volumes)
            held = f"It holds {files:,} file{'s' if files != 1 else ''} in {len(usage.volumes)} volume{'s' if len(usage.volumes) != 1 else ''}, and they all go. "
        name = Path(self._images[disk.index]).name if disk.index < len(self._images) else f"disk{disk.index}"
        if not edit_dialogs.confirm(
            self,
            "Format disk?",
            f"Erase everything on “{name}” and format it as {fmt.label}?",
            held + "This can't be undone.",
            action="Erase and Format",
        ):
            return
        self._run_edit(
            lambda: engine.format_disk(disk, fmt, partition_mb),
            lambda _none: self._edited(disk.index, select=disk, announce=True, message=f"Formatted disk{disk.index} as {fmt.label}."),
            lambda message: self._edit_failed(disk.index, message),
        )

    def _ask_new_image(self) -> tuple[formats.DiskFormat, int, int] | None:
        dialog = edit_dialogs.DiskFormatDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.format, dialog.size_mb, dialog.partition_mb

    def _ask_save_path(self, suggestion: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, "Create disk image", suggestion, IMAGE_FILTER)
        return path or None

    def _new_image(self) -> None:
        """File > New Disk Image: make an empty formatted image file, and open it (with whatever is open already)."""
        binary = find_akaiutil(settings.get_akaiutil_path())
        if binary is None:
            self._set_up_akaiutil(then=self._new_image)
            return
        answer = self._ask_new_image()
        if answer is None:
            return
        fmt, size_mb, partition_mb = answer
        start = settings.get_last_dir("open") or str(Path.home())
        path = self._ask_save_path(str(Path(start) / f"{fmt.label}.img".replace("(", "").replace(")", "")))
        if path is None:
            return
        if not Path(path).suffix:
            path += ".img"
        if any(Path(path).resolve() == Path(p).resolve() for p in self._images):
            QMessageBox.warning(self, "That image is open", f"{Path(path).name} is open in the window. Close it first if you want to replace it.")
            return
        settings.set_last_dir("open", str(Path(path).parent))
        engine = AkaiEngine(binary, on_traffic=self._traffic.feed)
        self.statusBar().showMessage(f"Creating {Path(path).name}…")
        self._run(
            lambda: engine.create_image(path, fmt, size_mb, partition_mb),
            lambda made: self.add_images([str(made)]),
            lambda message: QMessageBox.critical(self, "Couldn't create the disk image", message),
        )

    def _edited(self, disk_index: int, select=None, announce: bool | None = None, message: str = "") -> None:
        """A disk has just been changed: forget what we knew about it and show it as it is now.

        The tree reloads the disk and puts the selection back (`select` says what to select, if not what was);
        with a search showing, nothing is re-selected (that would end the search): the results are redone
        from the fresh index instead."""
        if announce is None:
            announce = not self._in_results
        self._preview_token += 1  # a sample being converted for preview may be of a file that no longer exists
        self.preview.stop()
        self._preview_cache.clear()
        self._usage_epoch[disk_index] = self._usage_epoch.get(disk_index, 0) + 1
        self._usage_cache.pop(disk_index, None)
        callbacks = self._usage_waiting.pop(disk_index, [])
        self._rebuild_catalog()  # the old entries for this disk are gone; searching waits for the new ones
        self.tree.refresh_disk(disk_index, select, announce=announce)
        disk = self.tree.disk(disk_index)
        if disk is not None:
            self._ensure_usage(disk)
            for callback in callbacks:
                self._ensure_usage(disk, callback)
        if not announce:
            current = self.tree.current_object()
            if current is not None:
                self._update_usage(current)
            volume, engine = self._volume, self._engine
            if engine is not None and volume is not None and volume.disk == disk_index:
                token = self._files_token  # keep the volume's list current for when the search ends
                self._run(lambda: engine.list_files(volume), lambda files: self._files_loaded(token, files))
        if message:
            self._status_hold = time.monotonic() + 8
            self.statusBar().showMessage(message, 8000)

    def _edit_failed(self, disk_index: int, message: str) -> None:
        self._warn("Couldn't change the disk image", message.splitlines()[0] if message else "akaiutil refused.", message.splitlines()[1:])
        engine = self._engine
        if engine is None:
            return
        if not engine.is_open:
            self.close_images()
            return
        self._edited(disk_index)  # whatever it managed to do, show the disk as it is now

    def _warn(self, title: str, text: str, details: list[str] | None = None) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        if details:
            box.setDetailedText("\n".join(details))
        box.exec()

    def _tree_menu(self, obj) -> QMenu | None:
        menu = QMenu(self)
        if isinstance(obj, Volume):
            menu.addAction("Rename…", lambda: self._rename_volume(obj))
            menu.addAction("Delete…", lambda: self._delete_volume(obj))
        elif isinstance(obj, Partition) and self._can_hold_volumes(obj):
            menu.addAction("New Volume…", lambda: self._new_volume(obj))
        elif isinstance(obj, Disk):
            if self.tree.disk_path(obj.index):
                menu.addAction(reveal.label(), lambda: self._reveal_disk(obj))
            menu.addAction("Close Disk Image", lambda: self.close_disk(obj))
            menu.addSeparator()
            menu.addAction("Format Disk…", lambda: self._format_disk(obj))
        return None if menu.isEmpty() else menu

    def _on_tree_context(self, obj, pos) -> None:
        menu = self._tree_menu(obj) if self._engine is not None else None
        if menu is not None:
            menu.exec(pos)

    def _file_menu(self, rows: list[tuple[Volume, FileEntry]]) -> QMenu:
        menu = QMenu(self)
        menu.addAction(self.act_export)
        menu.addAction(self.act_export_wav)
        menu.addSeparator()
        rename = menu.addAction("Rename…", lambda: self._rename_file(*rows[0]))
        rename.setEnabled(len(rows) == 1)
        menu.addAction("Delete…" if len(rows) == 1 else f"Delete {len(rows)} Files…", lambda: self._delete_files(rows))
        return menu

    def _on_table_context(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row >= 0 and not self.table.item(row, 0).isSelected():
            self.table.selectRow(row)
        rows = self._selected_rows()
        if self._engine is not None and rows:
            self._file_menu(rows).exec(self.table.viewport().mapToGlobal(pos))

    # --------------------------------------------------------------- misc
    def _run(self, fn, on_done=None, on_error=None) -> None:
        """run_async with a busy indicator and default error reporting."""
        self._pending += 1
        self._sync_busy()

        def ok(result):
            self._pending -= 1
            self._sync_busy()
            if on_done is not None:
                on_done(result)

        def bad(message):
            self._pending -= 1
            self._sync_busy()
            (on_error or self._report_error)(message)

        run_async(fn, ok, bad)

    def _sync_busy(self) -> None:
        self.busy_bar.setVisible(self._pending > 0)
        self._update_actions()

    def _report_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Error: {message}".splitlines()[0], 10000)
        self._append_console(f"\n[error] {message}\n")

    @Slot(str)
    def _append_console(self, text: str) -> None:
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)
        self.console.insertPlainText(text)
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)

    def _set_theme(self, value: str) -> None:
        settings.set_theme(value)
        theme.set_theme_preference(value)
        action = self._theme_actions.get(value)
        if action is not None:
            action.setChecked(True)  # keeps View > Theme in step when the Settings dialog changed it

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self, update_runner=self._update_runner, locate=self.locate_binary)
        dialog.themeChosen.connect(self._set_theme)
        dialog.exec()

    def schedule_update_check(self) -> None:
        """The automatic check: at most once a day, a couple of seconds after startup, and only if enabled."""
        due = time.time() - settings.get_last_update_check() > CHECK_INTERVAL_SECONDS
        if settings.get_check_updates_on_startup() and due:
            QTimer.singleShot(2000, lambda: self._update_runner.start(manual=False))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About akaiutil-gui",
            f"<b>akaiutil-gui {__version__}</b><br>"
            "A cross-platform Qt front end for akaiutil. Proof of concept.<br><br>"
            "Does its work by running <b>akaiutil</b> by Klaus Michael Indlekofer, "
            "which is licensed under the GNU GPL v2 or later.<br><br>"
            "akaiutil-gui is licensed under the GNU GPL v2 or later (see Help ▸ Third-party notices).",
        )

    def _restore_geometry(self) -> None:
        geometry = QSettings().value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # ------------------------------------------------------------- events
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if Path(p).is_file()]
        if paths:
            self.add_images(paths)  # opens them if nothing is open yet

    def closeEvent(self, event) -> None:
        QSettings().setValue("window/geometry", self.saveGeometry())
        self._update_runner.wait()
        self.preview.stop()
        if self._preview_dir is not None:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
        if self._engine is not None:
            self._engine.close()
        QThreadPool.globalInstance().waitForDone(3000)
        super().closeEvent(event)
