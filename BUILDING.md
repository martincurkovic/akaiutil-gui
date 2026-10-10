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

To check a finished build without opening it, run its executable with `--smoke-test`. It verifies the bundled files and
talks to the bundled akaiutil, then exits 0 (or 1 with a report of what's missing). CI does this for every platform.

```
QT_QPA_PLATFORM=offscreen src/akaiutil-GUI.app/Contents/MacOS/main --smoke-test=report.txt
```

## Icons

`assets/icon/AppIcon1024.png` is the master: 1024x1024 and already in macOS icon form (a rounded square about 824px wide,
centred, soft shadow, transparent corners), exported from IconKitchen or a similar tool. Everything else is made from it:

- `assets/icon/akaiutil-GUI-macos.icns` (macOS app): run `scripts/make_icns.sh` (macOS only). It builds all ten sizes
  (16, 32, 128, 256, 512 points at 1x and 2x) by scaling the master *down*.
- `assets/icon/icon_512x512.png` (Linux AppImage and Windows installer):
  `sips -z 512 512 assets/icon/AppIcon1024.png --out assets/icon/icon_512x512.png`

`icon_source.png` is the plain full-bleed artwork the master was made from. Commit all of them. A test fails if the `.icns`
no longer matches the master or is missing a size.

`scripts/make_icons.sh` is an older fallback that makes plain square icons straight from `icon_source.png`. It refuses to
overwrite the exported files unless you pass `--force`; you won't normally need it.

## Running Tests

There's a pytest suite in the `tests` directory (about 10 seconds). Sync the environment first with `uv sync`, fetch akaiutil with
`uv run python scripts/get_akaiutil.py` (most tests drive the real program and skip without it), then run `uv run pytest tests/ -q`.
Tests that use real disk images look in `testing_images/` (git-ignored) and skip if it isn't there.
