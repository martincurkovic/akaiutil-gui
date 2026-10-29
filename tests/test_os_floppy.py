"""OS boot floppies: the bytes OSMaker's code writes (dev_docs/os-floppy.md), and akaiutil reading the result."""

import random

import pytest
from PySide6.QtWidgets import QFileDialog

from akaiutil_gui import hfe, os_floppy
from akaiutil_gui.engine.api import AkaiEngine
from test_gui_editing import answers, pump, window  # noqa: F401  (fixtures)


def os_image(build_name: bytes = b"s20V0200.COD") -> bytes:
    """A stand-in for an OS: 256 KB of noise with the build names an OS carries (a leftover V0000 one comes after the real one)."""
    data = bytearray(random.Random(7).randbytes(os_floppy.OS_BYTES))
    data[0x3C1CC : 0x3C1CC + len(build_name)] = build_name
    data[0x3F5DB : 0x3F5DB + 12] = b"s20V0000.COD"
    return bytes(data)


def test_the_name_says_which_sampler_and_version():
    info = os_floppy.identify(os_image(b"x" * 12), "S200200.COD")
    assert (info.model, info.tag, info.major, info.minor, info.os_version) == ("S2000", 0x0A, 2, 0, (0, 17))
    assert info.description == "S2000 OS 2.00"


@pytest.mark.parametrize(
    "name, model, tag, os_version",
    [
        ("S280130.COD", "S2800", 0x00, (30, 11)),
        ("S300100.cod", "S3000", 0x01, (0, 11)),
        ("S310200.COD", "S3000XL", 0x0B, (0, 17)),
        ("S320105.COD", "S3200", 0x02, (5, 11)),
        ("S350200.COD", "S3200XL", 0x0C, (0, 17)),
        ("C300100.COD", "CD3000", 0x04, (0, 11)),
        ("C310200.COD", "CD3000XL", 0x08, (0, 17)),
        ("S3102002.COD", "S3000XL", 0x0B, (0, 17)),  # (a beta number after the version)
    ],
)
def test_every_model_osmaker_knows(name, model, tag, os_version):
    info = os_floppy.identify(os_image(b"x" * 12), name)
    assert (info.model, info.tag, info.os_version) == (model, tag, os_version)


def test_a_c_file_the_sampler_saved_is_known_by_the_build_name_inside_it():
    info = os_floppy.identify(os_image(), "MULTI FILE.C")
    assert (info.model, info.major, info.minor) == ("S2000", 2, 0)


def test_a_leftover_v0000_build_name_is_not_taken_for_the_version():
    with pytest.raises(os_floppy.OsFloppyError, match="Can't tell"):
        os_floppy.identify(os_image(b"x" * 12), "MULTI FILE.C")


def test_a_file_of_the_wrong_size_or_unknown_kind_is_refused():
    with pytest.raises(os_floppy.OsFloppyError, match="256 KB"):
        os_floppy.identify(b"\0" * 1000, "S200200.COD")
    with pytest.raises(os_floppy.OsFloppyError, match="Can't tell"):
        os_floppy.identify(bytes(os_floppy.OS_BYTES), "S990200.COD")
    with pytest.raises(os_floppy.OsFloppyError, match="256 KB"):
        os_floppy.build(b"\0" * 1000, os_floppy.identify(os_image(), "S200200.COD"))


@pytest.fixture(scope="module")
def made():
    data = os_image()
    return data, os_floppy.build(data, os_floppy.identify(data, "S200200.COD"))


def test_the_floppy_is_laid_out_as_osmaker_writes_it(made):
    data, image = made
    assert len(image) == 1600 * 1024
    assert image[16] == 0xFF and not any(image[:16]) and not any(image[17:24])  # (a header of zeros, but for the S3000 marker)
    fat = [int.from_bytes(image[1536 + 2 * i : 1538 + 2 * i], "little") for i in range(1600)]
    assert fat[:17] == [0x4000] * 17
    assert fat[17:272] == list(range(18, 273)) and fat[272] == 0xC000 and not any(fat[273:])
    label = "19 1d 0a 0e 13 1d 15 0a 0a 0a 0a 0a"  # OS DISK, in Akai characters
    assert image[4736:4762] == bytes.fromhex(label + " 00 00 00 11 00 00 01 00 00 00 32 09 0c 00")
    entry = "19 1d 0a 10 13 16 0f 0a 0a 0a 0a 0a"  # OS FILE
    assert image[5120 : 5120 + 24] == bytes.fromhex(entry + " 00 00 00 0a 63 00 00 04 11 00 00 11")
    assert image[17 * 1024 : 17 * 1024 + len(data)] == data and not any(image[17 * 1024 + len(data) :])


def test_the_floppy_becomes_an_hfe_that_decodes_back_to_it(made):
    _, image = made
    assert hfe.decode(hfe.encode(image)).image == image


def test_akaiutil_sees_the_volume_and_the_os_file(binary, made, tmp_path):
    data, image = made
    path = tmp_path / "os.img"
    path.write_bytes(image)
    eng = AkaiEngine(binary)
    try:
        eng.open([str(path)])
        disk = eng.list_disks()[0]
        assert disk.type == "FLH"
        [part] = eng.list_partitions(disk)
        [vol] = eng.list_volumes(part)
        assert (vol.name, vol.os_version) == ("OS DISK", "17.00")
        [entry] = eng.list_files(vol)
        assert (entry.name, entry.size, entry.start_block, entry.os_version, entry.tag_numbers) == ("OS FILE.C", 262144, "0x0011", "17.00", [10])
    finally:
        eng.close()


def choose(monkeypatch, source, target):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))


@pytest.mark.parametrize("target_name", ["S2000 OS.img", "S2000 OS.hfe"])
def test_create_os_boot_floppy_writes_an_image_or_an_hfe(qt_app, window, made, tmp_path, monkeypatch, target_name):
    data, image = made
    source, target = tmp_path / "S200200.COD", tmp_path / target_name
    source.write_bytes(data)
    choose(monkeypatch, source, target)
    window.act_os_floppy.trigger()
    assert pump(qt_app, lambda: target.exists() and f"Saved {target_name}" in window.statusBar().currentMessage())
    assert (hfe.read(target).image if target.suffix == ".hfe" else target.read_bytes()) == image


def test_create_os_boot_floppy_says_when_the_file_is_not_an_os(qt_app, window, answers, tmp_path, monkeypatch):
    source, target = tmp_path / "NOTES.TXT", tmp_path / "out.img"
    source.write_bytes(b"hello")
    choose(monkeypatch, source, target)
    window.act_os_floppy.trigger()
    assert answers.warnings and answers.warnings[0][0] == "Not an OS file" and not target.exists()
