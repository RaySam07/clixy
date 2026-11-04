"""Sketch notes: Clixy explains like a lecture on a clean note card.

Ask "what's going on here?" over a Fourier video, or "why does a wave wrap
into a flower?", and while the voice explains, a note card slides in and
Clixy sketches it stroke by stroke in handwriting: a title, a graph with its
axes and labels, the point that matters circled. When depth helps (an engine,
a cell, an orbit, a molecule) the note holds a real 3D model instead
(render3d.RealModel, a ready-made one from models3d/ or one Sonnet sculpts
from parts): rendered on the GPU like a studio photo, slowly rocking, its
moving parts moving, every part labelled, turnable by dragging. 3D maths
(vectors, surfaces) is drawn by scene3d.NoteModel. Two or three follow-up questions sit underneath
as buttons; Clear puts it all away. Every note is kept in ~/.clixy/notes.

The model writes the note as JSON (``PROMPT`` below): what to draw in the
note's own coordinates, never code. Curves can be given as a formula
("sin(3*x)"), which is evaluated by a tiny whitelist evaluator.

Threading: the note is asked for on a worker thread; the card is drawn on the
Qt thread.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import math
import random
import re
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap,
                         QPolygonF)
from PyQt6.QtWidgets import QWidget

import model3d
import render3d
import scene3d
import theme
from config import APP_HOME

NOTES_DIR = APP_HOME / "notes"

PROMPT = """\
you make a sketch note: a small, clean note card that teaches the idea behind the user's question, like a great lecturer sketching on paper while they talk. any subject: maths, science, history, language, business, code, how something works. the user sees their screen (the image) and asked a question out loud. a separate voice answers out loud; your note is what they look at while it does.

reply with json only, no other words:
{"topic": "2-4 words, the subject", "title": "the idea in 3-7 words", "subtitle": "one short line of context",
 "view": {"x": [min, max], "y": [min, max]},
 "items": [ ...what to draw, in order, like a teacher would... ],
 "followups": ["a short follow-up question", "another"]}

items are drawn one by one in the note's own coordinates (the view's x and y). each is one of:
{"type": "axes", "x_label": "time (s)", "y_label": "intensity", "ticks": true}
{"type": "function", "expr": "sin(2*pi*3*x)", "from": 0, "to": 1, "color": "blue", "label": "3 Hz"}  (x is the variable; + - * / ** and sin cos tan exp log sqrt abs pi e)
{"type": "curve", "points": [[x, y], ...], "color": "blue", "label": "..."}
{"type": "parametric", "x": "(1 + 0.5*cos(3*t))*cos(t)", "y": "(1 + 0.5*cos(3*t))*sin(t)", "from": 0, "to": 6.283, "color": "blue", "label": "..."}  (t is the variable: anything wound, polar or orbiting, like a wave wrapped around a circle, a spiral, an ellipse)
{"type": "dot", "at": [x, y], "label": "peak", "color": "red"}
{"type": "line", "from": [x, y], "to": [x, y], "dashed": true, "color": "grey", "label": "..."}
{"type": "arrow", "from": [x, y], "to": [x, y], "label": "..."}
{"type": "box", "from": [x, y], "to": [x, y], "label": "..."}
{"type": "circle", "at": [x, y], "r": 0.5, "label": "..."}
{"type": "text", "at": [x, y], "text": "a short note", "size": "small" | "normal" | "big"}

for ideas that aren't graphs, use one of these layouts instead (no view needed; it fills the note):
{"type": "steps", "steps": ["a stage", "the next", "..."]}               a process, in order (2-6 steps)
{"type": "cycle", "center": "water cycle", "stages": ["evaporation", "condensation", "rain", "collection"]}
{"type": "compare", "left": "mitosis", "right": "meiosis", "rows": [["2 cells", "4 cells"], ["identical", "varied"]]}
{"type": "timeline", "events": [["1789", "revolution begins"], ["1799", "napoleon takes power"]]}
{"type": "mindmap", "center": "photosynthesis", "nodes": ["sunlight", "water", "co2", "glucose", "oxygen"]}
{"type": "points", "points": ["a key fact", "another"]}                 3-5 short key points
pick the clearest form: a graph for quantities and functions, steps for processes, cycle for loops, compare for differences, timeline for history, mindmap for a topic's parts, points when it's a few key facts.

3d: when depth genuinely helps (anatomy, a machine or engine, a cell, a molecule, the solar system, a building, a plant, vectors, a 3d surface), make a real 3d model instead. clixy renders it like a studio photo (real materials, soft shadows), it rocks slowly and the user can turn it, and each part is named on a label beside it. clixy draws this 3d itself, so never say it can't and never point to another app. use "view": {"3d": true}, optional "turn" (degrees round) and "tilt" (degrees down).

ready-made models, hand-built and the best clixy can show. whenever the subject matches one, use it, and choose its labels to fit what the voice explains:
{"type": "model", "name": "heart", "labels": ["aorta", "left ventricle"], "cutaway": true, "highlight": ["left ventricle"]}
labels: from its list (5-8 read best; leave out to show all). cutaway: opens it up (only models marked "cutaway"). highlight: picks parts out, the rest go pale.
{LIBRARY}

anything else physical: sculpt it from parts like a 3d artist would, in real proportions, x y z with z up, the whole thing a few units across:
{"type": "part", "label": "piston", "color": "steel", "material": "metal", "shapes": [...], "blend": 0.1, "move": {...}}
shapes (each also takes "color", "subtract": true to carve it out of the shapes before it, "blend" for its own join):
{"shape": "sphere", "at": [x, y, z], "r": 1}   {"shape": "ellipsoid", "at": [x, y, z], "size": [rx, ry, rz], "turn": [x°, y°, z°]}
{"shape": "box", "at": [x, y, z], "size": [w, d, h], "round": 0.03}   {"shape": "rod", "from": [x, y, z], "to": [x, y, z], "r": 0.2, "r2": 0.1}  (rounded ends: limbs, horns, roots)
{"shape": "cylinder", "from": [x, y, z], "to": [x, y, z], "r": 0.5, "round": 0.02}   "cone": the same, "r2" is the top radius   {"shape": "torus", "at": [x, y, z], "r": 1, "tube": 0.2, "turn": [x°, y°, z°]}
{"shape": "tube", "points": [[x, y, z], ...], "r": [0.2, 0.15, 0.1]}  a smooth vessel, stem, cable or tail through the points, tapering
{"shape": "helix", "at": [x, y, z], "r": 0.5, "pitch": 0.3, "turns": 4, "tube": 0.05}   {"shape": "gear", "at": [x, y, z], "r": 0.6, "teeth": 18, "thickness": 0.2, "axis": [0, 1, 0]}
{"shape": "lathe", "at": [x, y, z], "profile": [[radius, z], ...]}  spun round z: vases, flasks, bells, bulbs, lenses, a volcano
{"shape": "extrude", "outline": [[x, y], ...], "height": 0.1, "round": 0.02, "smooth": true, "at": [x, y, z], "turn": [x°, y°, z°]}  leaves, petals, wings, blades, plates
part options: "blend" 0.1-0.4 melts its shapes into one smooth form (living things are never one sphere: blend several ellipsoids); "bumps": {"size": 0.3, "depth": 0.02} for a lumpy surface ("style": "ridged" for folds like a brain); "shell": 0.05 makes it hollow; "mirror": "x" makes it symmetric (model one side); "repeat": {"count": 12, "axis": "z"} copies it round an axis (petals, spikes, teeth, blades); "at", "turn", "scale" move the whole part; "labels": [{"text": "apex", "at": [x, y, z]}] names spots on it; "opacity".
materials: flesh, muscle, skin, bone, tooth, leaf, plastic, glossy, matte, rubber, ceramic, metal, steel, chrome, gold, copper, brass, glass, water, jelly, wood, rock, soil, glow (the sun, flames, leds), lava.
colors: any #rrggbb, or red, crimson, muscle, artery, flesh, pink, blue, vein, navy, sky, cyan, teal, green, leaf, lime, yellow, gold, orange, fat, brown, tan, wood, sand, skin, purple, violet, white, ivory, bone, grey, silver, steel, black, copper, brass, rock, lava, ice, water.
moving parts move: "move": {"spin": "z", "about": [x, y, z], "speed": 90} (degrees a second; orbits too), {"slide": [0, 0, 1], "amount": 0.3, "speed": 1} (a piston), {"swing": "x", "amount": 20, "speed": 0.5} (a pendulum, a valve), {"beat": 0.05, "speed": 1.2} (a heartbeat, breathing); "phase": 0-1 offsets one cycle from another.
{"type": "cut", "normal": [x, y, z], "at": [x, y, z]} cuts it open: everything on the normal's side goes, so the insides show (hollow parts: "shell", or carve with "subtract").
{"type": "molecule", "atoms": [["O", 0, 0, 0], ["H", 0.76, 0, 0.59], ["H", -0.76, 0, 0.59]], "bonds": [[0, 1], [0, 2]], "label": "water"}  real geometry in ångström, element colours; [0, 1, 2] is a double bond
a good model: 6 to 30 parts, each distinct part its own part, 5-8 labels on what the explanation talks about, real shapes, colours and materials.

3d maths (vectors, axes, surfaces, curves, fields) uses these items instead, never mixed with parts:
{"type": "axes3d", "labels": ["x", "y", "z"]}   {"type": "vector3d", "from": [0, 0, 0], "to": [1, 2, 1], "label": "a"}
{"type": "point3d", "at": [x, y, z], "label": "..."}   {"type": "line3d", "from": [x, y, z], "to": [x, y, z], "dashed": true}
{"type": "curve3d", "x": "2*cos(t)", "y": "2*sin(t)", "z": "0", "from": 0, "to": 6.283, "travel": true}  (t is the variable; travel: a ball moves along it)
{"type": "surface", "expr": "x^2 - y^2", "x": [-2, 2], "y": [-2, 2], "label": "saddle"}  shaded and coloured by height
{"type": "field", "x": "-y", "y": "x", "z": "0", "range": 2}  arrows showing a field or a flow (x, y, z are the variables)
in any 3d note: {"type": "text", "text": "the key insight"} is written under the model.

colors: ink, blue, red, green, orange, purple, grey (3d also: teal, yellow, white, black, brown, silver, gold).
keep it clean and true: 3 to 10 items (a 3d model of parts up to 30), labels of 1-4 words, exact maths (use "function" for real curves), nothing cluttered. text items are the key insight in a few words, like "peak = frequency present". draw what the idea really looks like: if it is wrapped, rotated or polar, draw it that way with "parametric", not a plain wave beside it. geometry stays round: circles and wound curves are drawn with equal x and y scales. leave room: no two labels in the same spot, and put text items in empty space, away from curves. followups are 2 or 3 natural next questions, under 6 words each.
make a note for anything someone is trying to learn or understand. only for small talk, a yes or no, or "where do i click", reply {"skip": true}.\
"""



def _library_lines() -> str:
    """The ready-made 3D models, one a line, for the prompt."""
    lines = []
    for name, info in sorted(model3d.library_index().items()):
        cut = " (cutaway)" if info["cutaway"] else ""
        lines.append(f"{name}: {str(info['title']).lower()}{cut}. labels: {', '.join(info['labels'][:14])}")
    return "\n".join(lines) if lines else "(none)"


PROMPT = PROMPT.replace("{LIBRARY}", _library_lines())

_SKETCH_WORDS = re.compile(
    r"\b(explain|explaining|why|how (?:does|do|is|are|can|did|was|were|would|many|much)|what(?:'s| is| are| was| were"
    r"| does| do| did| happens| causes| makes)?|who (?:is|was|were)|when (?:did|was|is)|teach|learn|study|understand"
    r"|intuition|derive|prove|solve|calculate|visuali[sz]e|graph|plot|sketch|diagram|draw me|lecture|summar\w+"
    r"|describe|define|definition|meaning|difference between|compare|vs\.?|versus|history of|tell me about"
    r"|break (?:it|this) down|walk me through|show me (?:how|why|what)|help me (?:understand|learn|revise|study)"
    r"|3-?d|three[- ]d\w*|in three dimensions|model of)\b",
    re.IGNORECASE,
)
_NOT_LEARNING = re.compile(
    r"^\s*(?:hey|hi|hello|thanks|thank you|ok|okay|open|launch|start|close|play|pause|stop|go to|click|where (?:is|are|do|can))\b",
    re.IGNORECASE,
)


def wants_sketch(question: str) -> bool:
    """Anything someone is trying to learn or understand gets a note (the
    model can still pass); small talk and "open/where is" don't."""
    q = (question or "").strip()
    return len(q.split()) >= 3 and not _NOT_LEARNING.search(q) and bool(_SKETCH_WORDS.search(q))


# --- Reading the model's note -------------------------------------------------------------

def parse_note(text: str) -> dict | None:
    """The note in a reply, checked and trimmed; None if there's none to draw."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        note = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(note, dict) or note.get("skip") or not isinstance(note.get("items"), list):
        return None
    items = [i for i in note["items"] if isinstance(i, dict) and isinstance(i.get("type"), str)][:30]
    if not items:
        return None
    note["items"] = items
    note["title"] = str(note.get("title") or "")[:70]
    note["subtitle"] = str(note.get("subtitle") or "")[:90]
    note["topic"] = str(note.get("topic") or "")[:40]
    note["followups"] = [str(f)[:48] for f in (note.get("followups") or []) if str(f).strip()][:3]
    return note


_FUNCS = {name: getattr(math, name) for name in ("sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh",
                                                  "tanh", "exp", "log", "log10", "sqrt", "floor", "ceil")}
_FUNCS.update(abs=abs, min=min, max=max, sign=lambda v: (v > 0) - (v < 0))
_CONSTS = {"pi": math.pi, "e": math.e}
_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant, ast.Add, ast.Sub,
          ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.FloorDiv)


def compile_expr(expr: str, names: tuple[str, ...] = ("x",)):
    """A safe formula -> a function of ``names``; None if it isn't a plain formula."""
    expr = str(expr).replace("^", "**").strip()[:200]
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            return None
        if isinstance(node, ast.Name) and node.id not in _FUNCS and node.id not in _CONSTS and node.id not in names:
            return None
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            return None
        if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, (int, float))):
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and abs(node.right.value) > 12:
                return None
    # Every number becomes a float, so a huge power overflows at once
    # (10**10**10 as integers would never finish).
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            node.value = float(node.value)
    code = compile(tree, "<note>", "eval")
    env = {"__builtins__": {}, **_FUNCS, **_CONSTS}

    def run(*values: float) -> float:
        try:
            out = float(eval(code, env, dict(zip(names, values))))  # noqa: S307 - whitelisted above
        except (ArithmeticError, ValueError, TypeError, OverflowError):
            return math.nan
        return out if math.isfinite(out) else math.nan
    return run


# --- Turning a note into strokes ----------------------------------------------------------

COLORS = {"ink": QColor(42, 42, 51), "blue": QColor(47, 111, 224), "red": QColor(226, 76, 56),
          "green": QColor(40, 150, 90), "orange": QColor(232, 132, 36), "purple": QColor(122, 90, 245),
          "grey": QColor(138, 138, 150), "gray": QColor(138, 138, 150)}


class Stroke:
    """One thing drawn: a pen path (maybe dashed, maybe with an arrowhead), a
    dot, or handwriting. ``length`` paces the drawing."""

    def __init__(self, kind: str, points: list[QPointF] | None = None, *, color: QColor = COLORS["ink"],
                 width: float = 2.2, dashed: bool = False, head: bool = False, text: str = "",
                 at: QPointF | None = None, size: float = 13.0, align: str = "left", movable: bool = False) -> None:
        self.kind, self.points, self.color, self.width = kind, points or [], color, width
        self.dashed, self.head, self.text, self.at, self.size, self.align = dashed, head, text, at, size, align
        self.movable = movable  # a label that may move to a clear spot (_untangle)
        self.length = self._length()
        self.start = 0.0   # seconds into the drawing
        self.duration = 0.0

    def _length(self) -> float:
        if self.kind == "text":
            return 9.0 * max(1, len(self.text))
        if self.kind == "dot":
            return 30.0
        return sum(math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(self.points, self.points[1:]))


def _wobble(points: list[QPointF], seed: int, amount: float = 0.7) -> list[QPointF]:
    """A hand's slight wobble along a path (the same every time it's drawn)."""
    if len(points) < 2:
        return points
    rng = random.Random(seed)
    out: list[QPointF] = []
    phase, freq = rng.uniform(0, 6.28), rng.uniform(0.04, 0.07)
    dist = 0.0
    for i, p in enumerate(points):
        if i:
            dist += math.hypot(p.x() - points[i - 1].x(), p.y() - points[i - 1].y())
        a = points[max(0, i - 1)]
        b = points[min(len(points) - 1, i + 1)]
        dx, dy = b.x() - a.x(), b.y() - a.y()
        norm = math.hypot(dx, dy) or 1.0
        off = amount * math.sin(phase + dist * freq)
        out.append(QPointF(p.x() - dy / norm * off, p.y() + dx / norm * off))
    return out


def _resample(points: list[QPointF], step: float = 5.0) -> list[QPointF]:
    out: list[QPointF] = []
    for a, b in zip(points, points[1:]):
        n = max(1, int(math.hypot(b.x() - a.x(), b.y() - a.y()) / step))
        out += [QPointF(a.x() + (b.x() - a.x()) * k / n, a.y() + (b.y() - a.y()) * k / n) for k in range(n)]
    if points:
        out.append(points[-1])
    return out


def _color(item: dict, default: str = "ink") -> QColor:
    return QColor(COLORS.get(str(item.get("color", default)).lower(), COLORS[default]))


def _pt(value) -> tuple[float, float] | None:
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        return None


def build(note: dict, canvas: QRectF, angle: float = 0.0) -> list[Stroke]:
    """The note's items as strokes inside ``canvas`` (card px), in order.
    ``angle`` turns a 3D note around its vertical axis (degrees)."""
    if (note.get("view") or {}).get("3d"):
        return _build_3d(note, canvas, angle)
    layout = next((i for i in note["items"] if i.get("type") in LAYOUTS), None)
    if layout is not None:
        return _pace(_keep_inside(LAYOUTS[layout["type"]](layout, canvas), canvas))
    view = note.get("view") or {}
    try:
        x0, x1 = (float(v) for v in view.get("x", [0, 10]))
        y0, y1 = (float(v) for v in view.get("y", [0, 10]))
    except (TypeError, ValueError):
        x0, x1, y0, y1 = 0.0, 10.0, 0.0, 10.0
    if x1 <= x0:
        x1 = x0 + 1
    if y1 <= y0:
        y1 = y0 + 1
    axes = next((i for i in note["items"] if i.get("type") == "axes"), None)
    ticks = bool(axes and axes.get("ticks"))
    plot = canvas.adjusted(34 if axes else 10, 22 if axes else 10, -14, (-44 if ticks else -30) if axes else -10)
    if view.get("equal") or any(i.get("type") in ("circle", "parametric") for i in note["items"]):
        # Circles and wound curves stay round: one scale for both axes, and the
        # roomier axis shows more of the view around the same centre.
        k = min(plot.width() / (x1 - x0), plot.height() / (y1 - y0))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        x0, x1 = cx - plot.width() / k / 2, cx + plot.width() / k / 2
        y0, y1 = cy - plot.height() / k / 2, cy + plot.height() / k / 2

    def to(x: float, y: float) -> QPointF:
        return QPointF(plot.left() + (x - x0) / (x1 - x0) * plot.width(),
                       plot.bottom() - (y - y0) / (y1 - y0) * plot.height())

    strokes: list[Stroke] = []
    for n, item in enumerate(note["items"]):
        kind, seed = item.get("type"), n * 7919 + 13
        label = str(item.get("label") or "")[:40]
        color = _color(item)
        if kind == "axes":
            ox = min(max(0.0, x0), x1) if item.get("origin") else x0
            oy = min(max(0.0, y0), y1) if item.get("origin") else y0
            strokes.append(Stroke("line", _wobble(_resample([to(x0, oy), to(x1, oy)]), seed, 0.5), head=True, width=2.0))
            strokes.append(Stroke("line", _wobble(_resample([to(ox, y0), to(ox, y1)]), seed + 1, 0.5), head=True, width=2.0))
            if item.get("ticks"):
                for value in _ticks(x0, x1):
                    p = to(value, oy)
                    strokes.append(Stroke("line", [QPointF(p.x(), p.y() - 3), QPointF(p.x(), p.y() + 3)], width=1.4))
                    strokes.append(Stroke("text", text=_num(value), at=QPointF(p.x(), p.y() + 15), size=10, align="center",
                                          color=COLORS["grey"]))
                for value in _ticks(y0, y1):
                    p = to(ox, value)
                    strokes.append(Stroke("line", [QPointF(p.x() - 3, p.y()), QPointF(p.x() + 3, p.y())], width=1.4))
                    strokes.append(Stroke("text", text=_num(value), at=QPointF(p.x() - 8, p.y() + 4), size=10, align="right",
                                          color=COLORS["grey"]))
            if item.get("x_label"):
                below = 38 if item.get("ticks") else 24
                strokes.append(Stroke("text", text=str(item["x_label"])[:30], at=QPointF(plot.right(), plot.bottom() + below),
                                      size=13, align="right"))
            if item.get("y_label"):
                strokes.append(Stroke("text", text=str(item["y_label"])[:30], at=QPointF(plot.left() + 8, plot.top() - 6),
                                      size=13))
        elif kind in ("function", "curve", "parametric"):
            pts: list[QPointF] = []
            if kind == "function":
                fn = compile_expr(item.get("expr", ""))
                if fn is None:
                    continue
                a = float(item.get("from", x0))
                b = float(item.get("to", x1))
                for k in range(241):
                    x = a + (b - a) * k / 240
                    y = fn(x)
                    if not math.isnan(y):
                        pts.append(to(x, max(y0 - (y1 - y0), min(y1 + (y1 - y0), y))))
            elif kind == "parametric":
                fx, fy = compile_expr(item.get("x", ""), ("t",)), compile_expr(item.get("y", ""), ("t",))
                if fx is None or fy is None:
                    continue
                try:
                    a, b = float(item.get("from", 0)), float(item.get("to", 2 * math.pi))
                except (TypeError, ValueError):
                    continue
                for k in range(481):
                    t = a + (b - a) * k / 480
                    x, y = fx(t), fy(t)
                    if not (math.isnan(x) or math.isnan(y)):
                        pts.append(to(max(x0 - (x1 - x0), min(x1 + (x1 - x0), x)),
                                      max(y0 - (y1 - y0), min(y1 + (y1 - y0), y))))
            else:
                pts = [to(*p) for p in (_pt(v) for v in (item.get("points") or [])[:400]) if p is not None]
                pts = _resample(pts, 4.0)
            if len(pts) < 2:
                continue
            strokes.append(Stroke("line", _wobble(pts, seed, 0.6), color=_color(item, "blue"), width=2.6))
            if label:
                end = max(pts, key=lambda q: -q.y()) if kind == "function" else pts[-1]
                strokes.append(Stroke("text", text=label, at=QPointF(end.x() + 6, end.y() - 8), size=13,
                                      color=_color(item, "blue"), movable=True))
        elif kind == "dot":
            p = _pt(item.get("at"))
            if p is None:
                continue
            c = to(*p)
            strokes.append(Stroke("dot", at=c, color=_color(item, "ink")))
            if label:
                strokes.append(Stroke("text", text=label, at=QPointF(c.x() + 9, c.y() - 9), size=14, movable=True))
        elif kind in ("line", "arrow"):
            a, b = _pt(item.get("from")), _pt(item.get("to"))
            if a is None or b is None:
                continue
            pa, pb = to(*a), to(*b)
            strokes.append(Stroke("line", _wobble(_resample([pa, pb]), seed, 0.5), color=_color(item, "ink"),
                                  dashed=bool(item.get("dashed")), head=kind == "arrow", width=2.0))
            if label:
                mid = QPointF((pa.x() + pb.x()) / 2, (pa.y() + pb.y()) / 2 - 8)
                strokes.append(Stroke("text", text=label, at=mid, size=12, align="center", movable=True))
        elif kind == "box":
            a, b = _pt(item.get("from")), _pt(item.get("to"))
            if a is None or b is None:
                continue
            pa, pb = to(*a), to(*b)
            corners = [pa, QPointF(pb.x(), pa.y()), pb, QPointF(pa.x(), pb.y()), pa]
            strokes.append(Stroke("line", _wobble(_resample(corners), seed, 0.7), color=_color(item, "red"),
                                  dashed=bool(item.get("dashed")), width=2.0))
            if label:
                strokes.append(Stroke("text", text=label, at=QPointF(min(pa.x(), pb.x()), min(pa.y(), pb.y()) - 6), size=12,
                                      movable=True))
        elif kind == "circle":
            p = _pt(item.get("at"))
            if p is None:
                continue
            c = to(*p)
            r = abs(float(item.get("r", 0.5))) / (x1 - x0) * plot.width()
            ring = [QPointF(c.x() + r * math.cos(2 * math.pi * k / 48 + 0.4), c.y() + r * math.sin(2 * math.pi * k / 48 + 0.4))
                    for k in range(52)]
            strokes.append(Stroke("line", _wobble(ring, seed, 0.8), color=_color(item, "red"), width=2.2))
            if label:
                strokes.append(Stroke("text", text=label, at=QPointF(c.x() + r * 0.72 + 4, c.y() - r * 0.72 - 4), size=12,
                                      movable=True))
        elif kind == "text":
            p = _pt(item.get("at"))
            if p is None or not item.get("text"):
                continue
            size = {"small": 12, "big": 18}.get(str(item.get("size")), 14)
            strokes.append(Stroke("text", text=str(item["text"])[:60], at=to(*p), size=size, color=_color(item),
                                  movable=True))
    return _pace(_keep_inside(_untangle(strokes, canvas), canvas))


# --- Layouts for learning anything: they fill the canvas themselves ----------------------

def _texts(values, limit: int, length: int = 34) -> list[str]:
    return [str(v)[:length] for v in (values or []) if str(v).strip()][:limit]


def _bubble(rect: QRectF, seed: int, color: QColor, round_: bool = False) -> Stroke:
    if round_:
        c, rx, ry = rect.center(), rect.width() / 2, rect.height() / 2
        pts = [QPointF(c.x() + rx * math.cos(2 * math.pi * k / 48 + 0.3), c.y() + ry * math.sin(2 * math.pi * k / 48 + 0.3))
               for k in range(51)]
    else:
        r = rect
        pts = _resample([r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft(), r.topLeft()])
    return Stroke("line", _wobble(pts, seed, 0.7), color=color, width=2.0)


def _arrow(a: QPointF, b: QPointF, seed: int, color: QColor = COLORS["ink"]) -> Stroke:
    return Stroke("line", _wobble(_resample([a, b]), seed, 0.4), color=color, head=True, width=1.9)


def _label_in(rect: QRectF, text: str, size: float = 13.0, color: QColor = COLORS["ink"]) -> Stroke:
    fit = size
    while fit > 9 and QFontMetricsF(theme.hand_font(fit)).horizontalAdvance(text) > rect.width() - 10:
        fit -= 1
    return Stroke("text", text=text, at=QPointF(rect.center().x(), rect.center().y() + fit * 0.38), size=fit,
                  align="center", color=color)


def _steps(item: dict, canvas: QRectF) -> list[Stroke]:
    """A process: numbered boxes joined by arrows, in a row (or two)."""
    steps = _texts(item.get("steps"), 6, 30)
    if not steps:
        return []
    per_row = len(steps) if len(steps) <= 3 else math.ceil(len(steps) / 2)
    rows = math.ceil(len(steps) / per_row)
    gap, h = 26.0, 52.0
    w = (canvas.width() - gap * (per_row - 1)) / per_row
    top = canvas.top() + (canvas.height() - rows * h - (rows - 1) * 34) / 2
    out, prev = [], None
    for k, text in enumerate(steps):
        row, col = divmod(k, per_row)
        if row % 2:
            col = per_row - 1 - col  # snake back so the arrows stay short
        box = QRectF(canvas.left() + col * (w + gap), top + row * (h + 34), w, h)
        color = COLORS["blue"] if k == 0 else COLORS["ink"]
        out.append(_bubble(box, 31 * k + 5, color))
        out.append(Stroke("text", text=str(k + 1), at=QPointF(box.left() + 9, box.top() + 15), size=11,
                          color=COLORS["red"]))
        out.append(_label_in(box.adjusted(8, 4, -4, 0), text))
        if prev is not None:
            if prev.top() == box.top():
                a = QPointF(prev.right() + 3, prev.center().y()) if box.left() > prev.left() else QPointF(prev.left() - 3, prev.center().y())
                b = QPointF(box.left() - 3, box.center().y()) if box.left() > prev.left() else QPointF(box.right() + 3, box.center().y())
            else:
                a, b = QPointF(prev.center().x(), prev.bottom() + 3), QPointF(box.center().x(), box.top() - 3)
            out.insert(len(out) - 3, _arrow(a, b, 17 * k))
        prev = box
    return out


def _cycle(item: dict, canvas: QRectF) -> list[Stroke]:
    """Things that go round: stages on a loop with arrows between them."""
    stages = _texts(item.get("stages") or item.get("steps"), 6, 24)
    if len(stages) < 2:
        return _steps({"steps": stages}, canvas)
    c = canvas.center()
    rx, ry = canvas.width() * 0.36, canvas.height() * 0.36
    n = len(stages)
    spots = [QPointF(c.x() + rx * math.cos(-math.pi / 2 + 2 * math.pi * k / n),
                     c.y() + ry * math.sin(-math.pi / 2 + 2 * math.pi * k / n)) for k in range(n)]
    out = []
    centre = str(item.get("center") or item.get("label") or "")[:24]
    if centre:
        out.append(Stroke("text", text=centre, at=QPointF(c.x(), c.y() + 6), size=15, align="center",
                          color=COLORS["blue"]))
    for k, (spot, text) in enumerate(zip(spots, stages)):
        w = min(150.0, QFontMetricsF(theme.hand_font(13)).horizontalAdvance(text) + 24)
        box = QRectF(spot.x() - w / 2, spot.y() - 17, w, 34)
        out.append(_bubble(box, 13 * k + 3, COLORS["ink"], round_=True))
        out.append(_label_in(box, text))
        nxt = spots[(k + 1) % n]
        a0 = math.atan2(spot.y() - c.y(), spot.x() - c.x()) + 0.42
        a1 = math.atan2(nxt.y() - c.y(), nxt.x() - c.x()) - 0.42
        if a1 < a0:
            a1 += 2 * math.pi
        arc = [QPointF(c.x() + rx * 0.98 * math.cos(a0 + (a1 - a0) * u / 16), c.y() + ry * 0.98 * math.sin(a0 + (a1 - a0) * u / 16))
               for u in range(17)]
        out.append(Stroke("line", _wobble(arc, 7 * k, 0.4), color=COLORS["blue"], head=True, width=1.9))
    return out


def _compare(item: dict, canvas: QRectF) -> list[Stroke]:
    """Two things side by side: headings, a line down the middle, rows."""
    left, right = str(item.get("left", ""))[:24], str(item.get("right", ""))[:24]
    rows = [r for r in (item.get("rows") or []) if isinstance(r, (list, tuple)) and len(r) >= 2][:5]
    mid = canvas.center().x()
    out = [Stroke("text", text=left, at=QPointF(canvas.left() + canvas.width() / 4, canvas.top() + 16), size=16,
                  align="center", color=COLORS["blue"]),
           Stroke("text", text=right, at=QPointF(canvas.left() + canvas.width() * 3 / 4, canvas.top() + 16), size=16,
                  align="center", color=COLORS["red"]),
           Stroke("line", _wobble(_resample([QPointF(canvas.left(), canvas.top() + 26), QPointF(canvas.right(), canvas.top() + 26)]), 3, 0.5)),
           Stroke("line", _wobble(_resample([QPointF(mid, canvas.top() + 4), QPointF(mid, canvas.bottom())]), 5, 0.5))]
    step = (canvas.height() - 34) / max(1, len(rows))
    for k, row in enumerate(rows):
        y = canvas.top() + 34 + step * k + step / 2 + 4
        for col, text in enumerate(row[:2]):
            cell = QRectF(canvas.left() + col * canvas.width() / 2 + 6, y - 14, canvas.width() / 2 - 12, 22)
            out.append(_label_in(cell, str(text)[:40], 13))
    return out


def _timeline(item: dict, canvas: QRectF) -> list[Stroke]:
    """Events in order along a line: dates above, what happened below."""
    events = [e for e in (item.get("events") or []) if isinstance(e, (list, tuple)) and len(e) >= 2][:6]
    if not events:
        return []
    y = canvas.center().y()
    out = [Stroke("line", _wobble(_resample([QPointF(canvas.left(), y), QPointF(canvas.right(), y)]), 9, 0.5), head=True)]
    step = canvas.width() / (len(events) + 0.4)
    for k, (when, what) in enumerate(events):
        x = canvas.left() + step * (k + 0.7)
        out.append(Stroke("dot", at=QPointF(x, y), color=COLORS["red"] if k == len(events) - 1 else COLORS["blue"]))
        out.append(Stroke("text", text=str(when)[:14], at=QPointF(x, y - 14), size=14, align="center", color=COLORS["blue"]))
        below = QRectF(x - step / 2 + 4, y + 10 + (k % 2) * 24, step - 8, 24)
        out.append(_label_in(below, str(what)[:30], 12))
    return out


def _mindmap(item: dict, canvas: QRectF) -> list[Stroke]:
    """A topic in the middle and its parts around it."""
    centre = str(item.get("center") or "")[:24]
    nodes = _texts(item.get("nodes"), 6, 22)
    c = canvas.center()
    cw = min(170.0, QFontMetricsF(theme.hand_font(16)).horizontalAdvance(centre) + 30)
    hub = QRectF(c.x() - cw / 2, c.y() - 20, cw, 40)
    out = [_bubble(hub, 11, COLORS["blue"], round_=True), _label_in(hub, centre, 16, COLORS["blue"])]
    rx, ry = canvas.width() * 0.38, canvas.height() * 0.38
    for k, text in enumerate(nodes):
        a = -math.pi / 2 + 2 * math.pi * k / max(1, len(nodes))
        spot = QPointF(c.x() + rx * math.cos(a), c.y() + ry * math.sin(a))
        w = min(140.0, QFontMetricsF(theme.hand_font(13)).horizontalAdvance(text) + 22)
        box = QRectF(spot.x() - w / 2, spot.y() - 15, w, 30)
        start = QPointF(c.x() + (cw / 2) * math.cos(a), c.y() + 20 * math.sin(a))
        end = QPointF(spot.x() - (w / 2 + 2) * math.cos(a), spot.y() - 17 * math.sin(a))
        out.append(Stroke("line", _wobble(_resample([start, end]), 19 * k, 0.5), color=COLORS["grey"], width=1.7))
        out.append(_bubble(box, 23 * k + 1, COLORS["ink"]))
        out.append(_label_in(box, text))
    return out


def _points(item: dict, canvas: QRectF) -> list[Stroke]:
    """Key points, handwritten, each with a tick."""
    points = _texts(item.get("points"), 5, 52)
    step = min(36.0, canvas.height() / max(1, len(points)))
    top = canvas.top() + (canvas.height() - step * len(points)) / 2 + step * 0.6
    out = []
    for k, text in enumerate(points):
        y = top + step * k
        x = canvas.left() + 12
        tick = [QPointF(x, y - 5), QPointF(x + 5, y), QPointF(x + 14, y - 12)]
        out.append(Stroke("line", tick, color=COLORS["green"], width=2.4))
        out.append(Stroke("text", text=text, at=QPointF(x + 26, y), size=15))
    return out


LAYOUTS = {"steps": _steps, "cycle": _cycle, "compare": _compare, "timeline": _timeline,
           "mindmap": _mindmap, "points": _points}


def _text_rect(s: Stroke, at: QPointF | None = None) -> QRectF:
    """The box a line of handwriting covers (``at`` is its baseline anchor)."""
    at = at or s.at
    width = QFontMetricsF(theme.hand_font(s.size)).horizontalAdvance(s.text)
    left = at.x() - (width if s.align == "right" else width / 2 if s.align == "center" else 0)
    return QRectF(left, at.y() - s.size * 0.95, width, s.size * 1.3)


_CELL = 6.0
"""Grid size for counting how much ink a label would sit on."""


def _untangle(strokes: list[Stroke], canvas: QRectF) -> list[Stroke]:
    """Labels move to the nearest clear spot: off other labels first, then off
    the drawn lines, staying on the canvas. Ticks and axis names stay put."""
    ink: dict[tuple[int, int], int] = {}
    for s in strokes:
        for q in s.points if s.kind == "line" else [s.at] if s.kind == "dot" else []:
            cell = (int(q.x() // _CELL), int(q.y() // _CELL))
            ink[cell] = ink.get(cell, 0) + 1

    def ink_under(r: QRectF) -> int:
        return sum(ink.get((cx, cy), 0)
                   for cx in range(int(r.left() // _CELL), int(r.right() // _CELL) + 1)
                   for cy in range(int(r.top() // _CELL), int(r.bottom() // _CELL) + 1))

    bounds = canvas.adjusted(-6, -4, 6, 4)
    taken = [_text_rect(s) for s in strokes if s.kind == "text" and not s.movable and s.at is not None]
    offsets = [(0.0, 0.0)] + [(dx * d, dy * d) for d in (6, 12, 18, 26, 36, 48, 62)
                              for dx, dy in ((0, -1), (0, 1), (1, 0), (-1, 0), (1, -1), (-1, -1), (1, 1), (-1, 1))]
    for s in strokes:
        if s.kind != "text" or not s.movable or s.at is None:
            continue
        best, best_score = s.at, math.inf
        for dx, dy in offsets:
            at = QPointF(s.at.x() + dx, s.at.y() + dy)
            r = _text_rect(s, at)
            pad = r.adjusted(-3, -2, 3, 2)
            # Never on another label; a little ink is better than a label far from its thing.
            score = (1000 * sum(pad.intersects(t) for t in taken) + 2.5 * ink_under(r)
                     + (800 if not bounds.contains(r) else 0) + 0.9 * math.hypot(dx, dy))
            if score < best_score:
                best, best_score = at, score
        s.at = best
        taken.append(_text_rect(s))
    return strokes


def _keep_inside(strokes: list[Stroke], canvas: QRectF) -> list[Stroke]:
    """Handwriting that would run off the canvas is nudged back onto it."""
    for s in strokes:
        if s.kind != "text" or s.at is None:
            continue
        r = _text_rect(s)
        shift = min(0.0, canvas.right() + 6 - r.right()) or max(0.0, canvas.left() - 6 - r.left())
        lift = min(0.0, canvas.bottom() + 6 - r.bottom()) or max(0.0, canvas.top() - 4 - r.top())
        if shift or lift:
            s.at = QPointF(s.at.x() + shift, s.at.y() + lift)
    return strokes


def _build_3d(note: dict, canvas: QRectF, angle: float) -> list[Stroke]:
    view = note.get("view") or {}
    span = float(view.get("range", 2) or 2)
    centre = QPointF(canvas.center().x(), canvas.center().y() + canvas.height() * 0.16)
    scale = min(canvas.width(), canvas.height()) / (1.75 * span)
    az, el = math.radians(-35 + angle), math.radians(24)

    def to(x: float, y: float, z: float) -> QPointF:
        rx = x * math.cos(az) - y * math.sin(az)
        ry = x * math.sin(az) + y * math.cos(az)
        sx = rx
        sy = -(z * math.cos(el) - ry * math.sin(el))
        return QPointF(centre.x() + sx * scale, centre.y() + sy * scale)

    def p3(value) -> tuple[float, float, float] | None:
        try:
            return float(value[0]), float(value[1]), float(value[2])
        except (TypeError, ValueError, IndexError):
            return None

    strokes: list[Stroke] = []
    for n, item in enumerate(note["items"]):
        kind, label, seed = item.get("type"), str(item.get("label") or "")[:30], n * 104729 + 7
        if kind == "axes3d":
            names = (item.get("labels") or ["x", "y", "z"]) + ["", "", ""]
            for k, end in enumerate(((span, 0, 0), (0, span, 0), (0, 0, span))):
                tip = to(*end)
                strokes.append(Stroke("line", _resample([to(0, 0, 0), tip]), head=True, width=1.8, color=COLORS["grey"]))
                strokes.append(Stroke("text", text=str(names[k])[:12], at=QPointF(tip.x() + 6, tip.y() - 4), size=13,
                                      color=COLORS["grey"]))
        elif kind in ("vector3d", "line3d"):
            a, b = p3(item.get("from", [0, 0, 0])), p3(item.get("to"))
            if a is None or b is None:
                continue
            strokes.append(Stroke("line", _wobble(_resample([to(*a), to(*b)]), seed, 0.4), head=kind == "vector3d",
                                  dashed=bool(item.get("dashed")), color=_color(item, "blue" if kind == "vector3d" else "ink"),
                                  width=2.4 if kind == "vector3d" else 1.8))
            if label:
                tip = to(*b)
                strokes.append(Stroke("text", text=label, at=QPointF(tip.x() + 7, tip.y() - 6), size=14,
                                      color=_color(item, "blue")))
        elif kind == "point3d":
            p = p3(item.get("at"))
            if p is None:
                continue
            c = to(*p)
            strokes.append(Stroke("dot", at=c, color=_color(item, "red")))
            if label:
                strokes.append(Stroke("text", text=label, at=QPointF(c.x() + 8, c.y() - 8), size=13))
        elif kind == "curve3d":
            fx, fy, fz = (compile_expr(item.get(k, "0"), ("t",)) for k in ("x", "y", "z"))
            if None in (fx, fy, fz):
                continue
            a, b = float(item.get("from", 0)), float(item.get("to", 6.28))
            pts = [to(fx(t), fy(t), fz(t)) for t in (a + (b - a) * k / 200 for k in range(201))]
            pts = [p for p in pts if not (math.isnan(p.x()) or math.isnan(p.y()))]
            if len(pts) > 1:
                strokes.append(Stroke("line", pts, color=_color(item, "blue"), width=2.4))
        elif kind == "box3d":
            c, size = p3(item.get("at", [0, 0, 0])), p3(item.get("size", [1, 1, 1]))
            if c is None or size is None:
                continue
            hx, hy, hz = (abs(v) / 2 for v in size)
            corner = [(c[0] + sx * hx, c[1] + sy * hy, c[2] + sz * hz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
            for i, j in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)):
                strokes.append(Stroke("line", _resample([to(*corner[i]), to(*corner[j])]), color=_color(item), width=1.9))
        elif kind == "sphere":
            c = p3(item.get("at", [0, 0, 0]))
            if c is None:
                continue
            r = abs(float(item.get("r", 1)))
            for plane in range(3):
                ring = []
                for k in range(61):
                    a = 2 * math.pi * k / 60
                    u, v = r * math.cos(a), r * math.sin(a)
                    xyz = [(u, v, 0), (u, 0, v), (0, u, v)][plane]
                    ring.append(to(c[0] + xyz[0], c[1] + xyz[1], c[2] + xyz[2]))
                strokes.append(Stroke("line", ring, color=_color(item, "purple" if plane else "blue"), width=1.7))
        elif kind == "surface":
            fn = compile_expr(item.get("expr", "0"), ("x", "y"))
            if fn is None:
                continue
            grid = [i * span / 6 - span for i in range(13)]
            for fixed_x in (True, False):
                for g in grid[::2]:
                    pts = [to(g, h, fn(g, h)) if fixed_x else to(h, g, fn(h, g)) for h in grid]
                    pts = [p for p in pts if not math.isnan(p.y())]
                    if len(pts) > 1:
                        strokes.append(Stroke("line", _resample(pts, 4.0), color=_color(item, "green"), width=1.4))
    return _pace(_keep_inside(strokes, canvas))


def _ticks(a: float, b: float) -> list[float]:
    span = b - a
    step = 10 ** math.floor(math.log10(span / 4)) if span > 0 else 1
    for m in (1, 2, 5, 10):
        if span / (step * m) <= 6:
            step *= m
            break
    first = math.ceil(a / step) * step
    out = []
    v = first
    while v <= b + 1e-9 and len(out) < 8:
        if abs(v - a) > 1e-9:
            out.append(round(v, 10))
        v += step
    return out


def _num(v: float) -> str:
    return str(int(v)) if abs(v - round(v)) < 1e-9 else f"{v:g}"


def _pace(strokes: list[Stroke], speed: float = 420.0) -> list[Stroke]:
    """When each stroke draws: one after another, like a hand, at ``speed`` px/s."""
    t = 0.0
    for s in strokes:
        s.start = t
        s.duration = max(0.12, min(1.4, s.length / speed))
        t += s.duration + (0.08 if s.kind != "text" else 0.04)
    return strokes


def save_note(note: dict, image) -> Path | None:
    """Keep the note (its picture and its data) in the notes folder."""
    try:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H%M")
        name = re.sub(r"[^\w\- ]+", "", note.get("title") or "note").strip()[:50] or "note"
        base = NOTES_DIR / f"{stamp} {name}"
        base.with_suffix(".json").write_text(json.dumps(note, indent=1), encoding="utf-8")
        if image is not None:
            image.save(str(base.with_suffix(".png")))
        return base
    except Exception:
        return None


# --- The note card ---------------------------------------------------------------------------

class NoteCard(QWidget):
    """The note on screen: paper, a grid, handwriting, the sketch drawing
    itself, follow-up buttons and Clear. Never takes focus; drag it by its top."""

    followup = pyqtSignal(str)
    cleared = pyqtSignal()

    W, H = 470.0, 352.0
    W3D, H3D = 560.0, 500.0  # a 3D model gets a bigger card
    _M = 18.0   # room for the shadow
    TURN_RATE = 0.5     # a 3D model rocks slowly to and fro (radians of swing per second)...
    TURN_FOR_S = 45.0   # ...then comes to rest; resting the mouse on the card sets it turning again

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Clixy note")
        self._w, self._h = self.W, self.H
        self.resize(int(self._w + 2 * self._M), int(self._h + 2 * self._M))
        self.note: dict | None = None
        self._t0 = time.monotonic()
        self._strokes: list[Stroke] = []
        self._chips: list[tuple[QRectF, str]] = []
        self._clear_rect = QRectF()
        self._drag_from = None
        self._saved = False
        self._backdrop = None         # the card itself (paper, grid, topic, Clear), drawn once
        self._model = None            # the 3D model, when the note has one (render3d.RealModel or scene3d.NoteModel)
        self._orbit_from = None       # dragging the model round
        self._clock = 0.0             # the model's own time: it turns and its parts move while this runs
        self._last = time.monotonic()
        self._turn_until = 0.0
        self._resting = False
        self._paint_ms = 0.0          # how long a 3D frame takes, to pace the frames to the PC
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # --- Showing a note ----------------------------------------------------------------
    def show_note(self, note: dict, screen=None) -> None:
        self.note = note
        self._t0 = time.monotonic()
        self._saved = False
        width, height = (self.W3D, self.H3D) if self._three_d() else (self.W, self.H)
        if (width, height) != (self._w, self._h):   # keep the card's bottom right corner where it was
            corner = self.geometry().bottomRight()
            self._w, self._h = width, height
            self.resize(int(width + 2 * self._M), int(height + 2 * self._M))
            if self.isVisible():
                self.move(corner.x() - self.width() + 1, corner.y() - self.height() + 1)
        self._layout()
        if not self.isVisible():
            screen = screen or QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 12, area.bottom() - self.height() - 12)
        self.show()
        self.raise_()
        self._clock, self._last, self._resting = 0.0, time.monotonic(), False
        self._turn_until = self._t0 + self.TURN_FOR_S
        self._timer.start(16 if self._model is None else 33)

    def clear(self) -> None:
        self._timer.stop()
        self.hide()
        self.note = None
        if self._model is not None and hasattr(self._model, "release"):
            self._model.release()
        self._model = None
        self.cleared.emit()

    @property
    def card(self) -> QRectF:
        return QRectF(self._M, self._M, self._w, self._h)

    @property
    def canvas(self) -> QRectF:
        c = self.card
        return QRectF(c.left() + 22, c.top() + 98, c.width() - 44, c.height() - 98 - 56)

    def _three_d(self) -> bool:
        return bool(self.note and (self.note.get("view") or {}).get("3d"))

    def _angle(self) -> float:
        return 18 * math.sin((time.monotonic() - self._t0) * 0.45) if self._three_d() else 0.0

    def _layout(self) -> None:
        if self._model is not None and hasattr(self._model, "release"):
            self._model.release()
        self._model = None
        if self._three_d():
            try:   # real 3D on the GPU; the painter's 3D on a PC without OpenGL 3.3
                self._model = render3d.RealModel.from_note(self.note)
            except Exception:
                self._model = None
            if self._model is None:
                self._model = self._painted_model()
        self._strokes = build(self.note, self.canvas, self._angle()) if self.note and self._model is None else []
        self._chips = []
        x = self.card.left() + 22
        y = self.card.bottom() - 44
        for text in (self.note or {}).get("followups", []):
            w = QFontMetricsF(theme.ui_font(9.5, QFont.Weight.DemiBold)).horizontalAdvance(text) + 44
            if x + w > self.card.right() - 20:
                break
            self._chips.append((QRectF(x, y, w, 30), text))
            x += w + 10
        self._clear_rect = QRectF(self.card.right() - 78, self.card.top() + 4, 70, 20)

    def _painted_model(self):
        try:
            return scene3d.NoteModel.from_note(self.note, compile_expr)
        except Exception:   # a model that can't be built: the sketched wireframe instead
            return None

    def _drawn_for(self) -> float:
        return time.monotonic() - self._t0 - 0.5   # the title writes first

    def _finished_at(self) -> float:
        """When everything on the note has been drawn (seconds into the drawing)."""
        if self._model is not None:
            return self._model.reveal_end()
        return self._strokes[-1].start + self._strokes[-1].duration if self._strokes else 0.0

    def _turn(self) -> float:
        return self._model.TURN * math.sin(self._clock * self.TURN_RATE) if self._model is not None else 0.0

    def _tick(self) -> None:
        now = time.monotonic()
        if self._model is not None:
            if not self._resting:
                self._clock += min(0.1, max(0.0, now - self._last))
                # Come to rest at the end of a swing, where it moves slowest, so it stops softly.
                if now > self._turn_until and abs(math.cos(self._clock * self.TURN_RATE)) < 0.06:
                    self._resting = True
            self._last = now
        elif self._three_d():
            self._strokes = build(self.note, self.canvas, self._angle())  # the wireframe turns slowly
        done = self._drawn_for() > self._finished_at() + 0.4
        if done and not self._saved:
            self._saved = True
            save_note(self.note, self.grab().toImage())
        if done and (self._resting if self._model is not None else not self._three_d()):
            self._timer.stop()
        elif self._model is not None:
            # 30 frames a second, fewer on a PC where a frame takes longer to draw.
            self._timer.setInterval(33 if self._paint_ms < 12 else 50 if self._paint_ms < 22 else 80)
        self.update()

    def enterEvent(self, _event) -> None:  # noqa: N802
        """Resting the mouse on a 3D note sets it turning again."""
        if self._model is not None and self.isVisible():
            self._turn_until = time.monotonic() + self.TURN_FOR_S
            if self._resting:
                self._resting = False
                self._last = time.monotonic()
                self._timer.start(33)

    # --- Mouse: follow-ups, Clear, dragging ----------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        p = event.position()
        if self._clear_rect.contains(p):
            self.clear()
            return
        for rect, text in self._chips:
            if rect.contains(p):
                self.followup.emit(text)
                return
        if p.y() < self.card.top() + 30:
            self._drag_from = event.globalPosition().toPoint() - self.pos()
        elif hasattr(self._model, "drag") and self.canvas.contains(p):
            self._orbit_from = p              # turn the model by hand; it stops rocking
            self._resting = True
            self._timer.start(16)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)
        elif self._orbit_from is not None and self._model is not None:
            p = event.position()
            self._model.drag(p.x() - self._orbit_from.x(), p.y() - self._orbit_from.y())
            self._orbit_from = p
            self.update()

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag_from = None
        self._orbit_from = None

    # --- Painting ------------------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self.note:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        card = self.card
        p.drawPixmap(0, 0, self._card_pixmap())

        # Title and subtitle, written first.
        t = time.monotonic() - self._t0
        self._write(p, self.note.get("title", ""), QPointF(card.left() + 22, card.top() + 60), 21, True,
                    COLORS["ink"], min(1.0, t / 0.5))
        self._write(p, self.note.get("subtitle", ""), QPointF(card.left() + 22, card.top() + 84), 14, False,
                    QColor(120, 116, 110), min(1.0, max(0.0, (t - 0.25) / 0.5)))

        # The sketch, stroke by stroke (or the 3D model), kept off the title and the buttons.
        drawn = self._drawn_for()
        p.save()
        p.setClipRect(QRectF(card.left() + 2, self.canvas.top() - 10, card.width() - 4, self.canvas.height() + 20))
        if self._model is not None:
            started = time.perf_counter()
            try:
                self._model.paint(p, self.canvas, turn=self._turn(), t=self._clock, reveal=drawn)
            except Exception:   # never a broken card: the painter's 3D, or the sketched wireframe
                fallback = self._painted_model() if not isinstance(self._model, scene3d.NoteModel) else None
                self._model = fallback
                if fallback is None:
                    self._strokes = build(self.note, self.canvas, self._angle())
            self._paint_ms = 0.8 * self._paint_ms + 0.2 * (time.perf_counter() - started) * 1000
        for s in self._strokes:
            u = 1.0 if self._three_d() and self._saved else (drawn - s.start) / s.duration if s.duration else 1.0
            if u <= 0:
                continue
            self._paint_stroke(p, s, min(1.0, u))
        p.restore()

        # Follow-ups.
        if self._model is not None:
            ready = drawn > self._model.reveal_end() - 0.6
        else:
            ready = drawn > (self._strokes[-1].start if self._strokes else 0)
        if ready:
            flood = theme.CORAL
            for rect, text in self._chips:
                p.setPen(QPen(QColor(flood.red(), flood.green(), flood.blue(), 120), 1.2))
                p.setBrush(QColor(253, 232, 226))
                p.drawRoundedRect(rect, 15, 15)
                p.setPen(QPen(flood, 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                bubble = QRectF(rect.left() + 12, rect.center().y() - 6, 13, 11)
                p.drawRoundedRect(bubble, 4, 4)
                p.drawLine(QPointF(bubble.left() + 3, bubble.bottom()), QPointF(bubble.left() + 1, bubble.bottom() + 3))
                p.setPen(QColor(50, 40, 40))
                p.setFont(theme.ui_font(9.5, QFont.Weight.DemiBold))
                p.drawText(rect.adjusted(32, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, text)
        p.end()

    def _card_pixmap(self) -> QPixmap:
        """The card's paper, shadow, topic band, Clear and grid: they never change
        while a note is up, so they are drawn once, not every frame."""
        dpr = self.devicePixelRatioF() or 1.0
        key = (self.width(), self.height(), dpr, (self.note or {}).get("topic", ""))
        if self._backdrop is None or self._backdrop[0] != key:
            pix = QPixmap(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            q = QPainter(pix)
            q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            q.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            self._paint_card(q)
            q.end()
            self._backdrop = (key, pix)
        return self._backdrop[1]

    def _paint_card(self, p: QPainter) -> None:
        card = self.card
        for i in range(1, 9):  # soft shadow
            p.setPen(QPen(QColor(0, 0, 0, 7), i * 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(card.translated(0, 4), 16, 16)
        path = QPainterPath()
        path.addRoundedRect(card, 16, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(251, 248, 242))
        p.drawPath(path)
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(card.left(), card.top(), card.width(), 28), QColor(239, 234, 224))
        p.restore()
        p.setPen(QPen(QColor(222, 215, 202), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # Topic in the band, Clear on the right.
        p.setFont(theme.ui_font(8.8))
        p.setPen(QColor(140, 134, 122))
        p.drawText(QRectF(card.left(), card.top(), card.width(), 28), Qt.AlignmentFlag.AlignCenter,
                   self.note.get("topic", ""))
        p.setPen(QPen(QColor(205, 198, 186), 1.0))
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(self._clear_rect, 10, 10)
        p.setPen(QColor(60, 60, 70))
        p.setFont(theme.ui_font(8.4, QFont.Weight.DemiBold))
        p.drawText(self._clear_rect, Qt.AlignmentFlag.AlignCenter, "×  Clear")

        # Graph paper.
        grid = self.canvas.adjusted(-8, -8, 8, 8)
        p.setPen(QPen(QColor(234, 228, 216), 1.0))
        x = grid.left()
        while x <= grid.right():
            p.drawLine(QPointF(x, grid.top()), QPointF(x, grid.bottom()))
            x += 16
        y = grid.top()
        while y <= grid.bottom():
            p.drawLine(QPointF(grid.left(), y), QPointF(grid.right(), y))
            y += 16

    @staticmethod
    def _write(p: QPainter, text: str, at: QPointF, size: float, bold: bool, color: QColor, reveal: float) -> None:
        if not text or reveal <= 0:
            return
        p.save()
        p.setFont(theme.hand_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        width = p.fontMetrics().horizontalAdvance(text)
        p.setClipRect(QRectF(at.x() - 2, at.y() - size * 2, (width + 4) * reveal, size * 3))
        p.setPen(color)
        p.drawText(at, text)
        p.restore()

    def _paint_stroke(self, p: QPainter, s: Stroke, u: float) -> None:
        if s.kind == "text":
            p.save()
            p.setFont(theme.hand_font(s.size))
            w = p.fontMetrics().horizontalAdvance(s.text)
            x = s.at.x() - (w if s.align == "right" else w / 2 if s.align == "center" else 0)
            p.setClipRect(QRectF(x - 2, s.at.y() - s.size * 2, (w + 4) * u, s.size * 3))
            p.setPen(s.color)
            p.drawText(QPointF(x, s.at.y()), s.text)
            p.restore()
            return
        if s.kind == "dot":
            r = 4.5 * min(1.0, u * 1.6)
            p.setPen(QPen(QColor(255, 255, 255), 1.5))
            p.setBrush(s.color)
            p.drawEllipse(s.at, r, r)
            return
        pts = s.points
        if len(pts) < 2:
            return
        shown = pts[: max(2, int(len(pts) * u))]
        pen = QPen(s.color, s.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if s.dashed:
            pen.setDashPattern([3.0, 3.0])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF(shown))
        if s.head and u >= 0.98:
            a, b = pts[-2] if len(pts) > 1 else pts[0], pts[-1]
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            size = 8.0
            head = [QPointF(b.x() - size * math.cos(ang - 0.45), b.y() - size * math.sin(ang - 0.45)), b,
                    QPointF(b.x() - size * math.cos(ang + 0.45), b.y() - size * math.sin(ang + 0.45))]
            solid = QPen(s.color, s.width)
            solid.setCapStyle(Qt.PenCapStyle.RoundCap)
            solid.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(solid)
            p.drawPolyline(QPolygonF(head))
