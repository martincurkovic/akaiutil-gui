"""The engine the UI talks to: typed listings, info text and exports.

Built on Session (a live akaiutil process). Every public method takes the
engine lock for its whole duration, because a logical operation such as
"list the files of volume 3 in partition B" is several commands that must not
interleave with another thread's commands (akaiutil keeps the current
directory inside the process).

The method names and return types here are the interface a future
ctypes/cffi engine would implement; nothing outside this package should know
there is a subprocess underneath.
"""

import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from . import parse
from .models import Disk, DiskUsage, ExportResult, FileEntry, Partition, Volume
from .session import EngineError, Session, TrafficCallback


class AkaiEngine:
    def __init__(self, binary: str | os.PathLike, on_traffic: TrafficCallback | None = None):
        self._binary = binary
        self._on_traffic = on_traffic
        self._session: Session | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle
    @property
    def is_open(self) -> bool:
        return self._session is not None and self._session.alive

    def open(self, images: list[str | os.PathLike]) -> str:
        """(Re)start akaiutil on the given disk images, read-only."""
        with self._lock:
            self.close()
            session = Session(self._binary, images, self._on_traffic)
            try:
                banner = session.start()
            except EngineError:
                session.close()
                raise
            self._session = session
            return banner

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None

    # ------------------------------------------------------------- listings
    def list_disks(self) -> list[Disk]:
        with self._lock:
            return parse.parse_disks(self._run("dir /").text)

    def list_partitions(self, disk: Disk) -> list[Partition]:
        with self._lock:
            return parse.parse_partitions(self._run(f"dir {disk.path}").text, disk.index, disk.block_size)

    def list_volumes(self, part: Partition) -> list[Volume]:
        with self._lock:
            text = self._run(f"dir {part.path}").text
            return parse.parse_volumes(text, part.disk, part.name)

    def list_files(self, vol: Volume) -> list[FileEntry]:
        with self._lock:
            self._enter_volume(vol)
            return parse.parse_files(self._run("dir").text)

    def disk_usage(self, disk: Disk) -> DiskUsage:
        """Partitions plus the space each volume uses, from a single recursive listing."""
        with self._lock:
            self._run(f"cd {disk.path}")
            text = self._run("dirrec", timeout=300).text  # every file on the disk; can be long
            partitions, volumes = parse.parse_dirrec(text, disk.index, disk.block_size)
            return DiskUsage(disk, partitions, volumes)

    def list_takes_text(self, part: Partition) -> str:
        """DD (direct-to-disk) partitions list takes, not files; the proof of
        concept just shows akaiutil's own text for them."""
        with self._lock:
            return self._run(f"dir {part.path}").text.strip("\n")

    # ----------------------------------------------------------------- info
    def disk_info(self, disk: Disk) -> str:
        with self._lock:
            self._run(f"cd {disk.path}")
            return self._run("dinfo").text.strip("\n")

    def partition_info(self, part: Partition) -> str:
        with self._lock:
            self._run(f"cd {part.path}")
            return self._run("dinfo").text.strip("\n")

    def volume_info(self, vol: Volume) -> str:
        with self._lock:
            self._enter_volume(vol)
            return self._run("dinfo").text.strip("\n")

    def file_info(self, vol: Volume, file: FileEntry) -> str:
        with self._lock:
            self._enter_volume(vol)
            return self._run(f"infoi {file.index}").text.strip("\n")

    # --------------------------------------------------------------- export
    def export_files(
        self,
        vol: Volume,
        files: list[FileEntry],
        dest_dir: str | os.PathLike,
        as_wav: bool = False,
        confirm_overwrite: Callable[[Path], bool] | None = None,
    ) -> ExportResult:
        """Copy files out of the disk image into dest_dir.

        as_wav converts samples with akaiutil's `sample2wavi`; otherwise the
        raw Akai file is written with `geti`. Existing destination files are
        only replaced if confirm_overwrite(path) says so (default: never).
        """
        dest = Path(dest_dir)
        result = ExportResult()
        with self._lock:
            session = self._need_session()
            self._enter_volume(vol)
            for f in files:
                if as_wav and not f.is_sample:
                    result.failed.append(f"{f.name}: not a sample")
                    continue
                _clear_dir(session.staging_dir)
                cmd = f"sample2wavi {f.index}" if as_wav else f"geti {f.index}"
                resp = self._run(cmd)
                produced = sorted(p for p in session.staging_dir.iterdir() if p.is_file())
                if not produced:
                    reason = resp.text.strip() or "akaiutil produced no file"
                    result.failed.append(f"{f.name}: {reason}")
                    continue
                for src in produced:
                    target = dest / src.name
                    if target.exists() and not (confirm_overwrite and confirm_overwrite(target)):
                        result.skipped.append(str(target))
                        continue
                    shutil.move(str(src), str(target))
                    result.exported.append(str(target))
        return result

    # ------------------------------------------------------------- internal
    def _need_session(self) -> Session:
        if self._session is None:
            raise EngineError("No disk image is open")
        return self._session

    def _run(self, command: str, timeout: float = 120):
        return self._need_session().run(command, timeout=timeout)

    def _enter_volume(self, vol: Volume) -> None:
        # `cdi` only works from partition level, and a failed `cd` can leave
        # the process somewhere unexpected, so always start from an absolute
        # path and verify where the prompt says we ended up.
        self._run(f"cd /disk{vol.disk}/{vol.partition}")
        resp = self._run(f"cdi {vol.index}")
        if resp.cwd.rstrip() != vol.path.rstrip():
            raise EngineError(f"Couldn't open volume {vol.path}: {resp.text.strip() or resp.cwd}")


def _clear_dir(path: Path) -> None:
    for p in path.iterdir():
        if p.is_file() or p.is_symlink():
            p.unlink()
        else:
            shutil.rmtree(p, ignore_errors=True)
