"""Persistent user settings (QSettings: plist / registry / ini per platform)."""

from PySide6.QtCore import QSettings

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_VALUES = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK)


def _qs() -> QSettings:
    # organization/application names are set on the QApplication in main.py
    return QSettings()


def get_theme() -> str:
    value = _qs().value("theme", THEME_SYSTEM)
    return value if value in THEME_VALUES else THEME_SYSTEM


def set_theme(value: str) -> None:
    _qs().setValue("theme", value)


def get_akaiutil_path() -> str | None:
    return _qs().value("akaiutil_path", None) or None


def set_akaiutil_path(path: str | None) -> None:
    _qs().setValue("akaiutil_path", path or "")


def get_last_dir(key: str) -> str | None:
    return _qs().value(f"last_dir/{key}", None) or None


def set_last_dir(key: str, path: str) -> None:
    _qs().setValue(f"last_dir/{key}", path)


# ------------------------------------------------------------------- audio preview
DEFAULT_AUDIO_BUFFER_SAMPLES = 1024  # frames, like a DAW's buffer-size dropdown


def get_audio_output_device() -> str | None:
    """Hex-encoded miniaudio device id (see audio.preview.device_id_string), or None for the system default."""
    return _qs().value("audio/output_device", None) or None


def set_audio_output_device(device_id: str | None) -> None:
    _qs().setValue("audio/output_device", device_id or "")


def get_audio_buffer_samples() -> int:
    try:
        return int(_qs().value("audio/buffer_samples", DEFAULT_AUDIO_BUFFER_SAMPLES))
    except (TypeError, ValueError):
        return DEFAULT_AUDIO_BUFFER_SAMPLES


def set_audio_buffer_samples(frames: int) -> None:
    _qs().setValue("audio/buffer_samples", int(frames))


# ----------------------------------------------------------- preview behaviour, export, updates
def _bool(key: str, default: bool) -> bool:
    value = _qs().value(key, default)
    if isinstance(value, str):  # ini-format stores booleans as text
        return value.lower() in ("1", "true", "yes")
    return bool(value)


def get_auto_play() -> bool:
    """Play a sample as soon as it's selected."""
    return _bool("audio/auto_play", False)


def set_auto_play(on: bool) -> None:
    _qs().setValue("audio/auto_play", bool(on))


def get_preview_loops() -> bool:
    """Play samples that have loops as a loop (otherwise they play once from start to end)."""
    return _bool("audio/preview_loops", True)


def set_preview_loops(on: bool) -> None:
    _qs().setValue("audio/preview_loops", bool(on))


def get_default_export_folder() -> str | None:
    return _qs().value("export/default_folder", None) or None


def set_default_export_folder(path: str | None) -> None:
    _qs().setValue("export/default_folder", path or "")


def get_ask_export_folder() -> bool:
    """Ask where to export every time (True), or go straight to the default folder (False)."""
    return _bool("export/ask_each_time", True)


def set_ask_export_folder(on: bool) -> None:
    _qs().setValue("export/ask_each_time", bool(on))


def get_check_updates_on_startup() -> bool:
    return _bool("updates/check_on_startup", True)


def set_check_updates_on_startup(on: bool) -> None:
    _qs().setValue("updates/check_on_startup", bool(on))


def get_last_update_check() -> float:
    try:
        return float(_qs().value("updates/last_check", 0))
    except (TypeError, ValueError):
        return 0.0


def set_last_update_check(timestamp: float) -> None:
    _qs().setValue("updates/last_check", float(timestamp))


def get_skipped_update_version() -> str | None:
    return _qs().value("updates/skipped_version", None) or None


def set_skipped_update_version(version: str | None) -> None:
    _qs().setValue("updates/skipped_version", version or "")


# ------------------------------------------------------------------------------ importing audio
IMPORT_FLAGS = {"file_markers": True, "root_from_name": False, "trim": False, "normalize": False}  # name -> default
IMPORT_STEREO_MIX = "import/stereo_mix"


def get_import_flags() -> dict[str, bool]:
    """The options last used in the import dialog."""
    return {name: _bool(f"import/{name}", default) for name, default in IMPORT_FLAGS.items()}


def set_import_flags(flags: dict[str, bool]) -> None:
    for name in IMPORT_FLAGS:
        if name in flags:
            _qs().setValue(f"import/{name}", bool(flags[name]))


def get_import_stereo_mix() -> bool:
    return _bool(IMPORT_STEREO_MIX, False)


def set_import_stereo_mix(mix: bool) -> None:
    _qs().setValue(IMPORT_STEREO_MIX, bool(mix))


def get_sort_after_adding() -> bool:
    """Put a volume in alphabetical order after files are imported or pasted into it."""
    return _bool("edit/sort_after_adding", False)


def set_sort_after_adding(on: bool) -> None:
    _qs().setValue("edit/sort_after_adding", bool(on))
