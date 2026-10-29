"""THROWAWAY prototype, not product code: Akai floppy HFE v1 (MFM) <-> flat image.

Naive on purpose (a track side is a str of '0'/'1' cells) so it is easy to read and check against the HxC spec. See hfe-plan.md.
Usage:
    python3 -I hfe_prototype.py decode  disk.hfe  out.img
    python3 -I hfe_prototype.py selftest disk.hfe     # decode, then check the encoders against the file itself
Only HD (10 sectors of 1024 bytes) has been seen so far; DD values below are guesses and marked.
"""

import binascii
import struct
import sys

SYNC_A1 = "0100010010001001"  # 0xA1 with its clock bit missing (0x4489)
SYNC_C2 = "0101001000100100"  # 0xC2 with its clock bit missing (0x5224)
SPT = 10  # HD sectors per track (DD: 5)
SECTOR = 1024
SIDE_BYTES = 25168  # HD: bytes per side in the track (track_len = 2x); 12584 MFM bytes of cells. DD value unknown.
BLOCKS = 99  # 512-byte blocks per track in the file
PAD = bytes([0x49, 0x2A])  # what the unused tail of the last block holds in the samples

# gaps, as measured on the Acrobatics HD samples (see measure_layout)
GAP4A, GAP1, GAP2, GAP3 = 80, 50, 22, 139


def crc16(data, crc=0xFFFF):
    return binascii.crc_hqx(data, crc)


# ---- MFM ------------------------------------------------------------------------------------------------------------------
_ENC = {(p, b): None for p in (0, 1) for b in range(256)}
for (_p, _b) in list(_ENC):
    _c, _prev = [], _p
    for _i in range(7, -1, -1):
        _d = (_b >> _i) & 1
        _c.append("1" if (_prev == 0 and _d == 0) else "0")
        _c.append(str(_d))
        _prev = _d
    _ENC[(_p, _b)] = "".join(_c)


def mfm_encode(data, prev=0):
    """Bytes -> cells (clock, data, clock, data, ...), `prev` is the last data bit before the first byte."""
    out = []
    for b in data:
        out.append(_ENC[(prev, b)])
        prev = b & 1
    return "".join(out)


def mfm_decode(cells, pos, n):
    out = bytearray()
    for i in range(n):
        seg = cells[pos + 16 * i : pos + 16 * i + 16]
        if len(seg) < 16:
            return None
        out.append(int(seg[1::2], 2))
    return bytes(out)


# ---- HFE container ----------------------------------------------------------------------------------------------------
def header(d):
    sig, rev, ntr, nsd, enc, br, rpm, mode, dnu, lut, wa, ss = struct.unpack_from("<8sBBBBHHBBHBB", d, 0)
    if sig != b"HXCPICFE":
        raise ValueError(f"not an HFE v1 file ({sig!r})")
    return dict(tracks=ntr, sides=nsd, enc=enc, bitrate=br, lut=lut)


def track_table(d, h):
    return [struct.unpack_from("<HH", d, h["lut"] * 512 + 4 * t) for t in range(h["tracks"])]


def side_cells(d, off, tlen, side):
    """The cells of one side of a track: 256 bytes of each side per 512-byte block, bits LSB first."""
    raw = bytearray()
    for blk in range(0, (tlen + 511) // 512 * 512, 512):
        raw += d[off * 512 + blk + side * 256 : off * 512 + blk + side * 256 + 256]
    return "".join(format(b, "08b")[::-1] for b in raw)


def put_side_cells(buf, off, tlen, side, cells):
    raw = bytes(int(cells[i : i + 8][::-1], 2) for i in range(0, len(cells), 8))
    for k, blk in enumerate(range(0, (tlen + 511) // 512 * 512, 512)):
        buf[off * 512 + blk + side * 256 : off * 512 + blk + side * 256 + 256] = raw[k * 256 : k * 256 + 256]


# ---- decode -----------------------------------------------------------------------------------------------------------
def find_sectors(cells):
    """{(C,H,R,N): (data bytes, cell index of the first data cell)} plus problem counts."""
    secs, bad, pos, cur = {}, 0, 0, None
    while True:
        p = cells.find(SYNC_A1 * 3, pos)
        if p < 0:
            break
        q = p + 48
        mark = mfm_decode(cells, q, 1)
        if not mark:
            break
        if mark[0] == 0xFE:
            h = mfm_decode(cells, q + 16, 6)
            ok = h and crc16(b"\xa1\xa1\xa1\xfe" + h[:4]) == (h[4] << 8 | h[5])
            cur = tuple(h[:4]) if ok else None
            bad += not ok
        elif mark[0] == 0xFB and cur:
            body = mfm_decode(cells, q + 16, SECTOR + 2)
            if body and crc16(b"\xa1\xa1\xa1\xfb" + body[:SECTOR]) == (body[SECTOR] << 8 | body[SECTOR + 1]):
                secs[cur] = (body[:SECTOR], q + 16)
            else:
                bad += 1
            cur = None
        pos = q
    return secs, bad


def decode(d):
    h = header(d)
    img, bad = {}, 0
    for t, (off, ln) in enumerate(track_table(d, h)):
        for s in range(h["sides"]):
            secs, b = find_sectors(side_cells(d, off, ln, s))
            bad += b
            for (c, hd, r, n), (data, _) in secs.items():
                img[(t, s, r)] = data
    missing = h["tracks"] * h["sides"] * SPT - len(img)
    flat = b"".join(img.get((t, s, r), b"\0" * SECTOR) for t in range(h["tracks"]) for s in range(h["sides"]) for r in range(1, SPT + 1))
    return flat, bad, missing


# ---- encode: patch an existing file in place ----------------------------------------------------------------------------
def patch(d, flat):
    """A copy of HFE file `d` with every sector's data (and its CRC) replaced by the flat image's. All else is kept bit for bit."""
    h = header(d)
    out = bytearray(d)
    for t, (off, ln) in enumerate(track_table(d, h)):
        for s in range(h["sides"]):
            cells = side_cells(d, off, ln, s)
            secs, _ = find_sectors(cells)
            lst = list(cells)
            for (c, hd, r, n), (old, start) in secs.items():
                new = flat[((t * h["sides"] + s) * SPT + r - 1) * SECTOR :][:SECTOR]
                body = new + struct.pack(">H", crc16(b"\xa1\xa1\xa1\xfb" + new))
                enc = mfm_encode(body, prev=1)  # the data mark 0xFB ends in a 1
                end = start + len(enc)
                lst[start:end] = enc
                # the clock cell of the byte after the CRC depends on the CRC's last bit
                nxt = int(cells[end + 1])
                lst[end] = "1" if (int(enc[-1]) == 0 and nxt == 0) else "0"
            put_side_cells(out, off, ln, s, "".join(lst))
    return bytes(out)


# ---- encode: a fresh file ------------------------------------------------------------------------------------------------
def sector_order(t):
    first = 1 if t % 2 == 0 else 6  # as seen on the HD samples; unverified for other disks
    return [(first - 1 + i) % SPT + 1 for i in range(SPT)]


def fresh_side(flat, t, s, nsides=2):
    cells = [mfm_encode(b"\x4e" * GAP4A + b"\x00" * 12)]
    cells.append(SYNC_C2 * 3)
    cells.append(mfm_encode(b"\xfc", prev=0))
    cells.append(mfm_encode(b"\x4e" * GAP1, prev=0))
    for r in sector_order(t):
        idf = bytes([t, s, r, 3])
        cells.append(mfm_encode(b"\x00" * 12, prev=0))
        cells.append(SYNC_A1 * 3)
        cells.append(mfm_encode(idf + struct.pack(">H", crc16(b"\xa1\xa1\xa1\xfe" + idf)), prev=0) if False else "")
        cells.append(mfm_encode(b"\xfe" + idf + struct.pack(">H", crc16(b"\xa1\xa1\xa1\xfe" + idf)), prev=1))  # (A1 ends in 1)
        cells.append(mfm_encode(b"\x4e" * GAP2 + b"\x00" * 12, prev=0))
        cells.append(SYNC_A1 * 3)
        data = flat[((t * nsides + s) * SPT + r - 1) * SECTOR :][:SECTOR]
        cells.append(mfm_encode(b"\xfb" + data + struct.pack(">H", crc16(b"\xa1\xa1\xa1\xfb" + data)), prev=1))
        cells.append(mfm_encode(b"\x4e" * GAP3, prev=int(cells[-1][-1])))
    used = sum(len(c) for c in cells) // 16
    cells.append(mfm_encode(b"\x4e" * (SIDE_BYTES // 2 - used), prev=int(cells[-1][-1])))
    return "".join(cells)


def fresh(flat, template_header=None):
    """A new HFE v1 file from a flat HD image. Header fields copied from the samples (HD, 500 kbit/s)."""
    hdr = bytearray(b"\xff" * 512)
    hdr[0:20] = struct.pack("<8sBBBBHHBBH", b"HXCPICFE", 0, 80, 2, 0, 500, 0, 1, 1, 1)
    hdr[20:22] = b"\xff\xff"
    out = bytearray(hdr) + bytearray(512 * (1 + 80 * BLOCKS))
    for t in range(80):
        off = 2 + t * BLOCKS
        struct.pack_into("<HH", out, 512 + 4 * t, off, SIDE_BYTES * 2)
        for s in (0, 1):
            cells = fresh_side(flat, t, s)
            raw = bytearray(int(cells[i : i + 8][::-1], 2) for i in range(0, len(cells), 8))
            padn = BLOCKS * 256 - len(raw)
            raw += bytes(PAD[i % 2] for i in range(padn))
            for k in range(BLOCKS):
                out[off * 512 + k * 512 + s * 256 : off * 512 + k * 512 + s * 256 + 256] = raw[k * 256 : (k + 1) * 256]
    return bytes(out)


# ---- self test --------------------------------------------------------------------------------------------------------------
def measure_layout(d):
    """Gap sizes read off track 0 side 0 of a file, to compare with the constants above."""
    h = header(d)
    off, ln = track_table(d, h)[0]
    cells = side_cells(d, off, ln, 0)
    sy, i = [], 0
    while (i := cells.find(SYNC_A1 * 3, i)) >= 0:  # in cells: the sampler's own writes need not sit on a byte boundary
        sy.append(i)
        i += 48
    idam = [i for i in sy if mfm_decode(cells, i + 48, 1) == b"\xfe"]
    dam = [i for i in sy if mfm_decode(cells, i + 48, 1) == b"\xfb"]
    print("  A1 syncs at cell offset mod 16:", sorted({i % 16 for i in sy}), "| IDAMs", len(idam), "DAMs", len(dam))
    print("  bytes: gap2 =", (dam[0] - idam[0]) // 16 - 10 - 12, "| gap3 =", (idam[1] - dam[0]) // 16 - 4 - SECTOR - 2 - 12,
          "| tail after last data =", SIDE_BYTES // 2 - (dam[-1] // 16 + 4 + SECTOR + 2), "| first IDAM at byte", idam[0] / 16)


def selftest(path):
    d = open(path, "rb").read()
    flat, bad, missing = decode(d)
    print(f"decode: {len(flat)} bytes, bad {bad}, missing {missing}")
    measure_layout(d)
    p = patch(d, flat)
    print("patch with unchanged data == original file:", p == d, "" if p == d else f"({sum(a != b for a, b in zip(p, d))} bytes differ)")
    mod = bytearray(flat)
    for i, pos in enumerate((0, 683 * SECTOR, 1599 * SECTOR)):  # first, a middle and the last sector
        mod[pos : pos + 1024] = bytes((pos + k * 7 + i) & 0xFF for k in range(1024))
    p2 = patch(d, bytes(mod))
    f2, bad2, miss2 = decode(p2)
    diff_sectors = sum(1 for k in range(0, len(flat), 1024) if f2[k : k + 1024] != flat[k : k + 1024])
    print(f"patch with 3 changed sectors: decodes to the new image: {f2 == bytes(mod)}, bad {bad2}, missing {miss2}, "
          f"sectors differing from original {diff_sectors}, file bytes differing {sum(a != b for a, b in zip(p2, d))}")
    f = fresh(flat)
    ff, badf, missf = decode(f)
    print(f"fresh encode: size {len(f)} (original {len(d)}), decodes to the same image: {ff == flat}, bad {badf}, missing {missf}")
    print("fresh == original file, byte for byte:", f == d, "" if f == d else f"({sum(a != b for a, b in zip(f, d))} of {len(d)} bytes differ)")


if __name__ == "__main__":
    if sys.argv[1] == "decode":
        flat, bad, missing = decode(open(sys.argv[2], "rb").read())
        open(sys.argv[3], "wb").write(flat)
        print(f"{len(flat)} bytes, bad {bad}, missing {missing}")
    else:
        selftest(sys.argv[2])
