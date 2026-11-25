"""The direct-to-disk test image (tests/imagegen.py) and what the engine already says about it.

The take table, export and import come later (dev_docs/dd-takes-plan.md); this checks the fixture those need.
"""

import shutil
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile

import imagegen
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui import takes as takes_module
from akaiutil_gui.engine.models import Take
from akaiutil_gui.engine.session import EngineError


@pytest.fixture
def dd_engine(binary, images):
    eng = AkaiEngine(binary)
    eng.open([images["dd"]])
    yield eng
    eng.close()


def _dd_partition(eng):
    disk = eng.list_disks()[0]
    return next(p for p in eng.list_partitions(disk) if p.is_dd)


def test_image_has_a_dd_partition_after_the_sampler_partitions(dd_engine):
    parts = dd_engine.list_partitions(dd_engine.list_disks()[0])
    assert [p.name for p in parts] == ["A", "B", "DD"]
    assert [p.is_dd for p in parts] == [False, False, True]


def test_dd_partition_lists_a_mono_and_a_stereo_take(dd_engine):
    text = dd_engine.list_takes_text(_dd_partition(dd_engine))
    rows = [ln.split() for ln in text.splitlines() if ln.strip()[:1].isdigit()]
    assert [(r[0], r[1]) for r in rows] == [("1", "MONO"), ("2", "STEREO")]
    assert [r[-1] for r in rows] == ["MONO", "STEREO"]
    assert "total:   2 take(s)" in text


def test_scount_counts_both_channels_of_a_stereo_take(dd_engine):
    # one second at 44.1 kHz: 44,100 words mono, 88,200 stereo (the take table's duration has to halve the second one)
    text = dd_engine.list_takes_text(_dd_partition(dd_engine))
    rows = {r[1]: r for r in (ln.split() for ln in text.splitlines() if ln.strip()[:1].isdigit())}
    assert rows["MONO"][-3] == "44100" and rows["STEREO"][-3] == "88200"
    assert rows["MONO"][-2] == rows["STEREO"][-2] == "44100"  # sample rate


def test_engine_lists_the_takes_as_objects(dd_engine):
    takes = dd_engine.list_takes(_dd_partition(dd_engine))
    assert [(t.index, t.name, t.stereo, t.rate) for t in takes] == [(1, "MONO", False, 44100), (2, "STEREO", True, 44100)]
    assert [t.seconds for t in takes] == [1.0, 1.0]  # one second each: the stereo take's words are halved
    assert [t.disk_bytes for t in takes] == [2 * 262144] * 2  # a sample cluster and an envelope cluster


def test_a_takes_details_read_as_settings_not_as_akaiutil_prints_them(dd_engine):
    from akaiutil_gui import describe

    part = _dd_partition(dd_engine)
    take = dd_engine.list_takes(part)[1]
    details = describe.describe_take(dd_engine.take_info(part, take), take)
    assert (details.title, details.subtitle) == ("STEREO", "Direct-to-disk take 2")
    rows = {r.label: r.value for b in details.blocks for r in b.rows}
    assert rows["Type"] == "Stereo" and rows["Sample rate"] == "44,100 Hz" and rows["Length"] == "1.00 s"
    assert rows["Fade in"] == "10ms" and rows["MIDI channel"] == "Channel 16" and rows["MIDI note"] == "C3  (MIDI 60)"
    assert rows["Output"] == "OFF" and rows["FX bus"] == "OFF"
    assert [b.title for b in details.blocks] == ["Take", "Playback", "Output", "Technical"]


def _frames(path: Path):
    with wave.open(str(path), "rb") as w:
        return w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())


def test_a_take_exports_as_the_wav_it_was_made_from(dd_engine, images, tmp_path):
    part = _dd_partition(dd_engine)
    takes = dd_engine.list_takes(part)
    res = dd_engine.export_takes(part, takes, tmp_path, as_wav=True)
    assert (res.skipped, res.failed) == ([], [])
    assert sorted(Path(p).name for p in res.exported) == ["MONO.wav", "STEREO.wav"]
    for name in ("MONO", "STEREO"):
        assert _frames(tmp_path / f"{name}.wav") == _frames(images["dd"].parent / "wavs" / f"{name}.wav")  # the audio comes back bit for bit
    assert _frames(tmp_path / "STEREO.wav")[:2] == (2, 44100)


def test_a_take_exports_raw_as_a_tk_file(dd_engine, tmp_path):
    part = _dd_partition(dd_engine)
    res = dd_engine.export_takes(part, dd_engine.list_takes(part)[:1], tmp_path)
    assert [Path(p).name for p in res.exported] == ["MONO.TK"]
    assert (tmp_path / "MONO.TK").stat().st_size == 2 * 262144 + 64  # a sample cluster, an envelope cluster, and 64 bytes more


def test_existing_files_are_left_alone_unless_the_caller_agrees(dd_engine, tmp_path):
    part = _dd_partition(dd_engine)
    take = dd_engine.list_takes(part)[:1]
    (tmp_path / "MONO.wav").write_bytes(b"mine")
    res = dd_engine.export_takes(part, take, tmp_path, as_wav=True)
    assert (res.exported, res.skipped) == ([], [str(tmp_path / "MONO.wav")]) and (tmp_path / "MONO.wav").read_bytes() == b"mine"
    res = dd_engine.export_takes(part, take, tmp_path, as_wav=True, confirm_overwrite=lambda _p: True)
    assert len(res.exported) == 1 and (tmp_path / "MONO.wav").read_bytes() != b"mine"


def test_two_takes_with_one_name_do_not_overwrite_each_other(binary, images, tmp_path):
    image = tmp_path / "twins.img"
    shutil.copy(images["dd"], image)
    imagegen._script(binary, image, ["cd /disk0/DD", "wav2take MONO.wav"], images["dd"].parent / "wavs")  # a second MONO
    eng = AkaiEngine(binary)
    eng.open([image])
    try:
        part = _dd_partition(eng)
        takes = [t for t in eng.list_takes(part) if t.name == "MONO"]
        assert [t.index for t in takes] == [1, 3]
        out = tmp_path / "out"
        out.mkdir()
        res = eng.export_takes(part, takes, out, as_wav=True)
        assert sorted(p.name for p in out.iterdir()) == ["MONO (take 3).wav", "MONO.wav"] and len(res.exported) == 2
    finally:
        eng.close()


def test_a_take_that_is_not_there_is_reported_not_raised(dd_engine, tmp_path):
    part = _dd_partition(dd_engine)
    ghost = Take(index=9, name="GHOST", sample_clusters=1, envelope_clusters=1, words=1, rate=44100, stereo=False)
    res = dd_engine.export_takes(part, [ghost], tmp_path, as_wav=True)
    assert res.exported == [] and len(res.failed) == 1 and res.failed[0].startswith("GHOST:")
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ editing (a copy of the image, editing switched on)
@pytest.fixture
def dd_ed(binary, images, tmp_path):
    image = tmp_path / "edit.img"
    shutil.copy(images["dd"], image)
    eng = AkaiEngine(binary)
    eng.open([image])
    eng.set_writable(True)
    yield eng
    eng.close()


def _reread_takes(binary, image):
    """What a fresh, read-only look at the image on disk shows (proves the change really was written)."""
    eng = AkaiEngine(binary)
    eng.open([image])
    try:
        return eng.list_takes(_dd_partition(eng))
    finally:
        eng.close()


def _audio(path, channels=1, rate=44100, seconds=1.0, subtype="PCM_24"):
    n = int(rate * seconds)
    t = np.arange(n) / rate
    data = np.stack([0.5 * np.sin(2 * np.pi * 440 * (c + 1) * t) for c in range(channels)], axis=1)
    soundfile.write(str(path), data, rate, subtype=subtype)
    return path


def _import(eng, part, src_path, tmp_path):
    src = takes_module.inspect(src_path)
    assert not src.problem, src.problem
    local = tmp_path / f"{takes_module.take_name(src_path)}.wav"
    return eng.import_take(part, local, expect=takes_module.prepare_audio(src, local)), local


def test_a_24_bit_stereo_file_becomes_a_take_with_its_audio_intact(dd_ed, binary, tmp_path):
    part = _dd_partition(dd_ed)
    wav = _audio(tmp_path / "pad sound_1.wav", channels=2, rate=48000, seconds=2)
    take, prepared = _import(dd_ed, part, wav, tmp_path)
    assert (take.index, take.name, take.stereo, take.rate, take.frames) == (3, "PAD SOUND 1", True, 48000, 96000)
    out = tmp_path / "out"
    out.mkdir()
    dd_ed.export_takes(part, [take], out, as_wav=True)
    assert _frames(out / "PAD SOUND 1.wav") == _frames(prepared)  # what was written is what is read back, bit for bit
    assert [(t.index, t.name) for t in _reread_takes(binary, dd_ed._session.images[0])] == [(1, "MONO"), (2, "STEREO"), (3, "PAD SOUND 1")]


def test_an_8_bit_file_is_widened_to_16_bit(dd_ed, tmp_path):
    take, _ = _import(dd_ed, _dd_partition(dd_ed), _audio(tmp_path / "LOFI.wav", rate=22050, subtype="PCM_U8"), tmp_path)
    assert (take.name, take.stereo, take.rate, take.frames) == ("LOFI", False, 22050, 22050)


def test_a_tk_file_comes_back_as_the_take_it_was(dd_ed, tmp_path):
    part = _dd_partition(dd_ed)
    mono = dd_ed.list_takes(part)[0]
    dd_ed.export_takes(part, [mono], tmp_path)
    assert dd_ed.delete_takes(part, [mono]) == []
    back = dd_ed.import_take(part, tmp_path / "MONO.TK")
    assert (back.index, back.name, back.stereo, back.rate, back.words) == (1, "MONO", False, 44100, 44100)  # the slot it had is free again


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24", "FLOAT"])
def test_reading_in_blocks_gives_the_same_samples_as_reading_the_whole_file(tmp_path, monkeypatch, subtype):
    wav = _audio(tmp_path / "LONG.wav", channels=2, rate=44100, seconds=1, subtype=subtype)
    src = takes_module.inspect(wav)
    whole = tmp_path / "whole.wav"
    monkeypatch.setattr(takes_module, "READ_BLOCK_FRAMES", 10**7)
    takes_module.prepare_audio(src, whole)
    blocks = tmp_path / "blocks.wav"
    monkeypatch.setattr(takes_module, "READ_BLOCK_FRAMES", 4097)  # not a divisor of 44,100: the last block is short
    takes_module.prepare_audio(src, blocks)
    data, _ = soundfile.read(str(wav), dtype="float64", always_2d=True)
    expected = np.clip(np.round(data * 32767.0), -32768, 32767).astype("<i2")  # what the whole-file version did
    channels, _, raw = _frames(blocks)
    assert channels == 2 and np.array_equal(np.frombuffer(raw, dtype='<i2').reshape(-1, 2), expected) and whole.read_bytes() == blocks.read_bytes()


@pytest.mark.parametrize(
    "rate, target",
    [(44100, 0), (48000, 0), (65535, 0), (88200, 44100), (176400, 44100), (96000, 48000), (192000, 48000), (70000, 48000)],
)
def test_a_rate_a_take_cant_hold_is_converted_to_a_usual_one(rate, target):
    assert takes_module.resample_target(rate) == target


def test_a_96_khz_stereo_file_is_resampled_to_a_take_at_48_khz(dd_ed, tmp_path):
    part = _dd_partition(dd_ed)
    wav = _audio(tmp_path / "FAST.wav", channels=2, rate=96000, seconds=1)
    src = takes_module.inspect(wav)
    assert not src.problem and src.resample_to == 48000  # (akaiutil would have stored 96,000 Hz as 30,464 without a word)
    take, prepared = _import(dd_ed, part, wav, tmp_path)
    assert (take.stereo, take.rate, take.frames) == (True, 48000, 48000)
    out = tmp_path / "out"
    out.mkdir()
    dd_ed.export_takes(part, [take], out, as_wav=True)
    channels, rate, raw = _frames(out / "FAST.wav")
    assert (channels, rate) == (2, 48000) and _frames(prepared) == (channels, rate, raw)
    heard = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype("float64") / 32767.0
    n = np.arange(len(heard)) / 48000
    for c in range(2):  # the tones are where they were: 440 and 880 Hz, at half scale
        assert np.abs(heard[2000:-2000, c] - 0.5 * np.sin(2 * np.pi * 440 * (c + 1) * n[2000:-2000])).max() < 0.01


def test_88_2_khz_goes_to_44_1_khz_and_an_odd_rate_to_about_48_khz(tmp_path):
    exact = takes_module.inspect(_audio(tmp_path / "A.wav", rate=88200, seconds=0.5))
    assert takes_module.prepare_audio(exact, tmp_path / "a.wav") == (1, 44100, 22050)
    odd = takes_module.inspect(_audio(tmp_path / "B.wav", rate=70000, seconds=0.5))
    channels, rate, frames = takes_module.prepare_audio(odd, tmp_path / "b.wav")
    assert channels == 1 and abs(rate - 48000) <= 50 and rate <= takes_module.MAX_RATE and frames == len(_frames(tmp_path / "b.wav")[2]) // 2


def test_a_file_at_a_rate_a_take_holds_is_not_resampled(tmp_path):
    src = takes_module.inspect(_audio(tmp_path / "OK.wav", rate=48000, seconds=0.2))
    assert src.resample_to == 0
    assert takes_module.prepare_audio(src, tmp_path / "ok.wav") == (1, 48000, 9600)


def test_a_file_too_long_to_convert_is_reported_and_the_rest_carry_on(dd_ed, tmp_path, monkeypatch):
    part = _dd_partition(dd_ed)
    big, good = _audio(tmp_path / "BIG.wav", rate=96000, seconds=0.1), _audio(tmp_path / "GOOD.wav", subtype="PCM_16")
    real = takes_module.prepare_audio

    def prepare(src, dest):
        if src.path.name == "BIG.wav":
            raise MemoryError
        return real(src, dest)

    monkeypatch.setattr(takes_module, "prepare_audio", prepare)
    work = tmp_path / "work"
    work.mkdir()
    res = takes_module.run_import(dd_ed, part, [takes_module.inspect(big), takes_module.inspect(good)], work)
    assert res.imported == ["GOOD"] and len(res.failed) == 1 and res.failed[0].startswith("BIG.wav: not enough memory")


def test_what_cannot_be_a_take_is_refused_with_a_reason(tmp_path):
    many = tmp_path / "MANY.wav"
    soundfile.write(str(many), np.zeros((100, 3)), 44100, subtype="PCM_16")
    assert "3 channels" in takes_module.inspect(many).problem
    (tmp_path / "NOTES.txt").write_text("hello")
    assert takes_module.inspect(tmp_path / "NOTES.txt").problem
    (tmp_path / "SMALL.TK").write_bytes(b"x" * 100)
    assert "too small" in takes_module.inspect(tmp_path / "SMALL.TK").problem
    assert takes_module.inspect(_audio(tmp_path / "FINE.wav")).problem == ""


def test_a_file_that_does_not_fit_says_so_and_changes_nothing(dd_ed, binary, tmp_path):
    part = _dd_partition(dd_ed)
    before = dd_ed.list_takes(part)
    huge = _audio(tmp_path / "HUGE.wav", channels=2, seconds=30, subtype="PCM_16")  # 5.3 MB for a partition with 4.8 free
    with pytest.raises(EngineError, match="isn't room for “HUGE”"):
        _import(dd_ed, part, huge, tmp_path)
    assert dd_ed.list_takes(part) == before


def test_a_take_that_comes_out_different_from_the_file_is_taken_out_again(dd_ed, tmp_path):
    part = _dd_partition(dd_ed)
    src = takes_module.inspect(_audio(tmp_path / "ODD.wav", subtype="PCM_16"))
    local = tmp_path / "ODD.wav.prepared.wav"
    takes_module.prepare_audio(src, local)
    with pytest.raises(EngineError, match="differently from the file") as err:
        dd_ed.import_take(part, local, expect=(1, 44100, 12345))
    assert len(str(err.value)) < 100 and "12,345" not in str(err.value)  # a line; the numbers go to the log
    assert [t.name for t in dd_ed.list_takes(part)] == ["MONO", "STEREO"]


def test_the_numbers_of_a_mismatched_take_are_logged(dd_ed, tmp_path, caplog):
    src = takes_module.inspect(_audio(tmp_path / "ODD.wav", subtype="PCM_16"))
    local = tmp_path / "ODD.wav.prepared.wav"
    takes_module.prepare_audio(src, local)
    with caplog.at_level("WARNING"), pytest.raises(EngineError):
        dd_ed.import_take(_dd_partition(dd_ed), local, expect=(1, 44100, 12345))
    assert "12345 frames" in caplog.text and "44100 Hz" in caplog.text


def test_a_failed_import_leaves_nothing_in_the_staging_folder(dd_ed, tmp_path, monkeypatch):
    part = _dd_partition(dd_ed)
    local = _audio(tmp_path / "BOOM.wav", subtype="PCM_16")
    staging = dd_ed._session.staging_dir
    real_run = dd_ed._run

    def run(cmd, *a, **kw):
        if cmd.startswith("wav2take"):
            assert any(staging.iterdir())  # the copy is there while akaiutil runs
            raise EngineError("akaiutil timed out")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(dd_ed, "_run", run)
    with pytest.raises(EngineError, match="timed out"):
        dd_ed.import_take(part, local)
    assert not any(staging.iterdir())
    monkeypatch.undo()
    assert [t.name for t in dd_ed.list_takes(part)] == ["MONO", "STEREO"]


def test_import_run_reports_each_file_and_carries_on(dd_ed, tmp_path):
    part = _dd_partition(dd_ed)
    good = _audio(tmp_path / "GOOD.wav", subtype="PCM_16")
    bad = tmp_path / "EMPTY.wav"
    soundfile.write(str(bad), np.zeros((0, 1)), 44100, subtype="PCM_16")
    work = tmp_path / "work"
    work.mkdir()
    seen = []
    res = takes_module.run_import(dd_ed, part, [takes_module.inspect(bad), takes_module.inspect(good)], work, progress=lambda i, n, name: seen.append((i, n, name)))
    assert res.imported == ["GOOD"] and len(res.failed) == 1 and res.failed[0].startswith("EMPTY.wav:")
    assert seen == [(0, 2, "EMPTY.wav"), (1, 2, "GOOD.wav")]


def test_a_take_is_renamed_by_number_and_duplicates_are_allowed(dd_ed, binary):
    part = _dd_partition(dd_ed)
    mono, stereo = dd_ed.list_takes(part)
    renamed = dd_ed.rename_take(part, stereo, "my pad")
    assert (renamed.index, renamed.name) == (2, "MY PAD")
    twin = dd_ed.rename_take(part, mono, "MY PAD")  # two takes can share a name
    assert [t.name for t in dd_ed.list_takes(part)] == ["MY PAD", "MY PAD"] and twin.index == 1
    with pytest.raises(EngineError, match="isn't take 2 any more"):
        dd_ed.rename_take(part, stereo, "LATE")  # the old name is gone: this is a stale selection
    with pytest.raises(EngineError):
        dd_ed.rename_take(part, twin, "bad*name")
    assert [t.name for t in _reread_takes(binary, dd_ed._session.images[0])] == ["MY PAD", "MY PAD"]


def test_deleting_takes_frees_their_numbers_and_the_next_import_reuses_one(dd_ed, tmp_path, binary):
    part = _dd_partition(dd_ed)
    mono, stereo = dd_ed.list_takes(part)
    free_before = dd_ed._free_bytes(part)
    assert dd_ed.delete_takes(part, [mono]) == []
    assert [(t.index, t.name) for t in dd_ed.list_takes(part)] == [(2, "STEREO")]  # no renumbering
    assert dd_ed._free_bytes(part) == free_before + mono.disk_bytes
    failed = dd_ed.delete_takes(part, [mono])
    assert failed == ["MONO: it isn't take 1 any more"]
    take, _ = _import(dd_ed, part, _audio(tmp_path / "NEW.wav", subtype="PCM_16"), tmp_path)
    assert take.index == 1
    assert [t.name for t in _reread_takes(binary, dd_ed._session.images[0])] == ["NEW", "STEREO"]


def test_takes_cannot_be_changed_while_editing_is_off(binary, images, tmp_path):
    eng = AkaiEngine(binary)
    eng.open([images["dd"]])
    try:
        part = _dd_partition(eng)
        take = eng.list_takes(part)[0]
        for call in (lambda: eng.rename_take(part, take, "NOPE"), lambda: eng.delete_takes(part, [take])):
            with pytest.raises(EngineError, match="Editing is switched off"):
                call()
    finally:
        eng.close()
