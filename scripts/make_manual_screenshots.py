#!/usr/bin/env python3
"""Regenerate the screenshots in the user manual (src/akaiutil_gui/ui/help/screenshots/).

    uv run python scripts/make_manual_screenshots.py            # every picture
    uv run python scripts/make_manual_screenshots.py --gallery  # only the README's: the main window in each theme, and the sample settings in System 6

Builds a small demo disk image of invented drum and bass sounds (none of anyone's real samples), runs the real window
offscreen at a scale factor of 2 (so the PNGs are twice their on-screen size, which is what the manual dialog expects:
ui/manual_dialog.py SCREENSHOT_PIXEL_RATIO), and saves each picture. Needs akaiutil (scripts/get_akaiutil.py).
"""

import os
import sys
import tempfile
import time
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
os.environ["AKAIUTIL_GUI_NO_AUDIO"] = "1"

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))
OUT = REPO / "src" / "akaiutil_gui" / "ui" / "help" / "screenshots"

import numpy as np  # noqa: E402
import soundfile  # noqa: E402
from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import imagegen  # noqa: E402
from akaiutil_gui import importing, settings  # noqa: E402
from akaiutil_gui.engine.locate import find_akaiutil  # noqa: E402
from akaiutil_gui.engine.models import Volume  # noqa: E402
from akaiutil_gui.ui import edit_dialogs, theme  # noqa: E402

SIZE = (1100, 700)


def pump(app, cond=lambda: False, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def sound(kind: str, rate=44100) -> np.ndarray:
    """A made-up sample, so the waveform on screen looks like something."""
    rng = np.random.default_rng(zlib.crc32(kind.encode()))  # (the same sounds every run, so pictures made at different times agree)
    spec = {
        "kick": (0.45, lambda t: np.sin(2 * np.pi * (140 * np.exp(-t * 18) + 45) * t) * np.exp(-t * 7)),
        "snare": (0.35, lambda t: (0.6 * rng.standard_normal(t.size) + np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 16)),
        "hat": (0.15, lambda t: rng.standard_normal(t.size) * np.exp(-t * 40)),
        "open": (0.5, lambda t: rng.standard_normal(t.size) * np.exp(-t * 9)),
        "clap": (0.3, lambda t: rng.standard_normal(t.size) * (np.exp(-t * 25) + 0.5 * np.exp(-((t - 0.02) ** 2) * 9000))),
        "bass": (0.7, lambda t: np.sin(2 * np.pi * 55 * t) * np.exp(-t * 3)),
        "pad": (0.9, lambda t: (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 330 * t)) * 0.4 * np.minimum(t * 8, 1) * np.exp(-t * 1.5)),
    }[kind]
    seconds, shape = spec
    t = np.arange(int(rate * seconds)) / rate
    wave = shape(t)
    return 0.8 * wave / np.abs(wave).max()


def build_demo(folder: Path, binary: Path) -> Path:
    wavs = folder / "wavs"
    wavs.mkdir()
    names = {
        "KICK": "kick", "SNARE": "snare", "HAT CLOSED": "hat", "HAT OPEN": "open", "CLAP": "clap",
        "BASS LOW": "bass", "PAD": "pad",
    }
    for name, kind in names.items():
        soundfile.write(str(wavs / f"{name.replace(' ', '_')}.wav"), sound(kind), 44100, subtype="PCM_16")
    image = folder / "demo.img"
    image.write_bytes(bytes(8192 * 3200))
    imagegen._script(binary, image, ["formatharddisk3 8M"], wavs)
    commands = ["cd /disk0/A", "mkvol3 DRUMS", "mkvol3 BASS", "mkvol3 KEYS"]
    for name in ("KICK", "SNARE", "HAT_CLOSED", "HAT_OPEN", "CLAP"):
        commands += ["cd /disk0/A/DRUMS", f"wav2sample3 {name}.wav"]
    commands += ["cd /disk0/A/BASS", "wav2sample3 BASS_LOW.wav", "cd /disk0/A/KEYS", "wav2sample3 PAD.wav"]
    imagegen._script(binary, image, commands, wavs)
    return image


def save(widget, name: str) -> None:
    widget.grab().save(str(OUT / name))
    print("saved", name)


def sample_settings_picture(app, window, work: Path, volume, name: str) -> None:
    """The Sample Settings dialog for SNARE (with a loop switched on), saved as `name`."""
    from akaiutil_gui.audio.sample import load_wav

    engine = window._engine
    snare = next(f for f in engine.list_files(volume) if f.name == "SNARE.S3")
    current = engine.read_sample_settings(volume, snare)
    wav = engine.export_sample_wav(volume, snare, work)
    audio = load_wav(wav, snare.name, None).samples
    dialog = edit_dialogs.SampleSettingsDialog(window, snare.name, current, audio)
    on, start_box, end_box, _time, hold = dialog.loop_rows[0]
    on.setChecked(True)
    start_box.setValue(int(current.frames * 0.25))
    end_box.setValue(int(current.frames * 0.6))
    dialog._select_loop(0)
    dialog.show()
    pump(app, timeout=0.4)
    save(dialog, name)
    dialog.close()


def extra_pictures(app, window, work: Path, volume) -> None:
    """The start screen with some recent images, the Sample Settings dialog (with a loop switched on) and the backup question."""
    from akaiutil_gui.audio.sample import load_wav
    from akaiutil_gui.ui.main_window import MainWindow

    home = (work / "home").resolve()
    disks = home / "Music" / "Akai disks"
    disks.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)  # (so the list shows "~/Music/Akai disks", not a temporary folder)
    settings.clear_recent_images()  # (the demo image the other pictures use isn't one anyone would have)
    for name in ("HD0.img", "Gotek floppy.img", "SD card backup.img", "HD5.img"):
        (disks / name).write_bytes(b"\0" * 16)
        settings.add_recent_image(str(disks / name))
    start = MainWindow()
    start.resize(*SIZE)
    start.show()
    pump(app, timeout=0.5)
    save(start, "start-screen.png")
    start.close()

    sample_settings_picture(app, window, work, volume, "sample-settings.png")

    question = edit_dialogs.ThreeWayDialog(
        window, "Allow editing?", "Editing changes the disk image files themselves.",
        "There is no undo. Back up this image first (512 MB)? The backups are kept in the app's folder, and File > Revert to Backup… puts one back.",
        "Back Up and Edit", "Edit Without Backup", ["HD5.img  ·  512.0 MB"],
    )
    question.show()
    pump(app, timeout=0.3)
    save(question, "backup-question.png")
    question.close()


def main(argv: list[str]) -> int:
    gallery_only = "--gallery" in argv
    binary = find_akaiutil()
    if binary is None:
        print("akaiutil isn't built: run scripts/get_akaiutil.py first")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="akaiutil-gui-shots-"))
    QCoreApplication.setOrganizationName("akaiutil-gui-shots")
    QCoreApplication.setApplicationName("shots")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(work / "settings"))
    app = QApplication([])
    image = build_demo(work, Path(binary))

    from akaiutil_gui.ui.main_window import MainWindow

    for scheme in ("dark", "light", "system6"):
        theme.apply_to_app(app, scheme)
        window = MainWindow()
        window.resize(*SIZE)
        window.show()
        window.open_images([str(image)])
        pump(app, lambda: window.tree.topLevelItemCount() == 1)
        window.tree.reveal_volume(Volume(0, "A", 1, "", "-", "", "", ""))
        pump(app, lambda: window._volume is not None and window.table.rowCount() == 5 and window._pending == 0)
        window.table.selectRow(1)
        pump(app, lambda: window._pending == 0 and window.preview.isVisible())
        pump(app, timeout=1.0)
        window.preview.play_button.setEnabled(True)  # (audio is off for this script, which greys Play; the picture shows it as it normally is)
        save(window, f"main-window-{scheme}.png")
        if scheme == "system6":
            sample_settings_picture(app, window, work, next(v for v in window.tree.volumes_in(0, "A") if v.name == "DRUMS"), "sample-settings-system6.png")
        if scheme == "dark" and not gallery_only:
            window.search.setText("hat")
            pump(app, lambda: window._in_results and window.table.rowCount() == 2)
            save(window, "search.png")
            window.search.clear()
            pump(app, lambda: not window._in_results)
            volume = next(v for v in window.tree.volumes_in(0, "A") if v.name == "DRUMS")
            menu = window._tree_menu(volume)
            menu.ensurePolished()
            save(menu, "context-menu.png")
            sources = [importing.inspect(p) for p in sorted((work / "wavs").glob("*.wav"))[:3]]
            import_dialog = edit_dialogs.ImportDialog(window, volume, sources)
            import_dialog.show()
            pump(app, timeout=0.3)
            save(import_dialog, "import-dialog.png")
            import_dialog.close()
            sort_dialog = edit_dialogs.SortDialog(window, volume, window._volume_files + [type(window._volume_files[0])(9, "PROG.P3", 1, "0x1", "17")])
            sort_dialog.show()
            pump(app, timeout=0.3)
            save(sort_dialog, "sort-dialog.png")
            sort_dialog.close()
            from akaiutil_gui.ui.settings_dialog import SettingsDialog

            settings_dialog = SettingsDialog(window, update_runner=window._update_runner, locate=window.locate_binary)
            settings_dialog.resize(560, 760)
            settings_dialog.show()
            pump(app, timeout=0.3)
            save(settings_dialog, "settings.png")
            settings_dialog.close()
            extra_pictures(app, window, work, volume)
        window.close()
    print("done:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
