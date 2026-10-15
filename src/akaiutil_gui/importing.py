"""Getting files into a volume: audio (converted for the sampler) and files that are already Akai's own (Qt-free).

An audio file becomes a WAV akaiutil can turn into a sample: 16-bit, mono or stereo, no faster than the sampler can play
(44.1 kHz; 40 kHz for an S900). A 16-bit WAV that is already fine is passed through untouched, so a WAV exported by this app
(which carries the sample's loops and settings in an extra chunk) goes back exactly as it was. Anything else is read with
soundfile and rewritten. Files that are already Akai's (KICK.S3, a program, ...) go in unchanged.

Names: a file's name becomes an Akai name (capitals, the characters Akai has, up to 12), and if the volume already has a
file of that name the new one gets a number, because akaiutil would silently replace the old one.
"""

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import soundfile

from . import akai_header, akai_names
from .audio import markers as audio_markers
from .audio import resample, wavfile
from .engine.models import ImportJob

AUDIO_EXTENSIONS = {".wav", ".wave", ".aif", ".aiff", ".aifc", ".flac", ".ogg", ".oga", ".mp3", ".caf", ".w64"}
# What akaiutil's own files end in: a type letter, then 9, 1 or 3 for the sampler (none: S1000 and S3000 share it).
# (.C and .C3 are operating-system images: not something to drop in by accident.)
AKAI_EXTENSIONS = {
    ".s", ".s1", ".s3", ".s9", ".s9c", ".s+",
    ".p", ".p1", ".p3", ".p9",
    ".d", ".d1", ".d3", ".d9",
    ".x", ".x3", ".q", ".q3", ".t", ".t3",
    ".m3", ".m9", ".o9", ".f9", ".cd",
}
# What akaiutil takes as a file type in a name: a letter and then 9, 1 or 3 (or nothing), or one of its three special ones.
# Any such file can be added anyway, since akaiutil takes the type from the name alone.
_AKAI_SUFFIX = re.compile(r"\.([a-z][139]?|s9c|cd|s\+)", re.IGNORECASE)
SAMPLE_TYPES = ("S3000", "S1000", "S900")
TRIM_THRESHOLD = 10 ** (-60 / 20)  # silence: quieter than -60 dBFS
NORMALIZE_PEAK = 10 ** (-1 / 20)  # loudest sample at -1 dBFS
MAX_RATE = {"S3000": 44100, "S1000": 44100, "S900": 40000}
STEREO_SPLIT, STEREO_MIX = "split", "mix"
_SAMPLE_EXT = {"S3000": ".S3", "S1000": ".S1", "S900": ".S9"}
_WAV_JOB = {"S3000": "wav3", "S1000": "wav1", "S900": "wav9"}


def kind_of(path: str | Path) -> str:
    """"audio", "akai" or "other" (anything else is taken to be a disk image)."""
    ext = Path(path).suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in AKAI_EXTENSIONS:
        return "akai"
    return "other"


def has_akai_suffix(path: str | Path) -> bool:
    """Whether the name ends in something akaiutil would take as a file type (.S3, .C, .C3, .X...)."""
    return _AKAI_SUFFIX.fullmatch(Path(path).suffix) is not None


def natural_key(path: str | Path) -> tuple[list, str]:
    """Alphabetical, ignoring case, with numbers in order (kick 2 comes before kick 10)."""
    p = Path(path)
    # by the name without its extension, so "kick.wav" comes before "kick 2.wav", and then by extension
    parts = [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", p.stem.casefold())]
    return parts, p.suffix.casefold()


def expand(paths: list[str]) -> list[Path]:
    """The files among `paths`, with a dropped folder replaced by the files directly inside it. Sorted alphabetically."""
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files += [c for c in path.iterdir() if c.is_file() and not c.name.startswith(".")]
        elif path.is_file():
            files.append(path)
    return sorted(files, key=natural_key)


def allowed_types(volume_type: str) -> list[str]:
    """Which kinds of sample a volume can hold: an S900 or S1000 volume only its own kind; S3000 (and CD3000) any."""
    if volume_type == "S900":
        return ["S900"]
    if volume_type == "S1000":
        return ["S1000"]
    return ["S3000", "S1000", "S900"]


def default_type(volume_type: str) -> str:
    return allowed_types(volume_type)[0]


def generation(ext: str) -> str:
    """Which sampler an Akai file's extension is for: "9", "1", "3", or "13" (the types S1000 and S3000 share)."""
    ext = ext.lower().lstrip(".")
    if ext in ("cd", "s+") or ext.endswith("3"):
        return "3"
    if ext.endswith("9") or ext == "s9c":
        return "9"
    if ext.endswith("1"):
        return "1"
    return "13"


def akai_file_problem(ext: str, volume_type: str) -> str | None:
    """Why a file of this type can't go into a volume of this type (None: it can)."""
    gen = generation(ext)
    if volume_type == "S900":
        ok = gen == "9"
    elif volume_type == "S1000":
        ok = gen in ("1", "13")
    else:
        ok = gen in ("3", "1", "13") or (gen == "9" and ext.lower() != ".s9c")
    if ok:
        return None
    names = {"9": "S900", "1": "S1000", "3": "S3000", "13": "S1000/S3000"}
    if ext.lower() == ".s9c":
        return "compressed S900 samples only work in an S900 volume"
    return f"{names[gen]} files don't belong in {'an' if volume_type[1] in '19' else 'a'} {volume_type} volume"


# ----------------------------------------------------------------------- looking at a file
@dataclass(frozen=True)
class Source:
    path: Path
    kind: str  # "audio" | "akai"
    format: str = ""  # soundfile's: "WAV", "FLAC", "AIFF"...
    subtype: str = ""  # "PCM_16", "PCM_24", "FLOAT"...
    channels: int = 0
    rate: int = 0
    frames: int = 0
    problem: str = ""  # why it can't be imported at all
    markers: audio_markers.Markers = audio_markers.Markers()  # the root note and loops the file carries
    forceable: bool = False  # the problem is only that the app can't check the file: "Add anyway" imports it as an Akai file

    @property
    def bits(self) -> int | None:
        return {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32, "PCM_U8": 8, "PCM_S8": 8, "FLOAT": 32, "DOUBLE": 64}.get(self.subtype)


def inspect(path: str | Path) -> Source:
    path = Path(path)
    kind = kind_of(path)
    if kind == "akai":
        problem = ""
        try:
            size = path.stat().st_size
        except OSError as e:
            return Source(path, kind, problem=str(e))
        if size == 0:
            problem = "the file is empty"
        elif path.suffix.lower() in (".s", ".s1", ".s3"):
            with open(path, "rb") as f:
                head = f.read(1)
            if head != b"\x03" or size < 150:  # an S1000/S3000 sample starts with its header, whose first byte is 3
                problem = "this doesn't look like an Akai sample"
                return Source(path, kind, problem=problem, forceable=True)
        return Source(path, kind, problem=problem)
    try:
        info = soundfile.info(str(path))
    except Exception as e:  # soundfile raises its own error types
        # An operating system image (.C, .C3) or any other file with an Akai type in its name: there is nothing to check it
        # against, so the user can say to add it as it is. (A name akaiutil can't use as a type is still refused.)
        forceable = has_akai_suffix(path) and path.is_file() and path.stat().st_size > 0
        return Source(path, kind, problem=f"can't read it as audio ({e})", forceable=forceable)
    problem = ""
    if info.channels > 2:
        problem = f"it has {info.channels} channels (samples are mono or stereo)"
    elif info.frames == 0:
        problem = "it has no audio in it"
    return Source(path, kind, info.format, info.subtype, info.channels, info.samplerate, info.frames, problem, audio_markers.read_markers(path))


@dataclass(frozen=True)
class ImportOptions:
    """What else to do to audio on the way in."""

    file_markers: bool = True  # use the root note and loops a file carries
    root_from_name: bool = False  # a name ending in a note (Piano_C3) is the root note, if the file doesn't say
    trim: bool = False  # cut silence from the start and end
    normalize: bool = False  # make the loudest sample -1 dBFS
    add_anyway: bool = False  # import the files the app can't check (Source.forceable) as Akai files, exactly as they are


def effective(src: Source, options: ImportOptions) -> Source:
    """`src` as it will be imported: a file that can only be added anyway is an Akai file, unchanged, if that was asked for."""
    if src.problem and src.forceable and options.add_anyway:
        return replace(src, kind="akai", problem="", forceable=False)
    return src


def sample_markers(src: Source, sample_type: str, options: ImportOptions) -> tuple[int | None, tuple[tuple[int, int], ...]]:
    """(root note, loops) to put in the sample's header: from the file and/or its name; nothing for a file that already has
    an Akai header (which is kept as it is) or for an S900 sample (its header is different, and has no loops here)."""
    if sample_type == "S900" or src.markers.has_akai_header:
        return None, ()
    root, loops = (src.markers.root, src.markers.loops) if options.file_markers else (None, ())
    if root is None and options.root_from_name:
        root = audio_markers.note_from_name(src.path.stem)
    return root, tuple(loops)


def conversion_steps(src: Source, sample_type: str, stereo: str = STEREO_SPLIT, options: ImportOptions = ImportOptions()) -> list[str]:
    """What has to be done to an audio file to import it ([] means it is used as it is)."""
    steps = []
    if src.format != "WAV":
        steps.append("converted to WAV")
    if src.subtype != "PCM_16":
        steps.append("converted to 16-bit")
    cap = MAX_RATE[sample_type]
    if src.rate > cap:
        steps.append(f"resampled from {src.rate / 1000:g} to {cap / 1000:g} kHz")
    if src.channels == 2 and stereo == STEREO_MIX:
        steps.append("mixed to mono")
    if not src.markers.has_akai_header:  # (a sample from this app keeps its own header, loops and all, exactly as it is)
        if options.trim:
            steps.append("silence trimmed from the ends")
        if options.normalize:
            steps.append("normalised to -1 dB")
        root, loops = sample_markers(src, sample_type, options)
        parts = ([f"root key {audio_markers.note_name(root)}"] if root is not None else []) + ([f"{len(loops)} loop{'s' if len(loops) != 1 else ''}"] if loops else [])
        if parts:
            steps.append("sets " + " and ".join(parts))
    return steps


def describe(src: Source, sample_type: str, stereo: str = STEREO_SPLIT, options: ImportOptions = ImportOptions()) -> str:
    """One line for the import dialog."""
    if src.problem:
        if src.forceable and options.add_anyway:
            return "added anyway: an Akai file, imported as it is (not checked)"
        hint = "  (tick “Add anyway” to import it as it is)" if src.forceable else ""
        return f"skipped: {src.problem}{hint}"
    if src.kind == "akai":
        return "Akai file, imported as it is"
    channels = "stereo" if src.channels == 2 else "mono"
    bits = f" · {src.bits}-bit" if src.bits else ""
    what = f"{src.format} · {src.rate / 1000:g} kHz{bits} · {channels}"
    steps = conversion_steps(src, sample_type, stereo, options)
    if src.markers.has_akai_header and sample_type != "S900":
        steps.append("keeps its Akai sample header")
    return f"{what}  →  {', '.join(steps)}" if steps else f"{what}  →  used as it is"


# ------------------------------------------------------------------------- names
def clean_stem(stem: str, limit: int) -> str:
    """A file's name as the start of an Akai name: capitals, only the characters Akai has (the others become spaces), no
    runs of spaces, at most `limit` characters."""
    text = re.sub(r"[^0-9A-Za-z #+\-.]", " ", stem.replace("_", " ")).upper()
    text = re.sub(r" +", " ", text).strip(" .")
    text = text[:limit].rstrip(" .")
    return text or "SAMPLE"


def final_names(base: str, ext: str, channels_out: int, limit: int) -> list[str]:
    """The names akaiutil gives a sample: the name itself, or for a stereo file two names ending "-L" and "-R"
    (the name cut to limit - 2 characters and padded to that, as the samplers' stereo pairs are)."""
    if channels_out == 1:
        return [base + ext]
    stem = base[: limit - 2].ljust(limit - 2)
    return [f"{stem}-L{ext}", f"{stem}-R{ext}"]


def unique_base(base: str, ext: str, channels_out: int, limit: int, taken: set[str]) -> str:
    """`base`, or the same with " 2", " 3"... if any name it would produce is already taken (compared without case)."""
    room = limit if channels_out == 1 else limit - 2
    candidate, n = base[:room].rstrip(), 2
    while any(name.casefold() in taken for name in final_names(candidate, ext, channels_out, limit)):
        suffix = f" {n}"
        candidate = base[: room - len(suffix)].rstrip() + suffix
        n += 1
    return candidate


# ----------------------------------------------------------------------- converting
def trim_silence(data: np.ndarray, loops: list[tuple[int, int]], threshold: float = TRIM_THRESHOLD) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Cut the silence from both ends of (frames, channels) audio, but never into a loop. Returns the audio and the loops moved to match."""
    loud = np.flatnonzero(np.abs(data).max(axis=1) >= threshold)
    if loud.size == 0:
        return data, loops  # all silence: nothing sensible to keep
    first, last = int(loud[0]), int(loud[-1])
    if loops:
        first, last = min(first, min(s for s, _ in loops)), max(last, max(e for _, e in loops))
    return data[first : last + 1], [(s - first, e - first) for s, e in loops]


def prepare_audio(src: Source, dest: Path, sample_type: str, stereo: str = STEREO_SPLIT, options: ImportOptions = ImportOptions()) -> tuple[int, int]:
    """Write the WAV akaiutil will read to `dest`. Returns (channels, sample rate) of what was written."""
    if not conversion_steps(src, sample_type, stereo, options):
        shutil.copyfile(src.path, dest)  # untouched: keeps whatever else the WAV carries (an exported sample's loops)
        return src.channels, src.rate
    data, rate = soundfile.read(str(src.path), dtype="float64", always_2d=True)
    root, file_loops = sample_markers(src, sample_type, options)
    loops = [loop for loop in file_loops]
    if data.shape[1] == 2 and stereo == STEREO_MIX:
        data = data.mean(axis=1, keepdims=True)
    keeps_header = src.markers.has_akai_header
    if options.trim and not keeps_header:
        data, loops = trim_silence(data, loops)
    cap = MAX_RATE[sample_type]
    if rate > cap:
        frames_before = data.shape[0]
        data = resample.resample(data, rate, cap)
        loops = [(round(s * data.shape[0] / frames_before), round(e * data.shape[0] / frames_before)) for s, e in loops]
        rate = resample.resampled_rate(rate, cap)
    if options.normalize and not keeps_header:
        peak = float(np.abs(data).max())
        if peak > 0:
            data = data * (NORMALIZE_PEAK / peak)
    pcm = np.clip(np.round(data * 32767.0), -32768, 32767).astype("<i2")
    header = None
    if sample_type != "S900" and not keeps_header and (root is not None or loops):
        header = (akai_header.CHUNK_IDS[sample_type], akai_header.build_header(sample_type, rate, pcm.shape[0], 60 if root is None else root, loops))
    wavfile.write_wav16(dest, pcm, rate, header)
    return pcm.shape[1], rate


@dataclass
class ImportResult:
    imported: list[str] = field(default_factory=list)  # the names that were created
    failed: list[str] = field(default_factory=list)  # "FILE: reason"

    @property
    def count(self) -> int:
        return len(self.imported)


def run_import(
    engine,
    volume,
    sources: list[Source],
    work_dir: Path,
    sample_type: str,
    compress: bool = False,
    stereo: str = STEREO_SPLIT,
    progress: Callable[[int, int, str], None] | None = None,
    options: ImportOptions = ImportOptions(),
) -> ImportResult:
    """Import `sources` into `volume`, in the order given. Each file succeeds or fails on its own."""
    result = ImportResult()
    taken = {f.name.casefold() for f in engine.list_files(volume)}
    for i, src in enumerate(sources):
        if progress is not None:
            progress(i, len(sources), src.path.name)
        src = effective(src, options)
        if src.problem:
            result.failed.append(f"{src.path.name}: {src.problem}")
            continue
        try:
            job = _make_job(src, volume, work_dir, sample_type, compress, stereo, taken, options)
            created = engine.import_job(volume, job)
        except Exception as e:  # one bad file mustn't stop the rest
            result.failed.append(f"{src.path.name}: {e}")
            continue
        taken.update(name.casefold() for name in created)
        result.imported += created
    return result


def _make_job(src, volume, work_dir: Path, sample_type: str, compress: bool, stereo: str, taken: set[str], options: ImportOptions) -> ImportJob:
    work_dir.mkdir(parents=True, exist_ok=True)
    for old in work_dir.iterdir():
        old.unlink()
    if src.kind == "akai":
        problem = akai_file_problem(src.path.suffix, volume.type)
        if problem:
            raise ValueError(problem)
        ext = src.path.suffix.upper()
        limit = akai_names.name_limit(volume.type)  # (an S900 volume's names are shorter)
        base = unique_base(clean_stem(src.path.stem, limit), ext, 1, limit, taken)
        local = work_dir / f"{base}{ext}"
        shutil.copyfile(src.path, local)
        return ImportJob(local, "put")
    if sample_type not in allowed_types(volume.type):
        raise ValueError(f"{sample_type} samples don't belong in {'an' if volume.type[1] in '19' else 'a'} {volume.type} volume")
    ext = ".S9C" if sample_type == "S900" and compress else _SAMPLE_EXT[sample_type]
    limit = akai_names.NAME_LENGTH_S900 if sample_type == "S900" or volume.type == "S900" else akai_names.NAME_LENGTH
    channels_out = 1 if (src.channels == 1 or stereo == STEREO_MIX) else 2
    base = unique_base(clean_stem(src.path.stem, limit), ext, channels_out, limit, taken)
    local = work_dir / f"{base}.wav"
    prepare_audio(src, local, sample_type, stereo, options)
    return ImportJob(local, "wav9c" if ext == ".S9C" else _WAV_JOB[sample_type])
