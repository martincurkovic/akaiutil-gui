"""HFE floppy images: decoding to a flat image akaiutil opens, patching edits back in, making a new file."""

import os
import random
import struct
from pathlib import Path

import pytest

from akaiutil_gui import hfe

HD_BYTES = hfe.IMAGE_BYTES[hfe.HD_SECTORS]
SECTOR = hfe.SECTOR_SIZE


@pytest.fixture(scope="module")
def image():
    return random.Random(7).randbytes(HD_BYTES)


@pytest.fixture(scope="module")
def hfe_bytes(image):
    return hfe.encode(image)


def _sector_position(data: bytes, track: int, side: int, sector: int) -> int:
    """Where in the file a sector's data starts (its first cell's byte), for tests that damage a file."""
    t = hfe._tracks(data)[track]
    found, _ = hfe._find_sectors(hfe._side_cells(data, t, side))
    cell = found[sector][1]
    byte = cell // 8
    return t.offset + (byte // 256) * 512 + side * 256 + byte % 256


# ---------------------------------------------------------------------------------------------------------- new files
def test_a_new_file_decodes_to_the_image_it_was_made_from(image, hfe_bytes):
    decoded = hfe.decode(hfe_bytes)
    assert decoded.image == image and decoded.problems == []
    assert decoded.sectors_per_track == 10 and decoded.density == "HD"


def test_a_new_file_has_the_layout_of_the_real_ones(hfe_bytes):
    assert hfe_bytes[:8] == b"HXCPICFE" and len(hfe_bytes) == 4_056_064
    _, rev, cylinders, sides, encoding, bitrate, _, mode, _, table, _, _ = struct.unpack_from("<8sBBBBHHBBHBB", hfe_bytes, 0)
    assert (rev, cylinders, sides, encoding, bitrate, mode, table) == (0, 80, 2, 0, 500, 1, 1)
    assert {struct.unpack_from("<HH", hfe_bytes, 512 + 4 * t)[1] for t in range(80)} == {50336}


def test_new_sectors_are_in_the_rotated_order_real_disks_use(hfe_bytes):
    firsts = []
    for t in range(4):
        found, _ = hfe._find_sectors(hfe._side_cells(hfe_bytes, hfe._tracks(hfe_bytes)[t], 0))
        firsts.append(sorted(found.items(), key=lambda item: item[1][1])[0][0])
    assert firsts == [1, 6, 1, 6]


def test_an_odd_sized_image_is_not_encoded():
    with pytest.raises(hfe.HfeError, match="bytes"):
        hfe.encode(bytes(1000))


# -------------------------------------------------------------------------------------------------------- patching
def test_patching_with_an_unchanged_image_gives_back_the_same_file(image, hfe_bytes):
    assert hfe.patch(hfe_bytes, image) == hfe_bytes


def test_patching_changes_only_the_sectors_that_differ(image, hfe_bytes):
    changed = bytearray(image)
    for n in (0, 683, 1599):  # first, a middle and the last sector
        changed[n * SECTOR : (n + 1) * SECTOR] = bytes((n + k * 7) & 0xFF for k in range(SECTOR))
    patched = hfe.patch(hfe_bytes, bytes(changed))
    assert hfe.decode(patched).image == bytes(changed)
    assert patched[:1024] == hfe_bytes[:1024]  # header and track table
    assert len(patched) == len(hfe_bytes)
    # a sector's 1024 bytes and its checksum are 2052 bytes of cells, so only a few thousand file bytes can differ
    assert 0 < sum(a != b for a, b in zip(patched, hfe_bytes)) < 3 * 2200


def test_a_change_that_ends_in_a_one_bit_still_reads_back(image, hfe_bytes):
    """The clock cell after a sector's checksum depends on its last bit, so try both."""
    for fill in (0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80):
        changed = bytearray(image)
        changed[5 * SECTOR : 6 * SECTOR] = bytes([fill]) * SECTOR
        assert hfe.decode(hfe.patch(hfe_bytes, bytes(changed))).image == bytes(changed)


def test_patching_an_image_of_the_wrong_size_is_refused(hfe_bytes):
    with pytest.raises(hfe.HfeError, match="bytes"):
        hfe.patch(hfe_bytes, bytes(1000))


# ------------------------------------------------------------------------------------------------- finding sectors
def test_sectors_are_found_when_the_whole_track_is_off_the_byte_grid(image):
    """Real disks have data fields rewritten at another bit phase, so syncs must be searched for in cells."""
    cells = hfe._new_side(image, 3, 1, hfe._HD)
    found, problems = hfe._find_sectors(cells)
    shifted, problems_shifted = hfe._find_sectors("010101" + cells)
    assert not problems and not problems_shifted
    assert {r: body for r, (body, _) in shifted.items()} == {r: body for r, (body, _) in found.items()} and len(found) == 10


def test_a_low_density_track_with_five_sectors_decodes():
    """(Only checks the sector count logic: no real low-density file has been seen.)"""
    image = random.Random(3).randbytes(hfe.IMAGE_BYTES[hfe.DD_SECTORS])
    run = hfe._Cells()
    run.add(b"\x4e" * 80 + b"\x00" * 12)
    for r in range(1, 6):
        head = bytes([0, 0, r, 3])
        run.add(b"\x00" * 12)
        run.sync(hfe._SYNC_A1)
        run.add(b"\xfe" + head + hfe._crc(b"\xa1\xa1\xa1\xfe" + head).to_bytes(2, "big") + b"\x4e" * 22 + b"\x00" * 12)
        run.sync(hfe._SYNC_A1)
        data = image[(r - 1) * SECTOR : r * SECTOR]
        run.add(b"\xfb" + data + hfe._crc(b"\xa1\xa1\xa1\xfb" + data).to_bytes(2, "big") + b"\x4e" * 40)
    found, problems = hfe._find_sectors(run.text())
    assert sorted(found) == [1, 2, 3, 4, 5] and not problems


# ----------------------------------------------------------------------------------------------------------- low density
@pytest.fixture(scope="module")
def dd_image():
    return random.Random(21).randbytes(hfe.IMAGE_BYTES[hfe.DD_SECTORS])


@pytest.fixture(scope="module")
def dd_bytes(dd_image):
    return hfe.encode(dd_image)


def test_a_five_sector_disk_decodes_patches_and_reads_back(dd_image, dd_bytes):
    decoded = hfe.decode(dd_bytes)
    assert decoded.image == dd_image and decoded.sectors_per_track == 5 and decoded.density == "DD" and not decoded.problems
    assert hfe.patch(dd_bytes, dd_image) == dd_bytes
    changed = bytearray(dd_image)
    for n in (0, 400, 799):
        changed[n * SECTOR : (n + 1) * SECTOR] = bytes((n + k) & 0xFF for k in range(SECTOR))
    patched = hfe.patch(dd_bytes, bytes(changed))
    assert hfe.decode(patched).image == bytes(changed) and len(patched) == len(dd_bytes)


def test_a_new_low_density_file_has_the_layout_hxc_gives_an_s900_disk(dd_bytes):
    assert dd_bytes[:8] == b"HXCPICFE" and len(dd_bytes) == 2_008_064
    _, rev, cylinders, sides, encoding, bitrate, _, mode, _, table, _, _ = struct.unpack_from("<8sBBBBHHBBHBB", dd_bytes, 0)
    assert (rev, cylinders, sides, encoding, bitrate, mode, table) == (0, 80, 2, 0, 250, 12, 1)
    assert {struct.unpack_from("<HH", dd_bytes, 512 + 4 * t)[1] for t in range(80)} == {25000}
    for t in range(3):  # sectors in order on every track
        found, _ = hfe._find_sectors(hfe._side_cells(dd_bytes, hfe._tracks(dd_bytes)[t], 1))
        assert [r for r, _ in sorted(found.items(), key=lambda item: item[1][1])] == [1, 2, 3, 4, 5]


# ----------------------------------------------------------------------------------------------- refusing bad files
def test_garbage_and_other_files_are_refused():
    with pytest.raises(hfe.HfeError, match="Not an HFE"):
        hfe.decode(b"%PDF-1.4" + bytes(5000))
    with pytest.raises(hfe.HfeError, match="cut short"):
        hfe.decode(b"HXCPICFE" + bytes(100))


def test_a_v3_file_says_so():
    with pytest.raises(hfe.HfeError, match="v3"):
        hfe.decode(b"HXCHFEV3" + bytes(5000))


def test_a_file_that_is_not_80_tracks_of_two_sides_mfm_is_refused(hfe_bytes):
    for offset, value, text in ((9, 40, "40 tracks"), (10, 1, "1 side"), (11, 2, "MFM")):
        broken = bytearray(hfe_bytes)
        broken[offset] = value
        with pytest.raises(hfe.HfeError, match=text):
            hfe.decode(bytes(broken))


def test_a_truncated_file_is_refused(hfe_bytes):
    with pytest.raises(hfe.HfeError, match="cut short"):
        hfe.decode(hfe_bytes[: len(hfe_bytes) // 2])


def test_a_damaged_sector_is_named_and_refused_unless_asked_for(image, hfe_bytes):
    broken = bytearray(hfe_bytes)
    broken[_sector_position(hfe_bytes, 12, 1, 7) + 100] ^= 0x08  # (bit 3 of a stored byte is a data cell; the even ones are clock)
    with pytest.raises(hfe.HfeError, match=r"track 12 side 1: sector 7 with a bad checksum"):
        hfe.decode(bytes(broken))
    loose = hfe.decode(bytes(broken), strict=False)
    assert loose.problems and "track 12 side 1" in loose.problems[0]
    assert loose.missing == [(12, 1, 7)]
    assert len(loose.image) == HD_BYTES
    where = ((12 * 2 + 1) * 10 + 6) * SECTOR  # (the zero-filled gap is the damaged sector, the rest is untouched)
    assert loose.image[where : where + SECTOR] == bytes(SECTOR)
    assert loose.image[:where] == image[:where] and loose.image[where + SECTOR :] == image[where + SECTOR :]
    with pytest.raises(hfe.HfeError):
        hfe.patch(bytes(broken), image)


# --------------------------------------------------------------------------------------------------------- on disk
def test_is_hfe_looks_at_the_start_of_the_file(tmp_path, hfe_bytes):
    good, flat = tmp_path / "a.hfe", tmp_path / "a.img"
    good.write_bytes(hfe_bytes)
    flat.write_bytes(bytes(HD_BYTES))
    assert hfe.is_hfe(good) and not hfe.is_hfe(flat) and not hfe.is_hfe(tmp_path / "missing.hfe")
    v3 = tmp_path / "v3.hfe"
    v3.write_bytes(b"HXCHFEV3" + bytes(100))
    assert hfe.is_hfe(v3)


def test_update_patches_the_file_in_place_and_leaves_nothing_behind(tmp_path, image, hfe_bytes):
    path = tmp_path / "disk.hfe"
    path.write_bytes(hfe_bytes)
    changed = bytearray(image)
    changed[100 * SECTOR : 101 * SECTOR] = bytes(SECTOR)
    hfe.update(path, bytes(changed))
    assert hfe.read(path).image == bytes(changed)
    assert path.read_bytes()[:1024] == hfe_bytes[:1024]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["disk.hfe"]


def test_save_without_a_template_makes_a_new_file(tmp_path, image):
    path = tmp_path / "new.hfe"
    hfe.save(path, image)
    assert hfe.read(path).image == image


def test_update_of_a_damaged_file_changes_nothing(tmp_path, image, hfe_bytes):
    broken = bytearray(hfe_bytes)
    broken[_sector_position(hfe_bytes, 0, 0, 1) + 50] ^= 0x02
    path = tmp_path / "bad.hfe"
    path.write_bytes(bytes(broken))
    with pytest.raises(hfe.HfeError):
        hfe.update(path, image)
    assert path.read_bytes() == bytes(broken) and [p.name for p in tmp_path.iterdir()] == ["bad.hfe"]


def test_problem_judges_a_file_by_its_header(tmp_path, hfe_bytes):
    ok, v3, forty, text = tmp_path / "ok.hfe", tmp_path / "v3.hfe", tmp_path / "forty.hfe", tmp_path / "t.hfe"
    ok.write_bytes(hfe_bytes[:2048])  # (the header and track list are enough)
    v3.write_bytes(b"HXCHFEV3" + bytes(2000))
    forty.write_bytes(hfe_bytes[:9] + bytes([40]) + hfe_bytes[10:2048])
    text.write_bytes(b"hello" * 500)
    assert hfe.problem(ok) is None and "v3" in hfe.problem(v3) and "40 tracks" in hfe.problem(forty)
    assert hfe.problem(text) == "Not an HFE file." and hfe.problem(tmp_path / "missing.hfe")


def test_the_open_dialog_check_lets_a_good_hfe_through_and_names_what_is_wrong_with_the_rest(tmp_path, hfe_bytes):
    from akaiutil_gui import importing

    good, v3 = tmp_path / "good.hfe", tmp_path / "v3.hfe"
    good.write_bytes(hfe_bytes)
    v3.write_bytes(b"HXCHFEV3" + bytes(2_000_000))
    assert importing.disk_image_problem(good) is None
    assert "v3" in importing.disk_image_problem(v3)


def test_save_with_a_template_keeps_its_layout_for_a_different_disk(tmp_path, image, hfe_bytes):
    other = random.Random(9).randbytes(HD_BYTES)
    path = tmp_path / "copy.hfe"
    hfe.save(path, other, template=hfe_bytes)
    assert hfe.read(path).image == other and path.read_bytes()[:1024] == hfe_bytes[:1024]


def test_read_says_what_is_wrong_with_a_missing_file(tmp_path):
    with pytest.raises(hfe.HfeError, match="gone.hfe"):
        hfe.read(tmp_path / "gone.hfe")


# ------------------------------------------------------------------------------------------------- with akaiutil
def test_akaiutil_reads_a_decoded_floppy_and_an_edit_survives_the_round_trip(binary, images, tmp_path):
    """A formatted S1000 floppy goes into an HFE and out again; an edit made by akaiutil is patched back in."""
    import imagegen
    from akaiutil_gui.engine.api import AkaiEngine

    flat = Path(images["fl1"]).read_bytes()
    original = hfe.encode(flat)
    working = tmp_path / "working.img"
    working.write_bytes(hfe.decode(original).image)

    def names(path):
        eng = AkaiEngine(binary)
        try:
            eng.open([path])
            disk = eng.list_disks()[0]
            return disk.type, disk.partitions
        finally:
            eng.close()

    assert names(working) == names(images["fl1"]) and names(working)[0] == "FLH"
    imagegen._script(binary, working, ["cd /disk0/A", "cdi 1", "reni 1 RENAMED.S1"], tmp_path)
    edited = working.read_bytes()
    assert edited != flat
    patched = hfe.patch(original, edited)
    assert hfe.decode(patched).image == edited
    back = tmp_path / "back.img"
    back.write_bytes(hfe.decode(patched).image)
    assert back.read_bytes() == edited and names(back) == names(working)


# ----------------------------------------------------------------------------------------------- real HFE files
IMAGE_DIR = Path(os.environ.get("AKAIUTIL_TEST_IMAGES") or Path(__file__).resolve().parent.parent / "testing_images")
REAL = sorted(IMAGE_DIR.glob("*.hfe")) + sorted(IMAGE_DIR.glob("*.HFE"))


@pytest.mark.skipif(not REAL, reason=f"no real .hfe files in {IMAGE_DIR} (git-ignored; see dev_docs/hfe-plan.md)")
@pytest.mark.parametrize("path", REAL, ids=lambda p: p.name)
def test_real_hfe_files_decode_cleanly_and_patch_back_to_themselves(path):
    data = path.read_bytes()
    decoded = hfe.decode(data)
    assert decoded.problems == [] and len(decoded.image) == hfe.IMAGE_BYTES[decoded.sectors_per_track]
    assert hfe.patch(data, decoded.image) == data
    changed = bytearray(decoded.image)
    changed[SECTOR * 40 : SECTOR * 41] = bytes(range(256)) * 4
    assert hfe.decode(hfe.patch(data, bytes(changed))).image == bytes(changed)


def _header(data: bytes) -> tuple:
    _, rev, cylinders, sides, encoding, bitrate, _, mode, _, _, _, _ = struct.unpack_from("<8sBBBBHHBBHBB", data, 0)
    return bitrate, mode, struct.unpack_from("<H", data, 512 + 2)[0]  # (bit rate, interface mode, track length)


@pytest.mark.skipif(not REAL, reason=f"no real .hfe files in {IMAGE_DIR}")
@pytest.mark.parametrize("path", REAL, ids=lambda p: p.name)
def test_a_new_file_is_what_hxc_wrote_when_a_real_file_has_the_same_layout(path):
    """The encoder against software that isn't ours: HxC's own Akai disks (blank S900 low density and S950 high density, and the S900
    operating-system disk, which holds real data) are reproduced byte for byte. Files made another way (a sampler's own disks,
    other generators) are skipped."""
    data = path.read_bytes()
    decoded = hfe.decode(data)
    for layout in (hfe._DD, hfe._HD_HXC, hfe._DD_OS):
        if _header(data) == (layout.bitrate, layout.mode, layout.side_bytes * 2) and decoded.sectors_per_track == layout.sectors:
            assert hfe._encode(decoded.image, layout) == data
            return
    pytest.skip("not a layout this encoder reproduces")
