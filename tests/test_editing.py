"""Changing disk images through the engine: rename, delete, create. Real akaiutil, on copies of a small image."""

import os
import stat

import pytest

from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.session import EngineError


@pytest.fixture
def ed(binary, editable_hd):
    """An engine on the editable image, with editing switched on."""
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def _part_a(eng):
    return eng.list_partitions(eng.list_disks()[0])[0]


def _volumes(eng):
    return eng.list_volumes(_part_a(eng))


def _reread(binary, image):
    """What a fresh, read-only look at the image on disk shows (proves the change really was written)."""
    eng = AkaiEngine(binary)
    eng.open([image])
    try:
        part = eng.list_partitions(eng.list_disks()[0])[0]
        return part, eng.list_volumes(part), eng
    except Exception:
        eng.close()
        raise


# ------------------------------------------------------------------ the mode
def test_an_engine_opens_read_only_and_refuses_to_edit(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        assert not eng.is_writable
        vol = _volumes(eng)[0]
        with pytest.raises(EngineError, match="Editing is switched off"):
            eng.rename_volume(vol, "NOPE")
        assert [v.name for v in _volumes(eng)] == ["DRUMS", "BASS", "MY SYNTH"]
    finally:
        eng.close()


def test_switching_editing_on_and_off_keeps_the_same_images_open(ed):
    before = [v.name for v in _volumes(ed)]
    assert ed.is_writable
    ed.set_writable(False)
    assert not ed.is_writable and [v.name for v in _volumes(ed)] == before
    ed.set_writable(True)
    assert ed.is_writable and [v.name for v in _volumes(ed)] == before


def test_a_read_only_file_cant_be_edited_and_stays_usable(binary, editable_hd):
    editable_hd.chmod(stat.S_IRUSR)
    if os.access(editable_hd, os.W_OK):
        pytest.skip("this account can write to read-only files")
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        with pytest.raises(EngineError):
            eng.set_writable(True)
        assert eng.is_open and not eng.is_writable  # fell back to reading
        assert [v.name for v in _volumes(eng)] == ["DRUMS", "BASS", "MY SYNTH"]
    finally:
        eng.close()


# ---------------------------------------------------------------------- volumes
def test_renaming_a_volume_is_written_to_the_image(ed, binary, editable_hd):
    drums = _volumes(ed)[0]
    renamed = ed.rename_volume(drums, "kick kit")
    assert (renamed.index, renamed.name) == (drums.index, "KICK KIT")
    assert [v.name for v in _volumes(ed)] == ["KICK KIT", "BASS", "MY SYNTH"]
    assert [f.name for f in ed.list_files(renamed)] == ["KICK.S3", "SNARE.S3"]  # its files came along

    ed.close()  # then look at the file afresh
    _, vols, fresh = _reread(binary, editable_hd)
    try:
        assert [v.name for v in vols] == ["KICK KIT", "BASS", "MY SYNTH"]
    finally:
        fresh.close()


def test_renaming_a_volume_with_a_space_in_its_name(ed):
    synth = _volumes(ed)[2]
    assert synth.name == "MY SYNTH"
    assert ed.rename_volume(synth, "YOUR SYNTH 2").name == "YOUR SYNTH 2"
    assert [v.name for v in _volumes(ed)] == ["DRUMS", "BASS", "YOUR SYNTH 2"]


@pytest.mark.parametrize("bad, message", [("", "Type a name"), ("THIS NAME IS TOO LONG", "At most 12"), ("A/B", "Not allowed")])
def test_a_bad_volume_name_is_refused_before_akaiutil_sees_it(ed, bad, message):
    with pytest.raises(EngineError, match=message):
        ed.rename_volume(_volumes(ed)[0], bad)
    assert [v.name for v in _volumes(ed)] == ["DRUMS", "BASS", "MY SYNTH"]


def test_a_volume_cant_take_another_volumes_name(ed):
    drums = _volumes(ed)[0]
    with pytest.raises(EngineError, match="already a volume called “BASS”"):
        ed.rename_volume(drums, "bass")
    assert [v.name for v in _volumes(ed)] == ["DRUMS", "BASS", "MY SYNTH"]


def test_creating_a_volume_uses_the_number_name_type_and_load_number_given(ed):
    part = _part_a(ed)
    made = ed.create_volume(part, 7, "new kit", "S3000", load_number=5)
    assert (made.index, made.name, made.type, made.load_number) == (7, "NEW KIT", "S3000", "005")
    assert [v.index for v in _volumes(ed)] == [1, 2, 3, 7]
    assert ed.list_files(made) == []


@pytest.mark.parametrize("volume_type", ["S1000", "S3000", "CD3000"])
def test_each_kind_of_volume_can_be_created(ed, volume_type):
    made = ed.create_volume(_part_a(ed), 4, "kind test", volume_type)
    assert made.type == volume_type and made.load_number == "-"


def test_creating_a_volume_checks_what_akaiutil_would_get_wrong(ed):
    part = _part_a(ed)
    with pytest.raises(EngineError, match="Volume number 1 is already taken"):
        ed.create_volume(part, 1, "OTHER", "S3000")
    with pytest.raises(EngineError, match="called “DRUMS”"):
        ed.create_volume(part, 9, "drums", "S3000")
    with pytest.raises(EngineError, match="1 to 100"):
        ed.create_volume(part, 101, "TOO HIGH", "S3000")
    with pytest.raises(EngineError, match="1 to 100"):
        ed.create_volume(part, 0, "ZERO", "S3000")
    with pytest.raises(EngineError, match="load number is 1 to 128"):
        ed.create_volume(part, 9, "LOAD", "S3000", load_number=129)
    with pytest.raises(EngineError, match="cannot create this volume type for this partition type"):
        ed.create_volume(part, 9, "NINE", "S900")  # akaiutil's own check, passed on as it said it
    with pytest.raises(EngineError, match="Can't create a FOO volume"):
        ed.create_volume(part, 9, "FOO", "FOO")
    assert [v.name for v in _volumes(ed)] == ["DRUMS", "BASS", "MY SYNTH"]


def test_the_free_volume_numbers_come_from_the_partition(ed):
    used, limit = ed.volume_slots(_part_a(ed))
    assert (used, limit) == ([1, 2, 3], 100)


def test_deleting_a_volume_removes_it_and_its_files_and_frees_the_space(ed):
    part_before = _part_a(ed)
    drums = _volumes(ed)[0]
    ed.delete_volume(drums)
    assert [v.name for v in _volumes(ed)] == ["BASS", "MY SYNTH"]
    assert _part_a(ed).free_blocks > part_before.free_blocks


def test_deleting_a_volume_that_isnt_what_we_think_it_is_does_nothing(ed):
    bass = _volumes(ed)[1]
    ed.delete_volume(ed.rename_volume(_volumes(ed)[0], "X"))  # slot 1 is now gone
    stale = type(bass)(bass.disk, bass.partition, 1, "DRUMS", bass.load_number, bass.start_block, bass.os_version, bass.type)
    with pytest.raises(EngineError, match="isn't volume 1"):
        ed.delete_volume(stale)
    assert [v.name for v in _volumes(ed)] == ["BASS", "MY SYNTH"]


# ------------------------------------------------------------------------ files
def test_renaming_a_file_keeps_its_type_and_number(ed):
    drums = _volumes(ed)[0]
    kick = ed.list_files(drums)[0]
    renamed = ed.rename_file(drums, kick, "big kick")
    assert (renamed.index, renamed.name) == (kick.index, "BIG KICK.S3")
    assert [f.name for f in ed.list_files(drums)] == ["BIG KICK.S3", "SNARE.S3"]
    assert renamed.size == kick.size


def test_renaming_a_sample_also_renames_it_inside_the_file(ed):
    drums = _volumes(ed)[0]
    kick = ed.list_files(drums)[0]
    ed.rename_file(drums, kick, "thump")
    info = ed.file_info(drums, ed.list_files(drums)[0])
    assert 'ramname: "THUMP"' in info


def test_a_file_name_cant_change_its_type_by_typing_one(ed):
    drums = _volumes(ed)[0]
    kick = ed.list_files(drums)[0]
    renamed = ed.rename_file(drums, kick, "kick.p3")  # the dot is just a character in the name: the type stays .S3
    assert renamed.name.endswith(".S3") and renamed.is_sample


def test_a_file_cant_take_the_name_of_another_in_its_volume(ed):
    drums = _volumes(ed)[0]
    kick, snare = ed.list_files(drums)
    with pytest.raises(EngineError, match="already a file called “SNARE.S3”"):
        ed.rename_file(drums, kick, "snare")


def test_a_bad_file_name_is_refused(ed):
    drums = _volumes(ed)[0]
    kick = ed.list_files(drums)[0]
    for bad in ("", "WAY TOO LONG A NAME", "NO*PE"):
        with pytest.raises(EngineError):
            ed.rename_file(drums, kick, bad)
    assert [f.name for f in ed.list_files(drums)] == ["KICK.S3", "SNARE.S3"]


def test_a_file_that_has_moved_isnt_renamed_by_mistake(ed):
    drums = _volumes(ed)[0]
    kick, snare = ed.list_files(drums)
    ed.delete_files(drums, [kick])
    with pytest.raises(EngineError, match="isn't file 1"):
        ed.rename_file(drums, kick, "ghost")


def test_deleting_files_frees_their_space_and_leaves_the_others_numbered_as_they_were(ed):
    drums = _volumes(ed)[0]
    kick, snare = ed.list_files(drums)
    free = _part_a(ed).free_blocks
    assert ed.delete_files(drums, [kick]) == []
    assert [(f.index, f.name) for f in ed.list_files(drums)] == [(2, "SNARE.S3")]
    assert _part_a(ed).free_blocks > free


def test_deleting_several_files_reports_the_ones_that_were_not_there(ed):
    drums = _volumes(ed)[0]
    kick, snare = ed.list_files(drums)
    assert ed.delete_files(drums, [kick]) == []
    failed = ed.delete_files(drums, [kick, snare])
    assert len(failed) == 1 and failed[0].startswith("KICK.S3: it isn't file 1")
    assert ed.list_files(drums) == []


def test_changes_survive_closing_and_reopening(ed, binary, editable_hd):
    part = _part_a(ed)
    drums = _volumes(ed)[0]
    ed.rename_file(drums, ed.list_files(drums)[0], "persisted")
    ed.create_volume(part, 9, "added", "S3000")
    ed.delete_volume(_volumes(ed)[1])  # BASS
    ed.close()

    part, vols, fresh = _reread(binary, editable_hd)
    try:
        assert [(v.index, v.name) for v in vols] == [(1, "DRUMS"), (3, "MY SYNTH"), (9, "ADDED")]
        assert [f.name for f in fresh.list_files(vols[0])] == ["PERSISTED.S3", "SNARE.S3"]
    finally:
        fresh.close()


def test_a_read_only_look_cant_change_anything_even_after_editing_was_switched_off(ed):
    ed.set_writable(False)
    with pytest.raises(EngineError, match="Editing is switched off"):
        ed.delete_volume(_volumes(ed)[0])
    with pytest.raises(EngineError, match="Editing is switched off"):
        ed.delete_files(_volumes(ed)[0], ed.list_files(_volumes(ed)[0]))
    assert len(_volumes(ed)) == 3


# ------------------------------------------------------------ new images and formatting
from akaiutil_gui.engine import formats  # noqa: E402


def _look(binary, image):
    eng = AkaiEngine(binary)
    eng.open([image])
    try:
        disks = eng.list_disks()
        parts = eng.list_partitions(disks[0]) if disks and disks[0].partitions else []
        return disks, parts, [eng.list_volumes(p) for p in parts if not p.is_dd]
    finally:
        eng.close()


@pytest.mark.parametrize("fmt", formats.FORMATS, ids=lambda f: f.key)
def test_every_kind_of_new_image_is_created_empty_and_readable(binary, tmp_path, fmt):
    path = tmp_path / f"new_{fmt.key}.img"
    made = AkaiEngine(binary).create_image(path, fmt, size_mb=10, partition_mb=5)
    assert made == path and path.is_file()
    disks, parts, volumes = _look(binary, path)
    assert len(disks) == 1 and disks[0].partitions >= 1
    if fmt.is_floppy:
        assert path.stat().st_size == fmt.floppy_kb * 1024
        assert disks[0].type == ("FLL" if fmt.floppy_kb == 800 else "FLH")
    else:
        assert path.stat().st_size == 10 * formats.MB
        assert disks[0].type == ("HD9" if fmt.key == "hd9" else "HD")
        assert path.stat().st_size == disks[0].total_blocks * disks[0].block_size
    assert not any(v for vs in volumes for v in vs if v.type != "S900" and v.name != "NOT NAMED" and not fmt.is_floppy)  # hard disks start with no volumes


def test_a_new_hard_disk_is_cut_into_partitions_of_the_size_asked_for(binary, tmp_path):
    path = tmp_path / "parts.img"
    AkaiEngine(binary).create_image(path, formats.by_key("hd3"), size_mb=20, partition_mb=5)
    _, parts, _ = _look(binary, path)
    assert [p.name for p in parts] == ["A", "B", "C", "D"] and all(p.size_mb == 5 for p in parts)


def test_a_new_image_can_be_filled_in_straight_away(binary, tmp_path):
    path = tmp_path / "fill.img"
    AkaiEngine(binary).create_image(path, formats.by_key("hd3"), size_mb=10, partition_mb=5)
    eng = AkaiEngine(binary)
    eng.open([path])
    eng.set_writable(True)
    try:
        part = eng.list_partitions(eng.list_disks()[0])[0]
        assert eng.create_volume(part, 1, "FIRST", "S3000").name == "FIRST"
    finally:
        eng.close()


def test_bad_sizes_are_refused_and_leave_nothing_behind(binary, tmp_path):
    eng = AkaiEngine(binary)
    for size, part in [(0, 5), (513, 5), (10, 0), (10, 61)]:
        with pytest.raises(EngineError):
            eng.create_image(tmp_path / "bad.img", formats.by_key("hd3"), size_mb=size, partition_mb=part)
    with pytest.raises(EngineError, match="1 to 64 MB"):
        eng.create_image(tmp_path / "bad.img", formats.by_key("hd9"), size_mb=65)
    assert list(tmp_path.iterdir()) == []


def test_replacing_an_image_only_happens_once_the_new_one_is_ready(binary, tmp_path):
    path = tmp_path / "keep.img"
    path.write_bytes(b"precious")
    with pytest.raises(EngineError):
        AkaiEngine(binary).create_image(path, formats.by_key("hd3"), size_mb=9999)
    assert path.read_bytes() == b"precious"
    AkaiEngine(binary).create_image(path, formats.by_key("hd3"), size_mb=5, partition_mb=5)  # now it really is replaced
    assert path.stat().st_size == 5 * formats.MB
    assert [p.name for p in tmp_path.iterdir()] == ["keep.img"]  # no half-made file left over


def test_a_folder_that_cant_be_written_to_is_reported(binary, tmp_path):
    with pytest.raises(EngineError, match="Couldn't create"):
        AkaiEngine(binary).create_image(tmp_path / "no_such_folder" / "x.img", formats.by_key("hd3"), size_mb=5)


def test_formatting_a_disk_erases_it_and_starts_again(ed, binary, editable_hd):
    disk = ed.list_disks()[0]
    assert len(ed.list_partitions(disk)) == 4
    ed.format_disk(disk, formats.by_key("hd1"), partition_mb=10)
    disk = ed.list_disks()[0]
    parts = ed.list_partitions(disk)
    assert [(p.name, p.size_mb) for p in parts] == [("A", 10.0), ("B", 9.5)]  # the image is 19.5 MB
    assert ed.list_volumes(parts[0]) == [] and ed.list_volumes(parts[1]) == []
    ed.close()
    _, parts, volumes = _look(binary, editable_hd)
    assert len(parts) == 2 and volumes == [[], []]  # on disk, not just in what we're showing


def test_a_formatted_disk_takes_the_kind_of_volume_it_was_formatted_for(ed):
    ed.format_disk(ed.list_disks()[0], formats.by_key("hd1"))
    part = ed.list_partitions(ed.list_disks()[0])[0]
    assert ed.create_volume(part, 1, "ONE", "S1000").type == "S1000"


def test_formatting_needs_editing_to_be_on(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        with pytest.raises(EngineError, match="Editing is switched off"):
            eng.format_disk(eng.list_disks()[0], formats.by_key("hd3"))
        assert len(eng.list_partitions(eng.list_disks()[0])) == 4
    finally:
        eng.close()


def test_a_floppy_keeps_its_density_when_reformatted(binary, tmp_path):
    path = tmp_path / "floppy.img"
    AkaiEngine(binary).create_image(path, formats.by_key("fl1l"))
    eng = AkaiEngine(binary)
    eng.open([path])
    eng.set_writable(True)
    try:
        floppy = eng.list_disks()[0]
        assert floppy.type == "FLL"
        with pytest.raises(EngineError, match="DD floppy can't be formatted as “S3000 floppy \\(HD, 1.6 MB\\)”"):
            eng.format_disk(floppy, formats.by_key("fl3h"))
        with pytest.raises(EngineError, match="DD floppy can't be formatted as “S3000 hard disk”"):
            eng.format_disk(floppy, formats.by_key("hd3"))
        eng.format_disk(floppy, formats.by_key("fl3l"))  # same density, a different sampler
        (vol,) = eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])
        assert vol.type == "S3000"
    finally:
        eng.close()


def test_a_hard_disk_cant_become_a_floppy(ed):
    with pytest.raises(EngineError, match="hard disk image can't be formatted as “S3000 floppy"):
        ed.format_disk(ed.list_disks()[0], formats.by_key("fl3h"))
    assert len(ed.list_partitions(ed.list_disks()[0])) == 4


def test_formatting_one_disk_isnt_confused_by_another_blank_one(binary, editable_hd, tmp_path):
    blank = tmp_path / "blank.img"
    blank.write_bytes(bytes(8192 * 2500))  # 20 MB (a small file would be taken for a floppy)
    eng = AkaiEngine(binary)
    eng.open([editable_hd, blank])  # the second has no filesystem: akaiutil says "invalid format" about it every time it starts
    eng.set_writable(True)
    try:
        eng.format_disk(eng.list_disks()[0], formats.by_key("hd3"), partition_mb=5)
        assert [p.name for p in eng.list_partitions(eng.list_disks()[0])] == ["A", "B", "C", "D"]
        assert eng.list_disks()[1].partitions == 0  # the other one wasn't touched
    finally:
        eng.close()


def test_the_formats_offered_for_a_disk_fit_it():
    keys = lambda t: {f.key for f in formats.formats_for_disk(t)}  # noqa: E731
    assert keys("FLL") == {"fl3l", "fl1l", "fl9l"}
    assert keys("FLH") == {"fl3h", "fl1h", "fl9h"}
    assert keys("HD") == keys("HD9") == {"hd3", "hd1", "hd3cd", "hd9"}


@pytest.mark.parametrize(
    "total, part, expected",
    [(20, 5, (4, 20)), (100, 60, (2, 100)), (512, 60, (9, 512)), (600, 60, (9, 512)), (200, 5, (18, 90)), (3, 5, (1, 3))],
)
def test_the_partition_layout_matches_what_akaiutil_makes(total, part, expected):
    assert formats.partition_layout(total, part) == expected


def test_the_command_lines():
    assert formats.by_key("hd3").akaiutil_command(60, 128) == "formatharddisk3 60M 128M"
    assert formats.by_key("hd9").akaiutil_command(60, 20) == "formatharddisk9 20M"
    assert formats.by_key("fl1l").akaiutil_command() == "formatfloppyl1"
