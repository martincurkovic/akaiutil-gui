"""Copying and moving files between volumes, partitions and disks, with the real akaiutil."""

import shutil

import numpy as np
import pytest
import soundfile

from akaiutil_gui import importing, transfer
from akaiutil_gui.engine.api import AkaiEngine


@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def part(eng, letter="A", disk=0):
    return next(p for p in eng.list_partitions(eng.list_disks()[disk]) if p.name == letter)


def vols(eng, letter="A", disk=0):
    return {v.name: v for v in eng.list_volumes(part(eng, letter, disk))}


def file(eng, vol, name):
    return next(f for f in eng.list_files(vol) if f.name == name)


def names(eng, vol):
    return [f.name for f in eng.list_files(vol)]


def raw(eng, vol, f, folder):
    folder.mkdir(exist_ok=True)
    eng.export_files(vol, [f], folder, confirm_overwrite=lambda p: True)
    return (folder / f.name).read_bytes()


# ---------------------------------------------------------------------------- copying
def test_a_copy_is_the_same_file_in_another_volume_and_the_original_stays(ed, tmp_path):
    v = vols(ed)
    kick = file(ed, v["DRUMS"], "KICK.S3")
    result = transfer.transfer_files(ed, [(v["DRUMS"], kick)], v["BASS"], move=False)
    assert result.done == ["KICK.S3"] and result.failed == [] and result.skipped == []
    assert names(ed, v["BASS"]) == ["BASS.S3", "KICK.S3"] and names(ed, v["DRUMS"]) == ["KICK.S3", "SNARE.S3"]
    copy = file(ed, v["BASS"], "KICK.S3")
    assert copy.size == kick.size
    assert raw(ed, v["BASS"], copy, tmp_path / "a") == raw(ed, v["DRUMS"], kick, tmp_path / "b")


def test_copying_several_keeps_their_order_and_slots(ed):
    v = vols(ed)
    items = [(v["DRUMS"], f) for f in ed.list_files(v["DRUMS"])]
    result = transfer.transfer_files(ed, items, v["MY SYNTH"], move=False)
    assert result.done == ["KICK.S3", "SNARE.S3"]
    assert names(ed, v["MY SYNTH"]) == ["LEAD.S3", "KICK.S3", "SNARE.S3"]


def test_a_copy_into_the_same_volume_is_a_numbered_duplicate_with_its_own_name_inside(ed):
    v = vols(ed)
    kick = file(ed, v["DRUMS"], "KICK.S3")
    result = transfer.transfer_files(ed, [(v["DRUMS"], kick)], v["DRUMS"], move=False)
    assert result.done == ["KICK 2.S3"]
    again = transfer.transfer_files(ed, [(v["DRUMS"], kick)], v["DRUMS"], move=False)
    assert again.done == ["KICK 3.S3"]
    assert names(ed, v["DRUMS"]) == ["KICK.S3", "SNARE.S3", "KICK 2.S3", "KICK 3.S3"]
    info = ed.file_info(v["DRUMS"], file(ed, v["DRUMS"], "KICK 2.S3"))
    assert 'ramname: "KICK 2"' in info  # the name inside the sample follows the new name


def test_a_name_already_in_the_target_is_never_replaced(ed):
    v = vols(ed)
    ed.rename_file(v["BASS"], file(ed, v["BASS"], "BASS.S3"), "kick")
    before = file(ed, v["BASS"], "KICK.S3")
    result = transfer.transfer_files(ed, [(v["DRUMS"], file(ed, v["DRUMS"], "KICK.S3"))], v["BASS"], move=False)
    assert result.done == ["KICK 2.S3"]
    assert file(ed, v["BASS"], "KICK.S3").size == before.size and len(ed.list_files(v["BASS"])) == 2


def test_copies_go_to_another_partition(ed, tmp_path):
    a, b = vols(ed, "A"), vols(ed, "B")
    assert b == {}
    new = ed.create_volume(part(ed, "B"), 1, "ELSEWHERE", "S3000")
    kick = file(ed, a["DRUMS"], "KICK.S3")
    result = transfer.transfer_files(ed, [(a["DRUMS"], kick)], new, move=False)
    assert result.done == ["KICK.S3"]
    assert raw(ed, new, file(ed, new, "KICK.S3"), tmp_path / "x") == raw(ed, a["DRUMS"], kick, tmp_path / "y")


def test_copies_go_to_another_disk(binary, editable_hd, tmp_path):
    second = tmp_path / "second.img"
    shutil.copy(editable_hd, second)
    eng = AkaiEngine(binary)
    eng.open([editable_hd, second])
    eng.set_writable(True)
    try:
        src = vols(eng, "A", 0)["DRUMS"]
        dst = vols(eng, "A", 1)["BASS"]
        result = transfer.transfer_files(eng, [(src, file(eng, src, "SNARE.S3"))], dst, move=False)
        assert result.done == ["SNARE.S3"] and "SNARE.S3" in names(eng, dst)
        assert "SNARE.S3" in names(eng, src)
    finally:
        eng.close()


# ----------------------------------------------------------------------------- moving
def test_a_move_inside_a_partition_changes_nothing_but_where_the_file_is(ed, tmp_path):
    v = vols(ed)
    kick = file(ed, v["DRUMS"], "KICK.S3")
    original = raw(ed, v["DRUMS"], kick, tmp_path / "before")
    result = transfer.transfer_files(ed, [(v["DRUMS"], kick)], v["BASS"], move=True)
    assert result.done == ["KICK.S3"] and result.failed == []
    assert names(ed, v["DRUMS"]) == ["SNARE.S3"] and names(ed, v["BASS"]) == ["BASS.S3", "KICK.S3"]
    moved = file(ed, v["BASS"], "KICK.S3")
    assert moved.start_block == kick.start_block  # the data itself wasn't copied or moved
    assert raw(ed, v["BASS"], moved, tmp_path / "after") == original


def test_moving_several_files(ed):
    v = vols(ed)
    items = [(v["DRUMS"], f) for f in ed.list_files(v["DRUMS"])]
    result = transfer.transfer_files(ed, items, v["BASS"], move=True)
    assert result.done == ["KICK.S3", "SNARE.S3"] and names(ed, v["DRUMS"]) == []
    assert names(ed, v["BASS"]) == ["BASS.S3", "KICK.S3", "SNARE.S3"]


def test_a_move_gets_a_numbered_name_rather_than_replacing(ed):
    v = vols(ed)
    ed.rename_file(v["BASS"], file(ed, v["BASS"], "BASS.S3"), "snare")
    result = transfer.transfer_files(ed, [(v["DRUMS"], file(ed, v["DRUMS"], "SNARE.S3"))], v["BASS"], move=True)
    assert result.done == ["SNARE 2.S3"] and names(ed, v["BASS"]) == ["SNARE.S3", "SNARE 2.S3"]


def test_a_move_to_the_volume_it_is_already_in_is_skipped(ed):
    v = vols(ed)
    result = transfer.transfer_files(ed, [(v["DRUMS"], file(ed, v["DRUMS"], "KICK.S3"))], v["DRUMS"], move=True)
    assert result.done == [] and result.skipped == ["KICK.S3: already in DRUMS"]
    assert names(ed, v["DRUMS"]) == ["KICK.S3", "SNARE.S3"]


def test_a_move_to_another_partition_copies_then_deletes_the_original(ed, tmp_path):
    a = vols(ed, "A")
    new = ed.create_volume(part(ed, "B"), 1, "ELSEWHERE", "S3000")
    kick = file(ed, a["DRUMS"], "KICK.S3")
    original = raw(ed, a["DRUMS"], kick, tmp_path / "x")
    result = transfer.transfer_files(ed, [(a["DRUMS"], kick)], new, move=True)
    assert result.done == ["KICK.S3"] and result.failed == []
    assert names(ed, a["DRUMS"]) == ["SNARE.S3"]
    assert raw(ed, new, file(ed, new, "KICK.S3"), tmp_path / "y") == original


def test_the_original_survives_if_the_copy_part_of_a_move_fails(ed):
    a = vols(ed, "A")
    new = ed.create_volume(part(ed, "B"), 1, "ELSEWHERE", "S3000")
    kick = file(ed, a["DRUMS"], "KICK.S3")
    ed.copy_file = lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("disk on fire"))  # (the copy can't happen)
    result = transfer.transfer_files(ed, [(a["DRUMS"], kick)], new, move=True)
    assert result.done == [] and result.failed == ["KICK.S3: disk on fire"]
    assert names(ed, a["DRUMS"]) == ["KICK.S3", "SNARE.S3"]  # still there: nothing was deleted


# ------------------------------------------------------------------------------ rules
def test_files_of_the_wrong_sampler_arent_copied_into_a_volume(binary, images, editable_hd, tmp_path):
    floppy = tmp_path / "f9.img"
    shutil.copy(images["fl9"], floppy)
    eng = AkaiEngine(binary)
    eng.open([editable_hd, floppy])
    eng.set_writable(True)
    try:
        hd, fl = vols(eng, "A", 0)["DRUMS"], next(iter(vols(eng, "A", 1).values()))
        assert fl.type == "S900"
        refused = transfer.transfer_files(eng, [(hd, file(eng, hd, "KICK.S3"))], fl, move=False)
        assert refused.done == [] and refused.failed == ["KICK.S3: S3000 files don't belong in an S900 volume"]
        assert names(eng, fl) == ["KICK.S9"]
        accepted = transfer.transfer_files(eng, [(fl, file(eng, fl, "KICK.S9"))], hd, move=False)  # an S3000 volume takes S900 samples
        assert accepted.done == ["KICK.S9"] and "KICK.S9" in names(eng, hd)
    finally:
        eng.close()


def test_a_compressed_s900_sample_keeps_its_uncompressed_size_when_copied(binary, images, tmp_path):
    src, dst = tmp_path / "a.img", tmp_path / "b.img"
    shutil.copy(images["fl9"], src)
    shutil.copy(images["fl9"], dst)
    wav = tmp_path / "tone.wav"
    t = np.arange(8000) / 40000
    soundfile.write(str(wav), 0.4 * np.sin(2 * np.pi * 440 * t), 40000, subtype="PCM_16")
    eng = AkaiEngine(binary)
    eng.open([src, dst])
    eng.set_writable(True)
    try:
        a, b = (next(iter(vols(eng, "A", d).values())) for d in (0, 1))
        made = importing.run_import(eng, a, [importing.inspect(wav)], tmp_path / "w", "S900", compress=True)
        assert made.imported == ["TONE.S9C"]
        original = file(eng, a, "TONE.S9C")
        result = transfer.transfer_files(eng, [(a, original)], b, move=False)
        assert result.done == ["TONE.S9C"], result
        assert file(eng, b, "TONE.S9C").os_version == original.os_version  # the "uncompr" column
        assert file(eng, b, "TONE.S9C").size == original.size
    finally:
        eng.close()


# ------------------------------------------------------------------------ robustness
def test_one_file_that_cant_be_transferred_doesnt_stop_the_others(ed):
    v = vols(ed)
    kick, snare = ed.list_files(v["DRUMS"])
    ed.delete_files(v["DRUMS"], [kick])  # `kick` is stale now
    result = transfer.transfer_files(ed, [(v["DRUMS"], kick), (v["DRUMS"], snare)], v["BASS"], move=False)
    assert result.done == ["SNARE.S3"]
    assert len(result.failed) == 1 and result.failed[0].startswith("KICK.S3: ") and "isn't file 1" in result.failed[0]


def test_progress_is_reported_for_each_file(ed):
    v = vols(ed)
    seen = []
    items = [(v["DRUMS"], f) for f in ed.list_files(v["DRUMS"])]
    transfer.transfer_files(ed, items, v["BASS"], move=False, progress=lambda i, n, name: seen.append((i, n, name)))
    assert seen == [(0, 2, "KICK.S3"), (1, 2, "SNARE.S3")]


def test_transfers_need_editing_to_be_on(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        v = vols(eng)
        result = transfer.transfer_files(eng, [(v["DRUMS"], file(eng, v["DRUMS"], "KICK.S3"))], v["BASS"], move=False)
        assert result.done == [] and "Editing is switched off" in result.failed[0]
    finally:
        eng.close()


def test_a_move_across_partitions_is_refused_by_the_engine_itself(ed):
    a = vols(ed, "A")
    new = ed.create_volume(part(ed, "B"), 1, "ELSEWHERE", "S3000")
    with pytest.raises(Exception, match="only be moved within a partition"):
        ed.move_file(a["DRUMS"], file(ed, a["DRUMS"], "KICK.S3"), new, "KICK.S3")
