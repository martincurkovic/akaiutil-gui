"""Help > Third-party notices: the licence notices that ship inside the app."""

from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

NOTICES_PATH = Path(__file__).parent / "help" / "THIRD_PARTY_NOTICES.md"


def notices_text() -> str:
    try:
        return NOTICES_PATH.read_text(encoding="utf-8")
    except OSError:
        return "The notices file is missing from this build.\n\nSee https://github.com/ for the project's licences."


class NoticesDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Third-party notices")
        self.resize(720, 560)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setMarkdown(notices_text())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        layout.addWidget(buttons)
