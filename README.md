# akaiutil-gui

A cross-platform Qt GUI for [akaiutil](https://sourceforge.net/projects/akaiutil/)
(Klaus Michael Indlekofer's tool for Akai S900/S1000/S3000 disk images).

**Status: early.** It opens Akai disk images read-only and never writes to them.

- **Browse** disks, partitions, volumes and files. A space bar under the tree shows what each volume uses (coloured to
  match the swatch beside it) and what's free; the Details pane turns akaiutil's terminal output into readable labels and
  values ("Raw output" shows the original).
- **Preview samples.** Select a sample to see its waveform and play it, with its loop: the attack once, then the loop
  repeating (until you stop it, if its loop time is Hold). Click the waveform to play from there, press Space to play and
  stop. The audio backend is the same one AKAISDS uses (miniaudio), including its macOS loop-playback fixes. Only
  S1000/S3000 loops are previewed as loops; S900 samples play once.
- **Search** every open disk at once (Ctrl+F): type words (they all have to match, in the file's or its volume's name), and
  filter by type. Double-click a result to show it in its volume. Results can be previewed and exported like any file.
- **Export** files as they are, or samples as WAV. Files from several volumes go into one folder each.
- **Settings** (File > Settings, or Cmd/Ctrl+,): theme, audio output device and buffer size, whether samples play when
  selected, a default export folder, which akaiutil to use, and whether to check for updates.
- **Check for updates** from the Help menu, and automatically once a day at startup (switch it off in Settings). It only ever
  points you at the GitHub release page; it doesn't download anything itself.

Qt 6.12 LTS via PySide6 · Python 3.12+ · uv · looks like [AKAISDS](../AKAISDS) (shared light/dark theme).

## Download & Installation

Download the latest release from the Releases tab on GitHub. akaiutil is included, so there's nothing else to install.

**macOS** (14 or newer, Apple Silicon or Intel): open the `.dmg` and drag akaiutil-GUI to Applications.

**Windows**: run `akaiutil-GUI-Setup.exe`.

**Linux**: unzip the download and run the `.AppImage` (you may need `chmod +x` on it first).

The macOS and Windows builds are not code-signed, so they will trigger security warnings (macOS Gatekeeper, Windows
SmartScreen). You can bypass them the usual way: on macOS, right-click the app and choose Open (or allow it under System
Settings > Privacy & Security); on Windows, choose "More info" > "Run anyway". If you'd rather not, build it yourself
from source ([BUILDING.md](BUILDING.md)).

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

## Audio notes

Preview plays through your system's default output, or the device chosen in Settings. If you hear clicks, raise the buffer
size there; if playback feels laggy, lower it. Tests never open an audio device: `AKAIUTIL_GUI_NO_AUDIO=1` turns audio off
entirely (the test suite sets it, and so does CI).

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
