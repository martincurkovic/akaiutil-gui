"""The questions asked before a disk image is changed: a new name, a new volume, "are you sure?"."""

from collections.abc import Iterable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from akaiutil_gui import akai_names
from akaiutil_gui.engine import formats
from akaiutil_gui.engine.models import Disk


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


def confirm(parent, title: str, text: str, detail: str = "", action: str = "Delete", details_lines: Iterable[str] = ()) -> bool:
    """A question whose default answer is "no"; the destructive button is named for what it does."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    if detail:
        box.setInformativeText(detail)
    lines = list(details_lines)
    if lines:
        box.setDetailedText("\n".join(lines))
    go = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is go
