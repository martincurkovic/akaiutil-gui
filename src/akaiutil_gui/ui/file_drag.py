"""What is carried when files are dragged out of the file list.

Inside the app a drag is just "these files" (so dropping them on a volume copies or moves them with their data and name
intact). To another application the same drag is a list of real files, which are only created if that application asks for them
(a drop in Finder or Explorer): samples as WAV, or exactly as Akai stores them when Alt/Option is held at the start of the drag.
"""

from collections.abc import Callable

from PySide6.QtCore import QMimeData, QUrl

INTERNAL_MIME = "application/x-akaiutil-gui-files"
URI_LIST = "text/uri-list"


class FileDragData(QMimeData):
    def __init__(self, rows: list, exporter: Callable[[], list[str]]):
        super().__init__()
        self.rows = rows  # [(Volume, FileEntry)]
        self._exporter = exporter
        self._paths: list[str] | None = None
        self.setData(INTERNAL_MIME, b"1")

    def formats(self) -> list[str]:
        return [INTERNAL_MIME, URI_LIST]

    def hasFormat(self, mime_type: str) -> bool:
        return mime_type in (INTERNAL_MIME, URI_LIST)

    def exported_paths(self) -> list[str]:
        """The files, written out now if that hasn't happened yet."""
        if self._paths is None:
            self._paths = self._exporter()
        return self._paths

    def retrieveData(self, mime_type: str, preferred_type):
        if mime_type == URI_LIST:
            return [QUrl.fromLocalFile(p) for p in self.exported_paths()]
        return super().retrieveData(mime_type, preferred_type)
