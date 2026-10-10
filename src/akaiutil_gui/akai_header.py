"""Building an S1000/S3000 sample header (Qt-free): what a sample's loops and root note are stored in.

Layout from `struct akai_sample1000_s` in akaiutil's akaiutil_file.h (150 bytes; the S3000's is the same plus 42 bytes of zeros):
  0 blockid(3)  1 bandw  2 rkey  3..14 name  15 0x80  16 lnum  17 lfirst  19 pmode  20 ctune  21 stune  22..25 locat
  26..29 slen  30..33 start  34..37 end  38.. 8 loops of 12 bytes (at, flen, len, time)  136..137 stpaira  138..139 srate
  140 hltoff
akaiutil itself writes the name and the number of samples, and uses the rest as given when the WAV carries this header.
"""

import struct

LOOP_HOLD = 9999  # a loop time meaning "until the key is released"
MAX_LOOPS = 8
CHUNK_IDS = {"S1000": b"S1H ", "S3000": b"S3H "}


def build_header(sample_type: str, rate: int, frames: int, root: int = 60, loops: list[tuple[int, int]] = ()) -> bytes:
    """The header for a sample of `frames` frames at `rate`, with a root note and loops given as (first, last) frames."""
    size = 192 if sample_type == "S3000" else 150
    h = bytearray(size)
    h[0] = 3  # blockid
    h[1] = 1  # 20 kHz bandwidth
    h[2] = root
    h[3:15] = bytes([10] * 12)  # (the name: a blank one, akaiutil fills it in)
    h[15] = 0x80
    usable = [(s, e) for s, e in loops if 0 <= s < e < frames][:MAX_LOOPS]
    h[16] = len(usable)  # lnum
    h[17] = 0  # lfirst
    h[19] = 0  # pmode: loop
    h[30:34] = struct.pack("<I", 0)  # start
    h[34:38] = struct.pack("<I", max(frames - 1, 0))  # end
    for i, (start, end) in enumerate(usable):
        at = 38 + 12 * i
        h[at : at + 4] = struct.pack("<I", end)  # the loop's last frame
        h[at + 4 : at + 6] = struct.pack("<H", 0)
        h[at + 6 : at + 10] = struct.pack("<I", end - start)  # how far back it starts
        h[at + 10 : at + 12] = struct.pack("<H", LOOP_HOLD)
    h[136:138] = struct.pack("<H", 0xFFFF)  # stpaira: no stereo partner
    h[138:140] = struct.pack("<H", min(rate, 0xFFFF))
    return bytes(h)
