"""engine/fetch.py against a fake SourceForge: no network needed."""

import hashlib
import io
import subprocess
import tarfile
import tempfile
import urllib.error
from pathlib import Path

import pytest

from akaiutil_gui.engine import fetch

FAKE_C = b'#include <stdio.h>\nint main(void){ puts("fake akaiutil"); return 0; }\n'


def make_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def serve(monkeypatch):
    """Make the 'download' return these bytes, and pin their checksum as the expected one."""

    def go(payload: bytes, pin: bool = True) -> None:
        monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda req, timeout=None: io.BytesIO(payload))
        if pin:
            monkeypatch.setattr(fetch, "SOURCE_SHA256", hashlib.sha256(payload).hexdigest())

    return go


def leftovers() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("akaiutil-fetch-*")}


needs_compiler = pytest.mark.skipif(fetch._find_compiler() is None, reason="no C compiler")


def test_the_plan_says_where_it_downloads_from():
    plan = fetch.describe_plan(build_from_source=True)
    assert "SourceForge" in plan and fetch.VERSION in plan and "compiler" in plan


@needs_compiler
def test_downloads_verifies_compiles_and_installs(serve, tmp_path):
    serve(make_tar({f"akaiutil-{fetch.VERSION}/main.c": FAKE_C}))
    before, steps = leftovers(), []
    out = fetch.install(tmp_path / "bin", build_from_source=True, progress=steps.append)
    assert out == tmp_path / "bin" / fetch.EXE_NAME and out.is_file()
    assert subprocess.run([str(out)], capture_output=True, text=True).stdout.strip() == "fake akaiutil"
    assert steps[0].startswith("Downloading") and "Compiling akaiutil…" in steps and steps[-1] == "Done."
    assert leftovers() == before, "the scratch directory must be cleaned up"


def test_a_wrong_checksum_is_refused_and_nothing_is_built(serve, tmp_path):
    serve(make_tar({f"akaiutil-{fetch.VERSION}/main.c": FAKE_C}), pin=False)  # SOURCE_SHA256 stays the real one
    before = leftovers()
    with pytest.raises(fetch.FetchError, match="checksum"):
        fetch.install(tmp_path / "bin", build_from_source=True)
    assert not (tmp_path / "bin").exists() and leftovers() == before


def test_no_network_is_a_readable_error(monkeypatch, tmp_path):
    def offline(req, timeout=None):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", offline)
    with pytest.raises(fetch.FetchError, match=r"(?s)Couldn't download.*SourceForge:\nName or service not known$"):
        fetch.install(tmp_path / "bin", build_from_source=True)


def test_a_missing_compiler_says_how_to_get_one(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "_find_compiler", lambda: None)
    with pytest.raises(fetch.FetchError, match="xcode-select|build-essential"):
        fetch.install(tmp_path / "bin", build_from_source=True)  # fails before any download is attempted


@needs_compiler
def test_a_compile_error_shows_the_compilers_message(serve, tmp_path):
    serve(make_tar({f"akaiutil-{fetch.VERSION}/main.c": b"this is not C"}))
    with pytest.raises(fetch.FetchError, match="Compiling akaiutil failed"):
        fetch.install(tmp_path / "bin", build_from_source=True)
    assert not (tmp_path / "bin" / fetch.EXE_NAME).exists()


@needs_compiler
def test_an_archive_that_escapes_its_folder_is_refused(serve, tmp_path):
    serve(make_tar({"../../evil.c": FAKE_C}))  # right checksum, hostile paths
    with pytest.raises(fetch.FetchError, match="unpack"):
        fetch.install(tmp_path / "bin", build_from_source=True)
    assert not (tmp_path.parent / "evil.c").exists()


@needs_compiler
def test_an_archive_without_the_sources_is_refused(serve, tmp_path):
    serve(make_tar({f"akaiutil-{fetch.VERSION}/README.txt": b"nothing to build"}))
    with pytest.raises(fetch.FetchError, match="expected files"):
        fetch.install(tmp_path / "bin", build_from_source=True)
