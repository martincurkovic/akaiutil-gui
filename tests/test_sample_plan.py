"""Loading a sample for playback, and deciding exactly how to play it."""

import wave

import numpy as np
import pytest

from akaiutil_gui.audio.sample import LoadedSample, load_wav, playback_plan
from akaiutil_gui.engine.models import Loop, SampleInfo


def info(**kw) -> SampleInfo:
    base = dict(name="S", generation="S1000/S3000", frames=1000, rate=44100, root_key=60, play_mode="LOOP", start=0, end=999,
                loops=(Loop(slot=1, end=799, length=300.0, time_ms=None),))
    return SampleInfo(**{**base, **kw})


def sample(i: SampleInfo | None, frames=1000) -> LoadedSample:
    return LoadedSample("S", np.arange(frames, dtype=np.int16), 44100, i)


def write_wav(path, frames, channels=1, width=2, rate=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)


# ----------------------------------------------------------------------- loading
def test_a_16_bit_mono_wav_loads_unchanged(tmp_path):
    values = np.array([0, 1, -1, 32767, -32768, 1234], dtype="<i2")
    write_wav(tmp_path / "a.wav", values.tobytes())
    s = load_wav(tmp_path / "a.wav", "A", None)
    assert (s.samples == values).all() and s.rate == 44100 and s.frames == 6 and s.samples.dtype == np.int16


def test_a_stereo_wav_is_mixed_down_to_mono(tmp_path):
    pairs = np.array([[1000, 3000], [-2000, 2000], [32767, 32767]], dtype="<i2")
    write_wav(tmp_path / "s.wav", pairs.tobytes(), channels=2)
    assert list(load_wav(tmp_path / "s.wav", "S", None).samples) == [2000, 0, 32767]


def test_an_8_bit_wav_is_scaled_to_16_bit(tmp_path):
    write_wav(tmp_path / "e.wav", bytes([128, 255, 0]), width=1)  # 8-bit WAV is unsigned: 128 is silence
    assert list(load_wav(tmp_path / "e.wav", "E", None).samples) == [0, 32512, -32768]


@pytest.mark.parametrize("content", [b"", b"not a wav"])
def test_unreadable_or_empty_files_are_errors_not_crashes(tmp_path, content):
    (tmp_path / "bad.wav").write_bytes(content)
    with pytest.raises(ValueError, match="bad.wav"):
        load_wav(tmp_path / "bad.wav", "B", None)


# ----------------------------------------------------------------------- the plan
def test_a_looping_sample_plays_its_loop():
    plan = playback_plan(sample(info()))
    assert plan.looping and (plan.start, plan.loop_start, plan.loop_end, plan.end) == (0, 499, 799, 999)
    assert plan.dwell_ms is None  # HOLD


def test_a_timed_loop_has_a_dwell():
    plan = playback_plan(sample(info(loops=(Loop(1, 799, 300.0, 250),))))
    assert plan.dwell_ms == 250


def test_loop_off_in_settings_plays_it_once():
    plan = playback_plan(sample(info()), use_loop=False)
    assert not plan.looping and (plan.start, plan.end) == (0, 999)


@pytest.mark.parametrize("mode", ["NOLOOP", "TOEND"])
def test_modes_that_dont_loop_play_once(mode):
    assert not playback_plan(sample(info(play_mode=mode))).looping


def test_a_sample_with_no_loops_or_no_header_plays_once():
    assert not playback_plan(sample(info(loops=()))).looping
    plan = playback_plan(sample(None, frames=500))
    assert (plan.start, plan.end, plan.looping) == (0, 499, False)


def test_s900_samples_play_once_and_their_end_marker_is_clamped():
    s900 = info(generation="S900", play_mode="LOOP", loops=(), end=1000, loop_length=100)  # S900 stores the end as a count
    plan = playback_plan(sample(s900))
    assert not plan.looping and plan.end == 999


def test_a_chosen_loop_slot_is_used_and_an_unknown_one_falls_back_to_the_first():
    loops = (Loop(2, 400, 100.0, None), Loop(5, 900, 200.0, 500))
    s = sample(info(loops=loops))
    assert (playback_plan(s, loop_slot=5).loop_start, playback_plan(s, loop_slot=5).dwell_ms) == (700, 500)
    assert playback_plan(s, loop_slot=7).loop.slot == 2


def test_the_hold_loop_tune_travels_with_the_plan():
    assert playback_plan(sample(info(hold_tune_cents=-12))).loop_tune_cents == -12


@pytest.mark.parametrize(
    "markers, loop, expected",
    [
        # the header's markers are the sampler's to keep consistent, not ours to trust: always start <= loop_start <= loop_end <= end
        (dict(start=100, end=900), Loop(1, 1000, 300.0, None), (100, 700, 900, 900)),  # loop overruns the end marker: cut at it
        (dict(start=100, end=900), Loop(1, 1500, 300.0, None), None),  # loop entirely beyond the end marker: nothing to repeat
        (dict(start=100, end=900), Loop(1, 150, 400.0, None), (100, 100, 150, 900)),  # loop starts before the start marker
        (dict(start=100, end=900), Loop(1, 50, 20.0, None), None),  # loop entirely before the start: nothing to repeat
        (dict(start=0, end=5000), Loop(1, 900, 100.0, None), (0, 800, 900, 999)),  # end marker beyond the audio
        (dict(start=-5, end=999), Loop(1, 500, 100.0, None), (0, 400, 500, 999)),  # nonsense start
        (dict(start=0, end=999), Loop(1, 500, 0.0, None), None),  # zero-length loop
    ],
)
def test_loop_bounds_are_always_consistent(markers, loop, expected):
    plan = playback_plan(sample(info(loops=(loop,), **markers)))
    if expected is None:
        assert not plan.looping
        return
    assert (plan.start, plan.loop_start, plan.loop_end, plan.end) == expected
    assert 0 <= plan.start <= plan.loop_start < plan.loop_end <= plan.end <= 999
