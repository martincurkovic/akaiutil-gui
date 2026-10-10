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


def test_a_move_keeps_a_files_tags_but_a_copy_cant(binary, tmp_path):
    """On a real image: `reni` (a move inside a partition) carries the file's tags; `put` (a copy) doesn't."""
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
        assert copied.done and not next(f for f in eng.list_files(dst) if f.name == copied.done[0]).tags  # copies lose tags
        moved = transfer.transfer_files(eng, [(src, tagged)], vols[3], move=True)
        assert moved.done == [tagged.name]
        assert next(f for f in eng.list_files(vols[3]) if f.name == tagged.name).tags == tagged.tags  # a move keeps them
    finally:
        eng.close()
