"""What an Akai name may look like, and how to type one into akaiutil (Qt-free).

An Akai name byte is an index into 0-9, space, A-Z, # + - . (S900 volumes keep lower case too). akaiutil
silently turns any other character into a '.', so the GUI checks names itself and says what is wrong
instead. On akaiutil's command line a space has to be written as '_' (commands split on whitespace),
which also means a name can never contain a real underscore.
"""

import re

NAME_LENGTH = 12  # S1000/S3000/CD3000
NAME_LENGTH_S900 = 10

ALLOWED = "letters, digits, spaces and # + - ."
_VALID = re.compile(r"[0-9A-Za-z #+\-.]*")
_EXTENSION = re.compile(r"^(?P<base>.*)\.(?P<ext>[^.]{1,3})$")


def name_limit(volume_type: str) -> int:
    """How many characters a volume's own name and the names of the files in it may have."""
    return NAME_LENGTH_S900 if volume_type == "S900" else NAME_LENGTH


def check_name(text: str, volume_type: str = "S3000") -> tuple[str, str | None]:
    """Tidy a name the way akaiutil would store it, and say what is wrong with it (None if nothing).

    Returns (name, problem). Surrounding spaces go, an underscore counts as a space (as in akaiutil), and
    so does the case on everything but S900 (the samplers only have capitals); the name returned is what
    will end up on the disk.
    """
    name = text.replace("_", " ").strip()
    if volume_type != "S900":
        name = name.upper()
    if not name:
        return name, "Type a name."
    if not _VALID.fullmatch(name):
        bad = sorted({c for c in name if not _VALID.fullmatch(c)})
        return name, f"Not allowed: {' '.join(bad)}  (names can use {ALLOWED})"
    limit = name_limit(volume_type)
    if len(name) > limit:
        return name, f"At most {limit} characters ({len(name)} typed)."
    if set(name) <= {"."}:
        return name, "A name can't be only dots."
    return name, None


def sanitize(text: str, volume_type: str = "S3000") -> tuple[str, list[str], bool]:
    """What a name field should hold after typing or pasting `text`: capitals (not on S900), an underscore as a space,
    characters Akai can't store dropped, and no longer than the limit.

    Returns (clean text, the distinct characters that were dropped, whether it was cut short)."""
    text = text.replace("_", " ")
    kept, dropped = [], []
    for c in text:
        if _VALID.fullmatch(c):
            kept.append(c)
        elif c not in dropped:
            dropped.append(c)
    clean = "".join(kept)
    if volume_type != "S900":
        clean = clean.upper()
    limit = name_limit(volume_type)
    return clean[:limit], dropped, len(clean) > limit


def split_extension(file_name: str) -> tuple[str, str]:
    """"KICK.S3" -> ("KICK", ".S3"). The type of a file is its extension, so renaming never touches it."""
    m = _EXTENSION.match(file_name)
    if m is None:
        return file_name, ""
    return m.group("base"), "." + m.group("ext")


def command_token(name: str) -> str:
    """The name as akaiutil's command line wants it: spaces written as '_'."""
    return name.replace(" ", "_")


def same_name(a: str, b: str) -> bool:
    """akaiutil compares names without regard to case."""
    return a.strip().casefold() == b.strip().casefold()
