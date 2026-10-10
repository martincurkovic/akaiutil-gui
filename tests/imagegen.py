"""Generate synthetic Akai disk images with the real akaiutil binary.

Used by the integration tests, and handy for trying the GUI before you have
real disk images:

    uv run python tests/imagegen.py build/images

writes s3000_hd.img, s1000_floppy.img and s900_floppy.img there.
"""

import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from akaiutil_gui.engine.locate import find_akaiutil  # noqa: E402


def write_sine_wav(path: Path, freq: float, seconds: float = 0.5, rate: int = 44100) -> None:
    n = int(seconds * rate)
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def _script(binary: Path, image: Path, commands: list[str], cwd: Path) -> str:
    """Run commands in a *writable* akaiutil session (the GUI itself never does)."""
    p = subprocess.run(
        [str(Path(binary).resolve()), str(Path(image).resolve())],
        input="\n".join([*commands, "exit"]) + "\n",
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
    )
    return p.stdout + p.stderr


def make_images(out_dir: Path, binary: Path | None = None) -> dict[str, Path]:
    binary = binary or find_akaiutil()
    out_dir = Path(out_dir).resolve()
    if binary is None:
        raise RuntimeError("akaiutil not found - run scripts/get_akaiutil.py first")
    out_dir.mkdir(parents=True, exist_ok=True)
    wavs = out_dir / "wavs"
    wavs.mkdir(exist_ok=True)
    for name, freq in [("KICK", 60), ("SNARE", 200), ("BASS", 55), ("LEAD", 440)]:
        write_sine_wav(wavs / f"{name}.wav", freq)

    images = {
        "hd": out_dir / "s3000_hd.img",
        "fl1": out_dir / "s1000_floppy.img",
        "fl9": out_dir / "s900_floppy.img",
    }
    images["hd"].write_bytes(bytes(8192 * 20000))  # 160000 KB
    images["fl1"].write_bytes(bytes(1024 * 1600))  # high-density floppy
    images["fl9"].write_bytes(bytes(1024 * 800))  # low-density floppy

    # formatting makes akaiutil restart itself, so it gets its own session
    _script(binary, images["hd"], ["formatharddisk3 5M"], wavs)
    _script(
        binary,
        images["hd"],
        [
            "cd /disk0/A",
            "mkvol3 DRUMS",
            "mkvol3 BASS",
            "mkvol3 MY_SYNTH",  # '_' stands in for ' ' on the command line
            "cd DRUMS",
            "wav2sample3 KICK.wav",
            "wav2sample3 SNARE.wav",
            "cd ../BASS",
            "wav2sample3 BASS.wav",
            "cd ../MY_SYNTH",
            "wav2sample3 LEAD.wav",
        ],
        wavs,
    )

    _script(binary, images["fl1"], ["formatfloppyh1"], wavs)
    _script(binary, images["fl1"], ["cd /disk0/A", "cdi 1", "wav2sample1 KICK.wav", "wav2sample1 SNARE.wav"], wavs)

    _script(binary, images["fl9"], ["formatfloppyl9"], wavs)
    _script(binary, images["fl9"], ["cd /disk0/A", "cdi 1", "wav2sample9 KICK.wav"], wavs)
    return images


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else REPO / "build" / "images")
    for key, path in make_images(target).items():
        print(f"{key:4} {path}")


_AKAI_CHARS = "0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ#+-."  # an Akai name byte is an index into this


def add_loop(image: Path, sample: str, loop_end: int, loop_length: int, time_ms: int | None = None, slot: int = 0,
             hold_tune: int = 0, play_mode: int = 0) -> None:
    """Write a loop into an S1000/S3000 sample's header, in place (akaiutil can't set loops itself).

    Layout from `struct akai_sample1000_s` in akaiutil_file.h: lnum at +16, lfirst +17, pmode +19, loop[8] from
    +38 (12 bytes each: at[4], flen[2], len[4], time[2]), hltoff +140. time_ms None means HOLD (9999).
    """
    name = bytes(_AKAI_CHARS.index(c) for c in sample.upper().ljust(12))
    data = bytearray(Path(image).read_bytes())
    at = bytes(data).find(b"\x03\x01\x3c" + name + b"\x80")
    if at < 0:
        raise RuntimeError(f"no S1000/S3000 sample header named {sample!r} in {image}")
    time_value = 9999 if time_ms is None else time_ms
    data[at + 16] = slot + 1  # lnum
    data[at + 17] = slot  # lfirst
    data[at + 19] = play_mode  # 0 LOOP, 1 LOOPNOTREL, 2 NOLOOP, 3 TOEND
    loop = at + 38 + 12 * slot
    data[loop : loop + 12] = (
        loop_end.to_bytes(4, "little") + (0).to_bytes(2, "little") + loop_length.to_bytes(4, "little") + time_value.to_bytes(2, "little")
    )
    data[at + 140] = hold_tune & 0xFF
    Path(image).write_bytes(bytes(data))
