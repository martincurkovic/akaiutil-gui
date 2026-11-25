"""Saving a partition to a file and putting it back (akaiutil's getpart and putpart), with the real akaiutil."""

import shutil

import pytest

from akaiutil_gui import partition_files
from akaiutil_gui.engine import formats
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.session import EngineError


@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def partition(eng, name="A", disk=0):
    return next(p for p in eng.list_partitions(eng.list_disks()[disk]) if p.name == name)


def volume_names(eng, part):
    return [v.name for v in eng.list_volumes(part)]


def staging_is_empty(eng):
    return not any(eng._need_session().staging_dir.iterdir())


# ---------------------------------------------------------------- the pure checks
def test_a_partition_file_is_checked_against_the_partition(ed, tmp_path):
    part = partition(ed)
    good = tmp_path / "A.part"
    ed.export_partition(part, good)
    assert partition_files.problem_with_file(good, part) == ""
    assert partition_files.problem_with_file(good, partition(ed, "B")) == ""  # same size, same kind: fits any sampler partition like it
    big = tmp_path / "BIG.part"
    big.write_bytes(good.read_bytes() * 2)
    assert "exactly as big" in partition_files.problem_with_file(big, part)
    small = tmp_path / "SMALL.part"
    small.write_bytes(good.read_bytes()[:-8192])
    assert "exactly as big" in partition_files.problem_with_file(small, part)
    blank = tmp_path / "BLANK.part"
    blank.write_bytes(bytes(part.size_bytes))
    assert "doesn't look like a sampler partition" in partition_files.problem_with_file(blank, part)
    assert "Couldn't read" in partition_files.problem_with_file(tmp_path / "missing.part", part)


def test_only_the_first_partition_has_a_table_that_may_differ(ed):
    assert partition_files.table_range(partition(ed, "A")) == (partition_files.TABLE_START, partition_files.TABLE_END)
    assert partition_files.table_range(partition(ed, "B")) is None


def test_the_table_offset_matches_what_akaiutil_leaves_alone(binary, ed, tmp_path):
    """Restoring the first partition of a disk with 4 partitions onto a disk with 3: the bytes that differ afterwards are inside TABLE_START..TABLE_END."""
    saved = tmp_path / "A.part"
    ed.export_partition(partition(ed), saved)
    three = AkaiEngine(binary).create_image(tmp_path / "three.img", next(f for f in formats.FORMATS if not f.is_floppy and "3000" in f.label), size_mb=15, partition_mb=5)
    eng = AkaiEngine(binary)
    eng.open([three])
    eng.set_writable(True)
    try:
        eng.import_partition(partition(eng), saved)  # (raises if the read-back differs outside the table)
        back = tmp_path / "back.part"
        eng.export_partition(partition(eng), back)
    finally:
        eng.close()
    a, b = saved.read_bytes(), back.read_bytes()
    differing = [i for i in range(len(a)) if a[i] != b[i]]
    assert differing and partition_files.TABLE_START <= differing[0] and differing[-1] < partition_files.TABLE_END


# ---------------------------------------------------------------- saving
def test_a_partition_is_saved_block_for_block(ed, tmp_path):
    part = partition(ed)
    out = tmp_path / "saved" / "A.part"
    out.parent.mkdir()
    assert ed.export_partition(part, out) == part.size_bytes == out.stat().st_size
    assert staging_is_empty(ed)
    assert not any(p.name.startswith(".") for p in out.parent.iterdir())  # (no temporary file left)


def test_saving_replaces_an_old_file_and_does_not_need_editing_on(binary, editable_hd, tmp_path):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])  # read-only
    try:
        out = tmp_path / "A.part"
        out.write_bytes(b"old")
        eng.export_partition(partition(eng), out)
        assert out.stat().st_size == partition(eng).size_bytes
    finally:
        eng.close()


def test_a_floppy_is_not_saved_this_way(binary, images, tmp_path):
    eng = AkaiEngine(binary)
    eng.open([images["fl1"]])
    try:
        floppy = eng.list_partitions(eng.list_disks()[0])[0]
        with pytest.raises(EngineError, match="image file is the whole disk"):
            eng.export_partition(floppy, tmp_path / "F.part")
        assert not (tmp_path / "F.part").exists()
    finally:
        eng.close()


# ---------------------------------------------------------------- restoring
def test_a_saved_partition_comes_back_after_its_volumes_are_gone(ed, tmp_path):
    part = partition(ed)
    saved = tmp_path / "A.part"
    ed.export_partition(part, saved)
    names = volume_names(ed, part)
    files = {v.name: [f.name for f in ed.list_files(v)] for v in ed.list_volumes(part)}
    assert len(names) == 3
    for vol in ed.list_volumes(part):
        ed.delete_volume(vol)
    assert volume_names(ed, part) == []
    ed.import_partition(part, saved)
    assert volume_names(ed, part) == names
    assert {v.name: [f.name for f in ed.list_files(v)] for v in ed.list_volumes(part)} == files
    assert staging_is_empty(ed)


def test_a_saved_partition_can_go_into_another_partition_of_the_disk(ed, tmp_path):
    saved = tmp_path / "A.part"
    ed.export_partition(partition(ed), saved)
    assert volume_names(ed, partition(ed, "B")) == []
    ed.import_partition(partition(ed, "B"), saved)
    assert volume_names(ed, partition(ed, "B")) == volume_names(ed, partition(ed, "A"))
    assert [p.name for p in ed.list_partitions(ed.list_disks()[0])] == ["A", "B", "C", "D"]  # nothing else moved


def test_the_first_partition_keeps_the_disks_own_partition_table(binary, ed, tmp_path):
    saved = tmp_path / "A.part"
    ed.export_partition(partition(ed), saved)
    three = AkaiEngine(binary).create_image(tmp_path / "three.img", next(f for f in formats.FORMATS if not f.is_floppy and "3000" in f.label), size_mb=15, partition_mb=5)
    eng = AkaiEngine(binary)
    eng.open([three])
    eng.set_writable(True)
    try:
        eng.import_partition(partition(eng), saved)  # (the file comes from a disk with four partitions)
        assert [p.name for p in eng.list_partitions(eng.list_disks()[0])] == ["A", "B", "C"]
        assert volume_names(eng, partition(eng)) == ["DRUMS", "BASS", "MY SYNTH"]
    finally:
        eng.close()


def test_a_file_that_does_not_fit_changes_nothing(ed, editable_hd, tmp_path):
    part = partition(ed)
    saved = tmp_path / "A.part"
    ed.export_partition(part, saved)
    image_before = editable_hd.read_bytes()
    for name, data in {
        "BIG": saved.read_bytes() * 2,  # (akaiutil would write all of it, over the partitions after this one)
        "SMALL": saved.read_bytes()[: part.size_bytes // 2],
        "BLANK": bytes(part.size_bytes),
        "ODD": saved.read_bytes() + b"x",
    }.items():
        bad = tmp_path / f"{name}.part"
        bad.write_bytes(data)
        with pytest.raises(EngineError):
            ed.import_partition(partition(ed, "B"), bad)
    assert editable_hd.read_bytes() == image_before
    assert staging_is_empty(ed)


def test_a_read_only_session_refuses_to_restore(binary, editable_hd, tmp_path):
    saved = tmp_path / "A.part"
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        eng.export_partition(partition(eng), saved)
        before = editable_hd.read_bytes()
        with pytest.raises(EngineError, match="switched off"):
            eng.import_partition(partition(eng, "B"), saved)
        assert editable_hd.read_bytes() == before
    finally:
        eng.close()


def test_a_restore_that_does_not_read_back_the_same_says_so(ed, tmp_path, monkeypatch):
    saved = tmp_path / "A.part"
    ed.export_partition(partition(ed), saved)
    monkeypatch.setattr(partition_files, "same_apart_from", lambda *a, **kw: False)
    with pytest.raises(EngineError, match="doesn't read back the same"):
        ed.import_partition(partition(ed, "B"), saved)
    assert staging_is_empty(ed)


# ---------------------------------------------------------------- direct-to-disk areas
@pytest.fixture
def dd_ed(binary, images, tmp_path):
    image = tmp_path / "edit.img"
    shutil.copy(images["dd"], image)
    eng = AkaiEngine(binary)
    eng.open([image])
    eng.set_writable(True)
    yield eng
    eng.close()


def test_a_direct_to_disk_area_goes_to_a_file_and_back(dd_ed, tmp_path):
    part = next(p for p in dd_ed.list_partitions(dd_ed.list_disks()[0]) if p.is_dd)
    saved = tmp_path / "DD.part"
    assert dd_ed.export_partition(part, saved) == part.size_bytes
    takes = [(t.index, t.name) for t in dd_ed.list_takes(part)]
    assert len(takes) == 2
    assert dd_ed.delete_takes(part, dd_ed.list_takes(part)) == []
    assert dd_ed.list_takes(part) == []
    dd_ed.import_partition(part, saved)
    assert [(t.index, t.name) for t in dd_ed.list_takes(part)] == takes


def test_a_sampler_partition_is_not_put_over_a_direct_to_disk_area(dd_ed, ed, tmp_path):
    sampler = tmp_path / "A.part"
    ed.export_partition(partition(ed), sampler)  # (5 MB, like the DD area's neighbours; the size check comes first, so use its size)
    part = next(p for p in dd_ed.list_partitions(dd_ed.list_disks()[0]) if p.is_dd)
    forged = tmp_path / "FORGED.part"
    forged.write_bytes(sampler.read_bytes()[: part.size_bytes].ljust(part.size_bytes, b"\0"))
    with pytest.raises(EngineError, match="not a direct-to-disk area"):
        dd_ed.import_partition(part, forged)


# ---------------------------------------------------------------- what the review found
def test_the_comparison_works_a_block_at_a_time_and_skips_the_table_across_block_edges(tmp_path, monkeypatch):
    monkeypatch.setattr(partition_files, "COMPARE_BLOCK", 7)  # (the skipped range 10..25 starts in one block and ends in a third)
    base = bytes(range(100))
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(base)

    def other(changed_at):
        data = bytearray(base)
        data[changed_at] ^= 0xFF
        b.write_bytes(data)
        return partition_files.same_apart_from(a, b, (10, 25))

    assert other(10) and other(17) and other(24)  # (inside the skipped bytes)
    assert not other(9) and not other(25) and not other(0) and not other(99)  # (just outside, and the ends)
    b.write_bytes(base + b"x")
    assert not partition_files.same_apart_from(a, b, (10, 25))  # (a different size)
    b.write_bytes(base)
    assert partition_files.same_apart_from(a, b) and partition_files.same_apart_from(a, b, (10, 25))


def test_only_akaiutils_error_line_makes_a_save_fail_not_a_name_with_error_in_it(ed, tmp_path, monkeypatch):
    from types import SimpleNamespace

    real = ed._run

    def with_text(extra):
        return lambda cmd, timeout=120: SimpleNamespace(text=real(cmd, timeout=timeout).text + extra)

    part = partition(ed)
    monkeypatch.setattr(ed, "_run", with_text("\nterrors.img\n"))
    assert ed.export_partition(part, tmp_path / "ok.part") == part.size_bytes
    monkeypatch.setattr(ed, "_run", with_text("\nerror in block 0x10\n"))
    with pytest.raises(EngineError, match="couldn't read all"):
        ed.export_partition(part, tmp_path / "bad.part")
    assert not (tmp_path / "bad.part").exists()


def test_the_tag_names_are_forgotten_even_when_putpart_gives_up(ed, tmp_path, monkeypatch):
    saved = tmp_path / "A.part"
    ed.export_partition(partition(ed), saved)
    ed._tag_names[(0, "B")] = {1: "OLD"}
    real = ed._run

    def failing(cmd, timeout=120):
        if cmd.startswith("putpart"):
            raise EngineError("akaiutil didn't answer")
        return real(cmd, timeout=timeout)

    monkeypatch.setattr(ed, "_run", failing)
    with pytest.raises(EngineError, match="didn't answer"):
        ed.import_partition(partition(ed, "B"), saved)
    assert ed._tag_names == {}
    assert staging_is_empty(ed)


def test_a_save_or_restore_with_no_room_for_the_temporary_copy_says_so(ed, editable_hd, tmp_path, monkeypatch):
    from collections import namedtuple

    from akaiutil_gui.engine import api

    part = partition(ed)
    saved = tmp_path / "A.part"
    ed.export_partition(part, saved)
    before = editable_hd.read_bytes()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(api.shutil, "disk_usage", lambda path: usage(10**9, 10**9 - 1000, 1000))
    with pytest.raises(EngineError, match="enough free space"):
        ed.export_partition(part, tmp_path / "B.part")
    with pytest.raises(EngineError, match="enough free space"):
        ed.import_partition(partition(ed, "B"), saved)
    assert not (tmp_path / "B.part").exists() and editable_hd.read_bytes() == before and staging_is_empty(ed)
