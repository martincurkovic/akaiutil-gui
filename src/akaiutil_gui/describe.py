"""Turns akaiutil's terminal-style info text into something a person can read.

akaiutil prints `srate: 44100Hz`, `rkey: 60`, `pmode: LOOP`, hex addresses and so
on. This module parses that text and rebuilds it as titled sections of
label/value rows (and small tables), with readable labels and values. There is no
Qt in here: the UI just renders the `Details` it gets back.

Like parse.py this depends on akaiutil's exact output format, and it goes away
with a ctypes/cffi engine that reads the structures directly. Anything it doesn't
know how to present is shown as akaiutil printed it, never dropped.

Terminology follows AKAISDS (loop modes, "Original key", ...). One trap worth
knowing about: in akaiutil's own names `ctune` is the *cents* (fine) tune and
`stune` the *semitone* (coarse) tune, the reverse of what the initials suggest
(see the struct comments in akaiutil_file.h).
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .engine.models import DiskUsage, FileEntry, Loop, SampleInfo

if TYPE_CHECKING:
    from .references import VolumeReferences
from .formatting import human_size, note_name, panel_os_version, percent, plural


# ------------------------------------------------------------------ the result
@dataclass(frozen=True)
class Row:
    label: str
    value: str


@dataclass
class Block:
    """One titled part of the details: label/value rows, a table, or preformatted text."""

    title: str = ""
    rows: list[Row] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)  # a table, when there are headers
    table: list[list[str]] = field(default_factory=list)
    text: str = ""  # shown as-is, monospaced
    muted: bool = False  # low-key, for technical details


@dataclass
class Details:
    title: str
    subtitle: str = ""
    blocks: list[Block] = field(default_factory=list)
    raw: str = ""  # what akaiutil actually printed


# --------------------------------------------------------------------- parsing
@dataclass
class Node:
    key: str
    value: str = ""
    children: list["Node"] = field(default_factory=list)

    def all(self, key: str) -> list["Node"]:
        return [c for c in self.children if c.key == key]

    def get(self, key: str, default: str = "") -> str:
        found = self.all(key)
        return found[0].value if found else default

    def last(self, key: str, default: str = "") -> str:
        found = self.all(key)
        return found[-1].value if found else default

    def has(self, key: str) -> bool:
        return bool(self.all(key))

    def sections(self, prefix: str) -> list["Node"]:
        return [c for c in self.children if c.key.startswith(prefix) and c.value == ""]


_KV = re.compile(r"^([A-Za-z][A-Za-z0-9 .\-/()]*?):\s*(.*?)\s*$")


def parse_info(text: str) -> tuple[str, Node, list[str]]:
    """Split akaiutil's info text into (path line, tree of key/value nodes, other lines).

    Indentation nests: `keygroup 1:` owns the indented lines under it. Lines that
    aren't `key: value` (the tables in cue lists, drum settings, ...) are returned
    untouched in the third item.
    """
    path = ""
    root = Node("")
    stack: list[tuple[int, Node]] = [(-1, root)]
    other: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if not path and line.startswith("/"):
            path = line.strip()
            continue
        indent = len(line) - len(line.lstrip(" "))
        m = _KV.match(line.strip())
        if not m:
            other.append(line)
            continue
        key, value = m.groups()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        node = Node(key, value)
        while stack[-1][0] >= indent:
            stack.pop()
        stack[-1][1].children.append(node)
        stack.append((indent, node))
    return path, root, other


# ------------------------------------------------------------ value formatting
def _num(s: str) -> int | None:
    """'0x00ff' or '255' -> 255."""
    s = s.strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return None


def _count(s: str) -> int | None:
    """A count printed as '0x00000100 (      256)': the decimal, falling back to the hex."""
    n = _in_parens(s)
    return n if n is not None else _num(s.split()[0]) if s.split() else None


def _in_parens(s: str) -> int | None:
    """'0x00000100 (      256)' -> 256."""
    m = re.search(r"\(\s*(\d+)\s*\)", s)
    return int(m.group(1)) if m else None


def _hz(s: str) -> str:
    m = re.match(r"(\d+)\s*Hz", s)
    if not m:
        return s
    hz = int(m.group(1))
    return f"{hz / 1000:g} kHz" if hz >= 1000 else f"{hz} Hz"


def _khz(s: str) -> str:
    return re.sub(r"(\d)\s*kHz", r"\1 kHz", s)


def _duration(ms_text: str) -> str:
    m = re.match(r"([\d.]+)\s*ms", ms_text)
    if not m:
        return ms_text
    ms = float(m.group(1))
    if ms < 1000:
        return f"{ms:.1f} ms" if ms < 100 else f"{ms:.0f} ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.2f} s"
    return f"{int(seconds // 60)} min {seconds % 60:.1f} s"


def _sample_pos(s: str) -> str:
    n = _num(s)
    return "—" if n is None else f"sample {n:,}"


def _signed(s: str, unit: str) -> str:
    n = _num(s.lstrip("+")) if s.strip().lstrip("+-").isdigit() else None
    if n is None:
        return s
    return f"{s.strip()} {unit if abs(n) != 1 else unit.rstrip('s')}"


def _on_off(s: str) -> str:
    return {"ON": "On", "OFF": "Off"}.get(s.upper(), s)


def _key_range(s: str) -> str:
    m = re.match(r"(\d+)\s*-\s*(\d+)$", s.strip())
    if not m:
        return s
    lo, hi = int(m.group(1)), int(m.group(2))
    return f"{note_name(lo)} – {note_name(hi)}  (MIDI {lo}–{hi})"


def _key_range_short(s: str) -> str:
    m = re.match(r"(\d+)\s*-\s*(\d+)$", s.strip())
    return f"{note_name(int(m.group(1)))} – {note_name(int(m.group(2)))}" if m else s


def _loop_length(s: str) -> str:
    """'0x000000a8.0x8fdf' -> '168.56 samples' (whole samples, then 1/65536ths)."""
    m = re.match(r"(0x[0-9a-fA-F]+)\.(0x[0-9a-fA-F]+)$", s.strip())
    if not m:
        return s
    value = int(m.group(1), 16) + int(m.group(2), 16) / 65536
    return f"{value:,.0f} samples" if value == int(value) else f"{value:,.2f} samples"


def _loop_time(s: str) -> str:
    if s.upper() == "HOLD":
        return "Hold (until release)"
    m = re.match(r"(\d+)\s*ms", s)
    return f"{int(m.group(1)):,} ms" if m else s


# Sample playback modes. akaiutil prints the S1000/S3000 names; AKAISDS's wording.
_SAMPLE_LOOP_MODE = {
    "LOOP": "Normal looping",
    "LOOPNOTREL": "Loop until release",
    "NOLOOP": "No looping",
    "TOEND": "Play to sample end",
}
# A program's velocity zone can override the sample's mode, or defer to it.
_ZONE_PLAY_MODE = {
    "SAMPLE": "As sample",
    "LOOP": "Loop in release",
    "LOOPNOTREL": "Loop until release",
    "NOLOOP": "No looping",
    "TOEND": "Play to sample end",
}
_S900_PLAY_MODE = {"ONESHOT": "One-shot", "LOOP": "Looping", "ALTLOOP": "Alternating loop"}


def _pitch_mode(s: str) -> str:
    return {"TRACK": "Key track", "CONST": "Constant pitch"}.get(s.upper(), s)


def _rows(*pairs: tuple[str, str | None]) -> list[Row]:
    return [Row(label, value) for label, value in pairs if value not in (None, "")]


def _size_text(n: int) -> str:
    return human_size(n) if n < 1024 else f"{human_size(n)}  ({n:,} bytes)"


# ----------------------------------------------------------------------- files
def describe_file(text: str, entry: FileEntry | None = None, refs: "VolumeReferences | None" = None) -> Details:
    path, root, other = parse_info(text)
    ftype = root.get("type")
    name = root.get("ramname") or (path.rsplit("/", 1)[-1] if path else "File")
    size = _num(root.get("size", "").split()[0]) if root.get("size") else None
    details = Details(title=name, subtitle=f"{ftype}  ·  {path}" if ftype else path, raw=text)

    head = _rows(("Type", ftype), ("Size", _size_text(size) if size is not None else None))
    start = root.get("start")
    block_no = _num(start.split()[-1]) if start.startswith("block") else None
    technical = _rows(
        ("File number", root.get("fnr")),
        ("Starts at", f"block {block_no} (0x{block_no:04x})" if block_no is not None else None),
        ("Exact size", f"{size:,} bytes" if size is not None else None),
    )

    body: list[Block] = []
    handler = _FILE_HANDLERS.get(ftype)
    if handler is not None:
        body = handler(root)
    else:
        # Effects, multis and OS files have nothing beyond the header. Anything else we
        # don't know how to present (cue lists, drum settings, errors...) is shown as printed.
        extra = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("/") and not _HEADER_LINE.match(ln)]
        if extra:
            body = [Block("As reported by akaiutil", text="\n".join(extra))]

    used = _file_reference_blocks(entry, refs) if entry is not None and refs is not None else []  # (up top: the details run long)
    details.blocks = [Block(rows=head), *used, *body]
    if technical:
        details.blocks.append(Block("Technical", rows=technical, muted=True))
    return details


_LIST_LIMIT = 30  # names listed before "and N more"


def _names(files: list[FileEntry]) -> list[str]:
    names = [f.name for f in files]
    return names[:_LIST_LIMIT] + ([f"and {len(names) - _LIST_LIMIT} more"] if len(names) > _LIST_LIMIT else [])


def _file_reference_blocks(entry: FileEntry, refs: "VolumeReferences") -> list[Block]:
    """Who plays a sample, or what a program plays (only samples in the same volume count)."""
    if entry.is_sample:
        users = refs.programs_using(entry)
        if users:
            return [Block("Used by", [Row("Program" if len(users) == 1 else f"Program {n}", u.name) for n, u in enumerate(users, 1)])]
        if refs.has_programs:
            return [Block("Used by", [Row("Programs", "None: no program in this volume plays this sample")])]
        return []  # a volume of just samples: nothing to say on every one of them
    if entry.is_program:
        found, missing = refs.samples_of(entry)
        if not found and not missing:
            return [Block("Samples used", [Row("Samples", "None")])]
        rows = [["In this volume", name] for name in _names(found)] + [["Not in this volume", name] for name in missing]
        return [Block("Samples used", headers=["", "Sample"], table=rows)]
    return []


_HEADER_LINE = re.compile(r"^(fnr|start|size|type|ramname):")


def _sample_3k(root: Node) -> list[Block]:
    count = _count(root.get("scount"))
    length = _duration(root.get("sdur"))
    rkey = _num(root.get("rkey"))
    blocks = [
        Block(
            "Sample",
            _rows(
                ("Length", f"{length}  ({plural(count, 'sample')})" if count is not None else length),
                ("Sample rate", _hz(root.get("srate"))),
                ("Bandwidth", _khz(root.get("bandw"))),
                ("Original key", f"{note_name(rkey)}  (MIDI {rkey})" if rkey is not None else None),
                ("Coarse tune", _signed(root.get("stune"), "semitones") if root.has("stune") else None),
                ("Fine tune", _signed(root.get("ctune"), "cents") if root.has("ctune") else None),
                ("Hold-loop tune offset", root.get("hltoff")),
            ),
        ),
        Block(
            "Playback",
            _rows(
                ("Mode", _SAMPLE_LOOP_MODE.get(root.get("pmode"), root.get("pmode"))),
                ("Start marker", _sample_pos(root.last("start")) if len(root.all("start")) > 1 else None),
                ("End marker", _sample_pos(root.get("end")) if root.has("end") else None),
                ("Loops", root.get("lnum")),
                ("First active loop", root.get("lfirst")),
            ),
        ),
    ]
    for loop in root.sections("loop "):
        blocks.append(
            Block(
                loop.key.capitalize(),
                _rows(
                    ("Loop at", _sample_pos(loop.get("loopat"))),
                    ("Length", _loop_length(loop.get("length"))),
                    ("Time", _loop_time(loop.get("ltime"))),
                ),
            )
        )
    return blocks


def _sample_900(root: Node) -> list[Block]:
    count = _count(root.get("scount"))
    length = _duration(root.get("sdur"))
    pitch = root.get("npitch")
    try:
        pitch_text = f"{note_name(round(float(pitch)))}  ({float(pitch):.2f})"
    except ValueError:
        pitch_text = pitch or None
    kinds = root.all("type")  # the second "type:" is the sample type, after the file type
    return [
        Block(
            "Sample",
            _rows(
                ("Length", f"{length}  ({plural(count, 'sample')})" if count is not None else length),
                ("Sample rate", _hz(root.get("srate"))),
                ("Original pitch", pitch_text),
                ("Loudness offset", root.get("loud")),
                ("Compressed", _on_off(root.get("compr")) if root.has("compr") else None),
                ("Sample type", {"NORM": "Normal", "VELXF": "Velocity crossfade"}.get(kinds[1].value, kinds[1].value) if len(kinds) > 1 else None),
            ),
        ),
        Block(
            "Playback",
            _rows(
                ("Mode", _S900_PLAY_MODE.get(root.get("pmode"), root.get("pmode"))),
                ("Direction", {"NORM": "Forward", "REV": "Reverse"}.get(root.get("dir"), root.get("dir"))),
                ("Start marker", _sample_pos(root.last("start")) if len(root.all("start")) > 1 else None),
                ("End marker", _sample_pos(root.get("end")) if root.has("end") else None),
                ("Loop length", plural(_num(root.get("llen")), "sample") if _num(root.get("llen")) is not None else None),
            ),
        ),
    ]


def _midi_channel(s: str) -> str:
    return "Omni (all channels)" if s.upper() == "OMNI" else f"Channel {s}"


def _program_3k(root: Node) -> list[Block]:
    aux = root.get("auxch")
    blocks = [
        Block(
            "Program",
            _rows(
                ("MIDI channel", _midi_channel(root.get("midich")) if root.has("midich") else None),
                ("Key range", _key_range(root.get("key lo-hi")) if root.has("key lo-hi") else None),
                ("Octave offset", root.get("oct")),
                ("Aux output", "Off" if aux.upper() == "OFF" else f"Channel {aux}" if aux else None),
                ("Keygroup crossfade", _on_off(root.get("kgxf")) if root.has("kgxf") else None),
                ("Keygroups", root.get("kgnum")),
            ),
        )
    ]
    for kg in root.sections("keygroup "):
        n = kg.key.split()[-1]
        zones = []
        for z in kg.sections("velzone "):
            sample = z.get("sname")
            if not sample:  # an unused zone
                continue
            zones.append(
                [
                    z.key.split()[-1],
                    z.get("vel lo-hi").replace("-", " – "),
                    sample,
                    z.get("stune"),
                    z.get("ctune"),
                    z.get("loud"),
                    z.get("filter"),
                    z.get("pan"),
                    _pitch_mode(z.get("pitch")),
                    _ZONE_PLAY_MODE.get(z.get("pmode"), z.get("pmode")),
                ]
            )
        blocks.append(
            Block(
                f"Keygroup {n}  ·  {_key_range_short(kg.get('key lo-hi'))}",
                _rows(
                    ("Key range", _key_range(kg.get("key lo-hi"))),
                    ("Coarse tune", _signed(kg.get("stune"), "semitones") if kg.has("stune") else None),
                    ("Fine tune", _signed(kg.get("ctune"), "cents") if kg.has("ctune") else None),
                    ("Filter cutoff", kg.get("filter")),
                    ("Velocity crossfade", _on_off(kg.get("velxf")) if kg.has("velxf") else None),
                ),
                headers=["Zone", "Velocity", "Sample", "Coarse tune", "Fine tune", "Loudness", "Filter", "Pan", "Pitch", "Play mode"] if zones else [],
                table=zones,
            )
        )
    return blocks


def _program_900(root: Node) -> list[Block]:
    blocks = [
        Block(
            "Program",
            _rows(
                ("Keygroup crossfade", _on_off(root.get("kgxf")) if root.has("kgxf") else None),
                ("Keygroups", root.get("kgnum")),
            ),
        )
    ]
    for kg in root.sections("keygroup "):
        n = kg.key.split()[-1]
        layers = []
        for key, label in (("sample 1 (soft)", "Soft"), ("sample 2 (loud)", "Loud")):
            node = next((c for c in kg.children if c.key == key), None)
            if node is None or not node.get("sname"):
                continue
            layers.append([label, node.get("sname"), node.get("tune"), node.get("filter"), node.get("loud")])
        outch = kg.get("outch")
        blocks.append(
            Block(
                f"Keygroup {n}  ·  {_key_range_short(kg.get('key lo-hi'))}",
                _rows(
                    ("Key range", _key_range(kg.get("key lo-hi"))),
                    ("Output", {"LEFT": "Left", "RIGHT": "Right", "ANY": "Any"}.get(outch, f"Channel {outch}" if outch else None)),
                    ("Pitch", _pitch_mode(kg.get("pitch")) if kg.has("pitch") else None),
                    ("One-shot", _on_off(kg.get("oneshot")) if kg.has("oneshot") else None),
                    ("Velocity crossfade", _on_off(kg.get("velxf")) if kg.has("velxf") else None),
                    ("Velocity switch", kg.get("velswth")),
                    ("MIDI channel offset", kg.get("midichoff")),
                ),
                headers=["Layer", "Sample", "Tune", "Filter", "Loudness"] if layers else [],
                table=layers,
            )
        )
    return blocks


_FILE_HANDLERS = {
    "S900 sample": _sample_900,
    "S1000 sample": _sample_3k,
    "S3000 sample": _sample_3k,
    "CD3000 CD-ROM sample parameters": _sample_3k,
    "S900 program": _program_900,
    "S1000 program": _program_3k,
    "S3000 program": _program_3k,
}


def describe_selection(files: list[FileEntry]) -> Details:
    """Several files selected: what's in the selection."""
    kinds: dict[str, int] = {}
    for f in files:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    rows = [Row("Total size", _size_text(sum(f.size for f in files)))]
    rows += [Row(kind, str(n)) for kind, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))]
    return Details(title=plural(len(files), "file") + " selected", blocks=[Block(rows=rows)])


# ----------------------------------------------- disks, partitions and volumes
_DISK_TYPES = {
    "S1000/S3000 harddisk": "S1000/S3000 hard disk",
    "S900/S950 harddisk": "S900/S950 hard disk",
    "low-density floppy": "Low-density (DD) floppy",
    "high-density floppy": "High-density (HD) floppy",
}
_PART_TYPES = {
    "S1000/S3000 harddisk sampler partition": "S1000/S3000 sampler partition",
    "S1100/S3000 harddisk DD parttion": "S1100/S3000 direct-to-disk partition",  # (sic) akaiutil's own typo
    "low-density floppy": "Low-density (DD) floppy",
    "high-density floppy": "High-density (HD) floppy",
}


def _kb(s: str) -> int | None:
    """'0x2cac blocks ( 91488 KB) =  57.2%' -> 91488 (KB)."""
    m = re.search(r"\(\s*(\d+)\s*KB\)", s)
    return int(m.group(1)) if m else None


def _pct(s: str) -> str | None:
    m = re.search(r"=\s*([\d.]+)%", s)
    return f"{float(m.group(1)):g}%" if m else None


def _space_rows(root: Node, free_label: str = "Free") -> list[Row]:
    rows = []
    total = _kb(root.get("total") or root.get("size"))
    free, bad = _kb(root.get("free")), _kb(root.get("bad"))
    if total is not None:
        rows.append(Row("Size", human_size(total * 1024)))
    if free is not None:
        rows.append(Row(free_label, f"{human_size(free * 1024)}  ({_pct(root.get('free'))})"))
    if bad is not None:
        rows.append(Row("Bad blocks", "None" if bad == 0 else f"{human_size(bad * 1024)}  ({_pct(root.get('bad'))})"))
    return rows


def _block_size(root: Node) -> Row | None:
    n = _num(root.get("blksize").split()[0]) if root.get("blksize") else None
    return Row("Block size", human_size(n)) if n else None


def describe_disk(text: str) -> Details:
    path, root, _ = parse_info(text)
    kind = _DISK_TYPES.get(root.get("disktype"), root.get("disktype"))
    parts = _num(root.get("parts") or "")
    rows = [Row("Type", kind)] if kind else []
    rows += _space_rows(root, "Free (inside partitions)")
    if parts is not None:
        rows.append(Row("Partitions", str(parts)))
    if (bs := _block_size(root)) is not None:
        rows.append(bs)
    offset = _num(root.get("start").replace("byte", "").strip()) if root.get("start") else None
    technical = _rows(("Start offset in the image", f"{offset:,} bytes" if offset is not None else None))
    blocks = [Block(rows=rows)]
    if technical:
        blocks.append(Block("Technical", technical, muted=True))
    return Details(title=f"Disk {root.get('disk')}", subtitle=kind or "", blocks=blocks, raw=text)


def describe_partition(text: str) -> Details:
    path, root, other = parse_info(text)
    kind = _PART_TYPES.get(root.get("parttype"), root.get("parttype"))
    rows = [Row("Type", kind)] if kind else []
    rows += _space_rows(root)
    if (bs := _block_size(root)) is not None:
        rows.append(bs)
    if root.has("max. vols"):
        rows.append(Row("Room for", plural(int(root.get("max. vols")), "volume")))
    if any("marked invalid" in line for line in other):
        rows.append(Row("Warning", "marked invalid"))
    technical = _rows(("Starts at", root.get("start")))
    blocks = [Block(rows=rows)]
    if technical:
        blocks.append(Block("Technical", technical, muted=True))
    return Details(title=f"Partition {root.get('part')}", subtitle=kind or "", blocks=blocks, raw=text)


_VOLUME_TYPES = {"S900": "S900", "S1000": "S1000", "S3000": "S3000", "CD3000": "CD3000 CD-ROM", "INACT": "Inactive"}


def _volume_contents(root: Node, path: str, usage: DiskUsage | None) -> Block | None:
    """File count and space used, from the disk-wide listing (see DiskUsage)."""
    vnr = _num(root.get("vnr"))
    parts = path.split("/")  # ['', 'disk0', 'A', 'VOLUME 001']
    partition = parts[2] if len(parts) > 3 else ""
    if usage is None or vnr is None:
        return None
    vol = next((v for v in usage.volumes if v.partition == partition and v.index == vnr), None)
    part = next((p for p in usage.partitions if p.name == partition), None)
    if vol is None:
        return None
    # Two different sizes: the files' own bytes, and what they take on the disk, where akaiutil
    # allocates whole blocks (so many small samples take noticeably more than their sizes add up to)
    on_disk = human_size(vol.used_bytes)
    if part is not None and part.size_bytes:
        on_disk += f"  ({percent(vol.used_bytes / part.size_bytes)} of partition {part.name})"
    return Block(
        "Contents",
        _rows(
            ("Files", plural(vol.files, "file")),
            ("File data", human_size(vol.data_bytes) if vol.data_bytes else None),
            ("Space used on disk", on_disk),
        ),
    )


def _volume_programs(refs: "VolumeReferences") -> Block | None:
    """How many programs there are, and which samples none of them plays (for tidying a library)."""
    if not refs.has_programs:
        return None
    programs = [f for f in refs.files if f.is_program]
    unused = refs.unused_samples()
    block = Block("Programs and samples", [Row("Programs", str(len(programs))), Row("Samples not in any program", str(len(unused)))])
    if unused:
        block.headers = ["Not played by any program"]
        block.table = [[name] for name in _names(unused)]
    return block


def describe_volume(text: str, usage: DiskUsage | None = None, refs: "VolumeReferences | None" = None) -> Details:
    path, root, _ = parse_info(text)
    name = root.get("vname") or path.rsplit("/", 1)[-1]
    vtype = _VOLUME_TYPES.get(root.get("voltype"), root.get("voltype"))
    load = root.get("load number").strip()
    rows = _rows(
        ("Type", f"{vtype} volume" if vtype else None),
        ("Volume number", root.get("vnr")),
        ("Load number", "None" if load in ("-", "") else load) if root.has("load number") else (None, None),
        ("OS version", panel_os_version(root.get("osver")) if root.get("osver") else None),
        ("Room for", plural(int(root.get("max. files")), "file") if root.has("max. files") else None),
    )
    osver = root.get("osver")
    # what akaiutil itself prints for it, to match against its own listings (only when that differs from the panel's number)
    technical = _rows(
        ("Starts at", root.get("start")),
        ("OS version as akaiutil prints it", osver if osver and panel_os_version(osver) != osver else None),
    )
    blocks = [Block(rows=rows)]
    if (contents := _volume_contents(root, path, usage)) is not None:
        blocks.append(contents)
    if refs is not None and (programs := _volume_programs(refs)) is not None:
        blocks.append(programs)
    if root.has("parttype"):  # a floppy: its one volume fills the disk, so show its space too
        blocks.append(Block("Disk space", _space_rows(root)))
    if technical:
        blocks.append(Block("Technical", technical, muted=True))
    return Details(title=name, subtitle=f"{vtype} volume" if vtype else "", blocks=blocks, raw=text)


def describe_takes(title: str, text: str) -> Details:
    """Direct-to-disk partitions: akaiutil's list of takes, as printed."""
    return Details(title=title, blocks=[Block("Takes", text=text.strip("\n"))], raw=text)


# ------------------------------------------------------------------ sample data
def _loop_frames(s: str) -> float | None:
    """'0x000000a8.0x8fdf' -> 168.56... (whole frames, then 1/65536ths)."""
    m = re.match(r"(0x[0-9a-fA-F]+)\.(0x[0-9a-fA-F]+)$", s.strip())
    return int(m.group(1), 16) + int(m.group(2), 16) / 65536 if m else None


def _first_int(s: str) -> int | None:
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None


def parse_sample_info(text: str) -> SampleInfo | None:
    """What akaiutil's info text says about how a sample plays, as numbers (frames, Hz, MIDI notes).
    None if the text isn't a sample's info."""
    _, root, _ = parse_info(text)
    ftype = root.get("type")
    if ftype in ("S1000 sample", "S3000 sample", "CD3000 CD-ROM sample parameters"):
        generation = "S1000/S3000"
    elif ftype == "S900 sample":
        generation = "S900"
    else:
        return None
    frames, rate = _count(root.get("scount")), _first_int(root.get("srate"))
    if frames is None or not rate:
        return None
    starts = root.all("start")  # the first is the file's block, the second (if any) the play marker
    start = _num(starts[-1].value) if len(starts) > 1 else 0
    end = _num(root.get("end")) if root.has("end") else frames - 1

    if generation == "S900":
        try:
            root_key = round(float(root.get("npitch")))
        except ValueError:
            root_key = None
        return SampleInfo(
            name=root.get("ramname"), generation=generation, frames=frames, rate=rate, root_key=root_key,
            play_mode=root.get("pmode"), start=start or 0, end=end if end is not None else frames,
            loop_length=_num(root.get("llen")) or 0,
        )

    loops = []
    for node in root.sections("loop "):
        slot, at, length = _first_int(node.key), _num(node.get("loopat")), _loop_frames(node.get("length"))
        if None in (slot, at, length):
            continue
        ltime = node.get("ltime")
        loops.append(Loop(slot=slot, end=at, length=length, time_ms=None if ltime.upper() == "HOLD" else _first_int(ltime) or 0))
    return SampleInfo(
        name=root.get("ramname"), generation=generation, frames=frames, rate=rate,
        root_key=_num(root.get("rkey")) if root.has("rkey") else None,
        semitones=_first_int(root.get("stune")) or 0, cents=_first_int(root.get("ctune")) or 0,
        hold_tune_cents=_first_int(root.get("hltoff")) or 0, play_mode=root.get("pmode"),
        start=start or 0, end=end if end is not None else frames - 1, loops=tuple(loops),
    )


def play_mode_label(info: SampleInfo) -> str:
    """'Normal looping', 'One-shot'...: the sample's own play mode in words."""
    table = _S900_PLAY_MODE if info.generation == "S900" else _SAMPLE_LOOP_MODE
    return table.get(info.play_mode, info.play_mode)
