"""The "a new version exists" notice. A port of AKAISDS's ui/update_dialog.py.

It deliberately doesn't download or install anything: it points the user at the GitHub release page. The
builds aren't code-signed, so a downloaded file needs the user's own Gatekeeper/SmartScreen click-through anyway.
"""

import shutil
import subprocess
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from akaiutil_gui import __version__, settings

from . import theme

APP_NAME = "akaiutil-GUI"


def open_url(url: str) -> None:
    """Open a web page. QDesktopServices.openUrl() can report success on Linux while doing nothing (seen on a
    Hyprland/Wayland session), so on Linux shell out to xdg-open first, which was confirmed reliable there."""
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
        except OSError:
            pass  # fall through to Qt
    QDesktopServices.openUrl(QUrl(url))


def _recolor_links(document, color: QColor, underline: bool = False) -> None:
    """Give every link run in the document one colour: Markdown anchors ignore QPalette.Link."""
    cursor = QTextCursor(document)
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.charFormat().isAnchor():
                cursor.setPosition(fragment.position())
                cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setForeground(color)
                if underline:
                    fmt.setFontUnderline(True)
                cursor.mergeCharFormat(fmt)
            it += 1
        block = block.next()


def restyle_code_runs(document) -> None:
    """In a theme with a font of its own (System 6), give inline code the same font and size as the text around it: Markdown marks a code
    span "monospace" at the document's own point size, which is the system's fixed font and bigger than the theme's text."""
    palette = theme.current_palette()
    family, size = palette.get("font_family"), palette.get("font_size_dense")
    if not family or not size:
        return
    fmt = QTextCharFormat()
    fmt.setFontFamilies([name.strip().strip('"') for name in family.split(",")])
    fmt.setFontFixedPitch(False)
    fmt.setProperty(QTextFormat.Property.FontPixelSize, int(size.removesuffix("px")))
    cursor = QTextCursor(document)
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.charFormat().fontFixedPitch():
                cursor.setPosition(fragment.position())
                cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(fmt)
            it += 1
        block = block.next()


def apply_theme_to_markdown(document) -> None:
    """What a theme needs done to a Markdown document once it is loaded: its link colour (System 6: Qt draws links blue whatever the
    palette says, and a black link is told from the text by its underline) and its font for inline code."""
    link = theme.current_palette().get("link")
    if link:
        _recolor_links(document, QColor(link), underline=True)
    restyle_code_runs(document)


class UpdateAvailableDialog(QDialog):
    def __init__(self, update_info, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.setWindowTitle("Update Available")
        layout = QVBoxLayout(self)

        heading = QLabel(f"{APP_NAME} {update_info.version} is available")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        current = QLabel(f"You have version {__version__} installed.")
        current.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(current)

        # also shown as selectable text, so a click that silently does nothing still leaves a URL to copy
        link = QLabel(f'<a href="{update_info.html_url}">{update_info.html_url}</a>')
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        link.linkActivated.connect(open_url)
        layout.addWidget(link)

        # the release notes, rendered from the Markdown they were written in. Links open in the system browser, only
        # web ones: release notes are text from the network, not something that may launch a file:// or custom URL.
        self.notes_view = None
        if update_info.notes.strip():
            self.notes_view = QTextBrowser()
            self.notes_view.setOpenLinks(False)
            self.notes_view.setOpenExternalLinks(False)
            self.notes_view.anchorClicked.connect(self._open_notes_link)
            self.notes_view.setMarkdown(update_info.notes)
            _recolor_links(self.notes_view.document(), QColor(theme.current_palette()["accent"]))
            restyle_code_runs(self.notes_view.document())
            self.notes_view.setMinimumHeight(180)
            layout.addWidget(self.notes_view, stretch=1)

        row = QHBoxLayout()
        skip = QPushButton("Skip This Version")
        skip.clicked.connect(self._skip_version)
        later = QPushButton("Remind Me Later")
        later.clicked.connect(self.reject)
        view = QPushButton("View Release…")
        view.setDefault(True)
        view.clicked.connect(self._open_release_page)
        for b in (skip, later, view):
            row.addWidget(b)
        layout.addLayout(row)
        self.setMinimumWidth(520)
        if self.notes_view is not None:
            self.setMinimumHeight(360)
            self.resize(560, 460)

    def _open_notes_link(self, url) -> None:
        if url.scheme() in ("http", "https"):
            open_url(url.toString())

    def _open_release_page(self) -> None:
        open_url(self.update_info.html_url)
        self.accept()

    def _skip_version(self) -> None:
        settings.set_skipped_update_version(self.update_info.version)
        self.reject()
