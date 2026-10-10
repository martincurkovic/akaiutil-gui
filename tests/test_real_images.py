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
