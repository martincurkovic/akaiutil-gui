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


def filter_catalog(catalog: list[CatalogEntry], query: str, kind: str = ALL, limit: int = DEFAULT_LIMIT) -> tuple[list[CatalogEntry], bool]:
    """The entries matching `query` and `kind`, and whether more matched than `limit` allowed.

    The query is split into words that must ALL appear, in any order, in the file's name or its volume's name,
    ignoring case: "kick drums" finds KICK.S3 in volume DRUMS. A blank query matches everything of that kind.
    """
    words = query.lower().split()
    results: list[CatalogEntry] = []
    for entry in catalog:
        if not matches_kind(entry, kind):
            continue
        if words:
            haystack = f"{entry.file.name} {entry.volume.name}".lower()
            if not all(word in haystack for word in words):
                continue
        if len(results) >= limit:
            return results, True
        results.append(entry)
    return results, False
