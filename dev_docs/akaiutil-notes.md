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
- **`settagi`, `clrtagi`, `settagall` and `clrtagall` crash akaiutil 4.6.7 and 4.6.8** (exit 139): they call
  `akai_rename_file(..., vp=NULL, ...)`, which then reads `vp->type` in its tags section. Nothing is written first. The GUI never
  uses them. A fix would be `voldstp->type` there.
- What does carry tags is **tar**: `tarc` writes a file's tags into the entry's link-name field (`\0T` + the 4 tag bytes) and `tarx`
  reads them back, for S3000/CD3000 volumes only (the OS version rides in the `devmajor` field, in octal). `tags.tagged_file_tar`
  builds a one-file tar, and `engine.copy_file(tags=...)` imports it. The tar entry's name is the plain file name (spaces and all).
- Tags are numbers (1-26) into the **partition's** 26 tag names (`lstags`, which lists the partition of wherever the prompt is, even
  inside a volume). Another partition can use other names, so a copy maps tags by name (`tags.map_tags`). Floppies have none.
- A format of a disk makes other blank images in the session print "invalid format" every restart: don't read that as failure.

## Copying volumes
- `copyvoli <n> <dst-path>` (run from the source partition) copies a volume across partitions and disks with everything: the files
  with their OS versions and tags (raw tag numbers, not remapped), volume parameters, load number. `copypart` does a partition.
- It **merges silently** into a volume that already has the destination's name, replacing same-named files; **copies the load
  number as it is** (two volumes can end up with load number 5); and when the partition fills up it prints
  `not enough space left` / `cannot create file` / `copy error` and **leaves the half-made volume behind**.
  `engine.copy_volume` checks the name and the room first, clears a clashing load number (`setlnumi <n> OFF`), and deletes the
  copy (`delvoli`) if the output says it failed or the file counts differ.
- A destination is a path like `/disk1/B/NEW_NAME`: an underscore for a space, as everywhere.
- Native `copyi` also keeps tags (by number), so the tar route in `tags.py` is only needed to map tags by name.

## Editing a sample's header
- No akaiutil command edits one. `geti <n> 0 192` reads the first 192 bytes (the end offset is exclusive: 191 gives 191 bytes).
- `engine.edit_sample_settings`: patch the header (`akai_header.write_settings`: only the edited fields change, and a loop entry
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
