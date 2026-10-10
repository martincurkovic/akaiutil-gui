"""Parsers for akaiutil's human-readable listings.

This is the fragile half of the subprocess approach: akaiutil prints
fixed-width tables meant for people, so every parser here is tied to the
printf formats in akaiutil.c / akaiutil_file.c (4.6.x). The tests pin the
exact text. A ctypes/cffi engine would make this module disappear.
"""

import math
import re

from .models import CatalogEntry, Disk, FileEntry, Partition, Volume, VolumeUsage

_HEX = r"0x[0-9a-fA-F]+"

# akai_disk_info(): "   0    HD    18   0x2000   0x4e20   156.2     0x2cca    89.6   57.3"
_DISK_RE = re.compile(
    rf"^\s*(\d+)\s+(\S+)\s+(\d+)\s+({_HEX})\s+({_HEX})\s+([\d.]+)\s+({_HEX})\s+([\d.]+)\s+([\d.]+)\s*$"
)
# akai_part_info(): "   A    HD   0x0000    0x0280      5.0     0x027d     5.0   99.5"
_PART_RE = re.compile(
    rf"^\s*(\S+)\s+(\S+)\s+{_HEX}\s+({_HEX})\s+([\d.]+)\s+({_HEX})\s+([\d.]+)\s+([\d.]+)\s*$"
)


def _table_rows(text: str):
    """Lines between the '-----' rule lines of a listing."""
    inside = False
    for line in text.splitlines():
        if line.startswith("-----"):
            if inside:
                return
            inside = True
            continue
        if inside:
            yield line


def parse_disks(text: str) -> list[Disk]:
    disks = []
    for line in _table_rows(text):
        m = _DISK_RE.match(line)
        if m:
            idx, typ, parts, blksize, tot_blks, tot_mb, free_blks, free_mb, pct = m.groups()
            disks.append(
                Disk(
                    index=int(idx),
                    type=typ,
                    partitions=int(parts),
                    block_size=int(blksize, 16),
                    total_blocks=int(tot_blks, 16),
                    free_blocks=int(free_blks, 16),
                    total_mb=float(tot_mb),
                    free_mb=float(free_mb),
                    free_pct=float(pct),
                )
            )
    return disks


def parse_partitions(text: str, disk: int, block_size: int) -> list[Partition]:
    """block_size is the disk's: partition sizes are printed in its blocks."""
    parts = []
    for line in _table_rows(text):
        m = _PART_RE.match(line)
        if m:
            name, typ, size_blks, size_mb, free_blks, free_mb, pct = m.groups()
            parts.append(
                Partition(
                    disk=disk,
                    name=name,
                    type=typ,
                    block_size=block_size,
                    size_blocks=int(size_blks, 16),
                    free_blocks=int(free_blks, 16),
                    size_mb=float(size_mb),
                    free_mb=float(free_mb),
                    free_pct=float(pct),
                )
            )
    return parts


def parse_volumes(text: str, disk: int, partition: str) -> list[Volume]:
    # akai_vol_info(): "%3u   %-12s   " + lnum/alias + "    0x%04x  " + osver + type
    # Volume names can contain spaces, so slice the fixed-width name column
    # instead of splitting on whitespace.
    vols = []
    for line in _table_rows(text):
        if len(line) < 18 or not line[:3].strip().isdigit():
            continue
        rest = line[18:].split()
        if len(rest) < 4:
            continue
        lnum, start, osver, typ = rest[0], rest[1], rest[2], rest[-1]
        vols.append(Volume(disk, partition, int(line[:3]), line[6:18].rstrip(), lnum, start, osver, typ))
    return vols


def parse_volume_limit(text: str) -> int | None:
    """The most volumes (or files) the listing's container can hold: "total:   3 volume(s) (max. 100)"."""
    m = re.search(r"\(max\.\s+(\d+)\)", text)
    return int(m.group(1)) if m else None


def parse_files(text: str) -> list[FileEntry]:
    # akaiutil_file.c: "%3u  %-16s %9u    0x%04x  " + osver/uncompr + tags
    files = []
    for line in _table_rows(text):
        if len(line) < 22 or not line[:3].strip().isdigit():
            continue
        rest = line[21:].split()
        if len(rest) < 2 or not rest[0].isdigit():
            continue
        files.append(
            FileEntry(
                index=int(line[:3]),
                name=line[5:21].rstrip(),
                size=int(rest[0]),
                start_block=rest[1],
                os_version=rest[2] if len(rest) > 2 else "",
                tags=" ".join(rest[3:]),
            )
        )
    return files


def parse_tag_names(text: str) -> dict[int, str]:
    """`lstags`: the names of a partition's tags ({1: "TAG A", ...}), empty if it has none ("no tags present")."""
    names = {}
    for line in _table_rows(text):
        m = re.match(r"^\s*(\d+)\s{2}(.*)$", line)
        if m:
            names[int(m.group(1))] = m.group(2).strip()
    return names


_PART_HEADER_RE = re.compile(r"^/disk(\d+)/([^/]+)$")
_VOL_HEADER_RE = re.compile(r"^/disk(\d+)/([^/]+)/(.+)$")


def parse_dirrec(text: str, disk: int, block_size: int) -> tuple[list[Partition], list[VolumeUsage], list[CatalogEntry]]:
    """`dirrec` at disk level: the partition table, then for every partition its
    volume table followed by one file listing per volume.

    Volume sizes are the files rounded up to whole blocks, because that is how
    akaiutil allocates them (checked against the free-block counts).
    """
    partitions = parse_partitions(text, disk, block_size)
    block = {p.name: p.block_size for p in partitions}
    lines = text.splitlines()

    def section_end(start: int) -> int:  # index of the "total:" line closing a table
        for j in range(start, len(lines)):
            if lines[j].startswith("total:"):
                return j
        return len(lines) - 1

    tables: dict[str, list[Volume]] = {}  # per partition, volumes still waiting for their file listing
    usages: list[VolumeUsage] = []
    catalog: list[CatalogEntry] = []
    i = 0
    while i < len(lines) - 1:
        head, nxt = lines[i], lines[i + 1]
        m = _PART_HEADER_RE.match(head)
        if m and nxt.startswith("vnr"):
            end = section_end(i + 1)
            vols = parse_volumes("\n".join(lines[i + 1 : end + 1]), int(m.group(1)), m.group(2))
            tables[m.group(2)] = [v for v in vols if v.type != "INACT"]
            i = end + 1
            continue
        m = _VOL_HEADER_RE.match(head)
        if m and nxt.startswith("fnr"):
            end = section_end(i + 1)
            files = parse_files("\n".join(lines[i + 1 : end + 1]))
            part, name = m.group(2), m.group(3).rstrip()
            waiting = tables.get(part, [])
            match = next((v for v in waiting if v.name.rstrip() == name), None)
            if match is not None:
                waiting.remove(match)
                bs = block.get(part) or block_size
                used = sum(math.ceil(f.size / bs) * bs for f in files)
                usages.append(VolumeUsage(disk, part, match.index, match.name, used, len(files), sum(f.size for f in files)))
                catalog.extend(CatalogEntry(match, f) for f in files)
            i = end + 1
            continue
        i += 1
    return partitions, usages, catalog


def split_infoall(text: str) -> dict[int, str]:
    """`infoall` prints each file's `infoi` block one after another. Returns {file number: its block}."""
    lines = text.splitlines()
    starts = [i for i in range(len(lines) - 1) if lines[i].startswith("/") and lines[i + 1].startswith("fnr:")]
    blocks: dict[int, str] = {}
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = "\n".join(lines[i:end]).rstrip()
        number = lines[i + 1].split(":", 1)[1].strip()
        if number.isdigit():
            blocks[int(number)] = block
    return blocks
