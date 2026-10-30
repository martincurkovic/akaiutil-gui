# Direct-to-disk (DD) takes: plan

Written 2026-10-30. **Built so far (2026-10-30): phases 0 to 4** (the test image, the take list and details, export, import, rename and delete, and the
manual page and README lines). Not built: drag-out, saved column widths, resampling a too-fast file. Nothing has been checked on a real recording or a real sampler. Selecting a DD partition shows its takes in a table with each take's settings in the details pane;
the partition's own text (`engine.list_takes_text`) still shows when no take is selected.

A DD take is a recording made by an S1100 or S3000 straight to a hard disk. akaiutil already has every command needed; the work
is parsing, a take table, and the checks around import. What akaiutil does, as measured, is in
[akaiutil-notes.md](akaiutil-notes.md) ("Direct-to-disk takes"). **Everything measured so far is on images akaiutil made itself
from sine and noise WAVs. No real take recorded on a sampler has been read yet** (see "Waiting on" below).

## Scope

- In: list takes, export a take as WAV or as a raw `.TK`, import a WAV or a `.TK`, rename, delete, show a take's settings.
- Out for now: editing take settings (pan, fades, MIDI note...), editing the edit markers, anything that touches the envelope, cue
  lists and take lists (`.Q`/`.T` files, which are ordinary files in a volume, not takes).
- DD exists on S1100/S3000-series disks only. An S1000 has no direct-to-disk, so none of this is for S1000-only users.

## What akaiutil gives us (all tried, see the notes)

| Job | Command | Output to parse |
|---|---|---|
| List | `dir` inside `/diskN/DD` | fixed table: `tnr tname cstarts cstarte csizes csizee scount srate stype` |
| Details | `tinfoi <n>` / `tinfoall` | `key: value` lines (fades, pan, MIDI channel/note, output, FX bus...) |
| Export WAV | `take2wavi <n>` | file `<NAME>.wav` in akaiutil's working dir |
| Export raw | `tgeti <n>` | file `<NAME>.TK` in akaiutil's working dir |
| Import WAV | `wav2take <file>` | `importing "<file>" to take <n>`; first free slot |
| Import raw | `tput <file>.TK` | `importing "<file>" to take <n>` |
| Rename | `treni <n> <NEW>` | none |
| Delete | `tdeli <n>` | none |

The export/import mechanics are the same as for samples (akaiutil's own working directory, `_` for a space, staging dir), so
`export_files` and `import_job` in `engine/api.py` are the patterns to copy.

## Rules the GUI has to add (akaiutil won't)

1. **Refuse a rate above 65,535 Hz.** akaiutil stores the rate in 16 bits and wraps silently: a 96 kHz WAV came in as 30,464 Hz with
   no message. Check the WAV header before calling `wav2take`.
2. **Check there is room first.** A take needs `ceil(scount * 2 / 262144)` sample clusters plus 1 envelope cluster (an envelope was
   1 cluster even for a 20 s take), 256 KiB each; a 1 s mono take is already 512 KiB. A full partition does fail cleanly
   ("not enough space left", nothing half-written, free space matches afterwards), so this check is about a better message and
   about batches, not about safety.
3. **Address takes by number, never by name.** akaiutil allows duplicate names (two takes called `MONO`), and `treni`/`tdeli` take
   an index. Re-list after every change: deleting frees a slot that the next import reuses, so numbers move.
4. **Names are cut to 12 characters** on import; say so, as for samples. Use the existing name checks in `akai_names.py` if they
   fit (they are written for volumes and files; take names may differ, check).
5. **Stereo `scount` counts both channels** (88,200 for one second of stereo at 44.1 kHz). Frames = `scount / 2` for `STEREO`.
   Duration = frames / rate. Easy to get wrong in a duration column.
6. Editing needs the writable session and a backup question, like every other edit (`_need_writable`, `_run_edit`).

## Phases

### 0. Fixture (small) — done
- `tests/imagegen.py`: add a `dd` image: a 10 MB file, `formatharddisk3 2M 4M` (**the second size must be smaller than the file**,
  or no DD partition is made), then `cd /disk0/DD` and `wav2take` a mono and a stereo WAV. Leave room for a few more takes.
- Tests that need it skip without akaiutil, like the rest.

### 1. Parse and show (small) — done

Built as: `Take` in `engine/models.py`, `parse.parse_takes` (`dir` table, split on akaiutil's own printf widths), `AkaiEngine.list_takes` / `take_info`,
`describe.describe_take` (Take / Playback / Output / Technical), `ui/take_table.py` (a sibling of `FileTable`, swapped in by
`MainWindow._show_takes`). Tests: `tests/test_dd_takes.py`, `test_parse.py`, `test_gui_takes.py`. The plan text below is what was intended.
- `engine/parse.py`: `parse_takes(text)` for the `dir` table. Names contain spaces (`LONG NAME TE`) and are padded, so split from
  the right (the last seven columns are fixed-format), not on whitespace. A `Take` model in `engine/models.py`.
- `AkaiEngine.list_takes(part)` next to `list_takes_text`; keep the text version for the details pane until it is replaced.
- Show takes as rows (name, mono/stereo, rate, duration, size) when a DD partition is selected, with the usage bar already there.
  **Read 2026-10-30: `ui/file_table.py` (237 lines) is tied to volume files:** fixed columns (`#`, Name, Type, Size, Start block,
  OS ver, Tags, Location), `FileEntry`/`Volume` item roles, a search-results mode and saved column widths. So a sibling
  `take_table.py` swapped into the files pane is the plan, not bending that table. The wiring is in `main_window.py` (3,000+
  lines): a DD partition is handled where the selection changes (around line 1456, `engine.list_takes_text`), and export, drag-out
  and drop-in hang off the same window (`_export`, `_export_for_drag`, the drop handlers).
- Details pane: `tinfoi` text as rows (fades, pan, MIDI note...), read-only.

### 2. Export (small) — done, except drag-out

Built as: `AkaiEngine.export_takes` (WAV via `take2wavi`, raw via `tgeti`; a name already used in the batch gets `(take N)` added),
File > Export… (raw `.TK`) and Export as WAV… working on the selected takes, the same two in the take table's right-click menu, and
"Export Takes as WAV…" on a DD partition in the tree (every take). Tests in `test_dd_takes.py` and `test_gui_takes.py`: exported WAVs
are bit-identical to the WAVs the takes were made from. **Drag-out is not built:** the file list's drag carries an internal
"copy these files into a volume" type and a URL list the window turns into an import, so a take drag needs its own mime type and a
decision on what dropping it on a volume should do. The plan text below is what was intended.
- `AkaiEngine.export_takes(part, takes, dest, as_wav, confirm_overwrite)`: same shape as `export_files`; one take at a time, staging
  dir cleared between, results moved to the chosen folder, overwrite asked.
- Context menu and drag-out for takes (`ui/file_drag.py` is 39 lines).
- WAV export has no loop chunk to add: takes have none.
- Check: exported WAV is bit-identical to the one imported (it was, for a take whose cluster chain was fragmented).

### 3. Import, rename, delete (medium) — done

Built as: `takes.py` (Qt-free: `inspect` a file, `prepare_audio` into a clean 16-bit WAV with `soundfile`, `run_import` over several files),
`AkaiEngine.import_take` / `rename_take` / `delete_takes`, and in the window: Edit > Import Takes… / Rename Take… / Delete Take… (the
same menu items and shortcuts as for files, worded for takes while a DD partition is open), the take table's right-click menu, "Import
Takes…" on a DD partition in the tree, and audio or `.TK` files dropped on the window. Behaviour worth knowing:

- A file above 65,535 Hz, with over two channels, with no audio, or that is not audio or a `.TK` is refused with a reason, and the others
  of a batch carry on. A file is always rewritten as clean 16-bit audio at its own rate (24-bit and 8-bit are converted by the app,
  not by akaiutil, so the audio conversion `wav2take` does is not relied on). **No resampling:** a 96 kHz file is refused, not
  converted to 48 kHz; that is the obvious next convenience.
- Room is checked first (`engine._free_bytes` re-reads the partition, since the `Partition` in hand may be stale), with a message that
  says how much is needed and free. After `wav2take` the new take must have the file's channels, rate and frame count, or it is
  deleted again and the import counts as failed.
- A take's name is its file's name cut to 12 Akai characters (`importing.clean_stem`). Duplicates are allowed, so rename and delete go
  by number and refuse a stale selection ("isn't take 2 any more").
- Delete frees the number; the next import takes the lowest free one. Nothing is renumbered.
- `.TK` import relies on akaiutil to reject a bad file (only the size is checked first).

The plan text below is what was intended.
- `import_takes(part, paths)`: WAV and `.TK`. Pre-checks from the rules above; then `wav2take`/`tput`; read the slot number from
  akaiutil's reply; re-list and confirm the take is there with the expected size and rate; on a failure, leave the image as it was.
- Which formats: anything soundfile reads, mono or stereo. Tested: 24-bit stereo 48 kHz and 8-bit mono 22.05 kHz files, the audio
  coming back bit-identical to the 16-bit WAV the app wrote.
- `rename_take`, `delete_take` (`treni`, `tdeli`), with confirmation for delete.
- A name for a dropped file: its stem, upper-cased and cut to 12; ask or number duplicates.

### 4. Polish — done (manual and README)

The manual has a "Direct-to-disk takes" section (contents entry, shortcuts table, "Good to know" line) and the README two bullets; no
screenshot yet (`scripts/make_manual_screenshots.py` would need a DD image and a take selected). Every command is already logged by the
session logger, and the engine methods log what they did. The text below is what was intended.
- Manual page (user-facing, short), README line, a log entry for every command (the session logger already does this).

## Waiting on a real image

These cannot be settled without a hard-disk image with takes recorded on an S1100 or S3000 (or made by software that writes them):

- Does `dir`/`tinfoi` print real takes the way it prints synthetic ones: names with odd characters, vari-speed and fine-rate values,
  takes with edit markers moved, stereo takes, 48 kHz takes?
- Real fragmentation: a drive that has had many takes recorded and deleted. The synthetic test had one fragmented chain (clusters
  1, 2, then 13...) and it exported bit-exactly; a long, heavily fragmented chain is untested.
- A real take through `tgeti`/`tput`: byte-identical after a round trip? (Synthetic: `.TK` is 524,352 bytes for two clusters, i.e.
  64 bytes more than the clusters; the extra is presumably the take header. Not read from the source.)
- **Loading anything this app wrote into a real S1100/S3000 and playing it.** Same verification debt as every other edit. Imported
  takes get default settings (fade in 10 ms, fade out 50 ms, stereo level 99, MIDI channel 16, note 60, pre-delay 500, output
  level 50); whether a sampler is happy with these is unknown.
- Whether a real disk's DD partition can be anything other than `DD` in name (`DD1`...). `Partition.is_dd` is
  `name.startswith("DD")`, which would cover it; akaiutil's code makes one DD partition at the end of the disk when formatting,
  but the partition table has room for more (the source has `AKAI_DDPART_NUM`).

## Getting a real image

- Searched online 2026-10-30 (general, archive.org, akaiutil wiki, owner forums): **no public DD image or sample set was found.**
  Nothing in akaiutil's own wiki either (it links template floppy images only).
- Leads: the S1100/S3000 owner threads on MPC Forums and Gearspace, the HxC forum, and anyone running a ZuluSCSI or SCSI2SD card
  (a DD session is just a ~510 MB image there). Sound on Sound's [HD recording with the S1100](https://www.soundonsound.com/techniques/hd-recording-akai-s1100-sampler)
  explains setting a disk up for DD only (a 1 MB partition setting on the FORMAT page; Akai's format limit is 500 MB).
- **What to ask for:** a zipped image with a mono take, a stereo take and ideally one at 48 kHz; one take renamed; one take deleted
  and another recorded afterwards (gives a fragmented cluster chain). Small is fine. The S2000, the author's own sampler, has no DD.

## Effort (estimate, 2026-10-30; nothing built)

About 3 to 5 working days for everything, about 2 for a read-only version (fixture, parser, take table, export). Per phase: fixture
0.5 day, parser 0.5, engine list/export 1, engine import/rename/delete with the pre-checks 1, take table and wiring 1 to 2, manual and
tests 0.5. Add about half a day for fixes once a real image has been read.

## Things that can be done before the image arrives

Phase 0, 1 (against synthetic text), 2 and 3 can all be built and tested against akaiutil-made takes. What stays unproven is only
what the list above names. The sensible order is to build 0-3 behind the existing DD partition view, mark the feature as
unchecked against real takes in the roadmap, and fix whatever the real image shows.
