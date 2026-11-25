"""Partition files: what akaiutil's `getpart` writes and `putpart` reads, a hard disk partition block for block.

`putpart` puts the file over an existing partition and does not check it against that partition: a file that is too big runs on
into the partitions after it (measured 2026-11-25, dev_docs/akaiutil-notes.md), and a smaller one makes akaiutil see the
partition as inconsistent. So everything that can be checked is checked here before it is called.
"""

from __future__ import annotations

import os
from pathlib import Path

from .engine.models import Partition
from .formatting import human_size

SUFFIX = ".part"

# A sampler partition starts with its size in blocks (16 bits, low byte first), then 98 magic fields that are 3333 * their number.
_SIZE_AT = 0
_MAGIC_AT, _MAGIC_FIELDS, _MAGIC_STEP = 2, 5, 3333

# The partition table sits in the header of the first sampler partition. `putpart` keeps the disk's own table whatever the file
# says, so a restored first partition reads back with those bytes unchanged. struct akai_parthead_s: size, magic, checksum, 100
# volume entries of 16 bytes, the FAT (0x1e00 entries of 2 bytes) and a gap of 0xf6, then struct akai_parttab_s (0x202 bytes).
TABLE_START = 2 + 98 * 2 + 4 + 100 * 16 + 0x1E00 * 2 + 0xF6
TABLE_END = TABLE_START + 0x202


def unsupported(part: Partition) -> str:
    """Why this partition can't be saved to a file or restored from one ("" if it can)."""
    if part.type.startswith("FL"):
        return "A floppy's image file is the whole disk: copy that file instead."
    if part.type not in ("HD", "DD"):  # (an S900 partition is another sort of header, and nothing here has been tried on one)
        return "Only partitions of S1000/S3000 hard disks can be saved to a file or restored from one."
    return ""


def table_range(part: Partition) -> tuple[int, int] | None:
    """The bytes of a partition that may differ after a restore (those of the disk's partition table), if it has any."""
    return None if part.is_dd or part.name != "A" else (TABLE_START, TABLE_END)


def _is_sampler_header(head: bytes) -> bool:
    return len(head) >= _MAGIC_AT + 2 * _MAGIC_FIELDS and all(
        int.from_bytes(head[_MAGIC_AT + 2 * i : _MAGIC_AT + 2 * i + 2], "little") == (_MAGIC_STEP * i) & 0xFFFF for i in range(_MAGIC_FIELDS)
    )


def problem_with_file(path: str | os.PathLike, part: Partition) -> str:
    """Why this file can't be put over the partition ("" if it looks like a copy of a partition just like it)."""
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            head = f.read(_MAGIC_AT + 2 * _MAGIC_FIELDS)
    except OSError as e:
        return f"Couldn't read the file: {e.strerror or e}."
    if size != part.size_bytes:
        return (
            f"The file is {human_size(size)} and partition {part.name} is {human_size(part.size_bytes)}. "
            "A partition file has to be exactly as big as the partition it goes into."
        )
    sampler = _is_sampler_header(head)
    if part.is_dd:
        return "That file is a sampler partition, not a direct-to-disk area." if sampler else ""
    if not sampler:
        return "That file doesn't look like a sampler partition."
    if int.from_bytes(head[_SIZE_AT : _SIZE_AT + 2], "little") != part.size_blocks:
        return "That file is a sampler partition of another size."
    return ""


COMPARE_BLOCK = 1 << 20  # (a partition can be hundreds of megabytes: compare it a block at a time)


def same_apart_from(a: str | os.PathLike, b: str | os.PathLike, skip: tuple[int, int] | None = None) -> bool:
    """Whether two files hold the same bytes, leaving out the ones in `skip` (start, end)."""
    if Path(a).stat().st_size != Path(b).stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        at = 0
        while True:
            block_a, block_b = fa.read(COMPARE_BLOCK), fb.read(COMPARE_BLOCK)
            if not block_a and not block_b:
                return True
            if skip is not None:  # (blank out the part of this block that lies in the skipped range)
                lo, hi = max(skip[0] - at, 0), max(skip[1] - at, 0)
                if lo < len(block_a) and hi > 0:
                    block_a = block_a[:lo] + block_a[hi:]
                    block_b = block_b[:lo] + block_b[hi:]
            if block_a != block_b:
                return False
            at += COMPARE_BLOCK
