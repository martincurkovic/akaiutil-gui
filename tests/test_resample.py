"""The sample-rate converter used when an import is faster than the samplers can play."""

import numpy as np
import pytest

from akaiutil_gui.audio import resample


def tone(freq, rate, seconds=2.0, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    return amp * np.sin(2 * np.pi * freq * t)


def spectrum_db(y, rate):
    window = np.hanning(len(y))
    mag = np.abs(np.fft.rfft(y * window))
    return np.fft.rfftfreq(len(y), 1 / rate), 20 * np.log10(np.maximum(mag, 1e-12) / mag.max())


@pytest.mark.parametrize("rate_in", [48000, 88200, 96000, 192000])
def test_a_tone_in_the_pass_band_comes_through_at_the_same_level_and_pitch(rate_in):
    y = resample.resample(tone(1000, rate_in), rate_in, 44100)[:, 0]
    assert len(y) == pytest.approx(2 * 44100, abs=1)
    middle = y[4000:-4000]  # (away from the ends, where the filter is still filling)
    assert np.sqrt(np.mean(middle**2)) == pytest.approx(0.5 / np.sqrt(2), rel=0.01)  # within 0.1 dB
    freqs, db = spectrum_db(middle, 44100)
    assert freqs[db.argmax()] == pytest.approx(1000, abs=5)


@pytest.mark.parametrize("rate_in, freq", [(96000, 30000), (96000, 23000), (48000, 23000), (192000, 40000)])
def test_what_is_above_the_new_nyquist_frequency_is_removed_not_folded_back(rate_in, freq):
    y = resample.resample(tone(freq, rate_in), rate_in, 44100)[:, 0][4000:-4000]
    assert np.abs(y).max() < 10 ** (-70 / 20) * 0.5  # at least 70 dB below the input's level


def test_the_top_of_the_band_survives(rate_in=96000):
    y = resample.resample(tone(19000, rate_in), rate_in, 44100)[:, 0][4000:-4000]
    assert np.sqrt(np.mean(y**2)) == pytest.approx(0.5 / np.sqrt(2), rel=0.03)  # within 0.3 dB at 19 kHz


def test_a_click_stays_where_it_was_in_time():
    x = np.zeros(48000)
    x[24000] = 1.0
    y = resample.resample(x, 48000, 44100)[:, 0]
    assert abs(int(np.argmax(np.abs(y))) - 24000 * 44100 / 48000) <= 1  # no delay from the filter


def test_the_length_is_the_exact_ratio_rounded_up():
    for rate_in, frames in [(48000, 48001), (96000, 12345), (88200, 7)]:
        out = resample.resample(np.zeros((frames, 1)), rate_in, 44100)
        assert out.shape == (-(-frames * 44100 // rate_in), 1)


def test_stereo_channels_are_converted_independently():
    left, right = tone(500, 48000), tone(2000, 48000)
    y = resample.resample(np.stack([left, right], 1), 48000, 44100)
    assert y.shape[1] == 2
    for channel, freq in ((0, 500), (1, 2000)):
        freqs, db = spectrum_db(y[4000:-4000, channel], 44100)
        assert freqs[db.argmax()] == pytest.approx(freq, abs=5)


def test_the_same_rate_is_left_alone():
    x = tone(440, 44100, 0.1)
    assert np.array_equal(resample.resample(x, 44100, 44100)[:, 0], x)


def test_the_other_cap_used_for_s900_works_too():
    y = resample.resample(tone(1000, 48000), 48000, 40000)[:, 0]
    assert len(y) == 2 * 40000
    assert np.sqrt(np.mean(y[3000:-3000] ** 2)) == pytest.approx(0.5 / np.sqrt(2), rel=0.01)


def test_an_odd_source_rate_is_approximated_and_the_real_result_rate_reported():
    rate_in = 47999
    y = resample.resample(tone(1000, rate_in, 1.0), rate_in, 44100)[:, 0]
    real = resample.resampled_rate(rate_in, 44100)
    assert abs(real - 44100) <= 50 and len(y) == pytest.approx(rate_in * real / rate_in, abs=2)
    assert resample.resampled_rate(48000, 44100) == 44100


def test_nothing_in_nothing_out():
    assert resample.resample(np.zeros((0, 2)), 48000, 44100).shape == (0, 2)
