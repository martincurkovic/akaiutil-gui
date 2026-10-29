"""Repairing damaged HFE disks: damage is put into a good disk on purpose, so every kind can be checked against the original."""

import random
from pathlib import Path

import pytest

from akaiutil_gui import hfe, hfe_work

SECTOR = hfe.SECTOR_SIZE
HD_BYTES = hfe.IMAGE_BYTES[hfe.HD_SECTORS]


@pytest.fixture(scope="module")
def image():
    return random.Random(5).randbytes(HD_BYTES)


@pytest.fixture(scope="module")
def good(image):
    return hfe.encode(image)


def damage(data: bytes, track: int, side: int, change) -> bytes:
    """`data` with `change(cells, field_starts)` applied to one side of one track (it returns the new cells)."""
    tr = hfe._tracks(data)[track]
    cells = hfe._side_cells(data, tr, side)
    found, _ = hfe._find_sectors(cells)
    new = change(cells, {r: start for r, (_, start) in found.items()})
    assert len(new) == len(cells)
    out = bytearray(data)
    hfe._store_side(out, tr, side, new)
    return bytes(out)


def flip_cell(at):
    return lambda cells, starts: cells[:at(starts)] + ("1" if cells[at(starts)] == "0" else "0") + cells[at(starts) + 1 :]


def lose_cell(sector, bit):
    """The drive lost one cell in the middle of a sector: everything after it moves up, and the track ends one cell short."""
    def change(cells, starts):
        at = starts[sector] + 2 * bit + 1
        return cells[:at] + cells[at + 1 :] + cells[-1]
    return change


def extra_cell(sector, bit, value="0"):
    def change(cells, starts):
        at = starts[sector] + 2 * bit + 1
        return cells[:at] + value + cells[at:-1]
    return change


def check_sector_is_back(data, repaired, image, track, side, sector):
    n = (track * 2 + side) * 10 + sector - 1
    assert hfe.decode(repaired).image[n * SECTOR : (n + 1) * SECTOR] == image[n * SECTOR : (n + 1) * SECTOR]


def test_a_good_disk_needs_nothing(good):
    result = hfe.repair(good)
    assert result.fixes == [] and result.data == good


def test_a_flipped_cell_is_put_back_and_checked_twice(good, image):
    broken = damage(good, 7, 1, flip_cell(lambda s: s[4] + 2 * 3000 + 1))
    assert hfe.decode(broken, strict=False).missing == [(7, 1, 4)]
    result = hfe.repair(broken)
    assert [(f.track, f.side, f.sector, f.result, f.how) for f in result.fixes] == [(7, 1, 4, hfe.FIXED, "bit")]
    assert hfe.decode(result.data).image == image  # (strict: everything reads again, and it is the original data)


def test_a_wrong_bit_that_looks_valid_is_fixed_but_only_probably(good, image):
    def wrong_bit(cells, starts):
        start = starts[2]
        body = bytearray(hfe._read_bytes(cells, start, SECTOR + 2))
        crc = bytes(body[SECTOR:])
        body[500] ^= 0x10
        run = hfe._Cells()
        run.last = 1
        run.add(bytes(body[:SECTOR]) + crc)  # valid MFM of wrong data, with the old (now wrong) checksum
        return cells[:start] + run.text() + cells[start + len(run.text()) :]

    broken = damage(good, 20, 0, wrong_bit)
    assert hfe._violations(hfe._side_cells(broken, hfe._tracks(broken)[20], 0), damage.__defaults__ or 0) is not None  # (just runs)
    result = hfe.repair(broken)
    assert [(f.result, f.how) for f in result.fixes] == [(hfe.FIXED_PROBABLY, "bit (checksum only)")]
    assert hfe.decode(result.data).image == image


@pytest.mark.parametrize("make", [lambda: lose_cell(6, 3000), lambda: extra_cell(6, 3000, "0"), lambda: extra_cell(6, 3000, "1"), lambda: lose_cell(6, 40)])
def test_a_lost_or_extra_cell_is_found_and_the_sector_realigned(good, image, make):
    broken = damage(good, 31, 0, make())
    problems = hfe.decode(broken, strict=False).problems
    assert any("sector 6" in p for p in problems)
    result = hfe.repair(broken)
    fixed = [(f.sector, f.result, f.how) for f in result.fixes]
    assert fixed == [(6, hfe.FIXED, "realigned")]
    assert hfe.decode(result.data).image == image
    assert len(result.data) == len(good)


def test_several_problems_on_one_track_are_all_fixed(good, image):
    broken = damage(good, 12, 1, lose_cell(2, 1500))
    broken = damage(broken, 12, 1, flip_cell(lambda s: s[8] + 2 * 4000 + 1))
    broken = damage(broken, 12, 1, extra_cell(9, 5000))
    assert len(hfe.decode(broken, strict=False).missing) == 3
    result = hfe.repair(broken)
    assert sorted(f.sector for f in result.fixes) == [2, 8, 9] and all(f.result == hfe.FIXED for f in result.fixes)
    assert hfe.decode(result.data).image == image


def test_heavy_damage_is_left_alone_unless_asked_to_rewrite_it(good, image):
    def garbage(cells, starts):
        start = starts[3] + 2 * 2000
        noise = "".join(random.Random(1).choice("01") for _ in range(600))
        return cells[:start] + noise + cells[start + 600 :]

    broken = damage(good, 40, 0, garbage)
    left = hfe.repair(broken)
    assert [(f.sector, f.result) for f in left.fixes] == [(3, hfe.LOST)] and left.data == broken
    assert hfe.decode(left.data, strict=False).missing == [(40, 0, 3)]

    rewritten = hfe.repair(broken, rewrite=True)
    assert [(f.sector, f.result) for f in rewritten.fixes] == [(3, hfe.REWRITTEN)]
    decoded = hfe.decode(rewritten.data)  # (reads without an error now...)
    n = (40 * 2) * 10 + 2
    assert decoded.image[n * SECTOR : (n + 1) * SECTOR] != image[n * SECTOR : (n + 1) * SECTOR]  # (...but holds the damaged data)
    assert decoded.image[: n * SECTOR] == image[: n * SECTOR]


def test_a_damaged_header_cant_be_repaired_even_if_asked(good):
    def kill_header(cells, starts):
        at = cells.find(hfe._SYNC_A1 * 3, 2000) + 48 + 16 * 6  # the checksum of the first sector header found past the start
        return cells[:at] + "".join("1" if c == "0" else "0" for c in cells[at : at + 16]) + cells[at + 16 :]

    broken = damage(good, 5, 0, kill_header)
    missing = hfe.decode(broken, strict=False).missing
    assert len(missing) == 1
    for rewrite in (False, True):
        result = hfe.repair(broken, rewrite=rewrite)
        assert [(f.track, f.side, f.sector, f.result) for f in result.fixes] == [(*missing[0], hfe.LOST)]
        assert hfe.decode(result.data, strict=False).missing == missing


def test_everything_else_in_the_file_is_left_as_it_was(good, image):
    broken = damage(good, 31, 0, lose_cell(6, 3000))
    result = hfe.repair(broken)
    tracks = hfe._tracks(good)
    for t in range(80):
        for side in (0, 1):
            if (t, side) == (31, 0):
                continue
            assert hfe._side_cells(result.data, tracks[t], side) == hfe._side_cells(good, tracks[t], side)
    assert result.data[:1024] == good[:1024]


# ---------------------------------------------------------------------------------------------------- saving a copy
def test_save_repair_writes_a_checked_copy_and_refuses_a_bad_one(tmp_path, good, image):
    broken = damage(good, 31, 0, lose_cell(6, 3000))
    result = hfe.repair(broken)
    path = tmp_path / "fixed.hfe"
    hfe.save_repair(path, broken, result)
    assert hfe.read(path).image == image

    altered = bytearray(result.data)  # a "repair" that changed something it never mentioned
    tr = hfe._tracks(good)[3]
    found, _ = hfe._find_sectors(hfe._side_cells(bytes(altered), tr, 0))
    byte = found[1][1] // 8 + 30
    altered[tr.offset + (byte // 256) * 512 + byte % 256] ^= 0x02
    other = tmp_path / "other.hfe"
    with pytest.raises(hfe.HfeError, match="nothing was written"):
        hfe.save_repair(other, broken, hfe.Repair(bytes(altered), result.fixes))
    assert not other.exists() and [p.name for p in tmp_path.iterdir()] == ["fixed.hfe"]


def test_the_workspace_saves_a_repaired_copy_and_never_touches_the_original(tmp_path, good, image):
    broken = damage(good, 31, 0, lose_cell(6, 3000))
    source = tmp_path / "BROKEN.hfe"
    source.write_bytes(broken)
    work = hfe_work.Workspace(tmp_path / "work")
    copy = tmp_path / "REPAIRED.hfe"
    result = work.repair_copy(source, copy)
    assert [f.result for f in result.fixes] == [hfe.FIXED]
    assert source.read_bytes() == broken and hfe.read(copy).image == image
    with pytest.raises(hfe.HfeError, match="new file"):
        work.repair_copy(source, source)
    assert source.read_bytes() == broken


def test_damage_in_random_places_is_put_right(good, image):
    """A lost or extra cell, or a flipped one, anywhere in a sector's data or checksum: the original comes back every time."""
    rng = random.Random(99)
    for _ in range(14):
        track, side, sector, bit = rng.randrange(80), rng.randrange(2), rng.randrange(1, 11), rng.randrange(0, 1026 * 8 - 8)
        kind = rng.choice(["lose", "extra0", "extra1", "flip"])
        change = {
            "lose": lose_cell(sector, bit), "extra0": extra_cell(sector, bit, "0"), "extra1": extra_cell(sector, bit, "1"),
            "flip": flip_cell(lambda s: s[sector] + 2 * bit + 1),
        }[kind]
        broken = damage(good, track, side, change)
        if not hfe.decode(broken, strict=False).missing:
            continue  # (the change fell in a place that doesn't matter)
        result = hfe.repair(broken)
        assert hfe.decode(result.data).image == image, (track, side, sector, bit, kind)
        assert {f.result for f in result.fixes} <= {hfe.FIXED, hfe.FIXED_PROBABLY}, (track, side, sector, bit, kind, result.fixes)
