# Effects files (`.X`): what is known

Written 2026-10-30. **Not built, and blocked on hardware.** Someone asked whether the GUI could edit an effects file. It can't
today, and nobody has documented the format. This note is for whoever has the hardware to work it out.

## Who has effects

- Files of type `X` are "S1100/S3000 effects" (akaiutil's own label). The effects come from the **EB16 board** (S2000/S3000XL/
  S3200XL) or the S1100's own effects board; the S1000 has none, so there is nothing for an S1000-only user to edit.
- Effects files of different sampler types are incompatible (comment in `akaiutil_file.h:431`).

## What akaiutil 4.6.8 knows (read from the source, 2026-10-30)

- `akaiutil_file.h:430-437`: type letter `'x'`, default name `EFFECTS FILE`, default size `0x1c90` (7,312 bytes), block IDs `0x00`
  (S1100, S3000) and `0x02` (S3000XL).
- `akaiutil_file.c:111, 1092`: prints "S1100/S3000 effects" and reads the 12-character name from the generic file header.
- **Nothing else.** No struct for the contents, no parser, no edit command. Anything beyond list, get, put, rename, delete means
  reading and writing the file's bytes ourselves (`engine.edit_sample_settings`'s patch-and-reimport route would carry that).

## What was found elsewhere

- **lakai's transcription of Akai's S2800/S3000/S3200 SysEx spec** (<https://lakai.sourceforge.net/docs/s2800_sysex.html>, by Frank
  Neumann, 2002, with his own doubts marked): defines messages `0x2D` (request FX/reverb bytes) and `0x2E` (FX/reverb bytes) with a
  selector (0 FX header, 1 FX assign, 2 FX entry, 3 reverb assign, 4 reverb entry), an effect number, a byte offset and a count.
  **It gives no layout for any of the five parts.** The only effects fields it describes are the program/keygroup bus and send
  fields (`PFXCHAN`, `PFXSLEV`, `KFXCHAN`, `KFXSLEV`).
- **Ohsaki's akaitools S3000 format page** (<https://lsnl.jp/~ohsaki/software/akaitools/S3000-format.html>): lists the file type
  byte only.
- **s3ked** (`~/s3ked`, GPL, same author as this app): `docs/RESOLUTION_NOTES.md` §88 says outright that "no document describes
  it", and measured it over MIDI on an S3000XL with no EB16 fitted:
  - FX header: 12-character name at **offset 3**, reading `EFFECTS FILE`.
  - FX entries and reverb entries: **128 bytes each**, **51 of each** (one machine; the list end cannot be detected, there is no count).
  - Entry names run `REVERB EQ 1` ... `NAMEOFEFFECT`, and `TIGHT ROOM 1` ... `NAMEOFREVERB`.
  - FX assign is all zeroes; reverb assign reads identically to reverb entries (unexplained).
  - Name writes were verified; parameter fields inside an entry were **not** mapped.
  - Writing a multi's FX1 to a value over 204 crashes the machine ("divide overflow"); that is a multi byte, not an effects file byte,
    but shows the effects area is touchy.
  - Expansion-board warning: s3ked treats the EB16 effects pages as fenced because the panel refused them and the project crashed an
    S3000XL twice while exploring.

## The gap that matters

Two lists of 51 entries of 128 bytes is 13,056 bytes. The disk file is 7,312 bytes (`0x1c90`). So the `.X` file is **not** a plain dump of
the SysEx structures (or the entries are smaller on disk, or the file holds something else). That has to be understood before any
field map can be trusted.

## How someone with hardware could do it

1. Save effects files from the sampler (on a disk the GUI can open) with one parameter changed at a time, including a factory
   state, and diff them.
2. Compare with the SysEx read of the same setup (s3ked can read FX over MIDI) to relate bytes to named parameters.
3. Do it per sampler type (S1100, S3000, S3000XL); the files differ.
4. Only then would an editor make sense, and it would be unchecked on hardware like every other edit (see the roadmap).
