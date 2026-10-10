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

from .. import akai_names, describe
from . import formats, parse
from .models import Disk, DiskUsage, ExportResult, FileEntry, ImportJob, Partition, SampleInfo, Volume
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

    @property
    def is_writable(self) -> bool:
        return self.is_open and self._session.writable

    def open(self, images: list[str | os.PathLike], writable: bool = False) -> str:
        """(Re)start akaiutil on the given disk images. Read-only unless `writable` says otherwise."""
        with self._lock:
            self.close()
            session = Session(self._binary, images, self._on_traffic, writable=writable)
            try:
                banner = session.start()
            except EngineError:
                session.close()
                raise
            self._session = session
            return banner

    def set_writable(self, writable: bool) -> str:
        """Restart akaiutil on the same images in read/write (or read-only) mode. What is shown stays valid:
        nothing about the images changes, only what akaiutil will let us do to them."""
        with self._lock:
            session = self._need_session()
            if session.writable == writable:
                return session.banner
            images = session.images
            try:
                return self.open(images, writable)
            except EngineError:
                if writable:  # open() has closed the old session: get back to where we were if we can
                    try:
                        self.open(images, False)
                    except EngineError:
                        pass
                raise

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

    def volume_slots(self, part: Partition) -> tuple[list[int], int]:
        """(the volume numbers in use, the highest number the partition allows)."""
        with self._lock:
            text = self._run(f"dir {part.path}").text
            used = [v.index for v in parse.parse_volumes(text, part.disk, part.name)]
            return used, parse.parse_volume_limit(text) or 100

    def disk_usage(self, disk: Disk) -> DiskUsage:
        """Partitions plus the space each volume uses, from a single recursive listing."""
        with self._lock:
            self._run(f"cd {disk.path}")
            text = self._run("dirrec", timeout=300).text  # every file on the disk; can be long
            partitions, volumes, catalog = parse.parse_dirrec(text, disk.index, disk.block_size)
            return DiskUsage(disk, partitions, volumes, catalog)

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

    def sample_info(self, vol: Volume, file: FileEntry) -> SampleInfo | None:
        """How one sample plays (rate, loops, markers...), or None if the file isn't a sample."""
        return describe.parse_sample_info(self.file_info(vol, file))

    def volume_sample_infos(self, vol: Volume) -> dict[int, SampleInfo]:
        """The SampleInfo of every sample in a volume, by file number, from a single `infoall`."""
        with self._lock:
            self._enter_volume(vol)
            text = self._run("infoall", timeout=300).text
        infos = {}
        for number, block in parse.split_infoall(text).items():
            info = describe.parse_sample_info(block)
            if info is not None:
                infos[number] = info
        return infos

    def export_sample_wav(self, vol: Volume, file: FileEntry, dest_dir: str | os.PathLike) -> Path:
        """Convert one sample to a WAV file in dest_dir (replacing one that's already there)."""
        result = self.export_files(vol, [file], dest_dir, as_wav=True, confirm_overwrite=lambda _path: True)
        if not result.exported:
            raise EngineError(result.failed[0] if result.failed else f"couldn't convert {file.name} to WAV")
        return Path(result.exported[0])

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

    # ---------------------------------------------------------------- editing
    # Every one of these needs a writable session (set_writable), changes the image straight away, and
    # raises EngineError, with akaiutil's own explanation, if akaiutil refused. They check afterwards
    # that the image really looks the way it should: akaiutil's messages are all we'd otherwise have.
    def rename_volume(self, vol: Volume, new_name: str) -> Volume:
        name, problem = akai_names.check_name(new_name, vol.type)
        if problem:
            raise EngineError(problem)
        with self._lock:
            self._need_writable()
            siblings = self._volumes_at(vol.disk, vol.partition)
            if sum(akai_names.same_name(v.name, vol.name) for v in siblings) != 1:
                # akaiutil finds a volume by its name, so it couldn't tell which of two to rename
                raise EngineError(f"More than one volume in partition {vol.partition} is called “{vol.name}”.")
            if any(v.index != vol.index and akai_names.same_name(v.name, name) for v in siblings):
                raise EngineError(f"There is already a volume called “{name}” in partition {vol.partition}.")
            old_path = f"/disk{vol.disk}/{vol.partition}/{akai_names.command_token(vol.name)}"
            self._write(f"renvol {old_path} {akai_names.command_token(name)}")
            renamed = self._find_volume(vol.disk, vol.partition, vol.index)
            if renamed is None or not akai_names.same_name(renamed.name, name):
                raise EngineError(f"akaiutil didn't rename “{vol.name}”.")
            return renamed

    def create_volume(self, part: Partition, index: int, name: str, volume_type: str, load_number: int | None = None) -> Volume:
        command = _MKVOL_COMMANDS.get(volume_type)
        if command is None:
            raise EngineError(f"Can't create a {volume_type} volume.")
        clean, problem = akai_names.check_name(name, volume_type)
        if problem:
            raise EngineError(problem)
        if load_number is not None and not 1 <= load_number <= 128:
            raise EngineError("A load number is 1 to 128.")
        with self._lock:
            self._need_writable()
            used, limit = self.volume_slots(part)
            if not 1 <= index <= limit:
                raise EngineError(f"A volume number in this partition is 1 to {limit}.")
            if index in used:
                raise EngineError(f"Volume number {index} is already taken.")
            if any(akai_names.same_name(v.name, clean) for v in self._volumes_at(part.disk, part.name)):
                raise EngineError(f"There is already a volume called “{clean}” in partition {part.name}.")
            self._run(f"cd {part.path}")
            lnum = f" {load_number}" if load_number is not None else ""
            self._write(f"{command} {index} {akai_names.command_token(clean)}{lnum}")
            created = self._find_volume(part.disk, part.name, index)
            if created is None:
                raise EngineError("akaiutil didn't create the volume.")
            return created

    def delete_volume(self, vol: Volume) -> None:
        """Delete a volume and every file in it."""
        with self._lock:
            self._need_writable()
            current = self._find_volume(vol.disk, vol.partition, vol.index)
            if current is None or not akai_names.same_name(current.name, vol.name):
                raise EngineError(f"“{vol.name}” isn't volume {vol.index} of partition {vol.partition} any more.")
            self._run(f"cd /disk{vol.disk}/{vol.partition}")
            self._write(f"delvoli {vol.index}")
            if self._find_volume(vol.disk, vol.partition, vol.index) is not None:
                raise EngineError(f"akaiutil didn't delete “{vol.name}”.")

    def rename_file(self, vol: Volume, file: FileEntry, new_name: str) -> FileEntry:
        """Give a file a new name. Its type is part of its name (".S3") and stays what it was."""
        _, ext = akai_names.split_extension(file.name)
        base, problem = akai_names.check_name(new_name, vol.type)
        if problem:
            raise EngineError(problem)
        with self._lock:
            self._need_writable()
            self._enter_volume(vol)
            files = parse.parse_files(self._run("dir").text)
            if not any(f.index == file.index and f.name == file.name for f in files):
                raise EngineError(f"“{file.name}” isn't file {file.index} of {vol.name} any more.")
            if any(f.index != file.index and akai_names.same_name(f.name, base + ext) for f in files):
                raise EngineError(f"There is already a file called “{base + ext}” in {vol.name}.")
            self._write(f"reni {file.index} {akai_names.command_token(base + ext)}")
            renamed = next((f for f in parse.parse_files(self._run("dir").text) if f.index == file.index), None)
            if renamed is None or not akai_names.same_name(renamed.name, base + ext):
                raise EngineError(f"akaiutil didn't rename “{file.name}”.")
            return renamed

    def delete_files(self, vol: Volume, files: list[FileEntry]) -> list[str]:
        """Delete files. Returns "NAME: reason" for each one that couldn't be."""
        failed = []
        with self._lock:
            self._need_writable()
            self._enter_volume(vol)
            present = {f.index: f.name for f in parse.parse_files(self._run("dir").text)}
            for f in files:
                if present.get(f.index) != f.name:
                    failed.append(f"{f.name}: it isn't file {f.index} of {vol.name} any more")
                    continue
                try:
                    self._write(f"deli {f.index}")
                except EngineError as e:
                    failed.append(f"{f.name}: {e}")
        return failed

    def import_job(self, vol: Volume, job: ImportJob) -> list[str]:
        """Put one file into a volume. Returns the names of the files that appeared (two for a stereo sample)."""
        command = _IMPORT_COMMANDS.get(job.kind)
        if command is None:
            raise EngineError(f"Can't import a “{job.kind}”.")
        with self._lock:
            self._need_writable()
            session = self._need_session()
            self._enter_volume(vol)
            before = {f.name for f in parse.parse_files(self._run("dir").text)}
            _clear_dir(session.staging_dir)
            # akaiutil reads a file from its own working directory, and takes '_' on the command line for a space in its name
            shutil.copyfile(job.local, session.staging_dir / job.local.name)
            text = self._run(f"{command} {akai_names.command_token(job.local.name)}", timeout=600).text
            _clear_dir(session.staging_dir)
            after = parse.parse_files(self._run("dir").text)
            created = [f.name for f in after if f.name not in before]
            if not created:
                said = " ".join(ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("importing"))
                raise EngineError(said or "akaiutil didn't add anything.")
            return created

    def format_disk(self, disk: Disk, fmt: formats.DiskFormat, partition_mb: int = formats.PARTITION_MAX_MB) -> None:
        """Erase a disk and make it an empty one of the given kind. Everything on it is lost.

        A floppy keeps its size, so only a kind of the same density fits; a hard disk is formatted over its
        whole image (up to 512 MB), cut into partitions of `partition_mb`."""
        if disk.type == "FLL" and fmt.floppy_kb != 800 or disk.type == "FLH" and fmt.floppy_kb != 1600:
            raise EngineError(f"A {'low' if disk.type == 'FLL' else 'high'}-density floppy can't be formatted as “{fmt.label}”.")
        if not disk.type.startswith("FL") and fmt.is_floppy:
            raise EngineError(f"A hard disk image can't be formatted as “{fmt.label}”.")
        with self._lock:
            self._need_writable()
            self._run(f"cd {disk.path}")
            command = fmt.akaiutil_command(partition_mb)
            text = self._run(command, timeout=600).text
            # on success akaiutil restarts itself and prints the new disk list; on failure it says "format error"
            now = next((d for d in parse.parse_disks(self._run("dir /").text) if d.index == disk.index), None)
            if now is None or now.partitions == 0 or "format error" in text:
                # (the banner of a restart also says "invalid format" about any other blank disk, so only look at what failed)
                reason = " ".join(ln.strip() for ln in text.splitlines() if "error" in ln.lower() or ln.strip().startswith("invalid"))
                raise EngineError(f"akaiutil couldn't format the disk. {reason}".strip())

    def create_image(self, path: str | os.PathLike, fmt: formats.DiskFormat, size_mb: int = 0, partition_mb: int = formats.PARTITION_MAX_MB) -> Path:
        """Make a new, empty disk image file. Nothing needs to be open; an existing file at `path` is only replaced
        once the new one is complete."""
        path = Path(path)
        if not fmt.is_floppy and not 1 <= size_mb <= fmt.max_mb:
            raise EngineError(f"A {fmt.label} image is 1 to {fmt.max_mb} MB.")
        if fmt.partitions and not formats.PARTITION_MIN_MB <= partition_mb <= formats.PARTITION_MAX_MB:
            raise EngineError(f"A partition is {formats.PARTITION_MIN_MB} to {formats.PARTITION_MAX_MB} MB.")
        part = path.with_name(f".{path.name}.part")
        try:
            with open(part, "wb") as f:
                f.truncate(fmt.image_bytes(size_mb))  # zeros; a sparse file where the filesystem has them
            session = Session(self._binary, [part], self._on_traffic, writable=True)
            try:
                session.start()
                session.run("cd /disk0")
                text = session.run(fmt.akaiutil_command(partition_mb, None if fmt.is_floppy else size_mb), timeout=600).text
                disks = parse.parse_disks(session.run("dir /").text)
            finally:
                session.close()
            if not disks or disks[0].partitions == 0 or "format error" in text:
                raise EngineError("akaiutil couldn't format the new image.")
            os.replace(part, path)
        except OSError as e:
            raise EngineError(f"Couldn't create {path.name}: {e}") from e
        finally:
            part.unlink(missing_ok=True)
        return path

    # ------------------------------------------------------------- internal
    def _need_writable(self) -> None:
        if not self._need_session().writable:
            raise EngineError("Editing is switched off.")

    def _write(self, command: str, timeout: float = 120) -> None:
        """Run a command that changes the image. They print nothing when they work, so anything printed is the reason they didn't."""
        text = self._run(command, timeout).text.strip()
        if text:
            raise EngineError(text)

    def _volumes_at(self, disk: int, partition: str) -> list[Volume]:
        return parse.parse_volumes(self._run(f"dir /disk{disk}/{partition}").text, disk, partition)

    def _find_volume(self, disk: int, partition: str, index: int) -> Volume | None:
        return next((v for v in self._volumes_at(disk, partition) if v.index == index), None)

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


# akaiutil's command for each kind of import (see models.ImportJob)
_IMPORT_COMMANDS = {"wav1": "wav2sample1", "wav3": "wav2sample3", "wav9": "wav2sample9", "wav9c": "wav2sample9c", "put": "put"}

# akaiutil's "create volume" command for each volume type
_MKVOL_COMMANDS = {"S900": "mkvoli9", "S1000": "mkvoli1", "S3000": "mkvoli3", "CD3000": "mkvoli3cd"}


def _clear_dir(path: Path) -> None:
    for p in path.iterdir():
        if p.is_file() or p.is_symlink():
            p.unlink()
        else:
            shutil.rmtree(p, ignore_errors=True)
