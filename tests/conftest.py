import sys
from pathlib import Path

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
