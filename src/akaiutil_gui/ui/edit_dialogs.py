"""The questions asked before a disk image is changed: a new name, a new volume, "are you sure?"."""

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
    QSpinBox,
    QStyle,
    QVBoxLayout,
)

from akaiutil_gui import akai_names, backup, importing, settings, sorting
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
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.files.setMinimumHeight(140)
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
        self.stereo_row[0].setVisible(any(s.channels == 2 for s in audio))
        self.stereo_row[1].setVisible(any(s.channels == 2 for s in audio))
        self.type_combo.currentIndexChanged.connect(self._refresh)
        self.stereo_combo.currentIndexChanged.connect(self._refresh)
        self._refresh()

    @staticmethod
    def _row(*widgets):
        from PySide6.QtWidgets import QWidget

        holder = QWidget()
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
        return sum(1 for s in self._sources if not s.problem)

    def _refresh(self) -> None:
        self.compress.setVisible(self.sample_type == "S900" and self.type_row[1].isVisible())
        self.files.clear()
        for s in self._sources:
            self.files.addItem(f"{s.path.name}   —   {importing.describe(s, self.sample_type, self.stereo, self.options)}")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.importable > 0)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")


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

    def _toggle_details(self) -> None:
        show = self.details.isHidden()
        self.details.setVisible(show)
        self.details_button.setText("Hide Details" if show else "Show Details…")
        self.adjustSize()

    def _finish(self, result) -> None:
        self._result = result
        self.accept()

    def ask(self) -> bool | str:
        """True for the main action, OTHER for the other, False for Cancel (or closing the window)."""
        self.exec()
        return self._result if self.result() == QDialog.DialogCode.Accepted else False
