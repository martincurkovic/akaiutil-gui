"""Which files sit on which blocks of a floppy: checked against what akaiutil itself says about real floppies."""

from pathlib import Path

import pytest

import imagegen
from akaiutil_gui import floppy_map
from akaiutil_gui.engine.api import AkaiEngine

BLOCK = floppy_map.BLOCK


def disk_files(binary, image):
    eng = AkaiEngine(binary)
    try:
        eng.open([image])
        usage = eng.disk_usage(eng.list_disks()[0])
        return [floppy_map.DiskFile(e.file.name, int(e.file.start_block, 16), e.file.size) for e in usage.catalog]
    finally:
        eng.close()


@pytest.fixture(scope="module")
def s3000_floppy(binary, images, tmp_path_factory):
    folder = tmp_path_factory.mktemp("s3k")
    path = folder / "s3000_floppy.img"
    path.write_bytes(bytes(1024 * 1600))
    wavs = Path(images["fl1"]).parent / "wavs"
    imagegen._script(binary, path, ["formatfloppyh3"], wavs)
    imagegen._script(binary, path, ["cd /disk0/A", "cdi 1", "wav2sample3 KICK.wav", "wav2sample3 SNARE.wav"], wavs)
    return path


@pytest.mark.parametrize("key", ["fl1", "fl9"])
def test_the_chains_of_a_floppy_match_the_files_akaiutil_lists(binary, images, key):
    image = Path(images[key]).read_bytes()
    files = disk_files(binary, images[key])
    assert files
    header, seen = floppy_map.header_blocks(image), set()
    for f in files:
        blocks = floppy_map.chain(image, f.start_block, f.size)
        assert len(blocks) == -(-f.size // BLOCK) and blocks[0] == f.start_block  # (as many blocks as the size needs)
        assert not set(blocks) & header and not set(blocks) & seen  # (not in the header, and no block shared)
        seen |= set(blocks)


def test_an_s3000_floppy_keeps_its_directory_behind_the_header(binary, s3000_floppy):
    image = s3000_floppy.read_bytes()
    assert floppy_map.header_blocks(image) == set(range(5 + 12))
    files = disk_files(binary, s3000_floppy)
    assert len(files) == 2
    for f in files:
        assert len(floppy_map.chain(image, f.start_block, f.size)) == -(-f.size // BLOCK)


def test_the_other_floppies_headers_are_four_or_five_blocks(images):
    assert floppy_map.header_blocks(Path(images["fl9"]).read_bytes()) == set(range(4))
    assert floppy_map.header_blocks(Path(images["fl1"]).read_bytes()) == set(range(5))


def test_a_damaged_block_names_the_file_it_belongs_to(binary, images):
    image = Path(images["fl1"]).read_bytes()
    files = disk_files(binary, images["fl1"])
    first, second = files[0], files[1]
    middle = floppy_map.chain(image, first.start_block, first.size)[3]
    hit = floppy_map.damage(image, {middle}, files)
    assert hit.files == [first.name] and not hit.header
    both = floppy_map.damage(image, {middle, second.start_block}, files)
    assert both.files == [first.name, second.name]
    assert floppy_map.damage(image, {1}, files).header  # (a block in the directory or chain table)
    assert floppy_map.damage(image, set(), files) == floppy_map.Damage([], False)


def test_a_sector_is_a_block(images):
    assert floppy_map.block_of(0, 0, 1, 10) == 0 and floppy_map.block_of(0, 1, 1, 10) == 10
    assert floppy_map.block_of(79, 1, 10, 10) == 1599 and floppy_map.block_of(79, 1, 5, 5) == 799


def test_a_broken_chain_table_cannot_loop_forever():
    image = bytearray(1024 * 800)
    for b in range(10, 20):  # every block points at the next, and the last back at the first
        image[floppy_map._FAT_OFFSET + 2 * b : floppy_map._FAT_OFFSET + 2 * b + 2] = (10 + (b - 9) % 10).to_bytes(2, "little")
    blocks = floppy_map.chain(bytes(image), 10, 100 * 1024)
    assert len(blocks) == 100 and blocks[0] == 10


@pytest.mark.parametrize("name", ["Alpha_DX.HFE"])
def test_a_real_sampler_disk_chains_like_akaiutil_says(binary, tmp_path, name):
    from akaiutil_gui import hfe

    source = Path(__file__).resolve().parent.parent / "testing_images" / name
    if not source.exists():
        pytest.skip("no real HFE disk in testing_images")
    image = hfe.read(source).image
    flat = tmp_path / "real.img"
    flat.write_bytes(image)
    files = disk_files(binary, flat)
    assert len(files) == 4
    for f in files:
        assert len(floppy_map.chain(image, f.start_block, f.size)) == -(-f.size // BLOCK)
