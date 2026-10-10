"""The main window."""

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
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
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

from akaiutil_gui import __version__, describe, search, settings
from akaiutil_gui.audio.sample import LoadedSample, load_wav
from akaiutil_gui.formatting import human_size
from akaiutil_gui.engine import fetch
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.locate import candidates, find_akaiutil
from akaiutil_gui.engine.models import CatalogEntry, Disk, DiskUsage, ExportResult, FileEntry, Partition, Volume

from . import theme
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
        self._usage_cache: dict[int, DiskUsage] = {}  # images are read-only, so this never goes stale
        self._usage_waiting: dict[int, list] = {}  # disk -> callbacks for a load that's under way

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
        self.mode_label.setToolTip("Disk images are opened read-only; this proof of concept can't modify them.")
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
        self.act_close = self._action("&Close Images", self.close_images, QKeySequence("Ctrl+W"))
        self.act_export = self._action("&Export…", lambda: self._export(False), QKeySequence("Ctrl+E"))
        self.act_export_wav = self._action("Export as &WAV…", lambda: self._export(True), QKeySequence("Ctrl+Shift+E"))
        act_quit = self._action("&Quit", self.close, QKeySequence.StandardKey.Quit)
        act_quit.setMenuRole(QAction.MenuRole.QuitRole)
        file_menu.addActions([self.act_open, self.act_close])
        file_menu.addSeparator()
        file_menu.addActions([self.act_export, self.act_export_wav])
        file_menu.addSeparator()
        settings_action = self._action("&Settings…", self._open_settings, QKeySequence.StandardKey.Preferences)
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)  # macOS moves it to the app menu
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

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
        self.search.setEnabled(has_images)
        self.search_kind.setEnabled(has_images)
        for w in (self.act_export, self.export_btn):
            w.setEnabled(can_export)
        for w in (self.act_export_wav, self.export_wav_btn):
            w.setEnabled(can_wav)

    # ------------------------------------------------------------- opening
    def open_dialog(self) -> None:
        start = settings.get_last_dir("open") or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Open disk image(s)", start, IMAGE_FILTER)
        if paths:
            settings.set_last_dir("open", str(Path(paths[0]).parent))
            self.open_images(paths)

    def open_images(self, paths: list[str]) -> None:
        binary = find_akaiutil(settings.get_akaiutil_path())
        if binary is None:
            # first run: offer to get akaiutil, then carry on with what the user asked for
            self._set_up_akaiutil(then=lambda: self.open_images(paths))
            return
        if self._engine is not None:
            self._engine.close()
        self._engine = AkaiEngine(binary, on_traffic=self._traffic.feed)
        self._engine_binary = binary
        self._reset_views()
        self._images = [str(p) for p in paths]
        engine = self._engine
        self.statusBar().showMessage("Opening…")
        self._run(
            lambda: engine.open(self._images),
            lambda banner: self._opened(engine, banner),
            self._open_failed,
        )

    def _opened(self, engine: AkaiEngine, banner: str) -> None:
        if engine is not self._engine:
            return  # superseded by a newer open
        m = re.search(r"Rev\.\s*([\w.]+)", banner)
        names = ", ".join(Path(p).name for p in self._images)
        self.setWindowTitle(f"{names} — akaiutil-gui")
        message = f"Opened {names}  ·  akaiutil {m.group(1) if m else '?'}  ·  {self._engine_binary}"
        # akaiutil reports unreadable/unformatted disks only in its start-up chatter
        problems = [ln.strip() for ln in banner.splitlines() if re.search(r"invalid format|cannot|error", ln, re.I)]
        if problems:
            message = f"⚠ {problems[0]}  ·  {message}"
        self.statusBar().showMessage(message)
        self.stack.setCurrentIndex(1)
        self.tree.load(engine)
        self._update_actions()

    def _open_failed(self, message: str) -> None:
        self.close_images()
        QMessageBox.critical(self, "Couldn't open disk image", message)

    def close_images(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            run_async(engine.close)  # can take a moment; keep the GUI responsive
        self._images = []
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

        def done(usage: DiskUsage) -> None:
            if engine is not self._engine:
                return  # the image was closed or replaced meanwhile
            self._usage_cache[disk.index] = usage
            self._rebuild_catalog()
            self.tree.set_volume_order(disk.index, usage.volume_order())
            for callback in self._usage_waiting.pop(disk.index, []):
                callback(usage)

        def failed(message: str) -> None:
            if engine is self._engine:
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
            self.open_images(paths)

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
