"""The Details pane: renders a describe.Details as themed rich text, or akaiutil's raw output."""

from html import escape

from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QTextBrowser

from akaiutil_gui.describe import Block, Details

from . import theme

LABEL_WIDTH = 190  # px, so values line up down the page
LABEL_WIDTH_TWO_COLUMNS = 150  # (narrower, so a column has room for its values)
TWO_COLUMNS_MIN_WIDTH = 900  # px of pane: wide enough for two columns of labels and values side by side

_MONO_CANDIDATES = ["Menlo", "Monaco", "SF Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono", "Courier New"]


def monospace_family() -> str:
    """A real, installed fixed-width family name, for use inside HTML.

    QFontDatabase.systemFont(FixedFont).family() isn't usable for that: on macOS
    it is the generic alias "monospace", which a QFont object resolves but a
    family name in rich text does not (the text comes out proportional).
    """
    installed = set(QFontDatabase.families())
    for name in _MONO_CANDIDATES:
        if name in installed:
            m = QFontMetrics(QFont(name))
            if m.horizontalAdvance("i") == m.horizontalAdvance("W"):
                return name
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def _block_html(block: Block, p: dict, mono: str, label_width: int = LABEL_WIDTH) -> str:
    out = []
    if block.title:
        colour = p["text_disabled"] if block.muted else p["text_bright"]
        out.append(
            f'<div style="margin-top:16px; margin-bottom:3px; font-weight:600; color:{colour};">{escape(block.title)}</div>'
        )
    label_colour = p["text_disabled"]
    value_colour = p["text_disabled"] if block.muted else p["text"]
    if block.rows:
        out.append('<table cellspacing="0" cellpadding="2">')
        for row in block.rows:
            out.append(
                f'<tr><td width="{label_width}" style="color:{label_colour};">{escape(row.label)}</td>'
                f'<td style="color:{value_colour};">{escape(row.value)}</td></tr>'
            )
        out.append("</table>")
    if block.headers and block.table:
        out.append('<table cellspacing="0" cellpadding="4" style="margin-top:6px;">')
        out.append(
            "<tr>" + "".join(f'<td style="color:{label_colour}; font-weight:600;">{escape(h)}</td>' for h in block.headers) + "</tr>"
        )
        for i, row in enumerate(block.table):
            bg = f' bgcolor="{p["bg_zebra"]}"' if i % 2 else ""
            out.append(f"<tr{bg}>" + "".join(f'<td style="color:{value_colour};">{escape(c)}</td>' for c in row) + "</tr>")
        out.append("</table>")
    if block.text:
        out.append(
            f'<pre style="font-family:\'{mono}\'; color:{value_colour}; margin-top:2px;">{escape(block.text)}</pre>'
        )
    return "".join(out)


def _weight(block: Block) -> int:
    """About how tall a block is, in lines, to split the blocks into two columns of similar height."""
    return (2 if block.title else 0) + len(block.rows) + (len(block.table) + 2 if block.table else 0) + (block.text.count("\n") + 2 if block.text else 0)


def split_blocks(blocks: list[Block]) -> tuple[list[Block], list[Block]]:
    """The blocks, in order, cut into a left and a right column of as near the same height as the order allows."""
    if len(blocks) < 2:
        return list(blocks), []
    total = sum(_weight(b) for b in blocks)
    best, running, best_at = None, 0, 1
    for i in range(1, len(blocks)):
        running += _weight(blocks[i - 1])
        gap = abs(running - (total - running))
        if best is None or gap < best:
            best, best_at = gap, i
    return list(blocks[:best_at]), list(blocks[best_at:])


def render_html(details: Details, palette: dict, mono: str = "monospace", columns: int = 1) -> str:
    """`columns` 2 puts the blocks side by side (the title stays across the top). Blocks of preformatted text need the width,
    so a page with one stays a single column."""
    parts = [f'<div style="font-size:17px; font-weight:600; color:{palette["text_bright"]};">{escape(details.title)}</div>']
    if details.subtitle:
        parts.append(f'<div style="color:{palette["text_disabled"]}; margin-top:2px;">{escape(details.subtitle)}</div>')
    if columns >= 2 and len(details.blocks) >= 2 and not any(b.text for b in details.blocks):
        left, right = split_blocks(details.blocks)
        cell = lambda blocks: "".join(_block_html(b, palette, mono, LABEL_WIDTH_TWO_COLUMNS) for b in blocks)  # noqa: E731
        parts.append(
            '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td width="50%" valign="top">{cell(left)}</td>'
            f'<td width="50%" valign="top">{cell(right)}</td></tr></table>'
        )
    else:
        parts += [_block_html(b, palette, mono) for b in details.blocks]
    return "".join(parts)


class DetailsView(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setPlaceholderText("Select a disk, partition, volume or file to see its details.")
        self._mono = monospace_family()
        self._details: Details | None = None
        self._raw_mode = False
        self._columns = 1
        theme.notifier.changed.connect(self._render)  # the colours are baked into the HTML

    @property
    def columns(self) -> int:
        return self._columns

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        columns = 2 if self.viewport().width() >= TWO_COLUMNS_MIN_WIDTH else 1
        if columns != self._columns:  # a wide pane shows two columns of details, a narrow one a single column
            self._columns = columns
            scroll = self.verticalScrollBar().value()
            self._render()
            self.verticalScrollBar().setValue(scroll)

    def show_details(self, details: Details | None) -> None:
        self._details = details
        self._render()

    def set_raw_mode(self, on: bool) -> None:
        """Show exactly what akaiutil printed instead of the readable version."""
        self._raw_mode = on
        self._render()

    def _render(self) -> None:
        d = self._details
        if d is None:
            self.clear()
        elif self._raw_mode and d.raw:
            self.setHtml(f'<pre style="font-family:\'{self._mono}\'; margin:0;">{escape(d.raw)}</pre>')
        else:
            self.setHtml(render_html(d, theme.current_palette(), self._mono, self._columns))
        self.verticalScrollBar().setValue(0)
