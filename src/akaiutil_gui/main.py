"""Entry point: `akaiutil-gui [image ...]` or `python -m akaiutil_gui`."""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from akaiutil_gui import __version__, paths
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.locate import bundled_binary, find_akaiutil
from akaiutil_gui.ui import notices_dialog, theme
from akaiutil_gui.ui.main_window import MainWindow


def smoke_test(report_path: str | None = None) -> int:
    """Check that a built app has everything it needs, then exit (0 = fine, 1 = not).

    CI runs this against each freshly built app, because a build can succeed and still be missing
    a data file or the bundled akaiutil. It also exercises the real thing end to end: it starts the
    bundled akaiutil on a scratch image and reads its disk list.
    """
    lines: list[str] = []
    failures = 0

    def check(name: str, fn) -> None:
        nonlocal failures
        try:
            lines.append(f"ok    {name}: {fn()}")
        except Exception as e:  # report every problem, not just the first
            failures += 1
            lines.append(f"FAIL  {name}: {e.__class__.__name__}: {e}")

    def stylesheets() -> str:
        for palette in (theme.DARK_PALETTE, theme.LIGHT_PALETTE):
            theme.render_stylesheet(palette)  # reads style.qss.template and the icons
        return "dark and light render"

    def svg_images() -> str:
        # the stylesheet draws checkmarks and combo-box arrows from SVG files; if a build left out Qt's SVG
        # support they would just silently not appear
        from PySide6.QtGui import QPixmap

        icon = Path(theme._ICONS_DIR) / "checkmark.svg"
        assert icon.is_file(), f"missing {icon}"
        assert not QPixmap(str(icon)).isNull(), "Qt can't load SVG images (is its svg plugin bundled?)"
        return "SVG images load"

    def notices() -> str:
        text = notices_dialog.notices_text()
        assert "akaiutil" in text and "missing from this build" not in text
        return f"{len(text)} characters"

    def the_akaiutil() -> Path:
        """The akaiutil this app will actually use. In a built app that must be the bundled one: finding
        some other copy on the machine would hide a broken bundle (and fail on a machine without one)."""
        if paths.is_frozen():
            binary = bundled_binary()
            assert binary is not None and binary.is_file(), f"no akaiutil is bundled in this app (looked at {binary})"
            assert os.access(binary, os.X_OK), f"the bundled akaiutil isn't executable: {binary}"
            return binary
        binary = find_akaiutil()
        assert binary is not None, "no akaiutil binary found"
        return binary

    def bundled_akaiutil() -> str:
        binary = the_akaiutil()
        out = subprocess.run([str(binary), "-h"], capture_output=True, text=True, timeout=30)
        assert "usage" in (out.stdout + out.stderr).lower(), "akaiutil -h printed no usage text"
        return f"{binary}{' (bundled)' if paths.is_frozen() else ''}"

    def talks_to_akaiutil() -> str:
        binary = the_akaiutil()
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "blank.img"
            image.write_bytes(bytes(1024 * 800))  # an unformatted floppy-sized image
            engine = AkaiEngine(binary)
            engine.open([image])
            try:
                disks = engine.list_disks()
            finally:
                engine.close()
        assert len(disks) == 1, f"expected 1 disk, got {len(disks)}"
        return f"read {disks[0].path} ({disks[0].type})"

    def window() -> str:
        w = MainWindow()
        w.show()
        QApplication.processEvents()
        w.close()
        return "main window opens"

    lines.append(f"akaiutil-gui {__version__}, Python {sys.version.split()[0]}")
    lines.append(f"app folder: {paths.app_dir()}  (executable: {sys.executable}, argv[0]: {sys.argv[0]})")
    check("stylesheets", stylesheets)
    check("svg images", svg_images)
    check("notices", notices)
    check("bundled akaiutil", bundled_akaiutil)
    check("akaiutil round trip", talks_to_akaiutil)
    check("main window", window)
    lines.append("SMOKE TEST " + ("FAILED" if failures else "PASSED"))
    report = "\n".join(lines)
    print(report)
    if report_path:  # a built Windows app has no console, so CI reads the report from a file
        Path(report_path).write_text(report + "\n", encoding="utf-8")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setOrganizationName("akaiutil-gui")
    app.setApplicationName("akaiutil-gui")
    theme.apply_to_app(app)

    smoke = next((a for a in argv[1:] if a == "--smoke-test" or a.startswith("--smoke-test=")), None)
    if smoke is not None:
        return smoke_test(smoke.partition("=")[2] or None)

    window = MainWindow()
    window.show()
    images = [a for a in argv[1:] if not a.startswith("-")]
    if images:
        window.open_images(images)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
