"""Tests for the marks the buddy draws: shapes, constraints, label
placement, the drawing tour, aiming (close-ups) and snapping."""
from __future__ import annotations

import math

import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


AREA = (0, 0, 1280, 720)


def _area():
    from PyQt6.QtCore import QRectF
    return QRectF(*AREA)


def _marks(shapes, **kw):
    import drawing
    return drawing.build_marks(shapes, _area(), **kw)


# --- Grammar ---------------------------------------------------------------------

def test_box_and_highlight_tags_parse_and_never_reach_speech():
    from annotations import Box, Highlight, parse_annotations
    spoken, shapes = parse_annotations(
        "that's the frequency graph. [BOX:552,96,868,152:frequency graph][highlight:10,20,200,36:total row]"
    )
    assert spoken == "that's the frequency graph."
    assert shapes == [Box(552, 96, 868, 152, "frequency graph"), Highlight(10, 20, 200, 36, "total row")]


def test_a_box_given_corners_backwards_is_normalised():
    from annotations import Box, parse_annotations
    assert parse_annotations("x [BOX:300,200,100,50:a]")[1] == [Box(100, 50, 300, 200, "a")]


def test_a_cut_off_box_tag_is_never_spoken():
    from annotations import parse_annotations
    spoken, shapes = parse_annotations("click it. [BOX:10,20,3")
    assert spoken == "click it." and shapes == []


# --- Shapes and constraints -----------------------------------------------------------

def test_a_loop_surrounds_its_element_without_swallowing_the_screen(qapp):
    from PyQt6.QtCore import QPointF
    from annotations import Box
    (mark,) = _marks([Box(600, 300, 680, 328, "export")])
    assert mark.kind == "loop"
    ink = mark.ink()
    assert ink.contains(mark.target)
    assert ink.width() < 80 * 1.8 and ink.height() < 28 * 3   # snug, not a giant ring
    # Every corner of the element is inside the loop's path.
    from PyQt6.QtGui import QPainterPath
    path = QPainterPath(mark.points[0])
    for p in mark.points[1:]:
        path.lineTo(p)
    for corner in (QPointF(600, 300), QPointF(680, 300), QPointF(600, 328), QPointF(680, 328)):
        assert path.contains(corner)


def test_a_huge_circle_is_shrunk_to_a_sane_size(qapp):
    from annotations import Circle
    import drawing
    (mark,) = _marks([Circle(640, 360, 380, "graph")])
    assert mark.target.width() == 2 * drawing.CIRCLE_R[1]


def test_a_big_element_gets_a_rectangle_not_a_giant_loop(qapp):
    from annotations import Box
    (mark,) = _marks([Box(100, 100, 500, 180, "chart")])
    assert mark.kind == "rect"
    ink = mark.ink()
    assert ink.left() > 80 and ink.right() < 520


def test_marks_that_cover_half_the_screen_are_dropped(qapp):
    from annotations import Box
    assert _marks([Box(0, 0, 1200, 700, "everything")]) == []


def test_at_most_three_marks_and_no_repeats(qapp):
    from annotations import Box
    shapes = [Box(10 + 100 * i, 10, 60 + 100 * i, 40, f"b{i}") for i in range(5)]
    shapes.insert(1, Box(12, 11, 61, 41, "same as the first"))
    marks = _marks(shapes)
    assert [m.label for m in marks] == ["b0", "b1", "b2"]


def test_several_marks_are_numbered_in_order(qapp):
    from annotations import Box, Highlight
    marks = _marks([Box(100, 100, 150, 130, "a"), Highlight(300, 300, 500, 320, "b")])
    assert [m.badge for m in marks] == [1, 2]
    assert _marks([Box(100, 100, 150, 130, "a")])[0].badge == 0


def test_tiny_arrows_and_empty_labels_are_skipped(qapp):
    from annotations import Arrow, Label
    assert _marks([Arrow(10, 10, 15, 15), Label(10, 10, "  ")]) == []


def test_an_arrow_ends_with_its_head_on_the_target(qapp):
    from annotations import Arrow
    (mark,) = _marks([Arrow(100, 400, 400, 200)])
    tip = mark.head[1]
    assert (tip.x(), tip.y()) == (400, 200)
    end = mark.points[-1]
    assert math.hypot(end.x() - 400, end.y() - 200) < 1


def test_marks_look_the_same_every_time(qapp):
    from annotations import Box
    a = _marks([Box(600, 300, 680, 328, "x")])[0].points
    b = _marks([Box(600, 300, 680, 328, "x")])[0].points
    assert [(p.x(), p.y()) for p in a] == [(p.x(), p.y()) for p in b]


# --- Label placement ----------------------------------------------------------------

def test_a_label_goes_above_its_mark_when_that_is_free(qapp):
    from annotations import Box
    (mark,) = _marks([Box(600, 300, 680, 328, "export")])
    assert mark.pill.bottom() <= mark.ink().top()
    assert abs(mark.pill.center().x() - mark.target.center().x()) < 2


def test_a_label_goes_below_when_there_is_no_room_above(qapp):
    from annotations import Box
    (mark,) = _marks([Box(600, 8, 680, 30, "export")])
    assert mark.pill.top() >= mark.ink().bottom()


def test_labels_never_cover_marks_or_each_other(qapp):
    from annotations import Box
    marks = _marks([Box(420, 491, 452, 523, "should be minus"), Box(575, 493, 607, 525, "carry the one")])
    a, b = marks
    for pill in (a.pill, b.pill):
        for m in marks:
            assert not pill.intersects(m.ink())
    assert not a.pill.intersects(b.pill)


def test_a_label_avoids_busy_text_and_the_island(qapp):
    from PyQt6.QtCore import QRectF
    from annotations import Box
    import drawing
    cols, rows = 96, 54
    # Everything above the mark is dense text.
    cells = bytearray(cols * rows)
    for r in range(rows):
        for c in range(cols):
            cells[r * cols + c] = 120 if r < 18 else 0
    busy = drawing.BusyMap(bytes(cells), cols, rows)
    (mark,) = _marks([Box(600, 260, 680, 290, "export")], busy=busy)
    assert mark.pill.top() > 290  # went below, off the text
    # A box under the island's spot takes its label elsewhere.
    island = QRectF(1100, 0, 180, 720)
    (mark,) = _marks([Box(1000, 300, 1080, 330, "save")], avoid=[island])
    assert not mark.pill.intersects(island)


def test_a_label_set_apart_gets_a_line_back_to_its_mark(qapp):
    from annotations import Box
    # Crowded: a mark right above and one right below push the label out.
    marks = _marks([Box(600, 300, 680, 328, "export"), Box(590, 250, 690, 262, "a"), Box(590, 366, 690, 378, "b")])
    first = marks[0]
    if first.leader is not None:
        start, end = first.leader
        assert first.pill.adjusted(-1, -1, 1, 1).contains(start)
        assert min(math.hypot(p.x() - end.x(), p.y() - end.y()) for p in first.points) < 0.5


# --- The tour ----------------------------------------------------------------------------

def test_the_buddy_flies_to_each_mark_and_traces_it(qapp):
    from PyQt6.QtCore import QPointF
    from annotations import Box, Underline
    import drawing
    marks = _marks([Box(600, 300, 680, 328, "a"), Underline(200, 500, 160, "b")])
    tour = drawing.plan_tour(marks, QPointF(100, 100))
    assert marks[0].start > 0 and marks[1].start >= marks[0].start + marks[0].duration
    # While a mark draws, the buddy's tip is on its ink.
    for m in marks:
        for f in (0.2, 0.5, 0.9):
            t = m.start + m.duration * f
            pos, _scale, drawing_now = tour.pen(t)
            assert drawing_now is m
            assert min(math.hypot(p.x() - pos.x(), p.y() - pos.y()) for p in m.points) < 2.5
    # It moves smoothly: no jumps between frames at 60 fps.
    last = tour.pen(0)[0]
    t = 0.0
    while t < tour.total:
        t += 1 / 60
        pos = tour.pen(t)[0]
        assert math.hypot(pos.x() - last.x(), pos.y() - last.y()) < 40
        last = pos
    assert tour.pen(tour.total + 1)[0] == tour.rest


def test_marks_are_drawn_progressively(qapp):
    from PyQt6.QtCore import QPointF
    from annotations import Box
    import drawing
    (mark,) = _marks([Box(600, 300, 680, 328, "a")])
    drawing.plan_tour([mark], QPointF(0, 0))
    assert drawing.progress(mark, mark.start - 0.1) == 0
    assert 0 < drawing.progress(mark, mark.start + mark.duration / 2) < 1
    assert drawing.progress(mark, mark.start + mark.duration) == 1


def test_painting_every_kind_of_mark(qapp):
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QImage, QPainter
    from annotations import Arrow, Box, Highlight, Label, Underline
    import drawing
    marks = _marks([Box(100, 100, 150, 130, "a"), Arrow(400, 400, 600, 300), Highlight(200, 500, 400, 520, "c")])
    marks += _marks([Underline(700, 600, 120, "d"), Label(900, 200, "hint")])
    drawing.plan_tour(marks, QPointF(0, 0))
    image = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    for t in (0.5, 2.0, 30.0):
        p = QPainter(image)
        drawing.paint_marks(p, marks, t, now=t)
        p.end()
    assert image.pixelColor(125, 98).alpha() > 0 or image.pixelColor(150, 115).alpha() > 0


# --- Aiming ----------------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text_deltas(self):
        yield self._text


class _FakeClient:
    """Answers each "find" with the next scripted reply; records images."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.images = []

    def ask_stream(self, images, transcript, history, system_prompt="", max_tokens=0):
        self.images.append(images[0][0])
        return _FakeStream(self.replies.pop(0))


class _Cap:
    def __init__(self, w=1280, h=720, full=None):
        from PIL import Image
        self.image = Image.new("RGB", (w, h), "white")
        self.full = full


def test_parse_box():
    import aim
    assert aim.parse_box('{"box": [10, 20, 30, 40]}') == (10, 20, 30, 40)
    assert aim.parse_box('{"box": [30, 40, 10, 20]}') == (10, 20, 30, 40)
    assert aim.parse_box('{"box": null}') is None
    assert aim.parse_box("i can't see it") is False


def test_the_close_up_contains_the_guess_and_stays_on_screen():
    import aim
    l, t, r, b = aim.crop_window((1200, 680, 1270, 715), 1280, 720)
    assert 0 <= l and 0 <= t and r <= 1280 and b <= 720
    assert l <= 1200 and r >= 1270 and t <= 680 and b >= 715
    assert r - l >= 640  # at least half the screen across


def test_aim_moves_a_mark_onto_what_the_close_up_found():
    import aim
    from annotations import Box, Circle
    cap = _Cap()
    client = _FakeClient('{"box": [100, 50, 300, 100]}')
    out = aim.aim(client, cap, [Circle(640, 400, 200, "frequency graph")])
    (box,) = out
    assert isinstance(box, Box) and box.label == "frequency graph"
    # Mapped from the crop back to the screenshot: small and near the crop's top-left.
    l, t, r, b = aim.crop_window((440, 200, 840, 600), 1280, 720)
    kx = (r - l) / client.images[0].width
    assert box.x1 == round(l + 100 * kx)
    assert box.x2 - box.x1 < 400


def test_aim_looks_at_the_whole_screen_when_the_close_up_misses():
    import aim
    from annotations import Box
    client = _FakeClient('{"box": null}', '{"box": [800, 90, 1100, 160]}')
    (box,) = aim.aim(client, _Cap(), [Box(100, 500, 200, 560, "frequency graph")])
    assert (box.x1, box.y1, box.x2, box.y2) == (800, 90, 1100, 160)
    assert client.images[1].size == (1280, 720)


def test_a_mark_nobody_can_find_is_dropped():
    import aim
    from annotations import Box
    client = _FakeClient('{"box": null}', '{"box": null}')
    assert aim.aim(client, _Cap(), [Box(100, 500, 200, 560, "unicorn")]) == []


def test_a_failed_lookup_keeps_the_mark():
    import aim
    from annotations import Box

    class Broken:
        def ask_stream(self, **kw):
            raise ConnectionError("offline")

    shape = Box(100, 500, 200, 560, "save")
    assert aim.aim(Broken(), _Cap(), [shape]) == [shape]


def test_the_close_up_uses_the_full_resolution_screen():
    from PIL import Image
    import aim
    from annotations import Box
    client = _FakeClient('{"box": [10, 10, 50, 40]}')
    aim.aim(client, _Cap(full=Image.new("RGB", (2560, 1440))), [Box(600, 300, 680, 330, "x")])
    l, t, r, b = aim.crop_window((600, 300, 680, 330), 1280, 720)
    sent = client.images[0]
    assert sent.width > r - l  # sharper than the screenshot's own pixels


def test_aim_keeps_at_most_three_marks_and_one_written_label():
    import aim
    from annotations import Box, Label
    shapes = [Box(i * 10, 0, i * 10 + 5, 5, "") for i in range(5)] + [Label(1, 1, "a"), Label(2, 2, "b")]
    out = aim.aim(_FakeClient(), _Cap(), shapes)  # unlabelled boxes aren't looked up
    assert len([s for s in out if isinstance(s, Box)]) == 3
    assert [s.text for s in out if isinstance(s, Label)] == ["a"]


def test_busy_map_sees_text(qapp):
    from PIL import Image, ImageDraw
    import aim
    img = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(img)
    for y in range(20, 200, 14):
        draw.text((20, y), "lorem ipsum dolor sit amet " * 6, fill="black")
    busy = aim.busy_map(img)
    assert busy.score(0.0, 0.03, 0.5, 0.27) > busy.score(0.0, 0.6, 0.5, 0.9)
    assert busy.luma(0, 0.6, 1, 1) > 0.9


# --- Snapping -------------------------------------------------------------------------------

def test_snap_accepts_a_control_that_matches_the_guess():
    import snap
    monitor = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    assert snap.accept((1100, 40, 1190, 80), (1104, 46, 1186, 76), 50000, monitor)


def test_snap_refuses_windows_panels_and_mismatches():
    import snap
    monitor = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    guess = (1100, 40, 1190, 80)
    assert not snap.accept(guess, (0, 0, 2560, 1400), 50032, monitor)      # a window
    assert not snap.accept(guess, (1000, 30, 1300, 90), 50033, monitor)    # a pane
    assert not snap.accept(guess, (1300, 40, 1390, 80), 50000, monitor)    # elsewhere
    assert not snap.accept(guess, (1140, 55, 1146, 61), 50000, monitor)    # a speck
    assert not snap.accept(guess, (600, 20, 1800, 600), 50000, monitor)    # far too big


def test_text_inside_a_button_stands_for_the_button():
    import snap
    text = ((1110, 50, 1150, 70), snap._TEXT)
    button = ((1100, 44, 1190, 78), 50000)
    assert snap.pick_rect(text, button) == button
    assert snap.pick_rect(text, ((0, 0, 2000, 1000), 50033)) == text


def test_refine_snaps_boxes_and_circles_and_leaves_the_rest():
    import snap
    from annotations import Arrow, Box, Circle
    monitor = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    found = {(1145, 60): ((1104, 46, 1186, 76), 50000)}
    out = snap.refine([Box(1100, 40, 1190, 80, "export"), Circle(300, 300, 10, "x"), Arrow(1, 1, 50, 50)],
                      monitor, rect_at=lambda x, y: found.get((x, y)))
    assert out[0] == Box(1104, 46, 1186, 76, "export")
    assert out[1] == Circle(300, 300, 10, "x")
    assert out[2] == Arrow(1, 1, 50, 50)


def test_snap_does_nothing_off_windows(monkeypatch):
    import snap
    monkeypatch.setattr(snap.sys, "platform", "linux")
    assert snap.element_rect_at(10, 10) is None


# --- The overlay: drawing, hover, fade ------------------------------------------------------

@pytest.fixture
def overlay_window(qapp, monkeypatch):
    import overlay
    monkeypatch.setattr(overlay, "apply_clickthrough_styles", lambda hwnd: None)
    window = overlay.OverlayWindow(qapp.primaryScreen())
    window.resize(1280, 720)
    yield window
    window.close()


def test_the_buddy_draws_then_reports_done(overlay_window, monkeypatch):
    import time as _time
    from PyQt6.QtCore import QPoint
    from annotations import Box
    clock = [100.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    done = []
    overlay_window._pointer_visible = True
    overlay_window._pointer_pos = QPoint(50, 50)
    overlay_window.start_drawing([Box(600, 300, 680, 328, "export")], on_done=lambda: done.append(1))
    assert overlay_window._mood() == "happy"
    total = overlay_window._tour.total
    clock[0] += total / 2
    overlay_window._step_marks(clock[0])
    assert overlay_window._pointer_pos != QPoint(50, 50)
    clock[0] += total
    overlay_window._step_marks(clock[0])
    assert done == [1] and overlay_window._tour is None
    assert overlay_window.drawing_labels == {"export"}


def test_hovering_a_mark_says_got_it_and_it_fades(overlay_window, monkeypatch):
    import time as _time
    from PyQt6.QtCore import QPoint
    from annotations import Box
    import overlay
    clock = [100.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    mouse = [QPoint(5, 5)]
    monkeypatch.setattr(overlay.QCursor, "pos", staticmethod(lambda: overlay_window.mapToGlobal(mouse[0])))
    overlay_window.start_drawing([Box(600, 300, 680, 328, "export")])
    clock[0] += overlay_window._tour.total + 0.1
    overlay_window._step_marks(clock[0])      # tour ends; the mouse is away
    overlay_window._step_marks(clock[0])
    mouse[0] = QPoint(640, 314)               # onto the export button
    clock[0] += 0.1
    overlay_window._step_marks(clock[0])
    assert overlay_window._marks[0].acked_at == clock[0]
    clock[0] += 1.0
    overlay_window._step_marks(clock[0])
    assert overlay_window._marks == []


def test_a_mark_drawn_under_the_mouse_waits_for_it_to_leave(overlay_window, monkeypatch):
    import time as _time
    from PyQt6.QtCore import QPoint
    from annotations import Box
    import overlay
    clock = [100.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(overlay.QCursor, "pos", staticmethod(lambda: overlay_window.mapToGlobal(QPoint(640, 314))))
    overlay_window.start_drawing([Box(600, 300, 680, 328, "export")])
    clock[0] += overlay_window._tour.total + 2
    overlay_window._step_marks(clock[0])
    overlay_window._step_marks(clock[0])
    assert overlay_window._marks[0].acked_at is None


def test_marks_fade_after_the_answer_and_clear_on_a_new_question(overlay_window, monkeypatch):
    import time as _time
    from annotations import Box
    clock = [100.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    overlay_window.start_drawing([Box(600, 300, 680, 328, "export")])
    overlay_window.fade_annotations()          # still drawing: waits
    assert overlay_window._marks_fade_at is None
    clock[0] += overlay_window._tour.total + 0.1
    overlay_window._step_marks(clock[0])
    overlay_window._marks_fade_at = clock[0]
    clock[0] += 1.0
    overlay_window._step_marks(clock[0])
    assert overlay_window._marks == []
    overlay_window.start_drawing([Box(600, 300, 680, 328, "export")])
    overlay_window.clear_annotations()
    assert overlay_window._marks == [] and overlay_window._tour is None


def test_controller_hands_the_buddy_to_the_drawing(qapp, monkeypatch):
    import overlay
    from annotations import Box
    monkeypatch.setattr(overlay, "apply_clickthrough_styles", lambda hwnd: None)
    screen = qapp.primaryScreen()
    controller = overlay.OverlayController(screens=[screen])
    try:
        geo = screen.geometry()
        monitor = {"left": geo.x(), "top": geo.y(), "width": geo.width(), "height": geo.height()}
        controller.show_annotations([Box(600, 300, 680, 328, "export")], monitor)
        target = controller._pointing_overlay
        assert controller._state == overlay._OverlayState.POINTING
        assert not controller._follow_timer.isActive()
        assert target._tour is not None
        controller._on_drawing_done()
        assert controller._dwell_timer.isActive()
        controller.island_done()
        assert controller._marks_fade_timer.isActive()
        controller.clear_all_annotations()
        assert target._marks == [] and not controller._marks_fade_timer.isActive()
    finally:
        controller._follow_timer.stop()
        controller._dwell_timer.stop()
        for o in controller.overlays:
            o.close()


# --- The pipeline in draw mode ------------------------------------------------------------

def _draw_mode_app(mocker, reply: str):
    import queue as _queue
    import threading
    from PIL import Image
    from app import ClickyApp
    mocker.patch("app.ANNOTATION_MODE", "on")
    app = ClickyApp(
        ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(), tts_client=mocker.MagicMock(),
        memory_store=mocker.MagicMock(), overlay_controller=mocker.MagicMock(), hotkey_instance=mocker.MagicMock(),
    )
    cap = mocker.MagicMock()
    cap.image = Image.new("RGB", (1280, 800))
    cap.full = None
    cap.label = "screen 1 of 1"
    cap.scale_x = cap.scale_y = 1.0
    cap.monitor = {"left": 0, "top": 0, "width": 1280, "height": 800}
    cap.target_width, cap.target_height = 1280, 800
    cap.is_cursor_screen = True
    app._press_captures = [cap]
    app._press_memory = ""
    app._press_cursor_pos = (100, 100)
    app._stt.stop_recording.return_value = "what does the right graph show"
    stream = mocker.MagicMock()
    stream.text_deltas.return_value = iter([reply])
    stream.final_result.return_value = mocker.MagicMock(
        spoken_text=reply, coordinate=None, element_label=None, screen_number=None,
    )
    app._ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=stream)
    app._ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)
    queue = _queue.Queue(maxsize=1)
    queue.put(([cap], "", "test"))

    def run():
        app._pipeline_worker("CHROME.EXE", "YouTube", threading.Event(), queue)
    return app, run


def test_in_draw_mode_the_buddy_draws_the_aimed_marks_instead_of_pointing(qapp, mocker):
    from annotations import Box
    reply = "that's the frequency graph, one spike per pure tone. [BOX:300,300,700,700:frequency graph]"
    app, run = _draw_mode_app(mocker, reply)
    aimed = [Box(820, 90, 1100, 160, "frequency graph")]
    aim_call = mocker.patch("aim.aim", return_value=aimed)
    mocker.patch("snap.refine", side_effect=lambda shapes, monitor, dbg=None: shapes)
    pointed, drawn = [], []
    app.sig_point_at.connect(lambda *a: pointed.append(a))
    app.sig_show_annotations.connect(lambda shapes, monitor, busy: drawn.append((shapes, busy)))
    run()
    spoken = " ".join(c.args[0] for c in app._tts.speak_sentence.call_args_list)
    assert "BOX" not in spoken and "300" not in spoken
    aim_call.assert_called_once()
    assert pointed == []                       # no separate pointing flight
    (shapes, busy), = drawn
    assert shapes == aimed and busy is not None


def test_speech_never_waits_for_aiming(qapp, mocker):
    from annotations import Box
    reply = "click export up top. [BOX:10,10,60,30:export]"
    app, run = _draw_mode_app(mocker, reply)
    order = []
    app._tts.speak_sentence.side_effect = lambda s: order.append("speak")
    mocker.patch("aim.aim", side_effect=lambda *a, **k: order.append("aim") or [Box(10, 10, 60, 30, "export")])
    mocker.patch("snap.refine", side_effect=lambda shapes, monitor, dbg=None: shapes)
    run()
    assert order.index("speak") < order.index("aim")
