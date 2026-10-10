# Roadmap

Last updated 2026-10-10. Items are in the order I'd do them; nothing here is promised.

## Built

| Area | What works |
| --- | --- |
| Browsing | Disks, partitions, volumes, files across several images at once; space bars; readable details; search across all open disks |
| Audio | Sample preview with loops (same miniaudio backend and macOS loop fixes as AKAISDS), waveform, Space to play |
| Images | Add, close and reveal single images; disk rows show their file; New Disk Image (floppy up to 512 MB hard disk); Format Disk |
| Editing (opt-in, no undo) | Rename and delete files and volumes; new volume; **Allow Editing** switches akaiutil from `-r` to read/write |
| Sample settings | Edit > Sample Settings…: root key, tuning, play mode, markers and 8 loops of an S1000/S3000 sample, with a waveform (draggable markers, zoom/pan, click to preview, as in AKAISDS). Rewrites the sample (new header, same audio) and puts it back in its slot with its name, tags and OS version; the old copy goes only after the new one is checked |
| Safety net | Asked at the first edit: back the open images up (to the app data folder, newest 5 kept per image) or not; **File > Revert to Backup…** puts one back, after backing up the current state |
| Import | Drop or pick WAV/AIFF/FLAC (also OGG/MP3/CAF/W64): converted to 16-bit, resampled with our own windowed-sinc, stereo split or mixed, S1000/S3000/S900 (+ S900 compressed), alphabetical order, numbered names instead of overwrites; raw Akai files (`.S3`, `.P3`...) as they are |
| Smart import | Loop points and root note from a WAV `smpl` chunk (and an AIFF root note), root note from a name like `Piano_C3`, trim silence, normalise |
| Sorting | Sort a volume's files by name (all, or samples only) by moving them between slots; optional automatic sort after import/paste |
| Moving files | Copy / Cut / Paste and drag onto a volume (Shift/Cmd to move); drag files out to Finder/Explorer as WAV (Option for raw Akai) |
| Volumes | Copy a whole volume to any hard disk partition on any open disk (right-click > Copy Volume To…): room checked first, load-number clash fixed, no half copies |
| Export | Files as they are, or samples as WAV with the root note and loops in a `smpl` chunk; right-click a volume, partition or disk to export all its samples |
| Tags | A Tags column with the partition's names for them; copies keep an S3000 file's tags (matched by name, through a tar import) |
| App | Settings, update check against GitHub releases, CI builds mirroring AKAISDS |

## Verification debt (do this before trusting any of it)

None of these could be checked from the development machine's test runs, which use a fake audio device, offscreen Qt and
akaiutil reading its own output back:

- [ ] **A real sampler.** Load an edited, imported or copied image into an S3000/S1000/S900 (or Gotek/SCSI2SD setup). Especially:
      imported loops and root notes, stereo `-L`/`-R` pairs, S900 samples and compressed S9C, copied files in a new volume.
- [ ] **Dragging files out to Finder / Explorer / a Linux file manager.** The drag carries lazily-created files; this works in
      the unit tests (`QMimeData.urls()`), but how each platform's drop target asks for them hasn't been seen.
- [ ] **Hearing the preview** on macOS, Windows and Linux (the loop fixes ported from AKAISDS were verified only by their logic).
- [ ] **The CI legs**: Linux AppImage, Windows installer, Intel Mac. Only the Apple-silicon build has ever been run, and not
      since editing, importing and copying were added.
- [ ] Opening very large or many images (restarting akaiutil on every add/close takes time on 512 MB images).

## Next

1. **Open an SD card / physical drive directly.** akaiutil can open raw devices. Needs OS permissions per platform and safety
   rails against picking the wrong disk; start read-only, add editing later.
2. **Programs and multis.** Today akaiutil's raw info text. A readable keygroup view, "which samples does this program use?",
   and "samples no program uses" for library cleanup.
3. **Copy a whole partition** (`copypart`, or `pget`/`pput` to a file): volume copy is done and `copypart` would reuse it.
4. **Editing tags** (setting, clearing, renaming a partition's tag names). Showing them and keeping them on copy are done.
   akaiutil's `settagi` & co crash (see akaiutil-notes.md), so editing means either patching akaiutil or rewriting a file's tags
   with the same tar trick (delete + import, which moves the file's slot), or `rentag` for the names.
5. **Import polish:** AIFF loop points (INST + MARK chunks: the end-marker convention needs checking), fade in/out, a pitch
   fraction from `smpl`, per-file overrides in the import dialog, a progress dialog for big batches.
6. **ctypes/cffi engine** to replace the subprocess and the text parsing in `engine/parse.py`. `engine/api.py` is the seam
   (see the README). Worth doing once the feature set settles: it removes the restart-on-add/close and the text fragility.

## Known limitations

- Editing a sample's settings has only been tried on generated images and a copy of HD5, never on a sampler. A stereo `-L`/`-R` pair's
  partner address (`stpaira`) isn't touched, but nobody has loaded an edited pair into a sampler yet.
- Editing is all-or-nothing across the open images (one shared akaiutil process).
- Adding or closing an image restarts akaiutil on the new set, so disk numbers change and the search index is rebuilt.
- S900 samples are previewed as one-shots (their loop start isn't known to the app) and get no loops on import.
- Only mono and stereo audio; more than two channels is refused.
- 24-bit to 16-bit is plain rounding (no dither), so digital silence stays silent.
- DD takes (direct-to-disk) are listed as text only.
