"""Building an S1000/S3000 sample header (Qt-free): what a sample's loops and root note are stored in.

Layout from `struct akai_sample1000_s` in akaiutil's akaiutil_file.h (150 bytes; the S3000's is the same plus 42 bytes of zeros):
  0 blockid(3)  1 bandw  2 rkey  3..14 name  15 0x80  16 lnum  17 lfirst  19 pmode  20 ctune  21 stune  22..25 locat
  26..29 slen  30..33 start  34..37 end  38.. 8 loops of 12 bytes (at, flen, len, time)  136..137 stpaira  138..139 srate
  140 hltoff
akaiutil itself writes the name and the number of samples, and uses the rest as given when the WAV carries this header.
"""

import struct
from dataclasses import dataclass

LOOP_HOLD = 9999  # a loop time meaning "until the key is released"
MAX_LOOPS = 8
CHUNK_IDS = {"S1000": b"S1H ", "S3000": b"S3H "}


def build_header(sample_type: str, rate: int, frames: int, root: int = 60, loops: list[tuple[int, int]] = ()) -> bytes:
    """The header for a sample of `frames` frames at `rate`, with a root note and loops given as (first, last) frames."""
    size = 192 if sample_type == "S3000" else 150
    h = bytearray(size)
    h[0] = 3  # blockid
    h[1] = 1  # 20 kHz bandwidth
    h[2] = root
    h[3:15] = bytes([10] * 12)  # (the name: a blank one, akaiutil fills it in)
    h[15] = 0x80
    usable = [(s, e) for s, e in loops if 0 <= s < e < frames][:MAX_LOOPS]
    h[16] = len(usable)  # lnum
    h[17] = 0  # lfirst
    h[19] = 0  # pmode: loop
    h[30:34] = struct.pack("<I", 0)  # start
    h[34:38] = struct.pack("<I", max(frames - 1, 0))  # end
    for i, (start, end) in enumerate(usable):
        at = 38 + 12 * i
        h[at : at + 4] = struct.pack("<I", end)  # the loop's last frame
        h[at + 4 : at + 6] = struct.pack("<H", 0)
        h[at + 6 : at + 10] = struct.pack("<I", end - start)  # how far back it starts
        h[at + 10 : at + 12] = struct.pack("<H", LOOP_HOLD)
    h[136:138] = struct.pack("<H", 0xFFFF)  # stpaira: no stereo partner
    h[138:140] = struct.pack("<H", min(rate, 0xFFFF))
    return bytes(h)


@dataclass(frozen=True)
class HeaderInfo:
    """What a DAW wants to know from a sample header."""

    root: int  # MIDI note the sample plays at its original pitch
    rate: int
    frames: int
    looping: bool  # the play mode loops (a header can hold loops that its play mode ignores)
    loops: tuple[tuple[int, int], ...]  # (first frame, last frame) of each loop that is in use


PMODE_LOOP, PMODE_LOOP_UNTIL_RELEASE = 0, 1


def parse_header(data: bytes) -> HeaderInfo | None:
    """Read an S1000/S3000 sample header (the same fields build_header writes). None if it isn't one."""
    if len(data) < 150 or data[0] != 3:
        return None
    frames = struct.unpack_from("<I", data, 26)[0]
    loops = []
    for i in range(min(data[16], MAX_LOOPS)):
        at, _fine, length, time = struct.unpack_from("<IHIH", data, 38 + 12 * i)
        if time == 0 or length == 0 or length > at or at >= frames:
            continue  # (time 0 is a loop that is switched off)
        loops.append((at - length, at))
    return HeaderInfo(
        root=data[2] if data[2] <= 127 else 60,
        rate=struct.unpack_from("<H", data, 138)[0],
        frames=frames,
        looping=data[19] in (PMODE_LOOP, PMODE_LOOP_UNTIL_RELEASE),
        loops=tuple(loops),
    )


# ---------------------------------------------------------------------------- reading and editing a header
PLAY_MODES = {0: "LOOP", 1: "LOOPNOTREL", 2: "NOLOOP", 3: "TOEND"}  # pmode, as akaiutil names them
HEADER_SIZES = {"S1000": 150, "S3000": 192}
MAX_TUNE = 50  # the samplers' own range for the semitone and cent tuning
LOOP_ENTRY = 12
LOOP_AT = 38  # where the 8 loops start
MAX_LOOP_TIME = LOOP_HOLD - 1


@dataclass(frozen=True)
class LoopSetting:
    start: int  # first frame of the loop
    end: int  # last frame of the loop
    time: int  # ms; 0 means the loop is off, LOOP_HOLD means "until the key is released"
    fine: int = 0  # the fraction of a frame the loop's length has (kept as it was)
    raw: bytes = b""  # the 12 bytes it was read from: an entry that isn't changed is written back exactly as it was

    @property
    def enabled(self) -> bool:
        return self.time != 0

    @property
    def hold(self) -> bool:
        return self.time == LOOP_HOLD


@dataclass(frozen=True)
class SampleSettings:
    """The parts of an S1000/S3000 sample header a person edits (everything else in the header is left alone)."""

    root: int
    semitone: int  # tuning, -50..50
    cents: int  # fine tuning, -50..50
    play_mode: int  # a key of PLAY_MODES
    start: int  # the start marker (a frame)
    end: int  # the end marker (the last frame played)
    first_loop: int  # which loop is "first": 0-7
    loops: tuple[LoopSetting, ...]  # always MAX_LOOPS of them, off ones included
    rate: int = 0  # (read only, for the dialog)
    frames: int = 0  # (read only)


def _signed(b: int) -> int:
    return b - 256 if b > 127 else b


def read_settings(header: bytes) -> SampleSettings | None:
    """The editable settings of an S1000/S3000 sample header, or None if the bytes aren't one."""
    if len(header) < 150 or header[0] != 3:
        return None
    frames, start, end = struct.unpack_from("<III", header, 26)
    loops = []
    for i in range(MAX_LOOPS):
        raw = bytes(header[LOOP_AT + LOOP_ENTRY * i : LOOP_AT + LOOP_ENTRY * (i + 1)])
        at, fine, length, time = struct.unpack("<IHIH", raw)
        loops.append(LoopSetting(max(0, at - length) if length <= at else 0, at, time, fine, raw))
    return SampleSettings(
        root=header[2],
        semitone=_signed(header[21]),
        cents=_signed(header[20]),
        play_mode=header[19],
        start=start,
        end=end,
        first_loop=min(header[17], MAX_LOOPS - 1),
        loops=tuple(loops),
        rate=struct.unpack_from("<H", header, 138)[0],
        frames=frames,
    )


def problems(s: SampleSettings) -> list[str]:
    """What is wrong with these settings (empty if they can be written)."""
    found = []
    if not 0 <= s.root <= 127:
        found.append("The root key is 0 to 127.")
    if not -MAX_TUNE <= s.semitone <= MAX_TUNE or not -MAX_TUNE <= s.cents <= MAX_TUNE:
        found.append(f"Tuning is {-MAX_TUNE} to {MAX_TUNE}.")
    if s.play_mode not in PLAY_MODES:
        found.append("Unknown play mode.")
    last = max(s.frames - 1, 0)
    if not 0 <= s.start < s.end <= last:
        found.append(f"The start marker must come before the end marker, and both lie within the sample (0 to {last}).")
    for i, loop in enumerate(s.loops, 1):
        if not loop.enabled:
            continue
        if not 0 <= loop.start < loop.end <= last:
            found.append(f"Loop {i}: the start must come before the end, and both lie within the sample (0 to {last}).")
        if not 0 < loop.time <= LOOP_HOLD:
            found.append(f"Loop {i}: the time is 1 to {MAX_LOOP_TIME} ms, or hold.")
    return found


def write_settings(header: bytes, s: SampleSettings) -> bytes:
    """`header` with these settings written into it. Only the fields a person edits change; a loop that wasn't touched keeps its
    exact bytes (including whatever the sampler left in it)."""
    bad = problems(s)
    if bad:
        raise ValueError(" ".join(bad))
    out = bytearray(header)
    out[2] = s.root
    out[19] = s.play_mode
    out[20] = s.cents & 0xFF
    out[21] = s.semitone & 0xFF
    out[30:34] = struct.pack("<I", s.start)
    out[34:38] = struct.pack("<I", s.end)
    original = read_settings(header)
    enabled = []
    changed = False
    for i, loop in enumerate(s.loops[:MAX_LOOPS]):
        at = LOOP_AT + LOOP_ENTRY * i
        was = original.loops[i] if original else None
        if was is not None and (loop.start, loop.end, loop.time) == (was.start, was.end, was.time):
            out[at : at + LOOP_ENTRY] = was.raw  # (untouched: exactly as it was)
        elif loop.enabled:
            changed = True
            fine = loop.fine if was is not None and (loop.start, loop.end) == (was.start, was.end) else 0
            out[at : at + LOOP_ENTRY] = struct.pack("<IHIH", loop.end, fine, loop.end - loop.start, loop.time)
        else:
            changed = True
            out[at : at + LOOP_ENTRY] = struct.pack("<IHIH", 0, 0, 0, 0)
        if loop.enabled:
            enabled.append(i)
    if changed:  # (leave the counts as the sampler wrote them if no loop was touched)
        out[16] = (max(enabled) + 1) if enabled else 0  # lnum: how many loop entries are in use
        out[17] = s.first_loop if s.first_loop in enabled else (enabled[0] if enabled else 0)
    return bytes(out)
