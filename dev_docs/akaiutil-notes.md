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
- An Akai name byte is one of `0-9 A-Z space # + - .`; akaiutil turns anything else into `.`, uppercases (except S900 volumes) and
  maps `_` to a space. 12 characters (10 on an S900). `akai_names.py` checks all of this before akaiutil sees a name.
- A file's type is its extension (`KICK.S3`); **renaming with a different extension changes the type**, so the GUI only edits the
  part before it.
- `mkvol` (no suffix) isn't recognised in 4.6.8; use `mkvol1/3/9/3cd`. `mkvoli*` take a volume number.

## Things that silently go wrong
- **`wav2sample*` and `put` replace a file of the same name without asking.** `importing.unique_base` numbers new names instead.
- **`put` and `wav2sample*` accept the wrong sampler's files in a volume** (an S3000 sample in an S900 floppy). The GUI refuses
  (`importing.akai_file_problem`).
- `put` of a `.S9C` into a non-S900 volume writes a wrong OS version; refused. Into an S900 volume it keeps the "uncompr" value.
- `put` doesn't carry a file's **tags**; `reni` (a move inside one partition) does. A cross-partition move is copy + delete.
- A format of a disk makes other blank images in the session print "invalid format" every restart: don't read that as failure.

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

## Other
- `dir` of a volume has a `(max. N)` volume limit; a floppy holds one volume.
- `reni <idx> /disk0/A/VOL_NAME/NEW_NAME.S3` moves between volumes of one partition.
- `updateuncompri` recomputes the uncompressed size of a compressed S900 file.
