# Plan: open and save `.hfe` floppy images

Written 2026-10-29. Nothing here is built yet. Facts marked **(verify)** come from memory of the HxC format and have not been
checked against a spec or a real file; the rest was read from this repo or from `akaiutil-4.6.8/`.

## Why a conversion

akaiutil 4.6.8 has no HFE support (nothing in its source mentions HFE, HxC or any track-level format). It treats a file as a flat
run of 1024-byte blocks and guesses the disk type from the size. An `.hfe` file is the HxC container: a header, a track table and
the raw MFM *bitstream* of every track. Opened as it is, it comes back as a floppy or HD9 with 0 partitions and `MainWindow._scanned`
closes it. (`.hfe` is already in `importing._IMAGE_EXTENSIONS` and the open dialog's filter, which only lets the file be picked.)

So the GUI has to turn an HFE into a flat image akaiutil can open, and turn it back after edits. akaiutil stays untouched.

## What the flat image has to look like

Read from `akaiutil_io.c` / `akaiutil_io.h` (the Windows raw-floppy code spells out the layout the flat image mirrors):

| | low density (`FLL`) | high density (`FLH`) |
|---|---|---|
| flat image size | 800 KB (`0x320` blocks = 819,200 bytes) | 1.6 MB (`0x640` blocks = 1,638,400 bytes) |
| cylinders x sides | 80 x 2 | 80 x 2 |
| sectors per track | 5 | 10 |
| sector size | 1024 (size code 3) | 1024 |
| sector numbers | 1..5 | 1..10 |
| gap3 when formatting | `0x74` | `0x74` |
| sector offset / track skew | 3 / 2 | 6 / 4 |

- **Block order in the flat file:** `cyl = blk / (2 * spt)`, `head = (blk / spt) & 1`, `sec = 1 + blk % spt`. So cylinder by
  cylinder, side 0 then side 1, sectors in logical order. (`testing_images/S1000 floppy HD, 1.6 MB.img` is 1,638,400 bytes, as expected.)
- **On the track the sectors are not in logical order.** Formatting puts sector number
  `1 + ((offset + pos + cyl * (spt - skew)) % spt)` at physical position `pos`. A decoder must therefore read each sector's ID field
  (C, H, R, N) and place the data by R, never by position. An encoder that wants the disk to behave like one akaiutil/the sampler
  formatted has to reproduce that skew (a plain 1..N order can read slowly on the sampler).
- The sampler's floppy drive is MFM: 250 kbit/s for DD, 500 kbit/s for HD, 300 RPM **(verify against the S900/S1000 service notes
  that DD disks are written 300 RPM, not 360)**.

## The HFE side **(verify all of this against the HxC format spec before coding)**

- **v1** (`HXCPICFE`): 512-byte header: signature, revision, number of tracks, number of sides, track encoding, bit rate (kbit/s),
  RPM, interface mode, offset of the track table (in 512-byte blocks), write-allowed, single-step, and four alt-encoding bytes for
  track 0. Then a table of `{offset (blocks), length (bytes)}` per track, then the track data.
- Track data is stored in 512-byte blocks that alternate **256 bytes of side 0, 256 bytes of side 1**, with the bits of each byte
  **LSB first**. What is stored is the encoded MFM cells (clock and data bits), not decoded bytes, so there is a decoding step.
- **v3** (`HXCHFEV3`) adds opcodes inside the bitstream (index marks, bit-rate changes, skipped bits, ...), so a v3 reader needs an
  opcode pass before the MFM pass. Gotek-style firmware reads both. Plan: write v1 (most widely read); read v1 first and refuse v3
  with a clear message until v3 is implemented (or implement v3 in the same step if the sample files turn out to be v3).
- Other `.hfe`-like files to refuse cleanly: anything that is not MFM (`track_encoding`), or not 80 cylinders / 2 sides.

## Library research (2026-10-29, from web searches only: no source was read, nothing was installed)

**Start here:** the user asked for both read and write. Before writing any codec, spend about an hour on option 1 below.

1. **Greaseweazle host tools** (Python, <https://github.com/keirf/greaseweazle/>). Its `gw convert` converts between image formats
   and lists `.hfe` among them; release notes say it reads and writes HFEv3, and an earlier one mentions fixing HFE v1 FM data
   rates. The HFE code, the MFM sector codecs and the disk layouts (`diskdefs.cfg`, where I believe Akai 800K/1.6M entries exist)
   are probably the reusable parts. **Not checked:**
   - the licence (README says "freely redistributable in any form"; I believe it is the Unlicense, but read the LICENSE file);
   - whether it is on PyPI or GitHub-only (its install wiki points at GitHub), and its dependencies;
   - whether the HFE code can be imported and called as a library, or is only the CLI's internals with no stable API
     (copy the few modules we need, with the licence, if so);
   - whether it bundles with `pyside6-deploy`/Nuitka on all three platforms (BUILDING.md);
   - whether its Akai layouts match the geometry in the table above (a cross-check of that table, too).
2. **libhxcfe / `hxcfe` CLI** (HxC's own, C). Not installed here; would have to be bundled per platform like akaiutil
   (`engine/fetch.py`, three build scripts, CI smoke test). I could not find its licence; the one hit was a GPL header on a tool
   built on it, which does not settle it. Treat as unusable until checked.
3. **Pure Python from the spec.** HxC publishes the format: <https://hxc2001.com/floppy_drive_emulator/HFE-file-format.html>
   (also as PDF), currently v3.1 (v3.0, 2017, redefined the opcodes; v3.1, 2019, added weak bits). Per those pages the opcodes
   exist only in v2/v3 and `HXCHFEV3` is the v3 signature, as assumed above. The spec is very short, so use working code to check
   details: MAME's reader (BSD-3-Clause) <https://git.redump.net/mame/tree/src/lib/formats/hxchfe_dsk.cpp> and the Rust
   `fluxfox` <https://docs.rs/crate/fluxfox/latest/source/src/file_parsers/hfe.rs>. The Akai case is narrow (one sector size,
   MFM only, known geometry), so this is realistic: a few hundred lines, Qt-free, testable like `backup.py`.

Other Python libraries found were unrelated (`machfs` is Macintosh HFS volumes, not HFE).

## Decision (not final): try Greaseweazle's code, else pure Python

If Greaseweazle's licence, API and bundling check out, reuse it; otherwise write it from the spec. Both live behind the same
`hfe.py` interface below, so the app side of the plan doesn't depend on the choice. Whatever is chosen, the codec must be
checked against files made by other software (the user's samples), because round-trip tests alone can't catch a mistake the
encoder and decoder share.

Proposed module: `src/akaiutil_gui/hfe.py` (Qt-free), keyed on the file's **signature**, not its extension:

```text
detect(path) -> "hfe" | None                       # HXCPICFE / HXCHFEV3 magic, cheap, used by importing.disk_image_problem
read_hfe(path) -> FloppyImage                      # flat bytes + geometry + a report (see below)
write_hfe(image, path, template=None)              # fresh encode, or patch `template` in place
```

- **Decode** (`read_hfe`): unpack the interleaved bits, find the sync marks (`A1 A1 A1` with the missing clock) and the ID
  address mark, read C/H/R/N and check the header CRC, find the data mark, read 1024 bytes and check the data CRC (CRC-16/CCITT
  seeded with the sync bytes). Place by R. Result is a flat 819,200 or 1,638,400 byte image, plus a report: sectors found, bad
  CRC, missing, duplicates.
- **Density** is read off the file (bit rate / track length), then confirmed by the sector count (5 -> DD, 10 -> HD). Anything else
  is "not an Akai floppy" and is refused with that reason, like other non-disk files.
- **Bad or missing sectors:** don't make a plausible-looking image out of a damaged one silently. Default is to refuse and say
  which tracks; a later option can open it read-only with the gaps zero-filled and a warning.
- **Encode, two ways:**
  1. *Patch in place* (the save path for an HFE that was opened): keep the original file's header, bit rate, track layout, gaps
     and skew exactly, and only rewrite each sector's 1024 data bytes plus its CRC where they differ. Because MFM clock bits
     depend on the neighbouring data bits, re-encode just that data field and fix the one clock bit on each side. Least change,
     so least risk of the sampler or Gotek disliking the result.
  2. *Fresh encode* (for "Save as HFE" from a flat image): build every track the way akaiutil formats it (gap3 `0x74`, the
     offset/skew above), with a header matching HxC's usual output for 80x2 MFM.
- Speed: 160 tracks of roughly 100,000 cells each. Plain Python loops could be slow; use `bytes.translate` / lookup tables (or
  numpy, already a dependency for audio) and measure. Target well under a few seconds so it can run in the worker thread
  (`ui/tasks.py`) with the existing "Opening..." status.

## How it fits the app

The flat image is a **working copy**; the `.hfe` stays the file the user thinks they are editing.

1. **Open** (`MainWindow.open_images`): for each path whose signature is HFE, decode into a flat `.img` in the app's data folder
   (same area `backup.py` uses, one subfolder per source, cleaned up on close and on startup) and hand *that* path to
   akaiutil. `AkaiEngine` and the parsers never see HFE. Window title, recent list and status line must show the original name,
   so `_images` needs a "display path" vs "working path" split (`_disk_files` is built from the banner's `diskN: "path"` lines,
   which will name the working copy).
2. **Read-only by default**, as for every image. Nothing is written back to the `.hfe` while read-only.
3. **Editing:** the backup question (`backup.py`) is asked about the **original `.hfe`**, not the working copy: that is what gets
   overwritten. akaiutil writes the working copy after every command (see akaiutil-notes.md), so the working copy is always
   current.
4. **Write-back** to the `.hfe`: when editing is switched off, the image is closed, or the app quits, and also as an explicit
   *File > Save as HFE...* (also usable for any flat image, using the fresh encode). Write to a temp file next to the target, read
   it back through `read_hfe`, compare with the working copy, and only then replace the original. A failed check leaves the
   original alone and says why.
5. **Dirty tracking:** compare the working copy's hash with the one taken at decode time, so an unedited session doesn't
   rewrite the user's file (and doesn't touch its modification time).
6. **Open as a pair with other images** works the same: each HFE is converted independently.

Failure messages written for the user, in the style of `disk_image_problem`: not an Akai disk (wrong geometry / not MFM),
damaged (tracks and sectors named), HFE v3 not supported yet.

## Test plan

- Qt-free unit tests, in the style of the existing ones (the real akaiutil via `tests/conftest.py`, temp folders).
- **Round trip:** flat image -> `write_hfe` (fresh) -> `read_hfe` -> identical bytes, for a DD and an HD image
  (`testing_images/` has an HD floppy; a DD one can be made with `format`).
- **Patch in place:** change one file with akaiutil on the working copy, patch the HFE, decode again, `dir` shows the change and
  every untouched sector is byte-identical in the HFE's bitstream (not just after decoding).
- **Damage:** flip a bit in a sector's data and in an ID field; expect a refusal naming the track and side. Truncated file,
  zero tracks, wrong signature, v3 file.
- The encoder and decoder must not just agree with each other. Cross-check against a file made by software that isn't ours (see
  verification debt), since a mirrored mistake in both would pass every round-trip test.

## Verification debt (add to the roadmap's list when this is built)

- [ ] **A real HFE of an Akai floppy** (from the HxC software or a Gotek/FlashFloppy setup) must decode to the same blocks as a
      raw image of that disk. Needed both as a fixture and to confirm the layout points marked **(verify)**.
- [ ] **A written HFE on real hardware** (Gotek or HxC feeding an S900/S1000/S3000): loads, lists, plays. Checks the skew, gaps,
      bit rate and header fields.
- [ ] Whether the people using HFE here need **v3** files.

## State of play

- The user who asked for this (a feature request, not the maintainer) is being asked for sample `.hfe` files; the maintainer
  knew nothing about HFE beforehand and wants **both reading and writing**. Samples had not arrived when this was written.
- When they do: put one under `testing_images/` (or `tests/` fixtures if small), note which tool made it and its version byte
  (`HXCPICFE` = v1, `HXCHFEV3` = v3) here, and use it to settle every **(verify)** above.
- Nothing in `src/` has changed for this. The only edits so far are these notes, the roadmap entry and the README index.

## Open questions for the user

- Which HFE producers are in play (HxC software, a Gotek with FlashFloppy, Greaseweazle)? It decides v1 vs v3 and whether
  write-back must preserve more of the header.
- Is it enough that *Save* writes back into the same `.hfe` on closing/leaving edit mode, or is an explicit "Save" per session
  better? (The plan above does both.)
- If the sampler is fed by a Gotek running FlashFloppy: as far as I remember, that firmware can read Akai `.img` files directly
  with an Akai host setting, which would make HFE unnecessary there. **(verify before spending the time)**.

## Out of scope

Other container formats (IMD, SCP, flux dumps, `.fdi`, ...). The detect-by-signature structure leaves room for them, but each is
its own reader. Non-MFM or copy-protected disks. Anything with more than 80 cylinders or two sides.
