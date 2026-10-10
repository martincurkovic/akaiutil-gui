"""What an Akai name may be, and how it is typed into akaiutil."""

import pytest

from akaiutil_gui import akai_names
from akaiutil_gui.engine import parse


@pytest.mark.parametrize(
    "typed, stored",
    [
        ("drums", "DRUMS"),
        ("  Lead 2  ", "LEAD 2"),
        ("my_synth", "MY SYNTH"),  # akaiutil's keyboard convention: '_' is a space
        ("A#+-.0", "A#+-.0"),
        ("TWELVE CHARS", "TWELVE CHARS"),
    ],
)
def test_a_good_name_is_tidied_to_what_will_be_stored(typed, stored):
    assert akai_names.check_name(typed) == (stored, None)


def test_s900_keeps_its_lower_case_and_is_shorter():
    assert akai_names.check_name("kick", "S900") == ("kick", None)
    assert akai_names.check_name("TENCHARSOK", "S900")[1] is None
    name, problem = akai_names.check_name("ELEVENCHARS", "S900")
    assert problem == "At most 10 characters (11 typed)."


@pytest.mark.parametrize("typed", ["", "   ", "___"])
def test_nothing_typed_is_a_problem(typed):
    assert akai_names.check_name(typed)[1] == "Type a name."


def test_too_long_says_by_how_much():
    assert akai_names.check_name("THIRTEEN CHRS")[1] == "At most 12 characters (13 typed)."


def test_characters_akaiutil_would_silently_turn_into_dots_are_refused():
    name, problem = akai_names.check_name("A/B*C É")
    assert problem is not None and "Not allowed" in problem
    for bad in ("/", "*", "É"):
        assert bad in problem
    assert "A" not in problem.split("Not allowed:")[1].split("(")[0]  # only the offending characters are listed


def test_a_name_of_only_dots_is_refused():
    assert akai_names.check_name("..")[1] == "A name can't be only dots."
    assert akai_names.check_name("A.")[1] is None


@pytest.mark.parametrize(
    "file_name, parts",
    [
        ("KICK.S3", ("KICK", ".S3")),
        ("MY PROG.P3", ("MY PROG", ".P3")),
        ("OLD.S", ("OLD", ".S")),  # an S1000 file has a one-letter type
        ("SQUASHED.S9C", ("SQUASHED", ".S9C")),
        ("A.B.S3", ("A.B", ".S3")),  # dots inside the name are the name's own
        ("CD SETUP.CD", ("CD SETUP", ".CD")),
        ("NOEXT", ("NOEXT", "")),
        ("TOOLONGEXT.ABCD", ("TOOLONGEXT.ABCD", "")),
    ],
)
def test_the_type_extension_is_split_off_a_file_name(file_name, parts):
    assert akai_names.split_extension(file_name) == parts


def test_spaces_are_typed_as_underscores_on_akaiutils_command_line():
    assert akai_names.command_token("MY SYNTH 2") == "MY_SYNTH_2"


def test_names_are_compared_without_regard_to_case_or_edge_spaces():
    assert akai_names.same_name("Drums ", "DRUMS")
    assert not akai_names.same_name("DRUMS", "DRUM")


def test_the_volume_limit_is_read_from_the_listing():
    text = "vnr   vname\n-----\n  1   A\n-----\ntotal:   1 volume(s) (max. 100)\n"
    assert parse.parse_volume_limit(text) == 100
    assert parse.parse_volume_limit("no limit here") is None


def test_sanitising_what_is_typed_or_pasted():
    assert akai_names.sanitize("my_synth") == ("MY SYNTH", [], False)
    assert akai_names.sanitize("a/b*c/d") == ("ABCD", ["/", "*"], False)  # each dropped character listed once
    assert akai_names.sanitize("THIRTEEN CHARS") == ("THIRTEEN CHA", [], True)
    assert akai_names.sanitize("kick  ") == ("KICK  ", [], False)  # a space typed on the way to a longer name stays
    assert akai_names.sanitize("é") == ("", ["é"], False)
    assert akai_names.sanitize("Keep Case", "S900") == ("Keep Case", [], False)
    assert akai_names.sanitize("ELEVEN CHARS", "S900") == ("ELEVEN CHA", [], True)
