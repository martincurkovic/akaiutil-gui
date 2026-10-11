# Building From Source

## Prerequisites

- Python 3.12 or higher, and [uv](https://docs.astral.sh/uv/)
- Other dependencies are in `pyproject.toml` and are installed automatically by the build scripts below
- A C compiler. The app bundles akaiutil, so the build downloads its source (checksum-verified) and compiles it. Nuitka needs one too.

#### macOS

- Xcode Command Line Tools (`xcode-select --install`)
- macOS 14 or newer (the Qt 6.12 wheels need it)

#### Linux

- `build-essential` and `patchelf` (can be installed with `sudo apt install build-essential patchelf` on Ubuntu-based distributions)

#### Windows

- A C++ compiler for Nuitka (it will offer to download MinGW if you don't have one). akaiutil itself needs no compiler on Windows: the build uses the official prebuilt `akaiutil.exe`.

## Running From Source

To run the app without building a packaged one:

```sh
uv sync                                        # Python deps
uv run python scripts/get_akaiutil.py          # fetch + verify + build akaiutil 4.6.8 into build/bin/
uv run python tests/imagegen.py build/images   # optional: synthetic S3000/S1000/S900 images to play with
uv run akaiutil-gui build/images/s3000_hd.img  # or just `uv run akaiutil-gui` and use File > Open
```

You can also drag and drop image files onto the window.

`scripts/get_akaiutil.py` downloads the pinned release from SourceForge, checks its SHA-256, and compiles it (macOS/Linux: needs `cc`/`gcc`/`clang`). On Windows it uses the official prebuilt `akaiutil.exe` (`--build` compiles with MinGW instead). The GUI finds the binary via, in order: **Tools > Locate akaiutil…**, `$AKAIUTIL_BIN`, `build/bin/`, then `PATH`.

## Setup and Build

1. Clone the repository
2. Run the relevant script for your platform (located in the `scripts` folder). Note that running a PowerShell script on Windows for the first time will trigger a warning but it can be mitigated by running `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` before running the build script. On Linux or macOS you _may_ need to run `chmod +x build_xxx.sh` on the relevant script for your platform
3. The final build lands in the `src` directory (`akaiutil-GUI.app` on macOS, `akaiutil-GUI.dist` on Linux and Windows)

Each script: syncs the environment, fetches akaiutil into `build/bin/`, writes the version from the nearest git tag into `src/akaiutil_gui/_version.py`, generates `src/pysidedeploy.spec` (not tracked), and runs `pyside6-deploy`. The built app contains the stylesheet template, icons, `THIRD_PARTY_NOTICES.md`, `LICENSE`, and akaiutil (in a `bin/` folder next to the app's executable, where the app looks for it first).

To check a finished build without opening it, run its executable with `--smoke-test`. It verifies the bundled files, that the audio libraries (miniaudio, soundfile/libsndfile, numpy) load and decode a WAV, and that the settings dialog and preview panel build, and it talks to the bundled akaiutil, then exits 0 (or 1 with a report of what's missing). Set `AKAIUTIL_GUI_NO_AUDIO=1` when running it on a machine with no sound card, as CI does. CI does this for every platform.

```
QT_QPA_PLATFORM=offscreen src/akaiutil-GUI.app/Contents/MacOS/main --smoke-test=report.txt
```

## Running Tests

There's a pytest suite in the `tests` directory. Sync the environment first with `uv sync`, fetch akaiutil with `uv run python scripts/get_akaiutil.py` (most tests drive the real program and skip without it), then run `uv run pytest tests/ -q`.
The GUI tests run offscreen. Tests that use real disk images look in `testing_images/` (git-ignored, or the folder in `$AKAIUTIL_TEST_IMAGES`) and skip if it isn't there.

## The user manual

`src/akaiutil_gui/ui/help/manual.md` is the in-app manual (Help > User Manual). Its pictures are in `help/screenshots/` and are made by `uv run python scripts/make_manual_screenshots.py` (offscreen, from a small demo image of invented sounds, at twice their on-screen size). Regenerate them when the interface changes. The README shows `main-window-dark.png` and `main-window-light.png` too, so they stay current with the rest. The whole `ui/help` folder is bundled into built apps (`--include-data-dir`), and the smoke test checks the manual and its pictures are present.
