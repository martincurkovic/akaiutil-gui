"""Searching the files of every open disk. No Qt here.

The catalog (every file with the volume it lives in) comes from the same single `dirrec` listing that gives volume
sizes, so there is nothing extra to load: searching 3,000 files, or 100,000, is a list filter.
"""

from .engine.models import CatalogEntry

ALL, SAMPLES, PROGRAMS, OTHER = "all", "samples", "programs", "other"
KINDS = [(ALL, "All types"), (SAMPLES, "Samples"), (PROGRAMS, "Programs"), (OTHER, "Other")]

DEFAULT_LIMIT = 3000  # the table is for looking at: beyond this, narrow the search


def matches_kind(entry: CatalogEntry, kind: str) -> bool:
    if kind == ALL:
        return True
    is_sample, is_program = entry.file.is_sample, entry.file.kind == "Program"
    if kind == SAMPLES:
        return is_sample
    if kind == PROGRAMS:
        return is_program
    return not is_sample and not is_program


def matches_tag(entry: CatalogEntry, tag: str | None) -> bool:
    """Whether the file carries the tag called `tag` (None: any file). Tags are matched by name, ignoring case, as when files are
    copied between partitions: each partition has its own names for its numbers, and a tag with no name is called "Tag 7"."""
    if not tag:
        return True
    wanted = tag.casefold()
    return any(label.casefold() == wanted for label in entry.file.tag_labels)


def tags_in_use(catalog: list[CatalogEntry]) -> list[str]:
    """The names of the tags some file carries (spelled as first met), in alphabetical order."""
    seen: dict[str, str] = {}
    for entry in catalog:
        for label in entry.file.tag_labels:
            seen.setdefault(label.casefold(), label)
    return [seen[key] for key in sorted(seen)]


def filter_catalog(
    catalog: list[CatalogEntry], query: str, kind: str = ALL, limit: int = DEFAULT_LIMIT, tag: str | None = None
) -> tuple[list[CatalogEntry], bool]:
    """The entries matching `query`, `kind` and `tag` (a tag's name, or None), and whether more matched than `limit` allowed.

    The query is split into words that must ALL appear, in any order, in the file's name or its volume's name,
    ignoring case: "kick drums" finds KICK.S3 in volume DRUMS. A blank query matches everything of that kind.
    """
    words = query.lower().split()
    results: list[CatalogEntry] = []
    for entry in catalog:
        if not matches_kind(entry, kind) or not matches_tag(entry, tag):
            continue
        if words:
            haystack = f"{entry.file.name} {entry.volume.name}".lower()
            if not all(word in haystack for word in words):
                continue
        if len(results) >= limit:
            return results, True
        results.append(entry)
    return results, False
