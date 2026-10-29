# OS boot floppies (`.COD` files)

Status 2026-10-29: **built (`os_floppy.py`, File > Create OS Boot Floppy…) from a reading of OSMaker's code; not tried on a sampler.**
akaiutil reads the result as a volume `OS DISK` (17.00) with `OS FILE.C`, 262,144 bytes, block 0x11, tag 10 (tests/test_os_floppy.py);
the HFE encoder round-trips it. Both a `.COD` and a saved `.C` are accepted; a `.C` has no model in its name, so the model and version
come from the `s20V0200.COD` build name inside it (found in both the `.COD` and the `.C` here; other models' files not seen).
The file is named a clean `OS FILE`, not OSMaker's `OS FILE0 000`. Dropping a `.COD` into a disk is not handled (it goes in as `.COD.X01`).

## What was measured

Source: `OSMaker.exe` v1.00 (Akai, 1996-97, 20 KB DOS program) and `S200200.COD`, both from the "S2000 - OS 2.0 (OSMaker, Paul)" folder.
OSMaker was disassembled (Capstone, 16-bit), **not run**. Offsets are in the MZ image.

- **`S200200.COD` is the S2000 OS 2.00.** 262,144 bytes. Strings: `AKAI EMIS2000 SAMPLER 2.00`, `VERSION 2.00`, `s20V0200.COD`.
  It starts with `eb 4e` (jump to the reset code at 0x50).
- **A `.COD` is not byte-identical to the `.C` an S2000 saves.** Same size, same code (nothing differs from 0xFE40 to 0x2D950), but
  18,988 bytes differ: the vector table at the start, and the RAM variable/buffer area (0x6000-0xF000). The `.C` is a RAM dump.
  Not checked: that the `.COD`'s RAM areas are blank, or that Load System accepts a `.COD` as it is.
- **OSMaker knows eight models**, picked from the **file name** (not its contents): `S28`, `S30`, `S32`, `C30`, `C31`, `S20`, `S31`, `S35`
  = S2800, S3000, S3200, CD3000, CD3000XL, S2000, S3000XL, S3200XL. Name characters 4-5 are the major version, 6-7 the minor
  (`S200200` = 2.00), an optional 8th digit is a beta number.
- **The disk it writes is an ordinary Akai S3000-style HD floppy**, the layout akaiutil's `formatfloppyh3` makes:
  - Format: 80 cylinders x 2 heads, 10 sectors of 1024 bytes, sector ids 1..10 in order (no interleave).
  - Header: 64 directory entries of 24 bytes from block 0 (here filled with zeros, not akaiutil's spaces; first entry's type byte 0xFF),
    the chain table from byte 1536 (entries 0-16 = 0x4000, the file's 256 blocks chained from 0x11, last entry 0xC000),
    the volume label at byte 4736.
  - **Volume label:** `OS DISK`, then 2 zero bytes, minor version, major version + offset, then 10 bytes `00 00 01 00 00 00 32 09 0C 00`
    (akaiutil writes `00 01 01 00 00 00 32 09 0C FF`; two bytes differ).
  - **One directory entry** (block 5 onwards): name, then 4 tag bytes `00 00 00 <model>`, type `0x63` (`c`, "S1000 operating system", the
    same type the S2000's own saved `MULTI FILE.C` has), size `00 00 04` (= 0x40000), start block 0x11, then the OS version.
  - **Model byte:** S28 0x00, S30 0x01, S32 0x02, C30 0x04, C31 0x08, S20 0x0A, S31 0x0B, S35 0x0C.
  - **OS version bytes:** minor, then major + 15 for S20/S31/S35/C31 and + 10 for S28/S30/S32/C30. S2000 2.00 is therefore 17.00,
    which is what akaiutil's `put` gives too.
  - **The file name** is converted from a string that, as stored, is `OS FILE`, TAB, space, NUL, `il`; by my reading of the converter
    (unknown characters become `0`) that makes the name `OS FILE0 000`. Probably a bug in OSMaker; the sampler is unlikely to care.

## What akaiutil already gives (scratch test)

`formatfloppyh3`, then `put OS_FILE.C`, gave a floppy with the file at block 0x11, type "S1000 operating system", 17.00. Missing against
OSMaker: the label (`renvol` refuses a floppy: "must be a volume"), the model tag byte, and the 2 header parameter bytes.

## Not known

- Whether an S2000 boots from a floppy made this way (a real OSMaker disk image would settle it).
- Whether the model byte, the label or the file name matter to the sampler.
- Whether the S2000 wants the `.COD` or the saved `.C` form.
