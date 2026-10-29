"""Which files a damaged part of a floppy disk belongs to (Qt-free).

A floppy keeps its directory and one chain table (the FAT) in the first few blocks. Each file starts at a block named in the
directory, and the table gives the next block of the file, and so on until an end code. A damaged sector is one block (a floppy
block is one 1024-byte sector), so the files it hits are the files whose chain passes through it. A hit in the header (the
directory and chain table) is the serious case: nothing on the disk can be trusted then.

akaiutil lists the files (name, start block, size); this reads only the chain table from the flat disk image.
"""

from dataclasses import dataclass

BLOCK = 1024
_FAT_OFFSET = 64 * 24  # the table follows the 64 directory entries of 24 bytes in the header
_HEADER_BLOCKS = {800: 4, 1600: 5}  # blocks in the header of a low- and a high-density floppy (by size in blocks)
_S3000_DIRECTORY_BLOCKS = 12  # an S3000 floppy keeps its directory in 12 more blocks behind the header
_FIRST_TYPE_OFFSET = 16  # the type byte of the first directory entry (12 name bytes, 4 tags)
_S3000_FLAG = 0xFF  # ... which is 0xFF on an S3000 floppy
_END_CODES = 0x8000  # a chain entry from here up ends the file (0x8000 S900, 0xC000 others); bad and system codes are lower


@dataclass(frozen=True)
class DiskFile:
    name: str
    start_block: int
    size: int  # bytes


@dataclass(frozen=True)
class Damage:
    files: list[str]  # the files with a damaged block, in the order of the directory
    header: bool  # the directory or chain table is damaged: the whole disk is in doubt


def block_of(track: int, side: int, sector: int, sectors_per_track: int) -> int:
    """The block number of a sector (a floppy's blocks are its sectors, cylinder by cylinder, side 0 then side 1)."""
    return (track * 2 + side) * sectors_per_track + sector - 1


def header_blocks(image: bytes) -> set[int]:
    """The blocks that hold the directory and the chain table, and (S3000 floppy) the directory behind them."""
    count = _HEADER_BLOCKS.get(len(image) // BLOCK, 4)
    blocks = set(range(count))
    if len(image) > _FIRST_TYPE_OFFSET and image[_FIRST_TYPE_OFFSET] == _S3000_FLAG:
        blocks |= set(range(count, count + _S3000_DIRECTORY_BLOCKS))
    return blocks


def chain(image: bytes, start_block: int, size: int) -> list[int]:
    """The blocks of a file, from its start block by the chain table. Stops at an end code, at a block outside the disk, or
    after as many blocks as the size needs (so a damaged table can't make it loop)."""
    total = len(image) // BLOCK
    needed = max(1, -(-size // BLOCK))
    blocks, block = [], start_block
    while len(blocks) < needed and 0 <= block < total:
        blocks.append(block)
        entry = image[_FAT_OFFSET + 2 * block : _FAT_OFFSET + 2 * block + 2] if block < total else b""
        if len(entry) < 2:
            break
        nxt = int.from_bytes(entry, "little")
        if nxt >= _END_CODES:
            break
        block = nxt
    return blocks


def damage(image: bytes, bad_blocks: set[int], files: list[DiskFile]) -> Damage:
    """The files hit by these blocks, and whether the header is."""
    header = bool(bad_blocks & header_blocks(image))
    hit = [f.name for f in files if bad_blocks & set(chain(image, f.start_block, f.size))]
    return Damage(hit, header)
