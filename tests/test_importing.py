"""Importing audio and Akai files into volumes: the conversion, the naming, and the real akaiutil."""

import struct
from pathlib import Path

import numpy as np
import pytest
import soundfile

from akaiutil_gui import importing
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import Volume


# --------------------------------------------------------------------------- helpers
def tone(freq=440.0, rate=44100, seconds=0.5, channels=1, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    wave = amp * np.sin(2 * np.pi * freq * t)
    return wave if channels == 1 else np.stack([wave * (c + 1) / channels for c in range(channels)], 1)


def make(path: Path, data, rate=44100, subtype="PCM_16", fmt=None):
    soundfile.write(str(path), data, rate, subtype=subtype, format=fmt)
    return path


@pytest.fixture
def audio(tmp_path):
    return tmp_path / "audio"


@pytest.fixture(autouse=True)
def _audio_dir(audio):
    audio.mkdir()


# ------------------------------------------------------------------- recognising files
@pytest.mark.parametrize(
    "name, kind",
    [
        ("a.wav", "audio"), ("a.WAV", "audio"), ("a.aif", "audio"), ("a.AIFF", "audio"), ("a.flac", "audio"), ("a.ogg", "audio"),
        ("KICK.S3", "akai"), ("kick.s1", "akai"), ("X.S9C", "akai"), ("PROG.P3", "akai"), ("FX.X", "akai"),
        ("disk.img", "other"), ("disk.iso", "other"), ("notes.txt", "other"), ("code.c", "other"), ("os.C3", "other"), ("noext", "other"),
    ],
)
def test_files_are_told_apart_by_their_extension(name, kind):
    assert importing.kind_of(name) == kind


def test_dropped_files_are_ordered_alphabetically_with_numbers_in_order(audio):
    names = ["snare 10.wav", "Kick.wav", "snare 2.wav", "bass.wav", "Snare 1.wav", "kick 03.wav"]
    for n in names:
        (audio / n).write_bytes(b"x")
    assert [p.name for p in importing.expand([str(audio / n) for n in names])] == [
        "bass.wav", "Kick.wav", "kick 03.wav", "Snare 1.wav", "snare 2.wav", "snare 10.wav",
    ]


def test_a_dropped_folder_gives_its_files_and_nothing_hidden_or_nested(audio):
    (audio / "b.wav").write_bytes(b"x")
    (audio / "a.flac").write_bytes(b"x")
    (audio / ".hidden.wav").write_bytes(b"x")
    (audio / "sub").mkdir()
    (audio / "sub" / "deep.wav").write_bytes(b"x")
    assert [p.name for p in importing.expand([str(audio), str(audio / "missing.wav")])] == ["a.flac", "b.wav"]


# ------------------------------------------------------------------------------ types
def test_each_kind_of_volume_accepts_its_own_kinds_of_sample():
    assert importing.allowed_types("S900") == ["S900"]
    assert importing.allowed_types("S1000") == ["S1000"]
    assert importing.allowed_types("S3000") == importing.allowed_types("CD3000") == ["S3000", "S1000", "S900"]
    assert importing.default_type("S1000") == "S1000" and importing.default_type("S3000") == "S3000"


@pytest.mark.parametrize(
    "ext, gen",
    [(".S9", "9"), (".S9C", "9"), (".P9", "9"), (".S1", "1"), (".P1", "1"), (".S3", "3"), (".P3", "3"), (".CD", "3"), (".s+", "3"), (".S", "13"), (".P", "13"), (".X", "13")],
)
def test_an_akai_files_sampler_comes_from_its_extension(ext, gen):
    assert importing.generation(ext) == gen


@pytest.mark.parametrize(
    "ext, volume, ok",
    [
        (".S3", "S3000", True), (".S1", "S3000", True), (".S", "S3000", True), (".S9", "S3000", True), (".S9C", "S3000", False),
        (".S3", "S1000", False), (".S1", "S1000", True), (".S", "S1000", True), (".S9", "S1000", False),
        (".S9", "S900", True), (".S9C", "S900", True), (".S3", "S900", False), (".S1", "S900", False), (".P", "S900", False),
        (".S3", "CD3000", True),
    ],
)
def test_which_akai_files_belong_in_which_volumes(ext, volume, ok):
    assert (importing.akai_file_problem(ext, volume) is None) is ok


def test_the_reason_a_file_doesnt_belong_is_in_words():
    assert importing.akai_file_problem(".S3", "S900") == "S3000 files don't belong in an S900 volume"
    assert importing.akai_file_problem(".S9", "S1000") == "S900 files don't belong in an S1000 volume"
    assert importing.akai_file_problem(".S9C", "S3000") == "compressed S900 samples only work in an S900 volume"


# ------------------------------------------------------------------------ inspecting
def test_inspecting_audio_reads_what_the_converter_needs(audio):
    src = importing.inspect(make(audio / "a.flac", tone(rate=48000, channels=2), 48000, "PCM_24"))
    assert (src.kind, src.format, src.subtype, src.channels, src.rate, src.bits) == ("audio", "FLAC", "PCM_24", 2, 48000, 24)
    assert src.frames == 24000 and src.problem == ""


def test_audio_that_cant_be_imported_says_why(audio):
    assert "3 channels" in importing.inspect(make(audio / "m.flac", tone(channels=3), 44100, "PCM_16")).problem
    empty = audio / "e.wav"
    make(empty, np.zeros((0,)), 44100)
    assert importing.inspect(empty).problem == "it has no audio in it"
    junk = audio / "j.wav"
    junk.write_bytes(b"this is not audio at all")
    assert importing.inspect(junk).problem.startswith("can't read it as audio")
    assert importing.inspect(audio / "gone.wav").problem.startswith("can't read it as audio")


def test_an_akai_sample_is_checked_for_its_header(audio):
    good = audio / "KICK.S3"
    good.write_bytes(b"\x03" + bytes(300))
    assert importing.inspect(good).problem == ""
    bad = audio / "NOT.S3"
    bad.write_bytes(b"hello, I am a text file" * 20)
    assert importing.inspect(bad).problem == "this doesn't look like an Akai sample"
    assert importing.inspect(audio / "tiny.S3").problem  # missing
    empty = audio / "EMPTY.S1"
    empty.write_bytes(b"")
    assert importing.inspect(empty).problem == "the file is empty"
    prog = audio / "PROG.P3"
    prog.write_bytes(b"whatever a program starts with")
    assert importing.inspect(prog).problem == ""  # only samples have a header worth checking


# -------------------------------------------------------------------------- converting
def test_what_has_to_be_done_to_a_file(audio):
    ok = importing.inspect(make(audio / "ok.wav", tone(), 44100, "PCM_16"))
    assert importing.conversion_steps(ok, "S3000") == []
    flac = importing.inspect(make(audio / "x.flac", tone(rate=48000), 48000, "PCM_24"))
    assert importing.conversion_steps(flac, "S3000") == ["converted to WAV", "converted to 16-bit", "resampled from 48 to 44.1 kHz"]
    aiff = importing.inspect(make(audio / "x.aiff", tone(), 44100, "PCM_16"))
    assert importing.conversion_steps(aiff, "S1000") == ["converted to WAV"]
    stereo = importing.inspect(make(audio / "s.wav", tone(channels=2), 44100, "PCM_16"))
    assert importing.conversion_steps(stereo, "S3000", importing.STEREO_SPLIT) == []
    assert importing.conversion_steps(stereo, "S3000", importing.STEREO_MIX) == ["mixed to mono"]
    assert importing.MAX_RATE == {"S3000": 44100, "S1000": 44100, "S900": 40000}
    hi = importing.inspect(make(audio / "hi.wav", tone(rate=44100), 44100, "PCM_16"))
    assert importing.conversion_steps(hi, "S900") == ["resampled from 44.1 to 40 kHz"]
    floaty = importing.inspect(make(audio / "f.wav", tone(), 44100, "FLOAT"))
    assert importing.conversion_steps(floaty, "S3000") == ["converted to 16-bit"]
    eight = importing.inspect(make(audio / "e.wav", tone(), 44100, "PCM_U8"))
    assert importing.conversion_steps(eight, "S3000") == ["converted to 16-bit"]


def test_the_dialog_line_for_each_kind_of_file(audio):
    flac = importing.inspect(make(audio / "x.flac", tone(rate=96000, channels=2), 96000, "PCM_24"))
    assert importing.describe(flac, "S3000") == "FLAC · 96 kHz · 24-bit · stereo  →  converted to WAV, converted to 16-bit, resampled from 96 to 44.1 kHz"
    ok = importing.inspect(make(audio / "ok.wav", tone(), 44100, "PCM_16"))
    assert importing.describe(ok, "S3000") == "WAV · 44.1 kHz · 16-bit · mono  →  used as it is"
    assert importing.describe(importing.inspect(audio / "x.S3"), "S3000").startswith("skipped:")
    akai = audio / "KICK.S3"
    akai.write_bytes(b"\x03" + bytes(300))
    assert importing.describe(importing.inspect(akai), "S3000") == "Akai file, imported as it is"


def test_a_good_wav_is_passed_through_byte_for_byte(audio):
    src = importing.inspect(make(audio / "ok.wav", tone(), 44100, "PCM_16"))
    out = audio / "out.wav"
    assert importing.prepare_audio(src, out, "S3000") == (1, 44100)
    assert out.read_bytes() == src.path.read_bytes()


def test_other_formats_become_16_bit_wavs_at_a_rate_the_sampler_can_play(audio):
    src = importing.inspect(make(audio / "x.flac", tone(1000, 96000, 0.5, channels=2), 96000, "PCM_24"))
    out = audio / "out.wav"
    assert importing.prepare_audio(src, out, "S3000") == (2, 44100)
    info = soundfile.info(str(out))
    assert (info.format, info.subtype, info.channels, info.samplerate) == ("WAV", "PCM_16", 2, 44100)
    assert info.frames == pytest.approx(0.5 * 44100, abs=1)
    data, _ = soundfile.read(str(out))
    assert np.abs(data).max() == pytest.approx(0.5, abs=0.01)  # the level is kept


def test_stereo_can_be_mixed_to_mono(audio):
    left = np.full(1000, 0.4)
    right = np.full(1000, -0.2)
    src = importing.inspect(make(audio / "s.aiff", np.stack([left, right], 1), 44100, "PCM_16"))
    out = audio / "out.wav"
    assert importing.prepare_audio(src, out, "S3000", importing.STEREO_MIX) == (1, 44100)
    data, _ = soundfile.read(str(out))
    assert data.shape == (1000,) and data[500] == pytest.approx(0.1, abs=0.001)


def test_loud_audio_is_clipped_not_wrapped(audio):
    src = importing.inspect(make(audio / "loud.wav", np.array([1.0, -1.0, 0.5, -0.5] * 100), 44100, "FLOAT"))
    out = audio / "out.wav"
    importing.prepare_audio(src, out, "S3000")
    pcm, _ = soundfile.read(str(out), dtype="int16")
    assert pcm.max() == 32767 and pcm.min() == -32767 and set(pcm) == {32767, -32767, 16384, -16384}


def test_an_s900_import_is_held_to_40_khz(audio):
    src = importing.inspect(make(audio / "ok.wav", tone(rate=44100), 44100, "PCM_16"))
    out = audio / "out.wav"
    assert importing.prepare_audio(src, out, "S900") == (1, 40000)
    assert soundfile.info(str(out)).samplerate == 40000


def test_digital_silence_stays_silent(audio):
    src = importing.inspect(make(audio / "z.flac", np.zeros(5000), 48000, "PCM_24"))
    out = audio / "out.wav"
    importing.prepare_audio(src, out, "S3000")
    pcm, _ = soundfile.read(str(out), dtype="int16")
    assert not pcm.any()  # no noise added


# ------------------------------------------------------------------------------ names
@pytest.mark.parametrize(
    "stem, limit, expected",
    [
        ("kick", 12, "KICK"), ("Kick Drum 01", 12, "KICK DRUM 01"), ("snare_hit_02", 12, "SNARE HIT 02"),
        ("Kick (close) [A]", 12, "KICK CLOSE A"), ("a very long sample name", 12, "A VERY LONG"), ("a very long sample name", 10, "A VERY LON"),
        ("  --x--  ", 12, "--X--"), ("???", 12, "SAMPLE"), ("...", 12, "SAMPLE"), ("café", 12, "CAF"), ("kick-01#2+", 12, "KICK-01#2+"),
    ],
)
def test_a_file_name_becomes_an_akai_name(stem, limit, expected):
    assert importing.clean_stem(stem, limit) == expected


def test_the_names_akaiutil_will_give(audio):
    assert importing.final_names("KICK", ".S3", 1, 12) == ["KICK.S3"]
    assert importing.final_names("KICK", ".S3", 2, 12) == ["KICK      -L.S3", "KICK      -R.S3"]
    assert importing.final_names("A VERY LONG", ".S1", 2, 12) == ["A VERY LON-L.S1", "A VERY LON-R.S1"]
    assert importing.final_names("KICK", ".S9", 2, 10) == ["KICK    -L.S9", "KICK    -R.S9"]


def test_a_name_thats_taken_gets_a_number_instead_of_replacing_the_old_one():
    taken = {"kick.s3", "kick 2.s3"}
    assert importing.unique_base("KICK", ".S3", 1, 12, taken) == "KICK 3"
    assert importing.unique_base("SNARE", ".S3", 1, 12, taken) == "SNARE"
    assert importing.unique_base("TWELVE CHARS", ".S3", 1, 12, {"twelve chars.s3"}) == "TWELVE CHA 2"  # the number makes room
    assert importing.unique_base("KICK", ".S3", 1, 12, {"kick.s1"}) == "KICK"  # a different type is a different file
    stereo_taken = {n.casefold() for n in importing.final_names("PAD", ".S3", 2, 12)}
    assert importing.unique_base("PAD", ".S3", 2, 12, stereo_taken) == "PAD 2"
    assert importing.unique_base("PAD", ".S3", 2, 12, {importing.final_names("PAD", ".S3", 2, 12)[1].casefold()}) == "PAD 2"  # either half clashing counts


# ------------------------------------------------------------------- with akaiutil
@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def volumes(eng):
    return eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])


def files(eng, vol):
    return [f.name for f in eng.list_files(vol)]


def run(eng, vol, paths, tmp_path, sample_type="S3000", **kw):
    sources = [importing.inspect(p) for p in importing.expand([str(p) for p in paths])]
    return importing.run_import(eng, vol, sources, tmp_path / "work", sample_type, **kw)


def test_a_wav_becomes_a_sample_with_the_same_audio(ed, audio, tmp_path):
    drums = volumes(ed)[0]
    src = make(audio / "pluck.wav", tone(330, 44100, 0.3), 44100, "PCM_16")
    result = run(ed, drums, [src], tmp_path)
    assert result.imported == ["PLUCK.S3"] and result.failed == []
    assert files(ed, drums) == ["KICK.S3", "SNARE.S3", "PLUCK.S3"]
    out = tmp_path / "back"
    out.mkdir()
    wav = ed.export_sample_wav(drums, [f for f in ed.list_files(drums) if f.name == "PLUCK.S3"][0], out)
    got, rate = soundfile.read(str(wav), dtype="int16")
    want, _ = soundfile.read(str(src), dtype="int16")
    assert rate == 44100 and np.array_equal(got, want)  # the very same 16-bit samples


def test_flac_aiff_and_a_fast_wav_all_import(ed, audio, tmp_path):
    drums = volumes(ed)[0]
    paths = [
        make(audio / "a flac.flac", tone(rate=48000, amp=0.3), 48000, "PCM_24"),
        make(audio / "b aiff.aiff", tone(), 44100, "PCM_16"),
        make(audio / "c hires.wav", tone(rate=96000), 96000, "PCM_24"),
    ]
    result = run(ed, drums, paths, tmp_path)
    assert result.imported == ["A FLAC.S3", "B AIFF.S3", "C HIRES.S3"] and result.failed == []
    infos = {f.name: ed.sample_info(drums, f) for f in ed.list_files(drums) if f.name not in ("KICK.S3", "SNARE.S3")}
    assert [i.rate for i in infos.values()] == [44100, 44100, 44100]


def test_a_batch_goes_in_the_order_given_into_the_next_free_slots(ed, audio, tmp_path):
    bass = volumes(ed)[1]
    names = ["take 10.wav", "take 2.wav", "Take 1.wav", "alpha.wav"]
    for n in names:
        make(audio / n, tone(), 44100, "PCM_16")
    result = run(ed, bass, [audio / n for n in names], tmp_path)
    assert result.imported == ["ALPHA.S3", "TAKE 1.S3", "TAKE 2.S3", "TAKE 10.S3"]
    listing = {f.name: f.index for f in ed.list_files(bass)}
    assert [listing[n] for n in result.imported] == sorted(listing[n] for n in result.imported)  # slots follow the order


def test_stereo_is_split_into_a_left_and_a_right_sample_or_mixed(ed, audio, tmp_path):
    drums, bass = volumes(ed)[0], volumes(ed)[1]
    src = make(audio / "pad.wav", tone(channels=2), 44100, "PCM_16")
    split = run(ed, drums, [src], tmp_path)
    assert split.imported == ["PAD       -L.S3", "PAD       -R.S3"]  # the name padded to 10 characters, then -L / -R
    assert split.imported == importing.final_names("PAD", ".S3", 2, 12)  # exactly what we predict
    mixed = run(ed, bass, [src], tmp_path, stereo=importing.STEREO_MIX)
    assert mixed.imported == ["PAD.S3"]


@pytest.mark.parametrize(
    "sample_type, compress, expected",
    [("S1000", False, "PLUCK.S1"), ("S900", False, "PLUCK.S9"), ("S900", True, "PLUCK.S9C")],
)
def test_every_kind_of_sample_can_be_made(ed, audio, tmp_path, sample_type, compress, expected):
    vol = volumes(ed)[0]  # an S3000 volume takes any
    src = make(audio / "pluck.wav", tone(), 44100, "PCM_16")
    result = run(ed, vol, [src], tmp_path, sample_type=sample_type, compress=compress)
    assert result.imported == [expected] and result.failed == []


def test_an_s900_sample_is_held_to_40_khz_and_ten_character_names(ed, audio, tmp_path):
    vol = volumes(ed)[0]
    src = make(audio / "a rather long name.wav", tone(), 44100, "PCM_16")
    result = run(ed, vol, [src], tmp_path, sample_type="S900")
    assert result.imported == ["A RATHER L.S9"]
    sample = next(f for f in ed.list_files(vol) if f.name == "A RATHER L.S9")
    assert ed.sample_info(vol, sample).rate == 40000


def test_an_existing_file_is_never_replaced(ed, audio, tmp_path):
    drums = volumes(ed)[0]
    before = {f.name: f.size for f in ed.list_files(drums)}
    src = make(audio / "kick.wav", tone(120, 44100, 0.2), 44100, "PCM_16")
    result = run(ed, drums, [src, make(audio / "kick.flac", tone(130, 44100, 0.2), 44100, "PCM_16")], tmp_path)
    assert result.imported == ["KICK 2.S3", "KICK 3.S3"]
    after = {f.name: f.size for f in ed.list_files(drums)}
    assert after["KICK.S3"] == before["KICK.S3"] and after["SNARE.S3"] == before["SNARE.S3"]
    assert len(after) == 4


def test_one_bad_file_doesnt_stop_the_others(ed, audio, tmp_path):
    drums = volumes(ed)[0]
    good1 = make(audio / "a.wav", tone(), 44100, "PCM_16")
    junk = audio / "b.wav"
    junk.write_bytes(b"not audio")
    multi = make(audio / "c.flac", tone(channels=3), 44100, "PCM_16")
    good2 = make(audio / "d.wav", tone(), 44100, "PCM_16")
    result = run(ed, drums, [good1, junk, multi, good2], tmp_path)
    assert result.imported == ["A.S3", "D.S3"]
    assert [f.split(":")[0] for f in result.failed] == ["b.wav", "c.flac"]
    assert "can't read it as audio" in result.failed[0] and "3 channels" in result.failed[1]


def test_progress_is_reported_for_each_file(ed, audio, tmp_path):
    seen = []
    drums = volumes(ed)[0]
    paths = [make(audio / f"s{i}.wav", tone(), 44100, "PCM_16") for i in range(3)]
    sources = [importing.inspect(p) for p in paths]
    importing.run_import(ed, drums, sources, tmp_path / "w", "S3000", progress=lambda i, n, name: seen.append((i, n, name)))
    assert seen == [(0, 3, "s0.wav"), (1, 3, "s1.wav"), (2, 3, "s2.wav")]


def test_importing_needs_editing_to_be_on(binary, editable_hd, audio, tmp_path):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        drums = volumes(eng)[0]
        src = make(audio / "x.wav", tone(), 44100, "PCM_16")
        result = run(eng, drums, [src], tmp_path)
        assert result.imported == [] and "Editing is switched off" in result.failed[0]
        assert files(eng, drums) == ["KICK.S3", "SNARE.S3"]
    finally:
        eng.close()


def test_an_akai_file_goes_in_as_it_is(ed, binary, audio, tmp_path):
    drums, bass = volumes(ed)[0], volumes(ed)[1]
    kick = next(f for f in ed.list_files(drums) if f.name == "KICK.S3")
    exported = tmp_path / "exp"
    exported.mkdir()
    ed.export_files(drums, [kick], exported)
    raw = exported / "KICK.S3"
    original = raw.read_bytes()
    result = run(ed, bass, [raw], tmp_path)
    assert result.imported == ["KICK.S3"] and result.failed == []
    copied = next(f for f in ed.list_files(bass) if f.name == "KICK.S3")
    assert copied.size == kick.size
    again = tmp_path / "again"
    again.mkdir()
    ed.export_files(bass, [copied], again)
    assert (again / "KICK.S3").read_bytes() == original  # byte for byte


def test_akai_files_of_the_wrong_sampler_or_with_no_header_are_refused(ed, audio, tmp_path):
    bass = volumes(ed)[1]
    junk = audio / "FAKE.S3"
    junk.write_bytes(b"plain text pretending" * 20)
    s9c = audio / "X.S9C"
    s9c.write_bytes(b"\x00" * 400)
    result = run(ed, bass, [junk, s9c], tmp_path)
    assert result.imported == []
    assert "doesn't look like an Akai sample" in result.failed[0]
    assert "compressed S900 samples only work in an S900 volume" in result.failed[1]
    assert files(ed, bass) == ["BASS.S3"]


def test_exported_wavs_keep_their_loops_when_they_come_back(binary, editable_hd, tmp_path):
    import imagegen

    imagegen.add_loop(editable_hd, "KICK", loop_end=4000, loop_length=1000, time_ms=None)
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    try:
        drums, bass = volumes(eng)[0], volumes(eng)[1]
        kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
        before = eng.sample_info(drums, kick)
        assert before.loops
        out = tmp_path / "wav"
        out.mkdir()
        wav = eng.export_sample_wav(drums, kick, out)
        result = importing.run_import(eng, bass, [importing.inspect(wav)], tmp_path / "w", "S3000")
        assert result.imported == ["KICK.S3"]
        after = eng.sample_info(bass, next(f for f in eng.list_files(bass) if f.name == "KICK.S3"))
        assert [(l.end, l.length, l.time_ms) for l in after.loops] == [(l.end, l.length, l.time_ms) for l in before.loops]
    finally:
        eng.close()


def test_an_s900_volume_takes_only_s900_samples(binary, images, tmp_path, audio):
    import shutil

    floppy = tmp_path / "f9.img"
    shutil.copy(images["fl9"], floppy)
    eng = AkaiEngine(binary)
    eng.open([floppy])
    eng.set_writable(True)
    try:
        vol = eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])[0]
        assert vol.type == "S900"
        src = make(audio / "new sound.wav", tone(), 44100, "PCM_16")
        ok = run(eng, vol, [src], tmp_path, sample_type="S900")
        assert ok.imported == ["NEW SOUND.S9"]
        wrong = run(eng, vol, [src], tmp_path, sample_type="S3000")
        assert wrong.imported == [] and "S3000 samples don't belong in an S900 volume" in wrong.failed[0]
        assert [f.name for f in eng.list_files(vol)] == ["KICK.S9", "NEW SOUND.S9"]
    finally:
        eng.close()


def test_sorting_mixed_names_never_compares_a_number_with_text():
    names = ["10 kick.wav", "kick 2.wav", "a1b2.wav", "2.wav", "kick.wav", "Z.wav", "1a.flac", "1a.wav", "x9.wav", "x10.wav"]
    ordered = [Path(n).name for n in sorted(names, key=importing.natural_key)]
    assert ordered == ["1a.flac", "1a.wav", "2.wav", "10 kick.wav", "a1b2.wav", "kick.wav", "kick 2.wav", "x9.wav", "x10.wav", "Z.wav"]
