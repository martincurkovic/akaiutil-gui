"""Sample-rate conversion by polyphase windowed-sinc filtering (numpy only).

Importing a 48 or 96 kHz recording into a sampler that tops out at 44.1 kHz needs a proper anti-aliasing filter:
miniaudio's built-in resampler lets 30 kHz content fold back at only -14 dB, which is audible. This one has a Kaiser-windowed
low-pass that is flat to 92% of the new Nyquist frequency and 80 dB down from there.
"""

import math
from fractions import Fraction

import numpy as np

STOPBAND_DB = 80.0  # how far down the filter is past the new Nyquist frequency
PASSBAND = 0.92  # fraction of the new Nyquist frequency that passes unchanged
MAX_PHASES = 2000  # an unusual ratio is approximated rather than built from a filter bank this big


def resampled_rate(rate_in: int, rate_out: int) -> int:
    """The rate the result really has: rate_out, unless the ratio had to be approximated (an odd source rate)."""
    up, down = _ratio(rate_in, rate_out)
    return round(rate_in * up / down)


def _ratio(rate_in: int, rate_out: int) -> tuple[int, int]:
    """(up, down): the exact ratio, or for odd rates a close one that doesn't need a huge filter bank."""
    fraction = Fraction(rate_out, rate_in)
    if fraction.numerator > MAX_PHASES:
        fraction = fraction.limit_denominator(max(1, int(MAX_PHASES / fraction)))
    return fraction.numerator, fraction.denominator


def _kaiser_beta(attenuation_db: float) -> float:
    if attenuation_db > 50:
        return 0.1102 * (attenuation_db - 8.7)
    if attenuation_db >= 21:
        return 0.5842 * (attenuation_db - 21) ** 0.4 + 0.07886 * (attenuation_db - 21)
    return 0.0


def _design(up: int, down: int) -> tuple[np.ndarray, int]:
    """The low-pass, as a polyphase bank: bank[r][q] weights input sample (i0 - J + q) for an output whose phase is r."""
    biggest = max(up, down)
    cutoff = 0.5 * (1 + PASSBAND) / 2 / biggest  # half-way through the transition band, in cycles per (up-sampled) sample
    transition = 0.5 * (1 - PASSBAND) / biggest
    taps = math.ceil((STOPBAND_DB - 7.95) / (2.285 * 2 * math.pi * transition))
    taps_per_phase = max(1, math.ceil(taps / up))
    J = taps_per_phase // 2 + 1
    half = J * up  # the filter reaches J inputs either side
    n = np.arange(-half, half + 1)
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.kaiser(2 * half + 1, _kaiser_beta(STOPBAND_DB))
    h *= up / h.sum()  # unity gain in the pass band once the zero-stuffing is accounted for
    k = half + np.arange(up)[:, None] + (J - np.arange(2 * J + 1))[None, :] * up  # the tap that multiplies input i0 - (J - q)
    bank = np.where((k >= 0) & (k < h.size), h[np.clip(k, 0, h.size - 1)], 0.0)
    return bank, J


def resample(data: np.ndarray, rate_in: int, rate_out: int) -> np.ndarray:
    """Convert (frames, channels) float audio from rate_in to rate_out. Returns float64 of the same layout.

    The output length is ceil(frames * rate_out / rate_in), and a sound starts at the same moment before and after.
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    if rate_in == rate_out:
        return data
    up, down = _ratio(rate_in, rate_out)
    frames, channels = data.shape
    n_out = -(-frames * up // down)
    bank, J = _design(up, down)
    width = 2 * J + 1
    out = np.zeros((n_out, channels))
    if n_out == 0:
        return out
    last_start = ((n_out - 1) * down) // up
    padded = np.zeros((frames + last_start + 2 * width + J, channels))
    padded[J : J + frames] = data  # input i0 - J + q lives at padded[i0 + q]
    for c in range(min(up, n_out)):
        r = (c * down) % up
        base = (c * down) // up
        count = len(range(c, n_out, up))  # outputs c, c + up, c + 2 up, ...
        starts = base + np.arange(count) * down
        windows = np.lib.stride_tricks.sliding_window_view(padded, width, axis=0)[starts]  # (count, channels, width)
        out[c::up] = windows @ bank[r]
    return out
