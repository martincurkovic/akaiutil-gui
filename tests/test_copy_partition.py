"""Copying a whole partition into another one, with the real akaiutil."""

import shutil

import pytest

import imagegen
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.session import EngineError


@pytest.fixture
def two_disks(binary, images, tmp_path):
    """The small hard disk (DRUMS, BASS, MY SYNTH in partition A) and a second, blank one, with editing on."""
    source, blank = tmp_path / "source.img", tmp_path / "blank.img"
    shutil.copy(images["hd"], source)
    blank.write_bytes(bytes(8192 * 20000))
    imagegen._script(binary, blank, ["formatharddisk3 5M"], images["hd"].parent / "wavs")
    eng = AkaiEngine(binary)
    eng.open([source, blank])
    eng.set_writable(True)
    yield eng
    eng.close()


def partition(eng, disk):
    return eng.list_partitions(eng.list_disks()[disk])[0]


def summary(eng, part):
    return {v.name: [(f.name, f.size) for f in eng.list_files(v)] for v in eng.list_volumes(part)}


def test_every_volume_goes_to_the_other_partition_with_its_files(two_disks):
    eng = two_disks
    before = summary(eng, partition(eng, 0))
    assert len(before) == 3
    result = eng.copy_partition(partition(eng, 0), partition(eng, 1))
    assert result.notes == []
    assert [v.name for v in result.volumes] == list(before) and {v.disk for v in result.volumes} == {1}
    assert summary(eng, partition(eng, 1)) == before
    assert summary(eng, partition(eng, 0)) == before  # (the source is as it was)


def test_it_can_be_copied_into_a_partition_that_has_other_volumes(two_disks):
    eng = two_disks
    eng.create_volume(partition(eng, 1), 1, "OTHER", "S3000")
    eng.copy_partition(partition(eng, 0), partition(eng, 1))
    assert list(summary(eng, partition(eng, 1))) == ["OTHER", "DRUMS", "BASS", "MY SYNTH"]


def test_volumes_that_share_a_name_are_refused_not_merged(two_disks):
    eng = two_disks
    eng.create_volume(partition(eng, 1), 1, "bass", "S3000")
    eng.create_volume(partition(eng, 1), 2, "DRUMS", "S3000")
    with pytest.raises(EngineError, match="already has volumes called “DRUMS”, “BASS”"):
        eng.copy_partition(partition(eng, 0), partition(eng, 1))
    assert list(summary(eng, partition(eng, 1))) == ["bass".upper(), "DRUMS"]


def test_a_partition_without_room_is_refused_before_anything_is_written(two_disks, monkeypatch):
    import akaiutil_gui.engine.api as api

    eng = two_disks
    monkeypatch.setattr(api, "COPY_SPARE_BLOCKS", 10**9)
    with pytest.raises(EngineError, match="hasn't got room"):
        eng.copy_partition(partition(eng, 0), partition(eng, 1))
    assert summary(eng, partition(eng, 1)) == {}


def test_too_many_volumes_for_the_slots_is_refused(two_disks, monkeypatch):
    eng = two_disks
    monkeypatch.setattr(eng, "volume_slots", lambda part: ([1, 2], 4))
    with pytest.raises(EngineError, match="would hold 5 volumes, and the most it can hold is 4"):
        eng.copy_partition(partition(eng, 0), partition(eng, 1))


def test_a_volume_that_fails_takes_the_ones_copied_before_it_away_again(two_disks, monkeypatch):
    eng = two_disks
    real_run, seen = eng._run, []

    def failing(command, timeout=120):
        if command.startswith("copyvoli"):
            seen.append(command)
            real_run(command, timeout)
            if len(seen) == 2:  # (the second volume really copies, but akaiutil says it gave up)
                class R:
                    text = "not enough space left\ncannot create file\ncopy error\n"
                    cwd = ""

                return R()
        return real_run(command, timeout)

    monkeypatch.setattr(eng, "_run", failing)
    with pytest.raises(EngineError, match="Nothing was left behind. The 1 volume copied before it was removed again"):
        eng.copy_partition(partition(eng, 0), partition(eng, 1))
    monkeypatch.undo()
    assert summary(eng, partition(eng, 1)) == {}


def test_the_wrong_partitions_are_refused(binary, images, tmp_path, two_disks):
    eng = two_disks
    with pytest.raises(EngineError, match="different partition"):
        eng.copy_partition(partition(eng, 0), partition(eng, 0))
    empty = partition(eng, 1)
    with pytest.raises(EngineError, match="no volumes to copy"):
        eng.copy_partition(empty, partition(eng, 0))
    eng.close()
    floppy = tmp_path / "fl.img"
    shutil.copy(images["fl1"], floppy)
    eng2 = AkaiEngine(binary)
    try:
        eng2.open([images["hd"], floppy])
        eng2.set_writable(True)
        with pytest.raises(EngineError, match="Only hard disk partitions"):
            eng2.copy_partition(partition(eng2, 0), partition(eng2, 1))
    finally:
        eng2.close()


def test_tag_names_come_along_when_the_destination_has_the_default_ones(two_disks):
    eng = two_disks
    src = partition(eng, 0)
    drums = next(v for v in eng.list_volumes(src) if v.name == "DRUMS")
    kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
    eng.copy_file(drums, kick, drums, "TAGGED.S3", tags=[2])
    eng._run("cd /disk0/A")
    eng._write("rentag 2 BASSES")
    eng._tag_names.clear()
    result = eng.copy_partition(src, partition(eng, 1))
    assert result.notes == []
    copy = next(v for v in result.volumes if v.name == "DRUMS")
    assert eng.tag_names(copy)[2] == "BASSES"
    tagged = next(f for f in eng.list_files(copy) if f.name == "TAGGED.S3")
    assert tagged.tag_numbers == [2] and tagged.tag_names == ("BASSES",)


def test_tags_that_would_change_name_are_reported_when_the_destination_has_its_own(two_disks):
    eng = two_disks
    src = partition(eng, 0)
    drums = next(v for v in eng.list_volumes(src) if v.name == "DRUMS")
    kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
    eng.copy_file(drums, kick, drums, "TAGGED.S3", tags=[2])
    eng._run("cd /disk0/A")
    eng._write("rentag 2 BASSES")
    eng._run("cd /disk1/A")
    eng._write("rentag 2 LEADS")
    eng._tag_names.clear()
    result = eng.copy_partition(src, partition(eng, 1))
    assert result.notes and "2: “BASSES” became “LEADS”" in result.notes[0]
