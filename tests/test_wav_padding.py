"""akaiutil 4.7 reads WAV files with odd-sized chunks (a chunk is padded to an even size). 4.6.8 refused or misread them.

These drive akaiutil itself, the way tests/imagegen.py does. The app writes clean WAVs of its own (audio/wavfile.py), so none of this
is needed to import audio through the window; it records what akaiutil does with other people's files."""

import math
import shutil
import struct

import pytest

from akaiutil_gui import akai_header
from akaiutil_gui.engine import locate

import imagegen


def chunk(chunk_id: bytes, data: bytes) -> bytes:
    return chunk_id + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


ODD_LIST = chunk(b"LIST", b"INFOabc")  # 7 bytes: odd


def write_wav(path, bits=16, frames=1000, channels=1, before=b"", after=b""):
    if bits == 8:
        data = bytes(128 + int(100 * math.sin(i / 8 + k)) for i in range(frames) for k in range(channels))
    else:
        data = b"".join(struct.pack("<h", int(10000 * math.sin(i / 8 + k))) for i in range(frames) for k in range(channels))
    fmt = struct.pack("<HHIIHH", 1, channels, 22050, 22050 * bits // 8 * channels, bits // 8 * channels, bits)
    body = b"WAVE" + chunk(b"fmt ", fmt) + before + chunk(b"data", data) + after
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


@pytest.fixture
def needs_4_7(binary):
    version = locate.parse_version(locate.binary_version(binary))
    if version is None or version < (4, 7):
        pytest.skip("akaiutil 4.7 is the first version that handles odd-sized WAV chunks")


@pytest.fixture
def work(binary, editable_hd, tmp_path):
    """A folder to run akaiutil in, and a function that imports a WAV into DRUMS and returns the new sample's bytes (or None)."""

    def import_wav(wav_name: str):
        said = imagegen._script(binary, editable_hd, ["cd /disk0/A", "cdi 1", f"wav2sample {wav_name}", "dir"], tmp_path)
        if "sample imported from WAV" not in said:
            return None, said
        imagegen._script(binary, editable_hd, ["cd /disk0/A", "cdi 1", "geti 3"], tmp_path)  # (the third file: the new one)
        (exported,) = tmp_path.glob("*.S3")
        return exported.read_bytes(), said

    return tmp_path, import_wav


def test_an_odd_sized_chunk_before_the_audio_is_read(needs_4_7, work):
    folder, import_wav = work
    write_wav(folder / "ODDBEFORE.wav", before=ODD_LIST)
    sample, said = import_wav("ODDBEFORE.wav")
    assert sample is not None, said
    assert len(sample) == 192 + 2 * 1000  # the whole audio, as 16-bit


def test_an_odd_sized_audio_chunk_followed_by_a_sample_header_keeps_the_loop(needs_4_7, work):
    """8-bit audio of an odd length is padded; the S3H chunk after it was read from the wrong place, so its loop was lost."""
    folder, import_wav = work
    header = akai_header.build_header("S3000", 22050, 1001, root=60, loops=[(100, 400)])
    write_wav(folder / "ODD8.wav", bits=8, frames=1001, after=chunk(b"S3H ", header))
    sample, said = import_wav("ODD8.wav")
    assert sample is not None, said
    settings = akai_header.read_settings(sample[:192])
    assert settings.root == 60
    assert [(loop.start, loop.end) for loop in settings.loops if loop.enabled] == [(100, 400)]


def test_a_stereo_file_with_an_odd_chunk_before_the_audio_is_read(needs_4_7, work):
    folder, import_wav = work
    write_wav(folder / "STODD.wav", channels=2, before=ODD_LIST)
    sample, said = import_wav("STODD.wav")
    assert sample is not None, said


def test_an_odd_sized_chunk_before_a_takes_audio_is_read(needs_4_7, binary, images, tmp_path):
    dd = tmp_path / "dd.img"
    shutil.copy(images["dd"], dd)
    write_wav(tmp_path / "TKODD.wav", frames=20000, before=ODD_LIST)
    said = imagegen._script(binary, dd, ["cd /disk0/DD", "wav2take TKODD.wav", "dir"], tmp_path)
    assert "invalid file size" not in said and "TKODD" in said


def test_what_akaiutil_exports_has_even_chunks_and_a_right_size(binary, images, tmp_path):
    """take2wav: fmt, data, a 64-byte header chunk, and a RIFF size that is the file's size minus 8 (checked on both versions)."""
    dd = tmp_path / "dd.img"
    shutil.copy(images["dd"], dd)
    imagegen._script(binary, dd, ["cd /disk0/DD", "take2wavall"], tmp_path)
    exported = sorted(tmp_path.glob("*.wav")) + sorted(tmp_path.glob("*.WAV"))
    assert exported
    for path in exported:
        raw = path.read_bytes()
        assert raw[:4] == b"RIFF" and struct.unpack_from("<I", raw, 4)[0] == len(raw) - 8
        at, ids = 12, []
        while at + 8 <= len(raw):
            size = struct.unpack_from("<I", raw, at + 4)[0]
            ids.append(raw[at : at + 4])
            at += 8 + size + size % 2
        assert at == len(raw) and ids[:2] == [b"fmt ", b"data"]
