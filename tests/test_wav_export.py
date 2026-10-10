"""Exported WAVs carry the root note and loops in a "smpl" chunk, which DAWs read (akaiutil's own header chunk they don't)."""

import struct

import numpy as np
import pytest

from akaiutil_gui import akai_header, importing
from akaiutil_gui.audio import markers, wavfile
from akaiutil_gui.engine.api import AkaiEngine


def akaiutil_style_wav(path, header: bytes, rate=44100, frames=5000):
    """A WAV the way akaiutil writes one: fmt, data, then its header chunk."""
    wavfile.write_wav16(path, np.zeros((frames, 1), dtype=np.int16), rate, header_chunk=(b"S3H ", header))


def test_a_header_reads_back_what_was_written():
    header = bytearray(akai_header.build_header("S3000", 44100, 5000, root=48, loops=[(100, 900), (1200, 3000)]))
    header[26:30] = struct.pack("<I", 5000)  # (build_header leaves the length to akaiutil, which fills it in on import)
    info = akai_header.parse_header(bytes(header))
    assert (info.root, info.rate, info.frames, info.looping) == (48, 44100, 5000, True)
    assert info.loops == ((100, 900), (1200, 3000))


def test_loops_that_are_off_or_ignored_by_the_play_mode_are_not_reported():
    header = bytearray(akai_header.build_header("S3000", 44100, 5000, root=60, loops=[(100, 900)]))
    header[26:30] = struct.pack("<I", 5000)
    header[19] = 2  # NOLOOP: the loop is stored, but the sample doesn't use it
    info = akai_header.parse_header(bytes(header))
    assert not info.looping and info.loops == ((100, 900),)  # (the caller decides what to do with them)
    header[19] = 0
    header[38 + 10 : 38 + 12] = struct.pack("<H", 0)  # loop time 0: that loop is switched off
    assert akai_header.parse_header(bytes(header)).loops == ()


def test_a_header_that_is_not_one_is_refused():
    assert akai_header.parse_header(b"\0" * 10) is None
    assert akai_header.parse_header(b"\x07" + b"\0" * 200) is None


def test_the_smpl_chunk_is_what_the_importer_reads_back(tmp_path):
    path = tmp_path / "a.wav"
    wavfile.write_wav16(path, np.zeros((5000, 1), dtype=np.int16), 44100)
    wavfile.append_chunk(path, wavfile.smpl_chunk(44100, 60, [(10, 20), (300, 4000)]))
    assert markers.read_markers(path) == markers.Markers(60, ((10, 20), (300, 4000)), False)
    raw = path.read_bytes()
    assert struct.unpack("<I", raw[4:8])[0] == len(raw) - 8  # the RIFF size was kept right
    assert [c for c, _, _ in wavfile.read_chunks(path)] == [b"fmt ", b"data", b"smpl"]


def test_a_wav_with_an_odd_sized_chunk_is_padded_before_the_next(tmp_path):
    path = tmp_path / "odd.wav"
    wavfile.write_wav16(path, np.zeros((5, 1), dtype=np.int16), 44100)
    wavfile.append_chunk(path, b"junk" + struct.pack("<I", 3) + b"abc")
    wavfile.append_chunk(path, wavfile.smpl_chunk(44100, 60, []))
    assert [c for c, _, _ in wavfile.read_chunks(path)] == [b"fmt ", b"data", b"junk", b"smpl"]


# ---------------------------------------------------------------------- with the real akaiutil
@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def volumes(eng):
    part = next(p for p in eng.list_partitions(eng.list_disks()[0]) if p.name == "A")
    return eng.list_volumes(part)


def test_an_exported_wav_has_the_samples_loops_and_root_note_for_a_daw(binary, editable_hd, tmp_path):
    import imagegen

    imagegen.add_loop(editable_hd, "KICK", loop_end=4000, loop_length=1000, time_ms=None)
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        drums = volumes(eng)[0]
        kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
        info = eng.sample_info(drums, kick)
        eng.export_files(drums, [kick], tmp_path, as_wav=True)
    finally:
        eng.close()
    found = markers.read_markers(tmp_path / "KICK.wav")
    assert found.has_akai_header  # (still there, so it comes back into a sampler as it was)
    assert found.loops == ((3000, 4000),)
    assert found.root == info.root_key
    assert [c for c, _, _ in wavfile.read_chunks(tmp_path / "KICK.wav")] == [b"fmt ", b"data", b"S3H ", b"smpl"]


def test_a_sample_without_loops_gets_a_root_note_and_no_loops(binary, editable_hd, tmp_path):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        drums = volumes(eng)[0]
        snare = next(f for f in eng.list_files(drums) if f.name == "SNARE.S3")
        eng.export_files(drums, [snare], tmp_path, as_wav=True)
    finally:
        eng.close()
    found = markers.read_markers(tmp_path / "SNARE.wav")
    assert found.loops == () and found.root is not None


def test_an_exported_raw_file_is_left_alone(binary, editable_hd, tmp_path):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    try:
        drums = volumes(eng)[0]
        kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
        before = eng.export_files(drums, [kick], tmp_path, as_wav=False)
    finally:
        eng.close()
    assert before.exported and (tmp_path / "KICK.S3").exists()


def test_the_wav_with_smpl_goes_back_into_a_sampler_unchanged(binary, editable_hd, tmp_path):
    import imagegen

    imagegen.add_loop(editable_hd, "KICK", loop_end=4000, loop_length=1000, time_ms=None)
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    try:
        drums, bass = volumes(eng)[0], volumes(eng)[1]
        kick = next(f for f in eng.list_files(drums) if f.name == "KICK.S3")
        before = eng.sample_info(drums, kick)
        eng.export_files(drums, [kick], tmp_path, as_wav=True)
        result = importing.run_import(eng, bass, [importing.inspect(tmp_path / "KICK.wav")], tmp_path / "w", "S3000")
        assert result.imported == ["KICK.S3"]
        after = eng.sample_info(bass, next(f for f in eng.list_files(bass) if f.name == "KICK.S3"))
        assert [(l.end, l.length) for l in after.loops] == [(l.end, l.length) for l in before.loops]
        assert after.root_key == before.root_key
    finally:
        eng.close()
