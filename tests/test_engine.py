"""End-to-end against the real akaiutil binary and generated images."""

import os

import pytest

from akaiutil_gui.engine.session import EngineError


def test_lists_three_disks(engine):
    disks = engine.list_disks()
    assert [d.type for d in disks] == ["HD", "FLH", "FLL"]


def test_hd_partitions_and_volumes(engine):
    hd = engine.list_disks()[0]
    parts = engine.list_partitions(hd)
    assert len(parts) == 18 and parts[0].name == "A"
    vols = engine.list_volumes(parts[0])
    assert [v.name for v in vols] == ["DRUMS", "BASS", "MY SYNTH"]


def test_files_in_volumes_incl_space_in_name(engine):
    hd = engine.list_disks()[0]
    vols = engine.list_volumes(engine.list_partitions(hd)[0])
    assert [f.name for f in engine.list_files(vols[0])] == ["KICK.S3", "SNARE.S3"]
    assert [f.name for f in engine.list_files(vols[2])] == ["LEAD.S3"]  # "MY SYNTH"


def test_floppies(engine):
    _, fl1, fl9 = engine.list_disks()
    (v1,) = engine.list_volumes(engine.list_partitions(fl1)[0])
    assert v1.type == "S1000"
    assert [f.name for f in engine.list_files(v1)] == ["KICK.S1", "SNARE.S1"]
    (v9,) = engine.list_volumes(engine.list_partitions(fl9)[0])
    assert v9.type == "S900"
    assert [f.name for f in engine.list_files(v9)] == ["KICK.S9"]


def test_info_text(engine):
    hd = engine.list_disks()[0]
    part = engine.list_partitions(hd)[0]
    vol = engine.list_volumes(part)[0]
    f = engine.list_files(vol)[0]
    assert "S1000/S3000 harddisk" in engine.disk_info(hd)
    assert "parttype" in engine.partition_info(part)
    assert 'vname:       "DRUMS"' in engine.volume_info(vol)
    assert 'ramname: "KICK"' in engine.file_info(vol, f)


def test_state_does_not_leak_between_calls(engine):
    # listing files leaves the process inside a volume; later partition/disk
    # listings must not be affected by where it happens to be
    hd = engine.list_disks()[0]
    part = engine.list_partitions(hd)[0]
    vol = engine.list_volumes(part)[0]
    engine.list_files(vol)
    assert len(engine.list_partitions(hd)) == 18
    assert len(engine.list_volumes(part)) == 3


def test_export_raw_and_wav_into_dir_with_spaces(engine, tmp_path):
    hd = engine.list_disks()[0]
    vol = engine.list_volumes(engine.list_partitions(hd)[0])[0]
    files = engine.list_files(vol)
    dest = tmp_path / "my samples"  # akaiutil itself can't take this via lcd
    dest.mkdir()

    raw = engine.export_files(vol, files, dest)
    assert sorted(os.path.basename(p) for p in raw.exported) == ["KICK.S3", "SNARE.S3"]
    assert (dest / "KICK.S3").stat().st_size == files[0].size

    wav = engine.export_files(vol, files, dest, as_wav=True)
    assert sorted(os.path.basename(p) for p in wav.exported) == ["KICK.wav", "SNARE.wav"]
    assert (dest / "KICK.wav").read_bytes()[:4] == b"RIFF"


def test_export_never_overwrites_unless_confirmed(engine, tmp_path):
    hd = engine.list_disks()[0]
    vol = engine.list_volumes(engine.list_partitions(hd)[0])[0]
    files = engine.list_files(vol)[:1]
    (tmp_path / "KICK.S3").write_bytes(b"precious")

    res = engine.export_files(vol, files, tmp_path)
    assert res.skipped and not res.exported
    assert (tmp_path / "KICK.S3").read_bytes() == b"precious"

    res = engine.export_files(vol, files, tmp_path, confirm_overwrite=lambda p: True)
    assert res.exported and (tmp_path / "KICK.S3").stat().st_size == files[0].size


def test_wav_export_of_non_sample_is_refused(engine, tmp_path):
    hd = engine.list_disks()[0]
    vol = engine.list_volumes(engine.list_partitions(hd)[0])[0]
    prog = type(engine.list_files(vol)[0])(1, "THING.P3", 100, "0x0001", "17.00")
    res = engine.export_files(vol, [prog], tmp_path, as_wav=True)
    assert res.failed and not res.exported


def test_images_are_never_modified(engine, images):
    # the GUI only ever opens images read-only (-r)
    import hashlib

    def digests():
        return {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in images.items()}

    before = digests()
    hd = engine.list_disks()[0]
    vol = engine.list_volumes(engine.list_partitions(hd)[0])[0]
    engine.list_files(vol)
    engine.export_files(vol, engine.list_files(vol), images["hd"].parent / "wavs")
    assert digests() == before


def test_close_and_errors(binary, images):
    from akaiutil_gui.engine.api import AkaiEngine

    eng = AkaiEngine(binary)
    with pytest.raises(EngineError):
        eng.list_disks()  # nothing open yet
    eng.open([images["fl9"]])
    assert eng.is_open
    eng.close()
    assert not eng.is_open
    with pytest.raises(EngineError):
        AkaiEngine("/nonexistent/akaiutil").open([images["fl9"]])


def test_disk_usage_adds_up(engine):
    hd, fl1, fl9 = engine.list_disks()
    u = engine.disk_usage(hd)
    assert [(v.partition, v.index, v.name, v.files) for v in u.volumes] == [
        ("A", 1, "DRUMS", 2),
        ("A", 2, "BASS", 1),
        ("A", 3, "MY SYNTH", 1),
    ]
    for d in (hd, fl1, fl9):
        usage = engine.disk_usage(d)
        for p in usage.partitions:
            files = sum(v.used_bytes for v in usage.volumes_in(p.name))
            overhead = p.used_bytes - files  # headers, FAT and volume directories
            assert 0 <= overhead < 0.1 * p.size_bytes, (d.path, p.name, overhead)
