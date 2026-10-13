"""An S3000-series OS version is stored 15 above the number the sampler's panel shows (akaiutil prints "17.00" for OS 2.00); the app shows the panel's."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from akaiutil_gui.engine.models import FileEntry
from akaiutil_gui.formatting import panel_os_version


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # keep QSettings (the table remembers its columns) out of the real user prefs
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("qs")))
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("akaiutil-gui-test")
    app.setApplicationName("test")
    return app


@pytest.mark.parametrize(
    "raw, shown",
    [
        ("17.00", "2.00"),  # a real S2000 on OS 2.00 (the max S3000 OS version in akaiutil.h)
        ("16.30", "1.30"),  # ...and on OS 1.30
        ("17.05", "2.05"),
        ("4.40", "4.40"),  # S1000 (akaiutil.h's max "4.40") and S1100 ("9.30") already read as the panel does
        ("9.30", "9.30"),
        ("-", "-"),  # an S900 volume has none
        ("uncompr", "uncompr"),
        ("12345", "12345"),  # akaiutil prints a bare number when a byte is 100 or more
        ("", ""),
    ],
)
def test_the_panel_version_is_shown_for_s3000_series_values_only(raw, shown):
    assert panel_os_version(raw) == shown


def test_whitespace_around_the_value_is_ignored_for_matching():
    assert panel_os_version(" 17.00 ") == "2.00"


def test_the_file_table_shows_the_panel_version_and_the_engine_keeps_akaiutils(app):
    from akaiutil_gui.ui.file_table import COLUMNS, FileTable

    table = FileTable()
    try:
        file = FileEntry(1, "THING.P3", 100, "0x0001", "17.00")
        table.set_files([file])
        column = COLUMNS.index("OS ver")
        assert table.item(0, column).text() == "2.00"
        assert file.os_version == "17.00"  # what tag copies and change detection (and akaiutil's tar import) use
        table.set_files([FileEntry(2, "OLD.P1", 100, "0x0002", "-")])
        assert table.item(0, column).text() == "-"
    finally:
        table.deleteLater()


def test_a_file_with_no_version_shows_a_dash(app):
    from akaiutil_gui.ui.file_table import COLUMNS, FileTable

    table = FileTable()
    try:
        table.set_files([FileEntry(1, "X.S3", 1, "0x0001", "")])
        column = COLUMNS.index("OS ver")
        assert table.item(0, column).text() == "—"
    finally:
        table.deleteLater()
