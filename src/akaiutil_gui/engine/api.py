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

import logging
import os
import shutil
import struct
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .. import akai_header, akai_names, describe, references, sorting, tags as filetags
from ..audio import wavfile
from . import formats, parse
from .models import Disk, DiskUsage, ExportResult, FileEntry, ImportJob, Partition, PartitionCopy, SampleInfo, Volume, VolumeCopy
from .session import EngineError, Session, TrafficCallback

log = logging.getLogger(__name__)


class AkaiEngine:
    def __init__(self, binary: str | os.PathLike, on_traffic: TrafficCallback | None = None):
        self._binary = binary
        self._on_traffic = on_traffic
        self._session: Session | None = None
        self._lock = threading.RLock()
        self._tag_names: dict[tuple[int, str], dict[int, str]] = {}  # (disk, partition) -> its tag names; they never change under us

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
            self._tag_names.clear()
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
            files = parse.parse_files(self._run("dir").text)
            return self._named_tags(files, vol.disk, vol.partition)

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
            for part in {e.volume.partition for e in catalog if e.file.tags}:
                self._run(f"cd /disk{disk.index}/{part}")
                names = self._partition_tag_names(disk.index, part)
                catalog = [replace(e, file=_with_tag_names(e.file, names)) if e.volume.partition == part else e for e in catalog]
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

    def volume_references(self, vol: Volume) -> references.VolumeReferences:
        """The volume's files and which samples each of its programs uses (one `infoi` per program, so a volume of many
        samples and few programs is quick)."""
        with self._lock:
            self._enter_volume(vol)
            files = parse.parse_files(self._run("dir").text)
            used = {f.index: tuple(references.sample_names(self._run(f"infoi {f.index}").text)) for f in files if f.is_program}
        return references.VolumeReferences(tuple(files), used)

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

        as_wav converts samples with akaiutil's `sample2wavi` (and adds a "smpl" chunk with the root note and loops,
        which akaiutil leaves out); otherwise the raw Akai file is written with `geti`. Existing destination files are
        only replaced if confirm_overwrite(path) says so (default: never).
        """
        dest = Path(dest_dir)
        result = ExportResult()
        with self._lock:
            log.info("export %d file(s) from %s to %s%s", len(files), vol.name, dest_dir, " as WAV" if as_wav else "")
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
                    if as_wav:
                        try:
                            wavfile.add_loop_info(target)  # so a DAW sees the loops and root note too
                        except (OSError, struct.error):
                            pass  # (the WAV is fine without them)
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
            log.info("rename volume %s (disk%d %s #%d) -> %s", vol.name, vol.disk, vol.partition, vol.index, new_name)
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
            log.info("new %s volume %r #%d in disk%d %s", volume_type, name, index, part.disk, part.name)
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

    def copy_volume(self, src: Volume, part: Partition, new_name: str, tag_notes: bool = True) -> VolumeCopy:
        """Copy a whole volume (every file, its load number and settings) into a hard disk partition, on any open disk, as a
        new volume called `new_name`. akaiutil's `copyvoli` does the work, but it would silently merge into a volume that
        already has the name, copy a load number that another volume already uses, and leave half a volume behind if the
        partition filled up: so this checks first, fixes the load number, and removes a copy that didn't come out whole."""
        clean, problem = akai_names.check_name(new_name, src.type)
        if problem:
            raise EngineError(problem)
        if part.is_dd or part.type.startswith("FL"):
            raise EngineError("A volume can only be copied into a hard disk partition.")
        if (part.type == "HD9") != (src.type == "S900"):
            raise EngineError("An S900 volume only fits an S900 hard disk, and other volumes don't.")
        notes: list[str] = []
        with self._lock:
            log.info("copy volume %s (disk%d %s #%d) -> disk%d %s as %r", src.name, src.disk, src.partition, src.index, part.disk, part.name, new_name)
            self._need_writable()
            current = self._find_volume(src.disk, src.partition, src.index)
            if current is None or not akai_names.same_name(current.name, src.name):
                raise EngineError(f"“{src.name}” isn't volume {src.index} of partition {src.partition} any more.")
            files = self.list_files(src)
            used, limit = self.volume_slots(part)
            if len(used) >= limit:
                raise EngineError(f"Partition {part.name} already holds the most volumes it can ({limit}).")
            if any(akai_names.same_name(v.name, clean) for v in self._volumes_at(part.disk, part.name)):
                raise EngineError(f"There is already a volume called “{clean}” in partition {part.name}.")
            free_blocks = self._free_blocks(part)
            needed = sum(-(-f.size // part.block_size) for f in files) + COPY_SPARE_BLOCKS
            if needed > free_blocks:
                raise EngineError(
                    f"Partition {part.name} hasn't got room: the volume needs about {needed * part.block_size / 2**20:.1f} MB "
                    f"and {free_blocks * part.block_size / 2**20:.1f} MB is free."
                )
            self._run(f"cd /disk{src.disk}/{src.partition}")
            text = self._run(f"copyvoli {src.index} /disk{part.disk}/{part.name}/{akai_names.command_token(clean)}", timeout=3600).text
            created = next((v for v in self._volumes_at(part.disk, part.name) if akai_names.same_name(v.name, clean)), None)
            copied = self.list_files(created) if created is not None else []
            if created is None or any(m in text for m in COPY_FAILED) or len(copied) != len(files):
                if created is not None:  # (it's the one we just made: the name was free a moment ago)
                    self._run(f"cd /disk{part.disk}/{part.name}")
                    self._run(f"delvoli {created.index}")
                said = " ".join(ln.strip() for ln in text.splitlines() if any(m in ln for m in COPY_FAILED))
                raise EngineError(f"akaiutil couldn't copy the volume{': ' + said if said else ''}. Nothing was left behind.")
            if created.load_number not in ("-", ""):
                others = [v for v in self._volumes_at(part.disk, part.name) if v.index != created.index]
                if any(v.load_number == created.load_number for v in others):
                    self._run(f"cd /disk{part.disk}/{part.name}")
                    self._write(f"setlnumi {created.index} OFF")
                    notes.append(f"Load number {created.load_number} is already used in partition {part.name}, so the copy has none.")
                    created = self._find_volume(part.disk, part.name, created.index) or created
            if tag_notes and src.type in ("S3000", "CD3000") and any(f.tags for f in files):
                here, there = self.tag_names(src), self.tag_names(created)
                differing = sorted({n for f in files for n in f.tag_numbers if here.get(n) != there.get(n)})
                if differing:
                    notes.append("Some files are tagged with tags that are named differently in partition " + part.name
                                 + " (" + ", ".join(f"{n}: “{here.get(n, '')}” became “{there.get(n, '')}”" for n in differing[:5]) + ").")
            return VolumeCopy(created, notes)

    def copy_partition(self, src: Partition, dst: Partition) -> PartitionCopy:
        """Copy every volume of a hard disk partition into another one (any open disk), under the same names and with everything
        in them. akaiutil's `copypart` would merge into volumes that share a name, replace their files, and stop half way
        when the partition fills up, so this does what `copy_volume` does for each volume, after checking the names, the
        number of volume slots and the room for all of them first. If a volume still fails, the ones already copied are
        removed again. The tag names go along when the destination still has the default ones."""
        for part in (src, dst):
            if part.is_dd or part.type.startswith("FL"):
                raise EngineError("Only hard disk partitions can be copied.")
        if (src.disk, src.name) == (dst.disk, dst.name):
            raise EngineError("Choose a different partition to copy into.")
        if (src.type == "HD9") != (dst.type == "HD9"):
            raise EngineError("An S900 partition can only be copied to an S900 hard disk, and other partitions can't.")
        with self._lock:
            log.info("copy partition disk%d %s -> disk%d %s", src.disk, src.name, dst.disk, dst.name)
            self._need_writable()
            sources = self._volumes_at(src.disk, src.name)
            if not sources:
                raise EngineError(f"Partition {src.name} has no volumes to copy.")
            there = self._volumes_at(dst.disk, dst.name)
            clashes = [v.name for v in sources if any(akai_names.same_name(v.name, t.name) for t in there)]
            if clashes:
                shown = ", ".join(f"“{n}”" for n in clashes[:4]) + (f" and {len(clashes) - 4} more" if len(clashes) > 4 else "")
                raise EngineError(f"Partition {dst.name} already has {'a volume' if len(clashes) == 1 else 'volumes'} called {shown}.")
            used, limit = self.volume_slots(dst)
            if len(used) + len(sources) > limit:
                raise EngineError(f"Partition {dst.name} would hold {len(used) + len(sources)} volumes, and the most it can hold is {limit}.")
            files = {v.index: self.list_files(v) for v in sources}
            needed = sum(-(-f.size // dst.block_size) for fs in files.values() for f in fs) + COPY_SPARE_BLOCKS * len(sources)
            free_blocks = self._free_blocks(dst)
            if needed > free_blocks:
                raise EngineError(
                    f"Partition {dst.name} hasn't got room: the volumes need about {needed * dst.block_size / 2**20:.1f} MB "
                    f"and {free_blocks * dst.block_size / 2**20:.1f} MB is free."
                )
            copied: list[Volume] = []
            notes: list[str] = []
            try:
                for vol in sources:
                    result = self.copy_volume(vol, dst, vol.name, tag_notes=False)
                    copied.append(result.volume)
                    notes += result.notes
            except EngineError as e:
                if not copied:
                    raise
                self._run(f"cd /disk{dst.disk}/{dst.name}")
                for done in copied:
                    self._run(f"delvoli {done.index}")
                many = len(copied) != 1
                raise EngineError(f"{e} The {len(copied)} volume{'s' if many else ''} copied before it {'were' if many else 'was'} removed again.") from e
            notes += self._carry_tags(sources, files, copied, dst)
            return PartitionCopy(copied, notes)

    def _carry_tags(self, sources: list[Volume], files: dict[int, list[FileEntry]], copied: list[Volume], dst: Partition) -> list[str]:
        """After a partition copy: give the destination the source's tag names if it still has the default ones, and say which
        tags that are in use end up with other names otherwise."""
        tagged = [v for v in sources if v.type in ("S3000", "CD3000") and any(f.tags for f in files[v.index])]
        if not tagged:
            return []
        here, there = self.tag_names(sources[0]), self.tag_names(copied[0])
        defaults = {n: f"TAG {chr(64 + n)}" for n in range(1, 27)}
        if here != there and all(there.get(n, d) == d for n, d in defaults.items()):
            src_path = f"/disk{sources[0].disk}/{sources[0].partition}"
            self._write(f"copytags {src_path} /disk{dst.disk}/{dst.name}")
            self._tag_names.pop((dst.disk, dst.name), None)
            return []
        used = sorted({n for v in tagged for f in files[v.index] for n in f.tag_numbers})
        differing = [n for n in used if here.get(n) != there.get(n)]
        if not differing:
            return []
        return ["Some files are tagged with tags that are named differently in partition " + dst.name
                + " (" + ", ".join(f"{n}: “{here.get(n, '')}” became “{there.get(n, '')}”" for n in differing[:5]) + ")."]

    # ---------------------------------------------------------- a sample's own settings
    def _header_size(self, vol: Volume, f: FileEntry) -> int:
        if not f.is_sample or vol.type == "S900" or f.name.upper().endswith((".S9", ".S9C")):
            raise EngineError("Only S1000 and S3000 samples have settings that can be edited here (not S900 samples).")
        return akai_header.HEADER_SIZES["S3000" if f.name.upper().endswith(".S3") else "S1000"]

    def read_sample_settings(self, vol: Volume, f: FileEntry) -> akai_header.SampleSettings:
        """The loops, root key, tuning, play mode and markers stored in a sample's header."""
        size = self._header_size(vol, f)
        with self._lock:
            session = self._need_session()
            self._enter_volume(vol)
            self._check_file_is_there(vol, f)
            _clear_dir(session.staging_dir)
            self._run(f"geti {f.index} 0 {size}")
            produced = [p for p in session.staging_dir.iterdir() if p.is_file()]
            raw = produced[0].read_bytes() if produced else b""
            _clear_dir(session.staging_dir)
        settings = akai_header.read_settings(raw)
        if settings is None:
            raise EngineError(f"“{f.name}” doesn't have a sample header akaiutil-gui understands.")
        return settings

    def _check_file_is_there(self, vol: Volume, f: FileEntry) -> None:
        present = {x.index: x.name for x in parse.parse_files(self._run("dir").text)}
        if present.get(f.index) != f.name:
            raise EngineError(f"“{f.name}” isn't file {f.index} of {vol.name} any more.")

    def edit_sample_settings(self, vol: Volume, f: FileEntry, new: akai_header.SampleSettings) -> FileEntry:
        """Change a sample's loops, root key, tuning, play mode or markers. akaiutil can't edit a header in place, so the sample
        is written again with the new header (its audio untouched) and put where the old one was, with the same name, tags and
        OS version. The old copy is only deleted once the new one is in place, and every step is checked."""
        size = self._header_size(vol, f)
        with self._lock:
            log.info("edit settings of %s in %s", f.name, vol.name)
            self._need_writable()
            session = self._need_session()
            self._enter_volume(vol)
            text = self._run("dir").text
            files = parse.parse_files(text)
            if not any(x.index == f.index and x.name == f.name for x in files):
                raise EngineError(f"“{f.name}” isn't file {f.index} of {vol.name} any more.")
            osver = filetags.osver_value(f.os_version)
            if osver is None:
                raise EngineError(f"Can't read the OS version of “{f.name}”.")
            base, ext = akai_names.split_extension(f.name)
            clean, problem = akai_names.check_name(base, vol.type)
            if problem or clean.casefold() != base.casefold():
                raise EngineError(f"“{f.name}” has a name akaiutil can't move safely, so it can't be edited.")
            _clear_dir(session.staging_dir)
            self._run(f"geti {f.index}")
            produced = [p for p in session.staging_dir.iterdir() if p.is_file()]
            if len(produced) != 1:
                raise EngineError(f"akaiutil couldn't read “{f.name}”.")
            original = produced[0].read_bytes()
            _clear_dir(session.staging_dir)
            if len(original) < size:
                raise EngineError(f"“{f.name}” is too short to be a sample.")
            try:
                header = akai_header.write_settings(original[:size], new)
            except ValueError as e:
                raise EngineError(str(e)) from None
            if header == original[:size]:
                return f  # nothing was changed
            patched = header + original[size:]

            taken = {x.name.casefold() for x in files}
            limit = parse.parse_volume_limit(text) or 510
            free = [i for i in range(1, limit + 1) if i not in {x.index for x in files}]
            if len(free) < 2:
                raise EngineError(f"{vol.name} has no free file slots left to work in. Delete a file first.")
            temp = next(n for n in (f"#EDIT{i or ''}{ext}" for i in range(100)) if n.casefold() not in taken)
            old = next(n for n in (f"#OLD{i or ''}{ext}" for i in range(100)) if n.casefold() not in taken | {temp.casefold()})

            # 1. the new copy, under a temporary name
            (session.staging_dir / "edit.tar").write_bytes(filetags.tagged_file_tar(temp, patched, osver, f.tag_numbers))
            said = self._run("tarx edit.tar", timeout=600).text
            _clear_dir(session.staging_dir)
            after = parse.parse_files(self._run("dir").text)
            made = next((x for x in after if akai_names.same_name(x.name, temp)), None)
            if made is None or made.size != f.size:
                if made is not None:
                    self._write(f"deli {made.index}")
                reason = " ".join(ln.strip() for ln in said.splitlines() if ln.strip())
                raise EngineError(f"akaiutil couldn't write the new copy of “{f.name}”{': ' + reason if reason else ''}. Nothing was changed.")
            moved_old = False
            try:
                # 2. the old one steps aside, 3. the new one takes its name and place
                spare = next(i for i in free if i != made.index)
                self._write(f"reni {f.index} {akai_names.command_token(old)} {spare}")
                moved_old = True
                self._write(f"reni {made.index} {akai_names.command_token(f.name)} {f.index}")
                self._write(f"fixramnamei {f.index}")  # (the name inside the header is the temporary one)
                # 4. check it before the old one goes
                now = next((x for x in parse.parse_files(self._run("dir").text) if x.index == f.index), None)
                if now is None or not akai_names.same_name(now.name, f.name) or (now.size, now.os_version, now.tags) != (f.size, f.os_version, f.tags):
                    raise EngineError("the new copy didn't come out the same size, OS version and tags as the original")
                if self._read_whole(f.index, session)[size:] != original[size:]:
                    raise EngineError("the audio of the new copy isn't the same as the original's")
            except EngineError as e:
                self._undo_sample_edit(f, old, temp, made.index, spare if moved_old else None)
                raise EngineError(f"“{f.name}” wasn't changed: {e}.") from None
            self._write(f"deli {spare}")  # (the old copy, now in the spare slot)
            final = next((x for x in parse.parse_files(self._run("dir").text) if x.index == f.index), None)
            if final is None:
                raise EngineError(f"“{f.name}” is missing after the edit: check {vol.name}.")
            return final

    def _read_whole(self, index: int, session) -> bytes:
        _clear_dir(session.staging_dir)
        self._run(f"geti {index}")
        produced = [p for p in session.staging_dir.iterdir() if p.is_file()]
        data = produced[0].read_bytes() if produced else b""
        _clear_dir(session.staging_dir)
        return data

    def _undo_sample_edit(self, f: FileEntry, old: str, temp: str, temp_index: int, spare: int | None) -> None:
        """Put things back as they were after a step of edit_sample_settings failed (best effort, and logged)."""
        try:
            files = {x.index: x.name for x in parse.parse_files(self._run("dir").text)}
            if spare is not None and akai_names.same_name(files.get(spare, ""), old):
                for index, name in list(files.items()):  # whatever is in the original's slot (the new copy, if it got there) goes
                    if index == f.index and not akai_names.same_name(name, old):
                        self._write(f"deli {index}")
                self._write(f"reni {spare} {akai_names.command_token(f.name)} {f.index}")
            files = {x.index: x.name for x in parse.parse_files(self._run("dir").text)}
            if akai_names.same_name(files.get(temp_index, ""), temp):
                self._write(f"deli {temp_index}")
        except EngineError as e:
            log.error("couldn't fully undo a failed sample edit of %s: %s", f.name, e)

    def delete_volume(self, vol: Volume) -> None:
        """Delete a volume and every file in it."""
        with self._lock:
            log.info("delete volume %s (disk%d %s #%d)", vol.name, vol.disk, vol.partition, vol.index)
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
            log.info("rename file %s in %s -> %s", file.name, vol.name, new_name)
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
            log.info("delete %d file(s) from %s: %s", len(files), vol.name, ", ".join(f.name for f in files[:10]) + (" …" if len(files) > 10 else ""))
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

    def sort_files(self, vol: Volume, scope: str = sorting.ALL) -> int:
        """Put the files of a volume in alphabetical order by moving them between slots (the order a sampler lists them in).
        `scope` is sorting.ALL, or sorting.SAMPLES to leave everything but the samples where it is. Returns how many moved.
        Nothing about a file changes but its slot: the data isn't touched, and tags and all stay."""
        with self._lock:
            log.info("sort %s in %s", scope, vol.name)
            self._need_writable()
            self._enter_volume(vol)
            text = self._run("dir").text
            files = parse.parse_files(text)
            limit = parse.parse_volume_limit(text) or 510
            moving = [f for f in files if sorting.takes_part(f, scope) and f.index in sorting.target_slots(files, scope)]
            for f in moving:  # akaiutil re-encodes a name when it moves a file: only move names that survive that
                base, ext = akai_names.split_extension(f.name)
                clean, problem = akai_names.check_name(base, vol.type)
                if problem or clean.casefold() != base.casefold():
                    raise EngineError(f"“{f.name}” has a name akaiutil can't move safely, so nothing was sorted.")
            used = {f.index for f in files}
            free = next((i for i in range(1, limit + 1) if i not in used), None)
            try:
                moves = sorting.plan_moves(files, scope, free)
            except ValueError:
                raise EngineError(f"Every slot of {vol.name} is in use, so there is no room to shuffle the files. Delete one file first.") from None
            names = {f.index: f.name for f in files}
            for src, dst in moves:
                name = names.pop(src)
                self._write(f"reni {src} {akai_names.command_token(name)} {dst}")
                names[dst] = name
            after = parse.parse_files(self._run("dir").text)
            fingerprint = lambda fs: sorted((f.name, f.size, f.start_block, f.os_version, f.tags) for f in fs)  # noqa: E731
            if fingerprint(after) != fingerprint(files):
                raise EngineError("The files changed while being sorted: check the volume before using it.")
            return len(moving)

    def import_job(self, vol: Volume, job: ImportJob) -> list[str]:
        """Put one file into a volume. Returns the names of the files that appeared (two for a stereo sample)."""
        command = _IMPORT_COMMANDS.get(job.kind)
        if command is None:
            raise EngineError(f"Can't import a “{job.kind}”.")
        with self._lock:
            log.info("import %s as %s into %s", job.local.name, job.kind, vol.name)
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

    def tag_names(self, vol: Volume) -> dict[int, str]:
        """The names of the tags of a volume's partition ({} for a floppy, or a partition without tags)."""
        with self._lock:
            if (vol.disk, vol.partition) not in self._tag_names:
                self._enter_volume(vol)
            return self._partition_tag_names(vol.disk, vol.partition)

    def copy_file(self, src: Volume, f: FileEntry, dst: Volume, new_name: str, tags: list[int] | None = None) -> str:
        """Copy a file, byte for byte, into another volume (any partition, any open disk) as `new_name` (with its
        extension). Returns the name it has there.

        akaiutil's `put` doesn't carry a file's tags. If `tags` (numbers of the destination partition's tags) are given, the
        file goes in through a one-file tar import instead, which does (see tags.py). If that doesn't work, `put` is used
        and the file arrives untagged."""
        with self._lock:
            log.info("copy file %s from %s to %s (%s)%s", f.name, src.name, dst.name, new_name, " with tags" if tags else "")
            self._need_writable()
            session = self._need_session()
            self._enter_volume(src)
            present = {x.index: x.name for x in parse.parse_files(self._run("dir").text)}
            if present.get(f.index) != f.name:
                raise EngineError(f"“{f.name}” isn't file {f.index} of {src.name} any more.")
            _clear_dir(session.staging_dir)
            self._run(f"geti {f.index}")
            produced = [p for p in session.staging_dir.iterdir() if p.is_file()]
            if len(produced) != 1:
                raise EngineError(f"akaiutil couldn't read “{f.name}”.")
            staged = session.staging_dir / new_name
            produced[0].replace(staged)
            self._enter_volume(dst)
            before = {x.name for x in parse.parse_files(self._run("dir").text)}
            text = ""
            created: list[FileEntry] = []
            osver = filetags.osver_value(f.os_version)
            if tags and osver is not None and dst.type in ("S3000", "CD3000"):
                data = staged.read_bytes()
                staged.unlink()
                (session.staging_dir / "tagged.tar").write_bytes(filetags.tagged_file_tar(new_name, data, osver, tags))
                text = self._run("tarx tagged.tar", timeout=600).text
                created = [x for x in parse.parse_files(self._run("dir").text) if x.name not in before]
                if not created:  # the tar route failed: carry on without the tags
                    staged.write_bytes(data)
                (session.staging_dir / "tagged.tar").unlink(missing_ok=True)
            if not created:
                text = self._run(f"put {akai_names.command_token(new_name)}", timeout=600).text
                created = [x for x in parse.parse_files(self._run("dir").text) if x.name not in before]
            _clear_dir(session.staging_dir)
            if not created:
                said = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())
                raise EngineError(said or "akaiutil didn't add the copy.")
            copy = created[0]
            if new_name.upper().endswith(".S9C") and dst.type == "S900" and copy.os_version != f.os_version:
                # a compressed S900 sample records how big it is uncompressed: put can't know, but akaiutil can work it out
                self._write(f"updateuncompri {copy.index}")
            return copy.name

    def move_file(self, src: Volume, f: FileEntry, dst: Volume, new_name: str) -> str:
        """Move a file to another volume of the same partition, as `new_name`: nothing about it changes but where it is (tags
        and all). Returns the name it has now."""
        if (src.disk, src.partition) != (dst.disk, dst.partition):
            raise EngineError("A file can only be moved within a partition (copy it, then delete the original).")
        with self._lock:
            log.info("move file %s from %s to %s (%s)", f.name, src.name, dst.name, new_name)
            self._need_writable()
            self._enter_volume(src)
            present = {x.index: x.name for x in parse.parse_files(self._run("dir").text)}
            if present.get(f.index) != f.name:
                raise EngineError(f"“{f.name}” isn't file {f.index} of {src.name} any more.")
            target = f"/disk{dst.disk}/{dst.partition}/{akai_names.command_token(dst.name)}/{akai_names.command_token(new_name)}"
            self._write(f"reni {f.index} {target}")
            self._enter_volume(dst)
            moved = next((x for x in parse.parse_files(self._run("dir").text) if akai_names.same_name(x.name, new_name)), None)
            if moved is None:
                raise EngineError(f"akaiutil didn't move “{f.name}”.")
            return moved.name

    def format_disk(self, disk: Disk, fmt: formats.DiskFormat, partition_mb: int = formats.PARTITION_MAX_MB) -> None:
        """Erase a disk and make it an empty one of the given kind. Everything on it is lost.

        A floppy keeps its size, so only a kind of the same density fits; a hard disk is formatted over its
        whole image (up to 512 MB), cut into partitions of `partition_mb`."""
        if disk.type == "FLL" and fmt.floppy_kb != 800 or disk.type == "FLH" and fmt.floppy_kb != 1600:
            raise EngineError(f"A {'DD' if disk.type == 'FLL' else 'HD'} floppy can't be formatted as “{fmt.label}”.")
        if not disk.type.startswith("FL") and fmt.is_floppy:
            raise EngineError(f"A hard disk image can't be formatted as “{fmt.label}”.")
        with self._lock:
            log.info("format disk%d (%s) as %s", disk.index, disk.type, fmt.label)
            self._need_writable()
            self._tag_names.clear()  # the partitions are about to be new ones
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
        log.info("create image %s (%s)", path, fmt.label)
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

    def _partition_tag_names(self, disk: int, partition: str) -> dict[int, str]:
        """The tag names of the partition akaiutil is currently in (`lstags` lists the partition of wherever the prompt is)."""
        key = (disk, partition)
        if key not in self._tag_names:
            self._tag_names[key] = parse.parse_tag_names(self._run("lstags").text)
        return self._tag_names[key]

    def _named_tags(self, files: list[FileEntry], disk: int, partition: str) -> list[FileEntry]:
        """The files with their tags' names filled in (akaiutil must be inside the partition)."""
        if not any(f.tags for f in files):
            return files
        names = self._partition_tag_names(disk, partition)
        return [_with_tag_names(f, names) for f in files]

    def _free_blocks(self, part: Partition) -> int:
        """The blocks free in a partition right now (the listing the window holds may be older)."""
        return next(
            (p.free_blocks for p in parse.parse_partitions(self._run(f"dir /disk{part.disk}").text, part.disk, part.block_size) if p.name == part.name),
            part.free_blocks,
        )

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


COPY_SPARE_BLOCKS = 4  # what a new volume itself takes, with a little to spare
COPY_FAILED = ("copy error", "not enough space", "cannot create", "read error", "write error")  # what copyvoli says when it gives up

# akaiutil's command for each kind of import (see models.ImportJob)
_IMPORT_COMMANDS = {"wav1": "wav2sample1", "wav3": "wav2sample3", "wav9": "wav2sample9", "wav9c": "wav2sample9c", "put": "put"}

# akaiutil's "create volume" command for each volume type
_MKVOL_COMMANDS = {"S900": "mkvoli9", "S1000": "mkvoli1", "S3000": "mkvoli3", "CD3000": "mkvoli3cd"}


def _with_tag_names(f: FileEntry, names: dict[int, str]) -> FileEntry:
    return replace(f, tag_names=tuple(names.get(n, "") for n in f.tag_numbers)) if f.tags else f


def _clear_dir(path: Path) -> None:
    for p in path.iterdir():
        if p.is_file() or p.is_symlink():
            p.unlink()
        else:
            shutil.rmtree(p, ignore_errors=True)
