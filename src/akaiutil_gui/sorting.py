"""Putting the files of a volume in alphabetical order (Qt-free).

The samplers list a volume's files in the order of their slots, and nothing on the sampler can change that. akaiutil can move a
file to a free slot (`reni`), so sorting is a permutation carried out with one spare slot. Which files take part is a choice:
sorting only the samples leaves programs and the rest exactly where they are, which matters because a program's number (what a
MIDI program change selects) is its place among the volume's programs.
"""

from . import importing
from .engine.models import FileEntry

ALL, SAMPLES = "all", "samples"


def file_key(name: str):
    """Alphabetical ignoring case, numbers in order (kick 2 before kick 10), ties by type."""
    return importing.natural_key(name)


def takes_part(f: FileEntry, scope: str) -> bool:
    return scope == ALL or f.is_sample


def target_slots(files: list[FileEntry], scope: str) -> dict[int, int]:
    """For each file that has to move: {its slot: the slot it should be in}. The files that take part keep using the same set of
    slots, handed out in alphabetical order."""
    subset = [f for f in files if takes_part(f, scope)]
    slots = sorted(f.index for f in subset)
    ordered = sorted(subset, key=lambda f: (file_key(f.name), f.index))
    return {f.index: slots[k] for k, f in enumerate(ordered) if f.index != slots[k]}


def plan_moves(files: list[FileEntry], scope: str, free_slot: int | None) -> list[tuple[int, int]]:
    """The (from slot, to slot) moves that put the files in order, each to a slot that is free at that moment. Every cycle of
    files that have to trade places goes through `free_slot`, which must be a slot nothing is in."""
    target = target_slots(files, scope)
    moves: list[tuple[int, int]] = []
    seen: set[int] = set()
    for start in sorted(target):
        if start in seen:
            continue
        cycle = [start]
        while target[cycle[-1]] != start:
            cycle.append(target[cycle[-1]])
        seen.update(cycle)
        if free_slot is None:
            raise ValueError("no free slot")
        moves.append((cycle[0], free_slot))  # lift the first file out of the way...
        for slot in reversed(cycle[1:]):  # ...so the file that belongs there can go in, and so on round the cycle
            moves.append((slot, target[slot]))
        moves.append((free_slot, target[cycle[0]]))
    return moves
