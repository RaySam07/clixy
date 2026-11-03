"""Guide mode, faster and smarter: speaking as the reply streams, looking
again once the screen has settled, knowing the open windows ([FOCUS], two
things side by side), know-how packs, and remembered routes. All with fakes:
no model calls, nothing on the real screen moves."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import apps
import guide
import kb
from annotations import Box
from memory import RouteBook
from snap import Element, Window, halves


# --- A fake desk: a clock, windows that open (sometimes behind), a watch ----------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += max(s, 0.0)


class Watch:
    def __init__(self, clock, events=()):
        self.clock, self.events = clock, list(events)

    def start(self):
        pass

    def stop(self):
        pass

    def drain(self):
        pass

    def get(self, timeout):
        if self.events and self.events[0][0] <= self.clock.now:
            return self.events.pop(0)[1]
        self.clock.sleep(timeout)
        return None


class Desk:
    """Windows as Windows would list them. ``open_target`` makes an app's
    window appear after ``delay`` seconds, in front or behind."""

    def __init__(self, clock, wins=()):
        self.clock = clock
        self.wins = list(wins)          # front first
        self.pending = []               # (at, Window, front?)
        self.focused, self.arranged, self.opened = [], [], []
        self._next = 100

    def _due(self):
        for item in [p for p in self.pending if p[0] <= self.clock.now]:
            self.pending.remove(item)
            _at, win, front = item
            if front:
                self.wins = [Window(**{**w.__dict__, "front": False}) for w in self.wins]
                self.wins.insert(0, win)
            else:
                self.wins.insert(1, win)

    def windows(self):
        self._due()
        return list(self.wins)

    def foreground(self):
        self._due()
        top = next((w for w in self.wins if w.front), None)
        return (top.hwnd, top.title) if top else (0, "")

    def focus(self, hwnd):
        self.focused.append(hwnd)
        win = next(w for w in self.wins if w.hwnd == hwnd)
        rest = [Window(**{**w.__dict__, "front": False}) for w in self.wins if w.hwnd != hwnd]
        self.wins = [Window(**{**win.__dict__, "front": True, "minimized": False})] + rest
        return True

    def will_open(self, target, title, exe, app, delay, front=True, admin=False):
        self._next += 1
        self.pending.append((self.clock.now + delay, Window(self._next, title, exe, app, front=front,
                                                            admin=admin), front))

    def arrange(self, a, b, _monitor):
        self.arranged.append((a, b))
        names = {w.hwnd: w.app for w in self.wins}
        return f"{names[a]} on the left, {names[b]} on the right"


def _cap(w=1366, h=768):
    return SimpleNamespace(monitor={"left": 0, "top": 0, "width": w, "height": h}, scale_x=1.0, scale_y=1.0,
                           target_width=w, target_height=h, image=None, label="screen 1", is_cursor_screen=True)


def _chrome(front=True):
    return Window(1, "WhatsApp - Google Chrome", "chrome.exe", "chrome", front=front)


def _guide(replies, clock, desk, *, events=(), elements=(), opens=None, **kw):
    """A guide on the fake desk. ``replies`` are lists of chunks (each
    advancing the clock by the gap given) or plain strings."""
    log = {"prompts": [], "said": [], "spoken": [], "shown": [], "events": [], "notes": []}
    replies = list(replies)
    opens = opens or {}

    def ask(_cap, text):
        log["prompts"].append(text)
        reply = replies.pop(0)
        if isinstance(reply, str):
            return reply

        def stream():
            for gap, chunk in reply:
                clock.sleep(gap)
                log["events"].append(("chunk", round(clock.now, 2), chunk))
                yield chunk
        return stream()

    def open_target(target):
        desk.opened.append(target)
        if target in opens:
            desk.will_open(target, **opens[target])
        return {"taskmgr": "Task Manager", "ms-settings:batterysaver": "Settings"}.get(target, target)

    def speak(sentence):
        log["spoken"].append(sentence)
        log["events"].append(("speak", round(clock.now, 2), sentence))

    g = guide.Guide(
        ask=ask, capture=_cap, list_elements=lambda _m: list(elements),
        show=lambda marks, _m: log["shown"].append(marks), clear=lambda: None,
        say=lambda text, n, total, where: log["said"].append((text, n, total, where)),
        note=log["notes"].append, cancelled=lambda: False, foreground=desk.foreground,
        watch=Watch(clock, events), about="windows 11 home, laptop", open=open_target,
        speak=speak, windows=desk.windows, focus=desk.focus, arrange=desk.arrange,
        clock=clock, sleep=clock.sleep, **kw)
    return g, log


# --- 1. Speak sooner --------------------------------------------------------------------

def test_the_sentence_is_spoken_before_the_tags_arrive():
    clock = Clock()
    desk = Desk(clock, [_chrome()])
    g, log = _guide([[(1.5, "press control, shift and escape together, "), (0.2, "that opens task manager. "),
                      (0.6, "[KEYS:ctrl+shift+esc:task manager]"), (0.1, "[STEP:1/2]")],
                     "there it is. [DONE]"], clock, desk,
                    events=[(3.0, ("combo", frozenset({"ctrl", "shift", "esc"})))])
    g.run("open task manager", _cap())
    events = log["events"]
    spoke = next(i for i, e in enumerate(events) if e[0] == "speak")
    tag = next(i for i, e in enumerate(events) if e[0] == "chunk" and "[KEYS" in e[2])
    assert spoke < tag, "spoken while the tags were still coming"
    assert events[spoke][1] == pytest.approx(1.7), "at 1.7 s, not at the end of the reply (2.4 s)"
    assert log["spoken"][0] == "press control, shift and escape together, that opens task manager."
    assert log["said"][0][:3] == ("press control, shift and escape together, that opens task manager.", 1, 2), \
        "the island and panel still get the step once, whole"
    assert log["spoken"].count(log["spoken"][0]) == 1, "never said twice"


def test_what_comes_after_the_tags_is_spoken_once_the_reply_is_read():
    clock = Clock()
    g, log = _guide([[(0.5, "that's it, your battery's on screen"), (0.2, " [DONE] faster next time, "),
                      (0.1, "press windows and i. [FASTER:win+i]")]], clock, Desk(clock, [_chrome()]))
    g.run("show my battery", _cap())
    assert log["spoken"] == ["that's it, your battery's on screen faster next time, press windows and i."]
    assert g.progress()["shortcut"] == "win+i", "the panel can show the quick way"


def test_without_a_voice_hook_say_speaks_as_before():
    clock = Clock()
    desk = Desk(clock, [_chrome()])
    g, log = _guide(["all set. [DONE]"], clock, desk)
    g.speak = None
    g.run("x", _cap())
    assert log["spoken"] == [] and log["said"][0][0] == "all set."


def test_a_retry_after_a_made_up_id_doesnt_speak_twice():
    clock = Clock()
    g, log = _guide([[(0.3, "click display. "), (0.1, "[CLICK:#9:display]")],
                     [(0.3, "click display settings. "), (0.1, "[CLICK:#1:display][STEP:1/1]")],
                     "done. [DONE]"], clock, Desk(clock, [_chrome()]),
                    events=[(2.0, ("click", 50, 50))], elements=[Element("button", "Display", (10, 10, 100, 100))])
    g.run("display settings", _cap())
    assert log["spoken"][:1] == ["click display."] and "click display settings." not in log["spoken"]


# --- 2. Look when the screen has settled ---------------------------------------------------

def test_a_click_inside_the_same_window_looks_again_quickly():
    clock = Clock()
    target = Element("tab item", "Performance", (10, 200, 200, 240))
    g, log = _guide(["click performance. [CLICK:#1:performance][STEP:1/2]", "there. [DONE]"], clock,
                    Desk(clock, [_chrome()]), events=[(1.0, ("click", 50, 210))], elements=[target])
    g.run("cpu", _cap())
    # The click at 1.0 s; next ask once nothing changed for settle_s (0.45 s), not 0.7 + 0.7 s.
    assert len(log["prompts"]) == 2
    assert 1.4 <= clock.now <= 1.7


def test_an_app_that_opens_behind_is_brought_forward_then_looked_at():
    clock = Clock()
    desk = Desk(clock, [_chrome()])
    g, log = _guide(["opening task manager for you. [OPEN:taskmgr][STEP:1/2]", "there. [DONE]"], clock, desk,
                    opens={"taskmgr": dict(title="Task Manager", exe="Taskmgr.exe", app="task manager",
                                           delay=0.9, front=False, admin=True)})
    g.run("open task manager", _cap())
    tm = next(w for w in desk.windows() if w.app == "task manager")
    assert desk.focused == [tm.hwnd], "it opened behind chrome, so clixy brought it forward"
    assert tm.front
    assert "clixy opened Task Manager (task manager, in front)" in log["prompts"][1]
    assert clock.now < 2.0, "looked again as soon as it was up (the old fixed wait was 1.6 s after opening)"


def test_settling_never_waits_past_the_cap():
    clock = Clock()

    class Flicker(Desk):
        def foreground(self):
            return (1, f"loading {clock.now:.2f}")   # the title never holds still

    g, _log = _guide([], clock, Flicker(clock, [_chrome()]))
    start = clock.now
    g._settle((1, "before"), grace=0.9)
    assert clock.now - start <= g.settle_cap_s + 0.1


def test_settled_once_the_front_window_changed_and_held_still():
    clock = Clock()
    desk = Desk(clock, [_chrome()])
    desk.will_open("x", "Settings", "SystemSettings.exe", "settings", delay=0.2)
    g, _log = _guide([], clock, desk)
    g._settle((1, "WhatsApp - Google Chrome"), grace=0.9)
    assert 0.5 <= clock.now <= 0.7, "changed at 0.2 s, still for 0.3 s"


# --- 3. Know what's open -------------------------------------------------------------------

def test_open_windows_are_listed_front_first_with_what_matters():
    wins = [Window(1, "WhatsApp - Google Chrome", "chrome.exe", "chrome", front=True),
            Window(2, "Task Manager", "Taskmgr.exe", "task manager", admin=True),
            Window(3, "Spotify Premium", "Spotify.exe", "spotify", minimized=True)]
    lines = guide.window_lines(wins)
    assert lines[0] == '1. chrome "WhatsApp - Google Chrome" (in front)'
    assert "admin" in lines[1] and "minimised" in lines[2]
    clock = Clock()
    g, log = _guide(["ok. [DONE]"], clock, Desk(clock, wins))
    g.run("x", _cap())
    assert "open windows, front first:\n1. chrome" in log["prompts"][0]


def test_focus_brings_an_open_window_forward():
    step = guide.parse_step("spotify's already open, i'll bring it up. [FOCUS:spotify][STEP:1/1]")
    assert (step.action, step.text, step.say) == ("focus", "spotify", "spotify's already open, i'll bring it up.")
    clock = Clock()
    desk = Desk(clock, [_chrome(), Window(3, "Spotify Premium", "Spotify.exe", "spotify", minimized=True)])
    g, log = _guide(["spotify's already open, i'll bring it up. [FOCUS:spotify][STEP:1/1]", "there. [DONE]"],
                    clock, desk)
    g.run("open spotify", _cap())
    assert desk.focused == [3] and "clixy brought spotify to the front" in log["prompts"][1]
    assert desk.opened == [], "never opened again"


def test_focus_on_a_window_that_isnt_open_says_so():
    clock = Clock()
    g, log = _guide(["bringing up excel. [FOCUS:excel]", "opening it. [DONE]"], clock, Desk(clock, [_chrome()]))
    g.run("excel", _cap())
    assert 'no open window is called "excel"' in log["prompts"][1]


def test_two_things_open_side_by_side():
    step = guide.parse_step("both side by side. [OPEN:taskmgr][OPEN:ms-settings:batterysaver][STEP:1/2]")
    assert step.opens == ["taskmgr", "ms-settings:batterysaver"] and step.action == "open"
    assert guide.step_label(step, {}) == "Opening taskmgr and Settings"
    clock = Clock()
    desk = Desk(clock, [_chrome()])
    g, log = _guide(["both side by side. [OPEN:taskmgr][OPEN:ms-settings:batterysaver][STEP:1/2]", "there. [DONE]"],
                    clock, desk,
                    opens={"taskmgr": dict(title="Task Manager", exe="Taskmgr.exe", app="task manager", delay=0.8),
                           "ms-settings:batterysaver": dict(title="Settings", exe="SystemSettings.exe",
                                                            app="settings", delay=0.4)})
    g.run("battery and cpu", _cap())
    tm = next(w for w in desk.windows() if w.app == "task manager")
    st = next(w for w in desk.windows() if w.app == "settings")
    assert desk.arranged == [(tm.hwnd, st.hwnd)]
    assert "side by side: task manager on the left, settings on the right" in log["prompts"][1]


def test_window_names_match_how_people_say_them():
    tm = Window(2, "Task Manager", "Taskmgr.exe", "task manager")
    st = Window(3, "Settings", "SystemSettings.exe", "settings")
    assert tm.matches("task manager") and tm.matches("taskmgr") and not tm.matches("settings")
    assert st.matches("settings") and st.matches("windows settings")
    assert halves((0, 0, 1920, 1040)) == ((0, 0, 960, 1040), (960, 0, 1920, 1040))


def test_the_real_window_list_reads_without_touching_anything():
    import snap
    wins = snap.windows()
    assert isinstance(wins, list)
    assert all(isinstance(w, Window) and w.title for w in wins)
    assert sum(w.front for w in wins) <= 1


# --- 4. App know-how ---------------------------------------------------------------------------

def test_every_pack_reads_and_stays_small():
    packs = kb.load_packs()
    for name in ("windows", "taskmgr", "explorer", "settings", "chrome", "edge", "whatsapp", "teams", "outlook",
                 "word", "excel", "powerpoint", "vscode", "spotify", "snipping"):
        assert name in packs, name
        p = packs[name]
        assert p.name and p.sections and (p.apps or name == "windows")
        cap = 5000 if name == "windows" else 4000   # windows.md is always cut down to the goal's bullets
        assert sum(len(b) for _h, bs in p.sections for b in bs) < cap, f"{name} is too long to stay lean"


def test_every_link_in_the_packs_is_one_clixy_can_open():
    import re
    known = set(apps.SETTINGS_PAGES.values()) | set(apps.MORE_SETTINGS_PAGES.values())
    for name, p in kb.load_packs().items():
        text = "\n".join([*p.head, *(b for _h, bs in p.sections for b in bs)])
        for target in re.findall(r"\[OPEN:([^\]]+)\]", text):
            if "..." in target:
                continue   # a placeholder ("[OPEN:ms-settings:...]")
            kind = apps.kind_of(target)
            if kind == "settings":
                assert target in known, f"{name}: {target} isn't a page clixy knows"
            else:
                assert kind in ("command", "report", "uri", "web", "app"), f"{name}: {target}"


def test_the_right_packs_for_the_goal():
    assert [p.id for p in kb.pick_packs("Help me open up my battery performance and CPU load.", "chrome.exe")] \
        == ["taskmgr", "settings"]
    assert [p.id for p in kb.pick_packs("how do I clear my cookies", "chrome.exe")] == ["chrome"], \
        "the browser in front wins, and only one browser"
    assert [p.id for p in kb.pick_packs("how do I do this", "WINWORD.EXE")] == ["word"], "else the app in front"
    assert kb.pick_packs("how do I do this", "unknown.exe") == []


def test_know_how_is_trimmed_to_the_goal(tmp_path):
    text = kb.knowhow("Help me open up my battery performance and CPU load.", "chrome.exe", kb_dir=tmp_path)
    assert "[task manager]" in text and "[settings]" in text and "battery report" in text
    assert "ms-settings:batterysaver" in text and "performance: live graphs for cpu" in text
    assert "chrome://" not in text and "freeze panes" not in text
    assert len(text) <= 2700
    small = kb.knowhow("how do i freeze the top row", "EXCEL.EXE", kb_dir=tmp_path)
    assert "freeze panes" in small and len(small) < 900


def test_the_users_notes_go_first_and_can_replace_a_pack(tmp_path):
    (tmp_path / "taskmgr.exe.md").write_text("- on this pc task manager always opens on performance.\n",
                                             encoding="utf-8")
    text = kb.knowhow("show my cpu load", "chrome.exe", kb_dir=tmp_path)
    assert text.index("your notes on task manager") < text.index("[task manager]")
    (tmp_path / "taskmgr.exe.md").write_text("---\nreplace: yes\n---\n- use process explorer instead.\n",
                                             encoding="utf-8")
    text = kb.knowhow("show my cpu load", "chrome.exe", kb_dir=tmp_path)
    assert "process explorer" in text and "[task manager]" not in text


def test_notes_for_an_app_with_no_pack(tmp_path):
    (tmp_path / "edupack.exe.md").write_text("## exporting\n- file > export > csv.\n", encoding="utf-8")
    assert "file > export > csv" in kb.knowhow("how do i export", "EduPack.exe", kb_dir=tmp_path)


def test_the_guide_gets_know_how_for_the_front_app_and_the_goal():
    clock = Clock()
    asked = []
    g, log = _guide(["ok. [DONE]"], clock, Desk(clock, [_chrome()]),
                    knowhow=lambda goal, exe: asked.append((goal, exe)) or "[task manager]\n- ctrl+shift+esc")
    g.run("cpu load", _cap())
    assert asked == [("cpu load", "chrome.exe")]
    assert "know-how (accurate; use the fastest route that fits):\n[task manager]" in log["prompts"][0]


# --- 5. Remember routes ------------------------------------------------------------------------

def test_routes_are_kept_and_found_for_a_similar_goal(tmp_path):
    book = RouteBook(tmp_path / "routes.json")
    book.record("help me open my battery performance and cpu load",
                ["open taskmgr + ms-settings:batterysaver side by side", "click performance"], app="task manager")
    found = book.recall("show me battery and cpu load")
    assert found and found["steps"][0].startswith("open taskmgr")
    assert book.recall("how do i change my wallpaper") is None
    book.record("open my battery and cpu load", ["open taskmgr"], shortcut="ctrl+shift+esc")
    rows = json.loads((tmp_path / "routes.json").read_text(encoding="utf-8"))
    assert len(rows) == 2 or rows[-1]["uses"] >= 1
    for i in range(100):
        book.record(f"goal number {i} about thing{i}", ["press win+i"])
    assert len(json.loads((tmp_path / "routes.json").read_text(encoding="utf-8"))) == book.limit


def test_a_finished_guide_remembers_its_route_and_the_next_one_uses_it(tmp_path):
    book = RouteBook(tmp_path / "routes.json")
    clock = Clock()
    desk = Desk(clock, [_chrome()])
    g, _log = _guide(["press windows and p. [KEYS:win+p:project][STEP:1/2]",
                      "pick duplicate. [CLICK:#1:duplicate][STEP:2/2]",
                      "you're projecting. [DONE]"], clock, desk, routes=book,
                     events=[(1.0, ("combo", frozenset({"win", "p"}))), (3.0, ("click", 900, 330))],
                     elements=[Element("button", "Duplicate", (800, 300, 1000, 360))])
    g.run("project my screen to the projector", _cap(1920, 1080))
    route = book.recall("project my screen")
    assert route["steps"] == ["press win+p", 'click "Duplicate"']
    g2, log2 = _guide(["press windows and p. [KEYS:win+p:project][STEP:1/2]"], clock, desk, routes=book)
    g2.give_up_s = 1.0
    g2.run("how do i project my screen", _cap())
    assert 'last time, "project my screen to the projector" worked like this: press win+p; click "Duplicate"' \
        in log2["prompts"][0]


def test_a_cancelled_guide_remembers_nothing(tmp_path):
    book = RouteBook(tmp_path / "routes.json")
    clock = Clock()
    g, _log = _guide(["press windows and p. [KEYS:win+p:project][STEP:1/2]"], clock, Desk(clock, [_chrome()]),
                     routes=book, give_up_s=1.0)
    g.run("project my screen", _cap())
    assert not (tmp_path / "routes.json").exists()


def test_history_off_means_no_routes(mocker):
    from app import ClickyApp
    app = ClickyApp(ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(), tts_client=mocker.MagicMock(),
                    memory_store=mocker.MagicMock(), overlay_controller=mocker.MagicMock(),
                    hotkey_instance=mocker.MagicMock())
    made = {}

    class FakeGuide:
        def __init__(self, **kw):
            made.update(kw)

        def run(self, *_a):
            return "done."

    mocker.patch("guide.Guide", FakeGuide)
    for setting, has_routes in (("off", False), ("on", True)):
        mocker.patch("app.resolve_setting", return_value=setting)
        app._run_guide("project my screen", _cap(), __import__("threading").Event(), mocker.MagicMock())
        assert (made["routes"] is not None) == has_routes
        assert made["speak"] == app._tts.speak_sentence and made["windows"] and made["focus"] and made["knowhow"]


# --- 6. The prompt and what OPEN can open ------------------------------------------------------

def test_the_prompt_knows_the_new_moves():
    p = guide.system_prompt()
    for must in ("[FOCUS:window]", "[OPEN:a][OPEN:b]", "side by side", "admin", "faster next time", "[FASTER:",
                 "never shown as captions", "never invent", "battery report", "taskmgr", "msinfo32"):
        assert must in p, must
    assert "side by side" not in apps.prompt_block(), "the everyday answer prompt stays as it was"


@pytest.mark.parametrize("target, kind", [
    ("taskmgr", "command"), ("devmgmt.msc", "command"), ("control /name Microsoft.PowerOptions", "command"),
    ("spotify:search:lo fi", "uri"), ("shell:startup", "uri"), ("mailto:a@b.com", "uri"), ("ms-screenclip:", "uri"),
    ("battery report", "report"), ("ms-settings:batterysaver", "settings"), ("youtube.com", "web"),
    ("Task Manager", "app"), (r"C:\Users", "app"),
])
def test_what_open_can_open(target, kind):
    assert apps.kind_of(target) == kind


def test_opening_tools_links_and_the_battery_report(monkeypatch, tmp_path):
    started = []
    start = lambda what, args="": started.append((what, args))  # noqa: E731
    assert apps.open_target("taskmgr", start=start) == "Task Manager"
    assert started[-1][0].lower().endswith("taskmgr.exe")
    apps.open_target("control /name Microsoft.PowerOptions", start=start)
    assert started[-1][0].lower().endswith("control.exe") and started[-1][1] == "/name Microsoft.PowerOptions"
    apps.open_target("spotify:search:lo fi", start=start)
    assert started[-1] == ("spotify:search:lo%20fi", "")
    monkeypatch.setenv("TEMP", str(tmp_path))
    apps.open_target("%TEMP%", start=start)
    assert started[-1] == (str(tmp_path), "")

    import config
    import subprocess
    monkeypatch.setattr(config, "APP_HOME", tmp_path)

    def fake_run(cmd, **_kw):
        assert cmd[1:3] == ["/batteryreport", "/output"]
        open(cmd[3], "w").write("<html>battery</html>")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(apps.sys, "platform", "win32")
    assert apps.open_target("battery report", start=start) == "the battery report"
    assert started[-1][0].endswith("battery-report.html")


def test_which_window_an_open_makes():
    assert apps.window_hint("ms-settings:display") == "settings"
    assert apps.window_hint("taskmgr") == "task manager"
    assert apps.window_hint("Spotify", "Spotify") == "spotify"
    assert apps.window_hint("https://youtube.com") == ""
    assert apps.window_hint("%temp%") == "file explorer"


# --- The battery-and-CPU request, start to finish -----------------------------------------------

def walk_battery_and_cpu():
    """Rayan's real request on the fake desk, with a model that answers at
    Sonnet's pace. Returns the guide's timeline and the fakes."""
    clock = Clock()
    desk = Desk(clock, [_chrome()])
    lines = []

    class Dbg:
        def log(self, line):
            lines.append(f"[+{round(clock.now * 1000):>5} ms] {line}")

    step1 = [(1.9, "i'll open task manager and your battery settings side by side, "),
             (0.3, "so you can see both at once. "),
             (0.5, "[OPEN:taskmgr][OPEN:ms-settings:batterysaver]"), (0.1, "[STEP:1/2]")]
    step2 = [(1.8, "click performance on the left of task manager for the live cpu graph; "),
             (0.3, "your battery's already on the right. "),
             (0.4, "[BOX:14,182,60,214:performance][DONE]")]
    g, log = _guide([step1, step2], clock, desk, dbg=Dbg(),
                    opens={"taskmgr": dict(title="Task Manager", exe="Taskmgr.exe", app="task manager",
                                           delay=0.9, front=False, admin=True),
                           "ms-settings:batterysaver": dict(title="Settings", exe="SystemSettings.exe",
                                                            app="settings", delay=0.5)},
                    events=[(9.0, ("click", 30, 400))],
                    knowhow=lambda goal, exe: kb.knowhow(goal, exe))
    g.run("Help me open up my battery performance and CPU load.", _cap())
    return lines, log, desk, clock


def test_battery_and_cpu_in_one_move():
    lines, log, desk, clock = walk_battery_and_cpu()
    assert desk.opened == ["taskmgr", "ms-settings:batterysaver"]
    assert desk.arranged, "side by side"
    assert len(log["prompts"]) == 2, "two model calls, not six"
    assert log["spoken"][0].startswith("i'll open task manager")
    assert any("first words after 2200 ms" in line for line in lines), "spoken at the first full stop"
    assert isinstance(log["shown"][-1][0], Box), "the last click is marked even though clixy can't see it"
    assert "admin" in log["prompts"][1] and "[task manager]" in log["prompts"][0]
    last_word = [e for e in log["events"] if e[0] == "speak"][-1][1]
    assert last_word < 8.0, "both instructions out within 8 s (the old run took 55 s and six calls)"


# --- Edges ------------------------------------------------------------------------------------

def test_an_answer_with_nothing_to_point_at_ends_at_once():
    clock = Clock()
    g, log = _guide(["projectors shine light through a lens."], clock, Desk(clock, [_chrome()]))
    g.run("how do projectors work", _cap())
    assert clock.now < 1.0 and log["shown"] == []


def test_a_step_in_an_admin_window_nudges_sooner_and_says_why():
    clock = Clock()
    tm = Window(2, "Task Manager", "Taskmgr.exe", "task manager", front=True, admin=True)
    g, log = _guide(["click performance. [BOX:14,182,60,214:performance][STEP:2/3]"], clock,
                    Desk(clock, [tm, _chrome(front=False)]), give_up_s=20.0)
    g.run("cpu", _cap())
    assert log["notes"] and "can't see clicks in Task Manager" in log["notes"][0]


def test_a_browser_tab_is_never_taken_for_the_app_it_mentions():
    tab = Window(1, "Task Manager - Google Search - Google Chrome", "chrome.exe", "chrome", front=True)
    assert not tab.matches("task manager") and tab.matches("chrome")
    assert Window(2, "Untitled - Notepad", "notepad.exe", "notepad").matches("untitled")
