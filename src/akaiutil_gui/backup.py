"""Backups of disk images (Qt-free): a copy taken before editing starts, and a way back to it.

Editing writes to the image file itself and has no undo, so before the first edit each image is copied into the app's own folder
(`root`), one folder per image, one file per backup named for when it was made. Only the newest few are kept. Reverting puts a
backup back in place of the image (after taking a backup of what the image is now, so the revert can itself be undone).
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

KEEP = 5  # backups kept per image; the oldest go when a new one is made
SPARE_BYTES = 64 * 1024 * 1024  # room to leave free when the copy has to be real (not a clone)
_STAMP = "%Y-%m-%d %H-%M-%S"
_NAME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}(?: \d+)?$")  # what a backup's name (without its suffix) looks like


class BackupError(Exception):
    """The reason a backup or a revert couldn't be done, written for the user."""


@dataclass(frozen=True)
class Backup:
    path: Path
    image: Path  # the image it is a backup of
    created: datetime
    size: int


def default_root() -> Path:
    """Where the app keeps backups: $AKAIUTIL_GUI_BACKUP_DIR if set (the tests use it), else a `backups` folder in the app's
    data folder."""
    override = os.environ.get("AKAIUTIL_GUI_BACKUP_DIR")
    return Path(override) if override else paths.user_data_dir() / "backups"


def folder_for(root: Path, image: str | os.PathLike) -> Path:
    """The folder holding one image's backups: its name for you to recognise, a hash of its path so two images that
    share a name don't share backups."""
    resolved = Path(image).resolve()
    safe = re.sub(r"[^\w.\- ]", "_", resolved.stem).strip(" .") or "image"
    return Path(root) / f"{safe}-{hashlib.sha1(str(resolved).encode()).hexdigest()[:8]}"


def list_backups(root: Path, image: str | os.PathLike) -> list[Backup]:
    """An image's backups, newest first."""
    folder = folder_for(root, image)
    found = []
    if folder.is_dir():
        for p in folder.iterdir():
            if p.is_file() and _NAME.match(p.stem):
                try:
                    created = datetime.strptime(p.stem[:19], _STAMP)
                    found.append(Backup(p, Path(image).resolve(), created, p.stat().st_size))
                except (ValueError, OSError):
                    continue
    return sorted(found, key=lambda b: (b.created, b.path.name), reverse=True)


def prune(root: Path, image: str | os.PathLike) -> None:
    """Delete the oldest backups of an image beyond KEEP (only files that look like our backups)."""
    for old in list_backups(root, image)[KEEP:]:
        old.path.unlink(missing_ok=True)


def cleanup(root: Path, max_age_days: int, now: datetime | None = None) -> list[Path]:
    """Delete the backups older than `max_age_days` (0 or less: never), and the half-written copies a crash can leave behind.
    Only files that look like this app's backups, directly inside a folder directly inside `root`, are touched, and a
    folder that ends up empty goes too. Returns what was deleted."""
    root = Path(root)
    removed: list[Path] = []
    if not root.is_dir():
        return removed
    now = now or datetime.now()
    cutoff = now - timedelta(days=max_age_days) if max_age_days > 0 else None
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        for p in sorted(folder.iterdir()):
            if not p.is_file():
                continue
            try:
                if p.name.startswith(".partial-"):
                    stale = now.timestamp() - p.stat().st_mtime > 3600  # (not one another running copy of the app is writing)
                elif _NAME.match(p.stem) and cutoff is not None:
                    stale = datetime.strptime(p.stem[:19], _STAMP) < cutoff
                else:
                    stale = False
                if stale:
                    p.unlink()
                    removed.append(p)
            except (OSError, ValueError):
                continue
        try:
            folder.rmdir()  # (only works if nothing is left in it)
        except OSError:
            pass
    return removed


def make_backup(root: Path, image: str | os.PathLike, now: datetime | None = None, keep_old: bool = False) -> Backup:
    """Copy an image into its backup folder and drop the oldest backups beyond KEEP (unless `keep_old`)."""
    image = Path(image)
    try:
        size = image.stat().st_size
    except OSError as e:
        raise BackupError(f"Can't read {image.name}: {e.strerror or e}.") from e
    folder = folder_for(root, image)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupError(f"Can't make the backup folder {folder}: {e.strerror or e}.") from e
    stamp = (now or datetime.now()).strftime(_STAMP)
    target = folder / f"{stamp}{image.suffix}"
    n = 1
    while target.exists():  # two backups in the same second
        n += 1
        target = folder / f"{stamp} {n}{image.suffix}"
    partial = folder / f".partial-{uuid.uuid4().hex}{image.suffix}"
    try:
        if not _clone(image, partial):
            free = shutil.disk_usage(folder).free
            if free < size + SPARE_BYTES:
                raise BackupError(
                    f"There isn't room for a backup of {image.name}: it needs {size / 2**20:.0f} MB and "
                    f"{free / 2**20:.0f} MB is free where the backups are kept ({folder})."
                )
            shutil.copyfile(image, partial)
        os.replace(partial, target)
    except OSError as e:
        raise BackupError(f"Couldn't back up {image.name}: {e.strerror or e}.") from e
    finally:
        partial.unlink(missing_ok=True)
    made = Backup(target, image.resolve(), datetime.strptime(stamp, _STAMP), size)
    if not keep_old:
        prune(root, image)
    return made


def restore(root: Path, backup: Backup, image: str | os.PathLike) -> Backup:
    """Put a backup back in place of the image. What the image is now is backed up first (returned), and the image file is
    replaced in one step, so it is never left half-written."""
    image = Path(image)
    if not backup.path.is_file():
        raise BackupError("That backup is gone.")
    before = make_backup(root, image, keep_old=True)  # (not pruned yet: the backup being restored may be the oldest)
    partial = image.with_name(f".{image.name}.restoring")
    try:
        if not _clone(backup.path, partial):
            free = shutil.disk_usage(image.parent).free
            if free < backup.size + SPARE_BYTES:
                raise BackupError(f"There isn't room next to {image.name} to put the backup back ({free / 2**20:.0f} MB free).")
            shutil.copyfile(backup.path, partial)
        try:
            shutil.copymode(image, partial)
        except OSError:
            pass
        os.replace(partial, image)
    except OSError as e:
        raise BackupError(f"Couldn't put the backup back: {e.strerror or e}.") from e
    finally:
        partial.unlink(missing_ok=True)
    prune(root, image)
    return before


def _clone(src: Path, dst: Path) -> bool:
    """A copy-on-write clone (instant, and takes no space) where the file system can: APFS through `cp -c`. False if not."""
    if sys.platform != "darwin":
        return False
    try:
        return subprocess.run(["/bin/cp", "-c", str(src), str(dst)], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        dst.unlink(missing_ok=True)
        return False
