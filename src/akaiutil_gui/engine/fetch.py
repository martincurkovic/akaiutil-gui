"""Download, verify and (where needed) compile the pinned akaiutil release.

akaiutil is a C program hosted on SourceForge, not a Python package, so uv can't
install it. This does the equivalent: fetch the pinned release over HTTPS, check its
SHA-256 against the value pinned below (a mismatch is refused, nothing is built), and
put a runnable binary in `default_install_dir()`.

macOS / Linux : download the source tarball and compile it with cc/gcc/clang
Windows       : use the official prebuilt akaiutil.exe (no compiler needed),
                or build_from_source=True to compile with MinGW gcc instead

Standard library only. Used by the app's first-launch setup and by scripts/get_akaiutil.py.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from .locate import EXE_NAME, default_install_dir

VERSION = "4.6.8"
BASE_URL = "https://sourceforge.net/projects/akaiutil/files/"
SOURCE_ARCHIVE = f"akaiutil-{VERSION}.tar.gz"
SOURCE_SHA256 = "48ea733cc38d90c2f9f70a6ff9c4fb12a8e64ddd7024d4b436e9ebeef2bf57b1"
WIN_ARCHIVE = f"akaiutil-{VERSION}win.zip"
WIN_SHA256 = "c6c310ac4ec42c1b8601f38f53f44628f349d12e14225807a0305aff40a4b9de"

Progress = Callable[[str], None]


class FetchError(Exception):
    """Something went wrong; the message is meant to be shown to the user."""


def describe_plan(build_from_source: bool | None = None) -> str:
    """One sentence on what install() is about to do, for a confirmation prompt."""
    if sys.platform == "win32" and not build_from_source:
        return f"download the official akaiutil {VERSION} program for Windows from SourceForge"
    return f"download the akaiutil {VERSION} source code from SourceForge and compile it (this needs a C compiler)"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(name: str, expected_sha256: str, into: Path, report: Progress) -> Path:
    url = f"{BASE_URL}{name}/download"
    report(f"Downloading {name}…")
    dest = into / name
    # SourceForge redirects /download to a mirror; a plain UA is enough
    req = urllib.request.Request(url, headers={"User-Agent": "akaiutil-gui-setup"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)  # URLError wraps the real cause ("Name or service not known", ...)
        raise FetchError(f"Couldn't download {name} from SourceForge:\n{reason}") from e
    report("Checking the download…")
    actual = _sha256(dest)
    if actual != expected_sha256:
        raise FetchError(
            f"The downloaded {name} doesn't match its expected checksum, so it was not used.\n"
            f"expected {expected_sha256}\ngot      {actual}"
        )
    return dest


def _find_compiler() -> str | None:
    names = ["gcc", "cc", "clang"] if sys.platform == "win32" else ["cc", "gcc", "clang"]
    if os.environ.get("CC"):
        names.insert(0, os.environ["CC"])
    return next((n for n in names if shutil.which(n)), None)


def _build_from_source(work: Path, report: Progress) -> Path:
    cc = _find_compiler()
    if cc is None:
        raise FetchError(
            "No C compiler was found (looked for cc, gcc and clang; $CC is honoured).\n\n"
            "  macOS:    xcode-select --install\n"
            "  Linux:    sudo apt install build-essential\n"
            "  Windows:  use the prebuilt program instead (the default)"
        )
    archive = _download(SOURCE_ARCHIVE, SOURCE_SHA256, work, report)
    src = work / "src"
    src.mkdir()
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(src, filter="data")  # the "data" filter refuses absolute paths, links out of the tree, etc.
    except (tarfile.TarError, OSError) as e:
        raise FetchError(f"Couldn't unpack the downloaded source archive: {e}") from e
    srcdir = src / f"akaiutil-{VERSION}"
    sources = sorted(str(p) for p in srcdir.glob("*.c"))
    if not sources:
        raise FetchError("The downloaded source archive didn't contain the expected files.")
    out = work / EXE_NAME
    report("Compiling akaiutil…")
    # same flags as upstream's Makefile, minus the warning noise
    cmd = [cc, "-O2", "-w", "-D_FILE_OFFSET_BITS=64", "-o", str(out), *sources, "-lm"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as e:
        raise FetchError(f"Compiling akaiutil failed:\n{(e.stderr or e.stdout or '').strip()[-1500:]}") from e
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FetchError(f"Couldn't run the compiler ({cc}): {e}") from e
    return out


def _fetch_windows_prebuilt(work: Path, report: Progress) -> Path:
    archive = _download(WIN_ARCHIVE, WIN_SHA256, work, report)
    out = work / EXE_NAME
    try:
        with zipfile.ZipFile(archive) as zf, zf.open("akaiutil.exe") as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except (zipfile.BadZipFile, KeyError) as e:
        raise FetchError(f"The downloaded Windows archive wasn't what was expected: {e}") from e
    return out


def download_source(dest_dir: str | os.PathLike, progress: Progress | None = None) -> Path:
    """Download the pinned source tarball into dest_dir (checked against its SHA-256).

    Releases attach this next to the built apps, because the apps contain a compiled copy of
    akaiutil, which is GPL-2 and so comes with an obligation to offer its source.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    return _download(SOURCE_ARCHIVE, SOURCE_SHA256, dest, progress or (lambda _msg: None))


def install(
    dest_dir: str | os.PathLike | None = None,
    build_from_source: bool | None = None,
    progress: Progress | None = None,
) -> Path:
    """Fetch akaiutil and return the path of the installed binary.

    Blocking (a few seconds, mostly the compile); call it from a worker thread in a GUI.
    """
    report = progress or (lambda _msg: None)
    dest = Path(dest_dir) if dest_dir is not None else default_install_dir()
    use_prebuilt = sys.platform == "win32" and not build_from_source
    with tempfile.TemporaryDirectory(prefix="akaiutil-fetch-") as tmp:
        work = Path(tmp)
        built = _fetch_windows_prebuilt(work, report) if use_prebuilt else _build_from_source(work, report)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            final = dest / EXE_NAME
            shutil.move(str(built), final)
            if sys.platform != "win32":
                final.chmod(0o755)
        except OSError as e:
            raise FetchError(f"Couldn't install akaiutil into {dest}:\n{e}") from e
    report("Done.")
    return final
