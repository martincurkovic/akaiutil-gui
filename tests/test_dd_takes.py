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
