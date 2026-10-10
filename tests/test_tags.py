"""File tags: reading their names, and keeping them when a file is copied."""

import io
import tarfile

import pytest

from akaiutil_gui import tags, transfer
from akaiutil_gui.engine import parse
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import FileEntry

LSTAGS = """
tag  name
-----------------
 01  TAG A
 02  KICKS
 03  
 04  TAG D
-----------------

filter tags: none
"""


def test_tag_names_are_parsed_and_a_partition_without_tags_has_none():
    assert parse.parse_tag_names(LSTAGS) == {1: "TAG A", 2: "KICKS", 3: "", 4: "TAG D"}
    assert parse.parse_tag_names("\nno tags present\n\nfilter tags: none\n") == {}
    assert parse.parse_tag_names("\nno tags in this partition type\n") == {}


def test_a_file_calls_its_tags_by_name_or_by_number_when_the_name_is_blank():
    f = FileEntry(1, "KICK.S3", 10, "0x0001", "17.00", "02 03 ?", ("KICKS", ""))
    assert f.tag_numbers == [2, 3]
    assert f.tag_labels == ["KICKS", "Tag 3"]
    assert FileEntry(1, "KICK.S3", 10, "0x0001", "17.00", "05").tag_labels == ["Tag 5"]  # a partition with no names
    assert FileEntry(1, "KICK.S3", 10, "0x0001", "17.00").tag_labels == []


def test_tags_are_matched_by_name_between_partitions_not_by_number():
    here = {1: "DRUMS", 2: "BASS", 3: ""}
    assert tags.map_tags(["BASS", "DRUMS"], here) == ([2, 1], [])
    assert tags.map_tags(["bass "], here) == ([2], [])  # case and padding don't matter
    assert tags.map_tags(["DRUMS", "DRUMS"], here) == ([1], [])  # no repeats
    assert tags.map_tags(["PADS", ""], here) == ([], ["PADS", "(unnamed)"])  # what can't come along is reported


def test_the_os_version_column_becomes_akaiutils_number():
    assert tags.osver_value("17.00") == 0x1100
    assert tags.osver_value("3.01") == 0x0301
    assert tags.osver_value("17") == 17  # shown as a plain number when it isn't major.minor
    assert tags.osver_value("uncompr") is None and tags.osver_value("") is None


def test_the_tar_for_one_tagged_file_has_what_akaiutils_import_reads():
    data = bytes(range(256)) * 3
    raw = tags.tagged_file_tar("MY FILE.S3", data, 0x1100, [2, 26])
    assert len(raw) % tags.TAR_BLOCK == 0
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:  # the checksum is right, or this refuses
        (member,) = tar.getmembers()
        assert (member.name, member.size) == ("MY FILE.S3", len(data))
        assert tar.extractfile(member).read() == data
    header = raw[: tags.TAR_BLOCK]
    assert header[157:163] == b"\0T\x02\x1a\0\0"
    assert header[329:337] == b"0010400\0"  # 0x1100 in octal


# ---------------------------------------------------------------------- with the real akaiutil
@pytest.fixture
def ed(binary, editable_hd):
    eng = AkaiEngine(binary)
    eng.open([editable_hd])
    eng.set_writable(True)
    yield eng
    eng.close()


def vols(eng, letter="A"):
    part = next(p for p in eng.list_partitions(eng.list_disks()[0]) if p.name == letter)
    return {v.name: v for v in eng.list_volumes(part)}


def file(eng, vol, name):
    return next(f for f in eng.list_files(vol) if f.name == name)


def test_a_new_partition_has_tags_named_tag_a_to_tag_z(ed):
    v = vols(ed)
    names = ed.tag_names(v["DRUMS"])
    assert names[1] == "TAG A" and names[26] == "TAG Z" and len(names) == 26


def test_a_copy_with_tags_arrives_with_them_and_otherwise_identical(ed, tmp_path):
    v = vols(ed)
    kick = file(ed, v["DRUMS"], "KICK.S3")
    assert kick.tags == ""
    ed.copy_file(v["DRUMS"], kick, v["BASS"], "TAGGED.S3", tags=[2, 10])
    tagged = file(ed, v["BASS"], "TAGGED.S3")
    assert (tagged.tags, tagged.tag_names, tagged.tag_labels) == ("02 10", ("TAG B", "TAG J"), ["TAG B", "TAG J"])
    assert (tagged.size, tagged.os_version) == (kick.size, kick.os_version)
    for folder, vol, f in (("a", v["DRUMS"], kick), ("b", v["BASS"], tagged)):
        (tmp_path / folder).mkdir()
        ed.export_files(vol, [f], tmp_path / folder, confirm_overwrite=lambda p: True)
    one, two = (tmp_path / "a" / "KICK.S3").read_bytes(), (tmp_path / "b" / "TAGGED.S3").read_bytes()
    assert len(one) == len(two)
    assert [i for i in range(len(one)) if one[i] != two[i]] and all(3 <= i < 15 for i in range(len(one)) if one[i] != two[i])  # only the name inside the sample header


def test_copying_a_tagged_file_keeps_its_tags(ed):
    v = vols(ed)
    ed.copy_file(v["DRUMS"], file(ed, v["DRUMS"], "KICK.S3"), v["BASS"], "TAGGED.S3", tags=[3])
    result = transfer.transfer_files(ed, [(v["BASS"], file(ed, v["BASS"], "TAGGED.S3"))], v["MY SYNTH"], move=False)
    assert result.done == ["TAGGED.S3"] and result.failed == [] and result.notes == []
    assert file(ed, v["MY SYNTH"], "TAGGED.S3").tags == "03"
    assert file(ed, v["BASS"], "TAGGED.S3").tags == "03"  # and the original is as it was


def test_a_tagged_file_with_a_name_that_needs_a_space_and_a_number_still_copies(ed):
    v = vols(ed)
    ed.copy_file(v["DRUMS"], file(ed, v["DRUMS"], "KICK.S3"), v["BASS"], "BIG KICK.S3", tags=[1])
    again = transfer.transfer_files(ed, [(v["BASS"], file(ed, v["BASS"], "BIG KICK.S3"))], v["BASS"], move=False)
    assert again.done == ["BIG KICK 2.S3"]
    assert file(ed, v["BASS"], again.done[0]).tags == "01"


def test_a_tag_the_other_partition_calls_something_else_is_reported_not_guessed(ed, monkeypatch):
    v = vols(ed)
    ed.copy_file(v["DRUMS"], file(ed, v["DRUMS"], "KICK.S3"), v["BASS"], "TAGGED.S3", tags=[2, 4])
    monkeypatch.setattr(ed, "tag_names", lambda vol: {1: "TAG A", 2: "TAG B", 4: "OTHER"})  # (as if it were on another disk)
    result = transfer.transfer_files(ed, [(v["BASS"], file(ed, v["BASS"], "TAGGED.S3"))], v["MY SYNTH"], move=False)
    assert result.done == ["TAGGED.S3"] and result.failed == []
    assert result.notes == ["TAGGED.S3: no tag called TAG D in the destination"]
    assert file(ed, v["MY SYNTH"], "TAGGED.S3").tags == "02"


def test_an_untagged_file_copies_as_before(ed):
    v = vols(ed)
    result = transfer.transfer_files(ed, [(v["DRUMS"], file(ed, v["DRUMS"], "KICK.S3"))], v["BASS"], move=False)
    assert result.done == ["KICK.S3"] and result.notes == []
    assert file(ed, v["BASS"], "KICK.S3").tags == ""


def test_the_file_list_shows_tags_by_name(qt_app):
    from akaiutil_gui.ui import file_table

    table = file_table.FileTable()
    table.set_files(
        [
            FileEntry(1, "KICK.S3", 10, "0x0001", "17.00", "02 10", ("TAG B", "TAG J")),
            FileEntry(2, "SNARE.S3", 10, "0x0002", "17.00"),
        ]
    )
    col = file_table.COLUMNS.index("Tags")
    assert [table.item(r, col).text() for r in range(2)] == ["TAG B, TAG J", ""]
