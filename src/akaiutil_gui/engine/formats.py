"""The kinds of disk akaiutil can format, and what each one needs (sizes in MB unless noted)."""

import math
from dataclasses import dataclass

MB = 1024 * 1024
HD_BLOCK = 8192  # a hard disk block
HD_MAX_MB = 512  # 16-bit block numbers: akaiutil's limit for a hard disk image
PARTITION_MAX_MB = 60  # the biggest sampler partition
PARTITION_MIN_MB = 1
MAX_PARTITIONS = 18
HD9_MAX_MB = 64  # S900 hard disks are small (about 64 MB at most)


@dataclass(frozen=True)
class DiskFormat:
    key: str
    label: str
    command: str  # the akaiutil command that does it
    floppy_kb: int | None = None  # a floppy has a fixed size; None for a hard disk
    partitions: bool = True  # a hard disk of this kind is split into partitions of a size you choose (a floppy and an S900 disk aren't)

    @property
    def is_floppy(self) -> bool:
        return self.floppy_kb is not None

    @property
    def max_mb(self) -> int:
        return HD9_MAX_MB if self.command == "formatharddisk9" else HD_MAX_MB

    def image_bytes(self, size_mb: int = 0) -> int:
        return self.floppy_kb * 1024 if self.floppy_kb is not None else size_mb * MB

    def akaiutil_command(self, partition_mb: int = PARTITION_MAX_MB, total_mb: int | None = None) -> str:
        """The command line that formats a disk this way (the whole image, or `total_mb` of it)."""
        if self.is_floppy:
            return self.command
        if not self.partitions:
            return f"{self.command} {total_mb}M" if total_mb else self.command
        total = f" {total_mb}M" if total_mb else ""
        return f"{self.command} {partition_mb}M{total}"


FORMATS: tuple[DiskFormat, ...] = (
    DiskFormat("hd3", "S3000 hard disk", "formatharddisk3"),
    DiskFormat("hd1", "S1000 hard disk", "formatharddisk1"),
    DiskFormat("hd3cd", "CD3000 CD-ROM", "formatharddisk3cd"),
    DiskFormat("hd9", "S900 hard disk", "formatharddisk9", partitions=False),
    DiskFormat("fl3h", "S3000 floppy (1.6 MB)", "formatfloppyh3", floppy_kb=1600, partitions=False),
    DiskFormat("fl3l", "S3000 floppy (800 KB)", "formatfloppyl3", floppy_kb=800, partitions=False),
    DiskFormat("fl1h", "S1000 floppy (1.6 MB)", "formatfloppyh1", floppy_kb=1600, partitions=False),
    DiskFormat("fl1l", "S1000 floppy (800 KB)", "formatfloppyl1", floppy_kb=800, partitions=False),
    DiskFormat("fl9h", "S900 floppy (1.6 MB)", "formatfloppyh9", floppy_kb=1600, partitions=False),
    DiskFormat("fl9l", "S900 floppy (800 KB)", "formatfloppyl9", floppy_kb=800, partitions=False),
)

_BY_KEY = {f.key: f for f in FORMATS}


def by_key(key: str) -> DiskFormat:
    return _BY_KEY[key]


def formats_for_disk(disk_type: str) -> list[DiskFormat]:
    """What an existing disk image can be reformatted as: a floppy keeps its density, a hard disk any hard disk kind."""
    if disk_type == "FLL":
        return [f for f in FORMATS if f.floppy_kb == 800]
    if disk_type == "FLH":
        return [f for f in FORMATS if f.floppy_kb == 1600]
    return [f for f in FORMATS if not f.is_floppy]


def partition_layout(total_mb: float, partition_mb: int) -> tuple[int, float]:
    """(how many partitions, how many MB they cover) when `total_mb` is cut into `partition_mb` pieces."""
    total = min(total_mb, HD_MAX_MB)
    count = min(MAX_PARTITIONS, max(1, math.ceil(total / partition_mb)))
    return count, min(total, count * partition_mb)
