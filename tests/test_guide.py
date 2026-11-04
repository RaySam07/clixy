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


# --- Taking any task from the first step to the end -------------------------------------------

@pytest.mark.parametrize("q", [
    "I want to access my YouTube studio on my account. Can you tell me exactly where I can access it?",
    "I want Hark and I want you to help me add a panel where I can add my calendar for the week",
    "where is the export button in photoshop",
    "open my downloads folder in file explorer",
    "can you help me set up a meeting in teams",
])
def test_demands_get_a_guide_too(q):
    assert guide.wants_guide(q)


@pytest.mark.parametrize("q", [
    "how does a jet engine work", "why does the polynomial drift away", "help me understand fourier transforms",
    "explain what is happening in the video", "show me a 3d model of the heart",
])
def test_learning_questions_still_dont(q):
    assert not guide.wants_guide(q)


@pytest.mark.parametrize("said, meaning", [
    ("", "keep"), ("next", "next"), ("done!", "next"), ("okay", "next"), ("what now", "next"),
    ("I can't find it", "remark"), ("i cant find it", "remark"), ("nothing happened", "remark"),
    ("it says access denied", "remark"), ("what is a fourier transform", "new"),
    ("how do I turn on dark mode", "new"),
])
def test_what_you_say_to_a_running_guide(said, meaning):
    assert guide.during_guide(said) == meaning


def test_a_plan_is_read_and_never_spoken():
    step = guide.parse_step('click your profile picture. [CLICK:#3:profile][STEP:1/3]'
                            '[PLAN:click "YouTube Studio": now pick youtube studio | press "ctrl+s": then save it]')
    assert step.say == "click your profile picture."
    assert [(p.action, p.name, p.say) for p in step.plan] == [
        ("click", "YouTube Studio", "now pick youtube studio"), ("press", "ctrl+s", "then save it")]


def test_the_next_planned_click_shows_at_once_without_asking_again():
    avatar = Element("button", "Account", (1200, 80, 1240, 120))
    studio = Element("menu item", "YouTube Studio", (1000, 300, 1240, 340))
    looks = [[avatar], [avatar, studio], [studio]]
    g, log = _guide(
        ['click your round profile picture at the top right. [CLICK:#1:profile picture][STEP:1/2]'
         '[PLAN:click "YouTube Studio": now pick youtube studio from the menu]',
         "you're in youtube studio. [DONE]"],
        [("click", 1220, 100), ("click", 1100, 320)],
    )
    g.list_elements = lambda _m: looks.pop(0) if len(looks) > 1 else looks[0]
    g.run("I want to get to my youtube studio", _capture(scale=1.0))
    assert len(log["prompts"]) == 2, "step two came from the plan, not a model call"
    assert log["said"][1][0] == "now pick youtube studio from the menu"
    assert log["shown"][1] == [Box(1000, 300, 1240, 340)]
    assert 'clicked "YouTube Studio"' in log["prompts"][1]


def test_a_plan_that_no_longer_fits_the_screen_asks_the_model():
    avatar = Element("button", "Account", (1200, 80, 1240, 120))
    g, log = _guide(
        ['click your profile picture. [CLICK:#1:profile picture][STEP:1/2][PLAN:click "YouTube Studio": now studio]',
         "the menu didn't open, click your profile picture again. [CLICK:#1:profile picture][STEP:1/2]",
         "there it is. [DONE]"],
        [("click", 1220, 100), ("click", 1220, 100)],
        elements=[avatar],
    )
    g.run("I want to get to my youtube studio", _capture(scale=1.0))
    assert len(log["prompts"]) == 3
    assert 'your plan for the rest (check the screen still fits it): click "YouTube Studio"' in log["prompts"][1]


def test_i_cant_find_it_looks_again_with_what_you_said():
    target = Element("button", "Settings", (10, 10, 60, 40))

    class Watch(FakeWatch):
        def get(self, timeout):
            if not self.said:          # you say it before clicking anything
                self.said = True
                g.hear("I can't find it")
                return None
            return super().get(timeout)

    watch = Watch([("click", 30, 20)])
    watch.said = False
    g, log = _guide(
        ["click settings at the top left. [CLICK:#1:settings][STEP:1/2]",
         "it's the little gear right at the top left corner. [CLICK:#1:settings][STEP:1/2]",
         "that's it. [DONE]"],
        [], elements=[target],
    )
    g.watch = watch
    g.run("help me open my settings", _capture(scale=1.0))
    assert 'the user said: "I can\'t find it"' in log["prompts"][1]
    assert [s[1] for s in log["said"]][:2] == [1, 1], "still step one"


# --- Do it for me (drive mode) ------------------------------------------------------------------

def _hands(log):
    return SimpleNamespace(
        click=lambda x, y: log.append(("click", x, y)) or True,
        press=lambda keys: log.append(("press", keys)) or True,
        type_text=lambda text: log.append(("type", text)) or True,
    )


def test_asked_to_do_it_clixy_does_each_step_itself():
    acted = []
    profile = Element("button", "Account", (1200, 80, 1240, 120))
    search = Element("field", "Search", (400, 20, 800, 50))
    g, log = _guide(
        ["opening your profile menu. [CLICK:#1:profile][STEP:1/3]",
         "searching for it. [TYPE:#2:youtube studio][STEP:2/3]",
         "and pressing enter. [KEYS:enter:search][STEP:3/3]",
         "you're in. [DONE]"],
        [], elements=[profile, search], hands=_hands(acted),
    )
    g.set_drive(True)
    g.run("open my youtube studio for me", _capture(scale=1.0))
    assert acted == [("click", 1220, 100), ("click", 600, 35), ("type", "youtube studio"), ("press", "enter")]
    assert "mode: you're doing it for them" in log["prompts"][0]
    assert log["shown"][0] == [Box(1200, 80, 1240, 120)], "it shows where it's about to click first"


def test_a_risky_click_waits_for_your_yes():
    acted = []
    send = Element("button", "Send", (900, 600, 980, 640))

    class Watch(FakeWatch):
        def get(self, timeout):
            if g.awaiting_yes and not acted:
                g.approve()                     # you said "yes"
            return super().get(timeout)

    g, log = _guide(["want me to send it? [CLICK:#1:send][STEP:2/2][DONE]"], [], elements=[send], hands=_hands(acted))
    g.watch = Watch([])
    g.set_drive(True)
    g.run("send this email for me", _capture(scale=1.0))
    assert acted == [("click", 940, 620)], "only after your yes"


def test_a_risky_click_you_make_yourself_is_never_clicked_again():
    acted = []
    delete = Element("button", "Delete", (900, 600, 980, 640))
    g, log = _guide(["want me to delete it? [CLICK:#1:delete][STEP:1/1][DONE]"], [("click", 940, 620)],
                    elements=[delete], hands=_hands(acted))
    g.set_drive(True)
    g.run("delete this file for me", _capture(scale=1.0))
    assert acted == []


def test_it_never_types_a_password():
    acted = []
    field_ = Element("field", "Password", (400, 300, 800, 340))
    g, log = _guide(["typing it in. [TYPE:#1:hunter2][STEP:1/1][DONE]"], [("combo", frozenset({"enter"}))],
                    elements=[field_], hands=_hands(acted))
    g.set_drive(True)
    g.run("log me in for me", _capture(scale=1.0))
    assert acted == []


def test_touching_the_mouse_hands_it_back_and_esc_stops_it():
    acted = []
    a = Element("button", "Settings", (10, 10, 60, 40))
    g, log = _guide(["opening settings. [CLICK:#1:settings][STEP:1/2]"], [("move", 500, 500), ("click", 30, 20)],
                    elements=[a], hands=_hands(acted))
    g.set_drive(True)
    g.max_steps = 1
    g.run("open settings for me", _capture(scale=1.0))
    assert acted == [] and not g.driving, "your move means you're taking over: it points instead"

    g2, log2 = _guide(["opening settings. [CLICK:#1:settings][STEP:1/2]", "never asked. [DONE]"],
                      [("combo", frozenset({"esc"}))], elements=[a], hands=_hands(acted))
    g2.set_drive(True)
    g2.run("open settings for me", _capture(scale=1.0))
    assert acted == [] and len(log2["prompts"]) == 1, "Esc stops it there"


def test_do_it_for_me_said_mid_guide_does_the_step_on_show():
    acted = []
    a = Element("button", "Settings", (10, 10, 60, 40))

    class Watch(FakeWatch):
        def get(self, timeout):
            if not self.asked:
                self.asked = True
                g.set_drive(True)
                g.do_this_one()
                return None
            return super().get(timeout)

    g, log = _guide(["click settings. [CLICK:#1:settings][STEP:1/1][DONE]"], [], elements=[a], hands=_hands(acted))
    g.watch = Watch([])
    g.watch.asked = False
    g.run("help me open settings", _capture(scale=1.0))
    assert acted == [("click", 35, 25)]


@pytest.mark.parametrize("label, keys, text, admin, why", [
    ("Send", "", "", False, "risky"), ("Place order", "", "", False, "risky"), ("Sign out", "", "", False, "risky"),
    ("Save", "", "", False, ""), ("Studio", "", "", True, "admin"), ("", "alt+f4", "", False, "risky"),
    ("", "win+i", "", False, ""), ("Card number", "", "4242", False, "secret"),
])
def test_what_is_always_theirs_to_do(label, keys, text, admin, why):
    action = "keys" if keys else ("type" if text else "click")
    step = guide.Step(say="", action=action, keys=keys, text=text, label=label)
    assert guide.theirs_to_do(step, label, admin) == why


# Said to Clixy on 4 Nov 2026, wanting it done: each must reach drive mode.
@pytest.mark.parametrize("said", [
    "For me, can you search out the top 5 best AI models on Chrome?",
    "For me, can you go on to Chrome and search up top 5 best open source AI models?",
    "Can you open up Chrome and search flights from Dubai to Calicut on 22nd of December?",
    "Yeah, so just go on to my account and search up flights from Dubai to Calicut on the 22nd of December",
    "Okay on this Chrome browser search up the flights from Dubai to Calicut",
    "can you look up the cheapest iphone on amazon",
    "google best pizza near me",
])
def test_said_the_way_people_say_it_a_job_gets_done(said):
    assert guide.wants_guide(said) and guide.wants_drive(said)


@pytest.mark.parametrize("said", [
    "Can you show me how a piston works now?", "Can you show me in 3D how a piston works?",
    "Hey, Quick-See, can you show me how an airplane works on 3D?", "is this good for me",
    "what does this mean for me", "can you dissect a heart and show me what exactly is the aorta",
])
def test_learning_and_questions_are_not_jobs(said):
    assert not guide.wants_guide(said) and not guide.wants_drive(said)


def test_a_new_search_while_guiding_starts_over_but_for_me_takes_the_wheel():
    assert guide.during_guide("search up the weather") == "new"
    assert guide.during_guide("for me") == "drive"


def test_the_guide_voice_starts_on_the_first_phrase_too():
    heard = []
    early = guide._EarlySpeech(heard.append, lambda: 1.0)
    early.feed("click your round profile picture at the top right so the menu opens. then ")
    early.feed("pick studio. [CLICK:#3:profile]")
    assert heard == ["click your round profile picture,", "at the top right so the menu opens.", "then pick studio."]
