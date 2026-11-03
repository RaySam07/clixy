"""Tests for the island (the top notch, or the side-edge sliver), its
states, its calm hover, and the model label."""
import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def island(qapp):
    """On the right edge (most state tests were written for the side)."""
    from island import SideIsland
    widget = SideIsland(side="right")
    yield widget
    widget.close()


@pytest.fixture
def notch(qapp):
    """At the top of the screen, like a laptop's notch (the default)."""
    from island import SideIsland
    widget = SideIsland(side="top")
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
    assert island._target() == island.SLIVER, "the answer is heard, never shown"
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


def _mouse_on_notch(monkeypatch, island, dy=0):
    import island as island_mod
    from PyQt6.QtCore import QPoint
    g = island.geometry()
    shape = island._shape_rect()
    monkeypatch.setattr(island_mod.QCursor, "pos", staticmethod(
        lambda: QPoint(int(g.x() + shape.center().x()), int(g.y() + shape.center().y() + dy))))


def _clock(monkeypatch, start=100.0):
    import island as island_mod
    now = [start]
    monkeypatch.setattr(island_mod.time, "monotonic", lambda: now[0])
    return now


def _wait(island, now, seconds, animate=False):
    for _ in range(int(seconds / 0.05)):
        now[0] += 0.05
        island._animate() if animate else island._update_near()


def test_passing_by_doesnt_open_it(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 5)
    _wait(island, now, island._DWELL / 2)
    _mouse_at(monkeypatch, island, 600)
    _wait(island, now, 1.0)
    assert not island._near


def test_resting_on_it_opens_it_then_it_lingers_and_tucks_away(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 5)
    _wait(island, now, island._DWELL + 0.2)
    assert island._near
    _mouse_at(monkeypatch, island, 600)
    _wait(island, now, island._LINGER - 0.15)
    assert island._near, "still open just after the mouse leaves"
    _wait(island, now, 0.3)
    assert not island._near


def test_near_the_edge_is_not_on_it(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 40)  # close, but not on the sliver
    _wait(island, now, 2.0)
    assert not island._near


def test_it_stays_open_while_the_mouse_is_over_the_pill(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 5)
    _wait(island, now, 3.0, animate=True)
    _mouse_at(monkeypatch, island, island.GREET[0] - 10)  # over the pill, far past the peek reach
    _wait(island, now, 3.0)
    assert island._near


def test_the_greeting_shows_its_content_once_open(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 5)
    _wait(island, now, 4.0, animate=True)
    assert (island._w, island._h) == island.GREET
    assert island._fade_greet == 1.0
    island.grab()  # paints "Clixy here.", the light and the keys


def test_no_greeting_while_it_is_busy(island, monkeypatch):
    now = _clock(monkeypatch)
    _mouse_at(monkeypatch, island, 5)
    island.listening()
    _wait(island, now, 3.0, animate=True)
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


# --- The notch at the top ------------------------------------------------------------

def test_the_top_is_the_default(qapp, monkeypatch):
    import config
    from island import SideIsland
    monkeypatch.setattr(config, "ISLAND_SIDE", "top")
    widget = SideIsland()
    try:
        assert widget.side == "top" and widget.home_side == "top"
        assert widget._shape_rect().top() == 0, "flush with the top of the screen"
        assert abs(widget._shape_rect().center().x() - widget.width() / 2) < 1, "centred"
    finally:
        widget.close()


def test_it_says_hello_then_rests(notch, monkeypatch):
    now = _clock(monkeypatch, start=notch._t0)
    notch.name = "Rayan"
    assert notch._target() == notch.HELLO
    now[0] += notch._HELLO_S + 0.1
    assert notch._target() == notch.NOTCH


def test_a_new_day_says_hello_again(notch, monkeypatch):
    import datetime
    now = _clock(monkeypatch, start=notch._t0 + 100)
    assert notch._target() == notch.NOTCH
    notch._greeted_on = datetime.date(2000, 1, 1)
    notch._animate()
    assert notch._target() == notch.HELLO


def test_the_notch_only_opens_for_a_mouse_that_rests_on_it(notch, monkeypatch):
    now = _clock(monkeypatch, start=notch._t0 + 100)
    _mouse_on_notch(monkeypatch, notch, dy=60)  # just below it: never
    _wait(notch, now, 2.0, animate=True)
    assert notch._target() == notch.NOTCH
    _mouse_on_notch(monkeypatch, notch)
    _wait(notch, now, notch._DWELL / 2)
    assert notch._target() == notch.NOTCH, "passing over it doesn't open it"
    _wait(notch, now, notch._DWELL)
    assert notch._near and notch._target()[0] == notch.DASH_W, "resting on it opens the dashboard"
    notch.dashboard = False
    assert notch._target() == notch.HINT, "or just the keys, with the dashboard off"
    notch.dashboard = True
    _mouse_on_notch(monkeypatch, notch, dy=200)
    _wait(notch, now, 0.5)
    assert notch._target() == notch.NOTCH, "and it closes as soon as you move away"


def test_the_notch_widens_to_listen_then_speaks_without_showing_the_answer(notch, monkeypatch):
    now = _clock(monkeypatch, start=notch._t0 + 100)
    notch.listening()
    assert notch._target() == notch.PILL
    _wait(notch, now, 2.0, animate=True)
    assert notch._fade_voice == 1.0 and notch._fade_notch == 0.0
    notch.say("Press the Windows key and P together.")
    assert notch._target() == notch.NOTCH, "the answer is only heard: the notch settles back"
    _wait(notch, now, 2.0, animate=True)
    assert notch._fade_voice == 0.0 and notch._fade_note == 0.0
    assert notch._light_b > 0.4, "its light stays alive while the answer plays"
    notch.done()
    assert notch._collapse_timer.isActive()
    notch._collapse()
    assert notch._target() == notch.NOTCH


def test_the_notch_is_a_tab_with_flared_shoulders(notch):
    rect = notch._shape_rect()
    bounds = notch._shape_path().boundingRect()
    assert bounds.top() == 0 and bounds.left() < rect.left() and bounds.right() > rect.right(), \
        "the shoulders flare out along the top edge"
    assert notch._shape_path().contains(rect.center())
    for side in ("left", "right"):
        notch.set_home(side)
        rect, bounds = notch._shape_rect(), notch._shape_path().boundingRect()
        assert bounds.top() < rect.top() and bounds.bottom() > rect.bottom(), "and along a side edge"
    notch.set_home("top")


def test_the_light_wears_the_buddys_colour(notch):
    import theme
    from island import accent_shades
    for buddy in ("ember", "splash"):
        theme.set_avatar(buddy, persist=False)
        hot, mid, deep = accent_shades(theme.current_palette().flood)
        assert mid == theme.current_palette().flood
        assert hot.lightnessF() > mid.lightnessF() > deep.lightnessF()
        notch.grab()
    theme.set_avatar(theme.DEFAULT_AVATAR, persist=False)


def test_the_light_swells_with_your_voice(notch, monkeypatch):
    now = _clock(monkeypatch, start=notch._t0 + 100)
    notch.listening()
    notch.set_level(0.0)
    _wait(notch, now, 1.0, animate=True)
    quiet = notch._light_r
    notch.set_level(1.0)
    _wait(notch, now, 1.0, animate=True)
    assert notch._light_r > quiet + 1.5


def test_a_notice_fits_in_a_few_lines(notch, monkeypatch):
    now = _clock(monkeypatch, start=notch._t0 + 100)
    notch.note("Dev mode is on: I restart by myself whenever the code changes. " * 4)
    w, h = notch._target()
    assert w <= notch.NOTE_W and len(notch._notice_lines()[2]) <= 3
    _wait(notch, now, 2.0, animate=True)
    assert notch._fade_note == 1.0
    notch.grab()


def test_a_guide_step_shows_only_its_number(notch):
    notch.step("pick duplicate.", 2, 3)
    assert notch._notice() == "Step 2 of 3"
    assert notch._sentences == ["pick duplicate."]


def test_every_notch_state_paints(notch, monkeypatch):
    now = _clock(monkeypatch, start=notch._t0)
    notch.name = "Rayan"
    for setup in (lambda: None,                                         # hello
                  lambda: now.__setitem__(0, now[0] + 10),              # resting
                  lambda: setattr(notch, "_near_until", now[0] + 99),   # the keys hint
                  notch.listening, notch.thinking,
                  lambda: notch.say("Click Export."), lambda: notch.note("I didn't catch that.")):
        setup()
        _wait(notch, now, 1.5, animate=True)
        notch.grab()


def test_a_guide_step_moves_the_notch_off_what_you_click(notch):
    from PyQt6.QtCore import QRect
    g = notch.geometry()
    notch.step("pick duplicate.", 2, 2, keep_clear=QRect(g.center().x() + 20, 10, 80, 30))
    assert notch.side == "left", "the far side from the button"
    notch._collapse()
    assert notch.side == "top"


def test_settings_can_move_it(notch):
    notch.set_home("right")
    assert notch.side == "right" and notch.width() == notch._WIN_W
    notch.set_home("top")
    assert notch.side == "top" and (notch.width(), notch.height()) == notch._TOP_WIN


def test_the_settings_preview_shows_top_and_sides(qapp):
    from widgets import LookPreview
    preview = LookPreview()
    preview.resize(420, 210)
    for side in ("top", "left", "right"):
        preview.set_side(side)
        preview.jump()
        preview.grab()


def test_the_greeting_uses_your_first_name(monkeypatch):
    import config
    import sysinfo
    monkeypatch.setattr(sysinfo.sys, "platform", "linux")
    for setting, user, want in (("Rayan Sameer", "x", "Rayan"), ("", "rayan", "Rayan"), ("", "Administrator", "")):
        sysinfo.first_name.cache_clear()
        monkeypatch.setattr(config, "resolve_setting", lambda key, default="", s=setting: s if key == "USER_NAME" else default)
        monkeypatch.setenv("USERNAME", user)
        assert sysinfo.first_name() == want
    sysinfo.first_name.cache_clear()


def test_a_new_question_clears_the_last_answer(island):
    island.say("Old answer.")
    island.listening()
    assert island._sentences == [] and island._transcript == ""


def test_long_answers_never_grow_the_island(island):
    island.listening()
    for _ in range(30):
        island.say("This is a fairly long sentence that keeps going and going.")
    assert island._target() == island.SLIVER


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
        now = _clock(monkeypatch)
        _mouse_at(monkeypatch, widget, 5)
        _wait(widget, now, 3.0, animate=True)
        assert widget._near and widget._shape_rect().left() == 0
        widget.grab()
    finally:
        widget.close()


def test_every_state_paints(island):
    """Smoke test: each state renders without raising."""
    island.set_transcript("where is export")
    for setup in (island.listening, island.thinking,
                  lambda: island.say("Click Export."), lambda: island.note("I didn't catch that.")):
        setup()
        for _ in range(60):
            island._animate()
        island.grab()
