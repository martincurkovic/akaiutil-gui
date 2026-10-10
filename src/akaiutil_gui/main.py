"""Entry point: `akaiutil-gui [image ...]` or `python -m akaiutil_gui`."""

import logging
import sys

from PySide6.QtWidgets import QApplication

from akaiutil_gui.ui import theme
from akaiutil_gui.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setOrganizationName("akaiutil-gui")
    app.setApplicationName("akaiutil-gui")
    theme.apply_to_app(app)

    window = MainWindow()
    window.show()
    images = [a for a in argv[1:] if not a.startswith("-")]
    if images:
        window.open_images(images)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
