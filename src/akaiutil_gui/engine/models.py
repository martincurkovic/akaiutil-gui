"""Plain data objects the engine hands to the UI.

Nothing here knows about Qt or about how the data was obtained, so the
subprocess-backed engine can later be swapped for a ctypes/cffi one that
fills in the same objects.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Disk:
    index: int
    type: str  # "HD", "HD9", "FLL", "FLH", ... as printed by akaiutil
    partitions: int
    block_size: int
    total_blocks: int  # the whole image, including any space not in a partition
    free_blocks: int  # free space inside the partitions
    total_mb: float
    free_mb: float
    free_pct: float

    @property
    def path(self) -> str:
        return f"/disk{self.index}"

    @property
    def total_bytes(self) -> int:
        return self.total_blocks * self.block_size


@dataclass(frozen=True)
class Partition:
    disk: int
    name: str  # "A".."Z", or "DD"/"DD1".. for direct-to-disk recording areas
    type: str
    block_size: int
    size_blocks: int
    free_blocks: int
    size_mb: float
    free_mb: float
    free_pct: float

    @property
    def is_dd(self) -> bool:
        return self.name.startswith("DD")

    @property
    def path(self) -> str:
        return f"/disk{self.disk}/{self.name}"

    @property
    def size_bytes(self) -> int:
        return self.size_blocks * self.block_size

    @property
    def free_bytes(self) -> int:
        return self.free_blocks * self.block_size

    @property
    def used_bytes(self) -> int:
        return self.size_bytes - self.free_bytes


@dataclass(frozen=True)
class Volume:
    disk: int
    partition: str
    index: int  # 1-based "vnr" column, usable with `cdi`
    name: str
    load_number: str  # "-" when off (or "V<n>" alias on S900 hard disks)
    start_block: str
    os_version: str  # "-" on S900
    type: str  # "S900", "S1000", "S3000", "CD3000"

    @property
    def path(self) -> str:
        return f"/disk{self.disk}/{self.partition}/{self.name}"


@dataclass(frozen=True)
class FileEntry:
    index: int  # 1-based "fnr" column, usable with `geti` / `sample2wavi`
    name: str  # includes the type suffix, e.g. "KICK.S3"
    size: int  # bytes
    start_block: str
    os_version: str  # S900 volumes show "uncompr" here instead
    tags: str = ""

    @property
    def kind(self) -> str:
        ext = self.name.rsplit(".", 1)[-1].upper() if "." in self.name else ""
        return _KIND_BY_EXT.get(ext, ext or "?")

    @property
    def is_sample(self) -> bool:
        return self.kind == "Sample"


# What akaiutil appends to file names: a type letter, then a digit for the sampler
# generation (9 = S900, 1 = S1000, 3 = S3000; none for types shared by S1000/S3000).
# Used for display, and to decide whether "export as WAV" applies.
_KIND_BY_EXT = {
    **{e: "Sample" for e in ("S", "S1", "S3", "S9", "S9C")},
    **{e: "Program" for e in ("P", "P1", "P3", "P9")},
    **{e: "Drum settings" for e in ("D", "D1", "D3", "D9")},
    **{e: "Effects" for e in ("X", "X3")},
    **{e: "Cue list" for e in ("Q", "Q3")},
    **{e: "Take list" for e in ("T", "T3")},
    "M3": "Multi",
    "M9": "Memory image",
    "O9": "Overall settings",
    "F9": "Fixup",
    "C": "Operating system",
    "C3": "Operating system",
    "CD": "CD3000 setup",
    "S+": "CD3000 sample parameters",
}


@dataclass(frozen=True)
class VolumeUsage:
    """Space a volume's files take, in whole allocation blocks (as akaiutil allocates them)."""

    disk: int
    partition: str
    index: int  # the volume's "vnr"
    name: str
    used_bytes: int  # space taken on the disk: each file rounded up to whole blocks
    files: int
    data_bytes: int = 0  # the files' sizes added up, without that rounding


@dataclass(frozen=True)
class DiskUsage:
    disk: Disk
    partitions: list[Partition]
    volumes: list[VolumeUsage]

    def volumes_in(self, partition: str) -> list[VolumeUsage]:
        return sorted((v for v in self.volumes if v.partition == partition), key=lambda v: v.index)

    def volume_order(self) -> dict[tuple[str, int], int]:
        """(partition, vnr) -> position of that volume across the whole disk (0, 1, 2, ...).

        This is what colours are assigned from, so a volume has the same colour
        in the usage bar and in the tree, and neighbours differ.
        """
        order: dict[tuple[str, int], int] = {}
        for p in self.partitions:
            for v in self.volumes_in(p.name):
                order[(p.name, v.index)] = len(order)
        return order


@dataclass
class ExportResult:
    exported: list[str] = field(default_factory=list)  # destination paths
    skipped: list[str] = field(default_factory=list)  # existing files left alone
    failed: list[str] = field(default_factory=list)  # "NAME: reason"
