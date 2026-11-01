"""Guide mode: one exact step at a time, moving on as soon as you've done it."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import guide
from annotations import Box, Keys, key_names, parse_annotations
from snap import Element, keep_element


# --- Which questions get a guide ------------------------------------------------

@pytest.mark.parametrize("q", [
    "how do I project my laptop to the projector",
    "how can I turn on dark mode",
    "take me to display settings",
    "walk me through connecting my headphones",
    "show me how to take a screenshot",
    "where do I change my wallpaper",
    "help me connect to wifi",
    "I want to project my screen",
])
def test_how_tos_get_a_guide(q):
    assert guide.wants_guide(q)


@pytest.mark.parametrize("q", ["what is a binary tree", "why is the sky blue", "summarise this page", ""])
def test_other_questions_dont(q):
    assert not guide.wants_guide(q)


# --- Reading a step ------------------------------------------------------------

def test_a_click_step():
    step = guide.parse_step("pick duplicate so both screens match. [CLICK:#4:duplicate][STEP:2/2]")
    assert (step.action, step.target, step.label, step.n, step.total) == ("click", 4, "duplicate", 2, 2)
    assert step.say == "pick duplicate so both screens match."
    assert not step.done


def test_a_keys_step_and_its_keycaps():
    step = guide.parse_step("press the windows key and p together. [KEYS:win+p:project menu][STEP:1/2]")
    assert (step.action, step.keys, step.label) == ("keys", "win+p", "project menu")
    assert step.say == "press the windows key and p together."
    assert key_names(step.keys) == ["Win", "P"]
    assert guide.combo_tokens("ctrl+shift+esc") == {"ctrl", "shift", "esc"}


def test_done_and_a_box_fallback_and_a_cut_off_tag():
    assert guide.parse_step("that's it, you're projecting. [DONE]").done
    step = guide.parse_step("click the gear. [BOX:10,20,40,50:gear][STEP:1/3]")
    assert step.action == "box" and step.box == Box(10, 20, 40, 50, "gear")
    step = guide.parse_step("open settings. [CLICK:#7:sett")
    assert step.say == "open settings." and step.action == "none"


def test_no_tag_is_ever_read_aloud():
    step = guide.parse_step("open settings. [POINT:721,215:settings] [CLICK:#2:settings][STEP:1/2]")
    assert step.say == "open settings." and step.target == 2


def test_type_step():
    step = guide.parse_step('type the name. [TYPE:#3:Projector][STEP:2/3]')
    assert (step.action, step.target, step.text) == ("type", 3, "Projector")


# --- Keys on screen ---------------------------------------------------------------

def test_keys_tag_is_drawn_never_spoken():
    spoken, shapes = parse_annotations("press control shift escape. [KEYS:ctrl+shift+esc:task manager]")
    assert spoken == "press control shift escape."
    assert shapes == [Keys("ctrl+shift+esc", "task manager")]
    assert shapes[0].caps == ["Ctrl", "Shift", "Esc"]


def test_keys_become_a_keycaps_mark(qapp):
    import drawing
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    area = QRectF(0, 0, 1280, 800)
    marks = drawing.build_marks([Keys("win+p", "project menu"), Box(100, 100, 160, 130, "x")], area)
    keys = [m for m in marks if m.kind == "keys"]
    assert len(keys) == 1 and keys[0].keys == ["Win", "P"]
    assert abs(keys[0].pill.center().x() - area.center().x()) < 1
    img = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    drawing.paint_marks(p, marks, t=10.0)
    p.end()


# --- The element list ----------------------------------------------------------------

def _capture(w=1280, h=800, scale=1.5, left=0, top=0):
    return SimpleNamespace(monitor={"left": left, "top": top, "width": int(w * scale), "height": int(h * scale)},
                           scale_x=scale, scale_y=scale, target_width=w, target_height=h,
                           image=None, label="screen 1", is_cursor_screen=True)


def test_elements_are_numbered_in_screenshot_pixels():
    cap = _capture(scale=1.5, left=1920)
    els = [Element("button", 'Say "hi"', (1920 + 150, 300, 1920 + 300, 360)),
           Element("button", "Off screen", (0, 0, 100, 100))]
    lines, ids = guide.element_lines(els, cap)
    assert lines == ["#1 button \"Say 'hi'\" (100,200,200,240)"]
    assert ids[1] is els[0]


def test_only_useful_elements_are_listed():
    mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    assert keep_element(50000, "Duplicate", (10, 10, 120, 40), mon) == Element("button", "Duplicate", (10, 10, 120, 40))
    assert keep_element(50020, "Some text", (10, 10, 120, 40), mon) is None       # plain text isn't clickable
    assert keep_element(50000, "", (10, 10, 120, 40), mon) is None                # unnamed button
    assert keep_element(50004, "", (10, 10, 120, 40), mon).kind == "field"         # unnamed field is fine
    assert keep_element(50000, "Tiny", (10, 10, 12, 12), mon) is None
    assert keep_element(50000, "Elsewhere", (2000, 10, 2100, 40), mon) is None


def test_no_element_list_off_windows():
    import snap
    assert snap.elements({"left": 0, "top": 0, "width": 10, "height": 10}) == [] or snap.sys.platform == "win32"


# --- The guide, start to finish ----------------------------------------------------------

class FakeWatch:
    def __init__(self, events):
        self.events = list(events)

    def start(self):
        pass

    def stop(self):
        pass

    def drain(self):
        pass

    def get(self, timeout):
        return self.events.pop(0) if self.events else None


def _guide(replies, events, *, elements=None, windows=None, **kw):
    log = {"prompts": [], "said": [], "shown": [], "notes": [], "cleared": 0}
    replies = list(replies)
    windows = list(windows or [])
    clock = [0.0]

    def ask(_cap, text):
        log["prompts"].append(text)
        return replies.pop(0)

    def tick():
        clock[0] += 0.25
        return clock[0]

    g = guide.Guide(
        ask=ask,
        capture=lambda: _capture(scale=1.0),
        list_elements=lambda _m: list(elements or []),
        show=lambda marks, _m: log["shown"].append(marks),
        clear=lambda: log.__setitem__("cleared", log["cleared"] + 1),
        say=lambda text, n, total, where: log["said"].append((text, n, total, where)),
        note=log["notes"].append,
        cancelled=kw.pop("cancelled", lambda: False),
        foreground=lambda: windows.pop(0) if len(windows) > 1 else (windows[0] if windows else (1, "Desktop")),
        watch=FakeWatch(events),
        about="windows 11 home, laptop, 1 screen: 1920x1080",
        clock=tick,
        sleep=lambda _s: None,
        **kw,
    )
    return g, log


def test_project_my_screen_in_two_steps_then_done():
    duplicate = Element("button", "Duplicate", (800, 300, 1000, 360))
    g, log = _guide(
        ["press the windows key and p together. [KEYS:win+p:project menu][STEP:1/2]",
         "pick duplicate so the projector shows your screen. [CLICK:#1:duplicate][STEP:2/2]",
         "that's it, you're projecting. [DONE]"],
        [("combo", frozenset({"win", "p"})), ("click", 900, 330)],
        elements=[duplicate],
    )
    last = g.run("how do I project my laptop to the projector", _capture(scale=1.0))
    assert last == "that's it, you're projecting."
    assert [s[1] for s in log["said"]] == [1, 2, 3]
    assert log["shown"][0] == [Keys("win+p", "project menu")]
    assert log["shown"][1] == [Box(800, 300, 1000, 360)]  # Windows' exact rectangle
    assert log["said"][1][3][0] == (800, 300, 1000, 360), "the island is told what to keep clear of"
    assert log["said"][0][3] is None
    assert "this computer: windows 11" in log["prompts"][0]
    assert "pressed win + p" in log["prompts"][1] and 'clicked "Duplicate"' in log["prompts"][2]
    assert '#1 button "Duplicate" (800,300,1000,360)' in log["prompts"][1]


def test_a_click_outside_the_target_doesnt_count_but_a_new_window_does():
    target = Element("list item", "Personalization", (10, 200, 200, 240))
    g, log = _guide(
        ["open personalization. [CLICK:#1:personalization][STEP:1/3]", "done here. [DONE]"],
        [("click", 900, 900)],
        elements=[target],
        windows=[(1, "Settings"), (1, "Settings"), (2, "Settings > Personalization")],
    )
    g.run("turn on dark mode", _capture(scale=1.0))
    assert len(log["prompts"]) == 2, "the window changed, so the step was done"


def test_a_made_up_id_gets_one_retry():
    g, log = _guide(
        ["click it. [CLICK:#9:thing][STEP:1/1]", "click display. [CLICK:#1:display][STEP:1/1]", "done. [DONE]"],
        [("click", 50, 50)],
        elements=[Element("button", "Display", (10, 10, 100, 100))],
    )
    g.run("take me to display settings", _capture(scale=1.0))
    assert "#9 isn't in the list" in log["prompts"][1]
    assert log["shown"][0] == [Box(10, 10, 100, 100)]


def test_a_general_question_is_answered_fully_without_marks():
    g, log = _guide(["projectors work by shining light through a small image and a lens. [DONE]"], [])
    last = g.run("how do projectors work", _capture(scale=1.0))
    assert last.startswith("projectors work") and log["shown"] == []


def test_a_new_question_stops_the_guide():
    stop = [False]
    g, log = _guide(["press win and p. [KEYS:win+p:project menu][STEP:1/2]"], [],
                    cancelled=lambda: stop[0])
    original = g.sleep
    g.watch.get = lambda _t: stop.__setitem__(0, True)  # you hold the keys and ask something else
    g.run("project my screen", _capture(scale=1.0))
    assert len(log["prompts"]) == 1


def test_it_nudges_then_gives_up_when_nothing_happens():
    g, log = _guide(["press win and p. [KEYS:win+p:project menu][STEP:1/2]"], [], nudge_s=2.0, give_up_s=4.0)
    g.run("project my screen", _capture(scale=1.0))
    assert log["notes"] and "Still with me" in log["notes"][0]
    assert len(log["prompts"]) == 1


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_island_shows_one_step_at_a_time(qapp):
    from island import SideIsland
    isl = SideIsland()
    try:
        isl.say("old answer.")
        isl.step("pick duplicate.", 2, 3)
        assert isl._sentences == ["pick duplicate."] and isl._meta == "step 2 of 3"
        isl.grab()
    finally:
        isl.close()


def test_the_island_moves_off_the_thing_to_click(qapp):
    from PyQt6.QtCore import QRect
    from island import SideIsland
    isl = SideIsland(side="right")
    try:
        isl.step("pick duplicate.", 2, 2, keep_clear=isl.geometry().adjusted(40, 40, -40, -40))
        assert isl.side == "left" and isl.home_side == "right"
        isl.step("next.", 3, 3, keep_clear=QRect(-5000, -5000, 10, 10))
        assert isl.side == "right"
        isl.step("again.", 3, 3, keep_clear=isl.geometry())
        isl._collapse()
        assert isl.side == "right", "home again once the guide is over"
    finally:
        isl.close()


def test_the_pipeline_sends_how_tos_to_the_guide(mocker):
    import queue as _queue
    import threading
    from PIL import Image
    from app import ClickyApp
    app = ClickyApp(ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(), tts_client=mocker.MagicMock(),
                    memory_store=mocker.MagicMock(), overlay_controller=mocker.MagicMock(),
                    hotkey_instance=mocker.MagicMock())
    run = mocker.patch.object(ClickyApp, "_run_guide")
    cap = mocker.MagicMock()
    cap.image = Image.new("RGB", (1280, 800))
    cap.label, cap.scale_x, cap.scale_y = "screen 1 of 1", 1.0, 1.0
    cap.monitor = {"left": 0, "top": 0, "width": 1280, "height": 800}
    cap.target_width, cap.target_height, cap.is_cursor_screen = 1280, 800, True
    app._press_captures = [cap]
    app._stt.stop_recording.return_value = "how do I project my laptop to the projector"
    q: _queue.Queue = _queue.Queue(maxsize=1)
    q.put(([cap], "", "test"))
    app._pipeline_worker("TEST.EXE", "Win", threading.Event(), q)
    run.assert_called_once()
    app._ai.ask_stream.assert_not_called()


def test_this_computer_is_described():
    import sysinfo
    assert sysinfo.profile()
