"""The preview panel: what it shows and, above all, what it sends to the audio device."""

import numpy as np
import pytest

from akaiutil_gui import settings
from akaiutil_gui.audio import preview
from akaiutil_gui.audio.sample import LoadedSample, playback_plan
from akaiutil_gui.engine.models import Loop, SampleInfo
from akaiutil_gui.ui import theme
from akaiutil_gui.ui.preview_panel import PLAY_TEXT, STOP_TEXT, PreviewPanel

RAMP = np.arange(1000, dtype=np.int16)  # frame n holds n: the audio stream shows exactly where it came from


def make(loops=((1, 799, 300.0, None),), mode="LOOP", generation="S1000/S3000", rate=44100, **kw) -> LoadedSample:
    info = SampleInfo(name="S", generation=generation, frames=1000, rate=rate, root_key=60, play_mode=mode, start=0, end=999,
                      loops=tuple(Loop(*lp) for lp in loops), **kw)
    return LoadedSample("S", RAMP.copy(), rate, info)


@pytest.fixture
def panel(fake_audio, qt_app):
    theme.apply_to_app(qt_app, "dark")
    p = PreviewPanel()
    p.show()
    return p


def stream(dev, n=64) -> np.ndarray:
    """Everything the device was asked to play (a finite stream), each chunk checked to be the size asked for."""
    out = b""
    while True:
        try:
            chunk = dev.gen.send(n)
        except StopIteration:
            return np.frombuffer(out, dtype="<i2")
        assert len(chunk) == n * 2
        out += chunk


def first(dev, frames, n=64) -> np.ndarray:
    out = b"".join(dev.gen.send(n) for _ in range(frames // n + 1))
    return np.frombuffer(out, dtype="<i2")[:frames]


# ---------------------------------------------------------------------------- showing
def test_before_a_sample_is_loaded_there_is_nothing_to_play(panel):
    assert not panel.play_button.isEnabled() and not panel.loop_check.isEnabled() and panel.sample is None


def test_it_summarises_the_sample(panel):
    panel.set_sample(make(mode="LOOP"))
    text = panel.info_label.text()
    assert "44.1 kHz" in text and "22.7 ms" in text and "C3" in text and "Normal looping" in text
    assert "loop 1" in text and "frames 499–799" in text and "hold" in text


def test_a_timed_loop_says_how_long(panel):
    panel.set_sample(make(loops=((1, 799, 300.0, 250),)))
    assert "250 ms" in panel.info_label.text()


def test_the_loop_choice_appears_only_when_there_is_a_choice(panel):
    panel.set_sample(make())
    assert panel.loop_check.isEnabled() and panel.loop_combo.isHidden()
    panel.set_sample(make(loops=((2, 400, 100.0, None), (5, 900, 200.0, 500))))
    assert not panel.loop_combo.isHidden() and [panel.loop_combo.itemText(i) for i in range(2)] == ["Loop 2 · Hold", "Loop 5 · 500 ms"]


def test_a_sample_that_doesnt_loop_has_no_loop_controls(panel):
    for s in (make(mode="NOLOOP"), make(loops=()), make(generation="S900", mode="ONESHOT", loops=()), LoadedSample("S", RAMP, 44100, None)):
        panel.set_sample(s)
        assert not panel.loop_check.isEnabled() and panel.loop_combo.isHidden() and panel.play_button.isEnabled()


def test_the_waveform_shows_the_loop_that_will_play(panel):
    panel.set_sample(make())
    assert panel.wave._loop is not None and panel.wave._loop.slot == 1
    panel.loop_check.setChecked(False)
    assert panel.wave._loop is None  # looping is off: nothing to shade


def test_the_settings_default_decides_whether_new_samples_loop(panel):
    settings.set_preview_loops(False)
    panel.set_sample(make())
    assert not panel.loop_check.isChecked()
    settings.set_preview_loops(True)
    panel.set_sample(make())
    assert panel.loop_check.isChecked()


def test_loading_and_errors_are_shown_and_leave_nothing_playable(panel):
    panel.set_sample(make())
    panel.show_loading("KICK.S3")
    assert "Loading KICK.S3" in panel.info_label.text() and not panel.play_button.isEnabled()
    panel.show_error("boom")
    assert "boom" in panel.info_label.text() and panel.sample is None


# --------------------------------------------------------------------------- playing
def test_play_sends_the_attack_loop_and_tail_to_the_device(panel, fake_audio):
    panel.set_sample(make())
    panel.play()
    dev = fake_audio.opened[-1]
    assert panel.play_button.text() == STOP_TEXT and panel.is_playing() and dev.sample_rate == 44100 and dev.nchannels == 1
    plan = playback_plan(panel.sample)
    expected_head = RAMP[plan.start : plan.loop_end + 1]  # the attack, once
    assert (first(dev, len(expected_head)) == expected_head).all()
    assert plan.looping and plan.dwell_ms is None


def test_a_hold_loop_keeps_going_until_stopped(panel, fake_audio):
    panel.set_sample(make())
    panel.play()
    dev = fake_audio.opened[-1]
    for _ in range(200):  # ten times the whole sample
        dev.gen.send(256)
    assert panel.is_playing()
    panel.toggle()  # the same button stops it
    assert not panel.is_playing() and dev.closed and panel.play_button.text() == PLAY_TEXT


def test_a_timed_loop_ends_by_itself(panel, fake_audio):
    panel.set_sample(make(loops=((1, 799, 300.0, 20),)))  # loop = frames 499..799 (301); 20 ms = 882 frames: three passes
    panel.play()
    dev = fake_audio.opened[-1]
    audio = stream(dev)
    expected = np.concatenate([RAMP[0:800], *([RAMP[499:800]] * 3), RAMP[799:1000]])
    assert (audio[: len(expected)] == expected).all()
    panel.player._on_tick()  # the GUI tick notices the end
    assert not panel.is_playing() and panel.play_button.text() == PLAY_TEXT


def test_with_loop_off_it_plays_start_to_end_once(panel, fake_audio):
    panel.set_sample(make())
    panel.loop_check.setChecked(False)
    panel.play()
    audio = stream(fake_audio.opened[-1])
    assert (audio[:1000] == RAMP).all() and not audio[1000:].any()


def test_a_sample_marked_no_loop_plays_once_even_though_it_has_a_loop(panel, fake_audio):
    panel.set_sample(make(mode="NOLOOP"))
    panel.play()
    assert (stream(fake_audio.opened[-1])[:1000] == RAMP).all()


def test_the_play_markers_limit_what_plays(panel, fake_audio):
    s = make(mode="NOLOOP")
    s = LoadedSample("S", RAMP.copy(), 44100, SampleInfo(**{**s.info.__dict__, "start": 200, "end": 399}))
    panel.set_sample(s)
    panel.play()
    audio = stream(fake_audio.opened[-1])
    assert (audio[:200] == RAMP[200:400]).all() and not audio[200:].any()


def test_picking_another_loop_while_playing_switches_to_it(panel, fake_audio):
    panel.set_sample(make(loops=((2, 400, 100.0, None), (5, 900, 200.0, None))))  # loop 2: 300..400, loop 5: 700..900
    panel.play()
    opened = len(fake_audio.opened)
    panel.loop_combo.setCurrentIndex(1)
    assert len(fake_audio.opened) == opened + 1 and panel.is_playing()  # restarted, so the choice is heard at once
    audio = first(fake_audio.opened[-1], 901 + 201)
    assert (audio[:901] == RAMP[:901]).all()  # the attack, up to the loop's end
    assert (audio[901 : 901 + 201] == RAMP[700:901]).all()  # then loop 5's region


def test_changing_the_loop_option_while_stopped_does_not_start_playback(panel, fake_audio):
    panel.set_sample(make())
    panel.loop_check.setChecked(False)
    assert not fake_audio.opened and not panel.is_playing()


def test_the_playhead_follows_playback_then_clears(panel, fake_audio):
    panel.set_sample(make(mode="NOLOOP"))
    panel.play()
    dev = fake_audio.opened[-1]
    dev.gen.send(300)
    panel.player._on_tick()
    assert panel.wave._playhead == 300
    panel.stop()
    assert panel.wave._playhead is None


def test_clicking_the_waveform_plays_from_there(panel, fake_audio):
    panel.set_sample(make())
    panel.play_from(600)
    audio = stream(fake_audio.opened[-1])
    assert (audio[:400] == RAMP[600:1000]).all() and not audio[400:].any()  # from frame 600 to the end, once
    assert panel.play_button.text() == STOP_TEXT


def test_a_new_sample_stops_the_old_one(panel, fake_audio):
    panel.set_sample(make())
    panel.play()
    dev = fake_audio.opened[-1]
    panel.set_sample(make())
    assert dev.closed and not panel.is_playing()


def test_nothing_plays_with_audio_turned_off(panel, fake_audio, monkeypatch):
    monkeypatch.setenv(preview.NO_AUDIO_ENV, "1")
    panel.set_sample(make())
    assert not panel.play_button.isEnabled() and "turned off" in panel.play_button.toolTip()
    panel.play()
    assert not fake_audio.opened


def test_a_device_that_wont_open_says_so(panel, fake_audio, monkeypatch):
    def boom(**kw):
        raise preview.miniaudio.MiniaudioError("no device")

    panel.set_sample(make())
    monkeypatch.setattr(preview.miniaudio, "PlaybackDevice", boom)
    panel.play()
    assert not panel.is_playing() and "audio output" in panel.info_label.text()
    assert panel.play_button.text() == PLAY_TEXT


def test_the_hold_loop_tune_reaches_the_player(panel, fake_audio):
    panel.set_sample(make(hold_tune_cents=50))
    panel.play()
    assert panel.player._live_loop_tune_cents == 50  # AKAISDS applies the hold-loop tune to the loop region
