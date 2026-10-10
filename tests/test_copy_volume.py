"""Copying a whole volume into another partition or disk, with the real akaiutil."""

import shutil

import pytest

from akaiutil_gui import akai_names
from akaiutil_gui.engine import parse
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.session import EngineError


@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def parts(eng, disk=0):
    return {p.name: p for p in eng.list_partitions(eng.list_disks()[disk])}


def vols(eng, letter="A", disk=0):
    return {v.name: v for v in eng.list_volumes(parts(eng, disk)[letter])}


def test_unique_names_get_copy_and_a_number_and_still_fit():
    assert akai_names.unique_name("DRUMS", ["BASS"]) == "DRUMS"
    assert akai_names.unique_name("DRUMS", ["drums"]) == "DRUMS COPY"
    assert akai_names.unique_name("DRUMS", ["DRUMS", "DRUMS COPY"]) == "DRUMS COPY 2"
    assert akai_names.unique_name("LONG VOLUME", ["LONG VOLUME"]) == "LONG VO COPY"  # (12 characters at most)
    assert len(akai_names.unique_name("LONG VOLUME", ["LONG VOLUME"], "S900")) <= 10


def test_a_volume_is_copied_into_another_partition_with_all_its_files(ed):
    v = vols(ed)
    other = parts(ed)["B"] if "B" in parts(ed) else None
    if other is None:
        pytest.skip("the test disk has one partition")
    result = ed.copy_volume(v["DRUMS"], other, "DRUMS")
    assert result.notes == []
    copy = result.volume
    assert (copy.partition, copy.name, copy.type) == ("B", "DRUMS", "S3000")
    assert [(f.name, f.size) for f in ed.list_files(copy)] == [(f.name, f.size) for f in ed.list_files(v["DRUMS"])]
    assert "DRUMS" in vols(ed, "A")  # the original is still there


def test_a_volume_can_be_copied_within_its_partition_under_a_new_name(ed):
    v = vols(ed)
    result = ed.copy_volume(v["DRUMS"], parts(ed)["A"], "DRUMS COPY")
    assert result.volume.name == "DRUMS COPY" and result.volume.index not in (v["DRUMS"].index,)
    assert [f.name for f in ed.list_files(result.volume)] == ["KICK.S3", "SNARE.S3"]


def test_a_name_that_is_taken_is_refused_rather_than_merged_into(ed):
    v = vols(ed)
    before = [f.name for f in ed.list_files(v["BASS"])]
    with pytest.raises(EngineError, match="already a volume called"):
        ed.copy_volume(v["DRUMS"], parts(ed)["A"], "bass")
    assert [f.name for f in ed.list_files(vols(ed)["BASS"])] == before


def test_the_load_number_is_cleared_when_the_partition_already_uses_it(ed):
    v = vols(ed)
    ed._run(f"cd /disk0/A")
    ed._write(f"setlnumi {v['DRUMS'].index} 7")
    drums = vols(ed)["DRUMS"]
    assert drums.load_number == "007"
    result = ed.copy_volume(drums, parts(ed)["A"], "DRUMS COPY")
    assert result.volume.load_number == "-"
    assert result.notes == ["Load number 007 is already used in partition A, so the copy has none."]
    assert vols(ed)["DRUMS"].load_number == "007"  # the original keeps it


def test_a_load_number_nobody_uses_comes_along(ed):
    v = vols(ed)
    other = next((p for n, p in parts(ed).items() if n != "A"), None)
    if other is None:
        pytest.skip("the test disk has one partition")
    ed._run("cd /disk0/A")
    ed._write(f"setlnumi {v['DRUMS'].index} 7")
    result = ed.copy_volume(vols(ed)["DRUMS"], other, "DRUMS")
    assert result.volume.load_number == "007" and result.notes == []


def test_a_copy_that_would_not_fit_is_refused_before_anything_is_written(ed, monkeypatch):
    import akaiutil_gui.engine.api as api

    v = vols(ed)
    other = next((p for n, p in parts(ed).items() if n != "A"), None)
    if other is None:
        pytest.skip("the test disk has one partition")
    before = [x.name for x in ed.list_volumes(other)]
    monkeypatch.setattr(api, "COPY_SPARE_BLOCKS", 10**9)  # (a volume that needs more than any partition has)
    with pytest.raises(EngineError, match="hasn't got room"):
        ed.copy_volume(v["DRUMS"], other, "BIG")
    assert [x.name for x in ed.list_volumes(other)] == before


def test_a_copy_that_fails_halfway_leaves_no_volume_behind(ed, monkeypatch):
    v = vols(ed)
    other = next((p for n, p in parts(ed).items() if n != "A"), None)
    if other is None:
        pytest.skip("the test disk has one partition")
    real_run = ed._run

    def failing(command, timeout=120):
        if command.startswith("copyvoli"):
            real_run(command, timeout)  # (it really copies...)
            class R:  # ...but tell the engine akaiutil gave up
                text = "not enough space left\ncannot create file\ncopy error\n"
                cwd = ""
            return R()
        return real_run(command, timeout)

    monkeypatch.setattr(ed, "_run", failing)
    with pytest.raises(EngineError, match="Nothing was left behind"):
        ed.copy_volume(v["DRUMS"], other, "HALF")
    monkeypatch.undo()
    assert "HALF" not in [x.name for x in ed.list_volumes(other)]


def test_only_a_hard_disk_partition_of_the_right_kind_will_do(binary, images, tmp_path):
    hd = tmp_path / "hd.img"
    shutil.copy(images["hd"], hd)
    eng = AkaiEngine(binary)
    eng.open([hd, images["fl1"], images["fl9"]])
    eng.set_writable(True)
    try:
        disks = eng.list_disks()
        drums = next(v for v in eng.list_volumes(eng.list_partitions(disks[0])[0]) if v.name == "DRUMS")
        floppy = eng.list_partitions(disks[1])[0]
        with pytest.raises(EngineError, match="hard disk partition"):
            eng.copy_volume(drums, floppy, "X")
        s900_floppy_volume = eng.list_volumes(eng.list_partitions(disks[2])[0])[0]
        with pytest.raises(EngineError, match="S900 volume only fits"):
            eng.copy_volume(s900_floppy_volume, eng.list_partitions(disks[0])[0], "X")
    finally:
        eng.close()


def test_a_volume_goes_to_a_partition_on_another_disk(binary, images, tmp_path):
    a, b = tmp_path / "a.img", tmp_path / "b.img"
    shutil.copy(images["hd"], a)
    shutil.copy(images["hd"], b)
    eng = AkaiEngine(binary)
    eng.open([a, b])
    eng.set_writable(True)
    try:
        src = next(v for v in eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0]) if v.name == "DRUMS")
        dst = eng.list_partitions(eng.list_disks()[1])[0]
        result = eng.copy_volume(src, dst, "FROM DISK 0")
        assert (result.volume.disk, result.volume.name) == (1, "FROM DISK 0")
        assert [f.name for f in eng.list_files(result.volume)] == ["KICK.S3", "SNARE.S3"]
    finally:
        eng.close()
