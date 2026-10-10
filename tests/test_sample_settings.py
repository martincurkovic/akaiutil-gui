"""Editing a sample's own settings (loops, root key, tuning, play mode, markers) with the real akaiutil."""

import dataclasses

import pytest

from akaiutil_gui import akai_header
from akaiutil_gui.akai_header import LoopSetting
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.session import EngineError


# ------------------------------------------------------------------------------ the header on its own
def header(loops=((100, 900),), frames=5000, patch=None):
    import struct

    h = bytearray(akai_header.build_header("S3000", 44100, frames, root=60, loops=list(loops)))
    h[26:30] = struct.pack("<I", frames)
    for k, v in (patch or {}).items():
        h[k] = v
    return bytes(h)


def test_settings_read_back_what_the_header_holds():
    s = akai_header.read_settings(header(((100, 900), (1200, 3000))))
    assert (s.root, s.semitone, s.cents, s.play_mode, s.rate, s.frames) == (60, 0, 0, 0, 44100, 5000)
    assert [(l.start, l.end, l.enabled) for l in s.loops[:3]] == [(100, 900, True), (1200, 3000, True), (0, 0, False)]
    assert len(s.loops) == akai_header.MAX_LOOPS


def test_tuning_is_signed():
    s = akai_header.read_settings(header(patch={20: 0xF6, 21: 0x03}))  # -10 cents, +3 semitones
    assert (s.cents, s.semitone) == (-10, 3)
    out = akai_header.write_settings(header(), dataclasses.replace(s, cents=-50, semitone=-50))
    assert akai_header.read_settings(out).cents == -50 and akai_header.read_settings(out).semitone == -50


def test_writing_settings_that_were_not_changed_gives_the_same_bytes_back():
    h = header(((100, 900),))
    assert akai_header.write_settings(h, akai_header.read_settings(h)) == h


def test_only_the_edited_fields_change_and_an_untouched_loop_keeps_its_exact_bytes():
    h = bytearray(header(((100, 900),)))
    junk = bytes(range(1, 11)) + b"\x00\x00"  # (what the sampler may leave in the entry of a loop that is off: its time is 0)
    h[38 + 12 : 38 + 24] = junk
    h = bytes(h)
    s = akai_header.read_settings(h)
    out = akai_header.write_settings(h, dataclasses.replace(s, root=72, play_mode=2, start=10, end=4000))
    changed = {i for i in range(len(h)) if h[i] != out[i]}
    assert changed <= {2, 19, 30, 34, 35}  # root, play mode, start and end markers
    assert out[38 + 12 : 38 + 24] == junk


def test_a_loop_is_added_changed_and_removed():
    h = header(((100, 900),))
    s = akai_header.read_settings(h)
    loops = list(s.loops)
    loops[2] = LoopSetting(2000, 3000, 250)  # a third loop (the second slot stays off)
    loops[0] = dataclasses.replace(loops[0], end=950)
    out = akai_header.read_settings(akai_header.write_settings(h, dataclasses.replace(s, loops=tuple(loops))))
    assert [(l.start, l.end, l.time) for l in out.loops[:3]] == [(100, 950, akai_header.LOOP_HOLD), (0, 0, 0), (2000, 3000, 250)]
    raw = akai_header.write_settings(h, dataclasses.replace(s, loops=tuple(loops)))
    assert raw[16] == 3  # lnum counts to the last loop in use
    loops[0] = dataclasses.replace(loops[0], time=0)
    loops[2] = dataclasses.replace(loops[2], time=0)
    gone = akai_header.write_settings(h, dataclasses.replace(s, loops=tuple(loops)))
    assert gone[16] == 0 and not any(l.enabled for l in akai_header.read_settings(gone).loops)


@pytest.mark.parametrize(
    "change, text",
    [
        (dict(root=128), "root key"),
        (dict(semitone=51), "Tuning"),
        (dict(cents=-51), "Tuning"),
        (dict(play_mode=9), "play mode"),
        (dict(start=5000, end=6000), "start marker"),
        (dict(start=300, end=300), "start marker"),
    ],
)
def test_settings_that_can_not_be_written_are_refused_with_a_reason(change, text):
    s = akai_header.read_settings(header())
    assert any(text in p for p in akai_header.problems(dataclasses.replace(s, **change)))
    with pytest.raises(ValueError, match=text):
        akai_header.write_settings(header(), dataclasses.replace(s, **change))


def test_a_loop_outside_the_sample_or_backwards_is_refused():
    s = akai_header.read_settings(header())
    for loop in (LoopSetting(900, 100, 100), LoopSetting(0, 9000, 100), LoopSetting(10, 20, 20000)):
        loops = (loop, *s.loops[1:])
        assert akai_header.problems(dataclasses.replace(s, loops=loops))


# ------------------------------------------------------------------- with the real akaiutil
@pytest.fixture
def ed(binary, editable_hd):
    import imagegen

    imagegen.add_loop(editable_hd, "KICK", loop_end=4000, loop_length=1000, time_ms=None)
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def drums(eng):
    part = next(p for p in eng.list_partitions(eng.list_disks()[0]) if p.name == "A")
    return next(v for v in eng.list_volumes(part) if v.name == "DRUMS")


def kick(eng):
    return next(f for f in eng.list_files(drums(eng)) if f.name == "KICK.S3")


def whole(eng, vol, f, folder):
    folder.mkdir(exist_ok=True)
    eng.export_files(vol, [f], folder, confirm_overwrite=lambda p: True)
    return (folder / f.name).read_bytes()


def test_the_settings_of_a_real_sample_are_read(ed):
    s = ed.read_sample_settings(drums(ed), kick(ed))
    assert (s.root, s.play_mode, s.rate) == (60, 0, 44100)
    assert [(l.start, l.end, l.hold) for l in s.loops if l.enabled] == [(3000, 4000, True)]


def test_editing_a_sample_changes_only_its_settings_and_leaves_it_where_it_was(ed, tmp_path):
    vol, f = drums(ed), kick(ed)
    before_files = ed.list_files(vol)
    original = whole(ed, vol, f, tmp_path / "a")
    s = ed.read_sample_settings(vol, f)
    loops = list(s.loops)
    loops[0] = dataclasses.replace(loops[0], start=3200, end=3900, time=500)
    loops[1] = LoopSetting(100, 200, 100)
    new = dataclasses.replace(s, root=48, semitone=2, cents=-12, play_mode=1, loops=tuple(loops), start=5, end=s.end - 5)
    result = ed.edit_sample_settings(vol, f, new)

    after_files = ed.list_files(vol)
    assert [(x.index, x.name, x.size, x.os_version, x.tags) for x in after_files] == [(x.index, x.name, x.size, x.os_version, x.tags) for x in before_files]
    assert result.index == f.index and result.name == f.name
    edited = whole(ed, vol, result, tmp_path / "b")
    size = akai_header.HEADER_SIZES["S3000"]
    assert edited[size:] == original[size:]  # the audio is untouched
    got = akai_header.read_settings(edited)
    assert (got.root, got.semitone, got.cents, got.play_mode, got.start, got.end) == (48, 2, -12, 1, 5, s.end - 5)
    assert [(l.start, l.end, l.time) for l in got.loops[:2]] == [(3200, 3900, 500), (100, 200, 100)]
    assert edited[3:15] == original[3:15]  # the name inside the header is still the sample's
    info = ed.sample_info(vol, result)
    assert info.root_key == 48 and len(info.loops) == 2  # akaiutil reads the same thing


def test_nothing_is_left_behind_and_nothing_moves_in_the_volume(ed):
    vol, f = drums(ed), kick(ed)
    s = ed.read_sample_settings(vol, f)
    ed.edit_sample_settings(vol, f, dataclasses.replace(s, root=50))
    assert [x.name for x in ed.list_files(vol)] == ["KICK.S3", "SNARE.S3"]
    assert not any(x.name.startswith("#") for x in ed.list_files(vol))


def test_an_edit_with_nothing_changed_does_not_touch_the_image(ed, editable_hd):
    vol, f = drums(ed), kick(ed)
    image = editable_hd.read_bytes()
    assert ed.edit_sample_settings(vol, f, ed.read_sample_settings(vol, f)) == f
    assert editable_hd.read_bytes() == image


def test_a_samples_tags_survive_an_edit(ed):
    vol = drums(ed)
    ed.copy_file(vol, kick(ed), vol, "TAGGED.S3", tags=[2, 10])
    tagged = next(x for x in ed.list_files(vol) if x.name == "TAGGED.S3")
    assert tagged.tags == "02 10"
    s = ed.read_sample_settings(vol, tagged)
    after = ed.edit_sample_settings(vol, tagged, dataclasses.replace(s, root=40))
    assert (after.tags, after.name, after.index) == ("02 10", "TAGGED.S3", tagged.index)


def test_a_sample_in_a_later_slot_returns_to_its_slot(ed):
    vol = drums(ed)
    snare = next(x for x in ed.list_files(vol) if x.name == "SNARE.S3")
    assert snare.index == 2
    s = ed.read_sample_settings(vol, snare)
    after = ed.edit_sample_settings(vol, snare, dataclasses.replace(s, root=70))
    assert after.index == 2 and [x.name for x in ed.list_files(vol)] == ["KICK.S3", "SNARE.S3"]


def test_invalid_settings_are_refused_before_anything_is_written(ed, editable_hd):
    vol, f = drums(ed), kick(ed)
    image = editable_hd.read_bytes()
    s = ed.read_sample_settings(vol, f)
    with pytest.raises(EngineError, match="start marker"):
        ed.edit_sample_settings(vol, f, dataclasses.replace(s, start=s.end, end=s.end))
    assert editable_hd.read_bytes() == image


def test_a_check_that_fails_puts_everything_back(ed, monkeypatch):
    vol, f = drums(ed), kick(ed)
    before = [(x.index, x.name, x.size, x.start_block, x.tags) for x in ed.list_files(vol)]
    s = ed.read_sample_settings(vol, f)
    monkeypatch.setattr(ed, "_read_whole", lambda index, session: b"not the audio")  # (the new copy looks wrong)
    with pytest.raises(EngineError, match="“KICK.S3” wasn't changed"):
        ed.edit_sample_settings(vol, f, dataclasses.replace(s, root=33))
    monkeypatch.undo()
    assert [(x.index, x.name, x.size, x.start_block, x.tags) for x in ed.list_files(vol)] == before  # the original, where it was
    assert ed.read_sample_settings(vol, kick(ed)).root == 60


def test_s900_samples_and_other_files_are_refused(binary, images, tmp_path):
    import shutil

    floppy = tmp_path / "f9.img"
    shutil.copy(images["fl9"], floppy)
    eng = AkaiEngine(binary)
    eng.open([floppy])
    eng.set_writable(True)
    try:
        vol = eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])[0]
        sample = eng.list_files(vol)[0]
        with pytest.raises(EngineError, match="not S900"):
            eng.read_sample_settings(vol, sample)
        with pytest.raises(EngineError, match="not S900"):
            eng.edit_sample_settings(vol, sample, None)
    finally:
        eng.close()


def test_a_file_that_moved_is_noticed(ed):
    vol, f = drums(ed), kick(ed)
    stale = dataclasses.replace(f, index=2)
    with pytest.raises(EngineError, match="any more"):
        ed.read_sample_settings(vol, stale)
