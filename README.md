# akaiutil-gui

A cross-platform Qt GUI for [akaiutil](https://sourceforge.net/projects/akaiutil/)
(Klaus Michael Indlekofer's tool for Akai S900/S1000/S3000 disk images).

**Status: early.** Disk images open read-only. Nothing is written to one until you switch on **Edit > Allow Editing**, and
there is no undo (see [Editing](#editing)).

- **Browse** disks, partitions, volumes and files, across as many disk images as you like at once (File > Add Disk Image,
  or drop files on the window; right-click a disk to close it or show its file). Adding or closing an image restarts akaiutil
  on the new set, so the disk numbers can change; what you had selected and the editing mode carry over. A space bar under the tree shows what each volume uses (coloured to
  match the swatch beside it) and what's free; the Details pane turns akaiutil's terminal output into readable labels and
  values ("Raw output" shows the original).
- **Preview samples.** Select a sample to see its waveform and play it, with its loop: the attack once, then the loop
  repeating (until you stop it, if its loop time is Hold). Click the waveform to play from there, press Space to play and
  stop. The audio backend is the same one AKAISDS uses (miniaudio), including its macOS loop-playback fixes. Only
  S1000/S3000 loops are previewed as loops; S900 samples play once.
- **Search** every open disk at once (Ctrl+F): type words (they all have to match, in the file's or its volume's name), and
  filter by type. Double-click a result to show it in its volume. Results can be previewed and exported like any file.
- **Export** files as they are, or samples as WAV (with the root note and loop points in a `smpl` chunk, so a DAW sees them).
  Right-click a volume, partition or disk to export all its samples at once. Files from several volumes go into one folder each.
- **Edit** (opt-in): rename and delete files and volumes, create volumes, import samples, format a disk, and make new empty
  disk images.
- **Sample settings** (Edit > Sample Settings…): change an S1000/S3000 sample's root key, tuning, play mode, markers and its 8
  loops, on a waveform with draggable markers, zoom and click-to-preview (the same mechanism as AKAISDS). The sample is rewritten with the new header and put back in the same slot, with its name, tags and OS version.
- **Settings** (File > Settings, or Cmd/Ctrl+,): theme, audio output device and buffer size, whether samples play when
  selected, a default export folder, which akaiutil to use, and whether to check for updates.
- **A log file** (Help > Show Log File) of what the app did and every error, with an option to record every akaiutil command,
  for bug reports. A `crash.log` beside it catches the program dying in native code. See "Reporting a problem" in the manual.
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

## Editing

Every image opens read-only, and the status bar says so. **Edit > Allow Editing** (or just asking for a change from a
right-click menu) asks first, then restarts akaiutil in read/write mode on the same images. From then on each change is
written to the image file straight away and **can't be undone**, so the first time you switch editing on the app offers to
**back the images up** (kept in the app's data folder, the newest 5 per image), and **File > Revert to Backup…** puts one back.
Backups older than 14 days (changeable in Settings) are deleted when the app starts.
Opening images again puts the app back in read-only mode.

- **Rename** (F2, or right-click): volumes and files. A file's type (`.S3`, `.P3`...) can't be changed this way. Akai names
  are up to 12 characters (10 on an S900) of letters, digits, space and `# + - .`, always in capitals.
- **Delete** (Delete key, or right-click): files, or a whole volume with everything in it. Always asks first.
- **New Volume** (right-click a partition): name, volume number, type and load number.
- **Format Disk** (right-click a disk): erases it and sets it up as an S1000, S3000, CD3000 or S900 disk, or a floppy of
  the same density; hard disks are cut into partitions of up to 60 MB.
- **Copy and move files** between volumes, partitions and disks: Copy / Cut / Paste (Edit menu), or drag files from the list onto
  a volume in the tree (hold Shift, or Cmd on a Mac, to move instead of copy). A move inside one partition keeps everything about
  the file; a copy keeps an S3000 file's tags too (matched by tag name). A name already in the target gets a number. Drag files out of the list to
  Finder, Explorer or your DAW: samples arrive as WAV files, or exactly as Akai stores them if you hold Option/Alt when you start
  the drag.
- **Copy a whole volume** (right-click it > Copy Volume To…) into any hard disk partition on any open disk, with its files, tags and
  settings. It checks there is room first, and doesn't leave half a volume behind if something goes wrong.
- **Sort a volume** (Edit > Sort Files by Name, or right-click a volume): the samplers list a volume's files in the order they
  were added and can't reorder them, so this puts them in alphabetical order (numbers in order, ignoring case) by moving files
  between slots; the files themselves aren't touched. You can sort only the samples and leave programs and other files where
  they are, which matters because a program's number (what a MIDI program change selects) is its place among the programs.
  Settings can do it automatically after every import or paste.
- **Import** (drop files on a volume, or Edit > Import Files): WAV, AIFF and FLAC (also OGG and MP3) become samples for the
  S1000, S3000 or S900 (you're asked which, for stereo files whether to split them into left and right samples or mix them to
  mono, and for an S900 whether to compress). Anything that isn't 16-bit or is faster than the sampler can play (44.1 kHz; 40 kHz
  on an S900) is converted first; a 16-bit WAV that is fine is used exactly as it is, which keeps the loops of a WAV this app
  exported. The dialog can also set a sample's loops and root key from the WAV (the `smpl` chunk many DAWs and sample editors
  write), take the root key from a name like `Piano_C3`, trim silence and normalise. Files are added in alphabetical order (numbers in order: `kick 2` before `kick 10`), and a name already in the
  volume gets a number rather than replacing the old file. Akai files (`KICK.S3`, `.P3`, ...) go in unchanged, but only into a
  volume of the right sampler: an S900 volume takes only S900 files, an S1000 volume only S1000 ones.
- **File > New Disk Image** makes a new empty image (a hard disk up to 512 MB, or a floppy), formatted and opened.

## Audio notes

Preview plays through your system's default output, or the device chosen in Settings. If you hear clicks, raise the buffer
size there; if playback feels laggy, lower it. Tests never open an audio device: `AKAIUTIL_GUI_NO_AUDIO=1` turns audio off
entirely (the test suite sets it, and so does CI).

## How it works

The GUI starts `akaiutil -r <images>` (without the `-r` once editing is allowed) as a subprocess and types commands into its prompt, using the
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
- Not yet: importing samples (WAV) into images, copying files between volumes, DD takes (listed as text only), real floppy/physical-drive access.

## Help

**Help > User Manual** (F1) in the app has the full guide, with screenshots: opening and browsing images, search, preview,
export, and everything that edits (renaming, importing, copying, sorting, formatting). It's the same file as
[the manual](src/akaiutil_gui/ui/help/manual.md), which `uv run python scripts/make_manual_screenshots.py` regenerates the
pictures for.

## For contributors

[dev_docs/](dev_docs/README.md) has the roadmap and notes on how akaiutil really behaves.

## Licence

GPL-2.0-or-later, matching akaiutil's own "version 2 or any later version" notice. This app
only *runs* akaiutil; it doesn't link to or contain its code.
