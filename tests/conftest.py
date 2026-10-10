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


