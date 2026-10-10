"""The preview player, driven exactly the way miniaudio drives it, with a fake device (no audio hardware)."""

import random

import numpy as np
import pytest

from akaiutil_gui.audio import preview

from conftest import FakeDevice  # noqa: F401  (the fake_audio fixture lives there too)


@pytest.fixture
def player(fake_audio):
    p = preview.PreviewPlayer()
    yield p
    p.stop()


def pull(dev: FakeDevice, sizes: list[int], channels: int = 1) -> bytes:
    """What miniaudio does: ask for `n` frames, over and over; every answer must be exactly that long."""
    out = b""
    for n in sizes:
        chunk = dev.gen.send(n)
        assert len(chunk) == n * 2 * channels, "a short chunk leaves stale audio in CoreAudio's buffer (the macOS 'chop')"
        out += chunk
    return out


def drain(dev: FakeDevice, n: int, channels: int = 1) -> bytes:
    """Ask for n frames at a time until the stream ends, checking every chunk is exactly n frames long."""
    out = b""
    while True:
        try:
            chunk = dev.gen.send(n)
        except StopIteration:
            return out
        assert len(chunk) == n * 2 * channels
        out += chunk


def pcm(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<i2")


RAMP = np.arange(1000, dtype=np.int16)  # frame n holds the value n, so the stream shows exactly where it came from


# ---------------------------------------------------------------------- plain play
def test_play_streams_the_region_then_silence_and_stops_itself(player, fake_audio):
    player.play(RAMP, 100, 899, 44100)
    dev = fake_audio.opened[-1]
    assert (dev.nchannels, dev.sample_rate) == (1, 44100) and dev.buffersize_msec == round(1024 / 44100 * 1000)
    assert player.is_playing()

    raw = pull(dev, [300, 300, 300])  # 800 frames in the region, 900 asked for: the last chunk is zero-padded
    stream = pcm(raw)
    assert (stream[:800] == RAMP[100:900]).all() and not stream[800:].any()

    with pytest.raises(StopIteration):  # exhausted; the device would now be told to stop by the tick
        dev.gen.send(300)
    finished = []
    player.finished.connect(lambda: finished.append(True))
    player._on_tick()
    assert dev.closed and not player.is_playing() and finished == [True]


def test_chunk_sizes_miniaudio_asks_for_never_matter(player, fake_audio):
    player.play(RAMP, 0, 999, 22050)
    sizes = [random.Random(1).randint(1, 700) for _ in range(6)] + [64, 1, 333]
    stream = pcm(pull(fake_audio.opened[-1], sizes))
    assert (stream[:1000] == RAMP).all()


def test_pitch_shift_opens_the_device_at_a_scaled_rate(player, fake_audio):
    player.play(RAMP, 0, 99, 44100, pitch_shift_semitones=12)
    assert fake_audio.opened[-1].sample_rate == 88200


def test_stereo_is_interleaved_and_opens_two_channels(player, fake_audio):
    right = -RAMP
    player.play(RAMP, 10, 19, 44100, right_samples=right)
    dev = fake_audio.opened[-1]
    assert dev.nchannels == 2
    stream = pcm(pull(dev, [10], channels=2))
    assert (stream[0::2] == RAMP[10:20]).all() and (stream[1::2] == right[10:20]).all()


def test_an_empty_region_plays_nothing(player, fake_audio):
    player.play(RAMP, 500, 400, 44100)
    assert not fake_audio.opened and not player.is_playing()


# ---------------------------------------------------------------------- loops
def expected_loop_stream(start, loop_start, loop_end, end, passes):
    attack = RAMP[start : loop_end + 1]
    loop = RAMP[loop_start : loop_end + 1]
    tail = RAMP[loop_end : end + 1]
    return np.concatenate([attack, *([loop] * passes), tail])


def test_a_timed_loop_plays_attack_loop_passes_then_the_tail(player, fake_audio):
    # 100-frame loop, 250 ms at 1 kHz = a 250-frame budget: three passes (the current pass is always finished)
    player.play_loop(RAMP, 0, 100, 199, 999, 1000, dwell_ms=250)
    expected = expected_loop_stream(0, 100, 199, 999, passes=3)
    stream = pcm(pull(fake_audio.opened[-1], [64, 100, 333, 7, 250, 512, 1, 70]))
    assert (stream[: len(expected)] == expected).all(), "attack, loop passes and tail must join with no gap or repeat"
    assert not stream[len(expected) :].any()


def test_every_chunk_is_exactly_the_size_asked_even_across_segment_and_pass_boundaries(player, fake_audio):
    """The macOS 'chop' fix: a loop region that doesn't divide into the buffer size must not produce a short chunk."""
    player.play_loop(RAMP, 0, 100, 199, 999, 1000, dwell_ms=500)  # 200 attack + 5 passes of 100 + 801 tail = 1501 frames
    dev = fake_audio.opened[-1]
    rng, frames = random.Random(7), 0
    while True:
        n = rng.randint(1, 256)  # miniaudio's request size varies from callback to callback
        try:
            chunk = dev.gen.send(n)
        except StopIteration:
            break
        assert len(chunk) == n * 2, "a short chunk is the macOS glitch"
        frames += n
    assert frames >= 1501


def test_a_hold_loop_never_ends_by_itself_and_stop_ends_it(player, fake_audio):
    player.play_loop(RAMP, 0, 100, 199, 999, 44100, dwell_ms=None)
    dev = fake_audio.opened[-1]
    pull(dev, [256] * 300)  # far longer than the whole sample
    assert not player._finished and player.is_playing()
    finished = []
    player.finished.connect(lambda: finished.append(True))
    player.stop()
    assert dev.stopped and dev.closed and finished == [True]


def test_the_loop_can_be_moved_while_playing_at_the_next_pass(player, fake_audio):
    player.play_loop(RAMP, 0, 100, 199, 999, 44100, dwell_ms=None)
    dev = fake_audio.opened[-1]
    before = pcm(pull(dev, [50]))  # inside the attack
    player.update_loop_points(300, 399)
    after = pcm(pull(dev, [2000]))  # long enough to finish the attack and go round again
    assert set(range(300, 400)) <= set(after.tolist()), "the new loop region is picked up"
    assert before[0] == 0


def test_degenerate_loop_bounds_are_ignored_live(player, fake_audio):
    player.play_loop(RAMP, 0, 100, 199, 999, 44100, dwell_ms=None)
    player.update_loop_points(300, 300)  # end <= start: keep the loop it has
    assert (player._live_loop_start, player._live_loop_end) == (100, 199)


def test_a_zero_length_loop_just_plays_the_region(player, fake_audio):
    player.play_loop(RAMP, 0, 150, 150, 299, 1000)
    stream = pcm(pull(fake_audio.opened[-1], [300]))
    assert (stream == RAMP[:300]).all()


def test_the_loop_tune_applies_to_the_loop_only(player, fake_audio):
    # a 500-frame loop tuned up 50 cents is ~486 frames, so a 500-frame budget takes two passes (a started pass is finished)
    player.play_loop(RAMP, 0, 100, 599, 999, 1000, dwell_ms=500, loop_tune_cents=50)
    tuned_loop = preview._resample_region_for_cents(RAMP, 100, 599, 50)
    assert len(tuned_loop) < 500, "tuning up shortens the region (pitch and duration move together)"
    expected = np.concatenate([RAMP[0:600], tuned_loop, tuned_loop, RAMP[599:1000]])
    stream = pcm(drain(fake_audio.opened[-1], 200))
    assert (stream[: len(expected)] == expected).all() and not stream[len(expected) :].any()


def test_stereo_loops_share_their_points(player, fake_audio):
    player.play_loop(RAMP, 0, 100, 199, 399, 1000, dwell_ms=100, right_samples=-RAMP)  # 200 + 100 + 201 frames
    dev = fake_audio.opened[-1]
    stream = pcm(drain(dev, 100, channels=2))
    left, right = stream[0::2], stream[1::2]
    expected = expected_loop_stream(0, 100, 199, 399, passes=1)
    assert dev.nchannels == 2 and (left[: len(expected)] == expected).all() and (right[: len(expected)] == -expected).all()


# ---------------------------------------------------------------------- safety
def test_the_playhead_is_reported_in_source_frames(player, fake_audio):
    player.play(RAMP, 100, 899, 44100)
    positions = []
    player.position_changed.connect(positions.append)
    pull(fake_audio.opened[-1], [250])
    player._on_tick()
    assert positions == [350]  # started at frame 100, 250 consumed


def test_a_device_that_wont_open_is_survived(player, monkeypatch, fake_audio):
    def boom(**kwargs):
        raise preview.miniaudio.MiniaudioError("no device")

    monkeypatch.setattr(preview.miniaudio, "PlaybackDevice", boom)
    player.play(RAMP, 0, 99, 44100)
    player.play_loop(RAMP, 0, 10, 20, 99, 44100)
    assert not player.is_playing()  # logged, never raised into a Qt slot


def test_a_generator_error_ends_the_preview_instead_of_crashing_the_audio_thread(player, fake_audio):
    """The generator runs on miniaudio's native thread, where an uncaught exception would vanish or kill the process."""
    player.play(RAMP, 0, 99, 44100)
    dev = fake_audio.opened[-1]
    with pytest.raises(StopIteration):  # not an exception out of the audio thread: the generator just ends
        dev.gen.send(None)  # nonsense for "how many frames": raises inside the generator
    assert player._finished
    player._on_tick()  # the GUI-thread tick then shuts the device down cleanly
    assert dev.closed and not player.is_playing()


def test_nothing_is_opened_when_audio_is_disabled(player, fake_audio, monkeypatch):
    monkeypatch.setenv(preview.NO_AUDIO_ENV, "1")
    assert preview.list_output_devices() == []
    player.play(RAMP, 0, 99, 44100)
    assert not fake_audio.opened and not player.is_playing()


def test_every_player_is_stopped_at_interpreter_exit(player, fake_audio):
    """A running device at exit crashes macOS (SIGTRAP on CoreAudio's thread), so atexit must stop it."""
    player.play_loop(RAMP, 0, 100, 199, 999, 44100)
    dev = fake_audio.opened[-1]
    preview._stop_all_players()
    assert dev.closed and not player.is_playing()


def test_a_saved_device_that_has_gone_falls_back_to_the_default(monkeypatch):
    here, gone = {"name": "Speakers", "id": "a"}, "unplugged"
    monkeypatch.setattr(preview, "list_output_devices", lambda: [here])
    monkeypatch.setattr(preview, "device_id_string", lambda d: d["id"])
    monkeypatch.setattr(preview.settings, "get_audio_output_device", lambda: "a")
    assert preview.resolve_output_device() == here
    monkeypatch.setattr(preview.settings, "get_audio_output_device", lambda: gone)
    assert preview.resolve_output_device() is None
    monkeypatch.setattr(preview.settings, "get_audio_output_device", lambda: None)
    assert preview.resolve_output_device() is None


# ---------------------------------------------------------------------- numpy fast paths == the original code
@pytest.mark.parametrize("n", [1, 2, 3, 50, 4000])
@pytest.mark.parametrize("cents", [-50, -7, 0, 1, 13, 50])
def test_resampling_a_numpy_region_matches_the_pure_python_one(n, cents):
    data = [random.Random(n).randint(-32768, 32767) for _ in range(n + 10)]
    as_list = preview._resample_region_for_cents(data, 5, 5 + n - 1, cents)
    as_array = preview._resample_region_for_cents(np.array(data, dtype=np.int16), 5, 5 + n - 1, cents)
    assert list(as_array) == list(as_list)


def test_packing_a_numpy_region_matches_the_pure_python_one():
    data = [random.Random(3).randint(-32768, 32767) for _ in range(500)]
    arr = np.array(data, dtype=np.int16)
    assert preview._pack_region(arr, None, 10, 400) == preview._pack_region(data, None, 10, 400)
    assert preview._pack_region(arr, arr[::-1].copy(), 10, 400) == preview._pack_region(data, data[::-1], 10, 400)
