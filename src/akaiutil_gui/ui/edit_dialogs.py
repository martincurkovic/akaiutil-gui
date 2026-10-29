"""The questions asked before a disk image is changed: a new name, a new volume, "are you sure?"."""

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QLayout,
    QListWidget,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollBar,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

import dataclasses

from akaiutil_gui import akai_header, akai_names, backup, importing, settings, sorting
from akaiutil_gui.audio import preview
from akaiutil_gui.audio.preview import PreviewPlayer
from akaiutil_gui.audio.sample import PlaybackPlan
from akaiutil_gui.engine.models import Loop
from akaiutil_gui.audio.markers import note_name
from .marker_waveform import END, START, MarkerWaveform, loop_in, loop_out
from .volume_colors import volume_color
from akaiutil_gui.engine import formats
from akaiutil_gui.engine.models import Disk, Partition, Volume


class AkaiNameEdit(QLineEdit):
    """A line edit that only lets an Akai name be typed: capitals, the characters Akai has, and not too long.
    `rejected` says (in words) what was left out of what was just typed or pasted."""

    rejected = Signal(str)

    def __init__(self, text: str = "", volume_type: str = "S3000", parent=None):
        super().__init__(parent)
        self._volume_type = volume_type
        self.textEdited.connect(self._clean)
        self.set_volume_type(volume_type)
        if text:
            self.setText(akai_names.sanitize(text, volume_type)[0])

    def set_volume_type(self, volume_type: str) -> None:
        """S900 names are shorter and keep their case: a field changing from one type to another is cleaned again."""
        self._volume_type = volume_type  # (the limit is enforced in _clean, not with setMaxLength, so a cut can be reported)
        clean = akai_names.sanitize(self.text(), volume_type)[0]
        if clean != self.text():
            self.setText(clean)

    def _clean(self, text: str) -> None:
        clean, dropped, cut = akai_names.sanitize(text, self._volume_type)
        if clean != text:
            before_cursor = akai_names.sanitize(text[: self.cursorPosition()], self._volume_type)[0]
            self.setText(clean)
            self.setCursorPosition(min(len(before_cursor), len(clean)))
        notes = []
        if dropped:
            notes.append(f"{' '.join(f'“{c}”' for c in dropped)} can't be used in a name")
        if cut:
            notes.append(f"names are at most {akai_names.name_limit(self._volume_type)} characters")
        if notes:
            # said once the edit has finished: Qt reports the change (which a dialog answers by re-checking the name and
            # rewriting its message) after this handler returns
            message = "; ".join(notes).capitalize() + "."
            QTimer.singleShot(0, lambda: self.rejected.emit(message))


def _problem_label() -> QLabel:
    label = QLabel()
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    label.setMinimumHeight(label.fontMetrics().lineSpacing() * 2)  # a message appearing mustn't resize the dialog
    return label


class RenameDialog(QDialog):
    """Ask for a new name for a volume or a file. A file's type (".S3") isn't editable: it is shown, not typed."""

    def __init__(self, parent, what: str, current: str, volume_type: str, taken: Iterable[str] = (), suffix: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Rename {what}")
        self.setMinimumWidth(380)
        self._volume_type = volume_type
        self._taken = [t for t in taken]
        self._current = current

        self.edit = AkaiNameEdit(current, volume_type)
        self.edit.selectAll()
        self.edit.textChanged.connect(self._validate)
        self.edit.rejected.connect(lambda note: self.problem.setText(note))
        row = QHBoxLayout()
        row.addWidget(self.edit, 1)
        if suffix:
            row.addWidget(QLabel(suffix))
        self.problem = _problem_label()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"New name for the {what}:"))
        layout.addLayout(row)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self._validate()

    @property
    def name(self) -> str:
        """The name as it will be stored (capitals, trimmed)."""
        return akai_names.check_name(self.edit.text(), self._volume_type)[0]

    def _validate(self) -> None:
        name, problem = akai_names.check_name(self.edit.text(), self._volume_type)
        if problem is None and any(akai_names.same_name(name, t) for t in self._taken):
            problem = f"There is already one called “{name}”."
        unchanged = problem is None and name == self._current
        self.problem.setText(problem or "")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(problem is None and not unchanged)


class NewVolumeDialog(QDialog):
    """Name, number, type and load number for a new volume."""

    def __init__(self, parent, partition_name: str, used: Iterable[int], limit: int, types: list[str], default_type: str, taken: Iterable[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("New Volume")
        self.setMinimumWidth(380)
        self._used = set(used)
        self._taken = [t for t in taken]
        free = [n for n in range(1, limit + 1) if n not in self._used]

        self.name_edit = AkaiNameEdit("", default_type)
        self.name_edit.setPlaceholderText(f"up to {akai_names.name_limit(default_type)} characters")
        self.name_edit.textChanged.connect(self._validate)
        self.number = QSpinBox()
        self.number.setRange(1, limit)
        self.number.setValue(free[0] if free else 1)
        self.number.valueChanged.connect(self._validate)
        self.type_combo = QComboBox()
        for t in types:
            self.type_combo.addItem(t)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findText(default_type)))
        self.type_combo.setEnabled(len(types) > 1)
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        self.type_combo.currentIndexChanged.connect(self._validate)
        self.load = QSpinBox()
        self.load.setRange(0, 128)
        self.load.setSpecialValueText("Off")
        self.load.setToolTip("The number the sampler's front panel uses to load this volume (1 to 128), or Off")
        self.problem = _problem_label()
        self.name_edit.rejected.connect(lambda note: self.problem.setText(note))

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Volume number", self.number)
        form.addRow("Type", self.type_combo)
        form.addRow("Load number", self.load)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Create a volume in partition {partition_name}:"))
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self._validate()

    @property
    def volume_type(self) -> str:
        return self.type_combo.currentText()

    def _type_changed(self, _index: int) -> None:
        self.name_edit.set_volume_type(self.volume_type)
        self.name_edit.setPlaceholderText(f"up to {akai_names.name_limit(self.volume_type)} characters")

    @property
    def name(self) -> str:
        return akai_names.check_name(self.name_edit.text(), self.volume_type)[0]

    @property
    def volume_number(self) -> int:
        return self.number.value()

    @property
    def load_number(self) -> int | None:
        return self.load.value() or None

    def _validate(self) -> None:
        name, problem = akai_names.check_name(self.name_edit.text(), self.volume_type)
        if not self.name_edit.text().strip():
            problem = None  # nothing typed yet: no need to nag, the button just stays off
            ok = False
        else:
            ok = problem is None
        if problem is None and ok:
            if self.number.value() in self._used:
                problem, ok = f"Volume number {self.number.value()} is already used.", False
            elif any(akai_names.same_name(name, t) for t in self._taken):
                problem, ok = f"There is already a volume called “{name}”.", False
        self.problem.setText(problem or "")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)


class SampleSettingsDialog(QDialog):
    """A sample's root key, tuning, play mode, markers and loops. Save is only offered when the settings can be written."""

    PLAY_MODE_NAMES = {0: "Normal looping", 1: "Loop until release", 2: "No looping", 3: "Play to sample end"}  # (as in the details pane)

    def __init__(self, parent, name: str, current: akai_header.SampleSettings, audio=None):
        super().__init__(parent)
        self.setWindowTitle("Sample Settings")
        self.setMinimumWidth(560)
        self._current = current
        self._syncing = False
        self._selected_loop: int | None = None
        last = max(current.frames - 1, 0)
        seconds = current.frames / current.rate if current.rate else 0
        summary = QLabel(f"{name}  ·  {current.rate} Hz  ·  {current.frames} frames ({seconds:.2f} s)")
        warning = QLabel("This changes the sample inside the disk image itself. There is no undo.")
        warning.setObjectName("mutedLabel")
        warning.setWordWrap(True)

        def spin(low, high, value, marker=False):
            box = QSpinBox()
            box.setRange(low, high)
            box.setValue(min(max(value, low), high))
            box.valueChanged.connect(self._edited if marker else self._validate)
            return box

        self.wave = MarkerWaveform()
        self.wave.set_audio(audio, current.frames)
        self.zoom_out_button, self.zoom_in_button, self.fit_button = QPushButton("−"), QPushButton("+"), QPushButton("Fit")
        for button, tip in ((self.zoom_out_button, "Zoom out"), (self.zoom_in_button, "Zoom in"), (self.fit_button, "Show the whole sample")):
            button.setToolTip(tip)
            button.setAutoDefault(False)
        self.zoom_out_button.clicked.connect(lambda: self.wave.zoom_out())
        self.zoom_in_button.clicked.connect(lambda: self.wave.zoom_in())
        self.fit_button.clicked.connect(self.wave.reset_zoom)
        self.view_scroll = QScrollBar(Qt.Orientation.Horizontal)
        policy = self.view_scroll.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # (its room is kept while the waveform isn't zoomed, so zooming doesn't move the dialog)
        self.view_scroll.setSizePolicy(policy)
        self.view_scroll.setVisible(False)
        self.view_scroll.valueChanged.connect(self.wave.set_view_start)
        self.wave.viewChanged.connect(self._view_changed)
        self.wave.markersChanged.connect(self._from_waveform)
        self.wave.loopGrabbed.connect(self._select_loop)
        self.player = PreviewPlayer(self)
        self.player.position_changed.connect(self.wave.set_playhead)
        self.player.finished.connect(self._preview_finished)
        self.wave.previewRequested.connect(self._toggle_preview)
        self._playing_loop: int | None = None
        hint = QLabel("Click the waveform to play it (click again to stop). Drag a marker to move it (hold Shift for fine control). Ctrl/Cmd + scroll zooms, Shift + scroll pans.")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(hint, 1)
        for button in (self.zoom_out_button, self.zoom_in_button, self.fit_button):
            zoom_row.addWidget(button)

        self.root = spin(0, 127, current.root)
        self.root_name = QLabel()
        self.root.valueChanged.connect(lambda v: self.root_name.setText(note_name(v)))
        self.root_name.setText(note_name(self.root.value()))
        root_row = QHBoxLayout()
        root_row.addWidget(self.root)
        root_row.addWidget(self.root_name)
        root_row.addStretch(1)
        self.semitone = spin(min(-akai_header.MAX_TUNE, current.semitone), max(akai_header.MAX_TUNE, current.semitone), current.semitone)
        self.cents = spin(min(-akai_header.MAX_TUNE, current.cents), max(akai_header.MAX_TUNE, current.cents), current.cents)
        self.cents.setSuffix(" cents")
        self.semitone.setSuffix(" semitones")
        self.play_mode = QComboBox()
        for mode, label in self.PLAY_MODE_NAMES.items():
            self.play_mode.addItem(label, mode)
        self.play_mode.setCurrentIndex(max(0, self.play_mode.findData(current.play_mode)))
        self.play_mode.currentIndexChanged.connect(self._validate)
        self.start = spin(0, last, current.start, marker=True)
        self.end = spin(0, last, current.end, marker=True)
        form = QGridLayout()  # two columns of label and field, to keep the dialog short
        for row, (left, right) in enumerate(
            (
                (("Root key", root_row), ("Tuning", self.semitone)),
                (("Fine tuning", self.cents), ("Play mode", self.play_mode)),
                (("Start marker", self.start), ("End marker", self.end)),
            )
        ):
            for col, (label, widget) in enumerate((left, right)):
                form.addWidget(QLabel(label), row, col * 2)
                if isinstance(widget, QWidget):
                    form.addWidget(widget, row, col * 2 + 1)
                else:
                    form.addLayout(widget, row, col * 2 + 1)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        grid = QGridLayout()
        for col, title in enumerate(("Loop", "On", "Start", "End", "Time (ms)", "Hold")):
            head = QLabel(title)
            head.setObjectName("mutedLabel")
            grid.addWidget(head, 0, col)
        self.loop_rows = []
        self.loop_labels: list[QLabel] = []
        for i, loop in enumerate(current.loops):
            on = QCheckBox()
            on.setChecked(loop.enabled)
            start = spin(0, last, loop.start if loop.enabled else current.start, marker=True)
            end = spin(0, last, loop.end if loop.enabled else current.end, marker=True)
            hold = QCheckBox()
            hold.setChecked(loop.hold or not loop.enabled)
            time = spin(1, akai_header.MAX_LOOP_TIME, loop.time if loop.enabled and not loop.hold else 500)
            for widget in (on, hold):
                widget.toggled.connect(self._loop_changed)
            self.loop_rows.append((on, start, end, time, hold))
            number = QLabel(str(i + 1))
            number.setStyleSheet(f"color: {volume_color(i).name()};")  # (the colour of this loop's markers on the waveform)
            self.loop_labels.append(number)
            grid.addWidget(number, i + 1, 0)
            for col, widget in enumerate((on, start, end, time, hold), 1):
                grid.addWidget(widget, i + 1, col)
        self._loop_changed()

        self.problem = _problem_label()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(warning)
        layout.addWidget(self.wave)
        layout.addWidget(self.view_scroll)
        layout.addLayout(zoom_row)
        layout.addLayout(form)
        layout.addWidget(QLabel("Loops"))
        layout.addLayout(grid)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self._push_markers()
        self._validate()

    # --------------------------------------------------- the waveform and the numbers
    def _marker_values(self) -> dict[str, int]:
        values = {START: self.start.value(), END: self.end.value()}
        for i, (on, start, end, _time, _hold) in enumerate(self.loop_rows):
            if on.isChecked():
                values[loop_in(i)], values[loop_out(i)] = start.value(), end.value()
        return values

    def _push_markers(self) -> None:
        self.wave.set_markers(self._marker_values())

    def _edited(self) -> None:
        """A marker's number was typed or stepped: the waveform follows."""
        if not self._syncing and hasattr(self, "wave"):
            self._push_markers()
        self._validate()
        if hasattr(self, "player"):
            self._update_playing_loop()

    def _from_waveform(self, markers: dict) -> None:
        """A marker was dragged (and maybe pushed another): the numbers follow."""
        self._syncing = True
        try:
            self.start.setValue(markers.get(START, self.start.value()))
            self.end.setValue(markers.get(END, self.end.value()))
            for i, (_on, start, end, _time, _hold) in enumerate(self.loop_rows):
                if loop_in(i) in markers:
                    start.setValue(markers[loop_in(i)])
                    end.setValue(markers[loop_out(i)])
        finally:
            self._syncing = False
        self._validate()
        self._update_playing_loop()

    # ------------------------------------------------------------------- preview
    def _preview_plan(self) -> tuple[PlaybackPlan, int | None] | None:
        """How the sample plays with the settings as they are now (and which loop that is, 0-7, if it loops)."""
        audio = self.wave._samples
        if audio is None:
            return None
        s = self.result_settings
        last = len(audio) - 1
        start = min(max(0, s.start), last)
        end = min(max(start, s.end), last)
        on = [i for i, loop in enumerate(s.loops) if loop.enabled]
        if s.play_mode in (0, 1) and on:  # a looping play mode, with a loop that is on
            slot = self._selected_loop if self._selected_loop in on else on[0]
            loop = s.loops[slot]
            loop_end = min(max(start, loop.end), end)
            loop_start = min(max(start, loop.start), loop_end)
            if loop_end > loop_start:
                dwell = None if loop.hold else loop.time
                shown = Loop(slot + 1, loop_end, float(loop_end - loop_start), dwell)
                return PlaybackPlan(start, end, shown, loop_start, loop_end, dwell, 0), slot
        return PlaybackPlan(start, end), None

    def _toggle_preview(self) -> None:
        if self.player.is_playing():
            self.player.stop()
            return
        planned = self._preview_plan()
        if planned is None or preview.audio_disabled():
            return
        plan, slot = planned
        rate = self._current.rate or 44100
        audio = self.wave._samples
        if plan.looping:
            self.player.play_loop(audio, plan.start, plan.loop_start, plan.loop_end, plan.end, rate, dwell_ms=plan.dwell_ms, loop_tune_cents=0)
        else:
            self.player.play(audio, plan.start, plan.end, rate)
        if self.player.is_playing():
            self._playing_loop = slot

    def _preview_finished(self) -> None:
        self._playing_loop = None
        self.wave.set_playhead(None)

    def _update_playing_loop(self) -> None:
        """A loop's markers changed while it plays: the loop follows (at the start of its next pass), so edits can be heard."""
        if self.player.is_playing() and self._playing_loop is not None:
            planned = self._preview_plan()
            if planned is not None and planned[1] == self._playing_loop:
                self.player.update_loop_points(planned[0].loop_start, planned[0].loop_end)

    def done(self, result) -> None:
        self.player.stop()  # (nothing keeps playing after the dialog is gone)
        super().done(result)

    def _select_loop(self, loop: int) -> None:
        self._selected_loop = loop
        for i, label in enumerate(self.loop_labels):
            font = label.font()
            font.setBold(i == loop)
            label.setFont(font)
        self.wave.set_selected_loop(loop)

    def _view_changed(self, start: int, length: int, frames: int) -> None:
        zoomed = frames > length
        self.view_scroll.setVisible(zoomed)
        self.view_scroll.blockSignals(True)
        self.view_scroll.setRange(0, max(0, frames - length))
        self.view_scroll.setPageStep(length)
        self.view_scroll.setValue(start)
        self.view_scroll.blockSignals(False)

    def _loop_changed(self) -> None:
        for on, start, end, time, hold in self.loop_rows:
            for widget in (start, end, hold):
                widget.setEnabled(on.isChecked())
            time.setEnabled(on.isChecked() and not hold.isChecked())
        if hasattr(self, "wave"):
            self._push_markers()
        self._validate()

    @property
    def result_settings(self) -> akai_header.SampleSettings:
        """The settings as they are in the dialog now (a loop that was left alone comes back exactly as it was)."""
        loops = []
        for was, (on, start, end, time, hold) in zip(self._current.loops, self.loop_rows):
            if not on.isChecked():
                loops.append(dataclasses.replace(was, time=0))
            else:
                loops.append(dataclasses.replace(was, start=start.value(), end=end.value(), time=akai_header.LOOP_HOLD if hold.isChecked() else time.value()))
        return dataclasses.replace(
            self._current,
            root=self.root.value(),
            semitone=self.semitone.value(),
            cents=self.cents.value(),
            play_mode=self.play_mode.currentData(),
            start=self.start.value(),
            end=self.end.value(),
            loops=tuple(loops),
        )

    def _validate(self) -> None:
        if not hasattr(self, "save_button"):
            return  # (still being built)
        found = akai_header.problems(self.result_settings)
        self.problem.setText(found[0] if found else "")
        self.save_button.setEnabled(not found)


class RevertDialog(QDialog):
    """Which backup to put back."""

    def __init__(self, parent, entries):
        super().__init__(parent)
        self.setWindowTitle("Revert to Backup")
        self.setMinimumWidth(460)
        self._entries = entries
        self.list = QListWidget()
        for image, b in entries:
            self.list.addItem(f"{image.name}   ·   {b.created:%d %b %Y, %H:%M}   ·   {b.size / 2**20:.0f} MB")
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        note = QLabel(f"A backup is made when you switch editing on. The newest {backup.KEEP} of each image are kept.")
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.revert_button = buttons.addButton("Revert…", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Put back one of these backups:"))
        layout.addWidget(self.list)
        layout.addWidget(note)
        layout.addWidget(buttons)

    @property
    def chosen(self):
        return self._entries[self.list.currentRow()]


@dataclass(frozen=True)
class CopyTarget:
    """A partition a volume could be copied into, and the names its volumes already have."""

    partition: Partition
    volume_names: tuple[str, ...]

    @property
    def label(self) -> str:
        p = self.partition
        return f"disk{p.disk}  ·  partition {p.name}  ·  {p.free_mb:.1f} MB free"

    @property
    def free_bytes(self) -> int:
        return self.partition.free_blocks * self.partition.block_size


class CopyVolumeDialog(QDialog):
    """Where to copy a volume to and what to call the copy."""

    def __init__(self, parent, volume: Volume, files: int, need_bytes: int, targets: list[CopyTarget]):
        super().__init__(parent)
        self.setWindowTitle("Copy Volume")
        self.setMinimumWidth(420)
        self._volume = volume
        self._need = need_bytes
        self._targets = targets
        self._suggested = ""

        self.where = QComboBox()
        for t in targets:
            self.where.addItem(t.label)
        elsewhere = next((i for i, t in enumerate(targets) if (t.partition.disk, t.partition.name) != (volume.disk, volume.partition)), 0)
        self.where.setCurrentIndex(elsewhere)
        self.name_edit = AkaiNameEdit("", volume.type)
        self.problem = _problem_label()
        self.name_edit.rejected.connect(lambda note: self.problem.setText(note))
        self.where.currentIndexChanged.connect(self._target_changed)
        self.name_edit.textChanged.connect(self._validate)

        form = QFormLayout()
        form.addRow("Copy to", self.where)
        form.addRow("Name", self.name_edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        what = f"“{volume.name}”: {files} file{'s' if files != 1 else ''}, about {need_bytes / 2**20:.1f} MB."
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(what))
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self._target_changed()

    @property
    def target(self) -> CopyTarget:
        return self._targets[self.where.currentIndex()]

    @property
    def name(self) -> str:
        return akai_names.check_name(self.name_edit.text(), self._volume.type)[0]

    def _target_changed(self) -> None:
        """Suggest a name that is free there, unless the user has typed their own."""
        if self.name_edit.text() in ("", self._suggested):
            self._suggested = akai_names.unique_name(self._volume.name, self.target.volume_names, self._volume.type)
            self.name_edit.setText(self._suggested)
        self._validate()

    def _validate(self) -> None:
        name, problem = akai_names.check_name(self.name_edit.text(), self._volume.type)
        ok = problem is None and bool(self.name_edit.text().strip())
        if not self.name_edit.text().strip():
            problem = None
        elif ok and any(akai_names.same_name(name, t) for t in self.target.volume_names):
            problem, ok = f"There is already a volume called “{name}” there.", False
        if ok and self._need > self.target.free_bytes:
            problem, ok = f"That partition only has {self.target.free_bytes / 2**20:.1f} MB free.", False
        self.problem.setText(problem or "")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)


class CopyPartitionDialog(QDialog):
    """Which partition to copy a whole partition into."""

    def __init__(self, parent, source: Partition, volume_names: tuple[str, ...], files: int, need_bytes: int, targets: list[CopyTarget]):
        super().__init__(parent)
        self.setWindowTitle("Copy Partition")
        self.setMinimumWidth(420)
        self._names = volume_names
        self._need = need_bytes
        self._targets = targets
        self.where = QComboBox()
        for t in targets:
            self.where.addItem(t.label)
        self.where.currentIndexChanged.connect(self._validate)
        self.problem = _problem_label()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        n = len(volume_names)
        what = f"Partition {source.name}: {n} volume{'s' if n != 1 else ''}, {files} file{'s' if files != 1 else ''}, about {need_bytes / 2**20:.1f} MB."
        form = QFormLayout()
        form.addRow("Copy to", self.where)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(what))
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self._validate()

    @property
    def target(self) -> CopyTarget:
        return self._targets[self.where.currentIndex()]

    def _validate(self) -> None:
        problem = None
        clashes = [n for n in self._names if any(akai_names.same_name(n, t) for t in self.target.volume_names)]
        if clashes:
            problem = f"That partition already has {'a volume' if len(clashes) == 1 else 'volumes'} called “{clashes[0]}”" + (f" and {len(clashes) - 1} more." if len(clashes) > 1 else ".")
        elif self._need > self.target.free_bytes:
            problem = f"That partition only has {self.target.free_bytes / 2**20:.1f} MB free."
        self.problem.setText(problem or "")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(problem is None)


class DiskFormatDialog(QDialog):
    """What kind of disk, how big, and how it is cut into partitions: for a new image (disk=None) or to
    reformat one that is open (then its size is whatever the image already is)."""

    def __init__(self, parent, disk: Disk | None = None):
        super().__init__(parent)
        self._disk = disk
        self.setWindowTitle("New Disk Image" if disk is None else f"Format disk{disk.index}")
        self.setMinimumWidth(400)

        self.type_combo = QComboBox()
        for fmt in formats.FORMATS if disk is None else formats.formats_for_disk(disk.type):
            self.type_combo.addItem(fmt.label, fmt.key)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, formats.HD_MAX_MB)
        self.size_spin.setValue(128)
        self.size_spin.setSuffix(" MB")
        self.partition_spin = QSpinBox()
        self.partition_spin.setRange(formats.PARTITION_MIN_MB, formats.PARTITION_MAX_MB)
        self.partition_spin.setValue(formats.PARTITION_MAX_MB)
        self.partition_spin.setSuffix(" MB")
        self.partition_spin.setToolTip("How big each sampler partition is (60 MB is the most the samplers allow)")
        self.summary = _problem_label()
        self.summary.setObjectName("mutedLabel")
        for widget in (self.size_spin, self.partition_spin):
            widget.valueChanged.connect(self._refresh)
        self.type_combo.currentIndexChanged.connect(self._refresh)

        form = QFormLayout()
        form.addRow("Type", self.type_combo)
        self.size_row = (QLabel("Size"), self.size_spin)
        form.addRow(*self.size_row)
        self.partition_row = (QLabel("Partition size"), self.partition_spin)
        form.addRow(*self.partition_row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create…" if disk is None else "Format…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.summary)
        layout.addWidget(self.buttons)
        self._refresh()

    @property
    def format(self) -> formats.DiskFormat:
        return formats.by_key(self.type_combo.currentData())

    @property
    def size_mb(self) -> int:
        return self.size_spin.value()

    @property
    def partition_mb(self) -> int:
        return self.partition_spin.value()

    def _refresh(self) -> None:
        fmt = self.format
        hard = not fmt.is_floppy
        own_size = hard and self._disk is None  # an existing image can't be made bigger or smaller
        for widget in self.size_row:
            widget.setVisible(own_size)
        for widget in self.partition_row:
            widget.setVisible(hard and fmt.partitions)
        self.size_spin.setMaximum(fmt.max_mb)
        total = self.size_mb if own_size else (self._disk.total_mb if self._disk is not None else 0)
        if fmt.is_floppy:
            text = f"A {fmt.floppy_kb / 1000:g} MB floppy image."
        elif not fmt.partitions:
            text = f"One partition of {min(total, fmt.max_mb):g} MB."
        else:
            count, covered = formats.partition_layout(total, self.partition_mb)
            text = f"{count} partition{'s' if count != 1 else ''} of up to {self.partition_mb} MB, {covered:g} MB in all"
            if total > formats.HD_MAX_MB:
                text += f" (the samplers can't use more than {formats.HD_MAX_MB} MB)"
            elif covered < total - 0.5:
                text += f", leaving {total - covered:g} MB unused (at most {formats.MAX_PARTITIONS} partitions)"
            text += "."
        self.summary.setText(text)


def fit_wrapped_text(dialog: QDialog) -> None:
    """Make a dialog at least as tall as its layout needs at its minimum width. A layout holding word-wrapped text reports
    a minimum height for one line of it, so a dialog shown at that height squeezes its widgets together (a list sliding under
    the row below it); asking the layout for the minimum height it needs at the width the dialog really has fixes that."""
    layout = dialog.layout()
    width = max(dialog.minimumWidth(), dialog.sizeHint().width())
    needed = layout.minimumHeightForWidth(width)
    if needed > 0:
        dialog.setMinimumHeight(needed)
        dialog.resize(max(dialog.width(), width), max(dialog.minimumSizeHint().height(), needed))


class ImportDialog(QDialog):
    """What to make of the audio being imported: which sampler's kind of sample, and what to do with stereo. Lists every file
    in the order it will be added, with what will be done to it."""

    def __init__(self, parent, volume, sources: list[importing.Source]):
        super().__init__(parent)
        self._volume = volume
        self._sources = sources
        self.setWindowTitle("Import")
        self.setMinimumWidth(620)
        audio = [s for s in sources if s.kind == "audio" and not s.problem]

        self.type_combo = QComboBox()
        for t in importing.allowed_types(volume.type):
            self.type_combo.addItem(t)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findText(importing.default_type(volume.type))))
        self.type_combo.setEnabled(self.type_combo.count() > 1)
        self.compress = QCheckBox("Compress (.S9C)")
        self.compress.setToolTip("The S900's compressed sample format: about a third of the space, for a little quality")
        self.stereo_combo = QComboBox()
        self.stereo_combo.addItem("Split into a left and a right sample", importing.STEREO_SPLIT)
        self.stereo_combo.addItem("Mix to mono", importing.STEREO_MIX)
        if settings.get_import_stereo_mix():
            self.stereo_combo.setCurrentIndex(1)
        flags = settings.get_import_flags()
        self.file_markers = QCheckBox("Use the loop points and root note stored in the files")
        self.file_markers.setToolTip("A WAV from a DAW or sample editor can carry them (its “smpl” chunk); an AIFF can carry the root note")
        self.root_from_name = QCheckBox("Take the root note from a name ending in a note, like Piano_C3")
        self.root_from_name.setToolTip("Only if the file doesn't say. Middle C is C3, as on the samplers")
        self.trim = QCheckBox("Trim silence from the start and end")
        self.trim.setToolTip("Anything quieter than -60 dB. Never cuts into a loop")
        self.normalize = QCheckBox("Normalise to -1 dB")
        self.normalize.setToolTip("Make the loudest sample -1 dBFS")
        for box, name in ((self.file_markers, "file_markers"), (self.root_from_name, "root_from_name"), (self.trim, "trim"), (self.normalize, "normalize")):
            box.setChecked(flags[name])
            box.toggled.connect(lambda _on: self._refresh())
        self.add_anyway = QCheckBox("Add anyway: import the skipped files as they are")
        self.add_anyway.setToolTip(
            "For a file the app can't check, like an operating system image (.C or .C3) or a text file, and for Akai files made for "
            "another sampler than the volume's (an S3000 sample in an S1000 volume). It goes in exactly as it is, with no conversion "
            "and no checks: as the type its extension says, or, if that isn't an Akai type, with .X01 added to its name. "
            "Not remembered: it has to be ticked each time"
        )
        self.add_anyway.toggled.connect(lambda _on: self._refresh())
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.files.setMinimumHeight(min(300, max(140, 40 * len(sources))))  # (a row can wrap onto two lines; room for a few)
        self.files.setWordWrap(True)  # a long description wraps instead of scrolling sideways
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.note = QLabel("Files are added in this order. A name that is already in the volume gets a number, so nothing is replaced.")
        self.note.setObjectName("mutedLabel")
        self.note.setWordWrap(True)

        form = QFormLayout()
        self.type_row = (QLabel("Sample type"), self._row(self.type_combo, self.compress))
        self.stereo_row = (QLabel("Stereo files"), self.stereo_combo)
        form.addRow(*self.type_row)
        form.addRow(*self.stereo_row)
        self.option_boxes = (self.file_markers, self.root_from_name, self.trim, self.normalize)
        for box in self.option_boxes:
            form.addRow(box)
        form.addRow(self.add_anyway)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        n = len(sources)
        layout.addWidget(QLabel(f"Import {n} file{'s' if n != 1 else ''} into the {volume.type} volume “{volume.name}”:"))
        layout.addWidget(self.files)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addWidget(self.buttons)

        for widget in self.type_row:
            widget.setVisible(bool(audio))
        for box in self.option_boxes:
            box.setVisible(bool(audio))
        self.add_anyway.setVisible(any(importing.can_add_anyway(s, volume.type) for s in sources))  # (only when there is something to add anyway)
        self.stereo_row[0].setVisible(any(s.channels == 2 for s in audio))
        self.stereo_row[1].setVisible(any(s.channels == 2 for s in audio))
        self.type_combo.currentIndexChanged.connect(self._refresh)
        self.stereo_combo.currentIndexChanged.connect(self._refresh)
        self._refresh()
        fit_wrapped_text(self)

    @staticmethod
    def _row(*widgets):
        from PySide6.QtWidgets import QWidget

        holder = QWidget()
        holder.setObjectName("transparentContainer")  # (a plain QWidget would otherwise paint the window colour behind the combo box)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        for w in widgets:
            row.addWidget(w)
        row.addStretch(1)
        return holder

    @property
    def sample_type(self) -> str:
        return self.type_combo.currentText()

    @property
    def compress_s900(self) -> bool:
        return self.sample_type == "S900" and self.compress.isChecked()

    @property
    def stereo(self) -> str:
        return self.stereo_combo.currentData()

    @property
    def options(self) -> importing.ImportOptions:
        return importing.ImportOptions(
            file_markers=self.file_markers.isChecked(),
            root_from_name=self.root_from_name.isChecked(),
            trim=self.trim.isChecked(),
            normalize=self.normalize.isChecked(),
            add_anyway=self.add_anyway.isChecked(),
        )

    def accept(self) -> None:
        """Remember the choices for next time."""
        settings.set_import_flags(
            {"file_markers": self.file_markers.isChecked(), "root_from_name": self.root_from_name.isChecked(), "trim": self.trim.isChecked(), "normalize": self.normalize.isChecked()}
        )
        settings.set_import_stereo_mix(self.stereo == importing.STEREO_MIX)
        super().accept()

    @property
    def importable(self) -> int:
        options = self.options
        return sum(1 for s in self._sources if not importing.import_problem(s, self._volume.type, options))

    def _refresh(self) -> None:
        self.compress.setVisible(self.sample_type == "S900" and self.type_row[1].isVisible())
        self.files.clear()
        for s in self._sources:
            self.files.addItem(f"{s.path.name}   —   {importing.describe(s, self.sample_type, self.stereo, self.options, self._volume.type)}")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.importable > 0)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")


class RepairDialog(QDialog):
    """Save a repaired copy of a damaged HFE disk: one optional extra, with what it costs said next to it."""

    TEXT_WIDTH = 400

    def __init__(self, parent, name: str):
        super().__init__(parent)
        self.setWindowTitle("Save Repaired Copy")
        self.rewrite_box = QCheckBox("Also rewrite sectors that can't be fixed")
        note = self._text(
            "They will load without errors, but may hold wrong data: a click in a sample, or a file that won't work. "
            "Leave this off if you're not sure.",
            muted=True,
            indent=True,
        )
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save Copy…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._text(f"Fix the damaged sectors of “{name}” in a new copy. The original isn't changed."))
        layout.addWidget(self.rewrite_box)
        layout.addWidget(note)
        layout.addWidget(self.buttons)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def _text(self, text: str, indent: bool = False, muted: bool = False) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        if muted:
            label.setObjectName("mutedLabel")
        label.setContentsMargins(24 if indent else 0, 0, 0, 0)
        label.setFixedWidth(self.TEXT_WIDTH)
        return label

    @property
    def rewrite(self) -> bool:
        return self.rewrite_box.isChecked()


class SortDialog(QDialog):
    """Which files of a volume to put in alphabetical order. Fixed size: the text wraps to a set width instead."""

    TEXT_WIDTH = 380

    def __init__(self, parent, volume, files: list):
        super().__init__(parent)
        self.setWindowTitle("Sort Files by Name")
        samples = sum(1 for f in files if f.is_sample)
        others = len(files) - samples
        self.everything = QRadioButton(f"All {len(files)} files")
        self.only_samples = QRadioButton(f"Only the {samples} sample{'s' if samples != 1 else ''}")
        (self.only_samples if others else self.everything).setChecked(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Sort")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._text(f"Put the files of “{volume.name}” in alphabetical order (numbers in order, ignoring case)."))
        if others:  # nothing to choose between when every file is a sample
            layout.addWidget(self.only_samples)
            layout.addWidget(self._text("Leave the programs and other files where they are.", indent=True, muted=True))
            layout.addWidget(self.everything)
            layout.addWidget(
                self._text(
                    "A program's number (what a MIDI program change picks) is its place among the volume's programs, so "
                    "sorting everything can change which program a number selects. Nothing else about a file changes.",
                    indent=True,
                    muted=True,
                )
            )
        layout.addWidget(self.buttons)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def _text(self, text: str, indent: bool = False, muted: bool = False) -> QLabel:
        """A label that wraps to TEXT_WIDTH (a fixed-size dialog can't grow to fit a long line)."""
        label = QLabel(text)
        label.setWordWrap(True)
        if muted:
            label.setObjectName("mutedLabel")
        left = 24 if indent else 0
        label.setContentsMargins(left, 0, 0, 0)
        label.setFixedWidth(self.TEXT_WIDTH)  # (the layout works out the height a wrapped label needs at that width)
        return label

    @property
    def scope(self) -> str:
        return sorting.SAMPLES if self.only_samples.isChecked() else sorting.ALL


OTHER = "other"  # what confirm() answers when the second action was chosen


def confirm(parent, title: str, text: str, detail: str = "", action: str = "Delete", details_lines: Iterable[str] = (),
            other_action: str | None = None, default_is_action: bool = False):
    """A question whose default answer is "no"; the destructive button is named for what it does.

    Returns True for `action`, False for Cancel, and (only if `other_action` was given) OTHER for that one. With
    `default_is_action` the first button is the default instead of Cancel: for a question where the safe choice is to go ahead."""
    if other_action:
        return ThreeWayDialog(parent, title, text, detail, action, other_action, list(details_lines)).ask()
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    if detail:
        box.setInformativeText(detail)
    lines = list(details_lines)
    if lines:
        box.setDetailedText("\n".join(lines))
    go = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole if not default_is_action else QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(go if default_is_action else cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is go


class ThreeWayDialog(QDialog):
    """A question with two ways forward and a way out, laid out the same on every platform (a QMessageBox puts its buttons
    where the platform's rules say, and on a Mac that isn't an order anyone would choose):

        [Show Details…]                  [Cancel] [other action] [action]

    The details (a list of what it is about) stay hidden until asked for. The last button is the default."""

    WIDTH = 540

    def __init__(self, parent, title: str, text: str, detail: str, action: str, other_action: str, lines: list[str]):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(self.WIDTH)
        self._result: bool | str = False

        icon = QLabel()
        icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        heading = QLabel(text)
        heading.setWordWrap(True)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        body = QLabel(detail)
        body.setWordWrap(True)
        body.setVisible(bool(detail))
        words = QVBoxLayout()
        words.addWidget(heading)
        words.addWidget(body)
        words.addStretch(1)
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(icon)
        top.addLayout(words, 1)

        self.details = QPlainTextEdit("\n".join(lines))
        self.details.setReadOnly(True)
        self.details.setFixedHeight(min(120, 22 * max(1, len(lines)) + 16))
        self.details.hide()
        self.details_button = QPushButton("Show Details…")
        self.details_button.setVisible(bool(lines))
        self.details_button.setAutoDefault(False)
        self.details_button.clicked.connect(self._toggle_details)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.other_button = QPushButton(other_action)
        self.other_button.setAutoDefault(False)
        self.other_button.clicked.connect(lambda: self._finish(OTHER))
        self.go_button = QPushButton(action)
        self.go_button.setDefault(True)
        self.go_button.clicked.connect(lambda: self._finish(True))
        buttons = QHBoxLayout()
        buttons.addWidget(self.details_button)
        buttons.addStretch(1)
        for b in (self.cancel_button, self.other_button, self.go_button):
            buttons.addWidget(b)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addWidget(self.details)
        layout.addLayout(buttons)
        self.go_button.setFocus()
        self._fit_height()

    def _fit_height(self) -> None:
        """Make the dialog as tall as its layout needs at its fixed width. Word-wrapped text reports the height of one line
        to the dialog, which would otherwise be shown too short, with the text cut off."""
        layout = self.layout()
        layout.activate()
        self.setFixedHeight(max(self.sizeHint().height(), layout.heightForWidth(self.WIDTH)))

    def _toggle_details(self) -> None:
        show = self.details.isHidden()
        self.details.setVisible(show)
        self.details_button.setText("Hide Details" if show else "Show Details…")
        self._fit_height()

    def _finish(self, result) -> None:
        self._result = result
        self.accept()

    def ask(self) -> bool | str:
        """True for the main action, OTHER for the other, False for Cancel (or closing the window)."""
        self.exec()
        return self._result if self.result() == QDialog.DialogCode.Accepted else False
