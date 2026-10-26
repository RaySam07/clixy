"""Guide mode: Clixy walks you through a task one step at a time.

Ask "how do I project my screen" and Clixy shows only the first step: the
keys to press as big keycaps, or a box hugging the exact button (its
rectangle comes from Windows itself, see snap.elements). Then it watches.
When you click that button, press those keys, or the window changes, it
looks at the screen again and shows the next step, until the task is done.
It never clicks or types for you.

Each step is one small model call (the screen, the goal, what's done so far
and the list of clickable things with ids). The model answers with one
spoken sentence and one action tag; ids make the marks exact.

Threading: ``Guide.run`` runs on the pipeline's worker thread. The input
watch listens on pynput's threads and hands events over in a queue.
"""
from __future__ import annotations

import queue
import re
import time
from dataclasses import dataclass, field

from annotations import Box, Keys, key_names, parse_annotations

_GUIDE_WORDS = re.compile(
    r"\b(how (?:do|can|would|should) (?:i|we|you)|how to|take me (?:to|through)|walk me through|guide me|"
    r"show me (?:how|where)|teach me how|steps? to|help me (?:to )?(?:set|turn|change|connect|project|install|"
    r"uninstall|open|find|enable|disable|switch|add|remove|update|fix|make|use|share|cast|mirror|pair|print|"
    r"save|move|back ?up|record|screenshot|clear)|where (?:do|can) i (?:find|turn|change|set|open|get)|"
    r"(?:i want|i need|i'd like) to (?:project|connect|change|turn|set|install|share|cast|mirror|pair|print))\b",
    re.IGNORECASE,
)


def wants_guide(question: str) -> bool:
    """Is this a how-to: something to do on this computer, step by step?"""
    return bool(_GUIDE_WORDS.search(question or ""))


SYSTEM_PROMPT = """\
you're clixy, guiding the user through a task on their windows computer one step at a time, like a friend sitting beside them. you see their screen as it is right now. you're told the goal, what they've done so far, what this computer is, and the clickable things windows itself lists on screen, each with an id and its box.

reply with the single next step only:
- one short spoken sentence saying what to do, all lowercase, casual and warm. it's read aloud: no lists, no markdown, no symbols.
- then exactly one action tag:
  [CLICK:#id:label] click that element from the list. always prefer this: it's exact. read the names carefully and pick the one that matches.
  [KEYS:win+p:label] press these keys together. use it whenever a shortcut is the quickest way or nothing on screen leads there yet: win+p projects to another screen or projector, win+i opens settings, win+a quick settings, win+e file explorer, win+shift+s a screenshot, win+k cast to a wireless display.
  [TYPE:#id:text] type this into that field.
  [BOX:x1,y1,x2,y2:label] only when what they need isn't in the list: its box in screenshot pixels, hugging it tightly.
- then [STEP:n/total]: this step's number and your best guess at the total.

when the goal is reached on screen, say so in one short sentence and add [DONE].
if the goal isn't something to do on this computer (a general question), answer it fully, the way a sharp expert would, in natural speech, then [DONE].
if the last step didn't do what you expected, say so kindly and give the step that fixes it.
never refuse to show the way and never say you can't draw or point: there is always a key to press or a thing to click. never click or type for them; they do each step and you see the result.
you know windows 11 and 10 inside out: settings, quick settings, the start menu, the taskbar, file explorer and common apps.

examples:
- goal "project my laptop to the projector", nothing done yet: "press the windows key and p together to open the project menu. [KEYS:win+p:project menu][STEP:1/2]"
- then the list has #4 button "Duplicate": "pick duplicate so the projector shows the same thing as your laptop. [CLICK:#4:duplicate][STEP:2/2]"
- then: "that's it, the projector is showing your screen. [DONE]"
- goal "turn on dark mode", the list has #12 list item "Personalization": "open personalization on the left. [CLICK:#12:personalization][STEP:2/4]"\
"""

_CLICK_RE = re.compile(r"\[\s*CLICK\s*:\s*#?\s*(\d+)\s*(?::([^\]]*))?\]", re.IGNORECASE)
_TYPE_RE = re.compile(r"\[\s*TYPE\s*:\s*#?\s*(\d+)\s*:([^\]]*)\]", re.IGNORECASE)
_STEP_RE = re.compile(r"\[\s*STEP\s*:\s*(\d+)\s*(?:/\s*(\d+))?\s*\]", re.IGNORECASE)
_DONE_RE = re.compile(r"\[\s*DONE\s*\]", re.IGNORECASE)
_CUT_OFF_RE = re.compile(r"\[\s*(?:CLICK|TYPE|STEP|DONE)\b[^\]]*$", re.IGNORECASE | re.DOTALL)
# Anything else tag-shaped ([POINT:...], a half-written tag) is never read aloud.
_STRAY_TAG_RE = re.compile(r"\[\s*[A-Za-z_]+\s*:[^\]]*(?:\]|$)", re.DOTALL)


@dataclass
class Step:
    """One step as the model gave it."""
    say: str
    action: str = "none"        # click, keys, type, box, or none
    target: int | None = None   # element id, for click and type
    keys: str = ""
    box: Box | None = None      # screenshot px, when nothing in the list fits
    label: str = ""
    text: str = ""              # what to type
    n: int = 0
    total: int = 0
    done: bool = False


def parse_step(reply: str) -> Step:
    """The spoken sentence and the one action in a guide reply."""
    text = reply or ""
    step = Step(say="")
    if m := _STEP_RE.search(text):
        step.n, step.total = int(m.group(1)), int(m.group(2) or 0)
    step.done = bool(_DONE_RE.search(text))
    if m := _CLICK_RE.search(text):
        step.action, step.target, step.label = "click", int(m.group(1)), (m.group(2) or "").strip()
    elif m := _TYPE_RE.search(text):
        step.action, step.target, step.text = "type", int(m.group(1)), m.group(2).strip()
    for pattern in (_CLICK_RE, _TYPE_RE, _STEP_RE, _DONE_RE):
        text = pattern.sub("", text)
    text = _CUT_OFF_RE.sub("", text)
    spoken, shapes = parse_annotations(text)
    spoken = re.sub(r"\s+", " ", _STRAY_TAG_RE.sub("", spoken)).strip()
    if step.action == "none":
        keys = next((s for s in shapes if isinstance(s, Keys)), None)
        box = next((s for s in shapes if isinstance(s, Box)), None)
        if keys is not None:
            step.action, step.keys, step.label = "keys", keys.keys, keys.label
        elif box is not None:
            step.action, step.box, step.label = "box", box, box.label
    step.say = spoken
    return step


# --- The element list -------------------------------------------------------------

def element_lines(elements: list, capture) -> tuple[list[str], dict[int, object]]:
    """Number the elements and put their boxes in the screenshot's pixels:
    ``#7 button "Display settings" (412,88,530,112)``. Returns the lines and
    id -> element."""
    mon = capture.monitor
    lines: list[str] = []
    ids: dict[int, object] = {}
    for e in elements:
        l, t, r, b = e.rect
        x1 = round((l - mon["left"]) / capture.scale_x)
        y1 = round((t - mon["top"]) / capture.scale_y)
        x2 = round((r - mon["left"]) / capture.scale_x)
        y2 = round((b - mon["top"]) / capture.scale_y)
        x1, x2 = max(0, x1), min(capture.target_width, x2)
        y1, y2 = max(0, y1), min(capture.target_height, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        n = len(ids) + 1
        name = (e.name or "empty").replace('"', "'")
        lines.append(f'#{n} {e.kind} "{name}" ({x1},{y1},{x2},{y2})')
        ids[n] = e
    return lines, ids


def _to_physical(box: Box, capture) -> Box:
    from capture import unscale_claude_coords

    def pt(x: int, y: int) -> tuple[int, int]:
        return unscale_claude_coords(x, y, capture.scale_x, capture.scale_y, capture.monitor["left"],
                                     capture.monitor["top"], capture.target_width, capture.target_height)

    x1, y1 = pt(box.x1, box.y1)
    x2, y2 = pt(box.x2, box.y2)
    return Box(x1, y1, x2, y2, box.label)


# --- Watching what you do -----------------------------------------------------------

_MODIFIERS = {"win", "ctrl", "alt", "shift"}
_CAP_TOKENS = {"↑": "up", "↓": "down", "←": "left", "→": "right", "page up": "pageup", "page down": "pagedown"}


def combo_tokens(keys: str) -> frozenset[str]:
    """"win+p" -> {"win", "p"}, in the names the input watch reports."""
    return frozenset(_CAP_TOKENS.get(c.lower(), c.lower().replace(" ", "")) for c in key_names(keys))


def _key_name(key) -> str | None:
    """pynput's key -> our name ("win", "ctrl", "p", "enter")."""
    name = getattr(key, "name", None)
    if name:  # a special key
        for prefix, ours in (("cmd", "win"), ("ctrl", "ctrl"), ("alt", "alt"), ("shift", "shift")):
            if name.startswith(prefix):
                return ours
        return {"print_screen": "prtsc", "page_up": "pageup", "page_down": "pagedown"}.get(name, name)
    vk = getattr(key, "vk", None)
    if vk is not None and (65 <= vk <= 90 or 48 <= vk <= 57):
        return chr(vk).lower()  # held Ctrl turns letters into control characters; the key code doesn't lie
    char = getattr(key, "char", None)
    return char.lower() if char and char.isprintable() else None


class InputWatch:
    """Your clicks and key presses, observe-only (they still reach your apps):
    ("click", x, y) in physical px, and ("combo", {"win", "p"}) for each key
    pressed with whatever modifiers were held."""

    def __init__(self) -> None:
        self._events: queue.Queue = queue.Queue()
        self._held: set[str] = set()
        self._win_alone = False
        self._listeners: list = []

    def start(self) -> None:
        try:
            from pynput import keyboard, mouse
        except Exception:
            return  # no input watch: steps still advance when the window changes

        def on_click(x, y, _button, pressed):
            if pressed:
                self._events.put(("click", int(x), int(y)))

        def on_press(key):
            name = _key_name(key)
            if name is None:
                return
            if name in _MODIFIERS:
                self._win_alone = name == "win" and not self._held
                self._held.add(name)
                return
            self._win_alone = False
            self._events.put(("combo", frozenset(self._held | {name})))

        def on_release(key):
            name = _key_name(key)
            self._held.discard(name)
            if name == "win" and self._win_alone:
                self._win_alone = False
                self._events.put(("combo", frozenset({"win"})))  # the Windows key on its own opens Start

        try:
            self._listeners = [mouse.Listener(on_click=on_click),
                               keyboard.Listener(on_press=on_press, on_release=on_release)]
            for listener in self._listeners:
                listener.daemon = True
                listener.start()
        except Exception:
            self._listeners = []

    def get(self, timeout: float):
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while self.get(0) is not None:
            pass

    def stop(self) -> None:
        for listener in self._listeners:
            try:
                listener.stop()
            except Exception:
                pass
        self._listeners = []


# --- The guide ------------------------------------------------------------------------

@dataclass
class Guide:
    """Runs a guide from the first step to done. Everything it touches is
    passed in, so tests drive it with fakes:

    ask(capture, text) -> reply; capture() -> a fresh capture of the cursor's
    screen; list_elements(monitor) -> [snap.Element]; show(annotations,
    monitor) draws marks (physical px); clear() removes them; say(text, n,
    total, where) speaks a step and shows it in the island, clear of
    ``where`` ((rect, monitor) of the thing to click, or None); note(text) a short
    note; cancelled() -> True once a new question or quit ends the guide;
    foreground() -> something that changes when the window does."""

    ask: object
    capture: object
    list_elements: object
    show: object
    clear: object
    say: object
    note: object
    cancelled: object
    foreground: object
    watch: object = field(default_factory=InputWatch)
    about: str = ""
    refine: object = None            # snap.refine, for BOX steps
    dbg: object = None
    clock: object = time.monotonic
    sleep: object = time.sleep
    max_steps: int = 12
    settle_s: float = 0.7
    nudge_s: float = 40.0
    give_up_s: float = 150.0

    def run(self, goal: str, capture) -> str:
        """Guide toward ``goal`` from ``capture`` (the screen as it was asked
        on). Returns the last thing said."""
        done: list[str] = []
        last = ""
        fix = ""
        self.watch.start()
        try:
            n = 0
            while n < self.max_steps:
                if self.cancelled():
                    return last
                try:
                    found = self.list_elements(capture.monitor)
                except Exception:
                    found = []
                lines, ids = element_lines(found, capture)
                reply = self.ask(capture, self._prompt(goal, done, lines, fix))
                self._log(f"GUIDE: step {n + 1} reply {reply!r}")
                if self.cancelled():
                    return last
                step = parse_step(reply)
                if step.action in ("click", "type") and step.target not in ids and not fix:
                    fix = f"#{step.target} isn't in the list. pick an id from the list, or use keys or a box."
                    continue  # one retry with that said
                fix = ""
                n += 1
                total = max(step.total, step.n, n) if (step.total or step.n) else 0
                marks, target = self._marks(step, ids, capture)
                if step.say:
                    self.say(step.say, n, total, (target, capture.monitor) if target else None)
                    last = step.say
                if step.done or not marks:
                    return last
                self.watch.drain()
                self.show(marks, capture.monitor)
                outcome = self._wait(step, target)
                self._log(f"GUIDE: step {n} {outcome}")
                if outcome != "done":
                    return last
                done.append(self._describe(step, ids))
                self.clear()
                self.sleep(self.settle_s)
                fresh = self.capture()
                capture = fresh if fresh is not None else capture
            return last
        finally:
            self.watch.stop()

    def _prompt(self, goal: str, done: list[str], lines: list[str], fix: str) -> str:
        parts = [f'goal: "{goal.strip()}"']
        if self.about:
            parts.append(f"this computer: {self.about}")
        parts.append("done so far: " + ("; ".join(f"{i}. {d}" for i, d in enumerate(done, 1)) if done else "nothing yet"))
        if lines:
            parts.append("clickable things on screen now (id, kind, name, box in screenshot pixels):\n" + "\n".join(lines))
        else:
            parts.append("windows didn't list the controls here, so use keys, or a box for what to click.")
        if fix:
            parts.append(fix)
        parts.append("what's the single next step?")
        return "\n\n".join(parts)

    def _marks(self, step: Step, ids: dict, capture) -> tuple[list, tuple | None]:
        """What to draw (physical px) and the rectangle a click must land in."""
        if step.action in ("click", "type") and step.target in ids:
            l, t, r, b = ids[step.target].rect
            # The island already names it; a tag beside a menu item can sit on the next one.
            label = f'type "{step.text}"'[:40] if step.action == "type" else ""
            return [Box(l, t, r, b, label)], (l, t, r, b)
        if step.action == "keys":
            return [Keys(step.keys, step.label)], None
        if step.action == "box" and step.box is not None:
            box = _to_physical(step.box, capture)
            if self.refine is not None:
                try:
                    box = self.refine([box], capture.monitor)[0]
                except Exception:
                    pass
            return [box], (box.x1, box.y1, box.x2, box.y2)
        return [], None

    def _wait(self, step: Step, target: tuple | None) -> str:
        """Until you've done the step: "done", "cancel", or "gone" (no sign of you)."""
        start = self.clock()
        window = self.foreground()
        want = combo_tokens(step.keys) if step.action == "keys" else frozenset()
        check_at = None
        nudged = False
        typed = 0
        acted = False   # you've clicked or pressed something since this step showed
        next_look = start + 1.0
        while True:
            if self.cancelled():
                return "cancel"
            event = self.watch.get(0.2)
            now = self.clock()
            if event is not None:
                kind = event[0]
                if kind == "click" and target is not None and _inside(event[1], event[2], target):
                    if step.action != "type":
                        self.sleep(self.settle_s)
                        return "done"
                elif kind == "combo":
                    keys = event[1]
                    if want and want <= keys:
                        self.sleep(self.settle_s)
                        return "done"
                    if keys == frozenset({"esc"}) and step.action != "keys":
                        return "cancel"
                    if step.action == "type":
                        typed += 1
                        if "enter" in keys or "tab" in keys:
                            return "done"
                acted = True
                check_at = now + self.settle_s
            if check_at is not None and now >= check_at:
                check_at = None
                if self.foreground() != window:
                    return "done"
                if step.action == "type" and typed >= max(1, len(step.text) // 2):
                    return "done"
            if now >= next_look:
                next_look = now + 1.0
                if (acted or step.action == "keys") and self.foreground() != window:
                    return "done"  # what you did (or the shortcut) opened something new
            if not nudged and now - start >= self.nudge_s:
                nudged = True
                self.note("Still with me? Do the step, or hold the keys and ask something new.")
            if now - start >= self.give_up_s:
                return "gone"

    @staticmethod
    def _describe(step: Step, ids: dict) -> str:
        if step.action == "keys":
            return f"pressed {' + '.join(key_names(step.keys)).lower()}"
        if step.action == "type":
            return f'typed "{step.text}"'
        if step.action == "click" and step.target in ids:
            return f'clicked "{ids[step.target].name}"'
        return f"clicked {step.label or 'the marked thing'}"

    def _log(self, line: str) -> None:
        if self.dbg is not None:
            self.dbg.log(line)


def _inside(x: int, y: int, rect: tuple, margin: int = 8) -> bool:
    l, t, r, b = rect
    return l - margin <= x <= r + margin and t - margin <= y <= b + margin
