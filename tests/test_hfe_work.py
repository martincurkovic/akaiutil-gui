"""Working copies of HFE images: decoded for akaiutil, written back after edits."""

import os
import random
from pathlib import Path

import pytest

from akaiutil_gui import hfe, hfe_work

HD_BYTES = hfe.IMAGE_BYTES[hfe.HD_SECTORS]
SECTOR = hfe.SECTOR_SIZE


@pytest.fixture(scope="module")
def image():
    return random.Random(11).randbytes(HD_BYTES)


@pytest.fixture
def hfe_file(tmp_path, image):
    path = tmp_path / "disks" / "KICKS.hfe"
    path.parent.mkdir()
    path.write_bytes(hfe.encode(image))
    return path


@pytest.fixture
def work(tmp_path):
    return hfe_work.Workspace(tmp_path / "work")


def edit(working: str, sector: int = 3, fill: int = 0xAB) -> bytes:
    data = bytearray(Path(working).read_bytes())
    data[sector * SECTOR : (sector + 1) * SECTOR] = bytes([fill]) * SECTOR
    Path(working).write_bytes(bytes(data))
    return bytes(data)


def test_other_files_pass_through_untouched(work, tmp_path):
    flat = tmp_path / "plain.img"
    flat.write_bytes(bytes(1000))
    assert work.engine_path(flat) == str(flat) and work.source_for(flat) == str(flat) and work.working_path(flat) == str(flat)
    assert not work.is_hfe(flat) and work.pending() == [] and work.sync() == []


def test_an_hfe_is_decoded_into_a_working_copy_that_stands_for_it(work, hfe_file, image):
    working = work.engine_path(hfe_file)
    assert working != str(hfe_file) and Path(working).suffix == ".img" and Path(working).name == "KICKS.img"
    assert Path(working).read_bytes() == image
    assert work.source_for(working) == str(hfe_file.resolve()) and work.working_path(hfe_file) == working
    assert work.is_hfe(hfe_file) and work.pending() == []


def test_a_second_call_reuses_the_working_copy_and_what_was_done_to_it(work, hfe_file):
    working = work.engine_path(hfe_file)
    edited = edit(working)
    assert work.engine_path(hfe_file) == working and Path(working).read_bytes() == edited  # (not decoded afresh)
    assert work.pending() == [str(hfe_file)]


def test_sync_writes_the_changed_sectors_back_and_nothing_else(work, hfe_file, image):
    before = hfe_file.read_bytes()
    working = work.engine_path(hfe_file)
    assert work.sync() == [] and hfe_file.read_bytes() == before  # nothing changed: the file isn't even rewritten
    edited = edit(working)
    assert work.sync() == [] and work.pending() == []
    assert hfe.read(hfe_file).image == edited and hfe_file.read_bytes()[:1024] == before[:1024]
    assert work.engine_path(hfe_file) == working  # still the same working copy: the file's new time was noted


def test_a_failed_write_back_is_reported_and_kept_for_next_time(work, hfe_file):
    working = work.engine_path(hfe_file)
    edited = edit(working)
    folder = hfe_file.parent
    folder.chmod(0o500)
    try:
        if os.access(folder, os.W_OK):
            pytest.skip("this account can write to read-only folders")
        failures = work.sync()
        assert [path for path, _ in failures] == [str(hfe_file)] and "KICKS.hfe" in failures[0][1]
        assert work.pending() == [str(hfe_file)]
    finally:
        folder.chmod(0o700)
    assert work.sync() == [] and hfe.read(hfe_file).image == edited


def test_release_deletes_working_copies_but_keeps_edits_that_never_went_back(work, hfe_file, tmp_path):
    working = Path(work.engine_path(hfe_file))
    work.release(keep=[hfe_file])
    assert working.exists()
    work.release()
    assert not working.parent.exists() and not work.is_hfe(hfe_file)

    working = Path(work.engine_path(hfe_file))
    edit(str(working))
    work.drop([hfe_file])
    assert working.exists()  # (never written back: not thrown away)


def test_an_hfe_that_changed_on_disk_is_decoded_afresh(work, hfe_file):
    first = work.engine_path(hfe_file)
    other = random.Random(5).randbytes(HD_BYTES)
    hfe_file.write_bytes(hfe.encode(other))
    assert work.engine_path(hfe_file) == first and Path(first).read_bytes() == other


def test_a_damaged_hfe_is_refused_with_the_reason(work, tmp_path):
    bad = tmp_path / "bad.hfe"
    bad.write_bytes(b"HXCHFEV3" + bytes(3000))
    with pytest.raises(hfe.HfeError, match="v3"):
        work.engine_path(bad)
    assert not work.is_hfe(bad)


def test_save_as_keeps_an_hfe_s_layout_and_encodes_a_flat_floppy_afresh(work, hfe_file, image, tmp_path):
    working = work.engine_path(hfe_file)
    edited = edit(working)
    copy = tmp_path / "copy.hfe"
    work.save_as(hfe_file, copy)
    assert hfe.read(copy).image == edited and copy.read_bytes()[:1024] == hfe_file.read_bytes()[:1024]

    flat = tmp_path / "flat.img"
    flat.write_bytes(image)
    fresh = tmp_path / "fresh.hfe"
    work.save_as(flat, fresh)
    assert hfe.read(fresh).image == image

    dd = tmp_path / "dd.img"
    dd.write_bytes(random.Random(2).randbytes(hfe.IMAGE_BYTES[hfe.DD_SECTORS]))
    work.save_as(dd, tmp_path / "dd.hfe")
    assert hfe.read(tmp_path / "dd.hfe").image == dd.read_bytes()

    big = tmp_path / "hd.img"
    big.write_bytes(bytes(8192 * 40))
    with pytest.raises(hfe.HfeError):
        work.save_as(big, tmp_path / "nope.hfe")
    assert not (tmp_path / "nope.hfe").exists()


# ------------------------------------------------------------------------------------------------------ damaged disks
@pytest.fixture
def damaged_file(tmp_path, image):
    data = bytearray(hfe.encode(image))
    t = hfe._tracks(bytes(data))[9]
    found, _ = hfe._find_sectors(hfe._side_cells(bytes(data), t, 0))
    byte = found[4][1] // 8 + 60
    data[t.offset + (byte // 256) * 512 + byte % 256] ^= 0x08  # (a data cell: flipping a clock cell wouldn't be noticed)
    path = tmp_path / "BROKEN.hfe"
    path.write_bytes(bytes(data))
    return path


def test_a_damaged_disk_is_opened_with_its_bad_sectors_blank_and_listed(work, damaged_file, image):
    working = work.engine_path(damaged_file)
    flat = Path(working).read_bytes()
    where = ((9 * 2 + 0) * 10 + 3) * SECTOR
    assert flat[where : where + SECTOR] == bytes(SECTOR)
    assert flat[:where] == image[:where] and flat[where + SECTOR :] == image[where + SECTOR :]
    assert work.damaged() == {str(damaged_file): [(9, 0, 4)]}


def test_a_damaged_disk_is_never_written_back_or_saved_as_another_hfe(work, damaged_file, tmp_path):
    working = work.engine_path(damaged_file)
    before = damaged_file.read_bytes()
    edit(working)  # (the window doesn't allow it, but if something did)
    assert work.pending() == [] and work.sync() == [] and damaged_file.read_bytes() == before
    with pytest.raises(hfe.HfeError, match="couldn't be read"):
        work.save_as(damaged_file, tmp_path / "copy.hfe")
    assert not (tmp_path / "copy.hfe").exists()


def test_a_good_disk_is_not_listed_as_damaged(work, hfe_file):
    work.engine_path(hfe_file)
    assert work.damaged() == {}
