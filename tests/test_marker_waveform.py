"""The draggable markers on the Sample Settings waveform: AKAISDS's mechanism, with real mouse events."""

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from akaiutil_gui.ui import marker_waveform as mw
from akaiutil_gui.ui.marker_waveform import MarkerWaveform, loop_in, loop_out, push_marker

FRAMES = 10001  # (a round number: at full zoom with a 1001-pixel widget, 10 frames to the pixel)


@pytest.fixture
def wave(qt_app):
    w = MarkerWaveform()
    w.resize(1001, 150)
    w.set_audio(np.zeros(FRAMES, dtype=np.int16))
    w.set_markers({mw.START: 1000, mw.END: 9000, loop_in(0): 3000, loop_out(0): 6000})
    w.show()
    yield w
    w.close()
    w.deleteLater()


def at(w, frame):
    """The widget x of a frame, at the current view."""
    return round(mw.x_for_frame(frame, w.width(), *w.view_state()))


def drag(w, name, to_frame, modifiers=Qt.KeyboardModifier.NoModifier, release=True):
    x0 = at(w, w.markers[name])
    QTest.mousePress(w, Qt.MouseButton.LeftButton, modifiers, QPoint(x0, 60))
    QTest.mouseMove(w, QPoint(at(w, to_frame), 60))
    if release:
        QTest.mouseRelease(w, Qt.MouseButton.LeftButton, modifiers, QPoint(at(w, to_frame), 60))


# ------------------------------------------------------------------------------- the maths
def test_a_marker_pushes_its_partner_instead_of_stopping_at_it():
    order = ("a", "b", "c")
    values = {"a": 10, "b": 20, "c": 30}
    assert push_marker(order, "a", 25, values, 100) == {"a": 25, "b": 26, "c": 30}  # b is pushed past; c is already clear
    assert push_marker(order, "a", 40, values, 100) == {"a": 40, "b": 41, "c": 42}  # the push cascades
    assert push_marker(order, "c", 5, values, 100) == {"a": 3, "b": 4, "c": 5}  # and the other way
    assert values == {"a": 10, "b": 20, "c": 30}  # (a new dict: the old one is untouched)


def test_a_marker_keeps_room_for_the_ones_it_pushes_inside_the_sample():
    order = (mw.START, mw.END)
    assert push_marker(order, mw.START, 500, {mw.START: 10, mw.END: 20}, 100) == {mw.START: 99, mw.END: 100}
    assert push_marker(order, mw.END, -50, {mw.START: 10, mw.END: 20}, 100) == {mw.START: 0, mw.END: 1}


def test_x_and_frame_are_inverse_within_the_visible_window():
    for start, length in ((0, 10001), (2000, 500)):
        for frame in (start, start + length // 3, start + length - 1):
            x = mw.x_for_frame(frame, 1001, start, length)
            assert abs(mw.frame_for_x(x, 1001, start, length) - frame) <= 1


# ------------------------------------------------------------------------------ dragging
def test_dragging_a_marker_moves_it_and_reports_while_and_after(wave):
    live, committed = [], []
    wave.markersChanged.connect(live.append)
    wave.markerCommitted.connect(lambda name, markers: committed.append((name, markers)))
    drag(wave, loop_out(0), 7000)
    assert abs(wave.markers[loop_out(0)] - 7000) <= 10
    assert live and live[-1][loop_out(0)] == wave.markers[loop_out(0)]
    assert len(committed) == 1 and committed[0][0] == loop_out(0)  # (once, on release)
    assert committed[0][1][loop_in(0)] == 3000  # the other markers are as they were


def test_a_marker_dragged_past_its_partner_pushes_it_and_other_loops_are_left_alone(wave):
    wave.set_markers({**wave.markers, loop_in(1): 500, loop_out(1): 800})
    drag(wave, loop_out(0), 1000)  # (to the left of loop 1's start at 3000)
    m = wave.markers
    assert m[loop_in(0)] < m[loop_out(0)]  # the pair stays in order
    assert abs(m[loop_out(0)] - 1000) <= 10
    assert (m[loop_in(1)], m[loop_out(1)]) == (500, 800) and (m[mw.START], m[mw.END]) == (1000, 9000)


def test_the_sample_start_and_end_push_each_other_but_not_the_loops(wave):
    drag(wave, mw.START, 9500)
    m = wave.markers
    assert m[mw.START] < m[mw.END] <= FRAMES - 1
    assert (m[loop_in(0)], m[loop_out(0)]) == (3000, 6000)


def test_a_drag_stops_at_the_ends_of_the_sample(wave):
    drag(wave, mw.END, FRAMES + 5000)
    assert wave.markers[mw.END] == FRAMES - 1
    drag(wave, mw.START, -5000)
    assert wave.markers[mw.START] == 0


def move_with(w, x, modifiers):
    """A mouse move with the left button down and these modifiers (QTest.mouseMove can't carry modifiers)."""
    pos = QPointF(x, 60)
    QApplication.sendEvent(w, QMouseEvent(QEvent.Type.MouseMove, pos, w.mapToGlobal(pos), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers))


def test_shift_makes_a_drag_fine(wave, monkeypatch):
    monkeypatch.setattr(mw, "MACOS", True)  # (the plain larger-divisor path: no cursor warping in a test)
    x0 = at(wave, 3000)
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0, 60))
    move_with(wave, x0 + 120, Qt.KeyboardModifier.NoModifier)  # 120 px = 1200 frames, normally
    normal = wave.markers[loop_in(0)] - 3000
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0 + 120, 60))
    wave.set_markers({**wave.markers, loop_in(0): 3000})
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0, 60))
    move_with(wave, x0 + 120, Qt.KeyboardModifier.ShiftModifier)
    fine = wave.markers[loop_in(0)] - 3000
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0 + 120, 60))
    assert normal >= 1100 and 0 < fine <= normal / 10  # (a twelfth)


def test_a_click_away_from_every_marker_grabs_nothing(wave):
    before = wave.markers
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 4500), 60))
    QTest.mouseMove(wave, QPoint(at(wave, 5500), 60))
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 5500), 60))
    assert wave.markers == before


def test_markers_on_top_of_each_other_are_taken_in_turn_by_repeated_clicks(wave):
    wave.set_markers({mw.START: 0, mw.END: 9000, loop_in(0): 3000, loop_out(0): 3001, loop_in(1): 3000, loop_out(1): 9000})
    grabbed = []
    x = at(wave, 3000)
    for _ in range(4):
        QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))
        grabbed.append(wave._dragging)
        QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 60))
    assert len(set(grabbed)) == 3 and grabbed[0] == grabbed[3]  # (three candidates within the radius, round and round)


def test_the_marker_under_the_cursor_lights_up_and_the_cursor_says_so(wave):
    QTest.mouseMove(wave, QPoint(at(wave, 3000) + 2, 60))
    assert wave._hover == loop_in(0) and wave.cursor().shape() == Qt.CursorShape.SizeHorCursor
    QTest.mouseMove(wave, QPoint(at(wave, 4500), 60))
    assert wave._hover is None and wave.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_grabbing_a_loops_marker_says_which_loop(wave):
    got = []
    wave.loopGrabbed.connect(got.append)
    wave.set_markers({**wave.markers, loop_in(2): 8000, loop_out(2): 8500})
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 8500), 60))
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 8500), 60))
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 1000), 60))  # (the sample's own: no loop)
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 1000), 60))
    assert got == [2]


# --------------------------------------------------------------------------- zoom and pan
def wheel(w, x, dy=0, dx=0, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QWheelEvent(QPointF(x, 60), w.mapToGlobal(QPointF(x, 60)), QPoint(), QPoint(dx, dy), Qt.MouseButton.NoButton,
                        modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(w, event)
    return event


def test_ctrl_wheel_zooms_around_the_cursor_and_the_frame_under_it_stays_put(wave):
    seen = []
    wave.viewChanged.connect(lambda *a: seen.append(a))
    frame = mw.frame_for_x(300, wave.width(), *wave.view_state())
    wheel(wave, 300, dy=120, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert wave.zoom > 1 and seen
    assert abs(mw.frame_for_x(300, wave.width(), *wave.view_state()) - frame) <= wave.view_state()[1] / 500 + 2
    wheel(wave, 300, dy=-120, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert wave.zoom == pytest.approx(1.0, abs=0.01) and wave.view_state() == (0, FRAMES)


def test_shift_wheel_pans_when_zoomed_and_stops_at_the_ends(wave):
    wave.set_zoom(4)  # (zooms around the middle)
    wave.set_view_start(0)
    wheel(wave, 100, dy=-120, modifiers=Qt.KeyboardModifier.ShiftModifier)  # (the wheel's direction pans the other way)
    start, length = wave.view_state()
    assert start > 0
    for _ in range(40):
        wheel(wave, 100, dy=-120, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert wave.view_state()[0] == FRAMES - length
    wave.set_view_start(-500)
    assert wave.view_state()[0] == 0


def test_a_plain_vertical_scroll_is_left_for_whatever_is_behind(wave):
    assert not wheel(wave, 100, dy=-120).isAccepted()


def test_zoomed_in_a_marker_can_be_placed_on_an_exact_frame(wave):
    wave.set_zoom(100)  # (100 frames in view)
    wave.set_view_start(2950)
    assert wave.view_state() == (2950, 100)
    drag(wave, loop_in(0), 2977)
    assert wave.markers[loop_in(0)] == 2977


def test_a_marker_that_is_scrolled_out_of_view_cant_be_grabbed(wave):
    wave.set_zoom(10)
    wave.set_view_start(5000)
    assert wave._candidates(0.0) == [] and all(wave._candidates(x) == [] for x in range(0, 1001, 50))  # (nothing in 5000..6000 but loop 1's end)
    wave.set_markers({mw.START: 0, mw.END: 9000})
    assert all(wave._candidates(x) == [] for x in range(0, 1001, 50))


def test_zoom_never_goes_below_the_whole_sample_or_past_one_frame(wave):
    wave.set_zoom(0.01)
    assert wave.zoom == 1.0
    wave.set_zoom(10**9)
    assert wave.view_state()[1] == 1


# ----------------------------------------------------------------------- without audio
def test_markers_work_without_any_audio(qt_app):
    w = MarkerWaveform()
    w.resize(1001, 150)
    w.set_audio(None, frames=5000)
    w.set_markers({mw.START: 0, mw.END: 4999})
    w.show()
    assert w.frames == 5000
    drag(w, mw.END, 2500)
    assert abs(w.markers[mw.END] - 2500) <= 5
    w.grab()  # (paints "no waveform to show" without trouble)
    empty = MarkerWaveform()
    empty.resize(500, 150)
    empty.grab()
    assert empty.frames == 0
    w.deleteLater()
    empty.deleteLater()


# ------------------------------------------------- overlapping markers: the top half or the bottom half
def click(w, frame, y, release=True):
    x = at(w, frame)
    QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    grabbed = w._dragging
    if release:
        QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    return grabbed


def test_where_a_sample_marker_and_a_loop_marker_overlap_the_top_half_takes_the_sample_and_the_bottom_half_the_loop(wave):
    wave.set_markers({mw.START: 3000, mw.END: 9000, loop_in(0): 3000, loop_out(0): 6000})  # the loop starts on the sample's start
    assert click(wave, 3000, 20) == mw.START  # (150 px high: 75 is the middle)
    assert click(wave, 3000, 130) == loop_in(0)
    assert click(wave, 3000, 74) == mw.START and click(wave, 3000, 76) == loop_in(0)


def test_overlap_means_close_enough_to_grab_not_exactly_the_same_frame(wave):
    wave.set_markers({mw.START: 3000, mw.END: 9000, loop_in(0): 3030, loop_out(0): 6000})  # 3 px apart: within the grab radius
    assert click(wave, 3000, 20) == mw.START and click(wave, 3030, 130) == loop_in(0)


def test_a_marker_with_nothing_on_top_of_it_is_grabbed_anywhere_along_its_line(wave):
    for y in (2, 40, 74, 76, 110, 148):
        assert click(wave, 1000, y) == mw.START  # (a sample marker, in the bottom half too)
        assert click(wave, 3000, y) == loop_in(0)  # (a loop marker, in the top half too)


def hover(w, x, y):
    pos = QPointF(x, y)
    QApplication.sendEvent(w, QMouseEvent(QEvent.Type.MouseMove, pos, w.mapToGlobal(pos), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def test_hovering_lights_up_what_a_click_would_grab(wave):
    wave.set_markers({mw.START: 3000, mw.END: 9000, loop_in(0): 3000, loop_out(0): 6000})
    hover(wave, at(wave, 3000), 20)
    assert wave._hover == mw.START
    hover(wave, at(wave, 3000), 130)
    assert wave._hover == loop_in(0)
    hover(wave, at(wave, 6000), 20)  # (a lone loop marker: the top half is fine)
    assert wave._hover == loop_out(0)


def test_each_kind_can_be_dragged_apart_from_the_other_when_they_overlap(wave):
    wave.set_markers({mw.START: 3000, mw.END: 9000, loop_in(0): 3000, loop_out(0): 6000})
    x0 = at(wave, 3000)
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0, 130))  # the loop's start
    QTest.mouseMove(wave, QPoint(at(wave, 4500), 130))
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 4500), 130))
    assert wave.markers[mw.START] == 3000 and abs(wave.markers[loop_in(0)] - 4500) <= 10
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0, 20))  # the sample's start
    QTest.mouseMove(wave, QPoint(at(wave, 1500), 20))
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(at(wave, 1500), 20))
    assert abs(wave.markers[mw.START] - 1500) <= 10 and abs(wave.markers[loop_in(0)] - 4500) <= 10


def test_several_loop_markers_on_top_of_each_other_still_take_turns(wave):
    wave.set_markers({mw.START: 0, mw.END: 9000, loop_in(0): 3000, loop_out(0): 6000, loop_in(1): 3000, loop_out(1): 7000})
    grabbed = [click(wave, 3000, 130) for _ in range(3)]
    assert grabbed[0] != grabbed[1] and grabbed[0] == grabbed[2]
