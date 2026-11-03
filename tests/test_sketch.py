"""Sketch notes: hard tests. The note format, the formula evaluator (and
attacks on it), every item and layout drawn, 3D, pacing, the card's buttons,
saving, and the app's wiring."""
from __future__ import annotations

import json
import math

import pytest

import sketch


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# --- When a note is made ----------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "explain what's happening on this graph", "why is the sky blue", "how does photosynthesis work",
    "what is a fourier transform", "what's the difference between mitosis and meiosis",
    "teach me the french revolution", "help me understand derivatives", "summarize this article for me",
    "who was napoleon bonaparte", "solve this equation for x", "compare python vs javascript",
])
def test_learning_questions_get_a_note(q):
    assert sketch.wants_sketch(q)


@pytest.mark.parametrize("q", ["open spotify", "where is the save button", "thanks a lot clixy", "hi", "", "play some music"])
def test_small_talk_and_commands_dont(q):
    assert not sketch.wants_sketch(q)


# --- Reading the model's note -----------------------------------------------------------------

def test_a_note_with_words_around_it_is_read():
    note = sketch.parse_note('here you go:\n{"title": "t", "items": [{"type": "axes"}, {"nope": 1}, "x"], '
                             '"followups": ["a", "b", "c", "d"]}\nthanks')
    assert note["items"] == [{"type": "axes"}] and note["followups"] == ["a", "b", "c"]


@pytest.mark.parametrize("reply", ["", "no json here", "{bad json", '{"skip": true}', '{"title": "x", "items": []}',
                                   '{"title": "x", "items": "not a list"}', "[1, 2, 3]"])
def test_nothing_to_draw(reply):
    assert sketch.parse_note(reply) is None


def test_huge_notes_are_trimmed():
    note = sketch.parse_note(json.dumps({"title": "x" * 500, "items": [{"type": "text"}] * 200}))
    assert len(note["items"]) == 30 and len(note["title"]) == 70


# --- The formula evaluator, including attacks on it ---------------------------------------------

def test_formulas_evaluate():
    assert sketch.compile_expr("sin(2*pi*x)")(0.25) == pytest.approx(1.0)
    assert sketch.compile_expr("x^2 + 1")(3) == 10
    assert sketch.compile_expr("sin(x)*cos(y)", ("x", "y"))(math.pi / 2, 0) == pytest.approx(1.0)
    assert math.isnan(sketch.compile_expr("1/x")(0))
    assert math.isnan(sketch.compile_expr("log(x)")(-1))


@pytest.mark.parametrize("attack", [
    "__import__('os').system('calc')", "().__class__.__bases__[0].__subclasses__()", "open('x').read()",
    "x.real", "[i for i in range(9)]", "lambda: 1", "eval('1')", "globals()", "x if x else 1",
    "'a' * 99", "y", "exec('import os')", "x ** 999", "(x, x)", "True + x",
])
def test_anything_but_plain_maths_is_refused(attack):
    assert sketch.compile_expr(attack) is None


@pytest.mark.parametrize("bomb", ["10**10**10", "9**9**9**9", "exp(exp(exp(x)))", "(x+1)**(x**12)"])
def test_huge_numbers_fail_fast_instead_of_freezing(bomb):
    import time
    start = time.monotonic()
    fn = sketch.compile_expr(bomb)
    assert fn is not None
    value = fn(50)
    assert time.monotonic() - start < 0.5
    assert math.isnan(value) or math.isfinite(value)


# --- Drawing: every item, every layout, 3D --------------------------------------------------------

from PyQt6.QtCore import QRectF  # noqa: E402

CANVAS = QRectF(40, 110, 426, 200)

ALL_2D = {"title": "t", "view": {"x": [0, 6], "y": [-1.5, 1.5]}, "items": [
    {"type": "axes", "x_label": "time", "y_label": "amp", "ticks": True, "origin": True},
    {"type": "function", "expr": "sin(3*x)", "from": 0, "to": 6, "label": "3 Hz"},
    {"type": "curve", "points": [[0, 0], [1, 1], [2, 0]], "color": "red", "label": "c"},
    {"type": "dot", "at": [3, 1], "label": "peak"},
    {"type": "line", "from": [0, 1], "to": [3, 1], "dashed": True},
    {"type": "arrow", "from": [4, -1], "to": [5, 0], "label": "here"},
    {"type": "box", "from": [1, -1], "to": [2, 1], "label": "window"},
    {"type": "circle", "at": [3, 0], "r": 0.4, "label": "o"},
    {"type": "text", "at": [5.8, 1.2], "text": "a long note that runs off the edge of the card", "size": "big"},
    {"type": "function", "expr": "__import__('os')"},           # refused, skipped
    {"type": "dot", "at": "nonsense"}, {"type": "mystery"},       # bad items, skipped
]}

LAYOUT_NOTES = {
    "steps": {"type": "steps", "steps": ["light hits leaf", "water splits", "co2 captured", "sugar made", "oxygen out"]},
    "cycle": {"type": "cycle", "center": "water cycle", "stages": ["evaporation", "condensation", "rain", "collection"]},
    "compare": {"type": "compare", "left": "mitosis", "right": "meiosis",
                "rows": [["2 cells", "4 cells"], ["identical", "varied"], ["growth", "sex cells"]]},
    "timeline": {"type": "timeline", "events": [["1789", "bastille"], ["1793", "terror"], ["1799", "napoleon"]]},
    "mindmap": {"type": "mindmap", "center": "photosynthesis", "nodes": ["sunlight", "water", "co2", "glucose", "oxygen"]},
    "points": {"type": "points", "points": ["energy from light", "makes glucose", "releases oxygen"]},
}

ALL_3D = {"title": "t", "view": {"3d": True, "range": 2}, "items": [
    {"type": "axes3d"}, {"type": "vector3d", "to": [1, 1, 1], "label": "v"}, {"type": "point3d", "at": [1, 0, 1]},
    {"type": "line3d", "from": [0, 0, 0], "to": [1, 0, 0], "dashed": True},
    {"type": "curve3d", "x": "cos(t)", "y": "sin(t)", "z": "t/6", "from": 0, "to": 12.6},
    {"type": "box3d", "at": [0, 0, 0.5], "size": [1, 1, 1]}, {"type": "sphere", "r": 1},
    {"type": "surface", "expr": "sin(x)*cos(y)"}, {"type": "curve3d", "x": "os.system()"},
]}


def _inside(strokes, canvas, slack=30):
    big = canvas.adjusted(-slack, -slack, slack, slack)
    for s in strokes:
        for p in s.points + ([s.at] if s.at is not None else []):
            assert big.contains(p), f"{s.kind} {s.text!r} drawn off the canvas at {p}"


def test_every_2d_item_draws_and_stays_on_the_card(qapp):
    strokes = sketch.build(ALL_2D, CANVAS)
    kinds = [s.kind for s in strokes]
    assert kinds.count("dot") == 1 and "text" in kinds and "line" in kinds
    assert any(s.head for s in strokes) and any(s.dashed for s in strokes)
    _inside([s for s in strokes if s.kind == "text"], CANVAS, slack=10)


@pytest.mark.parametrize("name", list(LAYOUT_NOTES))
def test_every_layout_draws_on_the_canvas(qapp, name):
    strokes = sketch.build({"title": "t", "items": [LAYOUT_NOTES[name]]}, CANVAS)
    assert len(strokes) >= 3
    _inside(strokes, CANVAS)


def test_3d_draws_and_turns(qapp):
    a = sketch.build(ALL_3D, CANVAS, angle=0)
    b = sketch.build(ALL_3D, CANVAS, angle=20)
    assert len(a) == len(b) > 8
    vec_a = next(s for s in a if s.head and s.color == sketch.COLORS["blue"])
    vec_b = next(s for s in b if s.head and s.color == sketch.COLORS["blue"])
    assert vec_a.points[-1] != vec_b.points[-1], "turning moves it"
    _inside(a, CANVAS, slack=60)


def test_strokes_draw_one_after_another():
    strokes = sketch.build({"title": "t", "items": [LAYOUT_NOTES["steps"]]}, CANVAS)
    for a, b in zip(strokes, strokes[1:]):
        assert b.start >= a.start
    total = strokes[-1].start + strokes[-1].duration
    assert 1.0 < total < 20.0


def test_empty_or_broken_layouts_draw_nothing_and_dont_crash(qapp):
    for item in ({"type": "steps"}, {"type": "compare", "rows": "x"}, {"type": "timeline", "events": [[1]]},
                 {"type": "mindmap"}, {"type": "points", "points": []}, {"type": "cycle", "stages": ["one"]}):
        sketch.build({"title": "t", "items": [item]}, CANVAS)


# --- The card ------------------------------------------------------------------------------------

def _card(qapp, monkeypatch, note):
    clock = [100.0]
    monkeypatch.setattr(sketch.time, "monotonic", lambda: clock[0])
    saved = []
    monkeypatch.setattr(sketch, "save_note", lambda n, img: saved.append(n))
    card = sketch.NoteCard()
    card.show_note(note)
    return card, clock, saved


@pytest.mark.parametrize("note", [ALL_2D, ALL_3D] + [{"title": "t", "items": [v]} for v in LAYOUT_NOTES.values()])
def test_the_card_draws_every_kind_of_note_start_to_finish(qapp, monkeypatch, note):
    card, clock, saved = _card(qapp, monkeypatch, {**note, "followups": ["why?", "show more"]})
    try:
        for t in (0.1, 0.6, 2.0, 5.0, 30.0):
            clock[0] = card._t0 + t
            card._tick()
            card.grab()
        assert saved, "a finished note is kept"
    finally:
        card.close()


def test_follow_ups_and_clear(qapp, monkeypatch):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    card, clock, _saved = _card(qapp, monkeypatch, {**ALL_2D, "followups": ["Why wrap it?", "Show two"]})
    asked, cleared = [], []
    card.followup.connect(asked.append)
    card.cleared.connect(lambda: cleared.append(True))

    def click(point):
        event = QMouseEvent(QMouseEvent.Type.MouseButtonPress, point, point, Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        card.mousePressEvent(event)

    click(card._chips[1][0].center())
    assert asked == ["Show two"]
    click(QPointF(card._clear_rect.center()))
    assert cleared and not card.isVisible()
    card.close()


def test_notes_are_kept(tmp_path, monkeypatch, qapp):
    from PyQt6.QtGui import QImage
    monkeypatch.setattr(sketch, "NOTES_DIR", tmp_path)
    image = QImage(10, 10, QImage.Format.Format_ARGB32)
    image.fill(0)
    base = sketch.save_note({"title": "Why waves wrap?", "items": []}, image)
    assert base.with_suffix(".json").exists() and base.with_suffix(".png").exists()
    assert "Why waves wrap" in base.name


# --- In the app -------------------------------------------------------------------------------------

def _app(mocker):
    from app import ClickyApp
    return ClickyApp(ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(), tts_client=mocker.MagicMock(),
                     memory_store=mocker.MagicMock(), overlay_controller=mocker.MagicMock(),
                     hotkey_instance=mocker.MagicMock())


def test_a_learning_question_asks_for_a_note_and_shows_it(mocker, monkeypatch):
    import threading
    app = _app(mocker)
    shown = []
    app.sig_show_sketch.disconnect()
    app.sig_show_sketch.connect(shown.append)
    stream = mocker.MagicMock()
    stream.text_deltas.return_value = iter([json.dumps({"title": "Waves", "items": [{"type": "axes"}]})])
    app._smart_ai = mocker.MagicMock()
    app._smart_ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=stream)
    app._smart_ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)
    cap = mocker.MagicMock()
    app._make_sketch("why do waves wrap", cap, threading.Event(), mocker.MagicMock())
    assert shown and shown[0]["title"] == "Waves"
    assert app._smart_ai.ask_stream.call_args.kwargs["system_prompt"] is sketch.PROMPT
    shown.clear()
    stop = threading.Event()
    stop.set()
    stream.text_deltas.return_value = iter([json.dumps({"title": "Waves", "items": [{"type": "axes"}]})])
    app._make_sketch("why do waves wrap", cap, stop, mocker.MagicMock())
    assert not shown, "a cancelled question shows nothing"


def test_a_follow_up_button_asks_without_listening(mocker):
    import queue
    import threading
    from PIL import Image
    app = _app(mocker)
    cap = mocker.MagicMock()
    cap.image = Image.new("RGB", (1280, 800))
    cap.label, cap.scale_x, cap.scale_y = "screen 1 of 1", 1.0, 1.0
    cap.monitor = {"left": 0, "top": 0, "width": 1280, "height": 800}
    cap.target_width, cap.target_height, cap.is_cursor_screen = 1280, 800, True
    app._ai.ask_stream.side_effect = RuntimeError("stop after the question is known")
    q: queue.Queue = queue.Queue(maxsize=1)
    q.put(([cap], "", "test"))
    seen = []
    app.sig_island_transcript.connect(seen.append)
    mocker.patch.object(type(app), "_make_sketch")
    app._pipeline_worker("TEST.EXE", "Win", threading.Event(), q, question="Show two frequencies")
    app._stt.stop_recording.assert_not_called()
    assert seen == ["Show two frequencies"]
