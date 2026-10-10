import os
import sys
from pathlib import Path

# No test may play real sound or open a real audio device (a test that clicks Play would otherwise be audible).
# Tests that need a device use the fake_audio fixture, which lifts this.
os.environ.setdefault("AKAIUTIL_GUI_NO_AUDIO", "1")

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for imagegen

from akaiutil_gui.engine.locate import find_akaiutil  # noqa: E402


@pytest.fixture(scope="session")
def binary():
    path = find_akaiutil()
    if path is None:
        pytest.skip("akaiutil binary not found - run: uv run python scripts/get_akaiutil.py")
    return path


@pytest.fixture(scope="session")
def images(binary, tmp_path_factory):
    import imagegen

    return imagegen.make_images(tmp_path_factory.mktemp("images"), binary)


@pytest.fixture
def engine(binary, images):
    from akaiutil_gui.engine.api import AkaiEngine

    eng = AkaiEngine(binary)
    eng.open([images["hd"], images["fl1"], images["fl9"]])
    yield eng
    eng.close()


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for every test that needs Qt (offscreen, so no display is needed)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def backup_sandbox(tmp_path_factory, monkeypatch):
    """Backups made while a test edits an image go to a temp folder, never to the real app data folder."""
    monkeypatch.setenv("AKAIUTIL_GUI_BACKUP_DIR", str(tmp_path_factory.mktemp("backups")))


@pytest.fixture(autouse=True)
def log_sandbox(tmp_path_factory, monkeypatch):
    """Anything that looks for the log folder finds a temp one, never the real app data folder."""
    monkeypatch.setenv("AKAIUTIL_GUI_LOG_DIR", str(tmp_path_factory.mktemp("logs")))


@pytest.fixture(scope="session", autouse=True)
def settings_sandbox(tmp_path_factory):
    """Every test's QSettings (theme, export folder, update check times...) lives in a temp folder, never in the real user's."""
    from PySide6.QtCore import QCoreApplication, QSettings

    QCoreApplication.setOrganizationName("akaiutil-gui-test")
    QCoreApplication.setApplicationName("tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("qsettings")))


@pytest.fixture(autouse=True)
def fresh_settings(settings_sandbox):
    """...and each test starts from the defaults."""
    from PySide6.QtCore import QSettings

    QSettings().clear()
    yield
    QSettings().clear()


# --------------------------------------------------------------------------- audio without hardware
class FakeDevice:
    """Stands in for miniaudio.PlaybackDevice: remembers how it was opened and the generator it was given."""

    opened: list["FakeDevice"] = []

    def __init__(self, output_format, nchannels, sample_rate, buffersize_msec, device_id):
        self.nchannels, self.sample_rate, self.buffersize_msec, self.device_id = nchannels, sample_rate, buffersize_msec, device_id
        self.gen, self.stopped, self.closed = None, False, False
        FakeDevice.opened.append(self)

    def start(self, gen):
        self.gen = gen

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_audio(monkeypatch, qt_app):
    from akaiutil_gui.audio import preview as preview_module

    FakeDevice.opened = []
    monkeypatch.setattr(preview_module.miniaudio, "PlaybackDevice", FakeDevice)
    monkeypatch.delenv(preview_module.NO_AUDIO_ENV, raising=False)
    monkeypatch.setattr(preview_module.settings, "get_audio_output_device", lambda: None)
    monkeypatch.setattr(preview_module.settings, "get_audio_buffer_samples", lambda: 1024)
    return FakeDevice




# --------------------------------------------------------------------------- images that tests may change
@pytest.fixture(scope="session")
def small_hd_template(binary, tmp_path_factory):
    """A small S3000 hard disk (4 partitions of 5 MB) with three volumes in A; tests that edit take a copy of it."""
    import imagegen

    out = tmp_path_factory.mktemp("editable")
    wavs = out / "wavs"
    wavs.mkdir()
    for name, freq in [("KICK", 60), ("SNARE", 200), ("BASS", 55), ("LEAD", 440)]:
        imagegen.write_sine_wav(wavs / f"{name}.wav", freq)
    image = out / "template.img"
    image.write_bytes(bytes(8192 * 2500))  # 20 MB
    imagegen._script(binary, image, ["formatharddisk3 5M"], wavs)  # formatting restarts akaiutil: its own session
    imagegen._script(
        binary,
        image,
        [
            "cd /disk0/A",
            "mkvol3 DRUMS",
            "mkvol3 BASS",
            "mkvol3 MY_SYNTH",
            "cd DRUMS",
            "wav2sample3 KICK.wav",
            "wav2sample3 SNARE.wav",
            "cd ../BASS",
            "wav2sample3 BASS.wav",
            "cd ../MY_SYNTH",
            "wav2sample3 LEAD.wav",
        ],
        wavs,
    )
    return image


@pytest.fixture
def editable_hd(small_hd_template, tmp_path):
    """A private copy of the small hard disk that a test can rename, delete and create things in."""
    import shutil

    copy = tmp_path / "edit.img"
    shutil.copy(small_hd_template, copy)
    return copy
