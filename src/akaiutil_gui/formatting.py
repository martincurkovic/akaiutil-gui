"""Small display helpers shared by the engine-facing and UI code (no Qt here)."""

import re

# An S3000-series OS version is stored 15 above the number the sampler's panel shows: akaiutil prints "17.00" for what the panel calls 2.00
# (akaiutil.h calls "17.00" the maximum S3000 OS version; AKAISDS measured OS 1.30 and 2.00 on a real S2000 as 16.30 and 17.00). S1000 and S1100 values
# (at most "4.40" and "9.30" in akaiutil.h) already read as the panel does, so only majors from 16 up - the S3000 series - are shifted.
_S3000_MAJOR_OFFSET = 15
_S3000_FIRST_STORED_MAJOR = 16
_OS_VERSION = re.compile(r"(\d+)\.(\d\d)")


def panel_os_version(text: str) -> str:
    """An OS version as the sampler's panel shows it, from akaiutil's text for it: "17.00" -> "2.00", "16.30" -> "1.30". Anything else - an S1000 version,
    S900's "uncompr", a bare number, "-" - comes back as it is. Display only: the engine keeps akaiutil's own text, which is what it compares and re-imports."""
    match = _OS_VERSION.fullmatch(text.strip())
    if match and int(match[1]) >= _S3000_FIRST_STORED_MAJOR:
        return f"{int(match[1]) - _S3000_MAJOR_OFFSET}.{match[2]}"
    return text


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def human_size(n: float) -> str:
    n = int(n) if float(n).is_integer() else n
    if n < 1024:
        return f"{n:g} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def note_name(note: int) -> str:
    """MIDI note number -> name as the Akai front panels show it: middle C (60) is "C3"
    (one octave below the General-MIDI convention). Same as AKAISDS."""
    return f"{_NOTE_NAMES[note % 12]}{note // 12 - 2}"


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return f"{n:,} {singular if n == 1 else plural_form or singular + 's'}"


def percent(fraction: float) -> str:
    """'73%', but '99.8%' / '0.4%' near the ends so "almost full" never reads as "full"."""
    pct = fraction * 100
    return f"{pct:.1f}%" if 0 < pct < 1 or 99 < pct < 100 else f"{pct:.0f}%"


def human_duration(seconds: float) -> str:
    """'5.8 ms', '500 ms', '2.50 s', '1 min 12.3 s'."""
    ms = seconds * 1000
    if ms < 1000:
        return f"{ms:.1f} ms" if ms < 100 else f"{ms:.0f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    return f"{int(seconds // 60)} min {seconds % 60:.1f} s"
