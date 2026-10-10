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
