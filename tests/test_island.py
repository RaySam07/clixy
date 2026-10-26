"""Tests for the side island's states and the model label."""
import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def island(qapp):
    from island import SideIsland
    widget = SideIsland()
    yield widget
    widget.close()


def test_model_label():
    from island import model_label
    assert model_label("anthropic/claude-haiku-5-5") == "Haiku 5.5"
    assert model_label("claude-sonnet-5-5") == "Sonnet 5.5"
    assert model_label("qwen3-vl:4b-instruct") == "qwen3-vl"


def test_a_turn_opens_the_island_and_then_tucks_it_away(island):
    island.model_name = "Haiku 5.5"
    assert island._target() == island.SLIVER
    island.listening()
    assert island._state == "listening" and island._meta == "Haiku 5.5"
    assert island._target() == island.CAPSULE
    island.thinking()
    island.set_transcript("where do I export this")
    island.say("Click Export at the top right.")
    island.set_meta("2.1s · $0.0004")
    assert island._state == "answer"
    assert island._sentences == ["Click Export at the top right."]
    assert island._target()[0] == island.CARD_W
    island.done()
    assert island._collapse_timer.isActive()
    island._collapse()
    assert island._target() == island.SLIVER


def test_it_greets_you_when_the_mouse_comes_near(island):
    island._near = True
    assert island._target() == island.GREET


def _mouse_at(monkeypatch, island, dx, dy=0):
    """Put the mouse ``dx`` px in from the island's screen edge."""
    import island as island_mod
    from PyQt6.QtCore import QPoint
    g = island.geometry()
    edge = g.right() if island.side == "right" else g.left()
    x = edge - dx if island.side == "right" else edge + dx
    monkeypatch.setattr(island_mod.QCursor, "pos", staticmethod(lambda: QPoint(int(x), g.center().y() + dy)))


def _clock(monkeypatch, start=100.0):
    import island as island_mod
    now = [start]
    monkeypatch.setattr(island_mod.time, "monotonic", lambda: now[0])
    return now


def test_the_greeting_lingers_a_moment_then_tucks_away(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 20)
    island._update_near()
    assert island._near
    _mouse_at(monkeypatch, island, 600)
    now[0] += island._LINGER - 0.1
    island._update_near()
    assert island._near, "still open just after the mouse leaves"
    now[0] += 0.2
    island._update_near()
    assert not island._near


def test_it_stays_open_while_the_mouse_is_over_the_pill(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 20)
    for _ in range(120):
        island._animate()
    _mouse_at(monkeypatch, island, island.GREET[0] - 10)  # over the pill, far past the peek reach
    now[0] += 5
    island._update_near()
    assert island._near


def test_the_greeting_shows_its_content_once_open(island, monkeypatch):
    _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 20)
    for _ in range(120):
        island._animate()
    assert (island._w, island._h) == island.GREET
    assert island._fade_greet == 1.0
    island.grab()  # paints the buddy, "Clixy here." and the keys


def test_no_greeting_while_it_is_busy(island, monkeypatch):
    _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 20)
    island.listening()
    for _ in range(120):
        island._animate()
    assert not island._near and island._fade_greet == 0.0
    assert island._target() == island.CAPSULE


def test_the_greeting_names_your_shortcut(island):
    assert island.hotkey == "Ctrl + Win"  # the default
    island.hotkey = "Ctrl + Shift + Space"
    island._near = True
    for _ in range(120):
        island._w, island._h = island.GREET
        island._animate()
    island.grab()  # a long shortcut still paints (the hint drops if there's no room)


def test_a_new_question_clears_the_last_answer(island):
    island.say("Old answer.")
    island.listening()
    assert island._sentences == [] and island._transcript == ""


def test_long_answers_never_grow_past_the_max_height(island):
    island.listening()
    for _ in range(30):
        island.say("This is a fairly long sentence that keeps going and going.")
    assert island._target()[1] <= island.MAX_CARD_H


def test_a_turn_with_no_answer_closes_right_away(island):
    island.listening()
    island.done()
    assert island._state == "idle"


def test_capture_hides_and_restores_it(island):
    island.hide_for_capture()
    assert not island.isVisible()
    island.show_after_capture()
    assert island.isVisible()


def test_spring_settles_on_the_target(island):
    island.listening()
    for _ in range(200):
        island._animate()
    assert (island._w, island._h) == island.CAPSULE
    assert island._fade_voice == 1.0


def test_it_can_live_on_the_left_edge(qapp, monkeypatch):
    from island import SideIsland
    widget = SideIsland(side="left")
    try:
        assert widget._shape_rect().left() == 0
        _clock(monkeypatch)
        _mouse_at(monkeypatch, widget, 20)
        for _ in range(120):
            widget._animate()
        assert widget._near and widget._shape_rect().left() == 0
        widget.grab()
    finally:
        widget.close()


def test_chips_for_labels_and_quotes_keep_punctuation_glued():
    from island import split_chips
    tokens = split_chips('Click Export, then "Save as PDF".', ["Export"])
    assert ("Export", True, False) in tokens
    assert (",", False, True) in tokens
    assert ("Save as PDF", True, False) in tokens
    assert (".", False, True) in tokens


def test_every_state_paints(island):
    """Smoke test: each state renders without raising."""
    island.set_transcript("where is export")
    for setup in (island.listening, island.thinking,
                  lambda: island.say("Click Export."), lambda: island.note("I didn't catch that.")):
        setup()
        for _ in range(60):
            island._animate()
        island.grab()
