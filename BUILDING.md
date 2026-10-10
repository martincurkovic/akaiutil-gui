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

`assets/icon/icon_source.png` is the master (square, at least 1024x1024). `scripts/make_icons.sh` (macOS only) turns it
into `icon_512x512.png` (Linux and Windows) and `akaiutil-GUI-macos.icns`. Commit all three.

## Running Tests

There's a pytest suite in the `tests` directory (about 10 seconds). Sync the environment first with `uv sync`, fetch akaiutil with
`uv run python scripts/get_akaiutil.py` (most tests drive the real program and skip without it), then run `uv run pytest tests/ -q`.
Tests that use real disk images look in `testing_images/` (git-ignored) and skip if it isn't there.
