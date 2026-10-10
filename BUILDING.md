# Building From Source

## Prerequisites

- Python 3.12 or higher, and [uv](https://docs.astral.sh/uv/)
- Other dependencies are in `pyproject.toml` and are installed automatically by the build scripts below
- A C compiler. The app bundles akaiutil, so the build downloads its source (checksum-verified) and compiles it.
  Nuitka needs one too.

#### macOS

- Xcode Command Line Tools (`xcode-select --install`)
- macOS 14 or newer (the Qt 6.12 wheels need it)

#### Linux

- `build-essential` and `patchelf` (can be installed with `sudo apt install build-essential patchelf` on Ubuntu-based distributions)

#### Windows

- A C++ compiler for Nuitka (it will offer to download MinGW if you don't have one). akaiutil itself needs no compiler on
  Windows: the build uses the official prebuilt `akaiutil.exe`.

## Setup and Build

1. Clone the repository
2. Run the relevant script for your platform (located in the `scripts` folder). Note that running a PowerShell script on Windows for the first time will trigger a warning but it can be mitigated by running `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` before running the build script. On Linux or macOS you _may_ need to run `chmod +x build_xxx.sh` on the relevant script for your platform
3. The final build lands in the `src` directory (`akaiutil-GUI.app` on macOS, `akaiutil-GUI.dist` on Linux and Windows)

Each script: syncs the environment, fetches akaiutil into `build/bin/`, writes the version from the nearest git tag into
`src/akaiutil_gui/_version.py`, generates `src/pysidedeploy.spec` (not tracked), and runs `pyside6-deploy`. The built app
contains the stylesheet template, icons, `THIRD_PARTY_NOTICES.md`, `LICENSE`, and akaiutil (in a `bin/` folder next to the
app's executable, where the app looks for it first).

To check a finished build without opening it, run its executable with `--smoke-test`. It verifies the bundled files, that the
audio libraries (miniaudio, soundfile/libsndfile, numpy) load and decode a WAV, and that the settings dialog and preview panel build,
and it talks to the bundled akaiutil, then exits 0 (or 1 with a report of what's missing). Set `AKAIUTIL_GUI_NO_AUDIO=1` when
running it on a machine with no sound card, as CI does. CI does this for every platform.

```
QT_QPA_PLATFORM=offscreen src/akaiutil-GUI.app/Contents/MacOS/main --smoke-test=report.txt
```

## Icons

The build uses two derived icons, both committed: `assets/icon/akaiutil-GUI-macos.icns` (macOS app) and
`assets/icon/icon_512x512.png` (Linux AppImage and Windows installer). `icon_source.png` is the plain full-bleed artwork.

To regenerate them from a new design, export a 1024x1024 PNG already in macOS icon form (a rounded square about 824px
wide, centred, soft shadow, transparent corners; IconKitchen produces this) and save it as
`assets/icon/AppIcon1024.png`. That file isn't kept in the repo (the same image is inside the `.icns`), so it's yours to supply.
Then:

- macOS icon: `scripts/make_icns.sh` (macOS only). It builds all ten sizes (16, 32, 128, 256, 512 points at 1x and 2x) by
  scaling the master *down*, never up.
- Linux and Windows icon: `sips -z 512 512 assets/icon/AppIcon1024.png --out assets/icon/icon_512x512.png`

A test fails if the `.icns` is missing any of its ten sizes or isn't in the rounded macOS form.

`scripts/make_icons.sh` is an older fallback that makes plain square icons straight from `icon_source.png`. It refuses to
overwrite the exported files unless you pass `--force`; you won't normally need it.

## Running Tests

There's a pytest suite in the `tests` directory (about 10 seconds). Sync the environment first with `uv sync`, fetch akaiutil with
`uv run python scripts/get_akaiutil.py` (most tests drive the real program and skip without it), then run `uv run pytest tests/ -q`.
Tests that use real disk images look in `testing_images/` (git-ignored) and skip if it isn't there.

## The user manual

`src/akaiutil_gui/ui/help/manual.md` is the in-app manual (Help > User Manual). Its pictures are in `help/screenshots/` and
are made by `uv run python scripts/make_manual_screenshots.py` (offscreen, from a small demo image of invented sounds, at
twice their on-screen size). Regenerate them when the interface changes. The whole `ui/help` folder is bundled into built
apps (`--include-data-dir`), and the smoke test checks the manual and its pictures are present.
