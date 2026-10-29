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

## Decision: our own `hfe.py` (done, 2026-10-29)

Written in pure Python from the spec and the sample files; Greaseweazle and `hxcfe` are not dependencies (see "Greaseweazle,
what was found"). The module is `src/akaiutil_gui/hfe.py` (Qt-free), tests in `tests/test_hfe.py`. Its API, keyed on the file's
**signature**, not its extension:

```text
is_hfe(path) -> bool                      # HXCPICFE / HXCHFEV3 magic: cheap, for importing.disk_image_problem and the open flow
decode(data, strict=True) -> Decoded      # .image (flat bytes), .sectors_per_track, .density, .problems
read(path, strict=True) -> Decoded
patch(original, image) -> bytes           # change only the sectors that differ, everything else bit for bit
encode(image) -> bytes                    # a new HD file from a flat image (HD only: no DD sample to copy the layout from)
save(path, image)                         # patch (or encode if there is no file), decode the result and compare, then replace
HfeError                                  # every refusal, with a sentence for the user
```

The design notes that led to it follow; the module docstring says what it does today.

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

## First real samples (2026-10-29)

The maintainer downloaded six zips of Acrobatics' "AKAI S3000XL HFE Programs" (Vol 1-6) from the web into
`~/Downloads/Akai HFE Images/`, 20 `.HFE` disks each. They are a commercial sound library, so **they are not in the repo and
should not be added**. Two (`Alpha_DX.HFE`, `Sitar.HFE` from Vol 1) were unpacked into a scratch folder and checked with
a throwaway Python prototype (naive string-based MFM decode and encode; it became `src/akaiutil_gui/hfe.py`, and `tests/test_hfe.py` repeats its checks).

What the samples settled (all **HD, HFE v1**, so nothing here covers DD or v3):

- **Header, as I remembered it:** `HXCPICFE`, revision 0, 80 tracks, 2 sides, track encoding 0 (ISO/IBM MFM), **bit rate 500**
  (kbit/s: the *data* rate; the cells run at 1 Mbit/s), RPM field 0, interface mode 1, track table at block 1, write-allowed 0xFF,
  single-step 0xFF, the rest of the header 0xFF. Whether bytes 0x1a-0x1f always read 0xFF isn't known (two files only).
- **Track table:** 80 entries of `{offset in 512-byte blocks, length in bytes}`; every track is **50,336 bytes** (both sides),
  99 blocks apart, first track at block 2. File size 4,056,064 = 512 + 512 + 80 x 99 x 512. About 25,168 bytes (201k cells) per
  side, i.e. one 200 ms revolution at 1 Mcell/s, as expected for 300 RPM.
- **Interleave and bit order:** 256 bytes of side 0, then 256 of side 1, per 512-byte block; bits **LSB first**. Decoding with
  that gives clean sync marks (`0x4489` x3), ID marks and data marks.
- **Sectors:** 10 per track, N=3 (1024 bytes), C and H in the ID fields equal the track and side, **every header and data CRC
  good, nothing missing** on all 160 tracks of both files. Decoded to 1,638,400 bytes.
- **akaiutil opens the decoded image as `FLH`**, one partition, volume `ALPHA DX`. So a flat image decoded this way is what the
  rest of the app already handles.
- **Skew differs from my table above.** On these disks the first sector on a track alternates **1, 6, 1, 6, ...** (tracks and
  sides alike), not the `offset 6 / skew 4` that akaiutil's Windows formatter would give (7, 3, ...). So the skew table in
  "What the flat image has to look like" describes akaiutil's formatter, not necessarily real disks. Decoding doesn't care (it
  places sectors by number). For *writing*, patching an existing HFE in place keeps whatever the disk had, which is another reason
  to prefer that over a fresh encode; a fresh encode should copy this 1/6 pattern (HD) unless a real sampler says otherwise.
- **Gaps (track 0, side 0, measured):** 80 x `4E`, 12 x `00`, `C2 C2 C2 FC` (index mark), 50 x `4E`; per sector 12 x `00`, `A1 A1 A1 FE`,
  C H R N, CRC, **22** x `4E`, 12 x `00`, `A1 A1 A1 FB`, 1024 data, CRC, **139** x `4E`; the rest of the side filled with `4E`.
  Standard IBM MFM apart from the longer gap 3. (akaiutil's formatter uses a gap3 *parameter* of `0x74`; that isn't a byte count to
  compare with the 139 here.)
- **Not every sync is on a byte boundary.** The index and ID syncs sit on the 16-cell grid, but the data-field syncs sit 6 cells
  off it (cell offset 0 and 6 mod 16 both occur): those fields were rewritten with a different bit phase, as a real drive does.
  A decoder must search for sync patterns in the cell stream, not step in bytes. It also means a fresh encode can never be
  byte-identical to these files, however right it is.
- Unused tail of the last 512-byte block of each track holds an alternating `49 2A` pattern (an MFM filler), 352 bytes per track.
- Exact cell timing (cell-length jitter) is not in a v1 file, so there was nothing to examine.
- The two disks differ in content as expected (`cmp` differs from byte 1571).

Still open: a **DD (800K)** HFE, a **v3** file, anything written by a tool other than the one that made these (the disks look
like S3000XL-made images converted for Gotek; who made the HFEs is not stated), and the other 238 disks (a batch decode would
be a good first test: every one should give 10 good sectors on all 160 tracks).

## Encoder results (2026-10-29, from the prototype, on `Alpha_DX.HFE` and `Sitar.HFE`)

Two ways of writing, as designed above.

1. **Patch in place** (`patch(file, flat_image)`: find each sector's data field in the cell stream, re-encode its 1024 bytes + CRC,
   fix the one clock cell after it, leave everything else alone).
   - With the *unchanged* decoded image the output is **byte-identical to the original file**, on both disks. That is the strongest
     check there is that the MFM clock rule, the CRC (CRC-16/CCITT seeded with the `A1 A1 A1 FB` sync bytes; `binascii.crc_hqx`
     does it) and the bit order are right, because they reproduce what a different program wrote.
   - Three changed sectors (first, middle, last) decode back to exactly the new image, with no bad or missing sectors, and only
     those three differ from the original.
   - A real edit: `reni 4 EDITED.S3` in akaiutil on the decoded image changed 2 sectors; patching them into the HFE changed
     **32 bytes** of the 4 MB file, header and track table untouched; decoding the patched HFE gives the same image akaiutil
     wrote, and `dir` lists `EDITED.S3`.
2. **Fresh encode** (`fresh(flat_image)`: header, index mark, gaps and the 1/6 sector rotation as measured above).
   - Decodes (with our own decoder) to the identical image, every CRC good. Same size and track layout as the
     originals (4,056,064 bytes).
   - It is **not** byte-identical to the originals (about 0.9 M of 4 M bytes differ), as expected from the sync phase point
     above. That means this path is only checked against *my own* decoder.

**Not checked, and it matters:** that anything other than my decoder reads these files. Greaseweazle's decoder was the plan for
that, but see the next section. Also unchecked: a sampler or Gotek loading a written file, DD, v3, any disk that is not one of
these two.

Caveats found on the way:
- `patch` assumes each data field starts after a `FB` mark (previous data bit 1) and fixes the clock cell after the CRC from the
  cell that follows. Both hold on these files. A track whose sector is damaged (bad CRC) is skipped by the decoder and so would be
  left as it was by `patch`: the product code must refuse to write back if the decode reported problems.
- `side_cells` reads every 512-byte block including the filler; `track_len` is not a multiple of 512 (50,336 = 98 x 512 + 160).
  Reading and writing must use `ceil(len / 512)` blocks. `hfe.py` does.

## Greaseweazle, what was found

- **Not on PyPI** (`uv pip install greaseweazle` finds no such package); it is installed from GitHub. So it cannot be a normal
  `pyproject.toml` dependency, which makes vendoring a few modules, or not using it, the options.
- An attempt to install it from GitHub into a throwaway virtualenv (to use its decoder as an independent check of the encoder)
  was **blocked by the permission system** (code from outside the project). It was not retried another way. So its licence, API
  and Akai disk definitions are still unchecked.
- Given that the decode and both encode paths took a few hundred lines and worked first time against real files, **we wrote it
  ourselves** (Qt-free `hfe.py`), and use Greaseweazle (or `hxcfe`) only as an
  external cross-check tool, run by hand. That check is the first thing to do next; see "Next steps".

## Built so far (2026-10-29)

- `src/akaiutil_gui/hfe.py` and `tests/test_hfe.py` (23 tests; the whole suite passes). (See "Wired into the app" below for the app side.)
- Decoding a disk takes about 0.2 s, patching the same, encoding a new file about 0.5 s (lookup tables; the prototype was ten
  times slower), so none of it needs special handling in a worker thread beyond what opening already has.
- Tests generate their own disks (random data encoded by `hfe.encode`), because the samples are copyrighted. One test runs
  akaiutil against a decoded floppy and patches a real akaiutil edit (`reni`) back in. `test_real_hfe_files_*` runs on every
  `.hfe` in `testing_images/` (git-ignored) or `$AKAIUTIL_TEST_IMAGES`, and skips without any: it decodes cleanly, patches
  back to the identical file, and survives a changed sector. It was run on `Alpha_DX.HFE` and `Sitar.HFE` (copied there locally).
- Refusals, each tested: not HFE, truncated, **v3** ("isn't supported yet"), not MFM, not 80 x 2, a damaged or missing sector
  (names the track, side and sector; `strict=False` zero-fills and lists them), an image of the wrong size, a low-density
  disk to `encode`.
- A decoder lesson worth keeping: it reads only the *data* cells (odd positions); clock cells are ignored, so a flipped clock bit
  is not an error. Tests that damage a file must flip a data bit (mask `0x02`, `0x08`, `0x20`, `0x80` of a stored byte).

## Wired into the app (2026-10-29)

Done: `src/akaiutil_gui/hfe_work.py` (Qt-free `Workspace`), the changes in `ui/main_window.py` (open, add, close, `_run_edit`,
File > Save Disk as HFE…), `hfe.problem` in `importing.disk_image_problem`, `HfeError` as a user-facing reason in `ui/tasks.py`, the
manual's "HFE images" section, and tests: `test_hfe_work.py` (Qt-free) and `test_gui_hfe.py` (offscreen, real akaiutil).

How it differs from the first sketch in "How it fits the app":
- **Write-back happens after every edit, not when the image is closed.** akaiutil already writes an `.img` after every command, so
  an HFE that waited for close would lose edits on a crash or force-quit that an `.img` wouldn't. Closing (and quitting) only retries
  a write-back that failed, and asks before discarding one that still can't be written.
- The window keeps `_images` as the HFE paths; only the call to `engine.open` sees the working copies (`_engine_paths`), and the
  banner's paths are mapped back (`_disk_files`).
- Adding an HFE decodes it on a worker first (`_reopen_with`), so a damaged one is refused without restarting akaiutil and closing
  the images that are open. Opening one that fails closes everything, as any open failure does.
- Save as HFE is enabled for floppies only (`disk.type` starts with `FL`).

Not covered, deliberately: a working copy left behind by a crash between an edit and its write-back is not recovered on the next
start (the HFE is decoded afresh; the old copy stays in the `hfe-work` folder until someone clears it). The window is the
size of one edit, so it is small, and `Workspace` logs it. Add a recovery prompt if it ever happens in practice.

## Damaged disks and repair (2026-10-29)

**Done: damaged disks open read-only.** `Workspace.engine_path` decodes with `strict=False`; the bad sectors are blank in the working
copy and listed (`Decoded.missing`, `Workspace.damaged()`). The window warns once per file with each (track, side, sector), forces the
engine read-only, refuses Allow Editing (and a reopen that asks for it), and nothing is ever written back: `sync` skips such a disk and
`save_as` refuses it. Tests in `test_hfe.py`, `test_hfe_work.py`, `test_gui_hfe.py`. The three real damaged disks (JD8 NYLON: 1 sector,
MARANZANO: 5, METAL KIT 13: 2) open and akaiutil lists their files.

**Built: repair (File > Save Repaired Copy..., 2026-10-29).** `hfe.repair(data, rewrite)` -> `Repair(data, fixes)`, `hfe.save_repair`
(writes the copy only after checking every sector other than the repaired ones reads as before), `Workspace.repair_copy`, `floppy_map`
(which files a block belongs to), `edit_dialogs.RepairDialog`, and the window's warning/summary. What it does, strongest first, per
damaged data field (header intact, checksum wrong):
1. *One flipped bit* (the checksum names it): accepted if the MFM clock violations (if any) are all at that bit -> `FIXED` ("bit"); if
   there are no violations at all the fix rests on the checksum alone -> `FIXED_PROBABLY`.
2. *A lost or extra cell* (many violations): one cell inserted or removed in a window from 240 cells before the first violation to 24
   after (the violation can show up well after the slip); accepted only if exactly one reading of the field results, with no
   violations left and the checksum right -> `FIXED` ("realigned"). The track keeps its length (the end of a track is filler).
3. Otherwise `LOST` (left exactly as it was), or with `rewrite` `REWRITTEN` (data as read plus a fresh checksum: reads without
   an error, may be wrong). A sector whose header is damaged or missing is `LOST` always.
Results on the three real damaged disks: **all 8 sectors repaired** (JD8 NYLON 1 realigned; MARANZANO 2 bit + 3 realigned; METAL
KIT 13 1 bit + 1 probable); the first search window (+-40 cells) had missed one. Tests: `test_hfe_repair.py` damages a good disk in
every way above (and 14 random places) and requires the original back, `test_floppy_map.py` checks the file mapping against akaiutil on
S1000, S900 and S3000 floppies and a real sampler disk, `test_gui_hfe.py` runs the flow in the window.
Open: nothing has played a repaired copy on a sampler, and a repair is only as good as the CRC + clock rule: a wrong candidate passes
by luck about 1 time in 65536 per candidate before the clock rule, far less with it (hence "probably" for checksum-only fixes).

**Two experiments on the 8 bad sectors of the three real damaged disks (2026-10-29)** shaped the design (kept for the reasoning; the
numbers 7 of 8 are from the first, narrower search, and the built version repairs all 8):
- *The MFM clock rule is a clean damage detector.* A clock cell is set exactly when both neighbouring data bits are 0. **All 200 good
  sectors sampled had zero violations**, so any violation inside a data field marks damage, independently of the CRC. The 8 bad
  sectors: 4 have 0-2 violations, 4 have 92-833.
- *Single-bit sectors (4):* the one flip that fixes the CRC lands exactly on the violation in 3 of them (a flipped data cell:
  verified twice over). The 4th (METAL KIT 13, track 20 side 0) has **no violation at all**: the cells are a valid MFM of wrong
  data (the corruption happened before the cells were made), so its fix is CRC-only, "probably".
- *Garbled sectors (the other 4) are cell slips:* everything after one point is misaligned by a missing or extra cell. Inserting
  one cell next to the first violation makes the rest clean **and** the CRC pass in 3 of 4 (JD8 NYLON t4s0, MARANZANO t14s0 and
  t30s0, each with 0 violations left); MARANZANO t33s0 (567 violations) has no one-cell repair (probably two slips or worse).
- So **7 of 8** sectors are repairable with both checks, 1 (maybe) with CRC only, and only 1 is lost. Chance that a wrong candidate
  passes the CRC by luck: about 240 candidates x 1/65536, under 0.4% per sector before the MFM requirement; far lower with it.
So the design is a ladder, strongest first, and each sector's report says which rung it got: (a) slip realigned, MFM clean + CRC ok;
(b) single cell flipped at an MFM violation, CRC ok; (c) single bit flipped, CRC ok but no violation ("probably"); (d) not
recoverable: left as read-and-unreadable (blank), or, only if asked, re-sealed with a fresh CRC (garbage in, but the sampler won't
report a read error).
**Which files are hit (feature 1) is easy on a floppy:** the header holds the directory and a FAT of 2-byte entries (`akai_fllhead_s`
/ `akai_flhhead_s` in `akaiutil.h`: directory entries, then `fatblk[]`, a 1 KB block each; S3000 floppies keep the directory behind
the header). A bad sector's block number is `((track*2+side)*spt + sector-1)`; walk each file's chain from its start block. A hit in
the header, directory or FAT is the serious case and should be said so.
Implementation sketch: `_find_sectors` also returns the bad data fields (their cell position and bytes read); a `repair(data)`
produces the new file plus a report; the window gets File > Save Repaired Copy… for a damaged HFE, with the report in the dialog.

## Low density and the S900/S950 samples (2026-10-29)

Three more downloaded sets, all in `~/Downloads/` (commercial or archive material: **not in the repo**; three small ones were copied
into the git-ignored `testing_images/` for the optional tests):

| Set | Files | Header | Result |
|---|---|---|---|
| `AKAI S900 IMAGES/` | 999 `.HFE` | v1, bit rate **250** (DD), track 25,000 bytes, mode 12 | all decode; all patch back byte-identical (the ones checked are blank formatted disks) |
| `QuickInstall_FloppyDiskImages/SDHxCFE_Akai_S900/` | 32 | the same, plus `DSKA0000` / `S900_OS4` (mode 7, track 25,008) = the S900 operating system disk | all decode and patch identically; akaiutil lists `FIXUPS.F9` |
| `QuickInstall_FloppyDiskImages/SDHxCFE_Akai_S950/` | 31 | v1, bit rate **500** (HD), track 50,016 (one: 50,176), mode 13 | all decode and patch identically; `DSKA0000` has 1,469 data sectors and lists `PRESIDENT.P9` etc. |
| `Akai SL5000 Factory Library DD/` | 20 `.AKAI` | not HFE: flat 800 KB images | 19 open in akaiutil (`FLL`); `SL5002` is 803,840 bytes, truncated, refused as too small |

So **1,062 of 1,062** new HFE files decode and patch back to themselves, in both densities, and decoding needed no change for DD.

The two HxC-made sets (SD HxC "QuickInstall" packs, meant for a real S900/S950 through an SD HxC emulator) are a different
*generator* from the Acrobatics set, so there are now three layouts seen:

| | sampler (Acrobatics, S3000XL, HD) | HxC S900 (DD) | HxC S950 (HD) |
|---|---|---|---|
| first 4E filler / index mark | 80 / yes | **230** / yes | 230 / yes |
| gap1 / gap2 | 50 / 22 | 50 / 22 | 50 / 22 |
| gap3 | 139 | **86** | **116** (= akaiutil's `0x74`) |
| first sector on track *t* | 1, 6, 1, 6 ... | 1 every track (in order) | 7, 3, 9, 5, 1 ... = akaiutil's formula (offset 6, skew 4) |
| stored bytes per side (track_len / 2) | 25,168 | **12,500** | 25,008 |
| bit rate / mode in header | 500 / 1 | 250 / 12 | 500 / 13 |
| data-field syncs on the byte grid | not always (rewritten) | yes | yes |

**The independent check at last:** `hfe._DD` (the S900 layout) and `hfe._HD_HXC` (the S950 layout), driven only by the numbers above,
reproduce HxC's blank-disk files **byte for byte**: every cell of all 160 track sides, the CRCs, the filler, the header and the
track list (the one difference found was HxC filling the unused end of the track list with 0xFF; fixed). That is a writer other than
this module's own agreeing with the encoder (the test is `test_a_new_file_is_what_hxc_wrote_...`, run on any real files in
`testing_images/`). Caveat: the disks reproduced are blank (all-zero data), so it checks structure, CRCs and the 4E/00/sync/filler
bytes rather than the MFM clock rule on varied data; that rule is what "patch gives the identical file back" on real data disks
(Acrobatics, the S900 OS, the S950 data disk) already checks. Files from other generators (`DSKA0000`: mode 7, track 25,008 or
50,176) decode and patch fine but aren't reproduced by a fresh encode, as expected.

Consequences, done: `encode` now offers **both densities** (HD = the sampler's layout, DD = HxC's S900 layout), so File > Save Disk
as HFE... works for any floppy; `.akai` is in the open dialog's filter. Still open: the layouts are all "known good" to somebody's
hardware, but a file *this app* wrote has still not been played in a real sampler or Gotek.

## Next steps

1. **Independent check of written files.** Convert a patched HFE and a freshly encoded HFE with software that isn't ours (the
   maintainer could run `gw convert` from a Greaseweazle checkout, or open both in the HxC Floppy Emulator software) and compare
   with the flat image. Ideally also load one on a Gotek/HxC and a sampler. Until then only this module has read what it wrote.
2. ~~Run the real-file test over all 120 sample disks.~~ Done 2026-10-29, straight from the zips in memory: all 120 have the same
   header (v1, HD, bit rate 500, 50,336-byte tracks; **none is DD**). **117 pass everything** (clean decode, patch with the unchanged
   image gives the identical file, a changed sector reads back). **3 are refused as damaged**: Vol 4 `JD8 NYLON` (1 sector),
   Vol 4 `MARANZANO` (10 sectors), Vol 6 `METAL KIT 13` (2 sectors). Their data fields fail the CRC; two of them have a single
   flipped bit that exactly repairs it, which looks like read errors in the dumps and not a decoder fault (a fault would not leave
   117 disks, about 187,000 sectors, perfect). So strict refusal is right for these, but it means a disk with 10 bad sectors out
   of 1600 can't be opened at all. A possible feature: open such a disk **read-only** with the bad sectors zero-filled and a
   warning (`decode(strict=False)` already does the decoding); editing would stay off, since `patch` refuses a damaged original.
   **Done** (see "Damaged disks and repair" below).
3. ~~Get a DD HFE~~ done (above). Still wanted: a **v3** HFE (v3 is refused), and a non-blank DD disk from the same generator as the
   blank ones, to check the clock rule on varied data against HxC's writer too.
4. Try it by hand in the real window with a real `.hfe` (nobody has yet; the GUI tests are offscreen): open, edit, check the file,
   close, reopen, Revert, Save as HFE.
5. Clean up `hfe-work` leftovers at start-up, if they turn out to matter.

## State of play

- The user who asked for this (a feature request, not the maintainer) has not sent their own files yet; the samples above are
  stand-ins found online. The maintainer knew nothing about HFE beforehand and wants **both reading and writing**.
- The codec and the app wiring are done and tested (offscreen) on two real HD disks and generated ones. Not checked by anything but
  this module: that other software and real hardware accept what it writes (next steps 1); nobody has used it by hand yet (4).

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
