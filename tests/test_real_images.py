"""Checks against real disk images, if you have any (git-ignored, so other people's checkouts skip these).

They're looked for in testing_images/ at the repo root, or in the folder named by $AKAIUTIL_TEST_IMAGES.
"""

import hashlib
import os
from pathlib import Path

import pytest

from akaiutil_gui.engine.api import AkaiEngine

IMAGE_DIR = Path(os.environ.get("AKAIUTIL_TEST_IMAGES") or Path(__file__).resolve().parent.parent / "testing_images")
REAL = sorted(IMAGE_DIR.glob("*.img"))
pytestmark = pytest.mark.skipif(not REAL, reason=f"no real images in {IMAGE_DIR}")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.mark.parametrize("image", REAL, ids=lambda p: p.name)
def test_real_image_adds_up(binary, image):
    eng = AkaiEngine(binary)
    eng.open([image])
    try:
        for disk in eng.list_disks():
            usage = eng.disk_usage(disk)
            assert usage.partitions, f"{image.name}: no partitions"
            assert sum(p.size_blocks for p in usage.partitions) <= disk.total_blocks
            for p in usage.partitions:
                assert 0 <= p.free_blocks <= p.size_blocks
                files = sum(v.used_bytes for v in usage.volumes_in(p.name))
                overhead = p.used_bytes - files  # headers, FAT and volume directories
                assert 0 <= overhead < 0.02 * p.size_bytes, (image.name, p.name, overhead)
            order = usage.volume_order()
            assert sorted(order.values()) == list(range(len(order)))  # positions are 0..n-1, no gaps or repeats
            # every file listing was matched to its volume row
            assert len(order) == len(usage.volumes)
    finally:
        eng.close()


def test_real_image_is_never_modified(binary):
    image = REAL[0]
    before = _sha(image)
    eng = AkaiEngine(binary)
    eng.open([image])
    try:
        for disk in eng.list_disks():
            usage = eng.disk_usage(disk)
            for part in eng.list_partitions(disk)[:2]:
                for vol in eng.list_volumes(part)[:2]:
                    eng.list_files(vol)
            assert usage.volumes
    finally:
        eng.close()
    assert _sha(image) == before


@pytest.mark.parametrize("image", REAL, ids=lambda p: p.name)
def test_real_samples_parse_into_sane_numbers(binary, image):
    eng = AkaiEngine(binary)
    eng.open([image])
    checked = 0
    try:
        disk = eng.list_disks()[0]
        for part in eng.list_partitions(disk):
            for vol in eng.list_volumes(part)[:3]:
                for number, info in eng.volume_sample_infos(vol).items():
                    checked += 1
                    assert info.frames > 0 and 1000 <= info.rate <= 96000, (vol.path, number, info)
                    assert 0 <= info.start <= info.end <= info.frames, (vol.path, number, info)
                    for loop in info.loops:  # a loop lies inside the sample
                        assert 0 <= loop.start <= loop.end <= info.frames, (vol.path, number, loop)
    finally:
        eng.close()
    assert checked > 0


def test_a_move_and_a_copy_both_keep_a_files_tags(binary, tmp_path):
    """On a real image: `reni` (a move inside a partition) carries the file's tags, and so does a copy (through a tar import)."""
    import shutil

    from akaiutil_gui import transfer

    image = next((p for p in REAL if "HD5" in p.name), None)
    if image is None:
        pytest.skip("needs the HD5 image")
    work = tmp_path / "copy.img"
    shutil.copy(image, work)
    eng = AkaiEngine(binary)
    eng.open([work])
    eng.set_writable(True)
    try:
        part = eng.list_partitions(eng.list_disks()[0])[0]
        vols = eng.list_volumes(part)
        src, dst = vols[0], vols[2]
        tagged = next((f for f in eng.list_files(src) if f.tags), None)
        if tagged is None:
            pytest.skip("no tagged file in the first volume")
        copied = transfer.transfer_files(eng, [(src, tagged)], dst, move=False)
        copy = next(f for f in eng.list_files(dst) if f.name == copied.done[0])
        assert copied.done and copy.tags == tagged.tags and copy.tag_names == tagged.tag_names  # copies keep them
        moved = transfer.transfer_files(eng, [(src, tagged)], vols[3], move=True)
        assert moved.done == [tagged.name]
        assert next(f for f in eng.list_files(vols[3]) if f.name == tagged.name).tags == tagged.tags  # a move keeps them
    finally:
        eng.close()


def test_a_real_volume_copies_whole_with_its_tags_into_another_partition(binary, tmp_path):
    """On a copy of HD5: every file of a volume arrives in a new volume of another partition, tags and all."""
    import shutil

    image = next((p for p in REAL if "HD5" in p.name), None)
    if image is None:
        pytest.skip("needs the HD5 image")
    work = tmp_path / "copy.img"
    shutil.copy(image, work)
    eng = AkaiEngine(binary)
    eng.open([work])
    eng.set_writable(True)
    try:
        parts = {p.name: p for p in eng.list_partitions(eng.list_disks()[0])}
        src = eng.list_volumes(parts["A"])[0]
        before = eng.list_files(src)
        copy = eng.copy_volume(src, parts["E"], "WHOLE COPY")
        after = eng.list_files(copy.volume)
        assert [(f.name, f.size, f.os_version, f.tags) for f in after] == [(f.name, f.size, f.os_version, f.tags) for f in before]
        assert eng.tag_names(src)[10] == "TAG J" and eng.tag_names(copy.volume).get(10, "") == ""  # partition E has no names for its tags
        assert len(copy.notes) == 1 and "named differently in partition E" in copy.notes[0] and "“TAG J” became “”" in copy.notes[0]
        assert eng.list_files(src) == before
    finally:
        eng.close()


def test_a_real_samples_settings_can_be_edited_and_everything_else_stays(binary, tmp_path):
    """On a copy of HD5: a tagged, looped sample keeps its slot, tags, OS version and audio when its root key and loop change."""
    import dataclasses
    import shutil

    from akaiutil_gui import akai_header

    image = next((p for p in REAL if "HD5" in p.name), None)
    if image is None:
        pytest.skip("needs the HD5 image")
    work = tmp_path / "copy.img"
    shutil.copy(image, work)
    eng = AkaiEngine(binary)
    eng.open([work])
    eng.set_writable(True)
    try:
        part = eng.list_partitions(eng.list_disks()[0])[0]
        vol = eng.list_volumes(part)[0]
        sine = next(f for f in eng.list_files(vol) if f.name == "SINE.S3")
        before_all = [(f.index, f.name, f.size, f.os_version, f.tags) for f in eng.list_files(vol)]
        s = eng.read_sample_settings(vol, sine)
        loops = list(s.loops)
        loops[0] = dataclasses.replace(loops[0], end=loops[0].end + 10)
        after = eng.edit_sample_settings(vol, sine, dataclasses.replace(s, root=61, loops=tuple(loops)))
        assert [(f.index, f.name, f.size, f.os_version, f.tags) for f in eng.list_files(vol)] == before_all
        got = eng.read_sample_settings(vol, after)
        assert got.root == 61 and got.loops[0].end == s.loops[0].end + 10
        assert after.tags == sine.tags and after.start_block != "" 
    finally:
        eng.close()
