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
    assert island._target() == island.COLLAPSED
    island.listening()
    assert island._state == "listening" and island._meta == "Haiku 5.5"
    assert island._target()[0] == island.OPEN_WIDTH
    island.thinking()
    island.set_transcript("where do I export this")
    island.say("Click Export at the top right.")
    island.set_meta("2.1s · $0.0004")
    assert island._state == "answer"
    assert island._sentences == ["Click Export at the top right."]
    island.done()
    assert island._collapse_timer.isActive()
    island._collapse()
    assert island._target() == island.COLLAPSED


def test_a_new_question_clears_the_last_answer(island):
    island.say("Old answer.")
    island.listening()
    assert island._sentences == [] and island._transcript == ""


def test_long_answers_never_grow_past_the_max_height(island):
    island.listening()
    for _ in range(30):
        island.say("This is a fairly long sentence that keeps going and going.")
    assert island._target()[1] <= island.MAX_HEIGHT


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
    assert island._w == island.OPEN_WIDTH
    assert island._content == 1.0
