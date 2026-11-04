"""Clixy's look and feel (ui_kit.py): skins, springs, the tactile controls."""
from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import theme
    theme.load_fonts()
    return app


@pytest.fixture
def look(monkeypatch):
    """The stored appearance, as a variable (never the real keyring)."""
    import ui_kit
    monkeypatch.setattr(ui_kit, "_appearance", None)
    monkeypatch.setattr(ui_kit, "_listeners", [])
    stored = {"APPEARANCE": None}
    monkeypatch.setattr("config.resolve_setting", lambda name, default: stored.get(name) or default)
    yield stored
    ui_kit._appearance = None


def test_daylight_is_the_default_and_midnight_is_never_flat_black(look):
    import ui_kit
    assert ui_kit.appearance() == "light" and ui_kit.skin() is ui_kit.DAYLIGHT
    dark = ui_kit.MIDNIGHT
    for colour in (dark.canvas_top, dark.surface):
        r, g, b, _a = colour.getRgb()
        assert max(r, g, b) > 20 and b > r, "deep ink with colour in it, not black"


def test_switching_the_look_tells_everyone(look):
    import ui_kit
    heard = []
    ui_kit.on_skin_changed(lambda: heard.append(ui_kit.skin().name))
    ui_kit.set_appearance("dark")
    assert heard == ["dark"] and ui_kit.skin() is ui_kit.MIDNIGHT
    ui_kit.set_appearance("dark")
    assert heard == ["dark"], "no news, no call"
    ui_kit.set_appearance("nonsense")
    assert ui_kit.appearance() == "light"


def test_match_windows_follows_windows(look, monkeypatch):
    import ui_kit
    ui_kit.set_appearance("system")
    monkeypatch.setattr(ui_kit, "windows_is_dark", lambda: True)
    assert ui_kit.skin() is ui_kit.MIDNIGHT
    monkeypatch.setattr(ui_kit, "windows_is_dark", lambda: False)
    assert ui_kit.skin() is ui_kit.DAYLIGHT


def test_text_on_the_gel_stays_readable():
    from PyQt6.QtGui import QColor
    import ui_kit
    assert ui_kit.ink_on(QColor(30, 150, 255)).lightness() > 200, "white on Splash's blue"
    assert ui_kit.ink_on(QColor(255, 201, 51)).lightness() < 60, "dark on Twinkle's yellow"


def test_a_spring_overshoots_a_hair_and_settles():
    from ui_kit import Spring
    s = Spring(0.0, 420.0, 30.0)
    s.target = 1.0
    peak, steps = 0.0, 0
    while s.step(1 / 60) and steps < 600:
        peak = max(peak, s.x)
        steps += 1
    assert s.x == 1.0 and s.v == 0.0
    assert 1.0 < peak < 1.12, "lively, not wobbly"
    assert steps < 60, "settles in under a second"


def test_a_moving_widget_repaints_until_its_springs_rest(qapp):
    from PyQt6.QtWidgets import QWidget
    import ui_kit

    class Box(ui_kit.Motion, QWidget):
        pass

    box = Box()
    box.go("x", 10.0)
    ticker = ui_kit._the_ticker()
    assert box in ticker._items and ticker._timer.isActive()
    for _ in range(200):
        ticker._tick()
    assert box.val("x") == 10.0 and box not in ticker._items
    box.go("x", 3.0, snap=True)
    assert box.val("x") == 3.0


def test_the_controls_answer_clicks(qapp):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent
    import ui_kit

    toggle = ui_kit.Toggle(False)
    toggle.click()
    assert toggle.isChecked() and toggle.spring("on").target == 1.0
    toggle.setChecked(False)
    assert toggle.val("on") == 0.0, "set from code: it's simply there"

    seg = ui_kit.Segmented([("a", "Alpha"), ("b", "Beta")], "a")
    picked = []
    seg.changed.connect(picked.append)
    second = seg._rects()[1].center()
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(second), QPointF(second),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    seg.mousePressEvent(press)
    assert picked == ["b"] and seg.value == "b" and seg.spring("at").target == 1.0

    rail = ui_kit.NavRail([("general", "General"), ("sounds", "Sounds")])
    chosen = []
    rail.picked.connect(chosen.append)
    at = QPointF(40, rail.ROW * 1.5)
    rail.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, at, at, Qt.MouseButton.LeftButton,
                                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert chosen == ["sounds"] and rail.current == "sounds"

    slider = ui_kit.Slider(70, width=220)
    heard = []
    slider.changed.connect(heard.append)
    slider._set_from(slider._track().left())
    assert slider.value == 0 and heard == [0]


def test_everything_paints_in_both_skins(qapp, look):
    from PyQt6.QtWidgets import QWidget
    import ui_kit
    widgets = [ui_kit.Toggle(True), ui_kit.Segmented([("a", "A"), ("b", "B")], "b"), ui_kit.Slider(40),
               ui_kit.NavRail([(k, k.title()) for k in ui_kit.TILES if k in
                               ("general", "look", "sounds", "voice", "buddy", "shortcut", "model", "privacy",
                                "about")])]
    for choice in ("light", "dark"):
        ui_kit.set_appearance(choice)
        for w in widgets:
            assert isinstance(w, QWidget)
            assert not w.grab().isNull()


def test_sounds_stay_silent_in_tests(monkeypatch):
    import ui_kit
    calls = []

    class Fake:
        @staticmethod
        def play(name, gain=1.0):
            calls.append(name)

    monkeypatch.setitem(__import__("sys").modules, "sfx", Fake)
    ui_kit.sound("press")
    assert calls == [], "SILENT in tests"
