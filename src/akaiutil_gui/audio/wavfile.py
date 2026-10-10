"""Writing the one kind of WAV akaiutil's importer is given: 16-bit PCM, with an optional Akai sample header after the data."""

import struct
from pathlib import Path

import numpy as np


def write_wav16(path: str | Path, pcm: np.ndarray, rate: int, header_chunk: tuple[bytes, bytes] | None = None) -> None:
    """Write (frames, channels) int16 audio. `header_chunk` is (chunk id, data), written after the audio: akaiutil reads a
    sample header from there and uses it instead of making a default one."""
    pcm = np.asarray(pcm, dtype="<i2")
    if pcm.ndim == 1:
        pcm = pcm[:, None]
    channels = pcm.shape[1]
    audio = pcm.tobytes()
    extra = b""
    if header_chunk is not None:
        cid, data = header_chunk
        extra = cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2, channels * 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(audio)) + audio + extra
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def read_chunks(path: str | Path) -> list[tuple[bytes, int, int]]:
    """(id, offset of its data, size) of each chunk of a RIFF/WAVE file. Empty if it isn't one."""
    raw = Path(path).read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return []
    chunks, at = [], 12
    while at + 8 <= len(raw):
        size = struct.unpack_from("<I", raw, at + 4)[0]
        if at + 8 + size > len(raw):
            break
        chunks.append((raw[at : at + 4], at + 8, size))
        at += 8 + size + size % 2  # (chunks are padded to an even size)
    return chunks


def sample_rate(path: str | Path) -> int | None:
    raw = Path(path).read_bytes()
    for cid, at, size in read_chunks(path):
        if cid == b"fmt " and size >= 16:
            return struct.unpack_from("<I", raw, at + 4)[0]
    return None


def smpl_chunk(rate: int, root: int, loops: list[tuple[int, int]]) -> bytes:
    """A "smpl" chunk, which DAWs and sample editors read the root note and loops from. A loop's end is its last frame."""
    data = struct.pack("<9I", 0, 0, round(1e9 / rate), root, 0, 0, 0, len(loops), 0)
    for i, (start, end) in enumerate(loops):
        data += struct.pack("<6I", i, 0, start, end, 0, 0)  # forward loop, no fraction, plays forever
    return b"smpl" + struct.pack("<I", len(data)) + data


def append_chunk(path: str | Path, chunk: bytes) -> None:
    """Add a whole chunk (id, size and data) to the end of a WAV file."""
    path = Path(path)
    raw = bytearray(path.read_bytes())
    if len(raw) % 2:
        raw.append(0)
    raw += chunk + (b"\0" if len(chunk) % 2 else b"")
    raw[4:8] = struct.pack("<I", len(raw) - 8)
    path.write_bytes(bytes(raw))


def add_loop_info(path: str | Path) -> bool:
    """Make a WAV that akaiutil exported readable by DAWs: akaiutil keeps the root note and loops in its own header chunk
    (S1H/S3H), which nothing else understands, so write them as a "smpl" chunk too. False if there was nothing to add
    (an S900 sample, a file that already has one, or one that isn't a WAV)."""
    from ..akai_header import CHUNK_IDS, parse_header

    chunks = read_chunks(path)
    ids = {cid for cid, _, _ in chunks}
    if b"smpl" in ids:
        return False
    header_at = next(((at, size) for cid, at, size in chunks if cid in CHUNK_IDS.values()), None)
    rate = sample_rate(path)
    if header_at is None or rate is None:
        return False
    at, size = header_at
    info = parse_header(Path(path).read_bytes()[at : at + size])
    if info is None:
        return False
    append_chunk(path, smpl_chunk(rate, info.root, list(info.loops) if info.looping else []))
    return True
