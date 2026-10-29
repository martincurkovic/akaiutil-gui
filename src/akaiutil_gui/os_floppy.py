"""A boot floppy for a sampler's operating system, made from an OS file (Qt-free).

The S2000 and its relatives have no OS in ROM: they load it from a floppy (or a SCSI disk). Akai's DOS tool OSMaker made that floppy
from a `.COD` file; the sampler's own Save System writes the same 256 KB image as a `.C` file. Both go in here, and out comes a
1.6 MB flat floppy image that the rest of the app can open or save as HFE.

The layout is the one OSMaker writes (`dev_docs/os-floppy.md` has how that was read and what has not been checked): an Akai
S3000-style high-density floppy with the volume `OS DISK` and one file of the "S1000 operating system" type, 256 blocks from block
17 on. Two things on it come from the OS itself: the sampler model (a tag byte on the file) and the OS version.
"""

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from . import hfe

OS_BYTES = 0x40000  # an OS image is the 256 KB the sampler loads at address 0
BLOCK = 1024
BLOCKS = 1600  # a high-density floppy
IMAGE_BYTES = BLOCKS * BLOCK

_DIRECTORY_ENTRIES = 64
_ENTRY_BYTES = 24
_FAT_OFFSET = _DIRECTORY_ENTRIES * _ENTRY_BYTES  # the chain table follows the first 64 entries
_LABEL_OFFSET = _FAT_OFFSET + 2 * BLOCKS  # ... and the volume label follows the chain table
_FILE_ENTRY_OFFSET = 5 * BLOCK  # the S3000 directory starts behind the 5 header blocks
_FIRST_FILE_BLOCK = 17  # ... and is 12 blocks long
_RESERVED, _END = 0x4000, 0xC000  # chain table codes: a block the directory and table use, and the last block of a file
_OS_FILE_TYPE = 0x63  # "c": akaiutil's "S1000 operating system"
_LABEL_PARAMETERS = bytes([0, 0, 1, 0, 0, 0, 0x32, 9, 0x0C, 0])  # the 10 bytes after the label's version, as OSMaker writes them
_AKAI_CHARS = "0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ#+-."
VOLUME_NAME = "OS DISK"
FILE_NAME = "OS FILE"


class OsFloppyError(Exception):
    """Why a floppy can't be made; the message is meant for the user."""


@dataclass(frozen=True)
class _Model:
    name: str
    tag: int  # the sampler model, written as the fourth tag byte of the OS file
    version_offset: int  # added to the major version to make the OS version akaiutil shows (S2000 2.00 is 17.00)


# OSMaker's list, by the first three characters of a `.COD` file's name (and of the `s20V0200.COD` build name inside the file).
MODELS = {
    "S28": _Model("S2800", 0x00, 10),
    "S30": _Model("S3000", 0x01, 10),
    "S32": _Model("S3200", 0x02, 10),
    "C30": _Model("CD3000", 0x04, 10),
    "C31": _Model("CD3000XL", 0x08, 15),
    "S20": _Model("S2000", 0x0A, 15),
    "S31": _Model("S3000XL", 0x0B, 15),
    "S35": _Model("S3200XL", 0x0C, 15),
}
_NAME = re.compile(r"([SC]\d\d)(\d\d)(\d\d)\d?")  # S200200: model, major version, minor version, optional beta number
_BUILD_NAME = re.compile(rb"([SC]\d\d)V(\d\d)(\d\d)\.COD", re.IGNORECASE)  # the linker's s20V0200.COD inside the file


@dataclass(frozen=True)
class OsInfo:
    model: str
    tag: int
    major: int
    minor: int
    os_version: tuple[int, int]  # (low byte, high byte) as the directory and the label keep it

    @property
    def description(self) -> str:
        return f"{self.model} OS {self.major}.{self.minor:02d}"


def _info(prefix: str, major: str, minor: str) -> OsInfo | None:
    model = MODELS.get(prefix.upper())
    if model is None:
        return None
    major_n, minor_n = int(major), int(minor)
    return OsInfo(model.name, model.tag, major_n, minor_n, (minor_n, (major_n + model.version_offset) & 0xFF))


def identify(data: bytes, file_name: str) -> OsInfo:
    """Which sampler and version an OS file is for: from its name if it is named as OSMaker wants (S200200.COD), otherwise from the
    build name inside it (a `.C` the sampler saved has no such name)."""
    if len(data) != OS_BYTES:
        raise OsFloppyError(f"An OS file is {OS_BYTES // 1024} KB; this one is {len(data) / 1024:g} KB.")
    named = _NAME.fullmatch(Path(file_name).stem.upper())
    if named and (info := _info(*named.groups())):
        return info
    for match in _BUILD_NAME.finditer(data):
        if match.group(2) + match.group(3) != b"0000" and (info := _info(*(g.decode() for g in match.groups()))):  # (V0000: a leftover)
            return info
    raise OsFloppyError("Can't tell which sampler this OS is for. Name the file like OSMaker's, S200200.COD (S20 = S2000, then version 02.00).")


def _akai_name(text: str) -> bytes:
    return bytes(_AKAI_CHARS.index(c) for c in text.ljust(12))


def build(data: bytes, info: OsInfo) -> bytes:
    """The flat 1.6 MB floppy image holding the OS."""
    if len(data) != OS_BYTES:
        raise OsFloppyError(f"An OS file is {OS_BYTES // 1024} KB; this one is {len(data) / 1024:g} KB.")
    image = bytearray(IMAGE_BYTES)
    blocks = OS_BYTES // BLOCK
    low, high = info.os_version

    image[16] = 0xFF  # the first header entry's type byte: this is an S3000-style floppy
    chain = [_RESERVED] * _FIRST_FILE_BLOCK + [0] * (BLOCKS - _FIRST_FILE_BLOCK)
    for i in range(blocks):
        block = _FIRST_FILE_BLOCK + i
        chain[block] = block + 1 if i < blocks - 1 else _END
    struct.pack_into(f"<{BLOCKS}H", image, _FAT_OFFSET, *chain)
    image[_LABEL_OFFSET : _LABEL_OFFSET + 26] = _akai_name(VOLUME_NAME) + bytes([0, 0, low, high]) + _LABEL_PARAMETERS

    entry = _akai_name(FILE_NAME) + bytes([0, 0, 0, info.tag, _OS_FILE_TYPE]) + OS_BYTES.to_bytes(3, "little")
    entry += _FIRST_FILE_BLOCK.to_bytes(2, "little") + bytes([low, high])
    image[_FILE_ENTRY_OFFSET : _FILE_ENTRY_OFFSET + _ENTRY_BYTES] = entry

    start = _FIRST_FILE_BLOCK * BLOCK
    image[start : start + OS_BYTES] = data
    return bytes(image)


def read(path: str | Path) -> tuple[bytes, OsInfo]:
    """An OS file's bytes, and which sampler and version it is for."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            data = f.read(OS_BYTES + 1)  # (one more than an OS holds is enough to see that it is not one)
    except OSError as e:
        raise OsFloppyError(f"Couldn't read {path.name}: {e.strerror or e}.") from e
    return data, identify(data, path.name)


def save(path: str | Path, image: bytes) -> None:
    """Write the floppy as an HFE file if the name ends in .hfe, as a flat image otherwise."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".hfe":
            hfe.save(path, image)
        else:
            path.write_bytes(image)
    except (OSError, hfe.HfeError) as e:
        raise OsFloppyError(f"Couldn't write {path.name}: {getattr(e, 'strerror', None) or e}") from e
