"""Help > About. Laid out like AKAISDS's: the ASCII logo, version, a line about the app, credits, and buttons for
checking for updates and reading the licences. Fixed size, so the text labels wrap to a set width."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLayout, QProgressBar, QPushButton, QVBoxLayout

from akaiutil_gui import __version__, updates

from .ascii_logo import LOGO
from .details_view import monospace_family
from .notices_dialog import NoticesDialog
from . import theme
from .update_helper import UpdateCheckRunner

# the dialog is fixed-size, so its width follows the widest label: the text labels wrap to this
_TEXT_MAX_WIDTH = 440


def link_palette(widget) -> QPalette:
    """The widget's palette with links in the theme's accent colour (the default blue is unreadable on the dark theme)."""
    palette = QPalette(widget.palette())
    palette.setColor(QPalette.ColorRole.Link, QColor(theme.current_palette()["accent"]))
    return palette


def _wrapped(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setFixedWidth(_TEXT_MAX_WIDTH)
    label.setFixedHeight(label.heightForWidth(_TEXT_MAX_WIDTH))
    return label


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About akaiutil-GUI")
        layout = QVBoxLayout(self)

        self.logo_label = QLabel(LOGO.strip("\n"))
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # (a stylesheet beats setFont, and the app's gives every label a pixel size and no fixed-width family)
        self.logo_label.setStyleSheet(f"font-family: '{monospace_family()}'; font-size: 10pt;")
        layout.addWidget(self.logo_label)

        version_label = QLabel(f"Version {__version__}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        layout.addWidget(
            _wrapped("A cross-platform Qt front end for akaiutil: browse, preview, edit and export Akai S900, S1000 and S3000 disk images."),
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )

        url = f"https://github.com/{updates.REPO}" if updates.REPO else ""
        if url:
            link_label = QLabel(f'<a href="{url}">{url}</a>')
            link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            link_label.setOpenExternalLinks(True)
            link_label.setPalette(link_palette(link_label))
            layout.addWidget(link_label)

        layout.addWidget(
            _wrapped(
                "Does its work by running akaiutil by Klaus Michael Indlekofer (GPL v2 or later).\n"
                "Built with PySide6 (Qt), miniaudio, soundfile, NumPy and Nuitka.\n"
                "Licensed under GPL-2.0-or-later."
            ),
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )

        self._update_runner = UpdateCheckRunner(self)
        row = QHBoxLayout()
        self.check_updates_button = QPushButton("Check for Updates")
        self.check_updates_button.clicked.connect(self._check_for_updates)
        self.licences_button = QPushButton("Licences")
        self.licences_button.clicked.connect(lambda: NoticesDialog(self).exec())
        row.addStretch()
        row.addWidget(self.check_updates_button)
        row.addWidget(self.licences_button)
        row.addStretch()
        layout.addLayout(row)

        # indeterminate: a version check has nothing measurable to show, only "still waiting on the network"
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 0)
        self.update_progress.setTextVisible(False)
        self.update_progress.setFixedHeight(6)
        self.update_progress.setVisible(False)
        layout.addWidget(self.update_progress)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def _check_for_updates(self) -> None:
        self.check_updates_button.setEnabled(False)
        self.update_progress.setVisible(True)
        self._update_runner.start(manual=True, on_finished=self._on_update_check_finished)

    def _on_update_check_finished(self) -> None:
        self.update_progress.setVisible(False)
        self.check_updates_button.setEnabled(True)

    def done(self, result: int) -> None:
        # a check that is still running mustn't outlive this dialog's Python object (see UpdateCheckRunner.wait); done() is
        # the one method every way of closing it (OK, Escape, the title bar) goes through
        self._update_runner.wait()
        super().done(result)
