"""The main window."""

import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QFontDatabase, QFontMetrics, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSettings, QThreadPool

from akaiutil_gui import (
    akai_names,
    describe,
    floppy_map,
    hfe,
    hfe_work,
    importing,
    os_floppy,
    search,
    settings,
    sorting,
    transfer,
)
from akaiutil_gui.audio.sample import LoadedSample, load_wav
from akaiutil_gui.formatting import human_size
from akaiutil_gui.engine import fetch, formats
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.locate import binary_version, candidates, find_akaiutil
from akaiutil_gui import __version__, backup, references
from akaiutil_gui.engine.models import (
    CatalogEntry,
    Disk,
    DiskUsage,
    ExportResult,
    FileEntry,
    Partition,
    Volume,
)

from . import edit_dialogs, file_drag, reveal, theme
from .ascii_logo import LOGO
from .details_view import DetailsView, monospace_family
from .disk_tree import DiskTree
from .about_dialog import AboutDialog
from .notices_dialog import NoticesDialog
from .manual_dialog import show_manual_dialog
from .preview_panel import PreviewPanel
from .settings_dialog import SettingsDialog
from .splitter import Splitter
from .file_table import FileTable
from .tasks import run_async
from .update_helper import CHECK_INTERVAL_SECONDS, UpdateCheckRunner
from .usage_bar import UsageSummary

log = logging.getLogger(__name__)

RECENT_WIDTH = 520  # px: the recent list on the start screen
STATUS_BAR_MARGIN = 10  # px: the status bar's text keeps this far from the window's sides (and so its rounded corners)
RECENT_SHOWN = 6  # how many of the remembered images the start screen lists

IMAGE_FILTER = "Disk images (*.img *.iso *.bin *.dsk *.hfe *.akai *.fdi *.raw);;All files (*)"


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
        self.setMinimumSize(880, 520)  # below this the disk list, file table and details pane are too cramped to use
        self.resize(1180, 720)

        self._engine: AkaiEngine | None = None
        self._engine_binary: Path | None = None
        self._images: list[str] = []
        self._disk_files: dict[
            int, str
        ] = {}  # disk number -> the image file akaiutil says it came from
        self._open_select: tuple[int, str | None, int | None] | None = (
            None  # where to land once a reopen has loaded
        )
        self._volume: Volume | None = None
        self._volume_details: describe.Details | None = None
        self._pending = 0
        self._setup_running = False  # akaiutil is being downloaded right now
        self._update_runner = UpdateCheckRunner(self)
        self._catalog: list[
            CatalogEntry
        ] = []  # every file on every open disk, for searching
        self._in_results = (
            False  # the file table is showing search results, not a volume's files
        )
        self._volume_files: list[
            FileEntry
        ] = []  # the selected volume's files, to put back when a search ends
        self._files_title_text = "Files"
        self._pending_reveal: tuple[Volume, int] | None = (
            None  # select this file once its volume has loaded
        )
        self._preview_token = (
            0  # numbers each preview load; only the newest may show its result
        )
        self._preview_cache: OrderedDict[tuple, LoadedSample] = (
            OrderedDict()
        )  # decoded samples, so replaying is instant
        self._preview_dir: str | None = (
            None  # scratch folder the WAVs are converted into
        )
        self._theme_actions: dict[str, QAction] = {}
        # Details requests run on worker threads and can finish in any order, so each gets a number
        # and only _info_current may show its result (0: nothing in flight is wanted any more).
        self._info_seq = 0
        self._info_current = 0
        self._volume_token = 0  # the request for the selected volume's own details
        self._files_token = 0
        self._usage_token = 0
        self._usage_cache: dict[
            int, DiskUsage
        ] = {}  # dropped for a disk whenever it is edited
        self._usage_waiting: dict[
            int, list
        ] = {}  # disk -> callbacks for a load that's under way
        self._usage_epoch: dict[
            int, int
        ] = {}  # bumped when a disk is changed: a load that started earlier is out of date
        self._writable = False  # akaiutil is running read/write (Edit > Allow Editing)
        self._hfe = hfe_work.Workspace()  # flat working copies of the open HFE images: akaiutil only reads flat ones
        self._hfe_warned: set[str] = set()  # damaged HFE files the user has been told about this session
        self._floppy_files: dict[str, list[floppy_map.DiskFile]] = {}  # damaged HFE file -> the files akaiutil lists on it
        self._hfe_failures: list[tuple[str, str]] = []  # write-backs that failed during an edit, to tell the user once it is done
        self._backup_handled: set[str] = set()  # images (resolved paths) already backed up, or made new, or declined, this session
        self._clip: tuple[str, list[tuple[Volume, FileEntry]]] | None = (
            None  # Copy / Cut: ("copy" | "cut", files)
        )
        self._drag_dir: str | None = (
            None  # where files dragged out of the window are written
        )
        self._refs_cache: dict[tuple, references.VolumeReferences] = {}  # (disk, partition, volume, disk's epoch) -> which programs use which samples
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
        self.tree.ejectRequested.connect(self.close_disk)
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

        # right top: the file list (exporting is in the File menu and the right-click menu)
        self.files_title = self._title("Files")
        self.table = FileTable()
        self.table.selectionFilesChanged.connect(self._on_files_selected)
        self.table.mime_provider = self._file_drag_mime
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context)
        self.tree.contextRequested.connect(self._on_tree_context)
        self.tree.filesDropped.connect(self._on_tree_drop)
        self.tree.transferDropped.connect(self._on_transfer_drop)
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
        self._search_timer.setInterval(
            150
        )  # so typing doesn't refilter the table on every keystroke
        self._search_timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.search_kind.currentIndexChanged.connect(
            lambda _i: self._search_timer.start()
        )
        header.addWidget(self.files_title, 1)
        header.addWidget(self.search)
        header.addWidget(self.search_kind)
        # a line edit is shorter than the combo box beside it in some themes: give it the combo's height, again whenever the theme changes
        theme.notifier.changed.connect(lambda: QTimer.singleShot(0, self._match_search_height))
        QTimer.singleShot(0, self._match_search_height)
        tv.addLayout(header)
        # The right-hand header row is as tall as its tallest widget (the search box and the filter); give the
        # left title the same height (the text stays vertically centred) so the tree and the file table start at
        # the same y.
        self.disks_title.setMinimumHeight(
            max(w.sizeHint().height() for w in (self.search, self.search_kind))
        )
        tv.addWidget(self.table)
        self.preview = PreviewPanel()
        self.preview.hide()  # appears when a single sample is selected (below the details, which shrink to make room)
        QGuiApplication.instance().focusWindowChanged.connect(self._on_focus_window)
        # Space plays/stops, like in a DAW or Finder's Quick Look
        space = QShortcut(QKeySequence(Qt.Key.Key_Space), self.table)
        space.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        space.activated.connect(self.preview.toggle)
        self.table.itemActivated.connect(
            lambda _item: self._activate_result()
        )  # double-click or Enter
        for widget in (
            self.table,
            self.tree,
        ):  # F2 and Delete work where the thing to rename or delete is
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
        bv.addWidget(self.info, 1)
        bv.addWidget(self.preview)

        right = self.right_splitter = Splitter(Qt.Orientation.Vertical)
        right.addWidget(top)
        right.addWidget(bottom)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 1)  # the readable details run long, give them room
        right.setChildrenCollapsible(False)

        browser = self.browser_splitter = Splitter(Qt.Orientation.Horizontal)
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
        self.empty_logo = QLabel(LOGO.strip("\n"))
        self.empty_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # (a stylesheet beats setFont, and the app's gives every label a pixel size and no fixed-width family)
        self.empty_logo.setStyleSheet(
            f"font-family: '{monospace_family()}'; font-size: 13pt;"
        )
        label = QLabel("Open or create a new Akai disk image to get started")
        label.setObjectName("emptyState")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("or drop image files onto this window")
        hint.setObjectName("mutedLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.open_btn = QPushButton("Open Disk Image…")
        self.open_btn.clicked.connect(self.open_dialog)
        self.new_btn = QPushButton("New Disk Image…")
        self.new_btn.setToolTip("Make a new, empty disk image: a floppy or a hard disk")
        self.new_btn.clicked.connect(self._new_image)
        buttons = QHBoxLayout()
        buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.new_btn)
        # quiet links along the bottom edge, so they don't compete with the two buttons that matter
        self.help_btn = QPushButton("User Manual")
        self.help_btn.setToolTip("Open the user manual")
        self.help_btn.clicked.connect(lambda: show_manual_dialog(self))
        self.about_btn = QPushButton("About")
        self.about_btn.setToolTip("About akaiutil-GUI")
        self.about_btn.clicked.connect(self._about)
        footer = QHBoxLayout()
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for button in (self.help_btn, self.about_btn):
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setObjectName("linkButton")
            footer.addWidget(button)
        # recently opened images, under the buttons (only when there are some)
        self.recent_box = QWidget()
        self.recent_box.setObjectName("recentCard")
        self.recent_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # (a plain QWidget only paints its QSS background with this)
        self.recent_box.setFixedWidth(RECENT_WIDTH)
        recent_layout = QVBoxLayout(self.recent_box)
        recent_layout.setContentsMargins(8, 8, 8, 8)
        recent_layout.setSpacing(2)
        recent_head = QHBoxLayout()
        recent_head.setContentsMargins(10, 0, 0, 0)  # (in line with the names below)
        recent_title = QLabel("Recent")
        recent_title.setObjectName("mutedLabel")
        self.clear_recent_btn = QPushButton("Clear List")
        self.clear_recent_btn.setFlat(True)
        self.clear_recent_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_recent_btn.setObjectName("linkButton")
        self.clear_recent_btn.clicked.connect(self._clear_recent)
        recent_head.addWidget(recent_title, 1)
        recent_head.addWidget(self.clear_recent_btn)
        recent_layout.addLayout(recent_head)
        self.recent_rows = QVBoxLayout()
        self.recent_rows.setSpacing(0)
        recent_layout.addLayout(self.recent_rows)
        ev.addStretch(2)
        ev.addWidget(self.empty_logo)
        ev.addSpacing(28)
        ev.addWidget(label)
        ev.addSpacing(8)
        ev.addLayout(buttons)
        ev.addSpacing(8)
        ev.addWidget(hint)
        ev.addSpacing(12)  # (never closer than this to the hint above or the links below)
        ev.addStretch(1)  # the same stretch above and below: the list sits halfway between the hint and the links
        ev.addWidget(self.recent_box, 0, Qt.AlignmentFlag.AlignHCenter)
        ev.addStretch(1)
        ev.addSpacing(12)
        ev.addLayout(footer)

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
        self.statusBar().setSizeGripEnabled(
            False
        )  # the corner square; macOS windows have no grip
        self.statusBar().setContentsMargins(STATUS_BAR_MARGIN, 0, STATUS_BAR_MARGIN, 0)  # (the window's rounded corners clip text set against the edge)
        self.statusBar().addPermanentWidget(self.busy_bar)
        self.statusBar().addPermanentWidget(self.mode_label)
        self.version_label = QLabel(f"Version {__version__}")  # (bottom left, on the start screen only)
        self.version_label.setObjectName("mutedLabel")
        self.statusBar().addWidget(self.version_label)
        self._refresh_akaiutil_version()
        self._sync_status_labels()
        self._refresh_recent()

    def _refresh_akaiutil_version(self) -> None:
        """Add the version of the akaiutil in use to the start screen's version label (once it has been asked)."""
        binary = find_akaiutil(settings.get_akaiutil_path())
        if binary is None:
            return

        def done(version) -> None:
            if version and find_akaiutil(settings.get_akaiutil_path()) == binary:  # (not changed since)
                self.version_label.setText(f"Version {__version__}  ·  akaiutil {version}")

        run_async(lambda: binary_version(binary), done)

    def _title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("panelTitle")
        return label

    def _build_menus(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        self.act_open = self._action(
            "&Open Disk Image…", self.open_dialog, QKeySequence.StandardKey.Open
        )
        self.act_add = self._action(
            "&Add Disk Image…", self.add_dialog, QKeySequence("Ctrl+Shift+O")
        )
        self.act_close_disk = self._action(
            "Close &Disk Image", self._close_selected_disk, QKeySequence("Ctrl+Shift+W")
        )
        self.act_close = self._action(
            "Close All &Images", self._close_asked, QKeySequence("Ctrl+W")
        )
        self.act_revert = self._action("Revert to &Backup…", self._revert)
        self.act_save_hfe = self._action("Save Disk as &HFE…", self._save_as_hfe)
        self.act_repair = self._action("Save Repaired &Copy…", self._save_repaired)
        self.act_os_floppy = self._action("Create &OS Boot Floppy…", self._create_os_floppy)
        self.act_export = self._action(
            "&Export…", lambda: self._export(False), QKeySequence("Ctrl+E")
        )
        self.act_export_wav = self._action(
            "Export as &WAV…", lambda: self._export(True), QKeySequence("Ctrl+Shift+E")
        )
        self.act_new_image = self._action(
            "&New Disk Image…", self._new_image, QKeySequence.StandardKey.New
        )
        act_quit = self._action("&Quit", self.close, QKeySequence.StandardKey.Quit)
        act_quit.setMenuRole(QAction.MenuRole.QuitRole)
        self.recent_menu = QMenu("Open &Recent", self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addActions([self.act_new_image, self.act_open])
        file_menu.addMenu(self.recent_menu)
        file_menu.addAction(self.act_add)
        file_menu.addSeparator()
        file_menu.addActions([self.act_close_disk, self.act_close])
        file_menu.addSeparator()
        file_menu.addActions([self.act_export, self.act_export_wav])
        file_menu.addSeparator()
        file_menu.addAction(self.act_revert)
        file_menu.addAction(self.act_save_hfe)
        file_menu.addAction(self.act_repair)
        file_menu.addSeparator()
        file_menu.addAction(self.act_os_floppy)
        file_menu.addSeparator()
        # Ctrl+, everywhere (Qt maps Ctrl to Cmd on a Mac): the standard Preferences key is only defined on macOS and some Linux desktops
        settings_action = self._action("&Settings…", self._open_settings, QKeySequence("Ctrl+,"))
        settings_action.setMenuRole(
            QAction.MenuRole.PreferencesRole
        )  # macOS moves it to the app menu
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        self.edit_menu = mb.addMenu("&Edit")
        self.act_allow_edit = QAction("Allow &Editing", self, checkable=True)
        self.act_allow_edit.setToolTip(
            "Let the app change the disk images (rename, delete, create). Off: they are only read."
        )
        self.act_allow_edit.triggered.connect(self._toggle_editing)
        self.act_new_volume = self._action("New &Volume…", self._new_volume)
        self.act_rename = self._action("&Rename…", self._rename_focused)
        self.act_sample_settings = self._action("Sample Se&ttings…", self._sample_settings_selected)
        self.act_delete = self._action("&Delete…", self._delete_focused)
        self.act_copy = self._action(
            "&Copy Files",
            lambda: self._copy_selection("copy"),
            QKeySequence.StandardKey.Copy,
        )
        self.act_cut = self._action(
            "Cu&t Files",
            lambda: self._copy_selection("cut"),
            QKeySequence.StandardKey.Cut,
        )
        self.act_paste = self._action(
            "&Paste Files", self._paste, QKeySequence.StandardKey.Paste
        )
        self.act_import = self._action(
            "&Import Files…", self._import_dialog, QKeySequence("Ctrl+I")
        )
        self.act_sort = self._action("&Sort Files by Name…", self._sort_volume)
        self.act_select_unused = self._action("Select &Unused Samples", self._select_unused_samples)
        self.act_format = self._action("&Format Disk…", self._format_disk)
        self.edit_menu.addAction(self.act_allow_edit)
        self.edit_menu.addSeparator()
        self.edit_menu.addActions([self.act_copy, self.act_cut, self.act_paste])
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.act_select_unused)
        self.edit_menu.addSeparator()
        self.edit_menu.addActions(
            [
                self.act_new_volume,
                self.act_import,
                self.act_sort,
                self.act_sample_settings,
                self.act_rename,
                self.act_delete,
            ]
        )
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
        self.act_manual = self._action(
            "&User Manual…",
            lambda: show_manual_dialog(self),
            QKeySequence.StandardKey.HelpContents,
        )
        help_menu.addAction(self.act_manual)
        help_menu.addSeparator()
        about = self._action("&About akaiutil-GUI…", self._about)
        about.setMenuRole(QAction.MenuRole.AboutRole)  # macOS moves it to the app menu
        help_menu.addAction(about)
        self.act_notices = self._action("Third-party &Notices…", lambda: NoticesDialog(self).exec())  # (also behind the About window's Licences button)
        help_menu.addAction(self.act_notices)
        help_menu.addSeparator()
        self.act_show_log = self._action("Show &Log File", lambda: reveal.show_log_file())
        help_menu.addAction(self.act_show_log)
        help_menu.addAction(
            self._action(
                "Check for &Updates…", lambda: self._update_runner.start(manual=True)
            )
        )

    def _action(self, text, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        act.triggered.connect(lambda _checked=False: slot())
        if shortcut is not None:
            act.setShortcut(shortcut)
        return act

    def _update_actions(self) -> None:
        has_images = bool(self._images)
        rows = self.table.selected_rows()
        can_export = (
            bool(rows) and all(v is not None for v, _ in rows) and self._pending == 0
        )
        can_wav = can_export and any(f.is_sample for _, f in rows)
        self.act_close.setEnabled(has_images)
        self.act_add.setEnabled(True)
        self.act_revert.setEnabled(has_images and self._writing == 0)
        disk = self._selected_disk() if has_images else None
        self.act_save_hfe.setEnabled(disk is not None and disk.type.startswith("FL") and self._writing == 0 and self._pending == 0)
        source = self._disk_files.get(disk.index) if disk is not None else None
        self.act_repair.setEnabled(source is not None and str(Path(source).resolve()) in self._hfe.damaged() and self._writing == 0 and self._pending == 0)
        self.act_close_disk.setEnabled(
            has_images and self._selected_disk() is not None and self._writing == 0
        )
        self.search.setEnabled(has_images)
        self.search_kind.setEnabled(has_images)
        self.act_export.setEnabled(can_export)
        self.act_export_wav.setEnabled(can_wav)
        self._update_edit_actions()
        self._sync_mode()

    # ------------------------------------------------------------- opening
    def open_dialog(self) -> None:
        start = settings.get_last_dir("open") or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open disk image(s)", start, IMAGE_FILTER
        )
        if paths:
            settings.set_last_dir("open", str(Path(paths[0]).parent))
            paths = self._only_disk_images(paths)
            if paths:
                self.open_images(paths)

    def open_images(
        self,
        paths: list[str],
        writable: bool = False,
        select: tuple[int, str | None, int | None] | None = None,
    ) -> None:
        """Open these disk images (replacing whatever is open). Read-only unless `writable` and every file allows it.
        `select` is a (disk, partition, volume number) to land on instead of the first disk."""
        binary = find_akaiutil(settings.get_akaiutil_path())
        if binary is None:
            # first run: offer to get akaiutil, then carry on with what the user asked for
            self._set_up_akaiutil(
                then=lambda: self.open_images(paths, writable, select)
            )
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
                writable, note = (
                    False,
                    f"Editing is off: {', '.join(locked)} can't be written to.",
                )
        if writable:
            unbacked = [Path(p).name for p in self._images if str(Path(p).resolve()) not in self._backup_handled]
            if unbacked:  # (a new image joins while editing is on: it gets its backup question when editing is switched on again)
                writable, note = False, f"Editing is off: switch it on to be asked about a backup of {', '.join(unbacked)}."
        engine, images = self._engine, list(self._images)
        self.statusBar().showMessage("Opening…")
        def start() -> str:
            paths = self._engine_paths(images)  # (decoding an HFE can find it damaged: such a disk is only ever opened read-only)
            return engine.open(paths, writable=writable and not self._hfe.damaged())

        self._run(
            start,
            lambda banner: self._opened(engine, banner, note),
            self._open_failed,
        )

    def _engine_paths(self, images: list[str]) -> list[str]:
        """What akaiutil is given for these images (worker thread): the files themselves, but a decoded working copy for an HFE."""
        paths = [self._hfe.engine_path(p) for p in images]
        self._hfe.release(keep=images)
        return paths

    def _opened(self, engine: AkaiEngine, banner: str, note: str = "") -> None:
        if engine is not self._engine:
            return  # superseded by a newer open
        self._writable = engine.is_writable
        for path in reversed(self._images):  # (so the first image of a set ends up on top)
            settings.add_recent_image(path)
        self._refresh_recent()
        log.info("opened %s (%s)", ", ".join(self._images), "editing" if self._writable else "read-only")
        m = re.search(r"Rev\.\s*([\w.]+)", banner)
        names = ", ".join(Path(p).name for p in self._images)
        # (one image is named; with several the disk list already names each, and a title of them all would run off the bar)
        self.setWindowTitle(f"{names if len(self._images) == 1 else f'{len(self._images)} disk images'} — akaiutil-gui")
        message = f"Opened {names}  ·  akaiutil {m.group(1) if m else '?'}  ·  {self._engine_binary}"
        # akaiutil reports unreadable/unformatted disks only in its start-up chatter
        problems = [
            ln.strip()
            for ln in banner.splitlines()
            if re.search(r"invalid format|cannot|error", ln, re.I)
        ]
        if problems:
            message = f"⚠ {problems[0]}  ·  {message}"
        if note:
            message = f"{note}  ·  {message}"
        self.statusBar().showMessage(message)
        self._disk_files = {  # (an HFE's working copy is shown as the HFE file)
            int(n): self._hfe.source_for(path) for n, path in re.findall(r'^disk(\d+): "(.*)"$', banner, re.M)
        }
        if not self._disk_files:  # (akaiutil numbers them in the order they were given)
            self._disk_files = {
                i: str(Path(p).resolve()) for i, p in enumerate(self._images)
            }
        self.stack.setCurrentIndex(1)
        self._sync_status_labels()
        select, self._open_select = self._open_select, None
        self._run(
            engine.list_disks,
            lambda disks: self._scanned(engine, disks, select),
            self._open_failed,
        )

    def _scanned(self, engine: AkaiEngine, disks: list[Disk], select) -> None:
        """akaiutil has opened the images. Whatever it found no Akai disk in (no partitions, and not an empty image waiting to
        be formatted) is closed again with a message, since it is something else, and showing it would look like a disk."""
        if engine is not self._engine:
            return
        not_akai = {
            Path(self._disk_files[d.index]).resolve(): Path(self._disk_files[d.index]).name
            for d in disks
            if d.partitions == 0
            and d.index in self._disk_files
            and not importing.is_blank_image(self._hfe.working_path(self._disk_files[d.index]))
        }
        if not_akai:
            n = len(not_akai)
            log.info("not Akai disks: %s", ", ".join(not_akai.values()))
            remaining = [p for p in self._images if Path(p).resolve() not in not_akai]
            if remaining:
                self.open_images(remaining, writable=self._writable)
            else:
                self.close_images()
            self._warn(
                "Not an Akai disk image" if n == 1 else "Not Akai disk images",
                f"akaiutil found no Akai disk in {'this file' if n == 1 else f'these {n} files'}, so "
                f"{'it was' if n == 1 else 'they were'} not opened.",
                [f"{name}: not an Akai disk image" for name in not_akai.values()],
            )
            return
        self.tree.load(engine, select=select, files=self._disk_files, disks=disks)
        self._update_actions()
        self._warn_damaged_hfe(engine, disks)

    def _warn_damaged_hfe(self, engine: AkaiEngine, disks: list[Disk]) -> None:
        """Tell the user, once per file, about an HFE image with sectors that couldn't be read (it is open read-only), and which
        of its files they belong to."""
        new = {path: sectors for path, sectors in self._hfe.damaged().items() if path not in self._hfe_warned}
        if not new:
            return
        self._hfe_warned.update(new)
        disk_files = dict(self._disk_files)

        def look() -> dict[str, tuple[list[floppy_map.DiskFile], floppy_map.Damage]]:  # (worker thread)
            found = {}
            for path, sectors in new.items():
                try:
                    index = next(i for i, p in disk_files.items() if str(Path(p).resolve()) == path)
                    disk = next(d for d in disks if d.index == index)
                    files = [floppy_map.DiskFile(e.file.name, int(e.file.start_block, 16), e.file.size) for e in engine.disk_usage(disk).catalog]
                    image = Path(self._hfe.working_path(path)).read_bytes()
                    spt = len(image) // (160 * 1024)
                    blocks = {floppy_map.block_of(t, side, r, spt) for t, side, r in sectors}
                    found[path] = (files, floppy_map.damage(image, blocks, files))
                except Exception:  # (the names of the files are a bonus: the warning goes ahead without them)
                    log.warning("couldn't work out which files of %s are damaged", path, exc_info=True)
            return found

        self._run(look, lambda found: self._show_damage(new, found), lambda _message: self._show_damage(new, {}))

    def _show_damage(self, new: dict, found: dict) -> None:
        for path, (files, _) in found.items():
            self._floppy_files[path] = files
        names = ", ".join(Path(p).name for p in new)
        count = sum(len(sectors) for sectors in new.values())
        hit = [name for path in new if path in found for name in found[path][1].files]
        header = any(found[path][1].header for path in new if path in found)
        if header:
            affected = "The disk's directory is damaged, so any file on it could be affected."
        elif hit:
            affected = f"Affected: {', '.join(hit[:6])}{f' and {len(hit) - 6} more' if len(hit) > 6 else ''}."
        elif found:
            affected = "No files are affected."
        else:
            affected = ""
        self._warn(
            "Damaged HFE image",
            f"{names} {'has' if len(new) == 1 else 'have'} {count} damaged sector{'s' if count != 1 else ''}, which show as blank. "
            f"{affected} It's open for viewing only. File > Save Repaired Copy… can try to fix it.".replace("  ", " "),
            [f"{Path(p).name}: track {t} side {side}, sector {r}" for p, sectors in new.items() for t, side, r in sectors],
        )

    def _ask_repair(self, name: str) -> bool | None:
        """Ask how to repair; returns whether to also rewrite what can't be fixed, or None if cancelled."""
        dialog = edit_dialogs.RepairDialog(self, name)
        return dialog.rewrite if dialog.exec() == dialog.DialogCode.Accepted else None

    def _ask_repair_path(self, suggestion: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, "Save repaired copy", suggestion, "HFE images (*.hfe);;All files (*)")
        return path or None

    def _save_repaired(self) -> None:
        """File > Save Repaired Copy: fix the damaged sectors of the selected HFE disk in a new file."""
        disk = self._selected_disk()
        source = self._disk_files.get(disk.index) if disk is not None else None
        if source is None or str(Path(source).resolve()) not in self._hfe.damaged() or self._busy():
            return
        rewrite = self._ask_repair(Path(source).name)
        if rewrite is None:
            return
        start = settings.get_last_dir("save_hfe") or str(Path(source).parent)
        path = self._ask_repair_path(str(Path(start) / f"{Path(source).stem} (repaired).hfe"))
        if path is None:
            return
        if not Path(path).suffix:
            path += ".hfe"
        if any(Path(path).resolve() == Path(p).resolve() for p in self._images):
            QMessageBox.warning(self, "That image is open", f"{Path(path).name} is open in the window. Close it first if you want to replace it.")
            return
        settings.set_last_dir("save_hfe", str(Path(path).parent))
        self.statusBar().showMessage(f"Repairing {Path(source).name}…")
        self._run(
            lambda: self._hfe.repair_copy(source, path, rewrite),
            lambda repaired: self._repaired(source, path, repaired),
            lambda message: (self.statusBar().clearMessage(), QMessageBox.critical(self, "Couldn't save the repaired copy", message)),
        )

    def _repaired(self, source: str, path: str, repaired: hfe.Repair) -> None:
        self.statusBar().clearMessage()
        unfixed = [f for f in repaired.fixes if f.result in (hfe.LOST, hfe.REWRITTEN)]
        files: list[str] = []
        if unfixed and source in self._floppy_files:
            try:
                image = Path(self._hfe.working_path(source)).read_bytes()
                spt = len(image) // (160 * 1024)
                files = floppy_map.damage(image, {floppy_map.block_of(f.track, f.side, f.sector, spt) for f in unfixed}, self._floppy_files[source]).files
            except OSError:
                files = []
        if self._show_repair_result(path, repaired, files):
            self.add_images([path])

    def _show_repair_result(self, path: str, repaired: hfe.Repair, files: list[str]) -> bool:
        """Say what the repair did; returns whether to open the repaired copy."""
        count = lambda results: len(repaired.with_result(*results))  # noqa: E731
        total, probably, rewritten, lost = len(repaired.fixes), count([hfe.FIXED_PROBABLY]), count([hfe.REWRITTEN]), count([hfe.LOST])
        fixed = count([hfe.FIXED, hfe.FIXED_PROBABLY])
        plural = lambda n: f"{n} sector{'s' if n != 1 else ''}"  # noqa: E731
        lines = [f"Fixed {'all ' if total > 1 else ''}{plural(total)}." if fixed == total else f"Fixed {fixed} of {plural(total)}."]
        if probably:
            lines.append(f"{probably} of them {'is' if probably == 1 else 'are'} probably right, but couldn't be double-checked.")
        if rewritten:
            lines.append(f"{plural(rewritten)} couldn't be fixed and {'was' if rewritten == 1 else 'were'} rewritten. {'It' if rewritten == 1 else 'They'} may hold wrong data.")
        if lost:
            lines.append(f"{plural(lost)} couldn't be fixed and still won't read.")
        if files:
            lines.append(f"Still affected: {', '.join(files[:6])}{f' and {len(files) - 6} more' if len(files) > 6 else ''}.")
        words = {hfe.FIXED: "fixed", hfe.FIXED_PROBABLY: "fixed (probably right)", hfe.REWRITTEN: "rewritten, may be wrong", hfe.LOST: "couldn't be fixed"}
        box = QMessageBox(QMessageBox.Icon.Information, "Repaired copy saved", f"Saved {Path(path).name}.\n\n" + "\n".join(lines), parent=self)
        box.setDetailedText("\n".join(f"Track {f.track}, side {f.side}, sector {f.sector}: {words[f.result]}" for f in repaired.fixes))
        open_copy = box.addButton("Open Copy", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Done", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is open_copy

    # ----------------------------------------------- adding and closing single disks
    def add_dialog(self) -> None:
        start = settings.get_last_dir("open") or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add disk image(s)", start, IMAGE_FILTER
        )
        if paths:
            settings.set_last_dir("open", str(Path(paths[0]).parent))
            paths = self._only_disk_images(paths)
            if paths:
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
        everything = [*self._images, *new]
        need = [str(Path(p).resolve()) for p in new if str(Path(p).resolve()) not in self._backup_handled] if self._writable else []
        if not need:
            self._reopen_with(everything, new)
            return
        # editing is on, so the new image can be changed straight away: ask about its backup now, as when editing was switched on
        answer = edit_dialogs.confirm(
            self,
            "Back up the new image?",
            "Editing is on, so this image can be changed as soon as it is open.",
            "There is no undo. Back it up first? The backup is kept in the app's folder, and File > Revert to Backup… puts it back.",
            action="Back Up and Add",
            other_action="Add Without Backup",
            details_lines=[Path(p).name for p in need],
            default_is_action=True,
        )
        if not answer:
            return
        if answer is edit_dialogs.OTHER:
            self._backup_handled.update(need)
            self._reopen_with(everything, new)
        else:
            self._backup_images(need, lambda: self._reopen_with(everything, new))

    def _reopen_with(self, everything: list[str], new: list[str]) -> None:
        """_reopen, once any HFE among the `new` images has been decoded: a damaged one is turned away with the reason, and the
        images already open stay open."""
        if not any(hfe.is_hfe(p) for p in new):
            self._reopen(everything)
            return
        self.statusBar().showMessage("Reading the HFE image…")
        self._run(
            lambda: [self._hfe.engine_path(p) for p in new],
            lambda _paths: self._reopen(everything),
            lambda message: (
                self.statusBar().clearMessage(),
                self._warn("Couldn't open the HFE image", message),
            ),
        )

    def _reopen(self, paths: list[str], disk_map=None) -> None:
        """Restart on another set of images, keeping the editing mode and the selection (`disk_map` says what number the
        selected disk has now, or None if it is gone)."""
        obj = self.tree.current_object()
        select = None
        if obj is not None:
            old = obj.index if isinstance(obj, Disk) else obj.disk
            new = disk_map(old) if disk_map is not None else old
            if new is not None:
                partition = (
                    None
                    if isinstance(obj, Disk)
                    else obj.name
                    if isinstance(obj, Partition)
                    else obj.partition
                )
                select = (
                    new,
                    partition,
                    obj.index if isinstance(obj, Volume) else None,
                )
        self.open_images(paths, writable=self._writable, select=select)

    def _close_selected_disk(self) -> None:
        disk = self._selected_disk()
        if disk is not None:
            self.close_disk(disk)

    def close_disk(self, disk: Disk) -> None:
        """Close one image and keep the others open. The disks after it get new numbers, since akaiutil numbers them in order."""
        if self._engine is None or self._busy() or not self._hfe_written():
            return
        path = self._disk_files.get(disk.index)
        if path is not None:
            remaining = [
                p for p in self._images if Path(p).resolve() != Path(path).resolve()
            ]
        else:
            remaining = [p for i, p in enumerate(self._images) if i != disk.index]
        if not remaining:
            self.close_images()
            return
        gone = disk.index
        self._reopen(
            remaining, disk_map=lambda old: None if old == gone else old - (old > gone)
        )

    def _reveal_disk(self, disk: Disk) -> None:
        path = self.tree.disk_path(disk.index)
        if path is not None and not reveal.reveal(path):
            self.statusBar().showMessage(
                f"Couldn't open the folder of {Path(path).name}.", 6000
            )

    def _open_failed(self, message: str) -> None:
        self.close_images()
        QMessageBox.critical(self, "Couldn't open disk image", message)

    def _hfe_written(self) -> bool:
        """Whether every open HFE image has its edits in the file (they go back after each edit, so this is nearly always so). If
        one can't be written, say why and ask whether to go on and lose the edits."""
        failures = self._hfe.sync() if self._hfe.pending() else []
        if not failures:
            return True
        answer = QMessageBox.warning(
            self,
            "Edits not saved",
            "These HFE files couldn't be written, so their latest edits aren't in them:\n\n"
            + "\n".join(f"{Path(path).name}: {reason}" for path, reason in failures)
            + "\n\nClose anyway and lose those edits?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _close_asked(self) -> None:
        if self._hfe_written():
            self.close_images()

    def close_images(self) -> None:
        engine, self._engine = self._engine, None
        closed = list(self._images)
        self._hfe_warned.clear()
        self._floppy_files.clear()
        if engine is not None:
            def shut() -> None:
                engine.close()
                self._hfe.drop(closed)  # (once akaiutil has let go of the working copies)

            run_async(shut)  # can take a moment; keep the GUI responsive
        else:
            self._hfe.drop(closed)
        self._images = []
        self._disk_files = {}
        self._writable = False
        self._reset_views()
        self.stack.setCurrentIndex(0)
        self._sync_status_labels()
        self._refresh_recent()
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
        self._refs_cache.clear()
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
        box.setText(
            "akaiutil-gui needs the akaiutil program to read disk images, and it isn't installed yet."
        )
        box.setInformativeText(
            f"It can {fetch.describe_plan()}. The download is checked against a known checksum before it is used, "
            "and nothing else on your system is changed.\n\nOr, if you already have akaiutil, point the app at it."
        )
        looked = "\n".join(str(c) for c in candidates(settings.get_akaiutil_path()))
        box.setDetailedText(f"Looked for akaiutil in:\n{looked}")
        download = box.addButton(
            "Download and set up", QMessageBox.ButtonRole.AcceptRole
        )
        locate = box.addButton("Locate existing…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(download)
        box.exec()
        clicked = box.clickedButton()
        return (
            "download"
            if clicked is download
            else "locate"
            if clicked is locate
            else "cancel"
        )

    def _download_akaiutil(self, then) -> None:
        relay = _ProgressRelay(self)
        dialog = QProgressDialog(
            "Starting…", "", 0, 0, self
        )  # 0, 0: an indeterminate bar
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
            settings.set_akaiutil_path(
                str(path)
            )  # remembered, so this only ever happens once
            self.statusBar().showMessage(
                f"akaiutil {fetch.VERSION} is ready ({path})", 8000
            )
            self._refresh_akaiutil_version()
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
            QMessageBox.warning(
                self, "Not executable", f"{path} isn't an executable file."
            )
            return None
        self.statusBar().showMessage(f"Using akaiutil at {found}", 5000)
        self._refresh_akaiutil_version()
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
            if engine is not self._engine or epoch != self._usage_epoch.get(
                disk.index, 0
            ):
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
        disk = (
            self.tree.disk(obj.index if isinstance(obj, Disk) else obj.disk)
            if obj is not None
            else None
        )
        if self._engine is None or disk is None:
            self.usage.clear()
            return

        def show(usage: DiskUsage) -> None:
            if token != self._usage_token:
                return  # the selection moved on while this was loading
            if isinstance(obj, Disk):
                self.usage.show_disk(usage)
            else:
                self.usage.show_partition(
                    usage, obj.name if isinstance(obj, Partition) else obj.partition
                )

        if disk.index not in self._usage_cache:
            self.usage.show_loading()
        self._ensure_usage(disk, show)

    # ----------------------------------------------------------------- search
    def _searching(self) -> bool:
        return (
            bool(self.search.text().strip())
            or self.search_kind.currentData() != search.ALL
        )

    def _reset_search_ui(self) -> None:
        """Clear the search box and type filter without reacting to it."""
        self._search_timer.stop()
        self._in_results = False
        for widget, clear in (
            (self.search, lambda: self.search.clear()),
            (self.search_kind, lambda: self.search_kind.setCurrentIndex(0)),
        ):
            widget.blockSignals(True)
            clear()
            widget.blockSignals(False)
        self.files_title.setText(self._files_title_text)

    def _rebuild_catalog(self) -> None:
        self._catalog = [
            entry
            for index in sorted(self._usage_cache)
            for entry in self._usage_cache[index].catalog
        ]
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
        results, truncated = search.filter_catalog(
            self._catalog, query, self.search_kind.currentData()
        )
        self._in_results = True
        self.table.set_results(results)
        what = f"“{query}”" if query else self.search_kind.currentText().lower()
        title = f"{len(results):,}{'+' if truncated else ''} result{'s' if len(results) != 1 else ''} for {what}"
        self.files_title.setText(
            title
            + (
                f"  ·  showing the first {len(results):,}, narrow the search"
                if truncated
                else ""
            )
        )
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
        if current is not None and (current.disk, current.partition, current.index) == (
            vol.disk,
            vol.partition,
            vol.index,
        ):
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
            self._run(
                lambda: engine.list_files(obj),
                lambda files: self._files_loaded(token, files),
            )
            disk = self.tree.disk(obj.disk)
            cached = self._usage_cache.get(
                obj.disk
            )  # filled for the usage bar; else it's one quick listing

            def volume_details(text: str) -> describe.Details:
                usage = cached or (
                    engine.disk_usage(disk) if disk is not None else None
                )
                return describe.describe_volume(text, usage, self._references(engine, obj))

            self._show_info(
                lambda: engine.volume_info(obj), volume_details, remember=True
            )
        elif isinstance(obj, Partition):
            self._set_files_title("Select a volume to see its files")
            if obj.is_dd:
                self._show_info(
                    lambda: engine.list_takes_text(obj),
                    lambda t: describe.describe_takes(
                        f"Direct-to-disk area {obj.name}", t
                    ),
                )
            else:
                self._show_info(
                    lambda: engine.partition_info(obj), describe.describe_partition
                )
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
            self.statusBar().showMessage(
                f"{n} file{'s' if n != 1 else ''}, {total}", 8000
            )
        self._select_pending_file()
        self._update_actions()

    def _select_pending_file(self) -> None:
        """After revealing a search result: select its file once the volume's list is showing."""
        if self._pending_reveal is None or self._in_results:
            return
        vol, file_index = self._pending_reveal
        if self._volume is not None and (
            self._volume.disk,
            self._volume.partition,
            self._volume.index,
        ) == (vol.disk, vol.partition, vol.index):
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
        if (
            engine is None
            or len(rows) != 1
            or rows[0][0] is None
            or not rows[0][1].is_sample
        ):
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
            self._show_info(
                lambda: engine.file_info(vol, f),
                lambda text: describe.describe_file(text, f, self._references(engine, vol) if f.is_sample or f.is_program else None),
            )
        elif len(rows) > 1:
            self._info_current = 0
            self.info.show_details(describe.describe_selection([f for _, f in rows]))
        elif (
            self._in_results
        ):  # a search with nothing selected: there is no volume to fall back to
            self._info_current = 0
            self.info.show_details(None)
        elif (
            self._volume_details is not None
        ):  # nothing selected: back to the volume's own details
            self._info_current = 0
            self.info.show_details(self._volume_details)
        elif self._volume is not None:
            # ...which are still on their way. Let that request show itself when it arrives (the file
            # list landing and clearing the selection must not cancel it), but not a file's details.
            self._info_current = self._volume_token

    def _select_unused_samples(self) -> None:
        """Select, in the file list, the samples that no program in the volume plays (to look them over, or delete them)."""
        vol, engine = self._selected_volume(), self._engine
        if vol is None or engine is None or self._in_results:
            return
        token = self._files_token

        def done(refs) -> None:
            if token != self._files_token or refs is None:  # (another volume was picked meanwhile, or it couldn't be worked out)
                return
            if not refs.has_programs:
                message = f"“{vol.name}” has no programs, so there is nothing to say which samples are unused."
            elif not refs.unused_samples():
                message = f"Every sample in “{vol.name}” is played by a program."
            else:
                n = self.table.select_files({f.index for f in refs.unused_samples()})
                message = f"Selected {n} sample{'s' if n != 1 else ''} that no program plays."
            self.table.setFocus()
            self.statusBar().showMessage(message, 8000)

        self._run(lambda: self._references(engine, vol), done)

    def _references(self, engine, vol: Volume) -> references.VolumeReferences | None:
        """Which programs use which samples in a volume: cached until its disk is edited. Runs on a worker thread; None
        if it can't be worked out (the details just go without it)."""
        key = (vol.disk, vol.partition, vol.index, self._usage_epoch.get(vol.disk, 0))
        cached = self._refs_cache.get(key)
        if cached is not None:
            return cached
        try:
            refs = engine.volume_references(vol)
        except Exception:  # noqa: BLE001  (a nicety on top of the details: never let it take them down)
            log.exception("couldn't work out which programs use which samples in %s", vol.path)
            return None
        if engine is self._engine:
            for stale in [k for k in self._refs_cache if k[:3] == key[:3]]:  # (older versions of this volume's)
                del self._refs_cache[stale]
            self._refs_cache[key] = refs
        return refs

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
                QMessageBox.information(
                    self, "Export as WAV", "None of the selected files are samples."
                )
                return
        dest = self._export_destination()
        if not dest:
            return

        groups: dict[tuple, tuple[Volume, list[FileEntry]]] = {}
        for vol, f in rows:
            groups.setdefault((vol.disk, vol.partition, vol.index), (vol, []))[
                1
            ].append(f)
        several_volumes = len(groups) > 1
        several_disks = len({k[0] for k in groups}) > 1
        asker = _OverwriteAsker(self)
        self.statusBar().showMessage(f"Exporting {len(rows)} file(s)…")

        def work() -> ExportResult:
            merged = ExportResult()
            for vol, files in groups.values():
                # files from several volumes get a folder each, so same-named files can't overwrite each other
                target = (
                    Path(dest) / export_folder_name(vol, several_disks)
                    if several_volumes
                    else Path(dest)
                )
                target.mkdir(parents=True, exist_ok=True)
                res = engine.export_files(
                    vol, files, target, as_wav, confirm_overwrite=asker.ask
                )
                merged.exported += res.exported
                merged.skipped += res.skipped
                merged.failed += res.failed
            return merged

        self._run(work, lambda res: self._export_done(res, dest))

    def _export_node(self, obj) -> None:
        """Export every sample in a volume, partition or disk as WAV files, a folder for each volume."""
        engine = self._engine
        if engine is None or self._busy():
            return
        dest = self._export_destination()
        if not dest:
            return
        asker = _OverwriteAsker(self)
        self.statusBar().showMessage("Exporting samples as WAV…")

        def work() -> ExportResult:
            merged = ExportResult()
            for vol in self._volumes_under(engine, obj):
                samples = [f for f in engine.list_files(vol) if f.is_sample]
                if not samples:
                    continue
                target = Path(dest) / export_folder_name(vol, False)
                target.mkdir(parents=True, exist_ok=True)
                res = engine.export_files(
                    vol, samples, target, True, confirm_overwrite=asker.ask
                )
                merged.exported += res.exported
                merged.skipped += res.skipped
                merged.failed += res.failed
            return merged

        def done(res: ExportResult) -> None:
            if not (res.exported or res.skipped or res.failed):
                self.statusBar().showMessage(
                    "There are no samples to export there.", 8000
                )
                return
            self._export_done(res, dest)

        self._run(work, done)

    @staticmethod
    def _volumes_under(engine, obj) -> list[Volume]:
        """The volumes inside a tree item (a volume is itself), leaving out direct-to-disk recording partitions."""
        if isinstance(obj, Volume):
            return [obj]
        partitions = (
            [obj] if isinstance(obj, Partition) else engine.list_partitions(obj)
        )
        return [v for p in partitions if not p.is_dd for v in engine.list_volumes(p)]

    def _export_destination(self) -> str | None:
        """Where to export to: the default folder from Settings (without asking, if that's what's set), or a
        folder the user picks, starting from the default folder or the last one used."""
        default = settings.get_default_export_folder()
        if default and not settings.get_ask_export_folder():
            if Path(default).is_dir():
                return default
            self.statusBar().showMessage(
                f"The default export folder {default} doesn't exist; choose another.",
                8000,
            )
        start = (
            default
            if default and Path(default).is_dir()
            else settings.get_last_dir("export") or str(Path.home())
        )
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
            lines = [f"Skipped (already exists): {p}" for p in res.skipped] + [
                f"Failed: {m}" for m in res.failed
            ]
            box = QMessageBox(
                QMessageBox.Icon.Warning
                if res.failed
                else QMessageBox.Icon.Information,
                "Export finished",
                "  ·  ".join(parts),
                parent=self,
            )
            box.setDetailedText("\n".join(lines))
            box.exec()

    # -------------------------------------------------------------- editing
    # ------------------------------------------------------------ recent images
    @staticmethod
    def _existing_recent() -> list[str]:
        """The remembered images that are still there (and forget the ones that aren't)."""
        found = settings.get_recent_images()
        alive = [p for p in found if os.path.isfile(p)]
        for gone in set(found) - set(alive):
            settings.remove_recent_image(gone)
        return alive

    @staticmethod
    def _shown_folder(path: str) -> str:
        folder = str(Path(path).parent)
        home = str(Path.home())
        return "~" + folder[len(home) :] if folder == home or folder.startswith(home + os.sep) else folder

    def _refresh_recent(self) -> None:
        """Rebuild the start screen's list of recent images."""
        while self.recent_rows.count():
            item = self.recent_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        recent = self._existing_recent()[:RECENT_SHOWN]
        self.recent_box.setVisible(bool(recent))
        for path in recent:
            self.recent_rows.addWidget(self._recent_row(path))

    def _recent_row(self, path: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("recentButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(path)
        button.setProperty("path", path)
        row = QHBoxLayout(button)
        row.setContentsMargins(10, 0, 10, 0)
        name = QLabel(Path(path).name)
        folder = QLabel()
        folder.setObjectName("mutedLabel")
        folder.setText(
            QFontMetrics(folder.font()).elidedText(self._shown_folder(path), Qt.TextElideMode.ElideMiddle, RECENT_WIDTH // 2 - 20)
        )
        folder.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for label in (name, folder):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # (a click on the text is a click on the row)
        row.addWidget(name, 1)
        row.addWidget(folder)
        button.setMinimumHeight(max(30, name.sizeHint().height() + 10))
        button.clicked.connect(lambda _checked=False, p=path: self._open_recent(p))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda pos, b=button, p=path: self._recent_menu_for(b, p, pos))
        return button

    def _recent_row_menu(self, path: str) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Remove from Recent Files", lambda: self._forget_recent(path))
        return menu

    def _recent_menu_for(self, button: QPushButton, path: str, pos) -> None:
        self._recent_row_menu(path).exec(button.mapToGlobal(pos))

    def _forget_recent(self, path: str) -> None:
        settings.remove_recent_image(path)
        self._refresh_recent()  # (the next remembered image, if any, takes its place)

    def _open_recent(self, path: str) -> None:
        if not os.path.isfile(path):
            settings.remove_recent_image(path)
            self._refresh_recent()
            QMessageBox.warning(self, "Can't find that image", f"{path}\n\nisn't there any more (it was moved, renamed or deleted).")
            return
        self.open_images([path])

    def _clear_recent(self) -> None:
        settings.clear_recent_images()
        self._refresh_recent()

    def _fill_recent_menu(self) -> None:
        """File > Open Recent: the remembered images that still exist, newest first."""
        self.recent_menu.clear()
        recent = self._existing_recent()
        if not recent:
            self.recent_menu.addAction("No Recent Images").setEnabled(False)
            return
        for path in recent:
            action = self.recent_menu.addAction(Path(path).name, lambda p=path: self._open_recent(p))
            action.setToolTip(path)
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear Menu", self._clear_recent)

    def _sync_status_labels(self) -> None:
        """Read-only / Editing means nothing until an image is open, and the version is only worth a corner of the start screen."""
        browsing = self.stack.currentIndex() == 1
        self.mode_label.setVisible(browsing)
        self.version_label.setVisible(not browsing)

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
        damaged = [Path(p).name for p in self._hfe.damaged()]
        if damaged:
            QMessageBox.warning(
                self,
                "Can't edit this image",
                "These HFE files have sectors that couldn't be read, so they can only be looked at:\n\n"
                + "\n".join(damaged)
                + "\n\nClose them to edit the other images.",
            )
            self._sync_mode()
            return
        locked = [Path(p).name for p in self._images if not os.access(p, os.W_OK)]
        if locked:
            QMessageBox.warning(
                self,
                "Can't edit this image",
                "These files can't be written to, so they can't be edited:\n\n"
                + "\n".join(locked)
                + "\n\nChange their permissions (or copy them somewhere writable) and open them again.",
            )
            self._sync_mode()
            return
        need = [str(Path(p).resolve()) for p in self._images if str(Path(p).resolve()) not in self._backup_handled]
        if need:
            sizes = [(Path(p), Path(p).stat().st_size) for p in need if Path(p).exists()]
            total = sum(size for _, size in sizes) / 2**20
            answer = edit_dialogs.confirm(
                self,
                "Allow editing?",
                "Editing changes the disk image files themselves.",
                f"There is no undo. Back up {'this image' if len(sizes) == 1 else f'these {len(sizes)} images'} first ({total:.0f} MB)? "
                "The backups are kept in the app's folder, and File > Revert to Backup… puts one back.",
                action="Back Up and Edit",
                other_action="Edit Without Backup",
                details_lines=[f"{p.name}  ·  {size / 2**20:.1f} MB" for p, size in sizes],
                default_is_action=True,
            )
            if not answer:
                self._sync_mode()
                return
            if answer is edit_dialogs.OTHER:
                self._backup_handled.update(need)
                self._set_editing(True, then)
            else:
                self._backup_images(need, lambda: self._set_editing(True, then))
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

    def _backup_images(self, paths: list[str], then) -> None:
        """Copy images into the backup folder (on a worker thread), then carry on with `then`."""
        root = backup.default_root()
        self.statusBar().showMessage(f"Backing up {len(paths)} image{'s' if len(paths) != 1 else ''}…")

        def work() -> list[backup.Backup]:
            return [backup.make_backup(root, p) for p in paths]

        def done(made: list[backup.Backup]) -> None:
            for b in made:
                log.info("backed up %s as %s (%.0f MB)", b.image, b.path, b.size / 2**20)
            self._backup_handled.update(paths)
            self.statusBar().showMessage(f"Backed up {', '.join(Path(p).name for p in paths)}.", 6000)
            then()

        def failed(message: str) -> None:
            QMessageBox.critical(self, "Couldn't back up", f"{message}\n\nEditing is still off.")
            self.statusBar().clearMessage()
            self._sync_mode()

        self._run(work, done, failed)

    def _ask_revert(self, entries: list[tuple[Path, backup.Backup]]) -> tuple[Path, backup.Backup] | None:
        dialog = edit_dialogs.RevertDialog(self, entries)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.chosen

    def _revert(self) -> None:
        """File > Revert to Backup: put a backup back in place of an open image."""
        engine = self._engine
        if engine is None or self._busy():
            return
        root = backup.default_root()
        entries = [(Path(p), b) for p in self._images for b in backup.list_backups(root, p)]
        if not entries:
            QMessageBox.information(
                self, "Revert to Backup", "There are no backups of the open images yet. They are made when you switch editing on."
            )
            return
        chosen = self._ask_revert(entries)
        if chosen is None:
            return
        image, picked = chosen
        if not edit_dialogs.confirm(
            self,
            f"Revert {image.name}?",
            f"Replace {image.name} with the backup from {picked.created:%d %b %Y, %H:%M}?",
            "Everything done to it since then is lost, except that the image as it is now is backed up first.",
            action="Revert",
        ):
            return
        images = list(self._images)
        self._writing += 1
        self._update_actions()
        self.statusBar().showMessage(f"Reverting {image.name}…")

        def work() -> backup.Backup:
            log.info("reverting %s to the backup %s", image, picked.path)
            engine.close()  # (the image is replaced while nothing has it open)
            return backup.restore(root, picked, image)

        def finish(message: str = "", note: str = "") -> None:
            self._writing -= 1
            self.open_images(images)  # (read-only: editing is switched on again, and asked about again, when wanted)
            if message:
                QMessageBox.critical(self, "Couldn't revert", message)
            elif note:
                self.statusBar().showMessage(note, 10000)

        self._run(work, lambda before: finish(note=f"Reverted {image.name}. Before that it was backed up as {before.path.name}."), lambda message: finish(message=message))

    def _set_editing(self, on: bool, then=None) -> None:
        engine = self._engine
        if engine is None:
            return

        def done(_banner: str) -> None:
            if engine is not self._engine:
                return
            self._writable = on
            log.info("editing %s", "on" if on else "off")
            self._sync_mode()
            self.statusBar().showMessage(
                "Editing is on: changes are written to the disk image straight away."
                if on
                else "Editing is off.",
                8000,
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

        def work():
            try:
                return fn()
            finally:  # (akaiutil has written the working copy of an HFE image by now; the HFE file follows)
                self._hfe_failures += self._hfe.sync()

        def finish(handler):
            def wrapped(result):
                self._writing -= 1
                self._update_actions()
                handler(result)
                self._report_hfe_failures()

            return wrapped

        self._run(work, finish(on_done), finish(on_error))

    def _report_hfe_failures(self) -> None:
        failures, self._hfe_failures = self._hfe_failures, []
        if failures:
            self._warn(
                "Couldn't write the HFE file",
                "The change is made, but it couldn't be written into the HFE file: "
                + "; ".join(f"{Path(path).name} ({reason})" for path, reason in dict(failures).items())
                + ". It is kept in a working copy and written again after the next change or when you close the image, "
                "or use File > Save Disk as HFE… to save it somewhere else.",
            )

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
        return (
            obj if isinstance(obj, Partition) and self._can_hold_volumes(obj) else None
        )

    @staticmethod
    def _can_hold_volumes(part: Partition) -> bool:
        return not part.is_dd and not part.type.startswith(
            "FL"
        )  # a floppy's one volume is always there

    def _update_edit_actions(self) -> None:
        """Edit menu: say what Rename and Delete would do right now, and switch off what can't be done."""
        has = self._engine is not None and self._writing == 0
        target = self._edit_target() if has else None
        rename_text, delete_text, can_rename, can_delete = (
            "&Rename…",
            "&Delete…",
            False,
            False,
        )
        if target is not None:
            kind, what = target
            if kind == "volume":
                rename_text, delete_text, can_rename, can_delete = (
                    "&Rename Volume…",
                    "&Delete Volume…",
                    True,
                    True,
                )
            else:
                n = len(what)
                rename_text = "&Rename File…" if n == 1 else "&Rename…"
                delete_text = "&Delete File…" if n == 1 else f"&Delete {n} Files…"
                can_rename, can_delete = n == 1, True
        self.act_rename.setText(rename_text)
        self.act_delete.setText(delete_text)
        self.act_rename.setEnabled(can_rename)
        self.act_delete.setEnabled(can_delete)
        self.act_new_volume.setEnabled(
            has and self._partition_for_new_volume() is not None
        )
        self.act_format.setEnabled(has and self._selected_disk() is not None)
        self.act_import.setEnabled(has and self._selected_volume() is not None)
        self.act_sort.setEnabled(has and self._selected_volume() is not None)
        self.act_select_unused.setEnabled(has and self._selected_volume() is not None and not self._in_results)
        rows = self._selected_rows() if has else []
        self.act_sample_settings.setEnabled(self._editable_sample(rows) is not None)
        self.act_copy.setEnabled(bool(rows))
        self.act_cut.setEnabled(bool(rows))
        self.act_paste.setEnabled(
            has and self._clip is not None and self._selected_volume() is not None
        )

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

    def _ask_name(
        self,
        what: str,
        current: str,
        volume_type: str,
        taken: list[str],
        suffix: str = "",
    ) -> str | None:
        dialog = edit_dialogs.RenameDialog(
            self, what, current, volume_type, taken, suffix
        )
        return dialog.name if dialog.exec() == dialog.DialogCode.Accepted else None

    def _rename_volume(self, vol: Volume) -> None:
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_rename_volume(vol))

    def _do_rename_volume(self, vol: Volume) -> None:
        engine = self._engine
        taken = [
            v.name
            for v in self.tree.volumes_in(vol.disk, vol.partition)
            if v.index != vol.index
        ]
        name = self._ask_name("volume", vol.name, vol.type, taken)
        if engine is None or name is None:
            return
        self._run_edit(
            lambda: engine.rename_volume(vol, name),
            lambda new: self._edited(
                vol.disk, select=new, message=f"Renamed “{vol.name}” to “{new.name}”."
            ),
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
        if (
            not self._in_results
            and v is not None
            and (v.disk, v.partition, v.index) == (vol.disk, vol.partition, vol.index)
        ):
            taken = [  # the other files of the same type in this volume (the type is part of a file's name)
                akai_names.split_extension(o.name)[0]
                for o in self._volume_files
                if o.index != f.index
                and akai_names.split_extension(o.name)[1].casefold() == ext.casefold()
            ]
        name = self._ask_name("file", base, vol.type, taken, suffix=ext)
        if engine is None or name is None:
            return

        def done(new: FileEntry) -> None:
            self._pending_reveal = (vol, new.index) if not self._in_results else None
            self._edited(vol.disk, message=f"Renamed “{f.name}” to “{new.name}”.")

        self._run_edit(
            lambda: engine.rename_file(vol, f, name),
            done,
            lambda message: self._edit_failed(vol.disk, message),
        )

    @staticmethod
    def _editable_sample(rows: list[tuple[Volume, FileEntry]]) -> tuple[Volume, FileEntry] | None:
        """The one selected file whose settings can be edited: an S1000 or S3000 sample (not an S900 one)."""
        if len(rows) != 1:
            return None
        vol, f = rows[0]
        ok = f.is_sample and vol.type != "S900" and not f.name.upper().endswith((".S9", ".S9C"))
        return (vol, f) if ok else None

    def _sample_settings_selected(self) -> None:
        picked = self._editable_sample(self._selected_rows())
        if picked is not None:
            self._sample_settings(*picked)

    def _sample_settings(self, vol: Volume, f: FileEntry) -> None:
        """Edit > Sample Settings: change a sample's root key, tuning, play mode, markers and loops."""
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_sample_settings(vol, f))

    def _match_search_height(self) -> None:
        self.search.setFixedHeight(self.search_kind.sizeHint().height())

    def _on_focus_window(self, _window) -> None:
        """A dialog has just opened over the window: whatever the preview is playing stops (the dialog may play its own sound, or be
        about the very sample that is playing)."""
        active = QApplication.activeWindow()
        if isinstance(active, QDialog) and active.isModal():
            self.preview.stop()

    def _ask_sample_settings(self, f: FileEntry, current, audio=None):
        self.preview.stop()  # (the dialog has its own player; the focus change below would stop this one too, but not on every platform)
        dialog = edit_dialogs.SampleSettingsDialog(self, f.name, current, audio)
        return dialog.result_settings if dialog.exec() == dialog.DialogCode.Accepted else None

    def _do_sample_settings(self, vol: Volume, f: FileEntry) -> None:
        engine = self._engine
        if engine is None:
            return

        cached = self._preview_cache.get((vol.disk, vol.partition, vol.index, f.index, f.name))  # (the preview may have it already)

        def load():
            current = engine.read_sample_settings(vol, f)
            if cached is not None:
                return current, cached.samples
            try:  # the waveform is a help: if the audio can't be read, the markers still work without it
                wav = engine.export_sample_wav(vol, f, self._preview_directory())
                try:
                    return current, load_wav(wav, f.name, None).samples
                finally:
                    wav.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                log.warning("no waveform for the settings dialog of %s: %s", f.name, e)
                return current, None

        def show(loaded) -> None:
            current, audio = loaded
            new = self._ask_sample_settings(f, current, audio)
            if new is None or new == current:
                return

            def done(after: FileEntry) -> None:
                self._pending_reveal = (vol, after.index) if not self._in_results else None
                self._edited(vol.disk, message=f"Changed the settings of “{f.name}”.")

            self._run_edit(
                lambda: engine.edit_sample_settings(vol, f, new),
                done,
                lambda message: self._edit_failed(vol.disk, message),
            )

        self._run(load, show)

    def _delete_files(self, rows: list[tuple[Volume, FileEntry]]) -> None:
        if self._engine is None or not rows or self._busy():
            return
        self._ensure_editing(lambda: self._do_delete_files(rows))

    def _do_delete_files(self, rows: list[tuple[Volume, FileEntry]]) -> None:
        engine = self._engine
        groups: dict[tuple, tuple[Volume, list[FileEntry]]] = {}
        for vol, f in rows:
            groups.setdefault((vol.disk, vol.partition, vol.index), (vol, []))[
                1
            ].append(f)
        if len(rows) == 1:
            vol, f = rows[0]
            text = f"Delete “{f.name}” from {vol.name}?"
        elif len(groups) == 1:
            text = f"Delete {len(rows)} files from {rows[0][0].name}?"
        else:
            text = f"Delete {len(rows)} files from {len(groups)} volumes?"
        lines = [f"{vol.name}: {f.name}" for vol, f in rows] if len(rows) > 1 else []
        if engine is None or not edit_dialogs.confirm(
            self, "Delete files?", text, "This can't be undone.", details_lines=lines
        ):
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
            self.statusBar().showMessage(
                f"Deleted {n} file{'s' if n != 1 else ''}.", 8000
            )
            if failed:
                self._warn(
                    "Some files weren't deleted",
                    f"{len(failed)} of {len(rows)} files couldn't be deleted.",
                    failed,
                )

        self._run_edit(work, done, lambda message: self._edit_failed(disks[0], message))

    def _delete_volume(self, vol: Volume) -> None:
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_delete_volume(vol))

    def _do_delete_volume(self, vol: Volume) -> None:
        engine = self._engine
        usage = self._usage_cache.get(vol.disk)
        count = (
            next(
                (
                    u.files
                    for u in usage.volumes
                    if (u.partition, u.index) == (vol.partition, vol.index)
                ),
                None,
            )
            if usage
            else None
        )
        detail = "Every file in it goes too. This can't be undone."
        if count is not None:
            detail = f"It holds {count} file{'s' if count != 1 else ''}, and they go too. This can't be undone."
        if engine is None or not edit_dialogs.confirm(
            self, "Delete volume?", f"Delete the volume “{vol.name}”?", detail
        ):
            return
        self._run_edit(
            lambda: engine.delete_volume(vol),
            lambda _none: self._edited(
                vol.disk, message=f"Deleted the volume “{vol.name}”."
            ),
            lambda message: self._edit_failed(vol.disk, message),
        )

    def _copy_volume(self, vol: Volume) -> None:
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_copy_volume(vol))

    def _do_copy_volume(self, vol: Volume) -> None:
        engine = self._engine
        if engine is None:
            return

        def survey() -> tuple[int, int, list[edit_dialogs.CopyTarget]]:
            files = engine.list_files(vol)
            targets = []
            for disk in engine.list_disks():
                for part in engine.list_partitions(disk):
                    if self._can_hold_volumes(part) and (part.type == "HD9") == (
                        vol.type == "S900"
                    ):
                        targets.append(
                            edit_dialogs.CopyTarget(
                                part, tuple(v.name for v in engine.list_volumes(part))
                            )
                        )
            block = targets[0].partition.block_size if targets else 8192
            return len(files), sum(-(-f.size // block) * block for f in files), targets

        def show(found: tuple[int, int, list[edit_dialogs.CopyTarget]]) -> None:
            count, need, targets = found
            if not targets:
                QMessageBox.information(
                    self,
                    "Copy Volume",
                    "There is no hard disk partition to copy it to.",
                )
                return
            answer = self._ask_copy_volume(vol, count, need, targets)
            if answer is None:
                return
            target, name = answer
            self.statusBar().showMessage(f"Copying “{vol.name}”…")

            def done(copied) -> None:
                self._edited(
                    target.disk,
                    select=copied.volume,
                    announce=True,
                    message=f"Copied “{vol.name}” to “{copied.volume.name}” (partition {target.name}).",
                )
                if copied.notes:
                    self._warn(
                        "About the copy",
                        f"“{copied.volume.name}” was copied.",
                        copied.notes,
                    )

            self._run_edit(
                lambda: engine.copy_volume(vol, target, name),
                done,
                lambda message: self._edit_failed(target.disk, message),
            )

        self._run(survey, show)

    def _ask_copy_volume(
        self, vol: Volume, count: int, need: int, targets
    ) -> tuple[Partition, str] | None:
        dialog = edit_dialogs.CopyVolumeDialog(self, vol, count, need, targets)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.target.partition, dialog.name

    def _copy_partition(self, part: Partition) -> None:
        if self._engine is None or self._busy():
            return
        self._ensure_editing(lambda: self._do_copy_partition(part))

    def _do_copy_partition(self, part: Partition) -> None:
        engine = self._engine
        if engine is None:
            return

        def survey():
            volumes = engine.list_volumes(part)
            sizes = [f.size for v in volumes for f in engine.list_files(v)]
            targets = [
                edit_dialogs.CopyTarget(other, tuple(v.name for v in engine.list_volumes(other)))
                for disk in engine.list_disks()
                for other in engine.list_partitions(disk)
                if self._can_hold_volumes(other) and (other.disk, other.name) != (part.disk, part.name) and (other.type == "HD9") == (part.type == "HD9")
            ]
            block = targets[0].partition.block_size if targets else 8192
            return tuple(v.name for v in volumes), len(sizes), sum(-(-s // block) * block for s in sizes), targets

        def show(found) -> None:
            names, files, need, targets = found
            if not names:
                QMessageBox.information(self, "Copy Partition", f"Partition {part.name} has no volumes to copy.")
                return
            if not targets:
                QMessageBox.information(self, "Copy Partition", "There is no other hard disk partition to copy it to.")
                return
            target = self._ask_copy_partition(part, names, files, need, targets)
            if target is None:
                return
            self.statusBar().showMessage(f"Copying partition {part.name}…")

            def done(copied) -> None:
                count = len(copied.volumes)
                self._edited(
                    target.disk,
                    select=copied.volumes[0],
                    announce=True,
                    message=f"Copied {count} volume{'s' if count != 1 else ''} of partition {part.name} to partition {target.name}.",
                )
                if copied.notes:
                    self._warn("About the copy", f"Partition {part.name} was copied.", copied.notes)

            self._run_edit(
                lambda: engine.copy_partition(part, target),
                done,
                lambda message: self._edit_failed(target.disk, message),
            )

        self._run(survey, show)

    def _ask_copy_partition(self, part: Partition, names, files: int, need: int, targets) -> Partition | None:
        dialog = edit_dialogs.CopyPartitionDialog(self, part, names, files, need, targets)
        return dialog.target.partition if dialog.exec() == dialog.DialogCode.Accepted else None

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
        common = max(
            (t for t in types if t in existing), key=existing.count, default="S3000"
        )
        return types, common

    def _do_new_volume(self, part: Partition) -> None:
        engine = self._engine
        if engine is None:
            return

        def show(slots: tuple[list[int], int]) -> None:
            used, limit = slots
            if len(used) >= limit:
                QMessageBox.information(
                    self,
                    "New Volume",
                    f"Partition {part.name} already holds the most volumes it can ({limit}).",
                )
                return
            types, default = self._volume_types(part)
            taken = [v.name for v in self.tree.volumes_in(part.disk, part.name)]
            answer = self._ask_new_volume(part, used, limit, types, default, taken)
            if answer is None:
                return
            name, number, volume_type, load_number = answer
            self._run_edit(
                lambda: engine.create_volume(
                    part, number, name, volume_type, load_number
                ),
                lambda new: self._edited(
                    part.disk,
                    select=new,
                    announce=True,
                    message=f"Created the volume “{new.name}”.",
                ),
                lambda message: self._edit_failed(part.disk, message),
            )

        self._run(lambda: engine.volume_slots(part), show)

    def _ask_new_volume(
        self, part, used, limit, types, default, taken
    ) -> tuple[str, int, str, int | None] | None:
        dialog = edit_dialogs.NewVolumeDialog(
            self, part.name, used, limit, types, default, taken
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.name, dialog.volume_number, dialog.volume_type, dialog.load_number

    def _ask_format(self, disk: Disk) -> tuple[formats.DiskFormat, int] | None:
        dialog = edit_dialogs.DiskFormatDialog(self, disk)
        return (
            (dialog.format, dialog.partition_mb)
            if dialog.exec() == dialog.DialogCode.Accepted
            else None
        )

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
        name = (
            Path(self._images[disk.index]).name
            if disk.index < len(self._images)
            else f"disk{disk.index}"
        )
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
            lambda _none: self._edited(
                disk.index,
                select=disk,
                announce=True,
                message=f"Formatted disk{disk.index} as {fmt.label}.",
            ),
            lambda message: self._edit_failed(disk.index, message),
        )

    def _ask_new_image(self) -> tuple[formats.DiskFormat, int, int] | None:
        dialog = edit_dialogs.DiskFormatDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.format, dialog.size_mb, dialog.partition_mb

    def _ask_hfe_path(self, suggestion: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, "Save disk as HFE", suggestion, "HFE images (*.hfe);;All files (*)")
        return path or None

    def _save_as_hfe(self) -> None:
        """File > Save Disk as HFE: the selected floppy as an HFE file (for a Gotek or HxC emulator)."""
        disk = self._selected_disk()
        source = self._disk_files.get(disk.index) if disk is not None else None
        if disk is None or source is None or self._busy():
            return
        start = settings.get_last_dir("save_hfe") or str(Path(source).parent)
        path = self._ask_hfe_path(str(Path(start) / f"{Path(source).stem}.hfe"))
        if path is None:
            return
        if not Path(path).suffix:
            path += ".hfe"
        if any(Path(path).resolve() == Path(p).resolve() for p in self._images):
            QMessageBox.warning(self, "That image is open", f"{Path(path).name} is open in the window. Close it first if you want to replace it.")
            return
        settings.set_last_dir("save_hfe", str(Path(path).parent))
        self.statusBar().showMessage(f"Saving {Path(path).name}…")
        self._run(
            lambda: self._hfe.save_as(source, path),
            lambda _none: self.statusBar().showMessage(f"Saved {Path(path).name}.", 8000),
            lambda message: (self.statusBar().clearMessage(), QMessageBox.critical(self, "Couldn't save the HFE file", message)),
        )

    def _create_os_floppy(self) -> None:
        """File > Create OS Boot Floppy: an S2000-family OS file (.COD, or the .C the sampler saves) as a floppy image or HFE file."""
        start = settings.get_last_dir("os_file") or str(Path.home())
        source, _ = QFileDialog.getOpenFileName(self, "Choose the OS file", start, "OS files (*.cod *.c);;All files (*)")
        if not source:
            return
        settings.set_last_dir("os_file", str(Path(source).parent))
        try:
            data, info = os_floppy.read(source)
            image = os_floppy.build(data, info)
        except os_floppy.OsFloppyError as e:
            QMessageBox.warning(self, "Not an OS file", str(e))
            return
        start = settings.get_last_dir("os_floppy") or str(Path(source).parent)
        path, chosen = QFileDialog.getSaveFileName(
            self, f"Save the {info.description} boot floppy", str(Path(start) / f"{info.model} OS {info.major}.{info.minor:02d}.img"),
            "Floppy images (*.img);;HFE images (*.hfe)",
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".hfe" if "hfe" in chosen else ".img"
        if any(Path(path).resolve() == Path(p).resolve() for p in self._images):
            QMessageBox.warning(self, "That image is open", f"{Path(path).name} is open in the window. Close it first if you want to replace it.")
            return
        settings.set_last_dir("os_floppy", str(Path(path).parent))
        self.statusBar().showMessage(f"Saving {Path(path).name}…")
        self._run(
            lambda: os_floppy.save(path, image),
            lambda _none: self.statusBar().showMessage(f"Saved {Path(path).name}: {info.description} boot floppy.", 8000),
            lambda message: (self.statusBar().clearMessage(), QMessageBox.critical(self, "Couldn't save the floppy", message)),
        )

    def _ask_save_path(self, suggestion: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Create disk image", suggestion, IMAGE_FILTER
        )
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
        path = self._ask_save_path(
            str(Path(start) / f"{fmt.label}.img".replace("(", "").replace(")", ""))
        )
        if path is None:
            return
        if not Path(path).suffix:
            path += ".img"
        if any(Path(path).resolve() == Path(p).resolve() for p in self._images):
            QMessageBox.warning(
                self,
                "That image is open",
                f"{Path(path).name} is open in the window. Close it first if you want to replace it.",
            )
            return
        settings.set_last_dir("open", str(Path(path).parent))
        engine = AkaiEngine(binary, on_traffic=self._traffic.feed)
        self.statusBar().showMessage(f"Creating {Path(path).name}…")
        self._run(
            lambda: engine.create_image(path, fmt, size_mb, partition_mb),
            lambda made: (self._backup_handled.add(str(Path(made).resolve())), self.add_images([str(made)])),
            lambda message: QMessageBox.critical(
                self, "Couldn't create the disk image", message
            ),
        )

    # --------------------------------------------------- copying and moving files
    def _copy_selection(self, mode: str) -> None:
        """Edit > Copy / Cut: remember the selected files until Paste (or a drop) says where they go."""
        rows = self._selected_rows()
        if not rows:
            return
        self._clip = (mode, rows)
        n = len(rows)
        self.statusBar().showMessage(
            f"{'Copied' if mode == 'copy' else 'Cut'} {n} file{'s' if n != 1 else ''}. Select a volume and choose Paste Files"
            + (" to move them." if mode == "cut" else "."),
            8000,
        )
        self._update_edit_actions()

    def _paste(self, volume: Volume | None = None) -> None:
        volume = volume if isinstance(volume, Volume) else self._selected_volume()
        if self._clip is None or volume is None:
            return
        mode, rows = self._clip
        self.transfer_files(rows, volume, move=(mode == "cut"))

    def _on_transfer_drop(self, volume, move: bool, mime) -> None:
        if volume is None:
            self.statusBar().showMessage("Drop the files on a volume.", 5000)
            return
        self.transfer_files(list(mime.rows), volume, move)

    def transfer_files(
        self, rows: list[tuple[Volume, FileEntry]], dst: Volume, move: bool
    ) -> None:
        """Copy (or move) files into a volume: from Paste, or from a drop on the volume in the tree."""
        if self._engine is None or not rows or self._busy():
            return
        self._ensure_editing(lambda: self._do_transfer(rows, dst, move))

    def _do_transfer(
        self, rows: list[tuple[Volume, FileEntry]], dst: Volume, move: bool
    ) -> None:
        engine = self._engine
        if engine is None:
            return
        add_anyway = self._ask_add_anyway(rows, dst, move)
        if add_anyway is None:
            return
        relay = _ProgressRelay(self)
        relay.message.connect(lambda text: self.statusBar().showMessage(text))
        verb = "Moving" if move else "Copying"

        sort_after = settings.get_sort_after_adding()

        def work() -> tuple[transfer.TransferResult, str]:
            result = transfer.transfer_files(
                engine,
                rows,
                dst,
                move,
                progress=lambda i, n, name: relay.message.emit(
                    f"{verb} {i + 1} of {n}: {name}…"
                ),
                add_anyway=add_anyway,
            )
            return result, (
                self._sort_after(engine, dst) if sort_after and result.done else ""
            )

        disks = sorted({dst.disk} | ({src.disk for src, _ in rows} if move else set()))

        def done(answer: tuple[transfer.TransferResult, str]) -> None:
            result, sort_problem = answer
            relay.deleteLater()
            n = len(result.done)
            what = f"{'Moved' if move else 'Copied'} {n} file{'s' if n != 1 else ''} to “{dst.name}”."
            if result.failed:
                what += f"  {len(result.failed)} failed."
            for disk in disks:
                self._edited(disk, message=what)
            if move and result.done:
                self._clip = None  # what was cut is where it is now
            if sort_problem:
                self._warn("Couldn't sort the volume", sort_problem)
            problems = result.failed + result.skipped
            if problems:
                self._warn(
                    "Some files weren't transferred",
                    f"{len(problems)} of {len(rows)} files weren't {'moved' if move else 'copied'}.",
                    problems,
                )
            if result.notes:
                self._warn(
                    "Some tags didn't come along",
                    "The destination has no tag with the same name for these, so they arrived without it.",
                    result.notes,
                )

        def failed(message: str) -> None:
            relay.deleteLater()
            self._edit_failed(dst.disk, message)

        self._run_edit(work, done, failed)

    def _ask_add_anyway(
        self, rows: list[tuple[Volume, FileEntry]], dst: Volume, move: bool
    ) -> bool | None:
        """Files made for another sampler than the volume's: put them in anyway, or leave them out? (None: cancel the whole
        transfer; False when nothing needs asking.) Files that would come out wrong are left out without a question."""
        found = [
            (name, m)
            for name, m in transfer.mismatches(rows, dst, move)
            if m.forceable
        ]
        if not found:
            return False
        n = len(found)
        samplers = list(dict.fromkeys(m.file_sampler for _, m in found))
        answer = edit_dialogs.confirm(
            self,
            "Incompatible Files",
            f"{n} incompatible file{'s' if n != 1 else ''} for {dst.type} volume “{dst.name}”. Add anyway?",
            f"These are {' and '.join(samplers)} files and the destination volume is {importing.article(dst.type)} {dst.type} one.",
            action="Add Anyway",
            other_action="Skip",
            details_lines=[f"{name}: {m.reason}" for name, m in found],
        )
        if answer is edit_dialogs.OTHER:
            return False
        return True if answer else None

    def _file_drag_mime(self):
        """What dragging the selected files carries (see ui/file_drag.py)."""
        rows = self._selected_rows()
        if not rows or self._engine is None:
            return None
        as_raw = bool(
            QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier
        )  # Option on a Mac
        return file_drag.FileDragData(rows, lambda: self._export_for_drag(rows, as_raw))

    def _export_for_drag(
        self, rows: list[tuple[Volume, FileEntry]], as_raw: bool
    ) -> list[str]:
        """Write the dragged files somewhere another application can reach, and say where. Samples are WAVs unless `as_raw`."""
        engine = self._engine
        if engine is None:
            return []
        if self._drag_dir is not None:
            shutil.rmtree(self._drag_dir, ignore_errors=True)
        self._drag_dir = tempfile.mkdtemp(prefix="akaiutil-gui-drag-")
        groups: dict[tuple, tuple[Volume, list[FileEntry]]] = {}
        for vol, f in rows:
            groups.setdefault((vol.disk, vol.partition, vol.index), (vol, []))[
                1
            ].append(f)
        paths: list[str] = []
        for vol, files in groups.values():
            # files from several volumes get a folder each, so same-named files can't overwrite each other
            target = (
                Path(self._drag_dir) / export_folder_name(vol, True)
                if len(groups) > 1
                else Path(self._drag_dir)
            )
            if len(groups) > 1:
                target.mkdir(parents=True, exist_ok=True)
                paths.append(str(target))
            samples = [f for f in files if f.is_sample and not as_raw]
            others = [f for f in files if f not in samples]
            for batch, wav in ((samples, True), (others, False)):
                if batch:
                    res = engine.export_files(
                        vol,
                        batch,
                        target,
                        as_wav=wav,
                        confirm_overwrite=lambda _p: True,
                    )
                    if len(groups) == 1:
                        paths += res.exported
        return paths

    # ----------------------------------------------------------------- sorting
    @staticmethod
    def _sort_after(engine, volume: Volume) -> str:
        """Sort a volume after files were added to it (on a worker thread). Returns what went wrong, or ""."""
        try:
            files = engine.list_files(volume)
            engine.sort_files(
                volume,
                sorting.ALL if all(f.is_sample for f in files) else sorting.SAMPLES,
            )
        except Exception as e:
            return str(e)
        return ""

    def _ask_sort(self, volume: Volume, files: list[FileEntry]) -> str | None:
        dialog = edit_dialogs.SortDialog(self, volume, files)
        return dialog.scope if dialog.exec() == dialog.DialogCode.Accepted else None

    def _sort_volume(self, volume: Volume | None = None) -> None:
        """Edit > Sort Files by Name: put a volume's files in alphabetical order (the order a sampler lists them in)."""
        volume = volume if isinstance(volume, Volume) else self._selected_volume()
        engine = self._engine
        if engine is None or volume is None or self._busy():
            return

        def ask() -> None:
            self._run(
                lambda: engine.list_files(volume),
                lambda files: self._do_sort(volume, files),
            )

        self._ensure_editing(ask)

    def _do_sort(self, volume: Volume, files: list[FileEntry]) -> None:
        engine = self._engine
        if engine is None:
            return
        if not files:
            self.statusBar().showMessage(f"“{volume.name}” has no files to sort.", 6000)
            return
        scope = self._ask_sort(volume, files)
        if scope is None:
            return

        def done(moved: int) -> None:
            text = (
                f"Sorted “{volume.name}”: {moved} file{'s' if moved != 1 else ''} moved."
                if moved
                else f"“{volume.name}” was already in order."
            )
            self._edited(volume.disk, select=volume, announce=True, message=text)

        self._run_edit(
            lambda: engine.sort_files(volume, scope),
            done,
            lambda message: self._edit_failed(volume.disk, message),
        )

    # --------------------------------------------------------------- importing
    def _ask_import_files(self) -> list[str]:
        start = (
            settings.get_last_dir("import")
            or settings.get_last_dir("open")
            or str(Path.home())
        )
        audio = " ".join(f"*{e}" for e in sorted(importing.AUDIO_EXTENSIONS))
        akai = " ".join(f"*{e} *{e.upper()}" for e in sorted(importing.AKAI_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import into the volume",
            start,
            f"Audio and Akai files ({audio} {akai});;Operating system images (*.C *.c *.C3 *.c3);;All files (*)",
        )
        if paths:
            settings.set_last_dir("import", str(Path(paths[0]).parent))
        return paths

    def _import_dialog(self, volume: Volume | None = None) -> None:
        volume = volume if isinstance(volume, Volume) else self._selected_volume()
        if self._engine is None or volume is None:
            return
        paths = self._ask_import_files()
        if paths:
            self.import_files(importing.expand(paths), volume)

    def import_files(self, paths: list, volume: Volume | None = None) -> None:
        """Put audio and Akai files into a volume (the one given, else the selected one), in alphabetical order."""
        if self._engine is None:
            self.statusBar().showMessage(
                "Open a disk image first, then drop the samples onto one of its volumes.",
                8000,
            )
            return
        volume = volume or self._selected_volume()
        if volume is None:
            QMessageBox.information(
                self,
                "Import",
                "Select the volume to import into (or drop the files onto it in the list of disks), then try again.",
            )
            return
        if self._busy():
            return
        sources = [
            importing.inspect(p) for p in sorted(paths, key=importing.natural_key)
        ]
        self._ensure_editing(lambda: self._do_import(volume, sources))

    def _ask_import(
        self, volume: Volume, sources: list
    ) -> tuple[str, bool, str, importing.ImportOptions] | None:
        dialog = edit_dialogs.ImportDialog(self, volume, sources)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.sample_type, dialog.compress_s900, dialog.stereo, dialog.options

    def _do_import(self, volume: Volume, sources: list) -> None:
        engine = self._engine
        answer = self._ask_import(volume, sources)
        if engine is None or answer is None:
            return
        sample_type, compress, stereo, options = answer
        relay = _ProgressRelay(self)
        relay.message.connect(lambda text: self.statusBar().showMessage(text))

        sort_after = settings.get_sort_after_adding()

        def work() -> tuple[importing.ImportResult, str]:
            work_dir = Path(tempfile.mkdtemp(prefix="akaiutil-gui-import-"))
            try:
                result = importing.run_import(
                    engine,
                    volume,
                    sources,
                    work_dir,
                    sample_type,
                    compress,
                    stereo,
                    progress=lambda i, n, name: relay.message.emit(
                        f"Importing {i + 1} of {n}: {name}…"
                    ),
                    options=options,
                )
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
            return result, (
                self._sort_after(engine, volume)
                if sort_after and result.imported
                else ""
            )

        def done(answer: tuple[importing.ImportResult, str]) -> None:
            result, sort_problem = answer
            relay.deleteLater()
            n = result.count
            text = f"Imported {n} sample{'s' if n != 1 else ''} into “{volume.name}”."
            if result.failed:
                text += f"  {len(result.failed)} failed."
            self._edited(volume.disk, select=volume, announce=True, message=text)
            if sort_problem:
                self._warn("Couldn't sort the volume", sort_problem)
            if result.failed:
                self._warn(
                    "Some files weren't imported",
                    f"{len(result.failed)} of {len(sources)} files couldn't be imported.",
                    result.failed,
                )

        def failed(message: str) -> None:
            relay.deleteLater()
            self._edit_failed(volume.disk, message)

        self._run_edit(work, done, failed)

    def _edited(
        self,
        disk_index: int,
        select=None,
        announce: bool | None = None,
        message: str = "",
    ) -> None:
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
                token = (
                    self._files_token
                )  # keep the volume's list current for when the search ends
                self._run(
                    lambda: engine.list_files(volume),
                    lambda files: self._files_loaded(token, files),
                )
        if message:
            self._status_hold = time.monotonic() + 8
            self.statusBar().showMessage(message, 8000)

    def _edit_failed(self, disk_index: int, message: str) -> None:
        self._warn(
            "Couldn't change the disk image",
            message.splitlines()[0] if message else "akaiutil refused.",
            message.splitlines()[1:],
        )
        engine = self._engine
        if engine is None:
            return
        if not engine.is_open:
            self.close_images()
            return
        self._edited(
            disk_index
        )  # whatever it managed to do, show the disk as it is now

    def _warn(self, title: str, text: str, details: list[str] | None = None) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        if details:
            box.setDetailedText("\n".join(details))
        box.exec()

    def _tree_menu(self, obj) -> QMenu | None:
        menu = QMenu(self)
        if isinstance(obj, Volume):
            menu.addAction("Import Files…", lambda: self._import_dialog(obj))
            paste = menu.addAction("Paste Files", lambda: self._paste(obj))
            paste.setEnabled(self._clip is not None)
            menu.addAction("Sort Files by Name…", lambda: self._sort_volume(obj))
            menu.addAction("Select Unused Samples", self._select_unused_samples)
            menu.addSeparator()
            menu.addAction("Export Samples as WAV…", lambda: self._export_node(obj))
            menu.addAction("Copy Volume To…", lambda: self._copy_volume(obj))
            menu.addSeparator()
            menu.addAction("Rename…", lambda: self._rename_volume(obj))
            menu.addAction("Delete…", lambda: self._delete_volume(obj))
        elif isinstance(obj, Partition):
            if self._can_hold_volumes(obj):
                menu.addAction("New Volume…", lambda: self._new_volume(obj))
                menu.addAction("Copy Partition To…", lambda: self._copy_partition(obj))
            if not obj.is_dd:
                menu.addAction("Export Samples as WAV…", lambda: self._export_node(obj))
        elif isinstance(obj, Disk):
            menu.addAction("Export Samples as WAV…", lambda: self._export_node(obj))
            menu.addSeparator()
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
        menu.addAction("Copy", lambda: self._copy_selection("copy"))
        menu.addAction("Cut", lambda: self._copy_selection("cut"))
        menu.addSeparator()
        rename = menu.addAction("Rename…", lambda: self._rename_file(*rows[0]))
        rename.setEnabled(len(rows) == 1)
        settings_action = menu.addAction("Sample Settings…", lambda: self._sample_settings(*rows[0]))
        settings_action.setEnabled(self._editable_sample(rows) is not None)
        menu.addAction(
            "Delete…" if len(rows) == 1 else f"Delete {len(rows)} Files…",
            lambda: self._delete_files(rows),
        )
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
            action.setChecked(
                True
            )  # keeps View > Theme in step when the Settings dialog changed it

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self, update_runner=self._update_runner, locate=self.locate_binary
        )
        dialog.themeChosen.connect(self._set_theme)
        dialog.exec()

    def schedule_update_check(self) -> None:
        """The automatic check: at most once a day, a couple of seconds after startup, and only if enabled."""
        due = time.time() - settings.get_last_update_check() > CHECK_INTERVAL_SECONDS
        if settings.get_check_updates_on_startup() and due:
            QTimer.singleShot(2000, lambda: self._update_runner.start(manual=False))

    def schedule_backup_cleanup(self) -> None:
        """A few seconds after startup, delete backups older than the Settings say (on a worker thread: they can be big files)."""
        QTimer.singleShot(4000, self.run_backup_cleanup)

    def run_backup_cleanup(self) -> None:
        days = settings.get_backup_keep_days()
        root = backup.default_root()

        def done(removed: list[Path]) -> None:
            if removed:
                log.info("deleted %d backup(s) older than %d days from %s", len(removed), days, root)

        run_async(lambda: backup.cleanup(root, days), done, lambda message: log.warning("backup cleanup failed: %s", message))
        run_async(self._hfe.cleanup, lambda removed: removed and log.info("deleted %d old HFE working cop%s", len(removed), "y" if len(removed) == 1 else "ies"))

    def _about(self) -> None:
        AboutDialog(self).exec()

    def _restore_geometry(self) -> None:
        geometry = QSettings().value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        for name, splitter in self._splitters().items():
            state = QSettings().value(f"window/{name}")
            if state is not None:
                splitter.restoreState(state)

    def _splitters(self) -> dict[str, Splitter]:
        return {"browserSplitter": self.browser_splitter, "rightSplitter": self.right_splitter}

    # ------------------------------------------------------------- events
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(file_drag.INTERNAL_MIME):
            event.ignore()  # files dragged out of the list only mean something dropped on a volume in the tree
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(file_drag.INTERNAL_MIME):
            return
        self.handle_drop(
            [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        )

    def _on_tree_drop(self, volume, paths: list[str]) -> None:
        self.handle_drop(paths, volume)

    def handle_drop(self, paths: list[str], volume: Volume | None = None) -> None:
        """Files dropped on the window. Dropped on a volume, everything goes into that volume (the import dialog says what it
        makes of each file). Otherwise audio and Akai files are imported (into the selected volume), anything that could be a
        disk image is opened, and the rest is turned away."""
        files = importing.expand(paths)
        if volume is not None:
            if files:
                self.import_files(files, volume)
            return
        samples = [f for f in files if importing.kind_of(f) != "other"]
        images = self._only_disk_images([f for f in files if importing.kind_of(f) == "other"], dropped=True)
        if samples:
            if images:
                self.statusBar().showMessage(
                    f"Ignored {len(images)} disk image{'s' if len(images) != 1 else ''} dropped along with samples.",
                    6000,
                )
            self.import_files(samples, volume)
        elif images:
            self.add_images(images)

    def _only_disk_images(self, files: list, dropped: bool = False) -> list[str]:
        """The files that might be disk images, as paths. The others are named in a message and left out: akaiutil would "open"
        a PDF or a text file without complaint, and show a disk with nothing on it."""
        images, rejected = [], []
        for f in files:
            problem = importing.disk_image_problem(f)
            if problem:
                rejected.append(f"{Path(f).name}: {problem}")
            else:
                images.append(str(f))
        if rejected:
            n = len(rejected)
            self._warn(
                "Not a disk image" if n == 1 else "Not disk images",
                f"{'This file' if n == 1 else f'These {n} files'} can't be opened as {'a disk image' if n == 1 else 'disk images'}."
                + (" To add files to a volume, drop them on the volume in the list of disks." if dropped else ""),
                rejected,
            )
        return images

    def closeEvent(self, event) -> None:
        if not self._hfe_written():
            event.ignore()
            return
        QSettings().setValue("window/geometry", self.saveGeometry())
        for name, splitter in self._splitters().items():
            QSettings().setValue(f"window/{name}", splitter.saveState())
        self._update_runner.wait()
        self.preview.stop()
        if self._preview_dir is not None:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
        if self._drag_dir is not None:
            shutil.rmtree(self._drag_dir, ignore_errors=True)
        if self._engine is not None:
            self._engine.close()
        self._hfe.release()
        QThreadPool.globalInstance().waitForDone(3000)
        super().closeEvent(event)
