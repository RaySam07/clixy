"""The Clixy panel (panel.py): it grows out of the notch, shows your newest
sketch note, takes a typed question, and follows a running guide."""
from __future__ import annotations

import datetime as dt
import threading
import time

import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import theme
    theme.load_fonts()
    return app


def _note(folder, stamp: str, title: str, *, png: bool = True, size=(1012, 776)):
    """A note as sketch.save_note keeps it: '<stamp> <title>.json' and '.png'."""
    import json
    from PyQt6.QtGui import QColor, QImage
    base = folder / f"{stamp} {title.replace(',', '')}"
    base.with_suffix(".json").write_text(json.dumps({"title": title, "followups": ["Why?"]}), encoding="utf-8")
    if png:
        image = QImage(*size, QImage.Format.Format_ARGB32)
        image.fill(QColor(251, 248, 242))
        image.save(str(base.with_suffix(".png")))
    return base


@pytest.fixture
def notes(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    _note(folder, "2026-11-04 0009", "Wrapping a wave around a circle")
    _note(folder, "2026-11-04 0015", "Polynomials approximating cosine")
    _note(folder, "2026-11-04 0020", "Two strokes, one power cycle")
    return folder


@pytest.fixture
def panel(qapp, tmp_path, monkeypatch):
    import panel as panel_mod
    monkeypatch.setattr(panel_mod, "_key_down", lambda vk: False)
    monkeypatch.setattr(panel_mod, "_start", lambda path: None)
    widget = panel_mod.ClixyPanel()
    widget.notes_folder = tmp_path / "empty-notes"
    widget.name = "Rayan"
    yield widget
    widget._tick.stop()
    widget.close()


def _settle(widget, frames=240):
    for _ in range(frames):
        if not widget._tick.isActive():
            break
        widget._animate()


def test_notes_are_newest_first_with_their_real_titles_and_times(notes):
    import panel as panel_mod
    found = panel_mod.load_notes(notes)
    assert [n.title for n in found] == ["Two strokes, one power cycle", "Polynomials approximating cosine",
                                        "Wrapping a wave around a circle"]
    assert found[0].when == dt.datetime(2026, 11, 4, 0, 20)
    now = dt.datetime(2026, 11, 4, 9, 0)
    assert panel_mod.when_text(found[0].when, now) == "12:20 am"
    assert panel_mod.when_text(dt.datetime(2026, 11, 3, 15, 5), now) == "Yesterday"
    assert panel_mod.when_text(dt.datetime(2026, 10, 30, 15, 5), now) == "30 Oct"
    assert panel_mod.when_text(dt.datetime(2026, 11, 4, 13, 5), now) == "1:05 pm"


def test_a_missing_folder_is_no_notes(tmp_path):
    import panel as panel_mod
    assert panel_mod.load_notes(tmp_path / "nope") == []


def test_the_drawing_is_cut_from_the_saved_card(qapp, notes):
    import panel as panel_mod
    png = next(notes.glob("*.png"))
    drawing = panel_mod.drawing_of(png)
    assert drawing is not None and drawing.width() < 1012 and drawing.height() < 776 * 0.6, \
        "just the sketch: no band, title or follow-up buttons"


def test_it_grows_out_of_the_notch_and_settles_on_home(panel, notes):
    import panel as panel_mod
    panel.notes_folder = notes
    panel.open_on()
    assert panel.isVisible() and (panel._w, panel._h) == panel_mod.NOTCH, "starts as the notch"
    _settle(panel)
    assert (panel._w, panel._h) == panel_mod.HOME and panel._fade == 1.0
    assert panel.view == "home"
    stack = panel.home.stack
    assert stack.current.title == "Two strokes, one power cycle" and len(stack._backs()) == 2
    assert stack.open_btn.isVisible() and stack.ask_btn.isVisible()
    panel.grab()


def test_no_notes_yet_shows_the_paper_hint(panel):
    panel.open_on()
    _settle(panel)
    assert panel.home.stack.current is None
    assert not panel.home.stack.open_btn.isVisible()
    panel.grab()


def test_older_notes_come_forward(panel, notes):
    panel.notes_folder = notes
    panel.open_on()
    stack = panel.home.stack
    stack.show_index(1)
    assert stack.current.title == "Polynomials approximating cosine" and len(stack._backs()) == 1
    stack.show_index(9)
    assert stack.index == 1, "past the oldest: stays"


def test_a_typed_question_goes_to_the_app_and_the_panel_tucks_away(panel):
    from PyQt6.QtTest import QTest
    asked = []
    panel.asked.connect(asked.append)
    panel.open_on()
    _settle(panel)
    panel.home.ask.edit.setText("  what is a fourier transform  ")
    panel.home.ask.edit.returnPressed.emit()
    assert not panel.isVisible(), "gone before the screenshot"
    QTest.qWait(200)
    assert asked == ["what is a fourier transform"]
    panel.home.ask.edit.setText("   ")
    panel.home.ask.send.click()
    QTest.qWait(150)
    assert asked == ["what is a fourier transform"], "nothing to ask"


def test_a_follow_up_names_the_note(panel, notes):
    from PyQt6.QtTest import QTest
    asked = []
    panel.asked.connect(asked.append)
    panel.notes_folder = notes
    panel.open_on()
    panel.home.stack.ask_btn.click()
    assert "Two strokes" in panel.home.ask.edit.placeholderText()
    panel.home.ask.edit.setText("why does it smoke")
    panel.home.ask.edit.returnPressed.emit()
    QTest.qWait(200)
    assert asked == ['why does it smoke (about my sketch note "Two strokes, one power cycle")']


def test_a_click_outside_or_esc_closes_it(panel, monkeypatch):
    import panel as panel_mod
    from PyQt6.QtCore import QPoint
    panel.open_on()
    _settle(panel)
    down = {"vk": None}
    monkeypatch.setattr(panel_mod, "_key_down", lambda vk: vk == down["vk"])
    inside = panel.geometry().topLeft() + QPoint(panel.width() // 2, 200)
    monkeypatch.setattr(panel_mod.QCursor, "pos", staticmethod(lambda: inside))
    down["vk"] = 0x01
    panel._animate()
    assert panel.is_open, "a click inside keeps it"
    down["vk"] = None
    panel._animate()
    outside = panel.geometry().topLeft() + QPoint(panel.width() // 2, 700)
    monkeypatch.setattr(panel_mod.QCursor, "pos", staticmethod(lambda: outside))
    down["vk"] = 0x01
    panel._animate()
    assert not panel.is_open
    _settle(panel)
    assert not panel.isVisible()

    panel.open_on()
    down["vk"] = None
    panel._animate()
    down["vk"] = 0x1B
    panel._animate()
    assert not panel.is_open, "Esc"


def test_settings_and_folders(panel, monkeypatch):
    import panel as panel_mod
    opened, wanted = [], []
    monkeypatch.setattr(panel_mod, "_start", opened.append)
    monkeypatch.setattr(panel, "show_settings", lambda section="": False)  # the built-in ones can't open
    panel.settings_requested.connect(lambda: wanted.append(True))
    panel.open_on()
    panel.home.settings_btn.click()
    assert wanted == [True] and not panel.isVisible(), "then the Settings window, as before"
    panel.open_on()
    panel.home.all_notes.click()
    assert opened == [panel.notes_folder] and panel.notes_folder.is_dir()


def test_it_wears_the_buddys_colour(panel):
    import theme
    import panel as panel_mod
    for buddy in ("ember", "splash", "mochi"):
        theme.set_avatar(buddy, persist=False)
        assert panel_mod.accent() == theme.current_palette().flood
        shadow = panel.home.stack.open_btn._shadow.color()
        assert (shadow.red(), shadow.green(), shadow.blue()) == theme.current_palette().flood.getRgb()[:3]
        top, mid, bottom = panel_mod.gloss(panel_mod.accent())
        assert top.lightness() > mid.lightness() > bottom.lightness()
    theme.set_avatar(theme.DEFAULT_AVATAR, persist=False)


def test_a_running_guide_shows_the_working_view(panel):
    info = {"goal": "how do I project my screen", "done": ["Opened Settings"], "now": "Click Duplicate",
            "keys": "", "n": 2, "total": 3, "shortcut": ""}
    state = {"info": info}
    panel.guide_fn = lambda: state["info"]
    panel.open_on()
    _settle(panel)
    assert panel.view == "working"
    assert panel._h < 400, "shorter than home"
    kinds = [row[0] for row in panel.working._rows()]
    assert kinds == ["done", "now", "next"]
    assert panel.working.did_it.isVisible()
    panel.grab()
    state["info"] = None
    for _ in range(20):
        panel._animate()
    assert panel.view == "home", "the guide ended: back home"


def test_the_working_view_drives_the_real_guide(panel, monkeypatch):
    """I did it moves the guide on; Stop ends it. Neither shows what Clixy said."""
    import guide
    from snap import Element
    from types import SimpleNamespace

    class Watch:
        def start(self): pass
        def stop(self): pass
        def drain(self): pass
        def get(self, timeout):
            time.sleep(0.01)
            return None

    replies = iter(["open settings, i'll do it. [OPEN:ms-settings:display][STEP:1/3]",
                    "pick duplicate so both screens match. [CLICK:#1:duplicate][STEP:2/3]",
                    "now check the projector. [CLICK:#1:duplicate][STEP:3/3]"])
    capture = SimpleNamespace(monitor={"left": 0, "top": 0, "width": 1920, "height": 1080}, image=None,
                              label="", scale=1.0, width=1920, height=1080)
    said = []
    g = guide.Guide(ask=lambda _c, _t: next(replies), capture=lambda: capture,
                    list_elements=lambda _m: [Element("button", "Duplicate", (800, 300, 1000, 360))],
                    show=lambda *_a: None, clear=lambda: None,
                    say=lambda text, n, total, where: said.append(text), note=lambda _t: None,
                    cancelled=lambda: False, foreground=lambda: (1, "Settings"), watch=Watch(),
                    open=lambda target: "Settings", sleep=lambda _s: None)
    monkeypatch.setattr(guide, "element_lines", lambda found, cap: (['#1 button "Duplicate"'], {1: found[0]}))
    worker = threading.Thread(target=g.run, args=("how do I project my laptop", capture), daemon=True)
    worker.start()
    import panel as panel_mod

    def wait_for(pred):
        for _ in range(300):
            if pred():
                return True
            time.sleep(0.01)
        return False

    assert wait_for(lambda: (panel_mod.guide_progress() or {}).get("n") == 2)
    progress = panel_mod.guide_progress()
    assert progress["done"] == ["Opened Settings"] and progress["now"] == "Click Duplicate"
    assert progress["goal"] == "how do I project my laptop"
    assert not any(s in str(progress) for s in said), "never the spoken sentences"
    panel.guide_fn = panel_mod.guide_progress
    panel.open_on()
    assert panel.view == "working"
    panel.working.did_it.click()
    assert wait_for(lambda: (panel_mod.guide_progress() or {}).get("n") == 3)
    assert panel_mod.guide_progress()["done"] == ["Opened Settings", "Clicked Duplicate"]
    stopped = []
    panel.stopped.connect(lambda: stopped.append(True))
    panel.working.stop.click()
    worker.join(2)
    assert not worker.is_alive() and guide.current() is None and stopped == [True]


# --- The notch opens it --------------------------------------------------------------------------

def test_a_click_on_the_notch_opens_the_panel(qapp, monkeypatch, tmp_path):
    import island as island_mod
    import panel as panel_mod
    from PyQt6.QtCore import QPoint
    monkeypatch.setattr(panel_mod, "_key_down", lambda vk: False)
    notch = island_mod.SideIsland(side="top")
    try:
        g, shape = notch.geometry(), notch._shape_rect()
        on = QPoint(int(g.x() + shape.center().x()), int(g.y() + shape.center().y()))
        monkeypatch.setattr(island_mod.QCursor, "pos", staticmethod(lambda: on))
        button = {"down": False}
        monkeypatch.setattr(island_mod, "_left_down", lambda: button["down"])
        notch._watch_click()
        assert notch._catching, "the notch takes the click instead of the window below"
        assert notch.panel is None
        button["down"] = True
        notch._watch_click()
        assert notch.panel is not None and notch.panel.isVisible() and notch.panel.is_open
        notch.panel.notes_folder = tmp_path
        asked = []
        notch.on_ask = asked.append
        notch.panel.asked.emit("hello")
        assert asked == ["hello"]
        notch._watch_click()
        assert not notch._catching, "click-through again while the panel is open"
        notch.hide_for_capture()
        assert not notch.panel.isVisible()
        notch.show_after_capture()
        assert notch.panel.isVisible(), "back after the screenshot"
    finally:
        if notch.panel is not None:
            notch.panel._tick.stop()
            notch.panel.close()
        notch.close()


def test_holding_the_button_onto_the_notch_is_not_a_click(qapp, monkeypatch):
    import island as island_mod
    from PyQt6.QtCore import QPoint
    notch = island_mod.SideIsland(side="top")
    try:
        far = QPoint(5, 900)
        g, shape = notch.geometry(), notch._shape_rect()
        on = QPoint(int(g.x() + shape.center().x()), int(g.y() + shape.center().y()))
        where = {"p": far}
        monkeypatch.setattr(island_mod.QCursor, "pos", staticmethod(lambda: where["p"]))
        monkeypatch.setattr(island_mod, "_left_down", lambda: True)
        notch._watch_click()
        where["p"] = on
        notch._watch_click()
        assert notch.panel is None
    finally:
        notch.close()


def test_the_status_chip_follows_the_island(qapp):
    from island import SideIsland
    notch = SideIsland(side="top")
    try:
        assert notch._status_word() == "Ready"
        notch.listening()
        assert notch._status_word() == "Listening"
        notch.say("the answer is only heard")
        assert notch._status_word() == "Speaking"
        notch.step("pick duplicate.", 2, 3)
        assert notch._status_word() == "Guiding"
    finally:
        notch.close()


# --- Settings, inside the panel ------------------------------------------------------------------

ANT_KEY = "sk-ant-api03-" + "k" * 80 + "3f9a"
CARTESIA_KEY = "sk_car_" + "c" * 30


@pytest.fixture
def stored(monkeypatch):
    """Keyring as a dict (never the real one), with a Claude key and Cartesia."""
    values = {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ANT_KEY, "TTS_PROVIDER": "cartesia",
              "STT_PROVIDER": "faster-whisper", "CARTESIA_API_KEY": CARTESIA_KEY}
    for name in ("LLM_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                 "GEMINI_API_KEY", "CARTESIA_API_KEY", "MODEL_ID", "HOTKEY", "AVATAR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("keyring.get_password", lambda s, n: values.get(n))
    monkeypatch.setattr("keyring.set_password", lambda s, n, v: values.update({n: v}))
    yield values
    import theme
    theme.set_avatar(theme.DEFAULT_AVATAR, persist=False)


def _labels(widget) -> str:
    from PyQt6.QtWidgets import QLabel, QLineEdit
    texts = [w.text() for w in widget.findChildren(QLabel)]
    texts += [w.text() for w in widget.findChildren(QLineEdit) if w.echoMode() == QLineEdit.EchoMode.Normal]
    return " ".join(texts)


def test_the_gear_turns_the_panel_into_settings(panel, stored):
    from panel_settings import SECTIONS
    panel.open_on()
    _settle(panel)
    panel.home.settings_btn.click()
    assert panel.view == "settings" and panel.isVisible(), "Settings open in the panel, not a window"
    _settle(panel, 120)
    assert (panel._w, panel._h) == (880.0, float(panel.settings.height()))
    for key, _label in SECTIONS:
        panel.settings.show_section(key)
        panel.grab()
    shown = _labels(panel.settings)
    assert ANT_KEY not in shown and CARTESIA_KEY not in shown and "3f9a" not in shown, "a key is never shown"
    assert "Claude Haiku" in shown, "the model it really uses"


def test_done_saves_through_the_settings_logic(panel, stored):
    saved = []
    panel.settings_saved.connect(saved.append)
    panel.open_on()
    assert panel.show_settings("voice")
    page = panel.settings
    page._voice_cards["kokoro"].click()
    assert page.dlg.provider_id("TTS") == "kokoro"
    assert not page._voice_cards["voicestudio"].isEnabled(), "VoiceStudio: coming soon"
    page._listen.click()
    assert page.dlg.provider_id("STT") == "cartesia"
    page.show_section("general")
    page.pages["general"].findChildren(type(page._listen))[0].click()   # edge glow off
    page.done.click()
    assert saved == [True], "a new voice needs a restart"
    assert stored["TTS_PROVIDER"] == "kokoro" and stored["STT_PROVIDER"] == "cartesia"
    assert stored["EDGE_GLOW"] == "off"
    assert stored["ANTHROPIC_API_KEY"] == ANT_KEY, "keys kept as they were"
    assert panel.view == "home"


def test_a_missing_key_holds_done_back(panel, stored):
    del stored["CARTESIA_API_KEY"]
    panel.open_on()
    assert panel.show_settings("voice")
    page = panel.settings
    assert page._voice_key.isVisible() or page._voice_key.isVisibleTo(page.pages["voice"])
    page.done.click()
    assert panel.view == "settings" and page.hint.text(), "says why it can't save"
    assert "TTS_PROVIDER" in stored and "EDGE_GLOW" not in stored, "nothing written"
    page._voice_key._open()
    assert page._voice_key._entry.echoMode().name == "Password"
    page._voice_key._entry.setText(CARTESIA_KEY)
    page._voice_key._commit()
    assert page.dlg.key_state("TTS") == "set"
    page.done.click()
    assert stored["CARTESIA_API_KEY"] == CARTESIA_KEY and panel.view == "home"


def test_a_buddy_previews_live_and_back_puts_it_back(panel, stored):
    import theme
    import panel as panel_mod
    panel.open_on()
    assert panel.show_settings("buddy")
    panel.settings._buddy_cards["splash"].click()
    assert theme.current_avatar_id() == "splash" and panel_mod.accent() == theme.avatar("splash").palette.flood
    panel.settings.back.click()
    assert theme.current_avatar_id() == "ember" and panel.view == "home"
    assert "AVATAR" not in stored


def test_a_new_shortcut_is_captured_and_esc_belongs_to_it(panel, stored, monkeypatch):
    import panel as panel_mod
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    panel.open_on()
    assert panel.show_settings("shortcut")
    caps = panel.settings._caps
    caps.record()
    assert panel.settings.busy
    monkeypatch.setattr(panel_mod, "_key_down", lambda vk: vk == 0x1B)
    panel._was_esc = False
    panel._watch_input()
    assert panel.is_open, "Esc cancels the capture, not the panel"

    def key(kind, code):
        return QKeyEvent(kind, code, Qt.KeyboardModifier.NoModifier)

    capture = caps._caps
    capture.keyPressEvent(key(QEvent.Type.KeyPress, Qt.Key.Key_Alt))
    capture.keyPressEvent(key(QEvent.Type.KeyPress, Qt.Key.Key_K))
    capture.keyReleaseEvent(key(QEvent.Type.KeyRelease, Qt.Key.Key_K))
    capture.keyReleaseEvent(key(QEvent.Type.KeyRelease, Qt.Key.Key_Alt))
    assert not panel.settings.busy and panel.settings.dlg._hotkey == "alt+k"
    panel.settings.done.click()
    assert stored["HOTKEY"] == "alt+k"


def test_closing_in_settings_keeps_the_old_settings(panel, stored):
    panel.open_on()
    assert panel.show_settings("voice")
    panel.settings._voice_cards["kokoro"].click()
    panel.close_panel(instant=True)
    assert "EDGE_GLOW" not in stored and stored["TTS_PROVIDER"] == "cartesia"
    panel.open_on()
    assert panel.view == "home"
