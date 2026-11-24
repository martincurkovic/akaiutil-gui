# How akaiutil behaves (found by trying it, akaiutil 4.6.8)

Each point says what the GUI does about it. The source is in `akaiutil-4.6.x/` after `scripts/get_akaiutil.py` (git-ignored).

## Protocol
- Interactive REPL; the prompt `<cwd> > ` ends every answer. Output and errors are merged. Commands are limited to 256 bytes and
  split on whitespace, so a space in a name is written `_` (also in paths, and when it opens a local file).
- Writes go to the image after *every* command (the block cache is flushed each time), so closing the app can't lose an edit.
- A restart (`format…`, after which akaiutil rescans) just prints the banner again; the session carries on.
- Opening an image that can't be opened aborts the whole run after the disks before it.
- `-r` is the read-only switch. Without it, an image file the user can't write makes akaiutil exit quietly: the GUI checks
  `os.access` first and says which file.

## What counts as a disk
- akaiutil "opens" any file as a disk. A file that isn't one comes back as `HD9` or a floppy with **0 partitions** (it guesses from the size),
  while every format the app makes has at least one (a blank floppy gets one too). So `MainWindow._scanned` closes an image that
  has none, unless its start is all zeros (an empty image waiting for `format…`). `importing.disk_image_problem` is only the quick first
  look, so that the obvious cases (a PDF, a text file, an MP4) get a reason without starting akaiutil.

## HFE images (not akaiutil: the GUI's own layer, see `hfe-plan.md`)
- akaiutil can't read `.hfe` (a Gotek/HxC track-level container), so `hfe.py` decodes it to a flat image and `hfe_work.Workspace` keeps
  that as a **working copy** in the app's `hfe-work` folder; akaiutil is given the working copy, and `MainWindow._disk_files` maps
  it back to the HFE so the tree, title and backups name the `.hfe`. `_images` holds the HFE paths throughout.
- **Write-back is per edit, not on close:** `_run_edit` calls `Workspace.sync()` after every edit (akaiutil has flushed the working copy by
  then). `sync` patches only the changed sectors into the HFE, decodes the result and compares it before moving it into place.
  A failure doesn't fail the edit: it is reported afterwards (`_report_hfe_failures`), the working copy is kept, and it is retried
  after the next edit and when images are closed (`_hfe_written`, which asks before discarding what can't be written).
- The backup question is asked about the `.hfe` itself, and Revert replaces it: the next open sees the file changed (size and time)
  and decodes afresh. A working copy is reused only while the HFE is untouched since the last decode or write-back.
- **A damaged disk** (sectors whose checksum fails, or that are missing) is opened anyway, read-only: `hfe.decode(strict=False)` blanks
  those sectors, `Workspace` records them (`damaged()`), and the window warns once per file (`_warn_damaged_hfe`), opens the engine
  read-only whatever was asked, refuses Allow Editing, and `sync`/`save_as` never write such a disk. (Checked on the three real
  damaged disks of the 120 downloads: akaiutil lists their files.)
- **Repair** of a damaged HFE is the GUI's own (hfe.py `repair`, see `hfe-plan.md`): into a *new* file only, never the original, with every
  other sector checked unchanged before anything is written. `floppy_map.py` names the files hit: it reads only the chain table
  (FAT, after the 64 directory entries of the header; 4 header blocks low density, 5 high density, 12 more behind it on an S3000
  floppy) and takes names and start blocks from akaiutil's own listing.
- A header problem (v3, not MFM, wrong geometry) is caught by `importing.disk_image_problem` -> `hfe.problem` before akaiutil starts;
  a damaged sector shows up when the file is decoded, which for a newly *added* image happens before akaiutil is restarted, so the
  disks already open stay open.
- `File > Save Disk as HFE…` is for floppies; an HFE source keeps its own track layout (patched copy), a flat one is encoded afresh
  (both densities).

## Names
- akaiutil only takes a file name that ends in a type: `.` and a letter (+ `1`, `3` or `9`), `.S9C`, `.CD`, `.S+`, or `.X` and two hex
  digits (the type byte itself; `.X00` and `.XFF` are refused). So a `.txt` can't be written under its own name. The GUI stores any
  other file as `<its name>.X01` (`importing.RAW_TYPE`, `raw_stem`): `NOTES.TXT.x01`, contents untouched, the volume's OS version
  put on it. A hex type belongs to no sampler, so it is accepted in every kind of volume.
- An Akai name byte is one of `0-9 A-Z space # + - .`; akaiutil turns anything else into `.`, uppercases (except S900 volumes) and
  maps `_` to a space. 12 characters (10 on an S900). `akai_names.py` checks all of this before akaiutil sees a name.
- A file's type is its extension (`KICK.S3`); **renaming with a different extension changes the type**, so the GUI only edits the
  part before it.
- `mkvol` (no suffix) isn't recognised in 4.6.8; use `mkvol1/3/9/3cd`. `mkvoli*` take a volume number.

## Things that silently go wrong
- **`wav2sample*` and `put` replace a file of the same name without asking.** `importing.unique_base` numbers new names instead.
- **`put` and `wav2sample*` accept the wrong sampler's files in a volume** (an S3000 sample in an S900 floppy). The GUI holds them
  back by default and asks (`importing.sampler_mismatch`; "Add anyway" in the import dialog, a question before a paste or drop,
  `transfer.mismatches`), because people do keep S1000/S900 images for an S3000 sampler. The contents are copied byte for byte but the
  **OS version is replaced by the volume's**: an S3000 sample (17.00) put into an S1000 volume reads 4.40, and an S900 volume has none.
  Whether a real S3000 lists such a file is untested (see the roadmap).
- `put` of a `.S9C` into a non-S900 volume writes a wrong OS version; refused, and the one mismatch "Add anyway" doesn't lift. Into an S900 volume it keeps the "uncompr" value.
- `put` doesn't carry a file's **tags**; `reni` (a move inside one partition) does. A cross-partition move is copy + delete.
- **Fixed in 4.7 (see below): `settagi`, `clrtagi`, `settagall` and `clrtagall` crash akaiutil 4.6.7 and 4.6.8** (exit 139): they call
  `akai_rename_file(..., vp=NULL, ...)`, which then reads `vp->type` in its tags section. Nothing is written first. The GUI never
  uses them on those versions. The fix, in 4.7, is `voldstp->type` there.
- What does carry tags is **tar**: `tarc` writes a file's tags into the entry's link-name field (`\0T` + the 4 tag bytes) and `tarx`
  reads them back, for S3000/CD3000 volumes only (the OS version rides in the `devmajor` field, in octal). `tags.tagged_file_tar`
  builds a one-file tar, and `engine.copy_file(tags=...)` imports it. The tar entry's name is the plain file name (spaces and all).
- Tags are numbers (1-26) into the **partition's** 26 tag names (`lstags`, which lists the partition of wherever the prompt is, even
  inside a volume). Another partition can use other names, so a copy maps tags by name (`tags.map_tags`). Floppies have none.
- A format of a disk makes other blank images in the session print "invalid format" every restart: don't read that as failure.

- **akaiutil 4.7 tag commands** (measured 2026-11-24 on generated images and a copy of a real one; no real sampler): `settagi <file> <tag>`, `clrtagi <file> <tag|all>`,
  `settagall <tag>`, `clrtagall <tag|all>`, inside a volume. They change only the tag bytes (setting and clearing everything gave a byte-identical image). A fifth tag:
  `no free tag entry`; a number outside 1-26: `invalid tag index`; setting a tag a file has: accepted, no change. An S900 volume says `no tags for this volume type`.
  An S1000 floppy file accepts tags (there are no names for them: `lstags` is per partition). `rentag <n> <name>` renames a tag of the partition (capitals, `_` for a space).
  `settagall`/`clrtagall` act on the whole volume, so `engine.change_file_tags` uses the per-file commands (`settagi`/`clrtagi`), clears first, and checks the listing afterwards.
  It leaves OS files of S1000-and-older volumes alone: their four tag bytes hold the sampler model (os-floppy.md).
- **`setfiltertag <n>` / `clrfiltertag <n|all>`** set a tag filter in the akaiutil *session* (shown by `lstags` as "filter tags"): `dir` (`akai_list_vol`) and the tar exports then list/export only files
  carrying all of the filter tags (up to four; a file with S1000-style tag bytes matches only an empty filter). **The GUI doesn't use it**: it is state in the process, so one left set would hide files from every
  later listing. The tag menu next to the search box filters in the GUI instead, from the tags the catalog already holds (`search.matches_tag`, matched by tag name).
- **`rentag <n> <name>`** (all versions we looked at; measured 2026-11-24 on 4.7) renames tag *n* of the partition akaiutil is in, which must be at partition level
  (`cd /disk0/A`, not inside a volume), on a writable disk. The name is stored as an S1000/S3000 name (12 characters, capitals; `_` reads as a space), and a name needs at least one character
  because the command takes it as an argument, so a tag can't be blanked. `rentag 30 X` says `invalid volume index` (wrong wording). A partition that isn't `HD` says `no tags in this partition type`.
  `engine.rename_tags` checks every name first, writes only the ones that differ, then reads `lstags` back and compares; names are per partition (`_tag_names` is refreshed).

## Copying volumes
- `copyvoli <n> <dst-path>` (run from the source partition) copies a volume across partitions and disks with everything: the files
  with their OS versions and tags (raw tag numbers, not remapped), volume parameters, load number. `copypart` does a partition.
- It **merges silently** into a volume that already has the destination's name, replacing same-named files; **copies the load
  number as it is** (two volumes can end up with load number 5); and when the partition fills up it prints
  `not enough space left` / `cannot create file` / `copy error` and **leaves the half-made volume behind**.
  `engine.copy_volume` checks the name and the room first, clears a clashing load number (`setlnumi <n> OFF`), and deletes the
  copy (`delvoli`) if the output says it failed or the file counts differ.
- **`copypart` has the same faults for every volume** (merges same-named volumes and replaces their files, stops half way when the
  partition fills, copies no tag names, takes the load numbers as they are). `engine.copy_partition` doesn't use it: it checks names,
  volume slots and room for all volumes first, then runs `copy_volume` per volume, removes the ones already copied if one fails, and
  runs `copytags` afterwards if the destination still has the default names (`TAG A`..`TAG Z`).
- A destination is a path like `/disk1/B/NEW_NAME`: an underscore for a space, as everywhere.
- Native `copyi` also keeps tags (by number), so the tar route in `tags.py` is only needed to map tags by name.

## Editing a sample's header
- akaiutil 4.6.8 has no command that edits one. `geti <n> 0 192` reads the first 192 bytes (the end offset is exclusive: 191 gives 191 bytes).
- **akaiutil 4.7 has `puti <external-file> <file-index> [<begin> [<end>]]`** (file name first, then the index; the reverse of `geti <n>`), which writes
  bytes into an existing file in place. Measured 2026-11-24 on the generated images and a copy of a real one (HD0): a 2-byte header patch changed exactly
  those 2 bytes of the image and nothing in the directory entry, neighbours or audio; it worked on S3000, S1000 and S900 samples and on a `.P3`. It never grows a
  file (`invalid end` past the size, `invalid begin/end`), truncates a longer external file, and writes only the length of a shorter one. It always prints
  `importing "NAME"` first. 4.7's `geti` now also refuses a range past the end (`invalid end`) and leaves a 0-byte file behind when it does.
- `engine.edit_sample_settings` uses `puti` when the akaiutil is 4.7 or newer (`engine.can_put_bytes`): `geti` the file, patch the header, `puti` the header
  bytes back, check the directory entry (name, size, start block, OS version, tags) and the whole file, and `puti` the old header back if a check fails. No spare
  slots, no temporary names. Older akaiutil takes the long way below (`_edit_settings_by_rewrite`); tests run both paths.
- The long way (4.6.x, `_edit_settings_by_rewrite`): patch the header (`akai_header.write_settings`: only the edited fields change, and a loop entry
  that wasn't touched keeps its exact bytes), import the whole sample through the tar route under a temporary name (`#EDIT.S3`),
  move the original aside to a spare slot (`#OLD.S3`), `reni` the copy to the original name and slot, `fixramnamei` (the name
  inside the header is otherwise the temporary one), check size, OS version, tags and audio, and only then `deli` the old copy.
  A failed step puts the original back. It needs two free slots.
- A loop's `at` is its last frame and `len` how far back it starts; time 0 is a loop that is off; entries after `lnum` can hold
  leftovers (a real sample had `at=1473175` in an off loop), so nothing but time decides whether a loop is on.
- The end marker of a normal sample is its last frame (`slen - 1`).

## Exporting audio
- `sample2wav*` writes fmt, data and the `S1H `/`S3H ` header chunk, and **no `smpl` chunk**, so a DAW sees no loops or root note.
  `audio/wavfile.add_loop_info` reads the header (`akai_header.parse_header`) and appends one: root key, and the loops that are
  in use (time not 0, within the sample) if the play mode loops (LOOP / LOOPNOTREL). The tuning offset isn't carried.
  S900 samples have no such chunk and get nothing.

## Importing audio
- `wav2sample*` reads mono/stereo 8/16/24/32-bit PCM at any rate (it writes the rate into the header) and splits stereo into
  `NAME      -L` / `-R` itself (name padded to 10 characters first, as the samplers' stereo pairs are).
- It takes the sample header from an **extra chunk after the audio data**: `S1H ` / `S3H ` (150 / 192 bytes) for S1000 / S3000.
  That's how a WAV exported by `sample2wav` keeps its loops on the way back, and how the GUI sets loops and root note
  (`akai_header.py`, layout in its docstring). It trusts the header: the sample rate, end marker and loops must be right.
- Chunk skipping in akaiutil's reader doesn't allow for the pad byte after an odd-sized chunk, so `audio/wavfile.py` writes a
  clean WAV (fmt, data, header chunk) rather than appending to someone else's.
- A loop's `at` is its **last frame** and `len` how far back it starts, so a WAV `smpl` loop (start, last frame) maps directly.
  Time 9999 is HOLD.

## Slots and sorting
- A sampler lists a volume's files in slot order (the `fnr` column) and has no way to reorder them. `reni <slot> <name> <new slot>`
  moves a file to a *free* slot inside the same volume (it copies the whole directory entry, so tags and OS version come along).
  `sorting.plan_moves` turns "sorted order" into such moves, taking each cycle of files that must trade places through one spare slot.
- `reni` re-encodes the name it is given (and derives the type from the extension), so a name that doesn't survive that exactly would
  be altered by moving it. `engine.sort_files` refuses to start if any name that has to move wouldn't, then re-lists the volume
  and checks that every (name, size, start block, OS version, tags) is still there.
- Programs are numbered by their place among a volume's programs (that's what a MIDI program change picks), and a multi refers to
  its programs by name. So sorting only samples is always safe; sorting everything can change which program a number selects.

## Other
- `dir` of a volume has a `(max. N)` volume limit; a floppy holds one volume.
- `reni <idx> /disk0/A/VOL_NAME/NEW_NAME.S3` moves between volumes of one partition.
- `updateuncompri` recomputes the uncompressed size of a compressed S900 file.

## Direct-to-disk (DD) takes (measured 2026-10-30 on akaiutil-made images, akaiutil 4.6.8; no real sampler recording seen yet)
Plan: [dd-takes-plan.md](dd-takes-plan.md). Everything below was tried on throwaway images in the scratchpad, with takes made by
`wav2take` from sine and noise WAVs.
- **Making a DD partition:** `formatharddisk3 <part> <total>`. The DD partition is what is left of the *file* after `<total>`, so
  `<total>` must be smaller than the image: a 100 MB image with `formatharddisk3 10M 40M` gives A-D (10 MB each) and `DD` (59.8 MB).
  `formatharddisk3 5M` or `15M 100M` gives none (the leftover became another HD partition, or nothing). `tests/imagegen.py` has no DD
  image yet. `engine.list_partitions` already reports it (`is_dd` true) and `list_takes_text` returns its `dir`.
- **Commands** (all need `cd /diskN/DD`; elsewhere "must be inside a DD partition"): `dir` (table), `tinfoi <n>` / `tinfoall`
  (key: value lines), `take2wavi <n>` / `take2wavall`, `tgeti <n>` / `tgetall` (a `.TK`), `wav2take <wav>` (`*.wav` for all), `tput <tk>`,
  `treni <n> <name>`, `tdeli <n>`, `lstfati <n>` (cluster chain). Takes are 1 to 256.
- **`dir` columns:** `tnr tname cstarts cstarte csizes csizee scount srate stype`. Names have spaces and are cut at 12, so parse
  from the right. `scount` is in 16-bit words, so a stereo take counts both channels (one second of 44.1 kHz stereo is 88,200).
- **Space:** a cluster is 262,144 bytes. A take is `ceil(scount*2/262144)` sample clusters plus 1 envelope cluster (still 1 for a
  20 s take). A one-second mono take costs 512 KiB. A full partition answers "not enough space left / cannot allocate FAT chain for
  sample / WAV import error", leaves no half take, and the free space afterwards is what it was (deleting a take gave back 0.5 MB).
- **Import takes the first free slot** (it printed `importing "x" to take N`), so a delete followed by an import reuses the number.
  Duplicate names are allowed (two takes called `MONO`); rename and delete go by number.
- **Formats that imported** (straight into `wav2take`): 16-bit mono/stereo at 44.1 and 48 kHz, 8-bit mono 22.05 kHz, 24-bit stereo
  44.1 kHz (stored rate = the WAV's). Whether akaiutil converted the 8- and 24-bit audio correctly was never checked: the GUI writes
  16-bit WAVs itself (`takes.prepare_audio`) and gives akaiutil only those.
- **Silent trap: the rate is stored in 16 bits.** A 96 kHz mono WAV imported without a message and showed `srate 30464`
  (96000 mod 65536). The GUI has to refuse rates over 65,535 before calling `wav2take`.
- **Defaults on import:** fade in 10 ms, fade out 50 ms, stereo level 99, pan 0, stereo mix ON, MIDI channel 16, note 60, start mode
  `M.NOTE+DEL`, de-emphasis OFF, pre-delay 500, output level 50, output/FX bus OFF, send level 25.
- **Export:** `take2wavi 2` wrote a stereo 44.1 kHz 16-bit WAV with all its frames, and a take whose chain was fragmented
  (clusters 1, 2, 13...) came back **bit-identical** to the WAV it was made from. `tgeti 1` wrote `MONO.TK`, 524,352 bytes for
  two clusters (64 more than the clusters; presumably a header, not read from the source), and `tput` of it recreated the take.
  An unused take number answers "cannot export take to WAV".
- `treni` and `tdeli` print nothing when they work (so anything printed is the reason they didn't); `wav2take` and `tput` print
  `importing "x" to take N`, and the way to know they worked is to list the takes again.
- Not checked: any take recorded by a sampler; long fragmented chains; 32-bit or float WAVs; several DD partitions on one disk.

## Effects files (`.X`)
akaiutil knows the type and the name and nothing else: no struct, no parser, no edit command. The format is undocumented anywhere
found; see [fx-files.md](fx-files.md). The GUI lists, copies, renames and deletes them like any file.
