"""The direct-to-disk test image (tests/imagegen.py) and what the engine already says about it.

The take table, export and import come later (dev_docs/dd-takes-plan.md); this checks the fixture those need.
"""

import shutil
import wave
from pathlib import Path

import pytest

import imagegen
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import Take


@pytest.fixture
def dd_engine(binary, images):
    eng = AkaiEngine(binary)
    eng.open([images["dd"]])
    yield eng
    eng.close()


def _dd_partition(eng):
    disk = eng.list_disks()[0]
    return next(p for p in eng.list_partitions(disk) if p.is_dd)


def test_image_has_a_dd_partition_after_the_sampler_partitions(dd_engine):
    parts = dd_engine.list_partitions(dd_engine.list_disks()[0])
    assert [p.name for p in parts] == ["A", "B", "DD"]
    assert [p.is_dd for p in parts] == [False, False, True]


def test_dd_partition_lists_a_mono_and_a_stereo_take(dd_engine):
    text = dd_engine.list_takes_text(_dd_partition(dd_engine))
    rows = [ln.split() for ln in text.splitlines() if ln.strip()[:1].isdigit()]
    assert [(r[0], r[1]) for r in rows] == [("1", "MONO"), ("2", "STEREO")]
    assert [r[-1] for r in rows] == ["MONO", "STEREO"]
    assert "total:   2 take(s)" in text


def test_scount_counts_both_channels_of_a_stereo_take(dd_engine):
    # one second at 44.1 kHz: 44,100 words mono, 88,200 stereo (the take table's duration has to halve the second one)
    text = dd_engine.list_takes_text(_dd_partition(dd_engine))
    rows = {r[1]: r for r in (ln.split() for ln in text.splitlines() if ln.strip()[:1].isdigit())}
    assert rows["MONO"][-3] == "44100" and rows["STEREO"][-3] == "88200"
    assert rows["MONO"][-2] == rows["STEREO"][-2] == "44100"  # sample rate


def test_engine_lists_the_takes_as_objects(dd_engine):
    takes = dd_engine.list_takes(_dd_partition(dd_engine))
    assert [(t.index, t.name, t.stereo, t.rate) for t in takes] == [(1, "MONO", False, 44100), (2, "STEREO", True, 44100)]
    assert [t.seconds for t in takes] == [1.0, 1.0]  # one second each: the stereo take's words are halved
    assert [t.disk_bytes for t in takes] == [2 * 262144] * 2  # a sample cluster and an envelope cluster


def test_a_takes_details_read_as_settings_not_as_akaiutil_prints_them(dd_engine):
    from akaiutil_gui import describe

    part = _dd_partition(dd_engine)
    take = dd_engine.list_takes(part)[1]
    details = describe.describe_take(dd_engine.take_info(part, take), take)
    assert (details.title, details.subtitle) == ("STEREO", "Direct-to-disk take 2")
    rows = {r.label: r.value for b in details.blocks for r in b.rows}
    assert rows["Type"] == "Stereo" and rows["Sample rate"] == "44,100 Hz" and rows["Length"] == "1.00 s"
    assert rows["Fade in"] == "10ms" and rows["MIDI channel"] == "Channel 16" and rows["MIDI note"] == "C3  (MIDI 60)"
    assert rows["Output"] == "OFF" and rows["FX bus"] == "OFF"
    assert [b.title for b in details.blocks] == ["Take", "Playback", "Output", "Technical"]


def _frames(path: Path):
    with wave.open(str(path), "rb") as w:
        return w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())


def test_a_take_exports_as_the_wav_it_was_made_from(dd_engine, images, tmp_path):
    part = _dd_partition(dd_engine)
    takes = dd_engine.list_takes(part)
    res = dd_engine.export_takes(part, takes, tmp_path, as_wav=True)
    assert (res.skipped, res.failed) == ([], [])
    assert sorted(Path(p).name for p in res.exported) == ["MONO.wav", "STEREO.wav"]
    for name in ("MONO", "STEREO"):
        assert _frames(tmp_path / f"{name}.wav") == _frames(images["dd"].parent / "wavs" / f"{name}.wav")  # the audio comes back bit for bit
    assert _frames(tmp_path / "STEREO.wav")[:2] == (2, 44100)


def test_a_take_exports_raw_as_a_tk_file(dd_engine, tmp_path):
    part = _dd_partition(dd_engine)
    res = dd_engine.export_takes(part, dd_engine.list_takes(part)[:1], tmp_path)
    assert [Path(p).name for p in res.exported] == ["MONO.TK"]
    assert (tmp_path / "MONO.TK").stat().st_size == 2 * 262144 + 64  # a sample cluster, an envelope cluster, and 64 bytes more


def test_existing_files_are_left_alone_unless_the_caller_agrees(dd_engine, tmp_path):
    part = _dd_partition(dd_engine)
    take = dd_engine.list_takes(part)[:1]
    (tmp_path / "MONO.wav").write_bytes(b"mine")
    res = dd_engine.export_takes(part, take, tmp_path, as_wav=True)
    assert (res.exported, res.skipped) == ([], [str(tmp_path / "MONO.wav")]) and (tmp_path / "MONO.wav").read_bytes() == b"mine"
    res = dd_engine.export_takes(part, take, tmp_path, as_wav=True, confirm_overwrite=lambda _p: True)
    assert len(res.exported) == 1 and (tmp_path / "MONO.wav").read_bytes() != b"mine"


def test_two_takes_with_one_name_do_not_overwrite_each_other(binary, images, tmp_path):
    image = tmp_path / "twins.img"
    shutil.copy(images["dd"], image)
    imagegen._script(binary, image, ["cd /disk0/DD", "wav2take MONO.wav"], images["dd"].parent / "wavs")  # a second MONO
    eng = AkaiEngine(binary)
    eng.open([image])
    try:
        part = _dd_partition(eng)
        takes = [t for t in eng.list_takes(part) if t.name == "MONO"]
        assert [t.index for t in takes] == [1, 3]
        out = tmp_path / "out"
        out.mkdir()
        res = eng.export_takes(part, takes, out, as_wav=True)
        assert sorted(p.name for p in out.iterdir()) == ["MONO (take 3).wav", "MONO.wav"] and len(res.exported) == 2
    finally:
        eng.close()


def test_a_take_that_is_not_there_is_reported_not_raised(dd_engine, tmp_path):
    part = _dd_partition(dd_engine)
    ghost = Take(index=9, name="GHOST", sample_clusters=1, envelope_clusters=1, words=1, rate=44100, stereo=False)
    res = dd_engine.export_takes(part, [ghost], tmp_path, as_wav=True)
    assert res.exported == [] and len(res.failed) == 1 and res.failed[0].startswith("GHOST:")
    assert list(tmp_path.iterdir()) == []
