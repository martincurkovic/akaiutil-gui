"""Entry point for built apps (pyside6-deploy compiles this file, as AKAISDS does with its src/main.py).

From a checkout, `uv run akaiutil-gui` or `python -m akaiutil_gui` do the same thing.
"""

from akaiutil_gui.main import main

if __name__ == "__main__":
    raise SystemExit(main())
