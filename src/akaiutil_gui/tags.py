"""File tags (Qt-free).

A hard disk partition has 26 tag names ("TAG A"...), and every file in its S3000 volumes can carry up to four of them,
stored as numbers. A copy that goes through akaiutil's `put` loses them, and akaiutil's own tag commands (settagi...) crash
in 4.6.8. What does carry tags is a tar import: akaiutil's tar export writes a file's tags into the entry's link-name field and
its import reads them back. So a tagged file is copied by building a one-file tar and importing that.

Another partition may name its tags differently, so a tag is matched by its name, not its number.
"""

import io
import tarfile

TAR_BLOCK = 512
_LINKNAME = slice(157, 257)  # struct tar_head_s in akaiutil_tar.h
_DEVMAJOR = slice(329, 337)  # akaiutil keeps the file's OS version here
_CHECKSUM = slice(148, 156)


def map_tags(names: tuple[str, ...] | list[str], destination: dict[int, str]) -> tuple[list[int], list[str]]:
    """Which of the destination's tags stand for these tag names: (their numbers, the names it has no tag for)."""
    by_name: dict[str, int] = {}
    for number, name in sorted(destination.items()):
        by_name.setdefault(name.strip().casefold(), number)
    numbers, missing = [], []
    for name in names:
        wanted = name.strip().casefold()
        number = by_name.get(wanted) if wanted else None
        if number is None:
            missing.append(name.strip() or "(unnamed)")
        elif number not in numbers:
            numbers.append(number)
    return numbers, missing


def osver_value(text: str) -> int | None:
    """akaiutil's number for an OS version column: "17.00" is 0x1100; a bare number is already the value."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    major, dot, minor = text.partition(".")
    if dot and major.isdigit() and minor.isdigit() and int(major) < 100 and int(minor) < 100:
        return (int(major) << 8) | int(minor)
    return None


def tagged_file_tar(name: str, data: bytes, osver: int, tags: list[int]) -> bytes:
    """A tar holding one file, in the dialect akaiutil's tar import reads: its OS version and up to four tag numbers."""
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    header = bytearray(info.tobuf(tarfile.USTAR_FORMAT, "ascii", "strict"))
    link = b"\0T" + bytes(tags[:4]).ljust(4, b"\0")
    header[_LINKNAME] = link.ljust(100, b"\0")
    header[_DEVMAJOR] = f"{osver:07o}".encode().ljust(8, b"\0")
    header[_CHECKSUM] = b" " * 8
    header[_CHECKSUM] = f"{sum(header):06o}".encode() + b"\0 "
    out = io.BytesIO()
    out.write(header)
    out.write(data)
    out.write(b"\0" * (-len(data) % TAR_BLOCK))
    out.write(b"\0" * (TAR_BLOCK * 2))
    return out.getvalue()
