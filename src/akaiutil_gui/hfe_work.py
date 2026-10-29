"""Working copies of HFE disk images (Qt-free), so akaiutil can open them.

akaiutil only reads flat images (see hfe.py). When the app is asked to open an HFE file it decodes it into a working copy, a flat
image in the app's own folder, and gives akaiutil that. akaiutil writes the working copy after every command, so after every edit
`sync` patches the changed sectors back into the HFE file: the file on disk is as up to date as an ordinary image would be, and
nothing is waiting for the window to close. A write-back that fails (a read-only card, a full disk) is reported by `sync` and kept
for the next one; the working copy stays until it has gone back, or the image is opened afresh.

A disk with sectors that can't be read is still opened, for looking at: the unreadable sectors are blank in the working copy.
Such a disk is never written back (`sync` skips it, `save_as` refuses it, and the window keeps editing off while one is open),
since patching it would put the blank sectors into the file.

Every other kind of file passes through untouched.
"""

import hashlib
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import hfe, paths

log = logging.getLogger(__name__)
KEEP_DAYS = 14  # how long a working copy that was never cleared away is kept


def default_root() -> Path:
    """Where working copies live: $AKAIUTIL_GUI_HFE_DIR if set (the tests use it), else an `hfe-work` folder in the app's data folder."""
    override = os.environ.get("AKAIUTIL_GUI_HFE_DIR")
    return Path(override) if override else paths.user_data_dir() / "hfe-work"


def _signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


@dataclass
class _Entry:
    source: Path  # the HFE file the user opened
    working: Path  # the flat image akaiutil has
    signature: tuple[int, int]  # the source's size and time as of the last decode or write-back
    digest: str  # of the working copy as of then
    missing: tuple[tuple[int, int, int], ...] = ()  # (track, side, sector) of every sector that couldn't be read: a damaged disk


class Workspace:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()
        self._entries: dict[str, _Entry] = {}  # resolved source path -> entry
        self._lock = threading.Lock()  # (decoding runs on a worker thread, the rest on the window's)

    # ------------------------------------------------------------------------------------------------------- opening
    def engine_path(self, source: str | os.PathLike) -> str:
        """The file akaiutil should open for this one: the file itself, or for an HFE a working copy (decoded now, or the one
        already there if the HFE has not changed since). Raises hfe.HfeError for an HFE that can't be decoded."""
        source = Path(source)
        if not hfe.is_hfe(source):
            return str(source)
        key = str(source.resolve())
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.working.exists() and entry.signature == _signature(source):
                return str(entry.working)
            if entry is not None and entry.working.exists() and _digest(entry.working) != entry.digest:
                log.warning("%s changed on disk while edits to it were waiting to be written; they are dropped", source)
            data = source.read_bytes() if source.exists() else b""
            decoded = hfe.decode(data, strict=False)
            folder = self.root / f"{re.sub(r'[^\w.\- ]', '_', source.stem).strip(' .') or 'disk'}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
            folder.mkdir(parents=True, exist_ok=True)
            working = folder / f"{source.stem}.img"
            working.write_bytes(decoded.image)
            self._entries[key] = _Entry(
                source, working, _signature(source), hashlib.sha1(decoded.image).hexdigest(), tuple(decoded.missing)
            )
            log.info("decoded %s (%s, %d bytes) into %s", source, decoded.density, len(decoded.image), working)
            if decoded.problems:
                log.warning("%s is damaged, opened read-only: %s", source, "; ".join(decoded.problems[:10]))
            return str(working)

    def source_for(self, path: str | os.PathLike) -> str:
        """The file the user knows by this path akaiutil was given (a working copy stands for its HFE; anything else for itself)."""
        resolved = Path(path).resolve()
        with self._lock:
            for entry in self._entries.values():
                if entry.working.resolve() == resolved:
                    return str(entry.source.resolve())
        return str(path)

    def working_path(self, source: str | os.PathLike) -> str:
        """Where the data of this file is being edited: its working copy if it is an open HFE, else the file itself."""
        with self._lock:
            entry = self._entries.get(str(Path(source).resolve()))
        return str(entry.working) if entry is not None and entry.working.exists() else str(source)

    def damaged(self) -> dict[str, list[tuple[int, int, int]]]:
        """The open HFE files that have sectors that couldn't be read, with where those sectors are: {file: [(track, side, sector)]}."""
        with self._lock:
            return {str(e.source.resolve()): list(e.missing) for e in self._entries.values() if e.missing}

    def is_hfe(self, source: str | os.PathLike) -> bool:
        with self._lock:
            return str(Path(source).resolve()) in self._entries

    # ------------------------------------------------------------------------------------------------------ writing back
    def pending(self) -> list[str]:
        """The HFE files whose working copy has changes that haven't gone back."""
        with self._lock:
            return [
                str(e.source) for e in self._entries.values() if not e.missing and e.working.exists() and _digest(e.working) != e.digest
            ]

    def sync(self) -> list[tuple[str, str]]:
        """Write every changed working copy back into its HFE file. Returns (file, reason) for each that could not be written."""
        failures: list[tuple[str, str]] = []
        with self._lock:
            entries = list(self._entries.values())
        for entry in entries:
            if entry.missing:
                continue  # (read-only: and writing it back would put the blank sectors into the file)
            try:
                digest = _digest(entry.working)
                if digest == entry.digest:
                    continue
                hfe.update(entry.source, entry.working.read_bytes())
                with self._lock:
                    entry.digest, entry.signature = digest, _signature(entry.source)
                log.info("wrote the edits to %s back into the HFE file", entry.source)
            except (hfe.HfeError, OSError) as e:
                reason = str(e) if isinstance(e, hfe.HfeError) else f"{e.strerror or e}"
                log.warning("couldn't write %s back: %s", entry.source, reason)
                failures.append((str(entry.source), reason))
        return failures

    def save_as(self, source: str | os.PathLike, dest: str | os.PathLike) -> None:
        """Save the disk that was opened from `source` (an HFE or a flat floppy image) as an HFE file at `dest`. An HFE source
        keeps its own layout; a flat image is encoded afresh (high-density floppies only)."""
        source = Path(source)
        entry = self._entries.get(str(source.resolve()))
        if entry is not None and entry.missing:
            raise hfe.HfeError(f"{source.name} has sectors that couldn't be read, so saving it as another HFE file would write them as blank.")
        if entry is not None:
            hfe.save(dest, entry.working.read_bytes(), template=source.read_bytes())
            return
        try:
            image = source.read_bytes()
        except OSError as e:
            raise hfe.HfeError(f"Couldn't read {source.name}: {e.strerror or e}.") from e
        hfe.save(dest, image)

    def repair_copy(self, source: str | os.PathLike, dest: str | os.PathLike, rewrite: bool = False) -> hfe.Repair:
        """Save a repaired copy of the HFE file `source` as `dest` (never `source` itself). Returns what was done to each damaged
        sector. With `rewrite`, sectors that can't be fixed are given a fresh checksum anyway."""
        source, dest = Path(source), Path(dest)
        if dest.resolve() == source.resolve():
            raise hfe.HfeError("A repaired copy has to be a new file: the original is never changed.")
        try:
            original = source.read_bytes()
        except OSError as e:
            raise hfe.HfeError(f"Couldn't read {source.name}: {e.strerror or e}.") from e
        repaired = hfe.repair(original, rewrite)
        hfe.save_repair(dest, original, repaired)
        log.info("saved a repaired copy of %s as %s: %s", source, dest, [(f.track, f.side, f.sector, f.result, f.how) for f in repaired.fixes])
        return repaired

    def cleanup(self, max_age_days: int = KEEP_DAYS, now: float | None = None) -> list[Path]:
        """Delete working copies left behind long ago (a crash, or edits that never could be written back and that nobody came back
        for). Only flat images directly inside a folder directly inside the working folder, none of the ones in use now. Returns
        what was deleted."""
        if max_age_days <= 0 or not self.root.is_dir():
            return []
        cutoff = (now if now is not None else time.time()) - max_age_days * 86400
        with self._lock:
            in_use = {e.working.parent.resolve() for e in self._entries.values()}
        removed: list[Path] = []
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if folder.resolve() in in_use:
                continue
            try:
                images = [p for p in folder.iterdir() if p.is_file() and p.suffix == ".img"]
                if images and all(p.stat().st_mtime < cutoff for p in images):
                    for p in images:
                        p.unlink()
                        removed.append(p)
                folder.rmdir()  # (only works if nothing else is in it)
            except OSError:
                continue
        return removed

    # ----------------------------------------------------------------------------------------------------------- closing
    def release(self, keep: list[str | os.PathLike] = ()) -> None:
        """Forget the working copies of every HFE not in `keep`, and delete them unless edits in them haven't gone back."""
        keep_keys = {str(Path(p).resolve()) for p in keep}
        with self._lock:
            self._drop([key for key in self._entries if key not in keep_keys])

    def drop(self, sources: list[str | os.PathLike]) -> None:
        """Forget the working copies of just these HFE files (as `release` does)."""
        with self._lock:
            self._drop([str(Path(p).resolve()) for p in sources])

    def _drop(self, keys: list[str]) -> None:
        for key in keys:
            entry = self._entries.pop(key, None)
            if entry is None:
                continue
            if entry.working.exists() and _digest(entry.working) != entry.digest:
                log.warning("keeping %s: its edits were never written back to %s", entry.working, entry.source)
                continue
            shutil.rmtree(entry.working.parent, ignore_errors=True)
