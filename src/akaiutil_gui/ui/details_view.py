"""The Details pane: renders a describe.Details as themed rich text, or akaiutil's raw output."""

from html import escape

from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QTextBrowser

from akaiutil_gui.describe import Block, Details

from . import theme

LABEL_WIDTH = 190  # px, so values line up down the page

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


def _block_html(block: Block, p: dict, mono: str) -> str:
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
                f'<tr><td width="{LABEL_WIDTH}" style="color:{label_colour};">{escape(row.label)}</td>'
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


def render_html(details: Details, palette: dict, mono: str = "monospace") -> str:
    parts = [f'<div style="font-size:17px; font-weight:600; color:{palette["text_bright"]};">{escape(details.title)}</div>']
    if details.subtitle:
        parts.append(f'<div style="color:{palette["text_disabled"]}; margin-top:2px;">{escape(details.subtitle)}</div>')
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
        theme.notifier.changed.connect(self._render)  # the colours are baked into the HTML

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
            self.setHtml(render_html(d, theme.current_palette(), self._mono))
        self.verticalScrollBar().setValue(0)
