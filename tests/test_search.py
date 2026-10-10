"""Searching the files of every open disk: the filter, and the table showing results."""

import pytest

from akaiutil_gui import search
from akaiutil_gui.engine.models import CatalogEntry, FileEntry, Volume
from akaiutil_gui.ui.file_table import LOCATION, FileTable


def vol(disk, part, index, name):
    return Volume(disk, part, index, name, "-", "0x0001", "17.00", "S3000")


def entry(name, volume, number=1, size=1000):
    return CatalogEntry(volume, FileEntry(number, name, size, "0x0001", "17.00"))


DRUMS, BASS, KEYS = vol(0, "A", 1, "DRUMS"), vol(0, "B", 1, "BASS LINES"), vol(1, "A", 1, "KEYS")
CATALOG = [
    entry("KICK.S3", DRUMS, 1), entry("SNARE.S3", DRUMS, 2), entry("KICK DEEP.S3", BASS, 1),
    entry("BASS 1.S3", BASS, 2), entry("MY PROGRAM.P3", KEYS, 1), entry("EFFECTS.X", KEYS, 2), entry("LEAD.S1", KEYS, 3),
]


def names(results):
    return [e.file.name for e in results]


def test_a_word_matches_anywhere_in_the_name_ignoring_case():
    results, truncated = search.filter_catalog(CATALOG, "kick")
    assert names(results) == ["KICK.S3", "KICK DEEP.S3"] and not truncated
    assert names(search.filter_catalog(CATALOG, "KiCk")[0]) == names(results)
    assert names(search.filter_catalog(CATALOG, "ick.s")[0]) == ["KICK.S3"]  # a fragment, not a prefix


def test_every_word_must_match_in_any_order():
    assert names(search.filter_catalog(CATALOG, "deep kick")[0]) == ["KICK DEEP.S3"]
    assert search.filter_catalog(CATALOG, "kick snare")[0] == []


def test_the_volumes_name_counts_too():
    assert names(search.filter_catalog(CATALOG, "kick drums")[0]) == ["KICK.S3"]  # KICK in volume DRUMS
    assert names(search.filter_catalog(CATALOG, "keys")[0]) == ["MY PROGRAM.P3", "EFFECTS.X", "LEAD.S1"]  # everything in volume KEYS


def test_a_blank_query_matches_everything_of_the_kind_asked_for():
    assert len(search.filter_catalog(CATALOG, "")[0]) == len(CATALOG)
    assert len(search.filter_catalog(CATALOG, "   ")[0]) == len(CATALOG)
    assert names(search.filter_catalog(CATALOG, "", search.SAMPLES)[0]) == ["KICK.S3", "SNARE.S3", "KICK DEEP.S3", "BASS 1.S3", "LEAD.S1"]
    assert names(search.filter_catalog(CATALOG, "", search.PROGRAMS)[0]) == ["MY PROGRAM.P3"]
    assert names(search.filter_catalog(CATALOG, "", search.OTHER)[0]) == ["EFFECTS.X"]


def test_the_kind_filter_combines_with_the_query():
    assert names(search.filter_catalog(CATALOG, "kick", search.PROGRAMS)[0]) == []
    # "k" is in KICK, KICK DEEP and (through its volume, KEYS) LEAD.S1; the program in KEYS is filtered out by kind
    assert names(search.filter_catalog(CATALOG, "k", search.SAMPLES)[0]) == ["KICK.S3", "KICK DEEP.S3", "LEAD.S1"]


def test_an_over_long_result_list_is_cut_and_says_so():
    big = [entry(f"S{i}.S3", DRUMS, i) for i in range(50)]
    results, truncated = search.filter_catalog(big, "s", limit=10)
    assert len(results) == 10 and truncated
    results, truncated = search.filter_catalog(big, "s", limit=50)
    assert len(results) == 50 and not truncated  # exactly at the limit isn't "more"


def test_nothing_matching_is_an_empty_list_not_an_error():
    assert search.filter_catalog(CATALOG, "zzz") == ([], False)
    assert search.filter_catalog([], "kick") == ([], False)


def test_the_filter_is_fast_on_a_large_disk():
    import time

    big = [entry(f"SAMPLE {i:06d}.S3", DRUMS, i % 500) for i in range(200_000)]
    started = time.perf_counter()
    search.filter_catalog(big, "sample 1999")
    assert time.perf_counter() - started < 1.0


# ------------------------------------------------------------------- the table
@pytest.fixture
def table(qt_app):
    t = FileTable()
    t.resize(900, 300)
    t.show()
    return t


def test_a_volumes_files_hide_the_location_column(table):
    table.set_files([e.file for e in CATALOG[:2]], DRUMS)
    assert table.isColumnHidden(LOCATION) and table.rowCount() == 2
    table.selectAll()
    assert [v.name for v, _ in table.selected_rows()] == ["DRUMS", "DRUMS"]


def test_results_show_where_each_file_lives(table):
    table.set_results(CATALOG[:3])
    assert not table.isColumnHidden(LOCATION)
    found = {table.item(r, 1).text(): table.item(r, LOCATION).text() for r in range(table.rowCount())}
    assert found == {"KICK.S3": "disk0 / A / DRUMS", "SNARE.S3": "disk0 / A / DRUMS", "KICK DEEP.S3": "disk0 / B / BASS LINES"}
    table.set_files([], None)
    assert table.isColumnHidden(LOCATION)  # back to a plain volume view


def test_results_are_sorted_by_name_and_selection_knows_each_rows_volume(table):
    table.set_results(CATALOG[:3])
    assert [table.item(r, 1).text() for r in range(3)] == ["KICK DEEP.S3", "KICK.S3", "SNARE.S3"]
    table.selectAll()
    rows = table.selected_rows()
    assert {(v.name, f.name) for v, f in rows} == {("DRUMS", "KICK.S3"), ("DRUMS", "SNARE.S3"), ("BASS LINES", "KICK DEEP.S3")}
    keys = [(v.disk, v.partition, v.index, f.index) for v, f in rows]
    assert keys == sorted(keys)  # reported in disk order, whatever order the table is sorted in


def test_select_file_tells_same_numbered_files_in_different_volumes_apart(table):
    a, b = vol(0, "A", 1, "ONE"), vol(0, "A", 2, "TWO")
    table.set_results([entry("X.S3", a, 1), entry("Y.S3", b, 1)])  # both are file number 1
    assert table.select_file(b, 1)
    (v, f), = table.selected_rows()
    assert (v.name, f.name) == ("TWO", "Y.S3")
    assert not table.select_file(b, 99)  # not in the table


def test_clearing_results_clears_the_selection_signal(table):
    seen = []
    table.selectionFilesChanged.connect(seen.append)
    table.set_results(CATALOG[:2])
    assert seen[-1] == []
