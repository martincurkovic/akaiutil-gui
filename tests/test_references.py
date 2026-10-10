"""Which programs play which samples: parsing, the cross-referencing, and what the details and Edit menu make of it."""

import pytest

from akaiutil_gui import describe, references
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import FileEntry, Volume

from test_describe import S3000_PROGRAM
from test_gui import pump


@pytest.fixture
def window(qt_app, binary):
    from akaiutil_gui import settings
    from akaiutil_gui.ui import theme
    from akaiutil_gui.ui.main_window import MainWindow

    theme.apply_to_app(qt_app, settings.THEME_DARK)
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()
    w.deleteLater()


def entry(index: int, name: str) -> FileEntry:
    return FileEntry(index=index, name=name, size=1000, start_block="0x0005", os_version="17.00")


def refs(files, programs) -> references.VolumeReferences:
    return references.VolumeReferences(tuple(files), {k: tuple(v) for k, v in programs.items()})


# ------------------------------------------------------------------ the text
def test_a_programs_sample_names_are_read_from_its_velocity_zones():
    assert references.sample_names(S3000_PROGRAM) == ["SINE"]  # (the empty zones name nothing)


def test_each_name_counts_once_in_order_of_first_use():
    text = '\n'.join(["/disk0/A/V/P.P3", "fnr: 1", "type: S3000 program", "keygroup 1:", "  velzone 1:", '    sname: "B"', "  velzone 2:", '    sname: "A"', "keygroup 2:", "  velzone 1:", '    sname: "B"'])
    assert references.sample_names(text) == ["B", "A"]


def test_s900_layers_are_read_too():
    text = '\n'.join(["/disk0/A/V/P.P9", "fnr: 1", "type: S900 program", "keygroup 1:", "  sample 1 (soft):", '    sname: "SOFT"', "  sample 2 (loud):", '    sname: "LOUD"'])
    assert references.sample_names(text) == ["SOFT", "LOUD"]


def test_a_program_that_names_nothing():
    assert references.sample_names("/disk0/A/V/P.P3\nfnr: 1\ntype: S3000 program\nkgnum: 0") == []


# ------------------------------------------------------------ the cross-referencing
@pytest.fixture
def volume():
    kick, snare, hat, lead, pad = entry(1, "KICK.S3"), entry(2, "SNARE.S3"), entry(3, "HAT.S3"), entry(4, "LEAD.P3"), entry(5, "PAD.P3")
    return kick, snare, hat, lead, pad, refs([kick, snare, hat, lead, pad], {4: ["kick", "GONE"], 5: ["KICK ", "SNARE"]})


def test_which_programs_play_a_sample(volume):
    kick, snare, hat, lead, pad, r = volume
    assert r.programs_using(kick) == [lead, pad]  # case and trailing spaces don't matter
    assert r.programs_using(snare) == [pad]
    assert r.programs_using(hat) == []


def test_what_a_program_plays_and_what_is_missing(volume):
    kick, snare, hat, lead, pad, r = volume
    assert r.samples_of(lead) == ([kick], ["GONE"])
    assert r.samples_of(pad) == ([kick, snare], [])


def test_unused_samples(volume):
    kick, snare, hat, lead, pad, r = volume
    assert r.unused_samples() == [hat]


def test_a_volume_without_programs_has_no_unused_samples():
    kick = entry(1, "KICK.S3")
    r = refs([kick], {})
    assert not r.has_programs and r.unused_samples() == [] and r.programs_using(kick) == []


def test_samples_of_other_types_with_the_same_name_all_count():
    s1, s3 = entry(1, "KICK.S1"), entry(2, "KICK.S3")
    program = entry(3, "P.P3")
    r = refs([s1, s3, program], {3: ["KICK"]})
    assert r.samples_of(program) == ([s1, s3], []) and r.unused_samples() == []


# --------------------------------------------------------------------- the details
def text_of(details: describe.Details) -> str:
    out = []
    for block in details.blocks:
        out += [block.title, *(f"{r.label} {r.value}" for r in block.rows), *(" ".join(row) for row in block.table)]
    return "\n".join(out)


def test_a_sample_says_which_programs_use_it(volume):
    kick, snare, hat, lead, pad, r = volume
    text = text_of(describe.describe_file("/d/KICK.S3\nfnr: 1\ntype: S3000 sample", kick, r))
    assert "Used by" in text and "LEAD.P3" in text and "PAD.P3" in text


def test_an_unused_sample_says_so_only_if_there_are_programs(volume):
    kick, snare, hat, lead, pad, r = volume
    assert "no program in this volume plays this sample" in text_of(describe.describe_file("/d/HAT.S3\nfnr: 3\ntype: S3000 sample", hat, r))
    bare = refs([hat], {})
    assert "no programs" in text_of(describe.describe_file("/d/HAT.S3\nfnr: 3\ntype: S3000 sample", hat, bare))


def test_a_program_lists_its_samples_and_the_missing_ones(volume):
    kick, snare, hat, lead, pad, r = volume
    text = text_of(describe.describe_file(S3000_PROGRAM, lead, r))
    assert "Samples used" in text and "In this volume KICK.S3" in text and "Not in this volume GONE" in text


def test_a_volume_lists_its_unused_samples(volume):
    *_, r = volume
    text = text_of(describe.describe_volume('/disk0/A/V\nvname: "V"\nvnr: 1', None, r))
    assert "Samples not in any program 1" in text and "HAT.S3" in text


def test_nothing_changes_without_references(volume):
    kick = volume[0]
    assert "Used by" not in text_of(describe.describe_file("/d/KICK.S3\nfnr: 1\ntype: S3000 sample", kick, None))
    assert "Programs and samples" not in text_of(describe.describe_volume('/disk0/A/V\nvname: "V"\nvnr: 1', None, None))


# ------------------------------------------------------------- with a real akaiutil
def volume_named(engine, name) -> Volume:
    disk = engine.list_disks()[0]
    part = engine.list_partitions(disk)[0]
    return next(v for v in engine.list_volumes(part) if v.name == name)


def test_the_engine_reads_a_real_programs_samples(binary, program_hd):
    engine = AkaiEngine(binary)
    engine.open([program_hd])
    try:
        lib = engine.volume_references(volume_named(engine, "LIB"))
        program = next(f for f in lib.files if f.is_program)
        assert lib.programs == {program.index: ("SINE",)}
        assert [f.name for f in lib.unused_samples()] == ["KICK.S3"]
        orphan = engine.volume_references(volume_named(engine, "ORPHAN"))
        assert orphan.samples_of(next(f for f in orphan.files if f.is_program)) == ([], ["SINE"])
        plain = engine.volume_references(volume_named(engine, "PLAIN"))
        assert not plain.has_programs
    finally:
        engine.close()


# ------------------------------------------------------------------------- the window
def open_volume(qt_app, window, image, name):
    window.open_images([str(image)])
    assert pump(qt_app, lambda: window.tree.topLevelItemCount() == 1)
    disk = window.tree.topLevelItem(0)
    assert pump(qt_app, lambda: disk.childCount() >= 1)
    part = disk.child(0)
    part.setExpanded(True)
    assert pump(qt_app, lambda: part.childCount() == 3)
    item = next(part.child(i) for i in range(part.childCount()) if part.child(i).text(0) == name)
    window.tree.setCurrentItem(item)
    assert pump(qt_app, lambda: window.table.rowCount() > 0 and window._pending == 0)


def row_of(window, name):
    return next(r for r in range(window.table.rowCount()) if window.table.item(r, 1).text() == name)


def test_selecting_a_sample_shows_its_program(qt_app, window, program_hd):
    open_volume(qt_app, window, program_hd, "LIB")
    window.table.selectRow(row_of(window, "SINE.S3"))
    assert pump(qt_app, lambda: "Used by" in window.info.toPlainText() and "TEST PROGRAM.P3" in window.info.toPlainText())
    window.table.selectRow(row_of(window, "KICK.S3"))
    assert pump(qt_app, lambda: "no program in this volume plays this sample" in window.info.toPlainText())


def test_selecting_a_program_shows_its_samples(qt_app, window, program_hd):
    open_volume(qt_app, window, program_hd, "ORPHAN")
    window.table.selectRow(row_of(window, "TEST PROGRAM.P3"))
    assert pump(qt_app, lambda: "Samples used" in window.info.toPlainText() and "Not in this volume" in window.info.toPlainText())


def test_the_volume_lists_unused_samples_only_if_it_has_programs(qt_app, window, program_hd):
    open_volume(qt_app, window, program_hd, "LIB")
    assert pump(qt_app, lambda: "Samples not in any program" in window.info.toPlainText() and "KICK.S3" in window.info.toPlainText())
    part = window.tree.topLevelItem(0).child(0)
    window.tree.setCurrentItem(next(part.child(i) for i in range(part.childCount()) if part.child(i).text(0) == "PLAIN"))
    assert pump(qt_app, lambda: "Room for" in window.info.toPlainText() and "PLAIN" in window.info.toPlainText())
    assert "Samples not in any program" not in window.info.toPlainText()


def test_select_unused_samples_picks_just_those(qt_app, window, program_hd):
    open_volume(qt_app, window, program_hd, "LIB")
    assert window.act_select_unused.isEnabled()
    window.act_select_unused.trigger()
    assert pump(qt_app, lambda: [f.name for f in window.table.selected_files()] == ["KICK.S3"])


def test_select_unused_samples_says_when_there_is_nothing_to_select(qt_app, window, program_hd):
    open_volume(qt_app, window, program_hd, "PLAIN")
    window.act_select_unused.trigger()
    assert pump(qt_app, lambda: "has no programs" in window.statusBar().currentMessage())
    assert window.table.selected_files() == []


def test_editing_a_disk_forgets_what_programs_use(qt_app, window, program_hd):
    open_volume(qt_app, window, program_hd, "LIB")
    assert pump(qt_app, lambda: len(window._refs_cache) == 1)
    (old_key,) = window._refs_cache
    window._usage_epoch[0] = window._usage_epoch.get(0, 0) + 1  # what _edited() does for the disk that was changed
    fresh = window._references(window._engine, window._volume)
    assert fresh is not None and len(window._refs_cache) == 1 and old_key not in window._refs_cache
