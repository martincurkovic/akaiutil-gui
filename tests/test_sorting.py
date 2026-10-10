"""Sorting a volume's files alphabetically: the planner, and the real akaiutil."""

import random

import pytest

from akaiutil_gui import sorting
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import FileEntry
from akaiutil_gui.engine.session import EngineError


def entry(index, name):
    return FileEntry(index, name, 100, f"0x{index:04x}", "17.00")


def apply(files, moves):
    """Carry out a plan on a dict of slot -> name, checking that every move goes to a free slot."""
    slots = {f.index: f.name for f in files}
    for src, dst in moves:
        assert src in slots and dst not in slots, (src, dst, slots)
        slots[dst] = slots.pop(src)
    return slots


# ---------------------------------------------------------------------------- the plan
def test_files_already_in_order_need_no_moves():
    files = [entry(1, "A.S3"), entry(2, "B.S3"), entry(3, "C.S3")]
    assert sorting.plan_moves(files, sorting.ALL, free_slot=9) == []


def test_two_files_trade_places_through_the_spare_slot():
    files = [entry(1, "B.S3"), entry(2, "A.S3")]
    moves = sorting.plan_moves(files, sorting.ALL, free_slot=3)
    assert apply(files, moves) == {1: "A.S3", 2: "B.S3"} and len(moves) == 3


def test_a_longer_cycle_and_a_separate_cycle():
    files = [entry(1, "C.S3"), entry(2, "A.S3"), entry(3, "B.S3"), entry(4, "E.S3"), entry(5, "D.S3")]
    result = apply(files, sorting.plan_moves(files, sorting.ALL, free_slot=6))
    assert [result[i] for i in sorted(result)] == ["A.S3", "B.S3", "C.S3", "D.S3", "E.S3"]


def test_the_order_ignores_case_and_puts_numbers_in_order():
    files = [entry(1, "snare 10.S3"), entry(2, "Kick.S3"), entry(3, "snare 2.S3"), entry(4, "bass.S3")]
    result = apply(files, sorting.plan_moves(files, sorting.ALL, free_slot=5))
    assert [result[i] for i in sorted(result)] == ["bass.S3", "Kick.S3", "snare 2.S3", "snare 10.S3"]


def test_files_keep_to_the_slots_they_already_use():
    files = [entry(2, "B.S3"), entry(5, "A.S3"), entry(9, "C.S3")]  # gaps stay where they are
    assert apply(files, sorting.plan_moves(files, sorting.ALL, free_slot=1)) == {2: "A.S3", 5: "B.S3", 9: "C.S3"}


def test_a_stereo_pair_stays_together_left_first():
    files = [entry(1, "PAD       -R.S3"), entry(2, "KICK.S3"), entry(3, "PAD       -L.S3")]
    result = apply(files, sorting.plan_moves(files, sorting.ALL, free_slot=4))
    assert [result[i] for i in sorted(result)] == ["KICK.S3", "PAD       -L.S3", "PAD       -R.S3"]


def test_sorting_only_the_samples_leaves_programs_in_their_slots():
    files = [entry(1, "Z PROG.P3"), entry(2, "B.S3"), entry(3, "A PROG.P3"), entry(4, "A.S3"), entry(5, "MULTI.M3")]
    result = apply(files, sorting.plan_moves(files, sorting.SAMPLES, free_slot=6))
    assert result == {1: "Z PROG.P3", 2: "A.S3", 3: "A PROG.P3", 4: "B.S3", 5: "MULTI.M3"}


def test_sorting_everything_moves_programs_too():
    files = [entry(1, "Z PROG.P3"), entry(2, "B.S3"), entry(3, "A PROG.P3")]
    result = apply(files, sorting.plan_moves(files, sorting.ALL, free_slot=4))
    assert [result[i] for i in sorted(result)] == ["A PROG.P3", "B.S3", "Z PROG.P3"]


def test_a_cycle_with_no_spare_slot_cant_be_planned():
    with pytest.raises(ValueError):
        sorting.plan_moves([entry(1, "B.S3"), entry(2, "A.S3")], sorting.ALL, free_slot=None)
    assert sorting.plan_moves([entry(1, "A.S3"), entry(2, "B.S3")], sorting.ALL, free_slot=None) == []  # (not needed when sorted)


@pytest.mark.parametrize("seed", range(20))
def test_any_shuffle_ends_up_sorted_with_every_move_to_a_free_slot(seed):
    rnd = random.Random(seed)
    names = [f"SOUND {n:03d}.S3" for n in range(rnd.randint(2, 60))]
    slots = rnd.sample(range(1, 200), len(names))
    files = [entry(slot, name) for slot, name in zip(slots, rnd.sample(names, len(names)))]
    result = apply(files, sorting.plan_moves(files, sorting.ALL, free_slot=max(slots) + 1))
    assert [result[i] for i in sorted(result)] == sorted(names)


# -------------------------------------------------------------------------- with akaiutil
@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def volume(eng, name="DRUMS"):
    return next(v for v in eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0]) if v.name == name)


def scramble(eng, vol, names):
    """Make `vol` hold copies of the DRUMS sample under these names, added in the order given."""
    from akaiutil_gui import transfer

    drums = volume(eng, "DRUMS")
    kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
    for name in names:
        base = name[:-3]
        eng.copy_file(drums, kick, vol, name)
    return [f.name for f in eng.list_files(vol)]


def test_a_volume_ends_up_in_alphabetical_order_with_nothing_else_changed(ed):
    vol = volume(ed, "MY SYNTH")
    scramble(ed, vol, ["ZED.S3", "ALPHA.S3", "MID 10.S3", "MID 2.S3"])
    before = {f.name: (f.size, f.start_block, f.os_version) for f in ed.list_files(vol)}
    assert [f.name for f in ed.list_files(vol)] == ["LEAD.S3", "ZED.S3", "ALPHA.S3", "MID 10.S3", "MID 2.S3"]
    moved = ed.sort_files(vol)
    assert moved == 4
    after = ed.list_files(vol)
    assert [f.name for f in after] == ["ALPHA.S3", "LEAD.S3", "MID 2.S3", "MID 10.S3", "ZED.S3"]
    assert [f.index for f in after] == [1, 2, 3, 4, 5]
    assert {f.name: (f.size, f.start_block, f.os_version) for f in after} == before  # the files themselves weren't touched


def test_a_sorted_volume_is_left_alone(ed):
    vol = volume(ed)
    assert ed.sort_files(vol) == 0
    assert [f.name for f in ed.list_files(vol)] == ["KICK.S3", "SNARE.S3"]


def test_the_sort_survives_closing_and_reopening(ed, binary, editable_hd):
    vol = volume(ed, "MY SYNTH")
    scramble(ed, vol, ["ZED.S3", "ALPHA.S3"])
    ed.sort_files(vol)
    ed.close()
    fresh = AkaiEngine(binary)
    fresh.open([editable_hd])
    try:
        assert [f.name for f in fresh.list_files(volume(fresh, "MY SYNTH"))] == ["ALPHA.S3", "LEAD.S3", "ZED.S3"]
    finally:
        fresh.close()


def test_sorting_works_with_gaps_in_the_slots(ed):
    vol = volume(ed, "MY SYNTH")
    scramble(ed, vol, ["ZED.S3", "ALPHA.S3", "MID.S3"])
    ed.delete_files(vol, [ed.list_files(vol)[1]])  # ZED is gone: slot 2 is empty
    assert [f.index for f in ed.list_files(vol)] == [1, 3, 4]
    ed.sort_files(vol)
    after = ed.list_files(vol)
    assert [(f.index, f.name) for f in after] == [(1, "ALPHA.S3"), (3, "LEAD.S3"), (4, "MID.S3")]


def test_a_name_that_cant_be_moved_safely_stops_the_sort_before_anything_moves(ed, monkeypatch):
    from akaiutil_gui import akai_names

    vol = volume(ed, "MY SYNTH")
    scramble(ed, vol, ["ZED.S3", "ALPHA.S3"])
    before = [f.name for f in ed.list_files(vol)]
    real = akai_names.check_name
    # as if ZED's name were one akaiutil can't re-encode exactly (the check mirrors what `reni` does to a name)
    monkeypatch.setattr(akai_names, "check_name", lambda text, vtype="S3000": (text, "odd") if text == "ZED" else real(text, vtype))
    with pytest.raises(EngineError, match="“ZED.S3” has a name akaiutil can't move safely"):
        ed.sort_files(vol)
    assert [f.name for f in ed.list_files(vol)] == before


def test_sorting_needs_editing_to_be_on(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        with pytest.raises(EngineError, match="Editing is switched off"):
            eng.sort_files(volume(eng))
    finally:
        eng.close()


def test_a_full_volume_that_needs_shuffling_is_refused_with_a_reason(ed, monkeypatch):
    vol = volume(ed, "MY SYNTH")
    scramble(ed, vol, ["ZED.S3", "ALPHA.S3"])
    monkeypatch.setattr("akaiutil_gui.engine.parse.parse_volume_limit", lambda text: 3)  # pretend the volume only has 3 slots
    with pytest.raises(EngineError, match="no room to shuffle"):
        ed.sort_files(vol)
    assert [f.name for f in ed.list_files(vol)] == ["LEAD.S3", "ZED.S3", "ALPHA.S3"]


def test_a_real_volumes_programs_stay_put_when_only_samples_are_sorted(binary, tmp_path):
    """On the HD5 image (if it's there): a volume with a program and a multi among samples."""
    import shutil
    from pathlib import Path

    image = Path(__file__).resolve().parent.parent / "testing_images" / "HD5 copy.img"
    if not image.exists():
        pytest.skip("needs the HD5 copy")
    work = tmp_path / "copy.img"
    shutil.copy(image, work)
    eng = AkaiEngine(binary)
    eng.open([work])
    eng.set_writable(True)
    try:
        vol = eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])[0]
        before = eng.list_files(vol)
        assert any(f.name.endswith(".P3") for f in before)  # TEST PROGRAM.P3, SINE.S3, EFFECTS FILE.X, MULTI FILE.M3 ...
        eng.sort_files(vol, sorting.SAMPLES)
        after = eng.list_files(vol)
        non_samples = lambda fs: [(f.index, f.name, f.tags) for f in fs if not f.is_sample]  # noqa: E731
        assert non_samples(after) == non_samples(before)  # programs, multis, effects: same slots, same tags
        eng.sort_files(vol, sorting.ALL)
        names = [f.name for f in eng.list_files(vol)]
        assert names == sorted(names, key=sorting.file_key)
        assert {f.name: f.tags for f in eng.list_files(vol)} == {f.name: f.tags for f in before}  # tags travelled with the files
    finally:
        eng.close()
