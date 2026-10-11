"""Settings: appearance, audio preview, export, akaiutil and updates. Laid out like AKAISDS's settings dialog
(cards in one scrolling page); the theme applies the moment it's picked, everything else on OK."""

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from akaiutil_gui import logsetup, settings
from akaiutil_gui.audio import preview
from akaiutil_gui.engine.locate import find_akaiutil

from . import reveal, theme

# frames, the same options and unit a DAW's buffer-size dropdown uses; converted to milliseconds when a device is
# opened (that depends on the sample rate in use)
BUFFER_SIZE_OPTIONS = [32, 64, 128, 256, 512, 1024, 2048]

AUDIO_BUFFER_TIP = (
    "How much audio is buffered ahead during preview playback.\n"
    "Lower values react faster but may click or pop on a slower\n"
    "output device or interface; higher values are more reliable\n"
    "but add latency before playback audibly starts."
)


def section_card(title: str, body: QFormLayout) -> QWidget:
    """A titled card, in the same style as AKAISDS's settings sections."""
    card = QWidget()
    card.setObjectName("sectionCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(10)
    header = QLabel(title)
    header.setObjectName("sectionHeader")
    layout.addWidget(header)
    layout.addLayout(body)
    return card


def note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    return label


class SettingsDialog(QDialog):
    themeChosen = Signal(str)  # applied immediately, not on OK

    def __init__(self, parent=None, update_runner=None, locate: Callable[[], object] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._runner = update_runner
        self._locate = locate

        # ------------------------------------------------------------ appearance
        form = QFormLayout()
        self.combo_theme = QComboBox()
        for label, value in theme.CHOICES:
            self.combo_theme.addItem(label, value)
        self.combo_theme.setCurrentIndex(max(0, self.combo_theme.findData(settings.get_theme())))
        # `activated`, not currentIndexChanged: it fires only for a choice the USER makes, never for the line above
        self.combo_theme.activated.connect(lambda _i: self._on_theme_chosen())
        form.addRow("Theme:", self.combo_theme)
        appearance = section_card("Appearance", form)

        # ---------------------------------------------------------- audio preview
        form = QFormLayout()
        form.addRow(note("Used when you preview a sample. Doesn't affect exporting."))
        self.combo_audio_output = QComboBox()
        self.combo_audio_output.addItem("(System Default)", None)
        for device in preview.list_output_devices():
            self.combo_audio_output.addItem(device["name"], preview.device_id_string(device))
        # Device names can be very long (on Linux especially). Size to a fixed width instead of the longest name, or the page
        # outgrows the dialog and gets a horizontal scroll bar; a long name is elided, and the tooltip shows it in full.
        self.combo_audio_output.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_audio_output.setMinimumContentsLength(24)
        self.combo_audio_output.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for i in range(self.combo_audio_output.count()):
            self.combo_audio_output.setItemData(i, self.combo_audio_output.itemText(i), Qt.ItemDataRole.ToolTipRole)
        self.combo_audio_output.setCurrentIndex(max(0, self.combo_audio_output.findData(settings.get_audio_output_device())))
        form.addRow("Output Device:", self.combo_audio_output)

        self.combo_audio_buffer = QComboBox()
        for frames in BUFFER_SIZE_OPTIONS:
            self.combo_audio_buffer.addItem(f"{frames} samples", frames)
        saved = settings.get_audio_buffer_samples()
        if self.combo_audio_buffer.findData(saved) < 0:
            self.combo_audio_buffer.addItem(f"{saved} samples", saved)
        self.combo_audio_buffer.setCurrentIndex(self.combo_audio_buffer.findData(saved))
        self.combo_audio_buffer.setToolTip(AUDIO_BUFFER_TIP)
        form.addRow("Buffer Size:", self.combo_audio_buffer)

        self.check_auto_play = QCheckBox("Play a sample as soon as it's selected")
        self.check_auto_play.setChecked(settings.get_auto_play())
        form.addRow(self.check_auto_play)
        self.check_preview_loops = QCheckBox("Play samples that have loops as a loop")
        self.check_preview_loops.setToolTip(
            "On: the attack plays once, then the loop repeats (until you stop it, if its loop time is Hold).\n"
            "Off: every sample plays once from its start to its end."
        )
        self.check_preview_loops.setChecked(settings.get_preview_loops())
        form.addRow(self.check_preview_loops)
        audio = section_card("Audio Preview", form)

        # ----------------------------------------------------------------- export
        form = QFormLayout()
        row = QHBoxLayout()
        self.edit_export_folder = QLineEdit(settings.get_default_export_folder() or "")
        self.edit_export_folder.setPlaceholderText("(the folder you used last)")
        self.edit_export_folder.setReadOnly(True)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_export_folder)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.edit_export_folder.setText(""))
        for w, stretch in ((self.edit_export_folder, 1), (browse, 0), (clear, 0)):
            row.addWidget(w, stretch)
        form.addRow("Default Folder:", row)
        self.check_ask_folder = QCheckBox("Ask where to export every time")
        self.check_ask_folder.setToolTip("Off: exports go straight to the default folder, without a prompt.")
        self.check_ask_folder.setChecked(settings.get_ask_export_folder())
        form.addRow(self.check_ask_folder)
        export = section_card("Export", form)

        # ---------------------------------------------------------------- editing
        form = QFormLayout()
        self.check_sort_after = QCheckBox("Sort a volume by name after importing or pasting files into it")
        self.check_sort_after.setToolTip(
            "Files are otherwise listed on the sampler in the order they were added.\n"
            "Programs and other files stay where they are; only the samples are put in order."
        )
        self.check_sort_after.setChecked(settings.get_sort_after_adding())
        form.addRow(self.check_sort_after)
        editing = section_card("Editing", form)

        # ---------------------------------------------------------------- backups
        form = QFormLayout()
        self.spin_keep_days = QSpinBox()
        self.spin_keep_days.setRange(0, 365)
        self.spin_keep_days.setSpecialValueText("Never")
        self.spin_keep_days.setSuffix(" days")
        self.spin_keep_days.setValue(settings.get_backup_keep_days())
        self.spin_keep_days.setToolTip("Backups made when editing is switched on are deleted this long after they were made, when the app starts")
        form.addRow("Delete backups older than", self.spin_keep_days)
        form.addRow(note("A backup of each image is offered when editing is switched on, and kept in the app's data folder. "
                         "File > Revert to Backup puts one back. Never keeps them until you delete them yourself."))
        backups = section_card("Backups", form)

        # ---------------------------------------------------------- troubleshooting
        form = QFormLayout()
        self.check_verbose_log = QCheckBox("Log every command sent to akaiutil")
        self.check_verbose_log.setToolTip("Makes the log bigger, but shows exactly what the app asked akaiutil to do. Turn it on to repeat a problem you want to report.")
        self.check_verbose_log.setChecked(settings.get_verbose_log())
        form.addRow(self.check_verbose_log)
        row = QHBoxLayout()
        show_log = QPushButton("Show Log File")
        show_log.setToolTip("Open the folder with the log, to attach it to a bug report")
        show_log.clicked.connect(lambda: reveal.show_log_file())
        row.addWidget(show_log)
        row.addStretch(1)
        form.addRow(row)
        form.addRow(note("The log lists what the app did and every error. It contains file paths and Akai file names, but no audio."))
        troubleshooting = section_card("Troubleshooting", form)

        # --------------------------------------------------------------- akaiutil
        form = QFormLayout()
        self.label_akaiutil = QLabel()
        self.label_akaiutil.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label_akaiutil.setWordWrap(True)
        form.addRow("In use:", self.label_akaiutil)
        row = QHBoxLayout()
        locate_btn = QPushButton("Locate…")
        locate_btn.setToolTip("Point the app at a different akaiutil program")
        locate_btn.clicked.connect(self._locate_akaiutil)
        reset_btn = QPushButton("Use Default")
        reset_btn.setToolTip("Forget a chosen path and use the copy that came with the app")
        reset_btn.clicked.connect(self._reset_akaiutil)
        row.addWidget(locate_btn)
        row.addWidget(reset_btn)
        row.addStretch(1)
        form.addRow(row)
        self._refresh_akaiutil_label()
        akaiutil = section_card("akaiutil", form)

        # ---------------------------------------------------------------- updates
        form = QFormLayout()
        self.check_updates = QCheckBox("Check for updates when the app starts")
        self.check_updates.setChecked(settings.get_check_updates_on_startup())
        form.addRow(self.check_updates)
        row = QHBoxLayout()
        self.button_check_now = QPushButton("Check Now")
        self.button_check_now.clicked.connect(self._check_now)
        self.label_last_check = QLabel(self._last_check_text())
        self.label_last_check.setObjectName("mutedLabel")
        row.addWidget(self.button_check_now)
        row.addWidget(self.label_last_check, 1)
        form.addRow(row)
        updates_card = section_card("Updates", form)

        # --------------------------------------------------------------- the page
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setSpacing(12)
        for card in (appearance, audio, export, editing, backups, akaiutil, updates_card, troubleshooting):
            page_layout.addWidget(card)
        page_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        self.setMinimumSize(540, 400)
        self.resize(560, 640)

    # -------------------------------------------------------------------- actions
    def _on_theme_chosen(self) -> None:
        self.themeChosen.emit(self.combo_theme.currentData())

    def _choose_export_folder(self) -> None:
        start = self.edit_export_folder.text() or settings.get_last_dir("export") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Default export folder", start)
        if folder:
            self.edit_export_folder.setText(folder)

    def _refresh_akaiutil_label(self) -> None:
        found = find_akaiutil(settings.get_akaiutil_path())
        self.label_akaiutil.setText(str(found) if found else "Not found")

    def _locate_akaiutil(self) -> None:
        if self._locate is not None:
            self._locate()
        self._refresh_akaiutil_label()

    def _reset_akaiutil(self) -> None:
        settings.set_akaiutil_path(None)
        self._refresh_akaiutil_label()

    def _last_check_text(self) -> str:
        last = settings.get_last_update_check()
        return "Never checked" if not last else "Last checked " + time.strftime("%d %b %Y, %H:%M", time.localtime(last))

    def _check_now(self) -> None:
        if self._runner is None:
            return
        self.button_check_now.setEnabled(False)
        self._runner.start(manual=True, on_finished=self._check_finished)

    def _check_finished(self) -> None:
        self.button_check_now.setEnabled(True)
        self.label_last_check.setText(self._last_check_text())

    # --------------------------------------------------------------------- saving
    def accept(self) -> None:
        settings.set_audio_output_device(self.combo_audio_output.currentData())
        settings.set_audio_buffer_samples(self.combo_audio_buffer.currentData())
        settings.set_auto_play(self.check_auto_play.isChecked())
        settings.set_preview_loops(self.check_preview_loops.isChecked())
        settings.set_default_export_folder(self.edit_export_folder.text() or None)
        settings.set_ask_export_folder(self.check_ask_folder.isChecked())
        settings.set_check_updates_on_startup(self.check_updates.isChecked())
        settings.set_sort_after_adding(self.check_sort_after.isChecked())
        settings.set_backup_keep_days(self.spin_keep_days.value())
        settings.set_verbose_log(self.check_verbose_log.isChecked())
        logsetup.set_verbose(self.check_verbose_log.isChecked())
        super().accept()
