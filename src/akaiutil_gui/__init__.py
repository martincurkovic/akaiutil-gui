"""akaiutil-gui: a Qt front end for akaiutil."""

try:
    # written by scripts/build_*.sh from the nearest git tag; not in the repo
    from ._version import APP_VERSION as __version__
except ImportError:  # running from a checkout that hasn't been built
    __version__ = "0.0.0-dev"
