"""Help > User Manual: renders help/manual.md (the one copy of the manual).

A port of AKAISDS's Quick Start dialog: Find in page (Ctrl/Cmd+F), `[links](#headings)` that jump within the page, and
screenshots that shrink to fit the window. It is not modal, so it can stay open next to the window it explains. The help
folder is bundled into built apps with --include-data-dir (see scripts/build_*).
"""

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPixmap, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QTextEdit, QVBoxLayout, QWidget

from .about_dialog import link_palette

HELP_DIR = Path(__file__).parent / "help"
MANUAL_PATH = HELP_DIR / "manual.md"

# Screenshots ship at twice their on-screen size (captured at a scale factor of 2, so they stay sharp on a Retina display),
# and QTextDocument doesn't fit images to its viewport the way a browser does: so each image's width is set here, shrunk to fit
# a narrow window but never past what the screenshot measured on screen.
SCREENSHOT_PIXEL_RATIO = 2.0
_IMAGE_WIDTH_MARGIN = 24
_RESIZE_DEBOUNCE_MS = 60  # only re-measure once a live resize has paused this long

# Fixed highlight colours, as in a browser's find-in-page: a plain text selection is painted dim whenever the viewer lacks focus,
# and focus sits in the search box while searching. ExtraSelections paint regardless.
_MATCH_BACKGROUND = QColor("#f5c518")
_MATCH_FOREGROUND = QColor("#1a1a1a")
_CURRENT_MATCH_BACKGROUND = QColor("#ff8f00")
_CURRENT_MATCH_FOREGROUND = QColor("#1a1a1a")


def heading_slug(text: str) -> str:
    """GitHub's anchor for a heading (lower case, punctuation dropped, spaces to hyphens, runs of hyphens kept), so the same
    `#some-heading` links work on GitHub and here."""
    return re.sub(r"[^\w\s-]", "", text.lower()).replace(" ", "-")


class ManualDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Manual")
        self.resize(820, 700)
        self._matches: list[tuple[int, int]] = []
        self._current = -1
        self._natural_sizes: dict[str, tuple[float, float]] = {}  # image name -> on-screen size, read from disk once

        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)
        self.viewer.setPalette(link_palette(self.viewer))
        self.viewer.setSearchPaths([str(HELP_DIR)])  # the markdown's relative image paths (screenshots/...) resolve here
        self.loaded = False
        try:
            self.viewer.setMarkdown(MANUAL_PATH.read_text(encoding="utf-8"))
            self.loaded = True
            self._fit_images(self._target_width())
            self._add_heading_anchors()
        except OSError as e:
            self.viewer.setPlainText(f"Couldn't load the manual: {e}")

        self.search_bar = self._build_search_bar()
        self.search_bar.setVisible(False)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.viewer)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(lambda: self._fit_images(self._target_width()))
        self.viewer.viewport().installEventFilter(self)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self).activated.connect(self.open_search)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close_search)

    # -------------------------------------------------------------------- images
    def _target_width(self) -> int:
        return max(1, self.viewer.viewport().width() - _IMAGE_WIDTH_MARGIN)

    def _natural_size(self, name: str) -> tuple[float, float] | None:
        cached = self._natural_sizes.get(name)
        if cached is not None:
            return cached
        pixmap = QPixmap(str(HELP_DIR / name))
        if pixmap.isNull():
            return None
        size = (pixmap.width() / SCREENSHOT_PIXEL_RATIO, pixmap.height() / SCREENSHOT_PIXEL_RATIO)
        self._natural_sizes[name] = size
        return size

    def _fit_images(self, max_width: float) -> None:
        """Set every image's width to fit `max_width` (never above its own size). Safe to repeat: an image that was shrunk
        grows back when the window does."""
        document = self.viewer.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()  # one edit for all of them: a relayout per image made resizing visibly lag
        try:
            block = document.begin()
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    fragment = it.fragment()
                    fmt = fragment.charFormat()
                    if fragment.isValid() and fmt.isImageFormat():
                        image = fmt.toImageFormat()
                        natural = self._natural_size(image.name())
                        if natural is not None:
                            width = min(max_width, natural[0])
                            image.setWidth(width)
                            image.setHeight(natural[1] * width / natural[0])
                            cursor.setPosition(fragment.position())
                            cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                            cursor.setCharFormat(image)
                    it += 1
                block = block.next()
        finally:
            cursor.endEditBlock()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.viewer.viewport() and event.type() == QEvent.Type.Resize and self.loaded:
            self._resize_timer.start(_RESIZE_DEBOUNCE_MS)
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------- anchors
    def _add_heading_anchors(self) -> None:
        """Qt turns `[link](#heading)` into a link but never names the heading it points at; give each heading GitHub's anchor."""
        document = self.viewer.document()
        block = document.begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0 and block.text():
                cursor = QTextCursor(document)
                cursor.setPosition(block.position())
                cursor.setPosition(block.position() + len(block.text()), QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setAnchor(True)
                fmt.setAnchorNames([heading_slug(block.text())])
                cursor.mergeCharFormat(fmt)
            block = block.next()

    # -------------------------------------------------------------------- search
    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background: transparent;")  # otherwise the global QWidget rule makes it a box on top of the dialog
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in page…")
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.returnPressed.connect(self.find_next)
        self.results_label = QLabel("")
        self.results_label.setFixedWidth(70)
        previous, following, close = QPushButton("←"), QPushButton("→"), QPushButton("×")
        previous.setToolTip("Previous match")
        following.setToolTip("Next match")
        close.setToolTip("Close the search bar (Esc)")
        previous.clicked.connect(self.find_previous)
        following.clicked.connect(self.find_next)
        close.clicked.connect(self.close_search)
        for w in (self.search_input, self.results_label, previous, following, close):
            row.addWidget(w)
        return bar

    def open_search(self) -> None:
        self.search_bar.setVisible(True)
        self.search_input.setFocus()
        self.search_input.selectAll()
        if self.search_input.text():
            self._on_text_changed(self.search_input.text())

    def close_search(self) -> None:
        if not self.search_bar.isVisible():
            return
        self.search_bar.setVisible(False)
        self.viewer.setExtraSelections([])
        self.viewer.setFocus()

    def _on_text_changed(self, text: str) -> None:
        self._matches = self._find_all(text) if text else []
        self._current = 0 if self._matches else -1
        if self._matches:
            self._go_to(self._current)
        else:
            self.viewer.setExtraSelections([])
            self._update_label()

    def _find_all(self, text: str) -> list[tuple[int, int]]:
        found = []
        document = self.viewer.document()
        cursor = QTextCursor(document)
        while True:
            cursor = document.find(text, cursor)
            if cursor.isNull():
                return found
            found.append((cursor.selectionStart(), cursor.selectionEnd()))

    def _go_to(self, index: int) -> None:
        self._current = index
        cursor = self.viewer.textCursor()
        cursor.setPosition(self._matches[index][0])
        self.viewer.setTextCursor(cursor)
        self.viewer.ensureCursorVisible()
        self._highlight()
        self._update_label()

    def _highlight(self) -> None:
        selections = []
        for i, (start, end) in enumerate(self._matches):
            cursor = QTextCursor(self.viewer.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            current = i == self._current
            fmt.setBackground(_CURRENT_MATCH_BACKGROUND if current else _MATCH_BACKGROUND)
            fmt.setForeground(_CURRENT_MATCH_FOREGROUND if current else _MATCH_FOREGROUND)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self.viewer.setExtraSelections(selections)

    def find_next(self) -> None:
        if self._matches:
            self._go_to((self._current + 1) % len(self._matches))

    def find_previous(self) -> None:
        if self._matches:
            self._go_to((self._current - 1) % len(self._matches))

    def _update_label(self) -> None:
        if not self.search_input.text():
            self.results_label.setText("")
        elif not self._matches:
            self.results_label.setText("0 of 0")
        else:
            self.results_label.setText(f"{self._current + 1} of {len(self._matches)}")


def show_manual_dialog(parent) -> ManualDialog:
    """One manual window, reused: choosing Help > User Manual again raises the open one rather than stacking another."""
    dialog = getattr(parent, "_manual_dialog", None)
    if dialog is None:
        dialog = ManualDialog(parent)
        parent._manual_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
