"""The direct-to-disk test image (tests/imagegen.py) and what the engine already says about it.

The take table, export and import come later (dev_docs/dd-takes-plan.md); this checks the fixture those need.
"""

import pytest

from akaiutil_gui.engine.api import AkaiEngine


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
