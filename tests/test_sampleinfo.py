"""Sample headers as numbers (frames, Hz, loops) for playback, from akaiutil's info text and from real images."""

import shutil
import wave
from pathlib import Path

import pytest

from akaiutil_gui import describe
from akaiutil_gui.engine import parse
from akaiutil_gui.engine.api import AkaiEngine
from akaiutil_gui.engine.models import Loop

from test_describe import EFFECTS, S900_SAMPLE, S3000_PROGRAM, S3000_SAMPLE  # real captured akaiutil text

TWO_LOOPS = S3000_SAMPLE.replace(
    "lnum:    1",
    "lnum:    2",
).replace(
    "loop 1:\n  loopat:  0x000000c0\n  length:  0x000000a8.0x8fdf\n  ltime:   HOLD",
    "loop 2:\n  loopat:  0x000000c0\n  length:  0x000000a8.0x8000\n  ltime:   250ms\n"
    "loop 5:\n  loopat:  0x000000f0\n  length:  0x00000010.0x0000\n  ltime:   HOLD",
)


def test_an_s3000_sample_as_numbers():
    info = describe.parse_sample_info(S3000_SAMPLE)
    assert (info.name, info.generation, info.frames, info.rate, info.root_key) == ("SINE", "S1000/S3000", 256, 44100, 60)
    assert (info.play_mode, info.start, info.end) == ("LOOP", 22, 255)  # 0x16 and 0xff: the play markers, not the file's block
    (loop,) = info.loops
    assert (loop.slot, loop.end, loop.time_ms) == (1, 192, None)  # HOLD
    assert loop.length == pytest.approx(168 + 0x8FDF / 65536)  # 32.16 fixed point
    assert loop.start == 23  # [LOOPAT - length, LOOPAT]: LOOPAT is the loop's END
    assert info.has_loop_to_preview and info.duration_s == pytest.approx(256 / 44100)


def test_loops_keep_their_slot_and_their_dwell_time():
    info = describe.parse_sample_info(TWO_LOOPS)
    assert [(l.slot, l.end, l.time_ms) for l in info.loops] == [(2, 192, 250), (5, 240, None)]
    assert info.loops[0].length == pytest.approx(168.5) and info.loops[1].start == 224


def test_the_loop_start_is_measured_back_from_the_end():
    assert Loop(slot=1, end=1000, length=250.0, time_ms=None).start == 750
    assert Loop(slot=1, end=1000, length=250.6, time_ms=None).start == 749  # rounded
    assert Loop(slot=1, end=10, length=500.0, time_ms=None).start == 0  # never before the sample


@pytest.mark.parametrize("mode, previews", [("LOOP", True), ("LOOPNOTREL", True), ("NOLOOP", False), ("TOEND", False)])
def test_only_looping_modes_are_previewed_as_loops(mode, previews):
    info = describe.parse_sample_info(S3000_SAMPLE.replace("pmode:   LOOP", f"pmode:   {mode}"))
    assert info.play_mode == mode and info.has_loop_to_preview is previews


def test_a_sample_without_loops_has_nothing_to_loop():
    plain = S3000_SAMPLE.split("loop 1:")[0].rstrip().replace("lnum:    1", "lnum:    0")
    info = describe.parse_sample_info(plain)
    assert info.loops == () and not info.has_loop_to_preview


def test_an_s900_sample():
    info = describe.parse_sample_info(S900_SAMPLE)
    assert (info.generation, info.frames, info.rate, info.root_key, info.play_mode) == ("S900", 22050, 44100, 60, "ONESHOT")
    assert info.loop_length == 0x5622 and not info.has_loop_to_preview  # S900 loops aren't previewed as loops


@pytest.mark.parametrize("text", [S3000_PROGRAM, EFFECTS, "must be inside a volume", ""])
def test_anything_that_is_not_a_sample_gives_none(text):
    assert describe.parse_sample_info(text) is None


def test_infoall_is_split_into_one_block_per_file():
    text = S3000_SAMPLE + "\n\n" + S3000_SAMPLE.replace("fnr:     2", "fnr:     7").replace("SINE.S3", "OTHER.S3")
    blocks = parse.split_infoall(text)
    assert sorted(blocks) == [2, 7] and blocks[7].startswith("/disk0/A/VOLUME 001/OTHER.S3")
    assert describe.parse_sample_info(blocks[7]).name == "SINE"


# ----------------------------------------------------- against the real akaiutil and generated images
@pytest.fixture
def engine_with_loop(binary, images, tmp_path):
    import imagegen

    image = tmp_path / "loop.img"
    shutil.copy(images["hd"], image)
    imagegen.add_loop(image, "KICK", loop_end=20000, loop_length=5000, time_ms=None, hold_tune=5)
    imagegen.add_loop(image, "SNARE", loop_end=15000, loop_length=1000, time_ms=300, slot=2)
    eng = AkaiEngine(binary)
    eng.open([image])
    yield eng
    eng.close()


def first_volume(eng):
    hd = eng.list_disks()[0]
    return eng.list_volumes(eng.list_partitions(hd)[0])[0]  # DRUMS: KICK, SNARE


def test_the_engine_reads_a_loop_it_was_given(engine_with_loop):
    eng = engine_with_loop
    vol = first_volume(eng)
    kick = eng.sample_info(vol, eng.list_files(vol)[0])
    assert (kick.frames, kick.rate, kick.play_mode, kick.hold_tune_cents) == (22050, 44100, "LOOP", 5)
    assert [(l.slot, l.start, l.end, l.time_ms) for l in kick.loops] == [(1, 15000, 20000, None)]


def test_infoall_gives_every_samples_info_in_one_command(engine_with_loop):
    eng = engine_with_loop
    infos = eng.volume_sample_infos(first_volume(eng))
    assert sorted(infos) == [1, 2] and infos[1].name == "KICK" and infos[2].name == "SNARE"
    assert [(l.slot, l.time_ms) for l in infos[2].loops] == [(3, 300)]  # slot 3: the loops keep their real slot number


def test_a_sample_converts_to_a_wav_that_can_be_replaced(engine_with_loop, tmp_path):
    eng = engine_with_loop
    vol = first_volume(eng)
    f = eng.list_files(vol)[0]
    wav = eng.export_sample_wav(vol, f, tmp_path)
    with wave.open(str(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 44100, 22050)
    wav.write_bytes(b"stale")
    assert eng.export_sample_wav(vol, f, tmp_path) == wav and wav.stat().st_size > 1000  # replaced, not skipped


def test_exporting_a_non_sample_as_a_wav_is_an_error(binary, images, tmp_path):
    from akaiutil_gui.engine.models import FileEntry
    from akaiutil_gui.engine.session import EngineError

    eng = AkaiEngine(binary)
    eng.open([images["hd"]])
    try:
        with pytest.raises(EngineError, match="not a sample"):
            eng.export_sample_wav(first_volume(eng), FileEntry(1, "X.P3", 10, "0x0001", "17.00"), tmp_path)
    finally:
        eng.close()
