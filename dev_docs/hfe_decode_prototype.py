"""Throwaway prototype: HFE v1 (MFM) -> flat image. Not product code."""
import struct, sys, collections
def crc16(data, crc=0xFFFF):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc
def side_cells(d, base, tlen, side):
    out = bytearray()
    for blk in range(0, tlen, 512):
        out += d[base + blk + side*256: base + blk + side*256 + 256]
    # LSB-first bits
    return ''.join(format(b, '08b')[::-1] for b in out)
SYNC = '0100010010001001'
def mfm_bytes(cells, pos, n):
    out = bytearray()
    for i in range(n):
        seg = cells[pos + 16*i: pos + 16*i + 16]
        if len(seg) < 16: return None
        out.append(int(seg[1::2], 2))
    return bytes(out)
def decode_track(cells):
    secs = {}; rep = collections.Counter(); pos = 0; cur = None
    while True:
        p = cells.find(SYNC + SYNC + SYNC, pos)
        if p < 0: break
        q = p + 48
        mark = mfm_bytes(cells, q, 1)
        if not mark: break
        m = mark[0]
        if m == 0xFE:
            h = mfm_bytes(cells, q + 16, 6)
            if h and crc16(b'\xa1\xa1\xa1\xfe' + h[:4]) == (h[4] << 8 | h[5]): cur = tuple(h[:4])
            else: rep['bad_id_crc'] += 1; cur = None
        elif m == 0xFB and cur:
            body = mfm_bytes(cells, q + 16, 1024 + 2)
            if body and crc16(b'\xa1\xa1\xa1\xfb' + body[:1024]) == (body[1024] << 8 | body[1025]):
                secs[cur] = body[:1024]
            else: rep['bad_data_crc'] += 1
            cur = None
        pos = q
    return secs, rep, 
if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    _, _, ntr, nsd, *_ = struct.unpack_from('<8sBBBBHHBBHBB', d, 0)
    lut = struct.unpack_from('<H', d, 0x12)[0]
    img = {}; order0 = None; issues = collections.Counter(); spt = set(); ids = set()
    for t in range(ntr):
        off, ln = struct.unpack_from('<HH', d, lut*512 + 4*t)
        for s in range(nsd):
            secs, rep = decode_track(side_cells(d, off*512, ln, s))
            issues.update(rep); spt.add(len(secs))
            if t in (0, 1) and s == 0: print('track', t, 'side', s, 'sector order (physical):', [k for k in secs][:12])
            for (c, h, r, n), data in secs.items():
                ids.add((c == t, h == s, n))
                img[(t, s, r)] = data
    print('sectors per track seen:', spt, 'issues:', dict(issues), 'id consistency (C==trk,H==side,N):', ids)
    nspt = max(spt)
    out = bytearray()
    for t in range(ntr):
        for s in range(nsd):
            for r in range(1, nspt + 1):
                out += img.get((t, s, r), b'\0' * 1024)
    print('image bytes', len(out), 'missing', ntr*nsd*nspt - len(img))
    open(sys.argv[2], 'wb').write(out)
