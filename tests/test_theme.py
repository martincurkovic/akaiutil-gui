"""Both palettes must satisfy every ${placeholder} in the stylesheet template."""

import pytest

from akaiutil_gui.ui import theme


@pytest.mark.parametrize("palette", [theme.DARK_PALETTE, theme.LIGHT_PALETTE], ids=["dark", "light"])
def test_stylesheet_renders(palette):
    qss = theme.render_stylesheet(palette)
    assert "${" not in qss and palette["accent"] in qss


def test_palettes_have_same_keys():
    assert theme.DARK_PALETTE.keys() == theme.LIGHT_PALETTE.keys()
