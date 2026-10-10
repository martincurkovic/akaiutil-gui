"""Copying and moving files between volumes (Qt-free).

A move inside one partition is akaiutil's own `reni`, which changes nothing but where the file is. Anything else (another
partition, another disk, or a copy) reads the file out and puts it back byte for byte, which doesn't carry its tags; a move
of that kind deletes the original only once the copy is there. The new file gets a numbered name if the volume already has
one of that name, because akaiutil would otherwise replace the old file.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from . import akai_names, importing
from .engine.models import FileEntry, Volume


@dataclass
class TransferResult:
    done: list[str] = field(default_factory=list)  # the names the files have now
    failed: list[str] = field(default_factory=list)  # "NAME: reason"
    skipped: list[str] = field(default_factory=list)  # "NAME: why nothing was done"


def transfer_files(
    engine,
    items: list[tuple[Volume, FileEntry]],
    dst: Volume,
    move: bool,
    progress: Callable[[int, int, str], None] | None = None,
) -> TransferResult:
    """Copy (or move) files into `dst`, in the order given. Each one succeeds or fails on its own."""
    result = TransferResult()
    taken = {f.name.casefold() for f in engine.list_files(dst)}
    limit = akai_names.name_limit(dst.type)
    for i, (src, f) in enumerate(items):
        if progress is not None:
            progress(i, len(items), f.name)
        if move and (src.disk, src.partition, src.index) == (dst.disk, dst.partition, dst.index):
            result.skipped.append(f"{f.name}: already in {dst.name}")
            continue
        base, ext = akai_names.split_extension(f.name)
        problem = importing.akai_file_problem(ext, dst.type)
        if problem:
            result.failed.append(f"{f.name}: {problem}")
            continue
        name_base = importing.unique_base(base, ext, 1, max(limit, len(base)), taken)
        new_name = name_base + ext
        try:
            if move and (src.disk, src.partition) == (dst.disk, dst.partition):
                final = engine.move_file(src, f, dst, new_name)
            else:
                final = engine.copy_file(src, f, dst, new_name)
                if move:
                    failed = engine.delete_files(src, [f])
                    if failed:
                        result.failed.append(f"{f.name}: copied to {dst.name}, but the original couldn't be deleted ({failed[0].split(': ', 1)[-1]})")
        except Exception as e:  # one bad file mustn't stop the rest
            result.failed.append(f"{f.name}: {e}")
            continue
        taken.add(final.casefold())
        result.done.append(final)
    return result
