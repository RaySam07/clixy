"""Hand-drawn marks that your buddy draws on the screen.

An answer's shapes (annotations.py) become marks: pen paths sampled every
few pixels and drawn like a marker, with a little hand wobble. The wobble
is seeded from the shape's coordinates, so a mark looks the same on every
frame. The marks hug what they point at:

- a loop around a small element: an ellipse for a circle, a squircle
  (a rounded-square loop) for a box, a little wider than the element,
  ending past where it began like a real pen loop;
- a hand-drawn rounded rectangle around a big element;
- a highlighter band over text, an underline, a curved arrow;
- a written label as a butter pill.

``plan_tour`` turns them into one route for the buddy: fly to the first
mark, trace it with its tip, hop to the next, rest beside the last one.
Labels and step numbers pop in as each mark is drawn. Everything here is in
an overlay's local logical pixels and needs no QApplication except to paint.
"""
from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF

import theme

STEP = 3.0
"""Pen path sample spacing in px."""

PEN_W = 3.6
"""Marker width in px. The halo and shadow are wider."""

CIRCLE_R = (9.0, 30.0)
"""A circle's radius is kept in this range: the model's circles run big."""

BIG_W, BIG_H = 280.0, 120.0
"""A box bigger than this gets a rounded rectangle instead of a loop."""

_LABEL_PT = 9.5


@dataclass
class Mark:
    kind: str                    # loop, rect, highlight, underline, arrow, label
    points: list[QPointF]        # the pen path
    cum: list[float]             # path length at each point
    target: QRectF               # what the mark is about (hover = got it)
    label: str = ""
    head: list[QPointF] = field(default_factory=list)  # arrowhead: left, tip, right
    width: float = PEN_W
    badge: int = 0               # step number, 0 = none
    pill: QRectF | None = None   # where the label goes
    start: float = 0.0           # seconds into the tour when drawing starts
    duration: float = 0.0
    exact: bool = False              # geometry on a figure: precise, never wobbly
    anchors: list[QPointF] = field(default_factory=list)  # best label spots, best first
    wedge: list[QPointF] = field(default_factory=list)    # an angle's sector, filled once drawn
    dark: bool = False               # drawn over a dark screen: a dark halo, not white
    acked_at: float | None = None   # when you hovered what it marks
    armed: bool = False              # the mouse has been outside it
    leader: tuple[QPointF, QPointF] | None = None  # line from a pill that sits apart

    @property
    def length(self) -> float:
        return self.cum[-1] if self.cum else 0.0

    def at_length(self, s: float) -> QPointF:
        if not self.points:
            return QPointF()
        if s <= 0:
            return QPointF(self.points[0])
        if s >= self.length:
            return QPointF(self.points[-1])
        i = bisect.bisect_right(self.cum, s)
        a, b = self.points[i - 1], self.points[i]
        span = self.cum[i] - self.cum[i - 1] or 1.0
        u = (s - self.cum[i - 1]) / span
        return QPointF(a.x() + (b.x() - a.x()) * u, a.y() + (b.y() - a.y()) * u)

    def partial(self, s: float) -> list[QPointF]:
        """The path drawn so far: everything up to length ``s``."""
        if s >= self.length:
            return self.points
        i = bisect.bisect_right(self.cum, s)
        return self.points[:i] + [self.at_length(s)]

    def ink(self) -> QRectF:
        """The area the stroke itself covers."""
        xs = [p.x() for p in self.points + self.head]
        ys = [p.y() for p in self.points + self.head]
        pad = self.width / 2 + 3
        return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)

    def solid(self) -> list[QRectF]:
        """What a label must not cover. A UI mark's whole box (it frames
        something); for geometry only the stroke itself, so labels can sit
        inside a figure, the way a teacher writes them."""
        if not self.exact:
            return [self.ink()]
        pad = self.width / 2 + 4
        pts = self.points[::4] + self.points[-1:] + self.head
        return [QRectF(p.x() - pad, p.y() - pad, 2 * pad, 2 * pad) for p in pts]

    def badge_rect(self) -> QRectF | None:
        if not self.badge:
            return None
        return QRectF(self.points[0].x() - 23, self.points[0].y() - 23, 24, 24)

    def bounds(self) -> QRectF:
        """Everything this mark paints, with room for its shadow."""
        rect = self.ink()
        for extra in (self.pill, self.badge_rect()):
            if extra is not None:
                rect = rect.united(extra)
        return rect.adjusted(-8, -8, 8, 10)


# --- Geometry -------------------------------------------------------------------

def _resample(raw: list[tuple[float, float]], step: float = STEP) -> tuple[list[QPointF], list[float]]:
    """Points every ``step`` px along a dense polyline, and their lengths."""
    pts = [QPointF(*raw[0])]
    cum = [0.0]
    carry = 0.0
    for (x0, y0), (x1, y1) in zip(raw, raw[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        d = step - carry
        while d <= seg:
            u = d / seg
            pts.append(QPointF(x0 + (x1 - x0) * u, y0 + (y1 - y0) * u))
            cum.append(cum[-1] + step)
            d += step
        carry = seg - (d - step)
    last = QPointF(*raw[-1])
    tail = math.hypot(last.x() - pts[-1].x(), last.y() - pts[-1].y())
    if tail > 0.01:
        pts.append(last)
        cum.append(cum[-1] + tail)
    return pts, cum


def _rng(*values: float) -> random.Random:
    return random.Random(hash(tuple(round(v) for v in values)) & 0xFFFFFFFF)


def loop_path(cx: float, cy: float, rx: float, ry: float, rng: random.Random,
              squareness: float = 2.0) -> list[tuple[float, float]]:
    """One pen loop around (cx, cy): starts up-left, goes round clockwise
    and runs on a little past the start, spiralling out a touch so the end
    passes outside the beginning. ``squareness`` 2 is an ellipse; higher is
    a squircle that hugs a rectangle."""
    start = -2.3 + rng.uniform(-0.12, 0.12)
    sweep = 2 * math.pi + 0.5 + rng.uniform(-0.06, 0.08)
    tilt = rng.uniform(-0.035, 0.035)
    phase = rng.uniform(0, 2 * math.pi)
    e = 2.0 / squareness
    n = max(160, int((rx + ry) * 3))
    ct, st = math.cos(tilt), math.sin(tilt)
    out = []
    for i in range(n + 1):
        u = i / n
        th = start + sweep * u
        c, s = math.cos(th), math.sin(th)
        x = math.copysign(abs(c) ** e, c)
        y = math.copysign(abs(s) ** e, s)
        f = 1.0 + 0.018 * math.sin(2 * th + phase) + 0.05 * u
        px, py = x * rx * f, y * ry * f
        out.append((cx + px * ct - py * st, cy + px * st + py * ct))
    return out


def rect_path(rect: QRectF, rng: random.Random) -> list[tuple[float, float]]:
    """A hand-drawn rounded rectangle, from the top-left round clockwise and
    on past the start along the top edge."""
    r = min(12.0, rect.width() / 4, rect.height() / 4)
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    pts: list[tuple[float, float]] = []

    def arc(cx, cy, a0, a1):
        for k in range(9):
            a = a0 + (a1 - a0) * k / 8
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    pts.append((x0 + r, y0))
    pts.append((x1 - r, y0))
    arc(x1 - r, y0 + r, -math.pi / 2, 0)
    pts.append((x1, y1 - r))
    arc(x1 - r, y1 - r, 0, math.pi / 2)
    pts.append((x0 + r, y1))
    arc(x0 + r, y1 - r, math.pi / 2, math.pi)
    pts.append((x0, y0 + r))
    arc(x0 + r, y0 + r, math.pi, 1.5 * math.pi)
    pts.append((x0 + r + min(28.0, rect.width() * 0.2), y0 - 3.5))  # runs on past the start
    # Densify, then let the line wander a pixel or two like a hand.
    dense, _ = _resample(pts, 2.0)
    total = len(dense)
    p1, p2 = rng.uniform(0, 6.3), rng.uniform(0, 6.3)
    out = []
    for i, p in enumerate(dense):
        u = i / max(1, total - 1)
        wob = 1.1 * math.sin(2 * math.pi * 2 * u + p1) + 0.6 * math.sin(2 * math.pi * 5 * u + p2)
        out.append((p.x() + wob * 0.6, p.y() + wob))
    return out


def arrow_path(x1: float, y1: float, x2: float, y2: float,
               rng: random.Random) -> tuple[list[tuple[float, float]], list[QPointF]]:
    """A gently curved shaft from (x1, y1) to the tip at (x2, y2), and the
    arrowhead as left, tip, right."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    side = -1.0 if ny > 0 or (ny == 0 and nx > 0) else 1.0   # bow upward
    bow = side * length * rng.uniform(0.09, 0.14)
    cx, cy = (x1 + x2) / 2 + nx * bow, (y1 + y2) / 2 + ny * bow
    shaft = []
    for i in range(65):
        t = i / 64
        shaft.append(((1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2,
                      (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2))
    tx, ty = x2 - cx, y2 - cy
    tl = math.hypot(tx, ty) or 1.0
    tx, ty = tx / tl, ty / tl
    size = max(10.0, min(16.0, length * 0.16))
    spread = 0.5

    def wing(angle):
        c, s = math.cos(angle), math.sin(angle)
        return QPointF(x2 - size * (tx * c - ty * s), y2 - size * (tx * s + ty * c))

    return shaft, [wing(spread), QPointF(x2, y2), wing(-spread)]


def underline_path(x: float, y: float, w: float, rng: random.Random) -> list[tuple[float, float]]:
    phase = rng.uniform(0, 6.3)
    n = max(24, int(w / 2))
    out = []
    for i in range(n + 1):
        u = i / n
        flick = -3.0 * max(0.0, (u - 0.9) / 0.1) ** 2      # a little lift at the end
        out.append((x - 3 + (w + 7) * u, y + 2 + 0.9 * math.sin(u * 6.6 + phase) - 1.2 * u + flick))
    return out


# --- Shapes to marks ----------------------------------------------------------------

class BusyMap:
    """How busy the screenshot is in each cell of a small grid (0-255 edge
    strength), for keeping labels off text and detail. Cells are fractions
    of the screen, so it fits any overlay of that screen."""

    def __init__(self, cells: bytes, cols: int, rows: int, luma: bytes | None = None) -> None:
        self.cells, self.cols, self.rows = cells, cols, rows
        self.lumas = luma

    def _mean(self, grid: bytes, fx0: float, fy0: float, fx1: float, fy1: float) -> float:
        c0 = max(0, min(self.cols - 1, int(fx0 * self.cols)))
        c1 = max(c0, min(self.cols - 1, int(fx1 * self.cols)))
        r0 = max(0, min(self.rows - 1, int(fy0 * self.rows)))
        r1 = max(r0, min(self.rows - 1, int(fy1 * self.rows)))
        total = n = 0
        for r in range(r0, r1 + 1):
            row = r * self.cols
            for c in range(c0, c1 + 1):
                total += grid[row + c]
                n += 1
        return total / max(1, n)

    def score(self, fx0: float, fy0: float, fx1: float, fy1: float) -> float:
        """0 (calm) to 1 (busy) for a region given as screen fractions."""
        return min(1.0, self._mean(self.cells, fx0, fy0, fx1, fy1) / 40.0)

    def luma(self, fx0: float, fy0: float, fx1: float, fy1: float) -> float:
        """0 (black) to 1 (white): how light the screen is there."""
        if self.lumas is None:
            return 1.0
        return self._mean(self.lumas, fx0, fy0, fx1, fy1) / 255.0


def _cover(rect: QRectF, others) -> float:
    """How much of ``rect`` the others cover, as a fraction (can pass 1)."""
    area = rect.width() * rect.height() or 1.0
    total = 0.0
    for other in others:
        if other is None:
            continue
        hit = rect.intersected(other)
        total += max(0.0, hit.width()) * max(0.0, hit.height())
    return total / area


def _pill_size(text: str) -> tuple[float, float]:
    metrics = QFontMetricsF(theme.ui_font(_LABEL_PT, QFont.Weight.DemiBold))
    return metrics.horizontalAdvance(text) + 20, metrics.height() + 8


def place_label(mark: "Mark", area: QRectF, obstacles: list[QRectF], avoid: list[QRectF],
                busy: BusyMap | None) -> tuple[QRectF, tuple[QPointF, QPointF] | None]:
    """The best spot for a mark's label pill: next to the mark, on screen,
    clear of other marks, labels and the island, and over the calmest part
    of the screen. Tries each side and corner, close and a step further
    out; one further out gets a thin line back to its mark."""
    w, h = _pill_size(mark.label)
    core = mark.ink()
    solid = mark.solid()
    cx, cy = core.center().x(), core.center().y()
    best = None
    preferred = [(a.x() - w / 2, a.y() - h / 2, 2.5 * i) for i, a in enumerate(mark.anchors)]
    for step, far_cost in ((8.0, 0.0), (30.0, 9.0)):
        spots = (
            (cx - w / 2, core.top() - step - h, 0.0),            # above
            (cx - w / 2, core.bottom() + step, 5.0),             # below
            (core.right() + step, cy - h / 2, 7.0),              # right
            (core.left() - step - w, cy - h / 2, 8.0),           # left
            (core.left() - w * 0.25, core.top() - step - h, 3.0),    # above, leaning left
            (core.right() - w * 0.75, core.top() - step - h, 3.0),   # above, leaning right
            (core.left() - w * 0.25, core.bottom() + step, 7.0),
            (core.right() - w * 0.75, core.bottom() + step, 12.0),   # where the buddy rests
        )
        if mark.anchors:
            # A figure's own spots first (beside a line, inside an angle);
            # the generic ones only if those are taken.
            extra = 16.0 if mark.exact else 6.0
            spots = tuple(preferred) + tuple((x, y, pref + extra) for x, y, pref in spots) if step == 8.0 else spots
        for x, y, pref in spots:
            rect = QRectF(x, y, w, h)
            off = 0.0
            inside = area.adjusted(6, 6, -6, -6)
            if not inside.contains(rect):
                # Slide it on screen; what it then covers is scored below.
                nx = max(inside.left(), min(rect.left(), inside.right() - w))
                ny = max(inside.top(), min(rect.top(), inside.bottom() - h))
                off = abs(nx - rect.left()) + abs(ny - rect.top())
                rect = QRectF(nx, ny, w, h)
            cost = pref + far_cost * (2.5 if mark.exact else 1.0) + 0.6 * off
            cost += 500 * min(1.0, _cover(rect, solid))
            cost += 400 * _cover(rect, obstacles)
            cost += 300 * _cover(rect, avoid)
            if busy is not None and area.width() > 0 and area.height() > 0:
                # A figure is busy with its own lines; a label still belongs in it.
                cost += (15 if mark.exact else 60) * busy.score((rect.left() - area.left()) / area.width(),
                                        (rect.top() - area.top()) / area.height(),
                                        (rect.right() - area.left()) / area.width(),
                                        (rect.bottom() - area.top()) / area.height())
            if best is None or cost < best[0]:
                best = (cost, rect)
    rect = best[1]
    leader = None
    gap = min(_distance(rect, r) for r in solid)
    if gap > 12:
        near = QPointF(max(rect.left(), min(cx, rect.right())), max(rect.top(), min(cy, rect.bottom())))
        on_mark = min(mark.points, key=lambda p: (p.x() - near.x()) ** 2 + (p.y() - near.y()) ** 2)
        leader = (near, on_mark)
    return rect, leader


def _distance(a: QRectF, b: QRectF) -> float:
    dx = max(0.0, max(a.left() - b.right(), b.left() - a.right()))
    dy = max(0.0, max(a.top() - b.bottom(), b.top() - a.bottom()))
    return math.hypot(dx, dy)


def _iou(a: QRectF, b: QRectF) -> float:
    hit = a.intersected(b)
    inter = max(0.0, hit.width()) * max(0.0, hit.height())
    union = a.width() * a.height() + b.width() * b.height() - inter
    return inter / union if union > 0 else 0.0


MAX_MARKS = 3
"""Marks per answer, at most; labels written on their own, at most one."""

MAX_GEOMETRY = 4
"""Geometry marks per answer, at most (a quadrilateral's four angles)."""


def ring_path(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """Exactly on a circle, from the top, clockwise, once round."""
    n = max(72, int(2 * math.pi * r / 2))
    return [(cx + r * math.cos(-math.pi / 2 + 2 * math.pi * i / n),
             cy + r * math.sin(-math.pi / 2 + 2 * math.pi * i / n)) for i in range(n + 1)]


def _unit(dx: float, dy: float) -> tuple[float, float]:
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _side_offset(nx: float, ny: float, w: float, h: float, gap: float) -> float:
    """How far a pill's centre must sit along (nx, ny) to clear a line by ``gap``."""
    return gap + abs(nx) * w / 2 + abs(ny) * h / 2


def angle_parts(vx: float, vy: float, ax: float, ay: float, bx: float, by: float, label: str = ""):
    """An angle mark: (arc points, wedge polygon, label anchor direction,
    arc radius). A right angle (90° labelled, or square on screen) gets the
    little square instead of an arc."""
    ua, ub = _unit(ax - vx, ay - vy), _unit(bx - vx, by - vy)
    ta, tb = math.atan2(ua[1], ua[0]), math.atan2(ub[1], ub[0])
    d = (tb - ta + math.pi) % (2 * math.pi) - math.pi        # the smaller turn from a to b
    shortest = min(math.hypot(ax - vx, ay - vy), math.hypot(bx - vx, by - vy))
    ra = max(16.0, min(40.0, 0.3 * shortest))
    bis = _unit(ua[0] + ub[0], ua[1] + ub[1]) if abs(abs(d) - math.pi) > 0.05 else (-ua[1], ua[0])
    text = label.replace(" ", "").lower()
    right = text.startswith("90") or "right" in text or abs(abs(d) - math.pi / 2) < math.radians(2.5)
    if right:
        s = max(10.0, min(20.0, 0.6 * ra))
        pts = [(vx + ua[0] * s, vy + ua[1] * s), (vx + (ua[0] + ub[0]) * s, vy + (ua[1] + ub[1]) * s),
               (vx + ub[0] * s, vy + ub[1] * s)]
        wedge = [(vx, vy)] + pts
        return pts, wedge, bis, s * 1.4
    n = max(12, int(abs(d) * ra / 2))
    pts = [(vx + ra * math.cos(ta + d * i / n), vy + ra * math.sin(ta + d * i / n)) for i in range(n + 1)]
    return pts, [(vx, vy)] + pts, bis, ra


def build_marks(annotations: list, area: QRectF, *, avoid: list[QRectF] | None = None,
                busy: BusyMap | None = None) -> list[Mark]:
    """Turn parsed shapes (local logical px) into marks, in order, keeping
    them concise: at most MAX_MARKS, none that repeat another, none that
    swallow half the screen, none that make no sense (an arrow 5 px long).
    Labels go where they read best (see place_label)."""
    from annotations import Angle, Arrow, Box, Circle, Dot, Highlight, Label, Line, Ring, Underline

    marks: list[Mark] = []
    for a in annotations:
        if isinstance(a, Ring):
            if a.r < 4:
                continue
            target = QRectF(a.x - a.r, a.y - a.r, 2 * a.r, 2 * a.r)
            mark = Mark("ring", *_resample(ring_path(a.x, a.y, a.r)), target=target, label=a.label, exact=True)
            if a.label:
                w, h = _pill_size(a.label)
                for deg in (-45, -135, 45, 135, -90):            # outside the circle, top-right first
                    nx, ny = math.cos(math.radians(deg)), math.sin(math.radians(deg))
                    d = a.r + _side_offset(nx, ny, w, h, 8)
                    mark.anchors.append(QPointF(a.x + nx * d, a.y + ny * d))
            marks.append(mark)
        elif isinstance(a, Line):
            if math.hypot(a.x2 - a.x1, a.y2 - a.y1) < 6:
                continue
            target = QRectF(min(a.x1, a.x2), min(a.y1, a.y2), abs(a.x2 - a.x1), abs(a.y2 - a.y1)).adjusted(-4, -4, 4, 4)
            mark = Mark("line", *_resample([(a.x1, a.y1), (a.x2, a.y2)]), target=target, label=a.label, exact=True)
            if a.label:
                w, h = _pill_size(a.label)
                ux, uy = _unit(a.x2 - a.x1, a.y2 - a.y1)
                mx, my = (a.x1 + a.x2) / 2, (a.y1 + a.y2) / 2
                for nx, ny in ((uy, -ux), (-uy, ux)):            # beside the middle, either side
                    if ny > 0 and abs(ny) > 0.3 and (nx, ny) == (uy, -ux):
                        nx, ny = -nx, -ny                          # prefer above first
                    d = _side_offset(nx, ny, w, h, 7)
                    mark.anchors.append(QPointF(mx + nx * d, my + ny * d))
            marks.append(mark)
        elif isinstance(a, Angle):
            pts, wedge, bis, ra = angle_parts(a.vx, a.vy, a.ax, a.ay, a.bx, a.by, a.label)
            target = QRectF(a.vx - ra, a.vy - ra, 2 * ra, 2 * ra)
            mark = Mark("angle", *_resample(pts, 2.0), target=target, label=a.label, exact=True,
                        wedge=[QPointF(*p) for p in wedge])
            if a.label:
                w, h = _pill_size(a.label)
                # Inside the angle just past the arc, then deeper in where it
                # widens, and only then across the corner.
                for k, far in ((1.0, 0.0), (1.0, 0.9 * ra), (-1.0, 0.0)):
                    d = ra + far + _side_offset(bis[0], bis[1], w, h, 6)
                    mark.anchors.append(QPointF(a.vx + k * bis[0] * d, a.vy + k * bis[1] * d))
            marks.append(mark)
        elif isinstance(a, Dot):
            target = QRectF(a.x - 6, a.y - 6, 12, 12)
            raw = [(a.x + 4 * math.cos(2 * math.pi * i / 16), a.y + 4 * math.sin(2 * math.pi * i / 16)) for i in range(17)]
            mark = Mark("dot", *_resample(raw, 1.0), target=target, label=a.label, exact=True)
            if a.label:
                w, h = _pill_size(a.label)
                for nx, ny in ((0.7, -0.7), (-0.7, -0.7), (0.7, 0.7), (-0.7, 0.7)):
                    d = _side_offset(nx, ny, w, h, 7)
                    mark.anchors.append(QPointF(a.x + nx * d, a.y + ny * d))
            marks.append(mark)
        elif isinstance(a, Circle):
            r = max(CIRCLE_R[0], min(CIRCLE_R[1], float(a.r)))
            rng = _rng(a.x, a.y, r)
            target = QRectF(a.x - r, a.y - r, 2 * r, 2 * r)
            raw = loop_path(a.x, a.y, r * 1.12 + 4, r + 3, rng)
            marks.append(Mark("loop", *_resample(raw), target=target, label=a.label))
        elif isinstance(a, Box):
            target = QRectF(a.x1, a.y1, max(4, a.x2 - a.x1), max(4, a.y2 - a.y1))
            rng = _rng(a.x1, a.y1, a.x2, a.y2)
            if target.width() > BIG_W or target.height() > BIG_H:
                raw = rect_path(target.adjusted(-7, -6, 7, 6), rng)
                marks.append(Mark("rect", *_resample(raw), target=target, label=a.label))
            else:
                k = 2 ** (1 / 3.0)  # a squircle this much bigger clears the corners
                rx = target.width() / 2 * k + 5
                ry = target.height() / 2 * k + 4
                c = target.center()
                raw = loop_path(c.x(), c.y(), rx, ry, rng, squareness=3.0)
                marks.append(Mark("loop", *_resample(raw), target=target, label=a.label))
        elif isinstance(a, Highlight):
            target = QRectF(a.x1, a.y1, max(4, a.x2 - a.x1), max(4, a.y2 - a.y1))
            rng = _rng(a.x1, a.y1, a.x2, a.y2)
            cy = target.center().y()
            tilt = rng.uniform(-1.2, 1.2)
            raw = [(target.left() - 3 + (target.width() + 6) * u / 20, cy + tilt * (u / 10 - 1)) for u in range(21)]
            width = max(10.0, min(40.0, target.height() + 4))
            marks.append(Mark("highlight", *_resample(raw), target=target, width=width))
        elif isinstance(a, Underline):
            if a.w < 6:
                continue
            target = QRectF(a.x, a.y - 18, a.w, 20)
            raw = underline_path(a.x, a.y, a.w, _rng(a.x, a.y, a.w))
            marks.append(Mark("underline", *_resample(raw), target=target))
        elif isinstance(a, Arrow):
            if math.hypot(a.x2 - a.x1, a.y2 - a.y1) < 24:
                continue
            shaft, head = arrow_path(a.x1, a.y1, a.x2, a.y2, _rng(a.x1, a.y1, a.x2, a.y2))
            target = QRectF(a.x2 - 14, a.y2 - 14, 28, 28)
            marks.append(Mark("arrow", *_resample(shaft), target=target, head=head))
        elif isinstance(a, Label):
            text = a.text.strip()
            if not text:
                continue
            metrics = QFontMetricsF(theme.ui_font(_LABEL_PT, QFont.Weight.DemiBold))
            w, h = metrics.horizontalAdvance(text) + 20, metrics.height() + 8
            x = max(area.left() + 6, min(float(a.x) - 10, area.right() - 6 - w))
            y = max(area.top() + 6, min(float(a.y) - h + 4, area.bottom() - 6 - h))
            pill = QRectF(x, y, w, h)
            raw = [(pill.left() + 4, pill.bottom() + 2), (pill.right() - 4, pill.bottom() + 2)]
            marks.append(Mark("label", *_resample(raw), target=pill, label=text, pill=pill))

    marks = _constrain(marks, area)
    if busy is not None and area.width() > 0 and area.height() > 0:
        for m in marks:
            r = m.ink()
            m.dark = busy.luma((r.left() - area.left()) / area.width(), (r.top() - area.top()) / area.height(),
                               (r.right() - area.left()) / area.width(), (r.bottom() - area.top()) / area.height()) < 0.42

    # Numbered steps when there are several things to look at; then each
    # label finds its spot, clear of every stroke, badge and earlier label.
    steps = [m for m in marks if m.kind in ("loop", "rect", "highlight", "underline")]
    for n, m in enumerate(steps, 1):
        if len(steps) > 1:
            m.badge = n
    taken: list[tuple[Mark | None, QRectF]] = [(m, r) for m in marks for r in m.solid()]
    taken += [(None, m.badge_rect()) for m in marks if m.badge]
    taken += [(None, m.pill) for m in marks if m.kind == "label" and m.pill is not None]
    for m in marks:
        if m.label and m.kind not in ("label", "highlight", "underline"):
            others = [r for owner, r in taken if r is not None and owner is not m]
            m.pill, m.leader = place_label(m, area, others, list(avoid or []), busy)
            taken.append((None, m.pill))
    return marks


def _constrain(marks: list[Mark], area: QRectF) -> list[Mark]:
    """Keep the marks concise: drop ones that cover half the screen or
    repeat an earlier one, then keep the first few."""
    screen = area.width() * area.height() or 1.0
    kept: list[Mark] = []
    for m in marks:
        if m.kind != "label" and not m.exact and m.target.width() * m.target.height() > 0.5 * screen:
            continue
        if m.exact:
            # Geometry repeats only when it's the same kind in the same place
            # (a radius inside a diameter is two marks, not one).
            if any(k.kind == m.kind and _iou(m.target, k.target) > 0.9 for k in kept):
                continue
        elif any(not k.exact and _iou(m.target, k.target) > 0.5 for k in kept):
            continue
        kept.append(m)
    geometry = [m for m in kept if m.exact][:MAX_GEOMETRY]
    shapes = [m for m in kept if m.kind != "label" and not m.exact][:MAX_MARKS]
    labels = [m for m in kept if m.kind == "label"][:1]
    return [m for m in kept if m in geometry or m in shapes or m in labels]


# --- The tour: the buddy's route --------------------------------------------------

def _ease(u: float) -> float:
    """Ease in and out: the pen speeds up, then slows to a stop."""
    u = max(0.0, min(1.0, u))
    return 0.5 - 0.5 * math.cos(math.pi * u)


def _smoothstep(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def draw_time(mark: Mark) -> float:
    if mark.kind == "label":
        return 0.34
    if mark.kind == "dot":
        return 0.18
    if mark.kind == "angle":
        return 0.38
    if mark.kind == "ring":
        return max(0.55, min(1.2, 0.3 + mark.length / 1100))
    if mark.kind == "arrow":
        return max(0.32, min(0.72, 0.2 + mark.length / 900)) + _HEAD_S
    return max(0.36, min(0.95, 0.24 + mark.length / 850))


_HEAD_S = 0.14


def progress(mark: Mark, t: float) -> float:
    """How much of the mark is drawn at tour time ``t``, 0..1, eased. For an
    arrow this is the shaft; see head_progress."""
    if mark.duration <= 0:
        return 1.0
    span = mark.duration - (_HEAD_S if mark.kind == "arrow" else 0.0)
    u = (t - mark.start) / span
    return _ease(u) if mark.kind != "label" else max(0.0, min(1.0, u))


def head_progress(mark: Mark, t: float) -> float:
    return max(0.0, min(1.0, (t - (mark.start + mark.duration - _HEAD_S)) / _HEAD_S))


@dataclass
class Leg:
    t0: float
    t1: float
    p0: QPointF
    c: QPointF            # bezier control point (fly legs)
    p1: QPointF
    mark: Mark | None = None   # set for a drawing leg
    pulse: float = 0.0    # how much the buddy grows mid-flight


@dataclass
class Tour:
    legs: list[Leg]
    total: float
    rest: QPointF

    def pen(self, t: float) -> tuple[QPointF, float, Mark | None]:
        """Where the buddy's tip is at tour time ``t``, its scale, and the
        mark it's drawing (None while flying)."""
        if t >= self.total or not self.legs:
            return QPointF(self.rest), 1.0, None
        for leg in self.legs:
            if t < leg.t1:
                break
        if t < leg.t0:
            return QPointF(leg.p0), 1.0, None
        if leg.mark is not None:
            m = leg.mark
            return m.at_length(progress(m, t) * m.length), 1.0, m
        u = (t - leg.t0) / max(1e-6, leg.t1 - leg.t0)
        e = _smoothstep(u)
        x = (1 - e) ** 2 * leg.p0.x() + 2 * (1 - e) * e * leg.c.x() + e * e * leg.p1.x()
        y = (1 - e) ** 2 * leg.p0.y() + 2 * (1 - e) * e * leg.c.y() + e * e * leg.p1.y()
        return QPointF(x, y), 1.0 + leg.pulse * math.sin(math.pi * u), None


def rest_point(mark: Mark) -> QPointF:
    """Where the buddy waits when it's done: just outside the mark's lower
    right, so it doesn't sit on what it marked."""
    if mark.kind == "arrow" and mark.head:
        tip, left = mark.head[1], mark.head[0]
        return QPointF(tip.x() + (tip.x() - left.x()) * 0.4 + 6, tip.y() + (tip.y() - left.y()) * 0.4 + 6)
    if mark.kind == "label" and mark.pill is not None:
        return QPointF(mark.pill.right() + 6, mark.pill.center().y())
    corner = QPointF(mark.target.right() + 6, mark.target.bottom() + 4)
    if mark.kind in ("loop", "rect", "ring"):
        # The point on the loop closest to the lower-right corner.
        return min(mark.points, key=lambda p: (p.x() - corner.x() - 30) ** 2 + (p.y() - corner.y() - 30) ** 2)
    return QPointF(mark.points[-1].x() + 8, mark.points[-1].y() + mark.width / 2 + 6)


def _fly(t: float, a: QPointF, b: QPointF, *, first: bool) -> Leg:
    d = math.hypot(b.x() - a.x(), b.y() - a.y())
    if first:
        dur = max(0.32, min(0.9, d / 950))
        lift, pulse = min(d * 0.2, 70.0), 0.22
    else:
        dur = max(0.14, min(0.42, 0.1 + d / 1500))
        lift, pulse = min(d * 0.15, 32.0), 0.05
    c = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2 - lift)
    return Leg(t, t + dur, QPointF(a), c, QPointF(b), pulse=pulse)


def plan_tour(marks: list[Mark], start: QPointF) -> Tour:
    """The buddy's route: fly to each mark's start, trace it, and finally
    rest beside the last one. Sets each mark's start and duration."""
    legs: list[Leg] = []
    t, pos = 0.0, QPointF(start)
    for i, mark in enumerate(marks):
        begin = mark.points[0]
        if math.hypot(begin.x() - pos.x(), begin.y() - pos.y()) > 2:
            leg = _fly(t, pos, begin, first=i == 0)
            legs.append(leg)
            t = leg.t1
        mark.start, mark.duration = t, draw_time(mark)
        legs.append(Leg(t, t + mark.duration, begin, begin, mark.points[-1], mark=mark))
        t += mark.duration + 0.05
        pos = mark.points[-1]
    rest = rest_point(marks[-1]) if marks else QPointF(start)
    if marks:
        leg = _fly(t, pos, rest, first=False)
        legs.append(leg)
        t = leg.t1
    return Tour(legs, t, rest)


# --- Painting ---------------------------------------------------------------------

_SHADOW = QColor(0, 0, 0, 46)
_HALO = QColor(255, 255, 255, 235)
_HALO_DARK = QColor(0, 0, 0, 130)
_HIGHLIGHTER = QColor(255, 222, 0, 96)


def _path(points: list[QPointF]) -> QPainterPath:
    path = QPainterPath(points[0])
    for p in points[1:]:
        path.lineTo(p)
    return path


def _pen(color: QColor, width: float, cap=Qt.PenCapStyle.RoundCap) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _mix(a: QColor, b: QColor, u: float) -> QColor:
    u = max(0.0, min(1.0, u))
    return QColor(round(a.red() + (b.red() - a.red()) * u), round(a.green() + (b.green() - a.green()) * u),
                  round(a.blue() + (b.blue() - a.blue()) * u))


def _pop(since: float, dur: float = 0.3) -> tuple[float, float]:
    """Opacity and scale of something popping in ``since`` seconds ago."""
    u = max(0.0, min(1.0, since / dur))
    if u <= 0:
        return 0.0, 0.0
    c = 1.70158
    back = 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2   # ease-out-back
    return min(1.0, u * 1.6), 0.6 + 0.4 * back


ACK_S = 0.55
"""How long a mark takes to say "got it" and fade once you hover it."""


def paint_marks(painter: QPainter, marks: list[Mark], t: float, *, now: float = 0.0, fade: float = 1.0) -> None:
    """Draw every mark as far as it's drawn at tour time ``t``. ``fade``
    dims them all (on the way out); a hovered mark flashes butter, swells a
    little and fades on its own (``acked_at`` against ``now``)."""
    if fade <= 0:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Dots last, so a point sits on top of the lines that meet at it.
    for m in sorted(marks, key=lambda mk: mk.kind == "dot"):
        u = progress(m, t)
        if u <= 0:
            continue
        alpha, swell, flash = fade, 1.0, 0.0
        if m.acked_at is not None:
            a = max(0.0, min(1.0, (now - m.acked_at) / ACK_S))
            alpha *= 1 - a
            swell = 1 + 0.08 * math.sin(math.pi * min(1.0, a * 1.4))
            flash = 1 - abs(1 - 2 * min(1.0, a * 2.5))
            if alpha <= 0.01:
                continue
        painter.save()
        if swell != 1.0:
            c = m.target.center()
            painter.translate(c)
            painter.scale(swell, swell)
            painter.translate(-c)
        painter.setOpacity(alpha)
        shown = m.partial(u * m.length)
        if m.wedge and u >= 1.0:
            # The angle's sector, softly filled once its arc is drawn.
            o, _s = _pop(t - (m.start + m.duration), 0.35)
            fill = QColor(theme.CORAL)
            fill.setAlpha(round(60 * o))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(QPolygonF(m.wedge))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if m.kind == "dot":
            o, s = _pop(t - m.start, 0.25)
            c = m.target.center()
            painter.setPen(QPen(_HALO_DARK if m.dark else _HALO, 2.5))
            painter.setBrush(theme.CORAL)
            painter.drawEllipse(c, 5.0 * s, 5.0 * s)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif m.kind == "highlight":
            # Over the text, so a light tint keeps it readable, like ink.
            painter.setPen(_pen(_HIGHLIGHTER, m.width, Qt.PenCapStyle.FlatCap))
            painter.drawPath(_path(shown))
        elif m.kind == "label":
            _paint_pill(painter, m.pill, m.label, reveal=u)
        else:
            path = _path(shown)
            head = None
            if m.kind == "arrow" and head_progress(m, t) > 0:
                h = head_progress(m, t)
                tip = m.head[1]
                head = QPainterPath(QPointF(tip.x() + (m.head[0].x() - tip.x()) * h,
                                            tip.y() + (m.head[0].y() - tip.y()) * h))
                head.lineTo(tip)
                head.lineTo(QPointF(tip.x() + (m.head[2].x() - tip.x()) * h, tip.y() + (m.head[2].y() - tip.y()) * h))
            halo = _HALO_DARK if m.dark else _HALO
            for color, width, dy in ((_SHADOW, m.width + 6.5, 1.6), (halo, m.width + 4.4, 0.0),
                                     (_mix(theme.CORAL, theme.BUTTER, flash), m.width, 0.0)):
                painter.setPen(_pen(color, width))
                painter.drawPath(path.translated(0, dy))
                if head is not None:
                    painter.drawPath(head.translated(0, dy))
        painter.restore()

        done = t - (m.start + m.duration)
        if m.badge:
            o, s = _pop(t - m.start)
            if o > 0:
                _paint_badge(painter, m.points[0], m.badge, o * alpha, s)
        if m.pill is not None and m.kind != "label" and done > -0.08:
            o, s = _pop(done + 0.08)
            if o > 0:
                painter.save()
                painter.setOpacity(o * alpha)
                if m.leader is not None:
                    for color, width in ((_HALO, 4.2), (theme.INK, 1.6)):
                        painter.setPen(_pen(color, width))
                        painter.drawLine(m.leader[0], m.leader[1])
                c = m.pill.center()
                painter.translate(c)
                painter.scale(s, s)
                painter.translate(-c)
                _paint_pill(painter, m.pill, m.label)
                painter.restore()
    painter.restore()


def _paint_pill(painter: QPainter, rect: QRectF, text: str, reveal: float = 1.0) -> None:
    painter.save()
    if reveal < 1.0:
        painter.setClipRect(QRectF(rect.left() - 2, rect.top() - 4, (rect.width() + 4) * reveal, rect.height() + 8))
    r = rect.height() / 2
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 55))
    painter.drawRoundedRect(rect.translated(0, 2), r, r)
    painter.setBrush(theme.BUTTER)
    painter.drawRoundedRect(rect, r, r)
    painter.setPen(theme.INK)
    painter.setFont(theme.ui_font(_LABEL_PT, QFont.Weight.DemiBold))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()


def _paint_badge(painter: QPainter, at: QPointF, n: int, opacity: float, scale: float) -> None:
    """A numbered step: an ink disc with a butter number, just outside where
    the pen started."""
    painter.save()
    painter.setOpacity(opacity)
    c = QPointF(at.x() - 11, at.y() - 11)
    painter.translate(c)
    painter.scale(scale, scale)
    painter.setPen(QPen(QColor(255, 255, 255, 235), 2.0))
    painter.setBrush(theme.INK)
    painter.drawEllipse(QPointF(0, 0), 10, 10)
    painter.setPen(theme.BUTTER)
    painter.setFont(theme.ui_font(8.5, QFont.Weight.DemiBold))
    painter.drawText(QRectF(-10, -10, 20, 20), Qt.AlignmentFlag.AlignCenter, str(n))
    painter.restore()
