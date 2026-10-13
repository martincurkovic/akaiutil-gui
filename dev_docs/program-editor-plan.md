# Program and keygroup editing: plan

Written 2026-10-12. Nothing here is built yet. This is the plan for editing S1000/S2000/S3000 programs and keygroups inside a
disk image, and for sharing code with [AKAISDS](../../AKAISDS) (the same editor, but live over MIDI).

## Why it should work

- A program file (`.P1`/`.P3`) is the program header followed by N keygroups, no file header: 150-byte blocks for the S1000,
  192-byte blocks for the S2000/S3000, N at header byte 42. These are the same blocks AKAISDS moves over SysEx.
- AKAISDS already has a Qt-free reader/writer for that layout (`src/core/akai_program_file.py`, 224 lines) and decodes/encodes
  fields through the `s3k.params` registry, so bytes the app doesn't model travel untouched.
- `engine.edit_sample_settings` is already the right transaction: `geti` the file, patch the bytes, import under a temporary
  name through the tar route, `reni` into place, `fixramnamei`, check size/OS version/tags, only then `deli` the old copy, and
  roll back on any failure. A program edit is the same thing with different bytes.
- On a disk image, adding or deleting a keygroup is just a list edit. AKAISDS needs a rebuild workaround for S1000 keygroup
  deletion only because it has to do it over SysEx.
- `references.py` already knows which samples each program plays, so zone sample pickers can offer the samples actually in the volume.

## Open questions (settle first)


1. **Pointers.** AKAISDS's `build_file` writes FIRSTKG as one block length in bytes (`0xC0` for a `.P3`) and each NXTKG as the
   next block's file offset. The real S3000 program in `tests/data/TEST_PROGRAM.P3` has FIRSTKG `0x600C` and the keygroup's NXTKG
   `0x6018`: that looks like an absolute address in 16-byte units (192 / 16 = 12), not a relative offset in bytes. Editing fields
   in place never touches pointers, but adding/removing keygroups does. Check against akaiutil's source and a few real images
   (S1000 and S3000, saved from a sampler and written by akaiutil) before writing any keygroup add/delete. AKAISDS's own test plan
   says its layout was never checked against a file from another tool, so the answer is also worth reporting there.
2. **Does `put`/tar of a program file need anything extra?** (name inside the header, OS version, tags.) `fixramnamei` and the
   copy path probably cover it, but it hasn't been tried on programs.

Licensing is not a concern: the same person owns the copyright on both apps, so code can move between them freely. (`s3k`/`s3ked`
are by someone else, GPL-2.0-or-later, which both apps can already use.)

## Scope

- In: `.P1` (S1000) and `.P3` (S2000/S3000) programs and their keygroups.
- Out for now: S900 `.P9` (a different format; AKAISDS has `s950_program.py`), multis (see ROADMAP "Next" 2).
- Possible side feature: S1000 to S3000 program conversion (AKAISDS has `convert_s1000_to_s3000`, with neutral values measured on a real S2000).

## Phases

### 1. Settle the format
- Resolve the pointer question above with the fixture, akaiutil's source and real images.
- Add the findings to `akaiutil-notes.md`.
- Parse every program in `testing_images`/real images with the AKAISDS parser; note what fails.

### 2. Read-only viewer
- Parse a program from the open image (`geti`) into program fields plus keygroups, using the `s3k.params` registry.
- Show them in the details view: program fields, a keygroup list with key ranges, zones and sample names.
- Warn about zones that name samples missing from the volume.
- Needs the shared data layer (phase 3), or a temporary copy of it, plus a decision on how to depend on `s3k`.

### 3. Shared data layer
- Create a small package (working name `akai-program-kit`) with the Qt-free parts: program file parse/build, field codec over
  `s3k.params`, conversion, zone/sample-name helpers, field metadata (labels, ranges, tooltips).
- Pin it by git rev from both apps. AKAISDS already pins `s3ked` this way, so the build and CI pattern exists.
- Move the code out of AKAISDS with its tests; AKAISDS switches to the package.
- Avoid vendored copies (they drift) and a monorepo (too heavy for two apps).

### 4. Field editing
- Edit program and keygroup fields in memory, commit with Save through the sample-edit transaction (new `engine.edit_program`).
- Reuse the backup-before-write behaviour; add revert/undo for the pending edits.
- Start with a few fields (levels, tuning, filter, envelopes, key/velocity ranges, zone samples), then widen.
- Tests: byte-exact round trip of an unedited program, field patches change only their bytes, rollback on a failed step.

### 5. Add, delete and copy keygroups
- Only after the pointer question is closed. Rewrite pointers on every structural change.
- Check the file still parses, `GROUPS` matches, and akaiutil lists the program afterwards.

### 6. Shared widgets and editor view
- Move the self-contained widgets (`knob`, `envelope_graph`, `keygroup_range_bar`, `note_spinbox`, `fine_slider`, `editor_layout`) into the package.
- AKAISDS's `program_editor_window.py` is about 9,000 lines and tied to `BridgeWorker` signals, so it can't be lifted as it is.
  Split it into a view and a backend interface: a MIDI backend that writes each change straight away, and a disk-image backend that
  edits an in-memory `ProgramFile` and commits on Save.
- Design that interface from what phase 4 actually needs, not from one consumer's guess. Do this last because it is a refactor of
  the most hardware-tested code in AKAISDS.

## Risks

- Nothing edited on disk can be played here; it is only proven once an image is loaded into a sampler (same verification debt as sample edits in the ROADMAP).
- Replacing a file needs two free directory slots (same limit as sample edits).
- The shared package couples the release cycles of two apps; pin by rev and bump deliberately.
- `s3k.params` notes some ranges that real hardware contradicts (AKAISDS keeps overrides, e.g. pitch bend down 0..24); the shared package must carry those overrides.
