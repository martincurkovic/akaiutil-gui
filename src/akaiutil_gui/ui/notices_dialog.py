"""Help > Third-party notices: the licence notices that ship inside the app."""

from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from .update_dialog import recolor_theme_links

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
        self.setMinimumSize(480, 320)
        self.resize(720, 560)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        from .about_dialog import link_palette  # (here: the About dialog imports this module)

        self.view.setPalette(link_palette(self.view))
        self.view.setMarkdown(notices_text())
        recolor_theme_links(self.view.document())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        layout.addWidget(buttons)
