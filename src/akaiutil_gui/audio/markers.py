"""What an audio file says about how it should be played: its root note and loop points (Qt-free).

A WAV from a DAW or sample editor can carry a "smpl" chunk (root note, loops); an AIFF can carry an "INST" chunk (root note).
A WAV exported from this app carries akaiutil's own header chunk instead, which already holds everything. Anything else has
no such information, though a name ending in a note (Piano_C3) is a common way of saying it.
"""

import re
import struct
from dataclasses import dataclass
from pathlib import Path

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_AT_END = re.compile(r"(?:^|[ _\-.])([A-Ga-g])([#b]?)(-?\d)$")
AKAI_HEADER_IDS = (b"S1H ", b"S3H ", b"S9H ")  # akaiutil's chunks: the sample header of an S1000, S3000, S900 sample


@dataclass(frozen=True)
class Markers:
    root: int | None = None  # MIDI note the sample plays at its original pitch
    loops: tuple[tuple[int, int], ...] = ()  # (first frame, last frame) of each loop
    has_akai_header: bool = False  # the file already holds an Akai sample header


def note_name(midi: int) -> str:
    """The Akai way (middle C, MIDI 60, is C3)."""
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 2}"


def note_from_name(stem: str) -> int | None:
    """The MIDI note a file name ends in ("Piano_C3", "pad C#2", "bass-Bb1"), or None. Middle C is C3, as on the samplers."""
    m = _NOTE_AT_END.search(stem.strip())
    if m is None:
        return None
    letter, accidental, octave = m.group(1).upper(), m.group(2), int(m.group(3))
    semitone = _SEMITONE[letter] + (1 if accidental == "#" else -1 if accidental == "b" else 0)
    note = 12 * (octave + 2) + semitone
    return note if 0 <= note <= 127 else None


def read_markers(path: str | Path) -> Markers:
    """Whatever a WAV or AIFF file says about its root note and loops (nothing, for any other kind, or if it can't be read)."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                return _read_wav(f)
            if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
                return _read_aiff(f)
    except (OSError, struct.error):
        pass
    return Markers()


def _chunks(f, big_endian: bool):
    """(id, data) for each chunk after the 12-byte file header."""
    fmt = ">I" if big_endian else "<I"
    while True:
        head = f.read(8)
        if len(head) < 8:
            return
        cid, size = head[:4], struct.unpack(fmt, head[4:])[0]
        if cid in (b"smpl", b"INST", *AKAI_HEADER_IDS):
            yield cid, f.read(size)
            if size % 2:
                f.seek(1, 1)
        else:
            f.seek(size + size % 2, 1)  # (chunks are padded to an even size)


def _read_wav(f) -> Markers:
    root, loops, akai = None, [], False
    for cid, data in _chunks(f, big_endian=False):
        if cid in AKAI_HEADER_IDS:
            akai = True
        elif cid == b"smpl" and len(data) >= 36:
            unity, _fraction, _smpte, _offset, count = struct.unpack("<IIIII", data[12:32])
            root = unity if 0 < unity <= 127 else None
            for i in range(min(count, 8)):
                at = 36 + 24 * i
                if len(data) < at + 24:
                    break
                _id, _kind, start, end, _frac, _plays = struct.unpack("<IIIIII", data[at : at + 24])
                if end > start:
                    loops.append((start, end))  # (a WAV loop's end is its last frame, as an Akai loop's is)
    return Markers(root, tuple(loops), akai)


def _read_aiff(f) -> Markers:
    root = None
    for cid, data in _chunks(f, big_endian=True):
        if cid == b"INST" and len(data) >= 1:
            root = data[0] if 0 < data[0] <= 127 else None
    return Markers(root)
