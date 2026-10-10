# akaiutil-gui

A cross-platform Qt GUI for [akaiutil](https://sourceforge.net/projects/akaiutil/)
(Klaus Michael Indlekofer's tool for Akai S900/S1000/S3000 disk images).

**Status: proof of concept.** It browses disk images read-only (disks → partitions → volumes → files),
shows akaiutil's details for whatever you select, and exports files raw or as WAV.
A space bar under the disk tree shows what each volume uses (coloured to match the swatch beside it in the tree) and what's free; it follows the selection. The Details pane translates akaiutil's terminal output into readable labels and values ("Raw output" shows the original). It never writes to an image.

Qt 6.12 LTS via PySide6 · Python 3.12+ · uv · looks like [AKAISDS](../AKAISDS) (shared light/dark theme).

## Quick start

```sh
uv sync                                        # Python deps
uv run python scripts/get_akaiutil.py          # fetch + verify + build akaiutil 4.6.8 into build/bin/
uv run python tests/imagegen.py build/images   # optional: synthetic S3000/S1000/S900 images to play with
uv run akaiutil-gui build/images/s3000_hd.img  # or just `uv run akaiutil-gui` and use File ▸ Open
```

You can also drop image files onto the window. `View ▸ Console` shows the raw conversation with akaiutil.

`scripts/get_akaiutil.py` downloads the pinned release from SourceForge, checks its SHA-256, and compiles it
(macOS/Linux: needs `cc`/`gcc`/`clang`). On Windows it uses the official prebuilt `akaiutil.exe`
(`--build` compiles with MinGW instead). The GUI finds the binary via, in order: *Tools ▸ Locate akaiutil…*,
`$AKAIUTIL_BIN`, `build/bin/`, then `PATH`.

## Tests

```sh
uv run pytest
```

The integration and GUI tests build real disk images with the real akaiutil binary (they skip if it isn't
built). The GUI tests run offscreen.

## How it works

The GUI starts `akaiutil -r <images>` as a subprocess and types commands into its prompt, using the
`<cwd> >` prompt as the end-of-response marker. Fixed-width listings (`dir`, `dinfo`, …) are parsed in
`engine/parse.py`; everything above that sees typed objects (`engine/models.py`, `engine/api.py`).

```
src/akaiutil_gui/
  describe.py   akaiutil's info text -> readable sections (Qt-free; goes away with a ctypes engine)
  engine/   session.py (subprocess + prompt protocol) · parse.py · api.py (AkaiEngine) · models.py · locate.py
  ui/       main_window.py · disk_tree.py · file_table.py · tasks.py (worker threads) · theme.py + style.qss.template
```

Things worth knowing:

- `engine/api.py` is the seam for the planned move to a shared library (ctypes/cffi): a new engine just needs
  the same methods and return types. `parse.py` is the part that goes away.
- akaiutil writes exports into *its own* working directory and can't take paths with spaces, so it runs in a
  scratch dir and Python moves the results to your chosen folder (asking before overwriting).
- Names with spaces: akaiutil accepts `_` for ` ` on its command line, but the GUI avoids that ambiguity by
  addressing volumes and files by index (`cdi`, `geti`, …).
- Not yet: importing into images, writing/formatting, DD takes (listed as text only), real floppy/physical-drive access.

## Licence

GPL-2.0-or-later, matching akaiutil's own "version 2 or any later version" notice. This app
only *runs* akaiutil; it doesn't link to or contain its code.
