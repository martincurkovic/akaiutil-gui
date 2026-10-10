"""Plays one sample (or a sample's loop) out the user's chosen audio device.

This is AKAISDS's preview backend (core/audio_preview.py there), ported with its behaviour intact. It uses
miniaudio rather than Qt's QAudioSink: miniaudio talks to the platform's native audio API directly
(CoreAudio, WASAPI, PipeWire/ALSA) through a real-time callback on its own thread, which avoided the
buffer underruns and crackle QAudioSink produced on Linux and macOS.

The things below are fixes AKAISDS earned the hard way, especially on macOS; don't "simplify" them away:

* miniaudio pulls audio through a generator and copies exactly what it yields into its native buffer. A
  SHORT chunk leaves the rest of that buffer holding stale audio from an earlier callback, heard as a chop
  or glitch. So every chunk is exactly the number of frames asked for (the final one zero-padded), and
  segments (attack, each loop pass, release tail) are concatenated seamlessly instead of yielded one by one.
* A running device at interpreter exit crashes macOS (SIGTRAP on CoreAudio's thread, which calls back into
  Python), so every player is stopped from an atexit hook.
* The generator runs on miniaudio's native thread, where an uncaught exception would vanish or take the
  process down, so it logs and ends the preview cleanly instead.

Differences from AKAISDS: settings come from this app's settings module, and sample data may be a numpy
array as well as a list (packing and loop-tune resampling use numpy when given one, so a minute-long sample
doesn't stall the UI; the results are identical, which the tests check).
"""

import atexit
import logging
import os
import struct
import weakref

import miniaudio
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from akaiutil_gui import settings

log = logging.getLogger(__name__)

# set to "1" on machines with no audio device (CI): nothing is enumerated or opened
NO_AUDIO_ENV = "AKAIUTIL_GUI_NO_AUDIO"

# every player that may own a running device, stopped at interpreter exit
_LIVE_PLAYERS: "weakref.WeakSet[PreviewPlayer]" = weakref.WeakSet()


def _stop_all_players() -> None:
    for player in list(_LIVE_PLAYERS):
        try:
            player.stop()
        except Exception:  # noqa: BLE001 - at exit there's nothing useful left to do with a failure
            pass


atexit.register(_stop_all_players)

_PLAYHEAD_TICK_MS = 30  # how often the playhead position is re-emitted during playback
_SAMPLE_FORMAT = miniaudio.SampleFormat.SIGNED16
_BYTES_PER_FRAME = 2  # per channel


# ------------------------------------------------------------------ PCM helpers
def _is_array(x) -> bool:
    return isinstance(x, np.ndarray)


def _pack_values(left, right=None) -> bytes:
    """Little-endian int16 PCM for already-sliced channels: mono, or interleaved L R L R when `right` is given
    (the shorter channel decides the length)."""
    if _is_array(left):
        if right is None:
            return left.astype("<i2").tobytes()
        n = min(len(left), len(right))
        out = np.empty(2 * n, dtype="<i2")
        out[0::2], out[1::2] = left[:n], right[:n]
        return out.tobytes()
    if right is None:
        return struct.pack("<" + "h" * len(left), *left)
    n = min(len(left), len(right))
    interleaved = [0] * (2 * n)
    interleaved[0::2] = left[:n]
    interleaved[1::2] = right[:n]
    return struct.pack("<" + "h" * len(interleaved), *interleaved)


def _pack_region(left, right, lo: int, hi: int) -> bytes:
    """The inclusive [lo, hi] frames of `left` (and `right`, for stereo) as PCM bytes."""
    return _pack_values(left[lo : hi + 1], None if right is None else right[lo : hi + 1])


def _resample_region_for_cents(samples, lo: int, hi: int, cents: float):
    """The inclusive [lo, hi] region resampled to sound `cents` cents higher (positive) or lower (negative).

    Plain linear interpolation, the same technique a hardware sampler's own tune control uses: shifting the
    playback rate shifts pitch and duration together. Matches the hold-loop tune's +/-50 cent range, at
    most a ~3% rate change.
    """
    region = samples[lo : hi + 1]
    if cents == 0 or len(region) < 2:
        return region
    ratio = 2.0 ** (cents / 1200.0)
    out_len = max(1, round(len(region) / ratio))
    last = len(region) - 1
    if _is_array(region):
        positions = np.arange(out_len) * ratio  # beyond the last frame np.interp holds the last value, as below
        return np.rint(np.interp(positions, np.arange(len(region)), region)).astype(region.dtype)
    out = [0] * out_len
    for i in range(out_len):
        src = i * ratio
        idx = int(src)
        if idx >= last:
            out[i] = region[last]
            continue
        frac = src - idx
        a, b = region[idx], region[idx + 1]
        out[i] = int(round(a + (b - a) * frac))
    return out


def _shifted_framerate(framerate: int, pitch_shift_semitones: float) -> int:
    if not pitch_shift_semitones:
        return framerate
    return max(1, round(framerate * 2.0 ** (pitch_shift_semitones / 12.0)))


# ------------------------------------------------------------------ devices
def audio_disabled() -> bool:
    return os.environ.get(NO_AUDIO_ENV) == "1"


def list_output_devices() -> list[dict]:
    """Output devices as miniaudio reports them; [] if there are none or audio isn't available."""
    if audio_disabled():
        return []
    try:
        return miniaudio.Devices().get_playbacks()
    except Exception:  # noqa: BLE001 - e.g. no audio subsystem on a headless machine
        log.warning("couldn't list audio output devices", exc_info=True)
        return []


def device_id_string(device: dict) -> str:
    # device["id"] is an opaque cffi union (an ALSA string on Linux, a CoreAudio UID on macOS): hex-encoded so it
    # round-trips through the settings store. Stable across repeated enumerations of the same device.
    return bytes(miniaudio.ffi.buffer(device["id"])).hex()


def find_output_device(id_string: str | None) -> dict | None:
    if not id_string:
        return None
    return next((d for d in list_output_devices() if device_id_string(d) == id_string), None)


def resolve_output_device() -> dict | None:
    """The device a preview should use, or None for "the system default" (a real answer to miniaudio).

    A saved device can easily be gone (an interface unplugged since it was chosen); falling back to the
    default beats refusing to preview at all.
    """
    saved = settings.get_audio_output_device()
    if not saved:
        return None
    device = find_output_device(saved)
    if device is None:
        log.error("saved output device %r not found, falling back to the system default", saved)
    return device


# ------------------------------------------------------------------ the player
class PreviewPlayer(QObject):
    """Plays one PCM region at a time, with a single device. A new play()/play_loop() always stops
    whatever is playing first (retrigger, not overlap).

    miniaudio pulls audio through a plain Python generator invoked from its own native real-time thread, so
    the generators below must not do anything slow or blocking. They track progress in plain attributes
    that the GUI-thread timer only ever READS, so there is nothing to lock.

    position_changed(frame) fires about every 30 ms while sounding, with the current absolute frame (same
    indexing as the samples passed in); finished() fires once when playback stops for any reason.
    """

    position_changed = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        _LIVE_PLAYERS.add(self)
        self._device = None
        self._current_frame = 0
        # set by a generator right before it ends for any reason; the tick notices and stops the device.
        # Stays False for a "Hold" loop (dwell_ms=None), which only ever ends through stop().
        self._finished = False
        # live loop bounds/tune for an in-progress play_loop(), picked up at the start of its next pass
        self._live_loop_start = 0
        self._live_loop_end = 0
        self._live_loop_tune_cents = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_PLAYHEAD_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    def is_playing(self) -> bool:
        return self._device is not None

    def _open_device(self, framerate: int, channels: int = 1):
        """Opens the configured output device; None (already logged) on failure. Never raises."""
        if audio_disabled():
            log.info("audio preview is disabled (%s=1)", NO_AUDIO_ENV)
            return None
        device_entry = resolve_output_device()
        device_id = device_entry["id"] if device_entry is not None else None
        # stored as a frame count, like a DAW's buffer-size dropdown; miniaudio wants milliseconds, which depends
        # on this sound's own rate
        buffersize_msec = max(1, round(settings.get_audio_buffer_samples() / framerate * 1000))
        try:
            return miniaudio.PlaybackDevice(
                output_format=_SAMPLE_FORMAT,
                nchannels=channels,
                sample_rate=framerate,
                buffersize_msec=buffersize_msec,
                device_id=device_id,
            )
        except miniaudio.MiniaudioError:
            log.error("couldn't open the output device", exc_info=True)
            return None

    def _open_device_for(self, framerate: int, right_samples):
        return self._open_device(framerate) if right_samples is None else self._open_device(framerate, 2)

    def _start(self, device, generator_fn, start_frame: int) -> None:
        gen = generator_fn()
        next(gen)  # primed with an empty yield first: miniaudio's documented generator protocol
        try:
            device.start(gen)
        except Exception:  # noqa: BLE001
            log.error("couldn't start playback", exc_info=True)
            device.close()
            return
        self._device = device
        self._current_frame = start_frame
        self._finished = False
        self._timer.start()

    def play(self, samples, start_frame: int, end_frame: int, framerate: int, pitch_shift_semitones: float = 0, right_samples=None) -> None:
        """Play frames [start_frame, end_frame] (inclusive) once. `samples` is the whole mono sample (list or
        numpy array); `right_samples`, if given, is the right channel of a stereo sample (then `samples` is the
        left one). A pitch shift opens the device at a scaled rate (pitch and duration move together)."""
        self.stop()
        if len(samples[start_frame : end_frame + 1]) == 0:
            return
        pcm_bytes = _pack_region(samples, right_samples, start_frame, end_frame)
        bytes_per_frame = _BYTES_PER_FRAME * (1 if right_samples is None else 2)
        device = self._open_device_for(_shifted_framerate(framerate, pitch_shift_semitones), right_samples)
        if device is None:
            return

        def generator():
            try:
                required_frames = yield b""
                pos = 0
                while pos < len(pcm_bytes):
                    end = pos + required_frames * bytes_per_frame
                    out = pcm_bytes[pos:end]
                    pos += len(out)
                    self._current_frame = start_frame + pos // bytes_per_frame
                    if len(out) < required_frames * bytes_per_frame:
                        # the true final chunk: zero-pad it so miniaudio's memmove doesn't leave stale native
                        # buffer content to play as a click right at the end
                        out = out + b"\x00" * (required_frames * bytes_per_frame - len(out))
                    required_frames = yield out
                # exhausted: the device keeps calling back and gets silence; `finally` lets the tick stop it
            except Exception:  # noqa: BLE001 - this runs on miniaudio's native thread; see the module docstring
                log.error("playback generator raised", exc_info=True)
            finally:
                self._finished = True

        self._start(device, generator, start_frame)

    def play_loop(
        self,
        samples,
        start_frame: int,
        loop_start_frame: int,
        loop_end_frame: int,
        end_frame: int,
        framerate: int,
        dwell_ms: int | None = None,
        loop_tune_cents: float = 0,
        pitch_shift_semitones: float = 0,
        right_samples=None,
    ) -> None:
        """Simulates a sample's own loop: plays the attack once ([start_frame, loop_end_frame]), repeats the loop
        region ([loop_start_frame, loop_end_frame]) for dwell_ms milliseconds, or indefinitely if dwell_ms is None
        ("Hold": it never ends on its own, only through stop()), then plays the release tail ([loop_end_frame,
        end_frame]) once.

        A dwell that doesn't divide evenly into the loop length always finishes its CURRENT pass before moving
        to the tail, like the hardware and without a glitch. Callers decide whether a loop applies at all (a
        sample that doesn't loop should use play()).

        While this plays, update_loop_points() / update_loop_tune_cents() change the loop region; they take
        effect at the start of the next pass, because jumping to a new position mid-buffer is an audible click.
        loop_tune_cents tunes ONLY the loop region, as the hardware's hold-loop tune does.
        """
        self.stop()
        if loop_end_frame <= loop_start_frame:
            # a zero-length loop has nothing to repeat: play the whole range once
            self.play(samples, start_frame, end_frame, framerate, pitch_shift_semitones, right_samples=right_samples)
            return

        channels = 1 if right_samples is None else 2
        bytes_per_frame = _BYTES_PER_FRAME * channels
        attack_bytes = _pack_region(samples, right_samples, start_frame, loop_end_frame)
        tail_bytes = _pack_region(samples, right_samples, loop_end_frame, end_frame)
        loop_frame_budget = None if dwell_ms is None else max(0, round(dwell_ms / 1000 * framerate))

        self._live_loop_start = loop_start_frame
        self._live_loop_end = loop_end_frame
        self._live_loop_tune_cents = loop_tune_cents

        device = self._open_device_for(_shifted_framerate(framerate, pitch_shift_semitones), right_samples)
        if device is None:
            return

        def _loop_region_bytes(lo, hi, cents):
            region = _resample_region_for_cents(samples, lo, hi, cents)
            right_region = None if right_samples is None else _resample_region_for_cents(right_samples, lo, hi, cents)
            return _pack_values(region, right_region)

        def _segments():
            # yields (segment_start_frame, raw_bytes) in play order: the attack once, the loop region repeated for
            # the budget (or forever), then the release tail once. Live loop updates are only picked up here,
            # between whole passes.
            if attack_bytes:
                yield start_frame, attack_bytes
            looped_frames = 0
            cur_start, cur_end, cur_cents = loop_start_frame, loop_end_frame, self._live_loop_tune_cents
            loop_bytes = _loop_region_bytes(cur_start, cur_end, cur_cents)
            while loop_frame_budget is None or looped_frames < loop_frame_budget:
                live_start, live_end, live_cents = self._live_loop_start, self._live_loop_end, self._live_loop_tune_cents
                if live_end > live_start and (live_start, live_end, live_cents) != (cur_start, cur_end, cur_cents):
                    cur_start, cur_end, cur_cents = live_start, live_end, live_cents
                    loop_bytes = _loop_region_bytes(cur_start, cur_end, cur_cents)
                looped_frames += len(loop_bytes) // bytes_per_frame
                yield cur_start, loop_bytes
            # the release tail, from the loop's last-used end point (which may have moved)
            out_bytes = _pack_region(samples, right_samples, cur_end, end_frame) if cur_end != loop_end_frame else tail_bytes
            if out_bytes:
                yield cur_end, out_bytes

        def generator():
            try:
                required_frames = yield b""
                # Every yield must be EXACTLY required_frames frames (never short) except the true final one:
                # a short yield leaves the rest of miniaudio's native buffer holding stale audio from an earlier
                # callback, the "chop"/"glitch" AKAISDS measured at both large and small buffer sizes. So
                # segments and passes are concatenated seamlessly and chunked to the size actually asked for.
                seg_iter = _segments()
                seg_start_frame, seg_bytes, seg_pos = 0, b"", 0

                def _advance_segment():
                    nonlocal seg_start_frame, seg_bytes, seg_pos
                    try:
                        seg_start_frame, seg_bytes = next(seg_iter)
                    except StopIteration:
                        seg_bytes = None
                    seg_pos = 0

                _advance_segment()
                while seg_bytes is not None:
                    need = required_frames * bytes_per_frame
                    out = bytearray()
                    while len(out) < need and seg_bytes is not None:
                        take = min(len(seg_bytes) - seg_pos, need - len(out))
                        out += seg_bytes[seg_pos : seg_pos + take]
                        seg_pos += take
                        self._current_frame = seg_start_frame + seg_pos // bytes_per_frame
                        if seg_pos >= len(seg_bytes):
                            _advance_segment()
                    if not out:
                        break
                    if len(out) < need:
                        out += b"\x00" * (need - len(out))  # the true final chunk: zero-pad, as in play()
                    required_frames = yield bytes(out)
            except Exception:  # noqa: BLE001 - native thread; see the module docstring
                log.error("playback generator raised", exc_info=True)
            finally:
                self._finished = True

        self._start(device, generator, start_frame)

    def update_loop_points(self, loop_start_frame: int, loop_end_frame: int) -> None:
        """Change the loop region of an in-progress play_loop(). A no-op for degenerate bounds."""
        if loop_end_frame <= loop_start_frame:
            return
        self._live_loop_start = loop_start_frame
        self._live_loop_end = loop_end_frame

    def update_loop_tune_cents(self, cents: float) -> None:
        self._live_loop_tune_cents = cents

    def stop(self) -> None:
        was_playing = self._device is not None
        self._timer.stop()
        if self._device is not None:
            self._device.stop()
            self._device.close()
            self._device = None
        if was_playing:
            self.finished.emit()

    def _on_tick(self) -> None:
        if self._device is None:
            return
        if self._finished:
            self.stop()
            return
        self.position_changed.emit(self._current_frame)
