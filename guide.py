"""Guide mode: Clixy walks you through a task one step at a time.

Ask "how do I project my screen" and Clixy shows only the first step: the
keys to press as big keycaps, or a box hugging the exact button (its
rectangle comes from Windows itself, see snap.elements). Then it watches.
When you click that button, press those keys, or the window changes, it
looks at the screen again and shows the next step, until the task is done.
It never clicks or types for you.

Each step is one small model call (the screen, the goal, what's done so far,
the open windows, know-how for the apps involved, a route that worked
before, and the list of clickable things with ids). The model answers with
one spoken sentence and one action tag; ids make the marks exact.

It stays with you till the goal is reached. Holding the keys and saying
nothing, or "next" / "done", keeps it going; "I can't find it", "nothing
happened" make it look again with what you said (``during_guide``); only a
new question ends it.

It thinks ahead: the first reply plans the whole route ([PLAN:...], the
next clicks by the names you'll see), each later look checks the last step
worked, and when the next planned button is already on screen it is shown
at once, with no model call (the plan's fast lane).

Fast, so it feels like a friend beside you:
- the sentence is spoken the moment it has streamed in, before the tags;
- after each action it looks again as soon as the screen has settled (the
  front window changed and held still, or the window it opened is up),
  not after a fixed sleep; a window that opened behind is brought forward;
- it knows the open windows, so it brings one forward ([FOCUS:...]) rather
  than opening it again, and opens two side by side when a goal needs both;
- know-how packs (knowhow/, through kb.knowhow) give it real shortcuts,
  settings pages and run commands; routes that worked are remembered
  (memory.RouteBook) so a similar goal goes straight there.

Threading: ``Guide.run`` runs on the pipeline's worker thread. The input
watch listens on pynput's threads and hands events over in a queue.
"""
from __future__ import annotations

import queue
import re
import threading
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


_TASK_VERBS = (r"open|find|access|reach|get to|go to|see|check|view|change|set|setup|turn|switch|add|remove|"
               r"delete|create|make|upload|download|install|uninstall|share|send|connect|pair|print|save|"
               r"export|import|rename|move|copy|sign|log|book|schedule|cancel|update|reset|fix|enable|"
               r"disable|record|edit|post|reply|submit|start|join|use|customi[sz]e|block|unblock|mute|"
               r"unmute|pin|unpin|restore|back ?up|clear|empty|format|sort|filter|merge|split|insert|attach")
_DEMANDS = re.compile(
    rf"\b(?:(?:i want|i need|i'd like|i would like|i wanna|i gotta|i have|i'm trying|i am trying) to (?:{_TASK_VERBS})\b|"
    r"help me (?!understand|learn|think|figure out why|see why)\w+|"
    r"(?:can|could|will|would) you (?:help me|show me|take me|walk me|guide me|get me)|"
    r"\b(?:take|get|bring) me to\b|"
    r"where(?:'s| is| are| do i| can i| would i| should i)\b.{0,60}\b(?:button|setting|settings|option|options|"
    r"menu|tab|page|studio|folder|file|tool|icon|panel|toggle|switch|preference|preferences|account|profile)\b|"
    rf"^\s*(?:please\s+)?(?:{_TASK_VERBS})\b.{{0,80}}\b(?:in|on|from|for|to|my|the)\b)",
    re.IGNORECASE,
)
_LEARNING = re.compile(r"^\s*(?:why|what|explain|tell me (?:about|why|what)|how (?:does|do (?!i\b|we\b|you\b))|"
                       r"teach me (?:about|what|why)|help me understand)\b", re.IGNORECASE)


def wants_guide(question: str) -> bool:
    """Is this something to do on this computer, step by step? A how-to ("how do
    I..."), or a demand ("I want to access my YouTube Studio", "help me add a
    panel", "where's the export button"), not a question to understand
    something ("how does a jet engine work")."""
    q = question or ""
    if _GUIDE_WORDS.search(q):
        return True
    if _LEARNING.search(q):
        return False
    return bool(_DEMANDS.search(q))


_CARRY_ON = re.compile(r"^\W*(?:next|next step|done|i did it|did it|i've done it|ok(?:ay)?|got it|yes|yep|yeah|"
                       r"continue|go on|carry on|what now|now what|what next|what's next|and now|then what|"
                       r"keep going|alright|right)\W*$", re.IGNORECASE)
_REMARK = re.compile(r"\b(?:can'?t find|cannot find|can not find|couldn'?t find|don'?t see|do not see|can'?t see|"
                     r"not there|isn'?t there|it'?s not|didn'?t work|doesn'?t work|not working|nothing happened|"
                     r"nothing'?s happening|wrong|stuck|lost|where is it|where's it|where is that|which one|"
                     r"what do i (?:click|press|do)|it says|it's asking|it asks|error|go back|undo|i clicked|"
                     r"i pressed|missed|too fast|slow down|again|repeat|say that again|what was that)\b",
                     re.IGNORECASE)


def during_guide(text: str) -> str:
    """What something said while a guide runs means: "keep" (nothing said, the
    keys were held by accident), "next" (carry on: look again now), "remark"
    (about this step: "I can't find it", "nothing happened"; look again with
    that in mind), or "new" (a different question: the guide ends)."""
    t = " ".join((text or "").split())
    if not t:
        return "keep"
    if _CARRY_ON.match(t):
        return "next"
    if _REMARK.search(t) and not wants_guide(t):
        return "remark"
    return "new"


SYSTEM_PROMPT = """\
you're clixy, guiding the user through a task on their windows computer one step at a time, like a friend beside them who knows windows and every common app inside out: the shortcuts, the direct links, the little tricks. you see their screen as it is right now. each turn you're told the goal, what's been done so far, this computer, the open windows (front first), know-how for the apps involved, sometimes the route that worked last time, and the clickable things windows lists on screen, each with an id and its box.

think first: what's the fastest real route from here? in this order:
1. open it directly with [OPEN:...]: a settings page, a windows tool, an app, a website, an app link or the battery report, straight to the right place. the know-how and the lists give real addresses: use them, never guess one.
2. what they need is already open (check the open windows)? bring it forward with [FOCUS:...]. never open again what's already open, and never say something didn't open when the open windows show it: it may be behind another window or still drawing.
3. a keyboard shortcut with [KEYS:...] when it gets there faster than clicking.
4. a click on a listed element with [CLICK:#id:...].
5. a [BOX:...] only for what isn't listed.
a goal with parts that live in different places (cpu load and battery, say) is one move: open both side by side.
the know-how is accurate: prefer its routes. never invent a menu, button, setting or address that isn't on screen, in the know-how, or real in this version of windows. if unsure, open the page or app that certainly has it, then look.

think the whole route through before the first step, the way someone who knows this app by heart would: where it ends up, and every click on the way. keep the plan you're told unless the screen shows something else; then make a better one.
each time you look, first check the screen: did the last step work? if it didn't (wrong page, nothing opened, a dialog or an error in the way), say so kindly in a few words and give the step that fixes it.
finish the job: keep going until the goal is truly done on screen, never stopping half way or handing it back. if the app can't do exactly what they asked, say so honestly in one sentence and take them the closest real way.

reply with the single next step only:
- first, one or two short spoken sentences, warm and natural: what to do and, in a few words, why. all lowercase. they're spoken aloud and never shown as captions, so no lists, markdown, symbols, ids, coordinates or addresses (say "your battery settings", not the link). your first sentence is spoken while you're still writing, so make it the instruction itself.
- then one action tag:
  [OPEN:x] clixy opens it at once. [OPEN:a][OPEN:b] opens two side by side, a on the left.
  [FOCUS:window] clixy brings that open window to the front, restoring it if it's minimised. name it as the open windows list does.
  [KEYS:ctrl+shift+esc:label] they press these keys together.
  [CLICK:#id:label] they click that element. read the names carefully; prefer it to a box, it's exact.
  [TYPE:#id:text] they type this into that field.
  [BOX:x1,y1,x2,y2:label] what to click when it isn't listed: its box in screenshot pixels, hugging it tightly.
  a label is one to three words naming the thing, never your sentence.
- then [STEP:n/total]: this step's number and your best guess at the total.
- then, on the first step and whenever the route changes, [PLAN:...]: the steps after this one, in order, separated by |, each the click with the exact name they'll see on screen and a few spoken words for it, like [PLAN:click "YouTube Studio": now pick youtube studio from the menu | click "Customisation": then customisation, on the left]. a press is press "win+i": words. leave out what you can't foresee.

windows marked admin (task manager often is) hide their buttons and the user's clicks from clixy: point with a box from the screenshot and say any next click in the same sentence; when that click is the last thing to do, add [DONE] after the box.
when the goal is reached on screen, say so in one short, happy sentence and add [DONE]. if there's a quicker way for next time than the route taken (a shortcut, a run command, a direct page), add it in a few words, like "faster next time, press windows and i", and tag it [FASTER:win+i] (the keys, or what to open).
if the goal isn't something to do on this computer (a general question), answer it fully, in natural speech, then [DONE].
if the last step didn't do what you expected, say so kindly and give the step that fixes it.
never refuse and never say you can't point: there's always something to open, bring forward, press or click. apart from opening things and bringing windows forward, never click or type for them; they do each step and you see the result.

examples:
- goal "project my laptop to the projector", nothing done yet: "press the windows key and p together, that opens the project menu. [KEYS:win+p:project menu][STEP:1/2][PLAN:click "Duplicate": pick duplicate, so the projector shows your screen]"
- then the list has #4 button "Duplicate": "pick duplicate, so the projector shows exactly what's on your laptop. [CLICK:#4:duplicate][STEP:2/2]"
- then: "that's it, you're projecting. [DONE]"
- goal "turn on dark mode": "let's go straight to your colour settings, i'll open them for you. [OPEN:ms-settings:personalization-colors][STEP:1/2]"
- goal "show my battery and cpu load": "i'll open task manager and your battery settings side by side, so you can see both at once. [OPEN:taskmgr][OPEN:ms-settings:batterysaver][STEP:1/2]"
- then task manager, marked admin, is on the left on its processes page: "click performance on the left of task manager for the live cpu graph; your battery's already on the right. [BOX:14,182,60,214:performance][DONE]"
- goal "open spotify", the open windows show spotify minimised: "spotify's already open, i'll bring it up for you. [FOCUS:spotify][STEP:1/1]"
- goal "i want to get to my youtube studio", youtube is open: "click your round profile picture at the top right, youtube studio is in that menu. [CLICK:#12:profile picture][STEP:1/2][PLAN:click "YouTube Studio": now pick youtube studio]"
- then the list has nothing new and the screen shows no menu: "the menu didn't open, so click the round profile picture once more, right at the top right. [CLICK:#12:profile picture][STEP:1/2]"\
"""


def system_prompt() -> str:
    """The guide's instructions, with every page, tool and app it can open."""
    import apps
    return SYSTEM_PROMPT + apps.prompt_block(guide=True)

_CLICK_RE = re.compile(r"\[\s*CLICK\s*:\s*#?\s*(\d+)\s*(?::([^\]]*))?\]", re.IGNORECASE)
_TYPE_RE = re.compile(r"\[\s*TYPE\s*:\s*#?\s*(\d+)\s*:([^\]]*)\]", re.IGNORECASE)
_STEP_RE = re.compile(r"\[\s*STEP\s*:\s*(\d+)\s*(?:/\s*(\d+))?\s*\]", re.IGNORECASE)
_DONE_RE = re.compile(r"\[\s*DONE\s*\]", re.IGNORECASE)
_FOCUS_RE = re.compile(r"\[\s*FOCUS\s*:\s*([^\]]+?)\s*\]", re.IGNORECASE)
_FASTER_RE = re.compile(r"\[\s*FASTER\s*:\s*([^\]]+?)\s*\]", re.IGNORECASE)
_PLAN_RE = re.compile(r"\[\s*PLAN\s*:\s*([^\]]*)\]", re.IGNORECASE)
_CUT_OFF_RE = re.compile(r"\[\s*(?:CLICK|TYPE|STEP|DONE|FOCUS|FASTER|PLAN)\b[^\]]*$", re.IGNORECASE | re.DOTALL)
_PLANNED_RE = re.compile(r"^\s*(click|press)\s+[\"“']([^\"”']+)[\"”']\s*(?::\s*(.*))?$", re.IGNORECASE)
# Anything else tag-shaped ([POINT:...], a half-written tag) is never read aloud.
_STRAY_TAG_RE = re.compile(r"\[\s*[A-Za-z_]+\s*:[^\]]*(?:\]|$)", re.DOTALL)


@dataclass
class Step:
    """One step as the model gave it."""
    say: str
    action: str = "none"        # open, focus, click, keys, type, box, or none
    target: int | None = None   # element id, for click and type
    keys: str = ""
    box: Box | None = None      # screenshot px, when nothing in the list fits
    label: str = ""
    text: str = ""              # what to type, what to open (the first), or the window to bring forward
    n: int = 0
    total: int = 0
    done: bool = False
    opens: list = field(default_factory=list)   # everything to open (two go side by side)
    faster: str = ""            # the quick way for next time ("win+i"), on the last step
    plan: list | None = None    # the steps after this one (Planned), when the reply gave a [PLAN:...]


@dataclass
class Planned:
    """A step the model foresaw: click "YouTube Studio", or press "win+i", and its words."""
    action: str                 # click or press
    name: str                   # the element's name on screen, or the keys
    say: str = ""

    def __str__(self) -> str:
        return f'{self.action} "{self.name}"'


def parse_plan(text: str) -> list[Planned]:
    """The steps in a [PLAN:...] tag's text."""
    out: list[Planned] = []
    for part in (text or "").split("|"):
        m = _PLANNED_RE.match(part)
        if m and m.group(2).strip():
            out.append(Planned(m.group(1).lower(), m.group(2).strip()[:80], (m.group(3) or "").strip()[:200]))
    return out[:8]


def parse_step(reply: str) -> Step:
    """The spoken sentence and the one action in a guide reply."""
    text = reply or ""
    step = Step(say="")
    if m := _STEP_RE.search(text):
        step.n, step.total = int(m.group(1)), int(m.group(2) or 0)
    step.done = bool(_DONE_RE.search(text))
    if m := _FASTER_RE.search(text):
        step.faster = m.group(1).strip()
    if m := _PLAN_RE.search(text):
        step.plan = parse_plan(m.group(1))
    text = _PLAN_RE.sub("", text)
    import apps
    text, opens = apps.parse_open(text)
    focus = _FOCUS_RE.search(text)
    if opens:
        step.action, step.text, step.opens = "open", opens[0], opens[:2]
    elif focus:
        step.action, step.text = "focus", focus.group(1).strip()
    elif m := _CLICK_RE.search(text):
        step.action, step.target, step.label = "click", int(m.group(1)), (m.group(2) or "").strip()
    elif m := _TYPE_RE.search(text):
        step.action, step.target, step.text = "type", int(m.group(1)), m.group(2).strip()
    for pattern in (_CLICK_RE, _TYPE_RE, _STEP_RE, _DONE_RE, _FOCUS_RE, _FASTER_RE):
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


# --- The open windows ---------------------------------------------------------------

def window_lines(wins: list, most: int = 8) -> list[str]:
    """The open windows for the model, front first:
    ``1. task manager "Task Manager" (in front, admin: ...)``."""
    lines = []
    for i, w in enumerate(wins[:most], 1):
        flags = [text for text, on in (("in front", w.front), ("minimised", w.minimized),
                                       ("admin: its buttons and clicks are hidden from clixy", w.admin)) if on]
        title = (w.title or "")[:60].replace('"', "'")
        lines.append(f'{i}. {w.app} "{title}"' + (f" ({', '.join(flags)})" if flags else ""))
    return lines


# --- Speaking before the reply is finished --------------------------------------------

_SENTENCE_END = re.compile(r"[.!?](?=\s)")


def _speakable(text: str) -> str:
    return re.sub(r"\s+", " ", _STRAY_TAG_RE.sub("", text)).strip()


class _EarlySpeech:
    """Feeds a streaming reply and speaks each sentence the moment it's
    complete, until the first tag starts (the rest is spoken once the whole
    reply has been read). ``pos`` is how far into the reply was spoken."""

    def __init__(self, speak, clock) -> None:
        self.speak, self.clock = speak, clock
        self.text = ""
        self.pos = 0
        self.first_at: float | None = None
        self.off = speak is None

    def feed(self, chunk: str) -> None:
        self.text += chunk
        if self.off:
            return
        head = self.text[self.pos:]
        cut = head.find("[")
        region = head if cut < 0 else head[:cut]
        start = 0
        for m in _SENTENCE_END.finditer(region):
            sentence = _speakable(region[start:m.end()])
            start = m.end()
            if sentence:
                if self.first_at is None:
                    self.first_at = self.clock()
                self.speak(sentence)
        self.pos += start
        if cut >= 0:
            self.off = True   # the tags have begun


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

_current: "Guide | None" = None


def current() -> "Guide | None":
    """The guide running right now, if any (the Clixy panel shows its steps)."""
    return _current


def step_label(step: Step, ids: dict, *, past: bool = False) -> str:
    """What a step asks of you in a few words, for the panel: "Press Win + P",
    "Click Duplicate". Built from the action, never from the spoken sentence."""
    if step.action == "keys":
        return ("Pressed " if past else "Press ") + " + ".join(key_names(step.keys))
    if step.action in ("click", "box"):
        name = ids[step.target].name if step.action == "click" and step.target in ids else step.label
        name = " ".join((name or "").split())
        return ("Clicked " if past else "Click ") + (name or "the marked spot")
    if step.action == "type":
        return ("Typed " if past else "Type ") + f'"{step.text}"'
    if step.action == "open":
        names = ["Settings" if t.lower().startswith("ms-settings:") else t for t in (step.opens or [step.text])]
        return ("Opened " if past else "Opening ") + " and ".join(names)
    if step.action == "focus":
        return ("Brought up " if past else "Bringing up ") + step.text
    return ""


@dataclass
class Guide:
    """Runs a guide from the first step to done. Everything it touches is
    passed in, so tests drive it with fakes:

    ask(capture, text) -> the reply, as a string or as text chunks while it
    streams; capture() -> a fresh capture of the cursor's screen;
    list_elements(monitor) -> [snap.Element]; show(annotations, monitor)
    draws marks (physical px); clear() removes them; say(text, n, total,
    where) shows a step in the island (and speaks it, unless ``speak`` is
    given), clear of ``where`` ((rect, monitor) of the thing to click, or
    None); note(text) a short note; cancelled() -> True once a new question
    or quit ends the guide; foreground() -> something that changes when the
    window does.

    Optional: speak(sentence) speaks each sentence as soon as it has
    streamed in; windows() -> [snap.Window] front first; focus(hwnd) brings
    one forward; arrange(first, second, monitor) puts two side by side;
    knowhow(goal, front_exe) -> the relevant know-how; routes, a
    memory.RouteBook (None when history is off)."""

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
    open: object = None              # apps.open_target, for OPEN steps
    speak: object = None             # tts.speak_sentence: the voice, sentence by sentence as it streams
    windows: object = None           # snap.windows
    focus: object = None             # snap.focus, for FOCUS (and windows that open behind)
    arrange: object = None           # snap.side_by_side, for two OPENs
    knowhow: object = None           # kb.knowhow
    routes: object = None            # memory.RouteBook
    dbg: object = None
    clock: object = time.monotonic
    sleep: object = time.sleep
    max_steps: int = 12
    settle_s: float = 0.45           # a click that changes nothing outside its window: look again after this
    keys_settle_s: float = 0.9       # the same after a shortcut (menus and flyouts take a moment)
    quiet_s: float = 0.3             # the front window has to hold still this long
    settle_cap_s: float = 3.0        # never wait longer than this for the screen to settle
    linger_s: float = 8.0            # a last mark stays up this long (or until you act)
    nudge_s: float = 40.0
    give_up_s: float = 150.0
    # What the panel shows (read from the Qt thread through progress()).
    _goal: str = field(default="", init=False, repr=False)
    _done_labels: list = field(default_factory=list, init=False, repr=False)
    _now: str = field(default="", init=False, repr=False)
    _keys: str = field(default="", init=False, repr=False)
    _n: int = field(default=0, init=False, repr=False)
    _total: int = field(default=0, init=False, repr=False)
    _confirmed: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _stopped: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _shortcut: str = field(default="", init=False, repr=False)   # the quick way next time ("win+i")
    _heard: list = field(default_factory=list, init=False, repr=False)      # what you said about this step
    _heard_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _know_cache: dict = field(default_factory=dict, init=False, repr=False)

    # --- For the panel (any thread) -------------------------------------------------

    def progress(self) -> dict:
        """Where the guide is: the goal (your own words), the steps done, the
        step now ("Press Win + P"), its keys if it is a shortcut, n and total.
        Never the spoken sentences."""
        return {"goal": self._goal, "done": list(self._done_labels), "now": self._now,
                "keys": self._keys, "n": self._n, "total": self._total, "shortcut": self._shortcut}

    def confirm(self) -> None:
        """"I did it": count this step as done and look at the screen again."""
        self._confirmed.set()

    def stop(self) -> None:
        """Stop guiding (the panel's Stop)."""
        self._stopped.set()

    def hear(self, text: str) -> None:
        """Something you said about this step ("I can't find it"): it looks again
        and answers with that in mind (any thread)."""
        text = " ".join((text or "").split())
        if text:
            self._heard.append(text[:200])
            self._heard_event.set()

    def _halted(self) -> bool:
        return self._stopped.is_set() or bool(self.cancelled())

    def run(self, goal: str, capture) -> str:
        """Guide toward ``goal`` from ``capture`` (the screen as it was asked
        on). Returns the last thing said."""
        global _current
        done: list[str] = []     # what has happened, told to the model
        route: list[str] = []    # the moves that worked, remembered for next time
        last = ""
        fix = ""
        finished = False
        self._goal = " ".join((goal or "").split())
        _current = self
        hint = self._route_hint(goal)
        self.watch.start()
        plan: list[Planned] = []     # the steps the model foresaw, still to come
        try:
            # Step one's look, with the know-how for the app in front fetched alongside.
            wins0 = self._windows()
            know = threading.Thread(target=self._know, args=(goal, wins0), name="clixy-guide-know", daemon=True)
            know.start()
            found = self._elements(capture.monitor)
            know.join(timeout=3.0)
            n = 0
            spoke = False        # this step's sentence is already out (a retry stays quiet)
            while n < self.max_steps:
                if self._halted():
                    return last
                lines, ids = element_lines(found, capture)
                fast = self._from_plan(plan, ids) if not fix else None
                if fast is not None:     # the next step is the one foreseen, and it's on screen: show it now
                    step, plan = fast, plan[1:]
                    self._log(f"GUIDE: step {n + 1} from the plan ({plan and len(plan) or 0} more): {step.say!r}")
                    if self.speak is not None and step.say:
                        self._speak(step.say)
                    reply, spoken_to, quiet = "", 0, True
                else:
                    wins = self._windows()
                    prompt = self._prompt(goal, done, lines, fix, wins, hint, self._know(goal, wins), plan)
                    asked = self.clock()
                    reply, spoken_to = self._ask(capture, prompt, speak=not spoke, n=n + 1)
                    self._log(f"GUIDE: step {n + 1} reply in {self._ms(asked)} ms {reply!r}")
                    if self._halted():
                        return last
                    step = parse_step(reply)
                    if step.action in ("click", "type") and step.target not in ids and not fix:
                        fix = f"#{step.target} isn't in the list. pick an id from the list, or use keys or a box."
                        spoke = spoke or spoken_to > 0
                        continue  # one retry with that said
                    quiet = spoke
                    if step.plan is not None:
                        plan = step.plan
                    elif plan:
                        plan = plan[1:]  # this step was (most likely) the plan's next one
                spoke, fix = False, ""
                wins = self._windows()
                n += 1
                total = max(step.total, step.n, n) if (step.total or step.n) else 0
                marks, target = self._marks(step, ids, capture)
                self._n, self._total = n, total
                self._now = step_label(step, ids)
                self._keys = step.keys if step.action == "keys" else ""
                self._confirmed.clear()
                if step.faster:
                    self._shortcut = step.faster
                rest = parse_step(reply[spoken_to:]).say if spoken_to else (step.say if reply else "")
                if self.speak is not None and rest and not quiet:
                    self._speak(rest)
                if step.say:
                    self.say(step.say, n, total, (target, capture.monitor) if target else None)
                    last = step.say
                if step.action in ("open", "focus"):
                    if step.action == "open":
                        notes, moved, failed = self._open_all(step.opens or [step.text], capture.monitor)
                    else:
                        notes, moved, failed = self._bring(step.text)
                    done.extend(notes)
                    if moved:
                        route.append(moved)
                        self._done_labels.append(step_label(step, ids, past=True))
                    if failed:
                        fix = failed
                    if step.done and moved:
                        finished = True
                        return last
                    capture, found = self._look(capture)
                    continue
                if not marks:   # done, or an answer with nothing to point at
                    finished = step.done
                    return last
                self.watch.drain()
                self.show(marks, capture.monitor)
                before = self.foreground()
                admin = next((w for w in wins if w.front and w.admin), None) if step.action == "box" else None
                outcome = self._wait(step, target, limit=self.linger_s if step.done else None,
                                     unseen=admin.app if admin is not None else "")
                self._log(f"GUIDE: step {n} {outcome}")
                if outcome == "heard":   # you said something about this step: look again with it in mind
                    said = "; ".join(self._heard)
                    self._heard.clear()
                    self._heard_event.clear()
                    self.clear()
                    n -= 1
                    plan = []
                    fix = (f'while doing step {n + 1} ({self._describe(step, ids).replace("clicked", "click").replace("pressed", "press")}) '
                           f'the user said: "{said}". look at the screen again and help with exactly that: '
                           "if they can't find it, describe where it is and point at it again; if something went "
                           "wrong, fix it.")
                    capture, found = self._look(capture)
                    continue
                if step.done:   # the last mark (a click Clixy can't watch, say) stayed up for a while
                    finished = outcome != "cancel"
                    route.append(self._move(step, ids))
                    return last
                if outcome != "done":
                    return last
                done.append(self._describe(step, ids))
                route.append(self._move(step, ids))
                self._done_labels.append(step_label(step, ids, past=True))
                self.clear()
                if step.action == "keys":
                    import apps
                    self._settle(before, apps.SHORTCUT_WINDOWS.get(combo_tokens(step.keys), ""),
                                 grace=self.keys_settle_s)
                else:
                    self._settle(before, grace=self.settle_s)
                capture, found = self._look(capture)
            return last
        finally:
            self.watch.stop()
            if finished:
                self._remember(goal, route)
            if _current is self:
                _current = None

    # --- Asking, and speaking as the answer streams in ---------------------------------

    def _ask(self, capture, prompt: str, *, speak: bool, n: int) -> tuple[str, int]:
        """The model's reply. With ``speak``, each sentence is spoken the
        moment it has streamed in, before the action tags arrive. Returns the
        reply and how far into it was spoken."""
        asked = self.clock()
        result = self.ask(capture, prompt)
        chunks = [result] if isinstance(result, str) else (result or [])
        early = _EarlySpeech(self._speak if (speak and self.speak is not None) else None, self.clock)
        try:
            for chunk in chunks:
                if chunk:
                    early.feed(chunk)
                if self._halted():
                    break
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                try:
                    close()   # a stream left early is closed, not left running
                except Exception:
                    pass
        if early.first_at is not None:
            self._log(f"GUIDE: step {n} first words after {round((early.first_at - asked) * 1000)} ms")
        return early.text, early.pos

    def _speak(self, sentence: str) -> None:
        self._log(f"GUIDE: speaking {sentence!r}")
        try:
            self.speak(sentence)
        except Exception:
            pass

    # --- Opening things, bringing windows forward, waiting for the screen ----------------

    def _open(self, target: str) -> str:
        if self.open is None:
            return ""
        try:
            return self.open(target) or ""
        except Exception:
            return ""

    def _open_all(self, targets: list[str], monitor) -> tuple[list[str], str, str]:
        """Open one thing, or two side by side. Each time, wait until its
        window is there and in front (bringing it forward if it opened
        behind). Returns notes for the model, the move to remember ("" if
        nothing opened) and what to fix ("" if all went well)."""
        import apps
        notes: list[str] = []
        opened_ok: list[tuple[str, object]] = []
        failed = []
        for target in targets[:2]:
            before = self.foreground()
            opened = self._open(target)
            if not opened:
                failed.append(target)
                notes.append(f"clixy tried to open {target} but it failed")
                continue
            expect = apps.window_hint(target, opened)
            self._settle(before, expect, grace=self.keys_settle_s)
            win = self._find_window(expect) if expect else self._front_window()
            opened_ok.append((target, win))
            self._log(f"GUIDE: opened {opened!r}" + (f" -> {win.app} {win.title!r}" if win else ""))
            notes.append(f"clixy opened {opened}" + (f" ({win.app}, in front)" if win is not None and win.front else ""))
        if len(opened_ok) == 2 and self.arrange is not None:
            a, b = opened_ok[0][1], opened_ok[1][1]
            placed = ""
            if a is not None and b is not None and a.hwnd != b.hwnd:
                try:
                    placed = self.arrange(a.hwnd, b.hwnd, monitor) or ""
                except Exception:
                    placed = ""
            self._log(f"GUIDE: side by side: {placed or 'no'}")
            notes.append(f"side by side: {placed}" if placed else
                         "they couldn't be put side by side; the second may cover the first")
            self._settle(self.foreground(), grace=0.2)
        moved = ""
        if opened_ok:
            moved = "open " + " + ".join(t for t, _w in opened_ok) + (" side by side" if len(opened_ok) == 2 else "")
        fix = (" and ".join(failed) + " couldn't be opened on this computer. find another way.") if failed else ""
        return notes, moved, fix

    def _bring(self, name: str) -> tuple[list[str], str, str]:
        """FOCUS: bring an open window to the front."""
        win = self._find_window(name)
        if win is None:
            return ([f"there's no open window called {name}"], "",
                    f"no open window is called \"{name}\": open it instead, or pick one from the open windows.")
        before = self.foreground()
        ok = self._focus(win)
        self._settle(before, name, grace=self.settle_s)
        self._log(f"GUIDE: brought {win.app} {win.title!r} forward: {'ok' if ok else 'refused'}")
        if not ok:
            return ([f"clixy tried to bring {win.app} forward but windows refused (it may be flashing in the taskbar)"],
                    "", f"bringing {win.app} forward didn't work: point them to it in the taskbar.")
        return [f"clixy brought {win.app} to the front"], f"focus {win.app}", ""

    def _focus(self, win) -> bool:
        if self.focus is None or win is None:
            return False
        try:
            return bool(self.focus(win.hwnd))
        except Exception:
            return False

    def _windows(self) -> list:
        if self.windows is None:
            return []
        try:
            return list(self.windows() or [])
        except Exception:
            return []

    def _find_window(self, name: str, wins: list | None = None):
        """The open window someone calls ``name``: by app name first, then by title."""
        wins = self._windows() if wins is None else wins
        want = " ".join((name or "").lower().split())
        if not want or not wins:
            return None
        if want.isdigit() and 0 < int(want) <= len(wins):
            return wins[int(want) - 1]
        for w in wins:
            if want in (w.app, w.exe.lower(), w.exe.lower().removesuffix(".exe")):
                return w
        return next((w for w in wins if w.matches(want)), None)

    def _front_window(self):
        return next((w for w in self._windows() if w.front), None)

    def _settle(self, before, expect: str = "", *, grace: float = 0.9) -> str:
        """Wait for the screen to settle after an action, instead of a fixed
        sleep. With ``expect`` (a window's name), until that window is in
        front and the front has held still for ``quiet_s``, bringing it
        forward if it opened behind another; otherwise until the front window
        changed and held still, or ``grace`` passes with no change (a click
        inside the same window). Never longer than ``settle_cap_s``."""
        start = self.clock()
        last = self.foreground()
        changed_at = start if last != before else None
        front = False
        behind = focused = 0
        next_scan = start
        watch_windows = bool(expect) and self.windows is not None
        reason = "timeout"
        try:
            while True:
                if self._halted():
                    reason = "cancel"
                    return reason
                now = self.clock()
                if now - start >= self.settle_cap_s:
                    return reason
                fg = self.foreground()
                if fg != last:
                    last, changed_at = fg, now
                if watch_windows:
                    if now >= next_scan:
                        next_scan = now + 0.15
                        win = self._find_window(expect)
                        front = win is not None and win.front
                        if win is not None and not front:
                            behind += 1
                            if behind >= 2 and focused < 2:   # it opened behind another window
                                focused += 1
                                behind = 0
                                ok = self._focus(win)
                                self._log(f"GUIDE: {win.app} was behind; brought it forward ({'ok' if ok else 'refused'})")
                                changed_at = now
                    if front and now - (changed_at or start) >= self.quiet_s:
                        reason = f"{expect} in front"
                        return reason
                    if not front and changed_at is not None and now - changed_at >= 4 * self.quiet_s \
                            and now - start >= 1.5:
                        reason = "changed (no window by that name)"
                        return reason
                else:
                    if changed_at is not None and now - changed_at >= self.quiet_s:
                        reason = "changed"
                        return reason
                    if changed_at is None and now - start >= grace:
                        reason = "same window"
                        return reason
                self.sleep(0.05)
        finally:
            self._log(f"GUIDE: settled in {self._ms(start)} ms ({reason})")

    def _elements(self, monitor) -> list:
        try:
            return list(self.list_elements(monitor) or [])
        except Exception:
            return []

    def _look(self, capture):
        """A fresh look once the screen has settled: the screenshot, with
        Windows' element list made at the same time."""
        monitor = capture.monitor
        box: dict = {}
        worker = threading.Thread(target=lambda: box.__setitem__("found", self._elements(monitor)),
                                  name="clixy-guide-look", daemon=True)
        worker.start()
        fresh = self.capture()
        worker.join(timeout=2.0)
        found = box.get("found", [])
        if fresh is None:
            return capture, found
        if fresh.monitor != monitor:
            found = self._elements(fresh.monitor)
        return fresh, found

    # --- Know-how and remembered routes --------------------------------------------------

    def _know(self, goal: str, wins: list) -> str:
        front = next((w.exe for w in wins if w.front), "")
        if self.knowhow is None:
            return ""
        if front not in self._know_cache:
            try:
                self._know_cache[front] = self.knowhow(goal, front) or ""
            except Exception:
                self._know_cache[front] = ""
            self._log(f"GUIDE: know-how for {front or 'no app'}: {len(self._know_cache[front])} chars")
        return self._know_cache[front]

    def _route_hint(self, goal: str) -> str:
        if self.routes is None:
            return ""
        try:
            r = self.routes.recall(goal)
        except Exception:
            r = None
        if not r:
            return ""
        self._shortcut = r.get("shortcut", "") or self._shortcut
        self._log(f"GUIDE: remembered route from {r['goal']!r}")
        text = f'last time, "{r["goal"]}" worked like this: ' + "; ".join(r["steps"])
        if r.get("shortcut"):
            text += f'. the quick way: {r["shortcut"]}'
        return text + ". if the screen still fits, go straight there in fewer steps."

    def _remember(self, goal: str, route: list[str]) -> None:
        if self.routes is None or not route:
            return
        front = self._front_window()
        try:
            self.routes.record(goal, route, app=front.app if front is not None else "", shortcut=self._shortcut)
            self._log(f"GUIDE: remembered the route: {route}")
        except Exception:
            pass

    def _prompt(self, goal: str, done: list[str], lines: list[str], fix: str, wins: list | None = None,
                hint: str = "", know: str = "", plan: list | None = None) -> str:
        parts = [f'goal: "{goal.strip()}"']
        if self.about:
            parts.append(f"this computer: {self.about}")
        if wins:
            parts.append("open windows, front first:\n" + "\n".join(window_lines(wins)))
        parts.append("done so far: " + ("; ".join(f"{i}. {d}" for i, d in enumerate(done, 1)) if done else "nothing yet"))
        if hint:
            parts.append(hint)
        if plan:
            parts.append("your plan for the rest (check the screen still fits it): " + " | ".join(str(p) for p in plan))
        if know:
            parts.append("know-how (accurate; use the fastest route that fits):\n" + know)
        if lines:
            parts.append("clickable things on screen now (id, kind, name, box in screenshot pixels):\n" + "\n".join(lines))
        else:
            parts.append("windows didn't list the controls here, so use keys, or a box for what to click.")
        if fix:
            parts.append(fix)
        parts.append("what's the single next step?")
        return "\n\n".join(parts)

    @staticmethod
    def _from_plan(plan: list, ids: dict) -> "Step | None":
        """The plan's next step, if it's a click on something named exactly so on
        screen now (one element, or one that's clearly it): ready to show with no
        model call."""
        if not plan or plan[0].action != "click":
            return None
        want = plan[0].name.strip().lower()
        exact = [i for i, e in ids.items() if (e.name or "").strip().lower() == want]
        if not exact:
            exact = [i for i, e in ids.items() if (e.name or "").strip().lower().startswith(want) and len(want) >= 4]
        if len(exact) != 1:
            return None
        say = plan[0].say or f"now click {plan[0].name}"
        return Step(say=say, action="click", target=exact[0], label=plan[0].name[:40])

    def _ms(self, since: float) -> int:
        return round((self.clock() - since) * 1000)

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

    def _wait(self, step: Step, target: tuple | None, limit: float | None = None, unseen: str = "") -> str:
        """Until you've done the step: "done", "cancel", or "gone" (no sign of
        you, or ``limit`` seconds passed). It returns the moment you act; the
        settling afterwards is ``_settle``'s job. ``unseen`` names an admin
        window in front, whose clicks Windows hides: the nudge comes sooner."""
        start = self.clock()
        nudge_s = min(self.nudge_s, 12.0) if unseen else self.nudge_s
        window = self.foreground()
        want = combo_tokens(step.keys) if step.action == "keys" else frozenset()
        opens = ""
        if step.action == "keys":
            import apps
            opens = apps.SHORTCUT_WINDOWS.get(want, "")
            already = self._find_window(opens) if opens else None
            if already is not None and already.front:
                opens = ""   # it was in front before you pressed anything
        check_at = None
        nudged = False
        typed = 0
        acted = False   # you've clicked or pressed something since this step showed
        next_look = start + 0.3
        while True:
            if self._halted():
                return "cancel"
            if self._confirmed.is_set():   # "I did it" in the panel, or "next" said aloud
                self._confirmed.clear()
                return "done"
            if self._heard_event.is_set():   # "I can't find it"
                return "heard"
            event = self.watch.get(0.1)
            now = self.clock()
            if event is not None:
                kind = event[0]
                if kind == "click" and target is not None and _inside(event[1], event[2], target):
                    if step.action != "type":
                        return "done"
                elif kind == "combo":
                    keys = event[1]
                    if want and want <= keys:
                        return "done"
                    if keys == frozenset({"esc"}) and step.action != "keys":
                        return "cancel"
                    if step.action == "type":
                        typed += 1
                        if "enter" in keys or "tab" in keys:
                            return "done"
                acted = True
                check_at = now + 0.4
            if check_at is not None and now >= check_at:
                check_at = None
                if self.foreground() != window:
                    return "done"
                if step.action == "type" and typed >= max(1, len(step.text) // 2):
                    return "done"
            if now >= next_look:
                next_look = now + 0.3
                if (acted or step.action == "keys") and self.foreground() != window:
                    return "done"  # what you did (or the shortcut) opened something new
                if opens:          # the shortcut's window is up (keys into an admin window go unseen)
                    win = self._find_window(opens)
                    if win is not None and win.front:
                        return "done"
            if limit is not None and now - start >= limit:
                return "gone"
            if not nudged and now - start >= nudge_s:
                nudged = True
                if unseen:
                    self.note(f"I can't see clicks in {unseen.title()} (it runs as administrator). "
                              "Click outside it, then hold the keys and say next.")
                else:
                    self.note("Still with me? Do the step, or hold the keys and ask something new.")
            if now - start >= self.give_up_s:
                return "gone"

    @staticmethod
    def _move(step: Step, ids: dict) -> str:
        """The step as a move to remember: "press win+p", 'click "Duplicate"'."""
        if step.action == "keys":
            return f"press {step.keys}"
        if step.action == "type":
            return f'type "{step.text}"'
        if step.action == "click" and step.target in ids:
            return f'click "{ids[step.target].name}"'
        return f"click {step.label or 'the marked thing'}"

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
