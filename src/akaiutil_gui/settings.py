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
