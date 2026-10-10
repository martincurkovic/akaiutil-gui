"""The preview waveform: its envelope against AKAISDS's original, the pixel mapping, and what it paints."""

import numpy as np
import pytest

from akaiutil_gui.engine.models import Loop
from akaiutil_gui.ui import theme
from akaiutil_gui.ui.waveform_preview import PreviewWaveform, build_envelope, frame_for_x, x_for_frame


def akaisds_envelope(samples, width):
    """AKAISDS's ui/waveform_view.py build_envelope + _bridge_envelope_gaps, as the reference."""
    n, out = len(samples), []
    for x in range(width):
        lo_i = (x * n) // width
        hi_i = max(lo_i + 1, ((x + 1) * n) // width)
        chunk = samples[lo_i:hi_i]
        out.append([min(chunk), max(chunk)])
    for i in range(1, width):
        lo, hi = out[i]
        prev_lo, prev_hi = out[i - 1]
        if lo > prev_hi:
            out[i][0] = prev_hi
        elif hi < prev_lo:
            out[i][1] = prev_lo
    return [tuple(c) for c in out]


@pytest.mark.parametrize("n, width", [(1000, 100), (1000, 999), (1000, 1000), (7, 50), (1, 10), (123456, 713), (50, 1)])
def test_the_numpy_envelope_matches_the_original_algorithm(n, width):
    samples = np.random.default_rng(n + width).integers(-32768, 32768, n).astype(np.int16)
    mins, maxs = build_envelope(samples, width)
    assert list(zip(mins.tolist(), maxs.tolist())) == akaisds_envelope(samples.tolist(), width)


def test_an_empty_sample_has_a_flat_envelope():
    mins, maxs = build_envelope(np.array([], dtype=np.int16), 5)
    assert len(mins) == len(maxs) == 5 and not mins.any() and not maxs.any()


def test_the_envelope_of_a_minute_of_audio_is_instant():
    import time

    samples = np.random.default_rng(0).integers(-32768, 32768, 44100 * 60).astype(np.int16)
    started = time.perf_counter()
    build_envelope(samples, 1200)
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize("frames, width", [(1000, 500), (1000, 1001), (2, 300)])
def test_x_and_frame_are_inverses_at_the_ends_and_monotonic_between(frames, width):
    assert x_for_frame(0, width, frames) == 0 and x_for_frame(frames - 1, width, frames) == pytest.approx(width - 1)
    assert frame_for_x(0, width, frames) == 0 and frame_for_x(width - 1, width, frames) == frames - 1
    frames_at = [frame_for_x(x, width, frames) for x in range(width)]
    assert frames_at == sorted(frames_at)
    assert frame_for_x(-50, width, frames) == 0 and frame_for_x(10**6, width, frames) == frames - 1  # outside: clamped


# -------------------------------------------------------------------- painting
@pytest.fixture
def wave(qt_app):
    theme.apply_to_app(qt_app, "dark")
    w = PreviewWaveform()
    w.resize(400, 96)
    w.show()
    return w


def image(w):
    qt_app = w.window()
    return w.grab().toImage()


def colour(img, x, y):
    return img.pixelColor(x, y).name()


def test_it_shows_a_hint_until_there_is_audio(wave):
    assert wave.frames == 0
    assert not wave.hasFocus()
    wave.set_audio(np.zeros(100, dtype=np.int16))
    assert wave.frames == 100
    wave.clear()
    assert wave.frames == 0


def test_the_waveform_is_drawn_in_the_accent_colour_where_there_is_signal(wave):
    p = theme.DARK_PALETTE
    samples = np.zeros(4000, dtype=np.int16)
    samples[2000:2100] = 30000  # a burst in the middle
    wave.set_audio(samples)
    img = image(wave)
    accent = p["accent"].lower()
    in_burst = [y for y in range(96) if colour(img, 200, y) == accent]
    elsewhere = [y for y in range(96) if colour(img, 40, y) == accent]
    # inside the burst every sample is 30000, so a column draws a point near the top; with no signal the line
    # sits on the centre
    assert in_burst and max(in_burst) < 40, "the burst should show as accent-coloured pixels well above the centre line"
    assert all(abs(y - 48) <= 1 for y in elsewhere), "no signal: nothing above the centre"
    assert colour(img, 40, 20) == p["bg_input"].lower()


def test_the_loop_is_shaded_and_its_edges_marked(wave):
    p = theme.DARK_PALETTE
    wave.set_audio(np.zeros(4000, dtype=np.int16))
    before = image(wave)
    wave.set_loop(Loop(1, end=2999, length=1000.0, time_ms=None))  # frames 1999..2999: x ~ 200..300 of 400
    after = image(wave)
    assert colour(before, 250, 10) != colour(after, 250, 10)  # shading appears inside the loop
    assert colour(after, 250, 10) != colour(after, 50, 10)  # and not outside it
    assert colour(after, 200, 10) == p["warning"].lower() or colour(after, 199, 10) == p["warning"].lower()  # an edge line
    wave.set_loop(None)
    assert colour(image(wave), 250, 10) == colour(before, 250, 10)


def test_the_playhead_moves_and_can_be_cleared(wave):
    wave.set_audio(np.zeros(4000, dtype=np.int16))
    blank = colour(image(wave), 100, 30)
    wave.set_playhead(1000)  # x ~ 100
    assert colour(image(wave), 100, 30) == theme.DARK_PALETTE["text_bright"].lower()
    wave.set_playhead(None)
    assert colour(image(wave), 100, 30) == blank


def test_outside_the_play_markers_is_dimmed(wave):
    samples = np.tile(np.array([30000, -30000], dtype=np.int16), 2000)  # full-height lines everywhere
    wave.set_audio(samples, play_start=1000, play_end=3000)  # plays x ~ 100..300
    img = image(wave)
    assert colour(img, 200, 20) == theme.DARK_PALETTE["accent"].lower()  # inside the play range: undimmed
    assert colour(img, 50, 20) != colour(img, 200, 20) and colour(img, 350, 20) != colour(img, 200, 20)  # dimmed outside
    assert colour(img, 50, 20) != theme.DARK_PALETTE["bg_input"].lower()  # dimmed, not erased: it's still the waveform


def test_it_follows_the_theme(wave, qt_app):
    wave.set_audio(np.full(2000, 20000, dtype=np.int16))
    dark = colour(image(wave), 100, 30)
    theme.apply_to_app(qt_app, "light")
    assert colour(image(wave), 100, 30) != dark
    theme.apply_to_app(qt_app, "dark")


def test_clicking_asks_to_play_from_that_frame(wave, qt_app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    wave.set_audio(np.zeros(4001, dtype=np.int16))
    clicked = []
    wave.seekRequested.connect(clicked.append)
    QTest.mouseClick(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(200, 40))
    assert len(clicked) == 1 and abs(clicked[0] - 2000) < 25  # the middle of the sample
    wave.clear()
    QTest.mouseClick(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(200, 40))
    assert len(clicked) == 1  # nothing loaded: nothing to play
