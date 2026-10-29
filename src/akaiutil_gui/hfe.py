"""Akai floppy disks in HxC's `.hfe` container, turned into the flat images akaiutil opens, and back (Qt-free).

akaiutil only knows flat images: the disk's 1024-byte blocks one after another. An HFE file holds the raw MFM bitstream of every
track (what a Gotek or HxC emulator plays to the sampler), so before akaiutil can look at one the sectors have to be decoded out
of it, and after an edit they have to be put back. Only what the Akai floppies need is here: HFE v1, MFM, 80 cylinders, 2 sides,
sectors of 1024 bytes (10 per track on a high-density disk, 5 on a low-density one). `dev_docs/hfe-plan.md` has the format facts,
what was measured on real disks and what has not been checked.

Two ways to write:

- `patch` takes an HFE file and a flat image and changes only the sectors whose data differ, leaving the header, gaps, sector order
  and everything else bit for bit as they were. This is what saving an edited disk does. With an unchanged image the result is
  the original file.
- `encode` builds a new file from a flat image alone, high or low density. The low-density layout is HxC's own for Akai S900 disks
  and is reproduced byte for byte from its files; the high-density one is a real sampler's (an S3000XL's). Both are checked against
  files this module did not write, but no written file has been tried in a sampler yet.

Sectors are found by searching the cell stream for sync marks, not by stepping in bytes: disks rewritten by a real drive have
data fields that do not sit on the byte grid of the rest of the track.
"""

import binascii
import os
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SIGNATURE_V1 = b"HXCPICFE"
SIGNATURE_V3 = b"HXCHFEV3"
SECTOR_SIZE = 1024
CYLINDERS = 80
SIDES = 2
DD_SECTORS, HD_SECTORS = 5, 10
IMAGE_BYTES = {DD_SECTORS: CYLINDERS * SIDES * DD_SECTORS * SECTOR_SIZE, HD_SECTORS: CYLINDERS * SIDES * HD_SECTORS * SECTOR_SIZE}

_GAP1, _GAP2 = 50, 22
_SYNC_A1 = "0100010010001001"  # 0xA1 with the clock bit missing from its fifth data bit
_SYNC_C2 = "0101001000100100"  # 0xC2, likewise
_ENCODING_MFM = 0
_FILLER = bytes([0x49, 0x2A])  # what the unused end of a track's last block holds


@dataclass(frozen=True)
class _Layout:
    """What a new file's tracks are made of."""

    sectors: int
    side_bytes: int  # stored bytes per side of a track (8 cells each): one 300 RPM turn
    gap3: int
    bitrate: int  # kbit/s, as the header says
    mode: int  # the header's interface mode
    first_sectors: tuple[int, ...]  # the first sector on a track, by track number (repeating)
    gap4a: int = 80  # filler bytes before the index mark


# High density, as the sampler's own disks have it (the Acrobatics S3000XL set; see hfe-plan.md): standard IBM MFM at 500 kbit/s,
# a long gap 3, and the first sector on a track alternating between 1 and 6.
_HD = _Layout(sectors=HD_SECTORS, side_bytes=25168, gap3=139, bitrate=500, mode=1, first_sectors=(1, 6))
# Low density, as HxC's own Akai S900 disks have it (the SD HxC "QuickInstall" pack and a library of ~1000 S900 disks; see
# hfe-plan.md): sectors in order on every track, a short gap 3. Reproduced byte for byte from those files by the tests.
_DD = _Layout(sectors=DD_SECTORS, side_bytes=12500, gap3=86, bitrate=250, mode=12, first_sectors=(1,), gap4a=230)
# High density as HxC lays it out for the S950 (the rotation is akaiutil's own formula, offset 6 and skew 4, and gap 3 is 0x74).
# Only used by the tests, to check the encoder against those files; `encode` writes the sampler's layout above.
_HD_HXC = _Layout(sectors=HD_SECTORS, side_bytes=25008, gap3=116, bitrate=500, mode=13, first_sectors=(7, 3, 9, 5, 1), gap4a=230)


class HfeError(Exception):
    """The reason a file could not be read or written, worded for the user."""


@dataclass(frozen=True)
class Decoded:
    image: bytes  # the flat disk image: cylinder by cylinder, side 0 then side 1, sectors in order
    sectors_per_track: int
    problems: list[str] = field(default_factory=list)  # sectors that were bad, missing or doubled (empty for a good disk)
    missing: list[tuple[int, int, int]] = field(default_factory=list)  # (track, side, sector) of each one left blank in the image

    @property
    def density(self) -> str:
        return "HD" if self.sectors_per_track == HD_SECTORS else "DD"


# ------------------------------------------------------------------------------------------------------------------- MFM
def _crc(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)  # CRC-16/CCITT, as floppy controllers use


_BITS = [format(b, "08b")[::-1] for b in range(256)]  # a stored byte holds its cells least significant bit first
_FROM_BITS = {bits: b for b, bits in enumerate(_BITS)}
_MFM = {}
for _prev in (0, 1):
    for _byte in range(256):
        _cells = []
        _last = _prev
        for _shift in range(7, -1, -1):
            _bit = (_byte >> _shift) & 1
            _cells.append("1" if (_last == 0 and _bit == 0) else "0")  # a clock cell is set between two 0 data bits
            _cells.append(str(_bit))
            _last = _bit
        _MFM[_prev, _byte] = "".join(_cells)


class _Cells:
    """A run of cells being built, remembering the last data bit because it decides the next clock cell."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.last = 0
        self.length = 0  # in bytes

    def add(self, data: bytes) -> None:
        for b in data:
            cells = _MFM[self.last, b]
            self.parts.append(cells)
            self.last = b & 1
        self.length += len(data)

    def sync(self, pattern: str, count: int = 3) -> None:
        self.parts.append(pattern * count)
        self.last = int(pattern[-1])
        self.length += count

    def text(self) -> str:
        return "".join(self.parts)


def _read_bytes(cells: str, pos: int, count: int) -> bytes | None:
    """`count` bytes of data from the cells at `pos` (every second cell is data)."""
    bits = cells[pos + 1 : pos + 16 * count : 2]
    if len(bits) < 8 * count:
        return None
    return int(bits, 2).to_bytes(count, "big")


# ----------------------------------------------------------------------------------------------------------- the container
def is_hfe(path: str | os.PathLike) -> bool:
    """Whether the file starts like an HFE file (v1 or v3). A quick look, nothing is validated."""
    try:
        with open(path, "rb") as f:
            return f.read(8) in (SIGNATURE_V1, SIGNATURE_V3)
    except OSError:
        return False


@dataclass(frozen=True)
class _Track:
    offset: int  # in bytes from the start of the file
    length: int  # both sides, as the track table says
    blocks: int  # 512-byte blocks it takes


def _check_header(data: bytes) -> int:
    """Refuse a file that can't be an Akai floppy by its header alone; returns where the track table is (in 512-byte blocks)."""
    if data[:8] == SIGNATURE_V3:
        raise HfeError("This is an HFE v3 file, which isn't supported yet.")
    if data[:8] != SIGNATURE_V1:
        raise HfeError("Not an HFE file.")
    if len(data) < 1024:
        raise HfeError("The HFE file is cut short.")
    _, _, cylinders, sides, encoding, _, _, _, _, table, _, _ = struct.unpack_from("<8sBBBBHHBBHBB", data, 0)
    if encoding != _ENCODING_MFM:
        raise HfeError("This HFE file isn't MFM-encoded, so it isn't an Akai disk.")
    if (cylinders, sides) != (CYLINDERS, SIDES):
        raise HfeError(f"This HFE file has {cylinders} tracks on {sides} side(s); an Akai disk has {CYLINDERS} on {SIDES}.")
    return table


def problem(path: str | os.PathLike) -> str | None:
    """Why this HFE file can't be an Akai floppy, judged from its header alone (None: it might be; decoding settles it)."""
    try:
        with open(path, "rb") as f:
            head = f.read(1024)
    except OSError as e:
        return e.strerror or str(e)
    try:
        _check_header(head)
    except HfeError as e:
        return str(e)
    return None


def _tracks(data: bytes) -> list[_Track]:
    table = _check_header(data)
    tracks = []
    for t in range(CYLINDERS):
        if table * 512 + 4 * t + 4 > len(data):
            raise HfeError("The HFE file is cut short (its track list is incomplete).")
        offset, length = struct.unpack_from("<HH", data, table * 512 + 4 * t)
        blocks = (length + 511) // 512
        if length == 0 or (offset + blocks) * 512 > len(data):
            raise HfeError(f"The HFE file is cut short (track {t} is missing or incomplete).")
        tracks.append(_Track(offset * 512, length, blocks))
    return tracks


def _side_cells(data: bytes, track: _Track, side: int) -> str:
    """One side of a track as cells: each 512-byte block holds 256 bytes of side 0 then 256 of side 1."""
    raw = b"".join(
        data[track.offset + k * 512 + side * 256 : track.offset + k * 512 + side * 256 + 256] for k in range(track.blocks)
    )
    return "".join(map(_BITS.__getitem__, raw))


def _store_side(out: bytearray, track: _Track, side: int, cells: str) -> None:
    raw = bytes(_FROM_BITS[cells[i : i + 8]] for i in range(0, len(cells), 8))
    for k in range(track.blocks):
        at = track.offset + k * 512 + side * 256
        out[at : at + 256] = raw[k * 256 : k * 256 + 256]


# ---------------------------------------------------------------------------------------------------------------- decode
def _find_sectors(cells: str) -> tuple[dict[int, tuple[bytes, int]], list[str]]:
    """The sectors on one side of a track: {sector number: (data, cell index of its first data cell)}, and what went wrong."""
    found: dict[int, tuple[bytes, int]] = {}
    problems: list[str] = []
    pos, current = 0, None
    while (at := cells.find(_SYNC_A1 * 3, pos)) >= 0:
        after = at + 48
        mark = _read_bytes(cells, after, 1)
        if mark is None:
            break
        if mark == b"\xfe":
            head = _read_bytes(cells, after + 16, 6)
            if head and _crc(b"\xa1\xa1\xa1\xfe" + head[:4]) == int.from_bytes(head[4:], "big"):
                current = head[2] if head[3] == 3 else None  # (R, if the size code says 1024 bytes)
            else:
                problems.append("a sector header with a bad checksum")
                current = None
        elif mark == b"\xfb" and current is not None:
            body = _read_bytes(cells, after + 16, SECTOR_SIZE + 2)
            if body and _crc(b"\xa1\xa1\xa1\xfb" + body[:SECTOR_SIZE]) == int.from_bytes(body[SECTOR_SIZE:], "big"):
                if current in found:
                    problems.append(f"sector {current} twice")
                found[current] = (body[:SECTOR_SIZE], after + 16)
            else:
                problems.append(f"sector {current} with a bad checksum")
            current = None
        pos = after
    return found, problems


def decode(data: bytes, strict: bool = True) -> Decoded:
    """The flat image in an HFE file's bytes. With `strict` a disk with any bad, missing or doubled sector is refused (with the
    tracks named); without it the gaps are zero-filled and listed in `problems`."""
    tracks = _tracks(data)
    sectors: dict[tuple[int, int, int], bytes] = {}
    problems: list[str] = []
    for t, track in enumerate(tracks):
        for side in range(SIDES):
            found, bad = _find_sectors(_side_cells(data, track, side))
            problems += [f"track {t} side {side}: {text}" for text in bad]
            sectors.update({(t, side, r): body for r, (body, _) in found.items()})
    per_track = max((r for _, _, r in sectors), default=0)
    if per_track not in (DD_SECTORS, HD_SECTORS):
        raise HfeError("This HFE file doesn't hold an Akai floppy disk (no 5 or 10 sectors of 1024 bytes per track).")
    blank = bytes(SECTOR_SIZE)
    parts, missing = [], []
    for t in range(CYLINDERS):
        for side in range(SIDES):
            for r in range(1, per_track + 1):
                if (t, side, r) not in sectors:
                    problems.append(f"track {t} side {side}: sector {r} missing")
                    missing.append((t, side, r))
                parts.append(sectors.get((t, side, r), blank))
    if strict and problems:
        more = f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""
        raise HfeError("The HFE file is damaged: " + "; ".join(problems[:3]) + more + ".")
    return Decoded(b"".join(parts), per_track, problems, missing)


def read(path: str | os.PathLike, strict: bool = True) -> Decoded:
    try:
        return decode(Path(path).read_bytes(), strict)
    except OSError as e:
        raise HfeError(f"Couldn't read {Path(path).name}: {e.strerror or e}.") from e


# ----------------------------------------------------------------------------------------------------------------- patch
def patch(original: bytes, image: bytes) -> bytes:
    """A copy of the HFE file `original` carrying the flat `image`: only sectors whose data differ are rewritten (data and
    checksum), the rest of the file is untouched. Refuses a damaged original, since a sector it can't read can't be changed."""
    before = decode(original)
    if len(image) != len(before.image):
        raise HfeError(f"The image is {len(image)} bytes but the disk in the HFE file is {len(before.image)}.")
    out = bytearray(original)
    spt = before.sectors_per_track
    for t, track in enumerate(_tracks(original)):
        for side in range(SIDES):
            first = (t * SIDES + side) * spt * SECTOR_SIZE
            if image[first : first + spt * SECTOR_SIZE] == before.image[first : first + spt * SECTOR_SIZE]:
                continue
            cells = _side_cells(original, track, side)
            found, _ = _find_sectors(cells)
            # (last sector first, so that the cell indexes of the ones still to do stay valid even if a length ever changed)
            for r, (old, start) in sorted(found.items(), key=lambda item: -item[1][1]):
                new = image[first + (r - 1) * SECTOR_SIZE : first + r * SECTOR_SIZE]
                if new == old:
                    continue
                run = _Cells()
                run.last = 1  # (the data mark 0xFB ends in a 1)
                run.add(new + _crc(b"\xa1\xa1\xa1\xfb" + new).to_bytes(2, "big"))
                encoded = run.text()
                end = start + len(encoded)
                clock = "1" if (run.last == 0 and cells[end + 1] == "0") else "0"  # (the byte after the checksum)
                cells = cells[:start] + encoded + clock + cells[end + 1 :]
            _store_side(out, track, side, cells)
    return bytes(out)


# ---------------------------------------------------------------------------------------------------------------- encode
def _new_side(image: bytes, t: int, side: int, layout: _Layout) -> str:
    run = _Cells()
    run.add(b"\x4e" * layout.gap4a + b"\x00" * 12)
    run.sync(_SYNC_C2)
    run.add(b"\xfc" + b"\x4e" * _GAP1)
    first = layout.first_sectors[t % len(layout.first_sectors)]
    for i in range(layout.sectors):
        r = (first - 1 + i) % layout.sectors + 1
        header = bytes([t, side, r, 3])
        run.add(b"\x00" * 12)
        run.sync(_SYNC_A1)
        run.add(b"\xfe" + header + _crc(b"\xa1\xa1\xa1\xfe" + header).to_bytes(2, "big") + b"\x4e" * _GAP2 + b"\x00" * 12)
        run.sync(_SYNC_A1)
        at = ((t * SIDES + side) * layout.sectors + r - 1) * SECTOR_SIZE
        data = image[at : at + SECTOR_SIZE]
        run.add(b"\xfb" + data + _crc(b"\xa1\xa1\xa1\xfb" + data).to_bytes(2, "big") + b"\x4e" * layout.gap3)
    run.add(b"\x4e" * (layout.side_bytes // 2 - run.length))
    return run.text()


def _encode(image: bytes, layout: _Layout) -> bytes:
    blocks = (layout.side_bytes + 255) // 256  # (a block holds 256 bytes of each side)
    out = bytearray(b"\xff" * 512)
    out[:20] = struct.pack("<8sBBBBHHBBH", SIGNATURE_V1, 0, CYLINDERS, SIDES, _ENCODING_MFM, layout.bitrate, 0, layout.mode, 1, 1)
    out += b"\xff" * 512 + bytes(512 * CYLINDERS * blocks)  # (HxC fills the unused end of the track list with 0xFF)
    for t in range(CYLINDERS):
        track = _Track((2 + t * blocks) * 512, layout.side_bytes * 2, blocks)
        struct.pack_into("<HH", out, 512 + 4 * t, track.offset // 512, track.length)
        for side in range(SIDES):
            cells = _new_side(image, t, side, layout)
            filler = "".join(_BITS[_FILLER[i % 2]] for i in range(blocks * 256 - len(cells) // 8))
            _store_side(out, track, side, cells + filler)
    return bytes(out)


def encode(image: bytes) -> bytes:
    """A new HFE v1 file from a flat floppy image: high density (1.6 MB) or low density (800 KB)."""
    for layout in (_HD, _DD):
        if len(image) == IMAGE_BYTES[layout.sectors]:
            return _encode(image, layout)
    raise HfeError(f"A floppy image is {IMAGE_BYTES[DD_SECTORS]} or {IMAGE_BYTES[HD_SECTORS]} bytes; this one is {len(image)}.")


# ------------------------------------------------------------------------------------------------------------------ save
def make(image: bytes, template: bytes | None = None) -> bytes:
    """The bytes of an HFE file holding `image`: the `template` file patched with it, or a new file without one."""
    return patch(template, image) if template is not None else encode(image)


def save(path: str | os.PathLike, image: bytes, template: bytes | None = None) -> None:
    """Write `image` as an HFE file at `path` (made as `make` does). The result is decoded again and compared before it replaces
    anything, so a failure leaves the file as it was; the file is written beside the target and moved into place."""
    path = Path(path)
    result = make(image, template)
    if decode(result).image != image:
        raise HfeError(f"Writing {path.name} didn't come out right, so it was left as it was.")
    try:
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(result)
            if path.exists():
                os.chmod(temp, path.stat().st_mode & 0o7777)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
    except OSError as e:
        raise HfeError(f"Couldn't write {path.name}: {e.strerror or e}.") from e


def update(path: str | os.PathLike, image: bytes) -> None:
    """Put `image` into the HFE file that is at `path`, changing only the sectors that differ."""
    try:
        template = Path(path).read_bytes()
    except OSError as e:
        raise HfeError(f"Couldn't read {Path(path).name}: {e.strerror or e}.") from e
    save(path, image, template)
