"""Getting files ready to become direct-to-disk takes (Qt-free): what a file has to be, and the clean WAV akaiutil is given.

A take comes from audio (any format soundfile reads, mono or stereo) or from a `.TK` file akaiutil wrote with `tgeti`.
Measured on akaiutil 4.6.8 (dev_docs/akaiutil-notes.md, "Direct-to-disk takes"): it keeps a take's sample rate in 16 bits and
wraps a higher one without a word, so those files are refused here.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile

from . import importing
from .audio import wavfile
from .engine.models import DD_CLUSTER_BYTES, Partition, take_clusters
from .engine.session import EngineError

TAKE_EXTENSION = ".tk"
MAX_RATE = 65535  # a take's rate is stored in 16 bits
NAME_LENGTH = 12


@dataclass(frozen=True)
class TakeSource:
    path: Path
    kind: str  # "audio" | "take"
    channels: int = 0
    rate: int = 0
    frames: int = 0
    problem: str = ""  # why it can't be imported

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
    problem = src.problem
    if not problem and src.rate > MAX_RATE:
        problem = f"its sample rate is {src.rate:,} Hz and a take can't be above {MAX_RATE:,} Hz (convert it to 48 kHz or less first)"
    return TakeSource(path, "audio", src.channels, src.rate, src.frames, problem)


def take_name(path: str | Path) -> str:
    """The take's name for a file: its name as the start of an Akai name, cut to what a take's name holds."""
    return importing.clean_stem(Path(path).stem, NAME_LENGTH)


def prepare_audio(src: TakeSource, dest: Path) -> None:
    """Write the 16-bit WAV akaiutil reads (a clean one: its reader trips over the padding of some chunks)."""
    data, rate = soundfile.read(str(src.path), dtype="float64", always_2d=True)
    pcm = np.clip(np.round(data * 32767.0), -32768, 32767).astype("<i2")
    wavfile.write_wav16(dest, pcm, rate)


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
                prepare_audio(src, local)
                take = engine.import_take(part, local, expect=(src.channels, src.rate, src.frames))
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
