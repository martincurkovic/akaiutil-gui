"""Loop points, root note, trimming and normalising on the way in."""

import struct
from pathlib import Path

import numpy as np
import pytest
import soundfile

from akaiutil_gui import akai_header, importing, settings
from akaiutil_gui.audio import markers, wavfile
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.importing import ImportOptions


# --------------------------------------------------------------------------- helpers
def tone(freq=440.0, rate=44100, seconds=0.5, amp=0.5, channels=1):
    t = np.arange(int(rate * seconds)) / rate
    wave = amp * np.sin(2 * np.pi * freq * t)
    return wave if channels == 1 else np.stack([wave, wave * 0.5], 1)


def add_chunk(path: Path, cid: bytes, data: bytes) -> None:
    raw = bytearray(path.read_bytes())
    raw += cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
    raw[4:8] = struct.pack("<I", len(raw) - 8)
    path.write_bytes(bytes(raw))


def smpl_chunk(root: int, loops: list[tuple[int, int]]) -> bytes:
    head = struct.pack("<IIIIIIIII", 0, 0, 22675, root, 0, 0, 0, len(loops), 0)
    return head + b"".join(struct.pack("<IIIIII", i, 0, s, e, 0, 0) for i, (s, e) in enumerate(loops))


def wav_with_smpl(path: Path, data, rate=44100, root=62, loops=((1000, 5000),), subtype="PCM_16") -> Path:
    soundfile.write(str(path), data, rate, subtype=subtype)
    add_chunk(path, b"smpl", smpl_chunk(root, list(loops)))
    return path


@pytest.fixture
def audio(tmp_path):
    folder = tmp_path / "audio"
    folder.mkdir()
    return folder


# ------------------------------------------------------------------------ reading markers
def test_a_wavs_smpl_chunk_gives_its_root_note_and_loops(audio):
    path = wav_with_smpl(audio / "a.wav", tone(), root=62, loops=[(1000, 5000), (6000, 9000)])
    m = markers.read_markers(path)
    assert (m.root, m.loops, m.has_akai_header) == (62, ((1000, 5000), (6000, 9000)), False)
    assert soundfile.info(str(path)).frames == 22050  # (and the file is still a perfectly good WAV)


def test_a_wav_without_markers_says_nothing(audio):
    soundfile.write(str(audio / "plain.wav"), tone(), 44100, subtype="PCM_16")
    assert markers.read_markers(audio / "plain.wav") == markers.Markers()


def test_a_loop_that_ends_where_it_starts_is_ignored_and_note_zero_means_unset(audio):
    path = wav_with_smpl(audio / "a.wav", tone(), root=0, loops=[(500, 500), (100, 900)])
    m = markers.read_markers(path)
    assert m.root is None and m.loops == ((100, 900),)


def test_at_most_eight_loops_are_read(audio):
    path = wav_with_smpl(audio / "a.wav", tone(), loops=[(i * 100, i * 100 + 50) for i in range(1, 12)])
    assert len(markers.read_markers(path).loops) == 8


def test_a_wav_exported_by_the_app_is_recognised_by_its_akai_header(audio):
    pcm = (tone() * 32767).astype("<i2")
    wavfile.write_wav16(audio / "x.wav", pcm, 44100, (b"S3H ", akai_header.build_header("S3000", 44100, len(pcm))))
    assert markers.read_markers(audio / "x.wav").has_akai_header


def test_an_aiffs_root_note_is_read_from_its_inst_chunk(audio):
    inst = bytes([64, 0, 0, 127, 1, 127]) + bytes(14)
    comm = struct.pack(">hIh", 1, 100, 16) + bytes(10)
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + b"INST" + struct.pack(">I", len(inst)) + inst
    (audio / "x.aiff").write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    assert markers.read_markers(audio / "x.aiff").root == 64


@pytest.mark.parametrize("name", ["missing.wav", "notaudio.txt"])
def test_unreadable_files_have_no_markers(audio, name):
    (audio / "notaudio.txt").write_text("hello")
    assert markers.read_markers(audio / name) == markers.Markers()


@pytest.mark.parametrize(
    "stem, note",
    [("Piano_C3", 60), ("pad c#2", 49), ("bass-Bb1", 46), ("Lead A4", 81), ("low C-2", 0), ("hi G8", 127), ("c3", 60), ("x.E3", 64)],
)
def test_a_name_ending_in_a_note(stem, note):
    assert markers.note_from_name(stem) == note


@pytest.mark.parametrize("stem", ["kick", "Kick 1", "PianoC3", "Strings_H3", "snare_A", "tom_G9", "pad C#", "808"])
def test_names_that_dont_end_in_a_note(stem):
    assert markers.note_from_name(stem) is None


def test_note_names_follow_the_akai_convention():
    assert [markers.note_name(n) for n in (0, 60, 61, 69, 127)] == ["C-2", "C3", "C#3", "A3", "G8"]
    assert all(markers.note_from_name(f"x {markers.note_name(n)}") == n for n in range(128))


# ------------------------------------------------------------------------------ headers
def test_a_header_has_everything_in_its_place():
    h = akai_header.build_header("S3000", 44100, 20000, root=62, loops=[(1000, 5000), (6000, 9000)])
    assert len(h) == 192 and h[0] == 3 and h[1] == 1 and h[2] == 62 and h[15] == 0x80
    assert h[16] == 2 and h[17] == 0 and h[19] == 0  # two loops, the first active, play mode LOOP
    assert struct.unpack("<I", h[34:38])[0] == 19999  # the end marker is the last frame
    assert struct.unpack("<I", h[38:42])[0] == 5000 and struct.unpack("<I", h[44:48])[0] == 4000 and struct.unpack("<H", h[48:50])[0] == 9999
    assert struct.unpack("<I", h[50:54])[0] == 9000 and struct.unpack("<I", h[56:60])[0] == 3000
    assert struct.unpack("<H", h[136:138])[0] == 0xFFFF and struct.unpack("<H", h[138:140])[0] == 44100
    assert len(akai_header.build_header("S1000", 22050, 10)) == 150


def test_loops_outside_the_sample_are_left_out_of_a_header():
    h = akai_header.build_header("S3000", 44100, 1000, loops=[(100, 900), (500, 5000), (900, 100), (-5, 10)])
    assert h[16] == 1 and struct.unpack("<I", h[38:42])[0] == 900


def test_a_header_holds_eight_loops_at_most():
    assert akai_header.build_header("S3000", 44100, 100000, loops=[(i * 100, i * 100 + 50) for i in range(1, 12)])[16] == 8


def test_the_wav_writer_makes_a_file_soundfile_and_the_header_search_can_read(audio):
    pcm = (tone(channels=2) * 32767).astype("<i2")
    header = (b"S3H ", akai_header.build_header("S3000", 44100, len(pcm)))
    wavfile.write_wav16(audio / "w.wav", pcm, 44100, header)
    info = soundfile.info(str(audio / "w.wav"))
    assert (info.channels, info.samplerate, info.subtype, info.frames) == (2, 44100, "PCM_16", len(pcm))
    raw = (audio / "w.wav").read_bytes()
    assert raw[:4] == b"RIFF" and struct.unpack("<I", raw[4:8])[0] == len(raw) - 8
    assert raw.endswith(header[1]) and b"S3H " in raw[44 + pcm.nbytes - 8 :]  # after the audio


# ------------------------------------------------------------------------------- trimming
def test_trim_cuts_silence_from_both_ends():
    data = np.zeros((1000, 1))
    data[300:700, 0] = 0.5
    trimmed, loops = importing.trim_silence(data, [])
    assert trimmed.shape == (400, 1) and loops == []


def test_trim_never_cuts_into_a_loop_and_moves_the_loops():
    data = np.zeros((1000, 1))
    data[300:700, 0] = 0.5
    trimmed, loops = importing.trim_silence(data, [(100, 800)])
    assert trimmed.shape == (701, 1) and loops == [(0, 700)]  # the loop reaches back to 100 and out to 800
    trimmed, loops = importing.trim_silence(data, [(350, 650)])
    assert trimmed.shape == (400, 1) and loops == [(50, 350)]


def test_all_silence_is_left_alone():
    data = np.zeros((500, 2))
    assert importing.trim_silence(data, [])[0].shape == (500, 2)


# ------------------------------------------------------------------- what will be done
def test_the_steps_include_the_new_options(audio):
    path = wav_with_smpl(audio / "Pad_C3.wav", tone(), root=60, loops=[(100, 900)])
    src = importing.inspect(path)
    assert importing.conversion_steps(src, "S3000", options=ImportOptions(file_markers=False)) == []
    assert importing.conversion_steps(src, "S3000") == ["sets root key C3 and 1 loop"]
    assert importing.conversion_steps(src, "S3000", options=ImportOptions(trim=True, normalize=True)) == [
        "silence trimmed from the ends", "normalised to -1 dB", "sets root key C3 and 1 loop",
    ]
    assert not any(step.startswith("sets") for step in importing.conversion_steps(src, "S900"))  # an S900 sample has no loops here


def test_the_root_from_the_name_is_only_used_if_the_file_doesnt_say(audio):
    plain = importing.inspect(_wav(audio / "Keys_D3.wav"))
    assert importing.sample_markers(plain, "S3000", ImportOptions()) == (None, ())
    assert importing.sample_markers(plain, "S3000", ImportOptions(root_from_name=True)) == (62, ())
    stored = importing.inspect(wav_with_smpl(audio / "Keys_D3 b.wav", tone(), root=40, loops=[]))
    assert importing.sample_markers(stored, "S3000", ImportOptions(root_from_name=True))[0] == 40


def _wav(path, data=None, rate=44100, subtype="PCM_16"):
    soundfile.write(str(path), tone() if data is None else data, rate, subtype=subtype)
    return path


def test_the_dialog_line_mentions_what_is_set(audio):
    src = importing.inspect(wav_with_smpl(audio / "a.wav", tone(), root=60, loops=[(100, 900), (1000, 2000)]))
    assert importing.describe(src, "S3000") == "WAV · 44.1 kHz · 16-bit · mono  →  sets root key C3 and 2 loops"
    exported = audio / "x.wav"
    wavfile.write_wav16(exported, (tone() * 32767).astype("<i2"), 44100, (b"S3H ", akai_header.build_header("S3000", 44100, 22050)))
    assert importing.describe(importing.inspect(exported), "S3000", options=ImportOptions(trim=True)).endswith("keeps its Akai sample header")


# ---------------------------------------------------------------------------- converting
def test_loops_move_with_the_audio_when_it_is_resampled(audio):
    path = wav_with_smpl(audio / "hi.wav", tone(rate=48000), 48000, root=60, loops=[(4800, 9600)], subtype="PCM_24")
    out = audio / "out.wav"
    importing.prepare_audio(importing.inspect(path), out, "S3000")
    raw = out.read_bytes()
    at = raw.rfind(b"S3H ")
    header = raw[at + 8 : at + 8 + 192]
    assert struct.unpack("<I", header[38:42])[0] == pytest.approx(9600 * 44100 / 48000, abs=1)
    assert struct.unpack("<I", header[44:48])[0] == pytest.approx(4800 * 44100 / 48000, abs=1)
    assert struct.unpack("<H", header[138:140])[0] == 44100


def test_trimming_and_normalising_happen_in_the_written_file(audio):
    data = np.zeros(20000)
    data[5000:15000] = 0.2 * np.sin(2 * np.pi * 440 * np.arange(10000) / 44100)
    path = _wav(audio / "quiet.wav", data)
    out = audio / "out.wav"
    importing.prepare_audio(importing.inspect(path), out, "S3000", options=ImportOptions(trim=True, normalize=True))
    pcm, _ = soundfile.read(str(out), dtype="float64")
    assert len(pcm) == pytest.approx(10000, abs=60)  # (the quiet fringe of the first and last cycles goes too)
    assert np.abs(pcm).max() == pytest.approx(10 ** (-1 / 20), abs=0.001)


def test_a_loop_survives_a_trim_and_lands_on_the_same_audio(audio):
    data = np.zeros(20000)
    data[5000:15000] = 0.3
    path = wav_with_smpl(audio / "p.wav", data, root=60, loops=[(8000, 12000)])
    out = audio / "out.wav"
    importing.prepare_audio(importing.inspect(path), out, "S3000", options=ImportOptions(trim=True))
    raw = out.read_bytes()
    header = raw[raw.rfind(b"S3H ") + 8 :][:192]
    assert struct.unpack("<I", header[38:42])[0] == 12000 - 5000 and struct.unpack("<I", header[44:48])[0] == 4000


def test_a_stereo_file_keeps_both_channels_and_one_header(audio):
    path = wav_with_smpl(audio / "s.wav", tone(channels=2), root=60, loops=[(100, 900)])
    out = audio / "out.wav"
    channels, rate = importing.prepare_audio(importing.inspect(path), out, "S3000")
    assert (channels, rate) == (2, 44100) and out.read_bytes().count(b"S3H ") == 1


def test_a_wav_from_the_app_goes_through_untouched_whatever_the_options(audio):
    pcm = (tone() * 32767).astype("<i2")
    exported = audio / "x.wav"
    wavfile.write_wav16(exported, pcm, 44100, (b"S3H ", akai_header.build_header("S3000", 44100, len(pcm), root=70, loops=[(100, 900)])))
    out = audio / "out.wav"
    importing.prepare_audio(importing.inspect(exported), out, "S3000", options=ImportOptions(trim=True, normalize=True, root_from_name=True))
    assert out.read_bytes() == exported.read_bytes()


def test_nothing_is_added_for_an_s900_sample(audio):
    path = wav_with_smpl(audio / "a.wav", tone(rate=40000), 40000, root=60, loops=[(100, 900)])
    out = audio / "out.wav"
    importing.prepare_audio(importing.inspect(path), out, "S900")
    assert b"S3H " not in out.read_bytes() and b"S1H " not in out.read_bytes()


# --------------------------------------------------------------------- with akaiutil
@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def volume(eng):
    return eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])[1]  # BASS


def info_of(eng, vol, name):
    return eng.sample_info(vol, next(f for f in eng.list_files(vol) if f.name == name))


def run(eng, vol, paths, tmp_path, sample_type="S3000", options=ImportOptions(), **kw):
    sources = [importing.inspect(p) for p in paths]
    return importing.run_import(eng, vol, sources, tmp_path / "work", sample_type, options=options, **kw)


def test_the_sampler_gets_the_root_note_and_loops_from_the_wav(ed, audio, tmp_path):
    vol = volume(ed)
    path = wav_with_smpl(audio / "pad.wav", tone(), root=62, loops=[(1000, 5000)])
    assert run(ed, vol, [path], tmp_path).imported == ["PAD.S3"]
    info = info_of(ed, vol, "PAD.S3")
    assert info.root_key == 62 and info.frames == 22050 and info.rate == 44100
    assert [(l.slot, l.end, l.length, l.time_ms) for l in info.loops] == [(1, 5000, 4000, None)]  # a HOLD loop
    assert info.has_loop_to_preview


def test_two_loops_arrive_in_their_slots(ed, audio, tmp_path):
    vol = volume(ed)
    run(ed, vol, [wav_with_smpl(audio / "two.wav", tone(), loops=[(1000, 5000), (6000, 9000)])], tmp_path)
    assert [(l.slot, l.end, l.length) for l in info_of(ed, vol, "TWO.S3").loops] == [(1, 5000, 4000), (2, 9000, 3000)]


def test_with_the_option_off_the_markers_are_ignored(ed, audio, tmp_path):
    vol = volume(ed)
    path = wav_with_smpl(audio / "pad.wav", tone(), root=62, loops=[(1000, 5000)])
    run(ed, vol, [path], tmp_path, options=ImportOptions(file_markers=False))
    info = info_of(ed, vol, "PAD.S3")
    assert info.root_key == 60 and info.loops == ()


def test_the_root_note_can_come_from_the_name(ed, audio, tmp_path):
    vol = volume(ed)
    run(ed, vol, [_wav(audio / "Rhodes_E3.wav")], tmp_path, options=ImportOptions(root_from_name=True))
    run(ed, vol, [_wav(audio / "Rhodes_E3 b.wav")], tmp_path)
    assert info_of(ed, vol, "RHODES E3.S3").root_key == 64
    assert info_of(ed, vol, "RHODES E3 B.S3").root_key == 60


def test_an_s1000_sample_gets_them_too(ed, audio, tmp_path):
    vol = volume(ed)
    run(ed, vol, [wav_with_smpl(audio / "old.wav", tone(), root=48, loops=[(500, 3000)])], tmp_path, sample_type="S1000")
    info = info_of(ed, vol, "OLD.S1")
    assert info.root_key == 48 and [(l.end, l.length) for l in info.loops] == [(3000, 2500)]


def test_both_halves_of_a_stereo_pair_get_the_loop(ed, audio, tmp_path):
    vol = volume(ed)
    run(ed, vol, [wav_with_smpl(audio / "st.wav", tone(channels=2), root=55, loops=[(100, 2000)])], tmp_path)
    for name in importing.final_names("ST", ".S3", 2, 12):
        info = info_of(ed, vol, name)
        assert info.root_key == 55 and [(l.end, l.length) for l in info.loops] == [(2000, 1900)]


def test_a_resampled_loop_still_loops_the_same_audio(ed, audio, tmp_path):
    vol = volume(ed)
    run(ed, vol, [wav_with_smpl(audio / "hi.wav", tone(rate=96000), 96000, loops=[(9600, 40000)], subtype="PCM_24")], tmp_path)
    info = info_of(ed, vol, "HI.S3")
    assert info.rate == 44100
    assert [(l.end, l.length) for l in info.loops] == [(pytest.approx(40000 * 44100 / 96000, abs=1), pytest.approx(30400 * 44100 / 96000, abs=1))]


def test_trim_and_normalise_arrive_in_the_sample(ed, audio, tmp_path):
    vol = volume(ed)
    data = np.zeros(30000)
    data[10000:20000] = 0.25 * np.sin(2 * np.pi * 330 * np.arange(10000) / 44100)
    run(ed, vol, [_wav(audio / "quiet.wav", data)], tmp_path, options=ImportOptions(trim=True, normalize=True))
    f = next(f for f in ed.list_files(vol) if f.name == "QUIET.S3")
    out = tmp_path / "back"
    out.mkdir()
    pcm, _ = soundfile.read(str(ed.export_sample_wav(vol, f, out)), dtype="float64")
    assert len(pcm) == pytest.approx(10000, abs=80) and np.abs(pcm).max() == pytest.approx(10 ** (-1 / 20), abs=0.001)


def test_a_wav_exported_by_the_app_keeps_everything_through_the_new_pipeline(binary, editable_hd, tmp_path):
    import imagegen

    imagegen.add_loop(editable_hd, "KICK", loop_end=4000, loop_length=1000, time_ms=None)
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    try:
        drums, bass = eng.list_volumes(eng.list_partitions(eng.list_disks()[0])[0])[:2]
        kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
        before = eng.sample_info(drums, kick)
        out = tmp_path / "wav"
        out.mkdir()
        wav = eng.export_sample_wav(drums, kick, out)
        options = ImportOptions(trim=True, normalize=True, root_from_name=True)
        assert importing.run_import(eng, bass, [importing.inspect(wav)], tmp_path / "w", "S3000", options=options).imported == ["KICK.S3"]
        after = info_of(eng, bass, "KICK.S3")
        assert [(l.end, l.length) for l in after.loops] == [(l.end, l.length) for l in before.loops] and after.frames == before.frames
    finally:
        eng.close()


# ----------------------------------------------------------------------- the dialog
def test_the_dialog_offers_the_options_and_remembers_them(qt_app, audio):
    from akaiutil_gui.engine.models import Volume
    from akaiutil_gui.ui import edit_dialogs

    vol = Volume(0, "A", 1, "DRUMS", "-", "", "", "S3000")
    sources = [importing.inspect(wav_with_smpl(audio / "a.wav", tone(), root=60, loops=[(100, 900)]))]
    d = edit_dialogs.ImportDialog(None, vol, sources)
    d.show()
    assert d.options == ImportOptions(file_markers=True, root_from_name=False, trim=False, normalize=False)  # the defaults
    assert d.file_markers.isVisible() and d.trim.isVisible()
    assert d.files.item(0).text().endswith("sets root key C3 and 1 loop")
    d.trim.setChecked(True)
    d.normalize.setChecked(True)
    d.stereo_combo.setCurrentIndex(1)
    assert "silence trimmed from the ends, normalised to -1 dB, sets root key C3 and 1 loop" in d.files.item(0).text()
    d.accept()
    assert settings.get_import_flags() == {"file_markers": True, "root_from_name": False, "trim": True, "normalize": True}
    assert settings.get_import_stereo_mix() is True
    again = edit_dialogs.ImportDialog(None, vol, sources)  # next time they start where they were left
    assert again.options.trim and again.options.normalize and again.stereo == importing.STEREO_MIX


def test_the_options_arent_shown_when_only_akai_files_are_dropped(qt_app, audio):
    from akaiutil_gui.engine.models import Volume
    from akaiutil_gui.ui import edit_dialogs

    akai = audio / "KICK.S3"
    akai.write_bytes(b"\x03" + bytes(300))
    d = edit_dialogs.ImportDialog(None, Volume(0, "A", 1, "DRUMS", "-", "", "", "S3000"), [importing.inspect(akai)])
    d.show()
    assert not any(box.isVisible() for box in d.option_boxes)
