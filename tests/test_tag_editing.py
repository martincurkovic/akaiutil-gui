"""Giving files tags and taking them away (akaiutil 4.7's settagi / clrtagi) with the real akaiutil."""

import shutil

import pytest

from akaiutil_gui import tags
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.session import EngineError


# ------------------------------------------------------------------------------ the plan on its own
def test_a_plan_clears_first_and_sets_what_is_missing():
    assert tags.plan_change([3, 5], [5, 7], [3]) == ([3], [7], [5, 7], None)
    assert tags.plan_change([], [2, 2, 9], []) == ([], [2, 9], [2, 9], None)  # no repeats
    assert tags.plan_change([4], [], [4, 8]) == ([4], [], [], None)  # taking away a tag it doesn't have is nothing


def test_a_file_holds_four_tags():
    clears, sets, result, problem = tags.plan_change([1, 2, 3], [4, 5], [])
    assert result == [1, 2, 3, 4, 5] and "5 tags" in problem
    assert tags.plan_change([1, 2, 3, 4], [5], [1])[3] is None  # a place frees up in the same change


@pytest.mark.parametrize(
    "vol_type, kind, refused",
    [
        ("S900", "Sample", True),
        ("S3000", "Sample", False),
        ("S1000", "Sample", False),  # (a floppy's files take tags too, though there are no names for them)
        ("S1000", "Operating system", True),  # its four bytes are the sampler model
        ("S3000", "Operating system", False),
    ],
)
def test_which_files_cant_be_tagged(vol_type, kind, refused):
    assert (tags.untaggable_reason(vol_type, kind) is not None) is refused


# ------------------------------------------------------------------- with the real akaiutil
@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def drums(eng):
    part = next(p for p in eng.list_partitions(eng.list_disks()[0]) if p.name == "A")
    return next(v for v in eng.list_volumes(part) if v.name == "DRUMS")


def files_of(eng, vol):
    return {f.name: f for f in eng.list_files(vol)}


def test_tags_are_added_and_taken_away(ed):
    vol = drums(ed)
    kick, snare = (files_of(ed, vol)[n] for n in ("KICK.S3", "SNARE.S3"))
    assert ed.change_file_tags(vol, [kick, snare], add=[3, 7], remove=[]) == []
    now = files_of(ed, vol)
    assert now["KICK.S3"].tags == "03 07" and now["SNARE.S3"].tags == "03 07"
    assert ed.change_file_tags(vol, [now["KICK.S3"]], add=[9], remove=[3]) == []
    now = files_of(ed, vol)
    assert now["KICK.S3"].tags == "07 09" and now["SNARE.S3"].tags == "03 07"  # the other file is as it was
    assert ed.change_file_tags(vol, [now["KICK.S3"], now["SNARE.S3"]], add=[], remove=[7, 9, 3]) == []
    assert all(f.tags == "" for f in files_of(ed, vol).values())


def test_nothing_else_about_a_file_changes(ed):
    vol = drums(ed)
    before = {n: (f.index, f.size, f.start_block, f.os_version) for n, f in files_of(ed, vol).items()}
    ed.change_file_tags(vol, list(files_of(ed, vol).values()), add=[1, 26], remove=[])
    assert {n: (f.index, f.size, f.start_block, f.os_version) for n, f in files_of(ed, vol).items()} == before
    assert [f.tag_names[:1] for f in files_of(ed, vol).values()] == [("TAG A",), ("TAG A",)]  # the names come from the partition


def test_a_fifth_tag_is_refused_for_that_file_only(ed):
    vol = drums(ed)
    kick, snare = (files_of(ed, vol)[n] for n in ("KICK.S3", "SNARE.S3"))
    ed.change_file_tags(vol, [kick], add=[1, 2, 3, 4], remove=[])
    failed = ed.change_file_tags(vol, [files_of(ed, vol)["KICK.S3"], snare], add=[5], remove=[])
    assert len(failed) == 1 and failed[0].startswith("KICK.S3:") and "4" in failed[0]
    now = files_of(ed, vol)
    assert now["KICK.S3"].tags == "01 02 03 04" and now["SNARE.S3"].tags == "05"  # SNARE still got its tag


def test_a_file_that_moved_is_noticed(ed):
    vol = drums(ed)
    kick = files_of(ed, vol)["KICK.S3"]
    stale = type(kick)(kick.index, "SNARE.S3", kick.size, kick.start_block, kick.os_version)
    failed = ed.change_file_tags(vol, [stale], add=[1], remove=[])
    assert failed and "isn't file" in failed[0]
    assert files_of(ed, vol)["KICK.S3"].tags == ""


def test_tag_numbers_are_checked(ed):
    vol = drums(ed)
    kick = files_of(ed, vol)["KICK.S3"]
    for bad in (0, 27):
        with pytest.raises(EngineError, match="1 to 26"):
            ed.change_file_tags(vol, [kick], add=[bad], remove=[])


def test_it_wont_run_on_an_akaiutil_that_crashes(ed, monkeypatch):
    vol = drums(ed)
    monkeypatch.setattr(AkaiEngine, "can_edit_tags", property(lambda self: False))
    with pytest.raises(EngineError, match="4.7"):
        ed.change_file_tags(vol, [files_of(ed, vol)["KICK.S3"]], add=[1], remove=[])
    assert files_of(ed, vol)["KICK.S3"].tags == ""


def test_a_floppy_with_s1000_files_takes_tags_and_an_s900_one_doesnt(binary, images, tmp_path):
    f1, f9 = tmp_path / "f1.img", tmp_path / "f9.img"
    shutil.copy(images["fl1"], f1)
    shutil.copy(images["fl9"], f9)
    eng = AkaiEngine(binary)
    eng.open([f1, f9])
    eng.set_writable(True)
    try:
        d1, d9 = eng.list_disks()
        v1 = eng.list_volumes(eng.list_partitions(d1)[0])[0]
        v9 = eng.list_volumes(eng.list_partitions(d9)[0])[0]
        sample = eng.list_files(v1)[0]
        assert eng.change_file_tags(v1, [sample], add=[3], remove=[]) == []
        after = eng.list_files(v1)[0]
        assert after.tags == "03" and after.tag_labels == ["Tag 3"]  # a floppy has no names for them
        assert eng.change_file_tags(v1, [after], add=[], remove=[3]) == []
        assert eng.list_files(v1)[0].tags == ""
        s900 = eng.list_files(v9)[0]
        failed = eng.change_file_tags(v9, [s900], add=[3], remove=[])
        assert failed and "no tags" in failed[0].lower()
    finally:
        eng.close()
