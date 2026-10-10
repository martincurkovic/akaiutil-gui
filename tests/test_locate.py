"""Finding akaiutil: in a source checkout, bundled inside a built app, or downloaded."""

import importlib
import os
import stat
import sys
from pathlib import Path

import pytest

import akaiutil_gui
from akaiutil_gui import paths
from akaiutil_gui.engine import locate


def fake_binary(folder: Path, executable: bool = True) -> Path:
    path = folder / locate.EXE_NAME
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho usage\n")
    path.chmod(0o755 if executable else 0o644)
    return path


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pretend to be a built app unpacked into tmp_path/app."""
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "app_dir", lambda: app)
    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "userdata")
    monkeypatch.delenv("AKAIUTIL_BIN", raising=False)
    return app


def test_from_source_nothing_is_bundled():
    assert not paths.is_frozen() and paths.app_dir() is None and locate.bundled_binary() is None


def test_a_built_app_uses_the_copy_inside_it(frozen):
    bundled = fake_binary(frozen / "bin")
    assert locate.bundled_binary() == bundled
    assert locate.find_akaiutil() == bundled


def test_the_order_is_settings_then_env_then_bundled_then_downloaded(frozen, monkeypatch, tmp_path):
    bundled = fake_binary(frozen / "bin")
    downloaded = fake_binary(locate.default_install_dir())
    chosen = fake_binary(tmp_path / "chosen")
    from_env = fake_binary(tmp_path / "env")

    assert locate.find_akaiutil() == bundled  # nothing configured: the bundled one beats a downloaded one
    monkeypatch.setenv("AKAIUTIL_BIN", str(from_env))
    assert locate.find_akaiutil() == from_env
    assert locate.find_akaiutil(str(chosen)) == chosen  # what the user picked in the app wins over everything
    monkeypatch.delenv("AKAIUTIL_BIN")
    bundled.unlink()
    assert locate.find_akaiutil() == downloaded  # no bundled copy any more: fall back to the downloaded one


@pytest.mark.skipif(sys.platform == "win32", reason="no executable bit on Windows")
def test_a_bundled_binary_that_lost_its_executable_bit_is_repaired(frozen):
    path = fake_binary(frozen / "bin", executable=False)
    assert not os.access(path, os.X_OK)
    assert locate.find_akaiutil() == path  # build steps that copy files don't always keep the bit
    assert path.stat().st_mode & stat.S_IXUSR


def test_a_built_app_downloads_into_the_users_folder_never_into_itself(frozen, tmp_path):
    assert locate.default_install_dir() == tmp_path / "userdata" / "bin"


def test_a_checkout_downloads_into_build_bin():
    assert locate.default_install_dir() == Path(locate.__file__).resolve().parents[3] / "build" / "bin"


@pytest.mark.parametrize(
    "platform, env, expected",
    [
        ("darwin", {}, "Library/Application Support/akaiutil-gui"),
        ("linux", {"XDG_DATA_HOME": "/xdg"}, "/xdg/akaiutil-gui"),
        ("linux", {}, ".local/share/akaiutil-gui"),
        ("win32", {"APPDATA": "/appdata"}, "/appdata/akaiutil-gui"),
    ],
)
def test_user_data_dir_per_platform(monkeypatch, platform, env, expected):
    monkeypatch.setattr(sys, "platform", platform)
    for var in ("XDG_DATA_HOME", "APPDATA"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert paths.user_data_dir().as_posix().endswith(expected)


def test_version_comes_from_the_build_and_falls_back_for_a_checkout(monkeypatch):
    import types

    try:
        monkeypatch.setitem(sys.modules, "akaiutil_gui._version", None)  # as if the file didn't exist
        assert importlib.reload(akaiutil_gui).__version__ == "0.0.0-dev"
        fake = types.ModuleType("akaiutil_gui._version")
        fake.APP_VERSION = "9.8.7"
        monkeypatch.setitem(sys.modules, "akaiutil_gui._version", fake)
        assert importlib.reload(akaiutil_gui).__version__ == "9.8.7"
    finally:
        monkeypatch.undo()
        importlib.reload(akaiutil_gui)


def test_the_app_folder_is_the_executables_folder_not_nuitkas_containing_dir(monkeypatch, tmp_path):
    """Regression: in a macOS .app, Nuitka's __compiled__.containing_dir is the folder holding the
    bundle, so the bundled akaiutil (in Contents/MacOS/bin) was never found."""
    import types

    macos = tmp_path / "Apps" / "akaiutil-GUI.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    fake_compiled = types.SimpleNamespace(containing_dir=str(tmp_path / "Apps"))  # what Nuitka says
    monkeypatch.setattr(paths, "_compiled", lambda: fake_compiled)
    monkeypatch.setattr(sys, "executable", str(macos / "main"))

    assert paths.is_frozen()
    assert paths.app_dir() == macos.resolve()
    assert locate.bundled_binary() == macos.resolve() / "bin" / locate.EXE_NAME


def test_from_source_is_not_frozen():
    assert paths._compiled() is None and not paths.is_frozen() and paths.app_dir() is None
