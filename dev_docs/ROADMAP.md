# Roadmap

Last updated 2026-10-30. Current status of the app is in the
[README](../README.md) and the manual.

## Verification debt

None of this could be checked by the test runs, which use a fake audio device, offscreen Qt and akaiutil reading its own output back.

- [ ] **A real sampler.** Load an edited, imported or copied image into an S3000/S1000/S900 (or Gotek/SCSI2SD setup). Especially:
      sample-settings edits (written in place with `puti` since akaiutil 4.7, 2026-11-24; only akaiutil has read them back), imported loops and root notes, stereo `-L`/`-R` pairs (an edit doesn't touch the partner address,
      `stpaira`), S900 samples and compressed S9C, and files copied into a new volume.
- [ ] **Files added anyway to a volume of another sampler** (an `.S3` in an S1000 or S900 volume). akaiutil writes them with the
      volume's OS version in place of the file's (4.40 in an S1000 volume, none in an S900 one). Check that an S3000-series sampler
      lists and loads them from both kinds of image, and that an S1000 or S900 doesn't choke on the disk. The people who asked
      for this say it works, but nothing here has confirmed it.
- [ ] **Other files stored as `<name>.X01`** (a text file or PDF added anyway). Nothing says what a sampler shows for a file of
      that type, or whether another hex type (or a name the sampler hides) would suit better; change `importing.RAW_TYPE` if so.
- [ ] **HFE files this app wrote** (an edited or newly saved `.hfe`): load on a Gotek/HxC feeding a real sampler, and decode with
      Greaseweazle or the HxC software. Only the app's own reader has read them so far. Details in [hfe-plan.md](hfe-plan.md).

- [ ] **OS boot floppies** (File > Create OS Boot Floppy…, from a `.COD` or a saved `.C`): boot an S2000 from one, as an image on a Gotek
      or as HFE. Written to match what OSMaker's code writes, never compared with a disk OSMaker made. Also unknown: whether a
      `.COD` loads as it is or the sampler wants the `.C` form, and whether the model tag, label or file name matter. See [os-floppy.md](os-floppy.md).
- [ ] **DD takes** (built, see [dd-takes-plan.md](dd-takes-plan.md)): only akaiutil-made takes have been read, exported and imported so
      far. Needs a hard-disk image with takes recorded on an S1100/S3000, and a real sampler loading what the app imported or renamed.

## Next

0. **HFE floppy images** are wired in (open, edit with write-back after each change, File > Save Disk as HFE…). What's left is checking
   the written files with other software and a real sampler/Gotek, and v3 files. Low- and high-density disks both work, tested on about 1,100 real files, and the encoder reproduces HxC's own files byte for byte (blank S900/S950 disks, and the S900 OS disk with real data). Damaged HFEs open read-only and name the files they hit; File > Save Repaired Copy… fixes them in a new file (all 8 sectors of the three real damaged disks), with an optional, warned-about rewrite of what can't be fixed. Not yet tried on a real sampler. See [hfe-plan.md](hfe-plan.md).
1. **Open an SD card / physical drive directly.** akaiutil can open raw devices. Needs OS permissions per platform and safety
   rails against picking the wrong disk; start read-only, add editing later.
2. **Direct-to-disk takes** (S1100/S3000 recordings): listing, settings, export (WAV or `.TK`), import, rename and delete are built
   (2026-10-30), with a manual section. What's left: a manual screenshot, drag-out, resampling a too-fast file instead of refusing it, and checking all of it against
   a real recording. Details and the rules akaiutil won't enforce (a 96 kHz WAV is silently stored as 30,464 Hz): [dd-takes-plan.md](dd-takes-plan.md).
   No public image exists, so one has to be asked for from an S1100/S3000 owner (the plan says what to ask for).
3. **Multis.** akaiutil only prints "S3000 multi" for one, so showing its parts means reading the file's bytes ourselves, which
   needs the format. A multi names programs, so it would also allow "programs no multi uses".
4. **Copy a whole partition to a file and back** (`pget`/`pput`). Partition to partition is done (Copy Partition To…); it is not checked on a real sampler.
5. **Tags**: setting and clearing a file's tags (Edit > Tag Files…, the file menu, a link in the details pane; akaiutil 4.7 or newer), renaming a partition's tag names (Edit > Rename Tags…,
   the partition menu, and a button in the Tag Files window) and a tag menu beside the search box (files with a tag, across the open disks) are built, tested against akaiutil only.
   What's left: checking a tagged and renamed image on a real sampler.
6. **Import polish:** AIFF loop points (INST + MARK chunks: the end-marker convention needs checking), fade in/out, a pitch
   fraction from `smpl`, per-file overrides in the import dialog, a progress dialog for big batches.
7. **ctypes/cffi engine** to replace the subprocess and the text parsing in `engine/parse.py`. `engine/api.py` is the seam
   (see [ARCHITECTURE.md](ARCHITECTURE.md)). Worth doing once the feature set settles: it removes the restart-on-add/close and the text fragility.

## Converting files between samplers

Today a file for another sampler than the volume's is either added as it is ("Add anyway", see the manual) or left out. Offering to
*convert* it instead is the next step, but only where the result is well defined:

- **S3000 sample -> S1000 sample.** Plausible first target. The two headers (150 and 192 bytes, `akai_header.py`) share most of
  their fields, so it is a header rewrite plus the right file type and OS version, with the audio untouched. Fields the S1000
  doesn't have (S3000-only loop and tuning details) are dropped, so the dialog has to say what is lost. It could go through the
  same import-by-tar route as `edit_sample_settings`, with a check afterwards (size, audio, name, slot).
- **Sample -> S900 sample.** A real conversion, not a header patch: a different header, 10-character names, a 40 kHz rate limit and
  optional compression. Easiest as export to WAV then `wav2sample9` (the audio path in `importing.py` already resamples), so loops
  and settings would not survive. Needs its own wording in the dialog.
- **S900/S1000 sample -> S3000 sample** is the other direction and the header patch is the same job in reverse; useful for moving
  old libraries forward.
- **Programs, multis and the rest** (`.P3` <-> `.P1`, effects, ...): not offered. Keygroup and parameter layouts differ, a
  half-right conversion is worse than a refusal, and the work belongs with the [program editor plan](program-editor-plan.md).

How it would fit: the question in `main_window._ask_add_anyway` and the import dialog gain a third choice, **Convert**, shown only
for the pairs above, with a one-line list of what changes. The pair table goes next to `importing.sampler_mismatch`. The
conversion itself should be Qt-free and tested against the real akaiutil, like the sample-settings edit. The sampler
verification debt above comes first: it tells us whether most users need the conversion at all or are content with Add Anyway.

## Blocked on hardware

- **Effects files (`.X`) editing.** akaiutil only knows the type and name; the contents are undocumented and nobody here has an
  S1100/S3000 with an effects board to measure one. What is known, the 7,312-byte vs 2 x 51 x 128-byte puzzle and how someone with
  the hardware could map it: [fx-files.md](fx-files.md). An S1000 has no effects.

## Known limitations

(The user-facing ones are in the manual's "Good to know".)

- Editing is all-or-nothing across the open images (one shared akaiutil process).
- Only mono and stereo audio; more than two channels is refused.
- 24-bit to 16-bit is plain rounding (no dither), so digital silence stays silent.
