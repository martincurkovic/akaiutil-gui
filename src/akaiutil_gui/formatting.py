"""Small display helpers shared by the engine-facing and UI code (no Qt here)."""

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
