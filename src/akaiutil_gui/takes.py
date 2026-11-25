"""Getting files ready to become direct-to-disk takes (Qt-free): what a file has to be, and the clean WAV akaiutil is given.

A take comes from audio (any format soundfile reads, mono or stereo) or from a `.TK` file akaiutil wrote with `tgeti`.
Measured on akaiutil 4.6.8 (dev_docs/akaiutil-notes.md, "Direct-to-disk takes"): it keeps a take's sample rate in 16 bits and
wraps a higher one without a word, so a file above that is resampled here (to 48 kHz, or 44.1 kHz for a multiple of it).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile

from . import importing
from .audio import resample, wavfile
from .engine.models import DD_CLUSTER_BYTES, Partition
from .engine.session import EngineError

TAKE_EXTENSION = ".tk"
MAX_RATE = 65535  # a take's rate is stored in 16 bits
NAME_LENGTH = 12
RATE_FAMILIES = (44100, 48000)  # what a file above MAX_RATE is resampled to: the first that its rate is a multiple of, else the last


def resample_target(rate: int) -> int:
    """The rate a file at `rate` is converted to (0 if it is stored as it is)."""
    if rate <= MAX_RATE:
        return 0
    return next((r for r in RATE_FAMILIES if rate % r == 0), RATE_FAMILIES[-1])


@dataclass(frozen=True)
class TakeSource:
    path: Path
    kind: str  # "audio" | "take"
    channels: int = 0
    rate: int = 0
    frames: int = 0
    problem: str = ""  # why it can't be imported
    resample_to: int = 0  # the rate its audio is converted to, if it is above what a take can hold

    @property
    def is_take_file(self) -> bool:
        return self.kind == "take"


def inspect(path: str | Path) -> TakeSource:
    path = Path(path)
    if path.suffix.lower() == TAKE_EXTENSION:
        try:
            size = path.stat().st_size
        except OSError as e:
            return TakeSource(path, "take", problem=str(e))
        if size < 2 * DD_CLUSTER_BYTES:  # a sample cluster and an envelope cluster at the least
            return TakeSource(path, "take", problem="this is too small to be a take")
        return TakeSource(path, "take")
    src = importing.inspect(path)
    if src.kind != "audio":
        return TakeSource(path, src.kind, problem="a take comes from audio (WAV, AIFF, FLAC...) or from a .TK file")
    return TakeSource(path, "audio", src.channels, src.rate, src.frames, src.problem, 0 if src.problem else resample_target(src.rate))


def take_name(path: str | Path) -> str:
    """The take's name for a file: its name as the start of an Akai name, cut to what a take's name holds."""
    return importing.clean_stem(Path(path).stem, NAME_LENGTH)


READ_BLOCK_FRAMES = 1 << 18  # (a block as float64 is 4 MB mono; the whole file as float64 would be 8 bytes a sample)


def prepare_audio(src: TakeSource, dest: Path) -> tuple[int, int, int]:
    """Write the 16-bit WAV akaiutil reads (a clean one: its reader trips over the padding of some chunks) and return its
    (channels, sample rate, frames). Plain rounding, no dither. Read a block at a time so a long file doesn't need several float64
    copies of itself in memory; a file above what a take can hold is resampled (a channel at a time) on the way."""
    with soundfile.SoundFile(str(src.path)) as f:
        rate = f.samplerate
        target = resample_target(rate)
        pcm = np.empty((f.frames, f.channels), dtype="<i2")
        heard = np.empty((f.frames, f.channels), dtype="float32") if target else None
        at = 0
        for block in f.blocks(blocksize=READ_BLOCK_FRAMES, dtype="float32" if target else "float64", always_2d=True):
            if heard is not None:
                heard[at : at + len(block)] = block
            else:
                pcm[at : at + len(block)] = np.clip(np.round(block * 32767.0), -32768, 32767)
            at += len(block)
    if heard is not None:
        columns = [resample.resample(heard[:at, c : c + 1], rate, target)[:, 0] for c in range(heard.shape[1])]
        pcm = np.clip(np.round(np.stack(columns, axis=1) * 32767.0), -32768, 32767).astype("<i2")
        rate, at = resample.resampled_rate(rate, target), len(pcm)
    wavfile.write_wav16(dest, pcm[:at], rate)
    return pcm.shape[1], rate, at


@dataclass
class TakeImportResult:
    imported: list[str] = field(default_factory=list)  # names of the takes that were created
    failed: list[str] = field(default_factory=list)  # "FILE: reason"

    @property
    def count(self) -> int:
        return len(self.imported)


def run_import(
    engine,
    part: Partition,
    sources: list[TakeSource],
    work_dir: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> TakeImportResult:
    """Put each source into the partition as a take, one after another. A file that can't be imported is reported and the
    rest carry on; an EngineError that is the disk's own (editing off, image gone) ends the whole run."""
    result = TakeImportResult()
    for i, src in enumerate(sources):
        if progress is not None:
            progress(i, len(sources), src.path.name)
        if src.problem:
            result.failed.append(f"{src.path.name}: {src.problem}")
            continue
        try:
            if src.is_take_file:
                take = engine.import_take(part, src.path)
            else:
                name = take_name(src.path)
                local = work_dir / f"{name}.wav"
                take = engine.import_take(part, local, expect=prepare_audio(src, local))
        except EngineError as e:
            if not engine.is_open or not engine.is_writable:
                raise
            result.failed.append(f"{src.path.name}: {e}")
            continue
        except (OSError, RuntimeError, soundfile.LibsndfileError) as e:
            result.failed.append(f"{src.path.name}: {importing._why(e)}")
            continue
        result.imported.append(take.name)
    return result
