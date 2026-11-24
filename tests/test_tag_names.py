"""Renaming a partition's tags (akaiutil's rentag) with the real akaiutil."""

import shutil

import pytest

from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.session import EngineError


@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def partition(eng, name="A"):
    return next(p for p in eng.list_partitions(eng.list_disks()[0]) if p.name == name)


def test_tags_are_renamed_and_the_rest_keep_their_names(ed):
    part = partition(ed)
    assert ed.partition_tag_names(part)[1] == "TAG A"
    after = ed.rename_tags(part, {1: "kicks", 3: "My_Bass"})
    assert after[1] == "KICKS" and after[3] == "MY BASS" and after[2] == "TAG B" and len(after) == 26  # capitals; an underscore is a space
    assert ed.partition_tag_names(part) == after  # and what is read back is the same


def test_other_partitions_keep_their_own_names(ed):
    ed.rename_tags(partition(ed, "A"), {1: "ONLY A"})
    assert ed.partition_tag_names(partition(ed, "B"))[1] == "TAG A"


def test_files_show_the_new_name_of_their_tag(ed):
    part = partition(ed)
    vol = next(v for v in ed.list_volumes(part) if v.name == "DRUMS")
    kick = next(f for f in ed.list_files(vol) if f.name == "KICK.S3")
    assert ed.change_file_tags(vol, [kick], add=[2], remove=[]) == []
    assert next(f for f in ed.list_files(vol) if f.name == "KICK.S3").tag_labels == ["TAG B"]
    ed.rename_tags(part, {2: "SNARES"})
    again = next(f for f in ed.list_files(vol) if f.name == "KICK.S3")
    assert again.tags == "02" and again.tag_labels == ["SNARES"]  # the file still has tag 2; only its name changed


def test_a_name_that_is_the_same_is_not_written_again(ed, editable_hd):
    part = partition(ed)
    before = editable_hd.read_bytes()
    ed.rename_tags(part, {1: "tag a", 2: "TAG B"})  # (they are those already)
    assert editable_hd.read_bytes() == before


def test_a_bad_name_or_number_changes_nothing(ed, editable_hd):
    part = partition(ed)
    before = editable_hd.read_bytes()
    with pytest.raises(EngineError, match="Tag 2: Not allowed"):
        ed.rename_tags(part, {1: "FINE", 2: "no/slash"})
    with pytest.raises(EngineError, match="Tag 3: At most 12"):
        ed.rename_tags(part, {3: "THIRTEEN CHARS"})
    with pytest.raises(EngineError, match="Tag 4: Type a name"):
        ed.rename_tags(part, {4: "  "})
    with pytest.raises(EngineError, match="1 to 26"):
        ed.rename_tags(part, {27: "X"})
    assert editable_hd.read_bytes() == before  # (all of it checked before anything is written)


def test_only_a_hard_disk_partition_has_tags(binary, images, tmp_path):
    floppy = tmp_path / "f.img"
    shutil.copy(images["fl1"], floppy)
    eng = AkaiEngine(binary)
    eng.open([floppy])
    eng.set_writable(True)
    try:
        part = eng.list_partitions(eng.list_disks()[0])[0]
        with pytest.raises(EngineError, match="hard disk partition"):
            eng.rename_tags(part, {1: "X"})
    finally:
        eng.close()


def test_a_read_only_session_refuses(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        with pytest.raises(EngineError):
            eng.rename_tags(partition(eng), {1: "X"})
    finally:
        eng.close()
