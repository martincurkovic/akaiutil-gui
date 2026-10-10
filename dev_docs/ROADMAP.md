# Roadmap

Last updated 2026-10-11. Current status of the app is in the
[README](../README.md) and the manual.

## Verification debt

None of this could be checked by the test runs, which use a fake audio device, offscreen Qt and akaiutil reading its own output back.

- [ ] **A real sampler.** Load an edited, imported or copied image into an S3000/S1000/S900 (or Gotek/SCSI2SD setup). Especially:
      sample-settings edits, imported loops and root notes, stereo `-L`/`-R` pairs (an edit doesn't touch the partner address,
      `stpaira`), S900 samples and compressed S9C, and files copied into a new volume.

## Next

1. **Open an SD card / physical drive directly.** akaiutil can open raw devices. Needs OS permissions per platform and safety
   rails against picking the wrong disk; start read-only, add editing later.
2. **Multis.** akaiutil only prints "S3000 multi" for one, so showing its parts means reading the file's bytes ourselves, which
   needs the format. A multi names programs, so it would also allow "programs no multi uses".
3. **Copy a whole partition** (`copypart`, or `pget`/`pput` to a file): volume copy is done and `copypart` would reuse it.
4. **Editing tags** (setting, clearing, renaming a partition's tag names). Showing them and keeping them on copy are done.
   akaiutil's `settagi` & co crash (see akaiutil-notes.md), so editing means either patching akaiutil or rewriting a file's tags
   with the same tar trick (delete + import, which moves the file's slot), or `rentag` for the names.
5. **Import polish:** AIFF loop points (INST + MARK chunks: the end-marker convention needs checking), fade in/out, a pitch
   fraction from `smpl`, per-file overrides in the import dialog, a progress dialog for big batches.
6. **ctypes/cffi engine** to replace the subprocess and the text parsing in `engine/parse.py`. `engine/api.py` is the seam
   (see the README). Worth doing once the feature set settles: it removes the restart-on-add/close and the text fragility.

## Known limitations

(The user-facing ones are in the manual's "Good to know".)

- Editing is all-or-nothing across the open images (one shared akaiutil process).
- Only mono and stereo audio; more than two channels is refused.
- 24-bit to 16-bit is plain rounding (no dither), so digital silence stays silent.
