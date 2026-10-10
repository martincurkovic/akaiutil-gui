"""Getting a sample into memory for playback, and deciding exactly how it should be played."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from akaiutil_gui.engine.models import Loop, SampleInfo


@dataclass
class LoadedSample:
    name: str
    samples: np.ndarray  # mono int16
    rate: int
    info: SampleInfo | None  # None when akaiutil's header couldn't be read: then it just plays start to end

    @property
    def frames(self) -> int:
        return len(self.samples)


def load_wav(path: str | Path, name: str, info: SampleInfo | None) -> LoadedSample:
    """Read a WAV akaiutil exported into mono int16 samples.

    Always read as float and scale by hand rather than asking libsndfile for int16 directly: AKAISDS found that
    dtype="int16" silently produces near-silence for some formats, while float is reliable for every subtype.
    akaiutil writes mono 16-bit, so this is normally a straight copy; a stereo file is mixed down.
    """
    try:
        data, rate = sf.read(str(path), dtype="float64", always_2d=True)
    except Exception as e:  # noqa: BLE001 - libsndfile raises its own error types
        raise ValueError(f"Couldn't read {Path(path).name}: {e}") from e
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    samples = np.clip(np.rint(mono * 32768.0), -32768, 32767).astype(np.int16)
    if len(samples) == 0:
        raise ValueError(f"{Path(path).name} contains no audio")
    return LoadedSample(name=name, samples=samples, rate=int(rate), info=info)


@dataclass(frozen=True)
class PlaybackPlan:
    """What to hand the player. All frame numbers are inclusive and within the sample."""

    start: int
    end: int
    loop: Loop | None = None  # None: play start to end once
    loop_start: int = 0
    loop_end: int = 0
    dwell_ms: int | None = None  # how long to stay in the loop; None = Hold (until stopped)
    loop_tune_cents: int = 0

    @property
    def looping(self) -> bool:
        return self.loop is not None


def playback_plan(sample: LoadedSample, loop_slot: int | None = None, use_loop: bool = True) -> PlaybackPlan:
    """How to play `sample`.

    Plain playback of [start, end] unless the header says it loops (a looping play mode with at least one loop)
    and `use_loop` is on. `loop_slot` picks one of several loops (default: the first). Everything is clamped to
    the audio actually present and to AKAISDS's invariant start <= loop_start <= loop_end <= end, because the
    markers in a header are the sampler's to keep consistent, not ours to trust.
    """
    last = sample.frames - 1
    info = sample.info
    if info is None:
        return PlaybackPlan(0, last)
    start = min(max(0, info.start), last)
    end = min(max(start, info.end), last)  # S900 headers store the end as a count, one past the last frame
    if not (use_loop and info.has_loop_to_preview):
        return PlaybackPlan(start, end)

    loop = next((lp for lp in info.loops if lp.slot == loop_slot), info.loops[0])
    loop_end = min(max(start, loop.end), end)
    loop_start = min(max(start, loop.start), loop_end)
    if loop_end <= loop_start:  # nothing to repeat
        return PlaybackPlan(start, end)
    return PlaybackPlan(
        start=start,
        end=end,
        loop=loop,
        loop_start=loop_start,
        loop_end=loop_end,
        dwell_ms=loop.time_ms,
        # AKAISDS applies the hold-loop tune to the loop region whenever it previews a loop
        loop_tune_cents=info.hold_tune_cents,
    )
