"""3D, drawn natively (numpy and QPainter, no web engine, no GPU needed).

``NoteModel`` (the second half of this file) is what Clixy uses: when a
learning question needs depth (an engine, a molecule, an orbit, vectors, a
surface), the sketch note's items describe a model and the note card shows
it solid, lit and slowly turning, with its moving parts moving and labels on
leader lines beside it. The AI only ever writes parts as JSON, never code.

The first half is the older floating overlay (``ModelOverlay``: a model over
the whole screen with numbered chips). It is not wired in.

Threading: the note is asked for on a worker thread (the sketch call); models
are built and drawn on the Qt thread.
"""
from __future__ import annotations

import math
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter, QPen,
                         QPolygonF, QRadialGradient)
from PyQt6.QtWidgets import QWidget

import theme

MAX_PARTS = 40
MAX_TRIANGLES = 9000

# --- The parts library: shape -> (vertices (n,3), triangles (m,3)), y is up --------------

def _box(w=1.0, h=1.0, d=1.0, **_):
    x, y, z = w / 2, h / 2, d / 2
    v = np.array([[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z],
                  [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]], float)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [3, 6, 2], [3, 7, 6], [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]])
    return v, f


def _cylinder(radius=0.5, height=1.0, radius_top=None, segments=28, caps=True, **_):
    rt = radius if radius_top is None else radius_top
    n = int(max(6, min(64, segments)))
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    bottom = np.stack([radius * np.cos(a), np.full(n, -height / 2), radius * np.sin(a)], 1)
    top = np.stack([rt * np.cos(a), np.full(n, height / 2), rt * np.sin(a)], 1)
    v = np.vstack([bottom, top, [[0, -height / 2, 0], [0, height / 2, 0]]])
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [[i, n + i, j], [j, n + i, n + j]]
        if caps:
            f += [[2 * n, i, j], [2 * n + 1, n + j, n + i]]
    return v, np.array(f)


def _cone(radius=0.5, height=1.0, segments=28, **_):
    return _cylinder(radius=radius, height=height, radius_top=0.0, segments=segments)


def _sphere(radius=0.5, segments=24, rings=14, **_):
    n, m = int(max(8, min(48, segments))), int(max(6, min(24, rings)))
    v = []
    for r in range(m + 1):
        phi = np.pi * r / m
        for s in range(n):
            th = 2 * np.pi * s / n
            v.append([radius * np.sin(phi) * np.cos(th), radius * np.cos(phi), radius * np.sin(phi) * np.sin(th)])
    f = []
    for r in range(m):
        for s in range(n):
            a, b = r * n + s, r * n + (s + 1) % n
            c, d = (r + 1) * n + s, (r + 1) * n + (s + 1) % n
            f += [[a, c, b], [b, c, d]]
    return np.array(v, float), np.array(f)


def _torus(radius=0.6, tube=0.12, segments=36, tube_segments=12, **_):
    n, m = int(max(8, min(64, segments))), int(max(6, min(20, tube_segments)))
    v = []
    for i in range(n):
        u = 2 * np.pi * i / n
        for j in range(m):
            w = 2 * np.pi * j / m
            r = radius + tube * np.cos(w)
            v.append([r * np.cos(u), tube * np.sin(w), r * np.sin(u)])
    f = []
    for i in range(n):
        for j in range(m):
            a, b = i * m + j, ((i + 1) % n) * m + j
            c, d = i * m + (j + 1) % m, ((i + 1) % n) * m + (j + 1) % m
            f += [[a, b, c], [b, d, c]]
    return np.array(v, float), np.array(f)


def _ring(radius=0.6, inner=0.3, segments=36, **_):
    """A flat washer in the xz plane, seen from both sides."""
    n = int(max(8, min(64, segments)))
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    v = np.vstack([np.stack([inner * np.cos(a), np.zeros(n), inner * np.sin(a)], 1),
                   np.stack([radius * np.cos(a), np.zeros(n), radius * np.sin(a)], 1)])
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [[i, n + i, j], [j, n + i, n + j], [i, j, n + i], [j, n + j, n + i]]
    return v, np.array(f)


def _blades(count=12, radius=0.9, inner=0.2, width=0.18, thickness=0.03, twist=30.0, **_):
    """Fan or propeller blades around the y axis, each twisted."""
    count = int(max(2, min(40, count)))
    vs, fs = [], []
    for i in range(count):
        v, f = _box(radius - inner, thickness, width)
        v[:, 0] += inner + (radius - inner) / 2
        t = math.radians(twist)
        rot = np.array([[1, 0, 0], [0, math.cos(t), -math.sin(t)], [0, math.sin(t), math.cos(t)]])
        v = v @ rot.T
        a = 2 * math.pi * i / count
        spin = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
        vs.append(v @ spin.T)
        fs.append(f + 8 * i)
    return np.vstack(vs), np.vstack(fs)


def _gear(radius=0.5, teeth=16, thickness=0.12, depth=None, **_):
    teeth = int(max(6, min(60, teeth)))
    depth = radius * 0.12 if depth is None else depth
    n = teeth * 4
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = radius if (i // 2) % 2 == 0 else radius - depth
        pts.append((r * math.cos(a), r * math.sin(a)))
    return _extrude(pts, thickness)


def _extrude(pts, height):
    """A flat outline (star-shaped about its centre) extruded along y."""
    n = len(pts)
    p = np.array(pts, float)
    bottom = np.stack([p[:, 0], np.full(n, -height / 2), p[:, 1]], 1)
    top = np.stack([p[:, 0], np.full(n, height / 2), p[:, 1]], 1)
    v = np.vstack([bottom, top, [[0, -height / 2, 0], [0, height / 2, 0]]])
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [[i, n + i, j], [j, n + i, n + j], [2 * n, i, j], [2 * n + 1, n + j, n + i]]
    return v, np.array(f)


def _tube(points=((0, 0, 0), (0, 1, 0)), radius=0.06, segments=10, **_):
    path = np.array([p for p in points if len(p) == 3][:60], float)
    if len(path) < 2:
        return np.zeros((0, 3)), np.zeros((0, 3), int)
    m = int(max(6, min(16, segments)))
    vs = []
    for k, c in enumerate(path):
        t = path[min(k + 1, len(path) - 1)] - path[max(k - 1, 0)]
        t = t / (np.linalg.norm(t) or 1)
        up = np.array([0, 1, 0]) if abs(t[1]) < 0.9 else np.array([1, 0, 0])
        a = np.cross(t, up)
        a /= np.linalg.norm(a) or 1
        b = np.cross(t, a)
        for j in range(m):
            w = 2 * math.pi * j / m
            vs.append(c + radius * (math.cos(w) * a + math.sin(w) * b))
    f = []
    for k in range(len(path) - 1):
        for j in range(m):
            p0, p1 = k * m + j, k * m + (j + 1) % m
            q0, q1 = p0 + m, p1 + m
            f += [[p0, q0, p1], [p1, q0, q1]]
    return np.array(vs), np.array(f)


def _shell(radius=0.6, inner=None, height=1.0, arc=200.0, segments=32, **_):
    """A cutaway wall: part of a thick tube, so you see inside."""
    inner = radius * 0.9 if inner is None else inner
    n = int(max(6, min(64, segments)))
    a = np.linspace(math.radians(90 - arc / 2), math.radians(90 + arc / 2), n)
    rows = []
    for r in (radius, inner):
        for y in (-height / 2, height / 2):
            rows.append(np.stack([r * np.cos(a), np.full(n, y), -r * np.sin(a)], 1))
    v = np.vstack(rows)
    ob, ot, ib, it = 0, n, 2 * n, 3 * n
    f = []
    for i in range(n - 1):
        j = i + 1
        f += [[ob + i, ot + i, ob + j], [ob + j, ot + i, ot + j]]          # outside
        f += [[ib + i, ib + j, it + i], [ib + j, it + j, it + i]]          # inside
        f += [[ot + i, it + i, ot + j], [ot + j, it + i, it + j]]          # top rim
        f += [[ob + i, ob + j, ib + i], [ob + j, ib + j, ib + i]]          # bottom rim
    return v, np.array(f)


SHAPES = {"box": _box, "cylinder": _cylinder, "cone": _cone, "sphere": _sphere, "torus": _torus, "ring": _ring,
          "blades": _blades, "gear": _gear, "tube": _tube, "shell": _shell}

MATERIALS = {  # base colour, specular, alpha
    "metal": ("#b9bfc8", 0.55, 1.0), "darkmetal": ("#5d6470", 0.45, 1.0), "paint": ("#ff6a3d", 0.25, 1.0),
    "plastic": ("#e9eaee", 0.2, 1.0), "rubber": ("#2a2b31", 0.04, 1.0), "glass": ("#a8d8ff", 0.4, 0.3),
    "glow": ("#ffd23f", 0.0, 1.0), "skin": ("#e8b49a", 0.1, 1.0), "wood": ("#b07a4a", 0.08, 1.0),
}

CHIP_COLORS = [QColor(255, 214, 224), QColor(208, 245, 214), QColor(212, 230, 255), QColor(255, 238, 190),
               QColor(232, 220, 255), QColor(255, 224, 200)]


def _rotation(degrees) -> np.ndarray:
    rx, ry, rz = (math.radians(float(d)) for d in (list(degrees) + [0, 0, 0])[:3])
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def _axis_rotation(axis: str, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


class Part:
    """One shaped part, placed and turned, perhaps spinning."""

    def __init__(self, spec: dict) -> None:
        shape = SHAPES[spec["shape"]]
        args = {k: v for k, v in spec.items() if k not in ("id", "shape", "at", "rotate", "color", "material", "spin",
                                                             "spin_axis", "label")}
        try:
            self.v, self.f = shape(**args)
        except Exception:
            self.v, self.f = np.zeros((0, 3)), np.zeros((0, 3), int)
        self.id = str(spec.get("id") or spec["shape"])
        material = spec.get("material") if spec.get("material") in MATERIALS else "metal"
        base, self.spec, self.alpha = MATERIALS[material]
        self.glow = material == "glow"
        color = QColor(str(spec.get("color") or base))
        self.color = color if color.isValid() else QColor(base)
        self.at = np.array((list(spec.get("at") or [0, 0, 0]) + [0, 0, 0])[:3], float)
        self.turn = _rotation(spec.get("rotate") or [0, 0, 0])
        self.spin = float(spec.get("spin") or 0.0)
        self.spin_axis = str(spec.get("spin_axis") or "y")

    def world(self, t: float) -> np.ndarray:
        v = self.v
        if self.spin:
            v = v @ _axis_rotation(self.spin_axis, 2 * math.pi * self.spin * t).T
        return v @ self.turn.T + self.at


def parse_model(model: dict) -> dict | None:
    """The model in a note, checked: known shapes only, numbers only, sizes capped."""
    if not isinstance(model, dict) or not isinstance(model.get("parts"), list):
        return None
    parts = []
    for spec in model["parts"][:MAX_PARTS]:
        if not isinstance(spec, dict) or spec.get("shape") not in SHAPES:
            continue
        clean = {}
        for k, v in spec.items():
            if k in ("id", "shape", "material", "color", "spin_axis", "label"):
                clean[k] = str(v)[:40]
            elif k == "points" and isinstance(v, list):
                clean[k] = [[float(c) for c in p[:3]] for p in v[:60] if isinstance(p, (list, tuple)) and len(p) >= 3
                            and all(isinstance(c, (int, float)) for c in p[:3])]
            elif k in ("at", "rotate") and isinstance(v, (list, tuple)):
                clean[k] = [float(c) for c in v[:3] if isinstance(c, (int, float))]
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                clean[k] = max(-50.0, min(50.0, float(v))) if k not in ("count", "teeth", "segments", "rings",
                                                                         "tube_segments") else int(max(1, min(64, v)))
        parts.append(clean)
    if not parts:
        return None
    labels = [{"part": str(l.get("part", ""))[:40], "text": str(l.get("text", ""))[:28]}
              for l in (model.get("labels") or []) if isinstance(l, dict) and l.get("text")][:8]
    return {"parts": parts, "labels": labels, "title": str(model.get("title") or "")[:60]}


# --- Drawing ------------------------------------------------------------------------------

class Scene:
    """A parsed model, ready to draw at any time ``t`` and turn ``angle``."""

    def __init__(self, model: dict) -> None:
        self.model = model
        self.parts = [Part(p) for p in model["parts"]]
        self.parts = [p for p in self.parts if len(p.f)]
        total = sum(len(p.f) for p in self.parts)
        if total > MAX_TRIANGLES:  # too detailed: keep the first parts that fit
            kept, count = [], 0
            for p in self.parts:
                if count + len(p.f) <= MAX_TRIANGLES:
                    kept.append(p)
                    count += len(p.f)
            self.parts = kept
        pts = np.vstack([p.world(0) for p in self.parts]) if self.parts else np.zeros((1, 3))
        lo, hi = pts.min(0), pts.max(0)
        self.centre = (lo + hi) / 2
        self.radius = float(np.linalg.norm(hi - lo) / 2) or 1.0
        self.bottom = float(lo[1])

    def paint(self, painter: QPainter, rect: QRectF, t: float, angle: float = 0.0,
              highlight: str | None = None) -> dict[str, QPointF]:
        """Draw the model into ``rect``; returns where each part's centre is on screen."""
        az = math.radians(32 + angle)
        el = math.radians(18)
        cam = np.array([math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)])
        forward = -cam
        right = np.cross(forward, [0, 1, 0])
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        dist = self.radius * 3.0
        eye = self.centre + cam * dist
        focal = min(rect.width(), rect.height()) * 1.15
        light = cam * 0.6 + up * 0.7 - right * 0.35
        light /= np.linalg.norm(light)
        half = (light + cam) / np.linalg.norm(light + cam)

        def project(p: np.ndarray) -> np.ndarray:
            rel = p - eye
            z = rel @ forward
            return np.stack([rect.center().x() + focal * (rel @ right) / z,
                             rect.center().y() - focal * (rel @ up) / z, z], -1)

        # The soft shadow under it.
        foot = project(np.array([[self.centre[0], self.bottom - self.radius * 0.05, self.centre[2]]]))[0]
        shadow = QRadialGradient(QPointF(foot[0], foot[1]), self.radius * focal / dist * 0.9)
        shadow.setColorAt(0, QColor(0, 0, 0, 70))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        sw = self.radius * focal / dist * 0.9
        painter.drawEllipse(QRectF(foot[0] - sw, foot[1] - sw * 0.22, 2 * sw, sw * 0.44))

        tris, centres = [], {}
        for part in self.parts:
            w = part.world(t)
            s = project(w)
            centres[part.id] = QPointF(*project(w.mean(0, keepdims=True))[0][:2])
            a, b, c = w[part.f[:, 0]], w[part.f[:, 1]], w[part.f[:, 2]]
            n = np.cross(b - a, c - a)
            n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
            view = eye - (a + b + c) / 3
            facing = np.einsum("ij,ij->i", n, view) > 0
            n = np.where(facing[:, None], n, -n)  # thin and open parts show both sides
            diffuse = np.clip(n @ light, 0, 1)
            spec = np.clip(n @ half, 0, 1) ** 28 * part.spec
            shade = 0.38 + 0.62 * diffuse
            lit = part.id == highlight
            base = np.array([part.color.redF(), part.color.greenF(), part.color.blueF()])
            if part.glow:
                rgb = np.repeat(base[None], len(n), 0)
            else:
                rgb = base[None] * shade[:, None] + spec[:, None]
                if lit:
                    accent = theme.current_palette().flood
                    rgb = rgb * 0.7 + np.array([accent.redF(), accent.greenF(), accent.blueF()]) * 0.3
            rgb = np.clip(rgb, 0, 1)
            depth = (s[part.f[:, 0], 2] + s[part.f[:, 1], 2] + s[part.f[:, 2], 2]) / 3
            for k in range(len(part.f)):
                if depth[k] <= 0.01:
                    continue
                tris.append((depth[k], s[part.f[k], :2], rgb[k], part.alpha))
        tris.sort(key=lambda tri: -tri[0])
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for _depth, pts, rgb, alpha in tris:
            color = QColor.fromRgbF(float(rgb[0]), float(rgb[1]), float(rgb[2]), alpha)
            painter.setBrush(color)
            painter.setPen(QPen(color, 0.7) if alpha >= 1 else Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in pts]))
        return centres


def paint_chip(painter: QPainter, at: QPointF, text: str, number: int, color: QColor, reveal: float = 1.0) -> QRectF:
    """A numbered label chip: "1 · Fan", in a soft colour, with a little shadow."""
    painter.save()
    font = theme.ui_font(10, QFont.Weight.DemiBold)
    painter.setFont(font)
    label = f"{number} · {text}"
    w = painter.fontMetrics().horizontalAdvance(label) + 22
    rect = QRectF(at.x() - w / 2, at.y() - 13, w, 26)
    painter.setOpacity(painter.opacity() * reveal)
    painter.translate(rect.center())
    painter.scale(0.85 + 0.15 * reveal, 0.85 + 0.15 * reveal)
    painter.translate(-rect.center())
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 50))
    painter.drawRoundedRect(rect.translated(0, 2), 8, 8)
    painter.setBrush(color)
    painter.setPen(QPen(color.darker(118), 1.0))
    painter.drawRoundedRect(rect, 8, 8)
    painter.setPen(QColor(30, 30, 38))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
    painter.restore()
    return rect


# --- The overlay -----------------------------------------------------------------------------

class ModelOverlay(QWidget):
    """The model floating over the screen the mouse is on: click-through, it
    never takes focus. Chips arrive one by one; it fades after a while or
    when you ask something new."""

    CHIP_EVERY_S = 2.2
    STAY_S = 75.0

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.scene: Scene | None = None
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def show_model(self, model: dict, screen=None) -> bool:
        clean = parse_model(model)
        if clean is None:
            return False
        self.scene = Scene(clean)
        if not self.scene.parts:
            self.scene = None
            return False
        screen = screen or QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        self._t0 = time.monotonic()
        self.show()
        try:
            from overlay import apply_clickthrough_styles
            apply_clickthrough_styles(int(self.winId()))
        except Exception:
            pass
        self._timer.start(33)
        return True

    def dismiss(self) -> None:
        self._timer.stop()
        self.scene = None
        self.hide()

    def area(self) -> QRectF:
        """Where the model floats: the right-centre of the screen, clear of the notch."""
        w, h = self.width(), self.height()
        size = min(w * 0.42, h * 0.62)
        return QRectF(w * 0.62 - size / 2, h * 0.5 - size / 2 + 20, size, size)

    def _tick(self) -> None:
        if time.monotonic() - self._t0 > self.STAY_S:
            self.dismiss()
            return
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.scene is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = time.monotonic() - self._t0
        fade = min(1.0, t / 0.6) * min(1.0, max(0.0, (self.STAY_S - t) / 1.5))
        p.setOpacity(fade)
        rect = self.area()
        grow = 0.86 + 0.14 * min(1.0, t / 0.8)
        p.translate(rect.center())
        p.scale(grow, grow)
        p.translate(-rect.center())
        labels = self.scene.model.get("labels") or []
        shown = int(max(0.0, t - 0.8) / self.CHIP_EVERY_S) + 1 if labels else 0
        current = labels[min(shown, len(labels)) - 1]["part"] if shown and labels else None
        centres = self.scene.paint(p, rect, t, angle=22 * math.sin(t * 0.35), highlight=current)
        paint_labels(p, labels[:shown], centres, rect, t)
        p.end()


def paint_labels(p: QPainter, labels: list, centres: dict, rect: QRectF, t: float = 99.0) -> None:
    """Chips beside the model with a leader line to each part, alternating sides."""
    for k, label in enumerate(labels):
        anchor = centres.get(label["part"])
        if anchor is None:
            continue
        left = k % 2 == 1
        y = rect.top() + rect.height() * (0.18 + 0.64 * (k // 2 + 0.5) / max(1, (len(labels) + 1) // 2))
        chip_at = QPointF(rect.left() - 10 if left else rect.right() + 10, y)
        p.save()
        pen = QPen(QColor(30, 30, 38, 200), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(chip_at, anchor)
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.setBrush(QColor(30, 30, 38))
        p.drawEllipse(anchor, 3.5, 3.5)
        p.restore()
        paint_chip(p, chip_at, label["text"], k + 1, CHIP_COLORS[k % len(CHIP_COLORS)])


# --- 3D on the note card ---------------------------------------------------------------------
# A learning question that needs depth gets a real 3D model on the note card:
# solid, lit parts that turn slowly, moving parts that move, and labels beside
# it on leader lines that never sit on each other. The sketch model writes the
# parts as note items (sketch.PROMPT); NoteModel builds their meshes once, and
# each frame numpy shades, culls and depth-sorts every face while QPainter
# fills them far to near (a few milliseconds). Spheres are drawn as shaded
# discs, so atoms and planets stay perfectly round and cost one call each.

MAX_FACES = 7000

SOLID_COLORS = {  # paintable versions of the note's pen colours
    "ink": (0.33, 0.34, 0.40), "blue": (0.35, 0.55, 0.93), "red": (0.90, 0.36, 0.31),
    "green": (0.29, 0.67, 0.45), "orange": (0.96, 0.60, 0.24), "purple": (0.55, 0.44, 0.93),
    "grey": (0.71, 0.72, 0.76), "gray": (0.71, 0.72, 0.76), "teal": (0.20, 0.68, 0.70),
    "yellow": (0.98, 0.79, 0.26), "white": (0.94, 0.94, 0.95), "black": (0.20, 0.20, 0.24),
    "brown": (0.62, 0.44, 0.30), "silver": (0.78, 0.80, 0.84), "gold": (0.90, 0.72, 0.30),
}
_AUTO_COLORS = ("blue", "orange", "green", "purple", "red", "teal")
_MATERIALS = {  # specular strength, shininess, opacity
    "plastic": (0.20, 22.0, 1.0), "metal": (0.34, 48.0, 1.0), "matte": (0.04, 8.0, 1.0),
    "glass": (0.50, 40.0, 0.28), "glow": (0.0, 1.0, 1.0),
}
_ELEMENTS = {  # colour, ball radius in ångström (ball and stick)
    "H": ((0.93, 0.94, 0.96), 0.25), "C": ((0.30, 0.31, 0.35), 0.36), "N": ((0.27, 0.42, 0.93), 0.35),
    "O": ((0.91, 0.25, 0.22), 0.35), "S": ((0.95, 0.78, 0.25), 0.42), "P": ((0.98, 0.55, 0.15), 0.42),
    "F": ((0.55, 0.85, 0.40), 0.30), "Cl": ((0.30, 0.78, 0.35), 0.40), "Br": ((0.62, 0.18, 0.15), 0.44),
    "I": ((0.45, 0.20, 0.60), 0.48), "Na": ((0.60, 0.40, 0.90), 0.50), "K": ((0.55, 0.30, 0.85), 0.55),
    "Mg": ((0.40, 0.75, 0.35), 0.45), "Ca": ((0.45, 0.80, 0.40), 0.50), "Fe": ((0.85, 0.48, 0.20), 0.42),
    "Cu": ((0.80, 0.50, 0.25), 0.40), "Zn": ((0.50, 0.52, 0.70), 0.40), "Si": ((0.85, 0.70, 0.50), 0.42),
    "Li": ((0.75, 0.50, 0.90), 0.45), "He": ((0.80, 0.95, 0.95), 0.28), "B": ((0.95, 0.70, 0.65), 0.36),
}
_INK = QColor(42, 42, 51)
_PAPER = QColor(251, 248, 242, 215)     # the note card's paper, for halos behind labels on the model


# --- Small helpers -------------------------------------------------------------------------------

def _f(value, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v)) if math.isfinite(v) else default


def _v3(value, default=None):
    try:
        v = np.array([float(value[0]), float(value[1]), float(value[2])])
    except (TypeError, ValueError, IndexError, KeyError):
        return None if default is None else np.array(default, float)
    if not np.all(np.isfinite(v)):
        return None if default is None else np.array(default, float)
    return np.clip(v, -1e4, 1e4)


def _unit(v, default=(0.0, 0.0, 1.0)) -> np.ndarray:
    v = np.asarray(v, float) if v is not None else np.array(default, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else np.array(default, float)


def _rgb(value, fallback) -> np.ndarray:
    if isinstance(value, str):
        key = value.strip().lower()
        if key in SOLID_COLORS:
            return np.array(SOLID_COLORS[key])
        if key.startswith("#"):
            c = QColor(key)
            if c.isValid():
                return np.array([c.redF(), c.greenF(), c.blueF()])
    return np.array(fallback, float)


def _about(axis: np.ndarray, angle: float) -> np.ndarray:
    """Rotation by ``angle`` (radians) about the unit ``axis``."""
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) * c + s * k + (1 - c) * np.outer(axis, axis)


def _turn_to(d: np.ndarray) -> np.ndarray:
    """The rotation that takes +z to the unit vector ``d``."""
    c = float(d[2])
    if c < -0.9999:
        return np.diag([1.0, -1.0, -1.0])
    v = np.array([-d[1], d[0], 0.0])        # z x d
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def _euler(degrees) -> np.ndarray:
    rx, ry, rz = (math.radians(_f(d, 0, -720, 720)) for d in (list(degrees) + [0, 0, 0])[:3])
    return _about(np.array([0.0, 0, 1]), rz) @ _about(np.array([0.0, 1, 0]), ry) @ _about(np.array([1.0, 0, 0]), rx)


# --- Meshes, in the part's own frame with its axis along +z. Faces are quads, outward
#     and anticlockwise; a triangle repeats its last corner. ------------------------------------

def _m_box(w: float, d: float, h: float):
    x, y, z = w / 2, d / 2, h / 2
    v = np.array([[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z],
                  [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]], float)
    q = np.array([[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [3, 0, 4, 7]])
    return v, q


def _circle(r: float, n: int) -> np.ndarray:
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([r * np.cos(a), r * np.sin(a)])


def _m_prism(outline: np.ndarray, h: float, top: float = 1.0, caps: bool = True):
    """A flat outline (anticlockwise) pulled up along z from -h/2 to h/2; ``top``
    scales the top (0 makes a cone). The flat ends come back as whole polygons
    (one fill each, so no seams), apart from the side quads."""
    n = len(outline)
    v = np.vstack([np.column_stack([outline, np.full(n, -h / 2)]),
                   np.column_stack([outline * top, np.full(n, h / 2)])])
    i = np.arange(n)
    j = (i + 1) % n
    ends = []
    if caps:
        ends.append(i[::-1].copy())                 # the bottom, seen from below
        if top > 1e-6:
            ends.append(n + i)                      # the top, seen from above
    return v, np.column_stack([i, j, n + j, n + i]), ends


def _m_hollow(r: float, inner: float, h: float, n: int = 32, arc: float = 360.0):
    """A pipe wall; with ``arc`` under 360 it is cut open around +x, and the two
    cut faces (returned count) show the wall's thickness."""
    full = arc >= 359.0
    if full:
        a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    else:
        cut = math.radians(360 - arc)
        a = np.linspace(cut / 2, 2 * np.pi - cut / 2, n + 1)
    m = len(a)

    def ring(rad, z):
        return np.column_stack([rad * np.cos(a), rad * np.sin(a), np.full(m, z)])

    rings = np.vstack([ring(r, -h / 2), ring(r, h / 2), ring(inner, -h / 2), ring(inner, h / 2)])
    v = np.vstack([rings, rings])     # walls use the first copy; rims and cut faces the second
    k = np.arange(m if full else m - 1)
    k1 = (k + 1) % m
    ob, ot, ib, it = 0, m, 2 * m, 3 * m
    walls = [np.column_stack([ob + k, ob + k1, ot + k1, ot + k]), np.column_stack([ib + k, it + k, it + k1, ib + k1])]
    c = 4 * m
    flat = [np.column_stack([c + ot + k, c + ot + k1, c + it + k1, c + it + k]),
            np.column_stack([c + ob + k, c + ib + k, c + ib + k1, c + ob + k1])]
    if not full:
        last = m - 1
        flat.append(np.array([[c + ob, c + ot, c + it, c + ib],
                              [c + ob + last, c + ib + last, c + it + last, c + ot + last]]))
    q = np.vstack(walls + flat)
    smooth = np.zeros(len(q), bool)
    smooth[:2 * len(k)] = True
    return v, q, (0 if full else 2), smooth


def _m_torus(big: float, small: float, n: int = 32, m: int = 12):
    u = np.linspace(0, 2 * np.pi, n, endpoint=False)[:, None]
    w = np.linspace(0, 2 * np.pi, m, endpoint=False)[None, :]
    ring = big + small * np.cos(w)
    v = np.stack([ring * np.cos(u), ring * np.sin(u), np.broadcast_to(small * np.sin(w), (n, m))],
                 -1).reshape(-1, 3)
    i = np.arange(n)[:, None]
    k = np.arange(m)[None, :]

    def idx(a, b):
        return (a % n) * m + (b % m)
    q = np.stack([idx(i, k), idx(i + 1, k), idx(i + 1, k + 1), idx(i, k + 1)], -1).reshape(-1, 4)
    return v, q


def _m_tube(path: np.ndarray, r: float, n: int = 10):
    """A round pipe along a path (open ends), with frames carried along so it never twists."""
    k = len(path)
    tangents = np.gradient(path, axis=0)
    tangents /= np.linalg.norm(tangents, axis=1, keepdims=True) + 1e-12
    ref = np.array([0.0, 0, 1]) if abs(tangents[0][2]) < 0.9 else np.array([1.0, 0, 0])
    a = _unit(np.cross(tangents[0], ref))
    w = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rings = []
    for c, t in zip(path, tangents):
        a = _unit(a - (a @ t) * t, a)
        b = np.cross(t, a)
        rings.append(c + r * (np.cos(w)[:, None] * a + np.sin(w)[:, None] * b))
    v = np.vstack(rings)
    s = np.arange(k - 1)[:, None] * n
    j = np.arange(n)[None, :]
    j1 = (j + 1) % n
    q = np.stack([s + j, s + j1, s + n + j1, s + n + j], -1).reshape(-1, 4)
    return v, q


def _m_arrow(length: float, r: float, segments: int = 10):
    """An arrow along +z from 0 to ``length``: a shaft and a cone head."""
    head = min(length * 0.42, r * 6.5)
    rh = r * 2.5
    v1, q1, e1 = _m_prism(_circle(r, segments), max(1e-6, length - head), caps=True)
    v1[:, 2] += (length - head) / 2
    v2, q2, e2 = _m_prism(_circle(rh, segments + 4), head, top=0.0)
    v2[:, 2] += length - head / 2
    return np.vstack([v1, v2]), np.vstack([q1, q2 + len(v1)]), e1[:1] + [e + len(v1) for e in e2]


def _gear_outline(r: float, teeth: int) -> np.ndarray:
    n = teeth * 4
    depth = r * 0.14
    a = 2 * np.pi * np.arange(n) / n
    rad = np.where((np.arange(n) // 2) % 2 == 0, r, r - depth)
    return np.column_stack([rad * np.cos(a), rad * np.sin(a)])


def _volume(v: np.ndarray, q: np.ndarray, ends=()) -> float:
    """Signed volume of a closed mesh: positive when every face points out."""
    a, b, c, d = (v[q[:, k]] for k in range(4))
    area = 0.5 * np.cross(c - a, d - b)
    total = float(np.sum(np.einsum("ij,ij->i", (a + b + c + d) / 4, area)))
    for e in ends:
        pts = v[e]
        newell = 0.5 * np.cross(pts, np.roll(pts, -1, axis=0)).sum(0)
        total += float(pts.mean(0) @ newell)
    return total / 3


# --- What a model is made of ---------------------------------------------------------------------

class _Motion:
    """How a part moves: a spin about its axis (or a pivot), a slide back and
    forth, or sliding in step with another part."""

    def __init__(self, item: dict, axis: np.ndarray, centre: np.ndarray) -> None:
        self.spin = _f(item.get("spin"), 0.0, -4.0, 4.0)
        self.axis = _unit(_v3(item.get("spin_axis"), axis))
        self.pivot = _v3(item.get("pivot"), centre)
        self.slide = _v3(item.get("slide"))
        self.rate = _f(item.get("rate"), 0.5, 0.02, 4.0)
        self.phase = math.radians(_f(item.get("phase"), 0.0, -720, 720))
        follow = item.get("follow")
        self.follow = str(follow)[:40] if isinstance(follow, str) and follow else None
        self.dir = _unit(self.slide) if self.slide is not None and np.linalg.norm(self.slide) > 1e-9 else axis
        self.moves = bool(self.spin or self.slide is not None or self.follow)

    def apply(self, pts: np.ndarray, t: float, moved: dict) -> np.ndarray:
        if self.spin:
            rot = _about(self.axis, 2 * math.pi * self.spin * t)
            pts = (pts - self.pivot) @ rot.T + self.pivot
        if self.slide is not None:
            pts = pts + self.slide * 0.5 * math.sin(2 * math.pi * self.rate * t + self.phase)
        if self.follow and self.follow in moved:
            pts = pts + self.dir * float(moved[self.follow] @ self.dir)
        return pts


class _Solid:
    """A meshed part: where it rests, how it's shaded, how it moves."""

    def __init__(self, v, q, *, color, material: str, closed: bool, item: dict | None = None, at=None,
                 turn=None, edges: bool = False, sections: int = 0, anchor=None, face_rgb=None, ends=(),
                 smooth=False, vertex_rgb=None, lift: float = 0.0) -> None:
        turn = np.eye(3) if turn is None else turn
        at = np.zeros(3) if at is None else at
        self.rest = np.asarray(v, float) @ turn.T + at
        self.quads = np.asarray(q, int).reshape(-1, 4)
        self.ends = [np.asarray(e, int) for e in ends if len(e) >= 3]    # flat polygons (caps)
        self.smooth = np.broadcast_to(np.asarray(smooth, bool), (len(self.quads),)).copy()  # shaded as curved
        lo, hi = self.rest.min(0), self.rest.max(0)
        self.anchor = (lo + hi) / 2 if anchor is None else np.asarray(anchor, float)
        self.color = np.asarray(color, float)
        self.spec, self.shine, self.alpha = _MATERIALS.get(material, _MATERIALS["plastic"])
        self.glow = material == "glow"
        self.closed = closed and self.alpha >= 1.0
        self.edges = edges
        self.sections = sections       # the last faces are cut faces, drawn lighter
        self.face_rgb = face_rgb       # a colour per face (a surface coloured by height)
        self.vertex_rgb = vertex_rgb   # ...and per corner, so the colours blend
        self.lift = lift               # brighter shadows (an open surface seen from below)
        item = item or {}
        self.id = str(item.get("id") or "")[:40] or None
        self.motion = _Motion(item, turn @ np.array([0.0, 0, 1]), at)
        self.link = None               # (from, to, radius): a rod joining two parts, rebuilt each frame
        self.unit = None
        self.offset = 0


class _Ball:
    """A sphere, drawn as a shaded disc: an atom, a planet, a point."""

    def __init__(self, centre, r: float, color, material: str = "plastic", item: dict | None = None,
                 symbol: str = "", path=None, rate: float = 0.1) -> None:
        self.centre = np.asarray(centre, float)
        self.r = float(r)
        self.color = np.asarray(color, float)
        self.spec, self.shine, self.alpha = _MATERIALS.get(material, _MATERIALS["plastic"])
        self.glow = material == "glow"
        item = item or {}
        self.id = str(item.get("id") or "")[:40] or None
        self.motion = _Motion(item, np.array([0.0, 0, 1]), self.centre)
        self.symbol = symbol
        self.path = path               # a traveller: it goes round this path
        self.rate = rate

    def at(self, t: float, moved: dict) -> np.ndarray:
        if self.path is not None:
            seg = np.linalg.norm(np.diff(self.path, axis=0), axis=1)
            total = float(seg.sum()) or 1.0
            s = (t * self.rate % 1.0) * total
            k = int(np.searchsorted(np.cumsum(seg), s))
            k = min(k, len(seg) - 1)
            before = float(np.sum(seg[:k]))
            u = (s - before) / (seg[k] or 1.0)
            return self.path[k] + (self.path[k + 1] - self.path[k]) * u
        if self.motion.moves:
            return self.motion.apply(self.centre[None], t, moved)[0]
        return self.centre


class _Line:
    def __init__(self, pts, color: QColor, width: float = 2.0, dashed: bool = False, head: bool = False) -> None:
        self.pts = np.asarray(pts, float)
        self.color, self.width, self.dashed, self.head = color, width, dashed, head


class _Label:
    """A name for something in the model. ``side`` labels sit in a column beside
    the model on a leader line; ``tip`` labels sit right by their point."""

    def __init__(self, text: str, kind: str, ref: tuple, color: QColor) -> None:
        self.text, self.kind, self.ref, self.color = text, kind, ref, color
        self.side = 1          # -1 left column, 1 right column
        self.spot = None       # a tip label's chosen offset, kept while it fits
        self.toward = None     # for an arrow's name: where the arrow comes from (the name goes beyond its tip)
        self.rect = QRectF()


def _pen_color(rgb: np.ndarray) -> QColor:
    """A label's colour: the part's colour, dark enough to read on paper."""
    r, g, b = (float(c) for c in rgb)
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    if max(r, g, b) - min(r, g, b) < 0.12:
        return QColor(_INK)
    k = min(1.0, 0.42 / max(lum, 1e-3))
    return QColor.fromRgbF(r * k, g * k, b * k)


# --- The model ---------------------------------------------------------------------------------

class NoteModel:
    """A note's 3D items as a solid model, ready to draw at any turn and time."""

    TURN = 26.0        # how far it rocks either way (degrees)
    ROW = 20.0         # a label row's height
    FONT = 14.0

    def __init__(self, note: dict, compile_expr) -> None:
        self._expr = compile_expr
        view = note.get("view") or {}
        self.az0 = _f(view.get("turn"), 35.0, -360, 360)
        self.el = _f(view.get("tilt"), 22.0, -20, 80)
        self.range = _f(view.get("range"), 0.0, 0.0, 1e4)
        self.solids: list[_Solid] = []
        self.balls: list[_Ball] = []
        self.lines: list[_Line] = []
        self.labels: list[_Label] = []
        self.captions: list[str] = []
        self._auto = 0
        self._faces = 0
        self._grounded = False
        self._vectors: list[tuple[np.ndarray, np.ndarray]] = []
        items =[i for i in note.get("items") or [] if isinstance(i, dict) and isinstance(i.get("type"), str)]
        later = ("vector3d", "arrow3d", "point3d", "field", "line3d", "curve3d", "text", "axes3d")
        for item in items:                     # the things themselves first: their size sets the scale
            if item["type"] not in later:
                self._try(item)
        self.unit = self._size(extra=[p for i in items for p in self._coords(i)])
        for item in items:
            if item["type"] in later and item["type"] != "axes3d":
                self._try(item)
        for item in items:
            if item["type"] == "axes3d":
                self._try(item)
        self._finish()

    @classmethod
    def from_note(cls, note: dict, compile_expr=None) -> "NoteModel | None":
        if compile_expr is None:
            from sketch import compile_expr
        model = cls(note, compile_expr)
        if not (model.solids or model.balls or model.lines):
            return None
        view = note.get("view") or {}
        if view.get("turn") is None or view.get("tilt") is None:
            az, el = model._best_view(view.get("turn") is None, view.get("tilt") is None)
            if (az, el) != (model.az0, model.el):
                note = {**note, "view": {**view, "turn": az, "tilt": el}}
                model = cls(note, compile_expr)       # rebuilt, so cut-aways face the new view
        return model

    def _best_view(self, free_turn: bool, free_tilt: bool) -> tuple[float, float]:
        """The view that shows the model most openly (its parts spread out, not
        lined up behind each other), staying near the usual three-quarter view."""
        pts = [s.rest[:: max(1, len(s.rest) // 60)] for s in self.solids if s.link is None]
        pts += [b.centre[None] for b in self.balls]
        pts += [ln.pts[:: max(1, len(ln.pts) // 20)] for ln in self.lines if not ln.head]
        if not pts:
            return self.az0, self.el
        allp = np.vstack(pts)
        allp = allp - allp.mean(0)
        axes = any(ln.head for ln in self.lines)
        turns = [self.az0] if not free_turn else (np.arange(5, 81, 7.5) if axes else np.arange(35, 395, 15) % 360)
        tilts = [self.el] if not free_tilt else (14.0, 22.0, 32.0)
        arrows = [_unit(b - a) for a, b in self._vectors]
        best, best_score, usual = (self.az0, self.el), -1.0, 0.0
        for az in turns:
            for el in tilts:
                a, e = math.radians(az), math.radians(el)
                eye = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
                right = _unit(np.cross(-eye, [0.0, 0, 1]), (1, 0, 0))
                up = np.cross(right, -eye)
                if arrows:
                    # Vectors: no arrow should point at the viewer (it would shrink to a dot),
                    # anywhere in the slow turn.
                    area = 1.0
                    for swing in (-self.TURN, 0.0, self.TURN):
                        b = math.radians(az + swing)
                        look = np.array([math.cos(e) * math.cos(b), math.cos(e) * math.sin(b), math.sin(e)])
                        area = min(area, min(math.sqrt(max(0.0, 1 - float(d @ look) ** 2)) for d in arrows))
                else:
                    flat = np.column_stack([allp @ right, allp @ up])
                    sv = np.linalg.svd(flat - flat.mean(0), compute_uv=False)
                    area = float(sv[0] * sv[1]) if len(sv) > 1 else 0.0
                # Prefer the usual view unless another is clearly better.
                turn_off = abs((az - 35.0 + 180) % 360 - 180) / 180
                score = area * (1 - 0.35 * turn_off) * (1 - 0.25 * abs(el - 22.0) / 30)
                if score > best_score * 1.0001:
                    best, best_score = (float(az), float(el)), score
                if abs(az - self.az0) < 1e-6 and abs(el - self.el) < 1e-6:
                    usual = score
        # Keep the usual view unless it hides the model badly (parts lined up, a flat thing
        # edge-on, an arrow end-on).
        return best if usual < (0.8 if arrows else 0.55) * best_score else (self.az0, self.el)

    # --- Building ---------------------------------------------------------------------------
    def _try(self, item: dict) -> None:
        build = getattr(self, "_i_" + item["type"].lower().replace("-", "_"), None)
        if build is None:
            return
        try:
            build(item)
        except Exception:      # one odd item never spoils the rest of the model
            pass

    def _color(self, item: dict, default: str | None = None) -> np.ndarray:
        if default is None and not item.get("color"):
            default = _AUTO_COLORS[self._auto % len(_AUTO_COLORS)]
            self._auto += 1
        if (item.get("material") == "metal") and not item.get("color"):
            return np.array(SOLID_COLORS["silver"])
        return _rgb(item.get("color"), SOLID_COLORS.get(default or "grey", SOLID_COLORS["grey"]))

    @staticmethod
    def _material(item: dict, default: str = "plastic") -> str:
        m = str(item.get("material") or default).lower()
        return m if m in _MATERIALS else default

    def _add(self, solid: _Solid, item: dict | None = None, label_kind: str = "side") -> _Solid | None:
        cost = len(solid.quads) + 4 * len(solid.ends)
        if self._faces + cost > MAX_FACES:
            return None
        self._faces += cost
        self.solids.append(solid)
        self._label(item, ("solid", len(self.solids) - 1), solid.color, label_kind)
        return solid

    def _label(self, item: dict | None, ref: tuple, rgb, kind: str = "side") -> None:
        text = str((item or {}).get("label") or "").strip()[:28]
        if text and len(self.labels) < 12:
            self.labels.append(_Label(text, kind, ref, _pen_color(np.asarray(rgb, float))))

    def _placement(self, item: dict):
        at = _v3(item.get("at"), (0, 0, 0))
        if isinstance(item.get("rotate"), (list, tuple)):
            return at, _euler(item["rotate"]), _unit(_euler(item["rotate"]) @ np.array([0.0, 0, 1]))
        axis = _unit(_v3(item.get("axis"), (0, 0, 1)))
        return at, _turn_to(axis), axis

    def _size_of(self, item: dict, key: str, default: float) -> float:
        return _f(item.get(key), default, 1e-3, 1e4)

    def _i_box3d(self, item: dict) -> None:
        self._grounded = True
        at, turn, _axis = self._placement(item)
        size = _v3(item.get("size"), (1, 1, 1))
        w, d, h = (max(1e-3, abs(float(s))) for s in size)
        v, q = _m_box(w, d, h)
        az, el = math.radians(self.az0), math.radians(self.el)
        eye = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
        centres = v[q].mean(1)
        front = centres[int(np.argmax((centres / np.array([w, d, h])) @ (turn.T @ eye)))]
        self._add(_Solid(v, q, color=self._color(item), material=self._material(item), closed=True, item=item,
                         at=at, turn=turn, edges=True, anchor=front * 0.92 @ turn.T + at), item)

    _i_box = _i_cube = _i_cuboid = _i_box3d

    def _i_cylinder(self, item: dict, cone: bool = False) -> None:
        self._grounded = True
        r = self._size_of(item, "r", 0.5)
        top = 0.0 if cone else _f(item.get("r_top"), r, 0.0, 1e4) / r
        ends = [item.get("from"), item.get("to")]
        n = 24 if r > 0.05 * (getattr(self, "unit", None) or r * 10) else 12
        color, material = self._color(item), self._material(item)
        if all(isinstance(e, str) for e in ends) or (any(isinstance(e, str) for e in ends)
                                                    and all(isinstance(e, (str, list, tuple)) for e in ends)):
            v, q, caps = _m_prism(_circle(1.0, n), 1.0, top=top)
            v[:, 2] += 0.5
            solid = _Solid(v, q, color=color, material=material, closed=True, item=item, ends=caps, smooth=True)
            solid.link, solid.unit = (ends[0], ends[1], r), v
            self._add(solid, item)
            return
        a, b = _v3(ends[0]), _v3(ends[1])
        if a is not None and b is not None and np.linalg.norm(b - a) > 1e-6:
            at, axis, h = (a + b) / 2, _unit(b - a), float(np.linalg.norm(b - a))
            turn = _turn_to(axis)
        else:
            at, turn, axis = self._placement(item)
            h = self._size_of(item, "h", 1.0)
        caps = []
        if item.get("hollow") and not cone:
            v, q, _cut, smooth = _m_hollow(r, r * _f(item.get("inner"), 0.8, 0.1, 0.98), h, n)
        else:
            v, q, caps = _m_prism(_circle(r, n), h, top=top)
            smooth = True
        self._add(_Solid(v, q, color=color, material=material, closed=True, item=item, at=at, turn=turn,
                         ends=caps, smooth=smooth), item)

    def _i_cone(self, item: dict) -> None:
        self._i_cylinder(item, cone=True)

    _i_disc = _i_disk = _i_rod = _i_pipe_wall = _i_cylinder

    def _i_cutaway(self, item: dict) -> None:
        self._grounded = True
        at, turn, axis = self._placement(item)
        r = self._size_of(item, "r", 0.6)
        h = self._size_of(item, "h", 1.0)
        inner = _f(item.get("inner"), 0.86, 1e-3, 1e4)
        inner = r * min(inner, 0.97) if inner < 1 else min(inner, r * 0.97)
        v, q, cut, smooth = _m_hollow(r, inner, h, 28, arc=_f(item.get("arc"), 230.0, 90.0, 330.0))
        # Turn the opening toward the viewer.
        az, el = math.radians(self.az0), math.radians(self.el)
        eye = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
        want = eye - (eye @ axis) * axis
        if np.linalg.norm(want) > 1e-6:
            have = turn @ np.array([1.0, 0, 0])
            angle = math.atan2(float(np.cross(have, want) @ axis), float(have @ _unit(want)))
            turn = _about(axis, angle) @ turn
        color = self._color(item, "grey" if not item.get("color") else None)
        # Anchor the label on the wall, beside the opening.
        anchor_local = np.array([inner * math.cos(math.radians(140)), inner * math.sin(math.radians(140)), h * 0.25])
        self._add(_Solid(v, q, color=color, material=self._material(item, "metal"), closed=True, item=item, at=at,
                         turn=turn, sections=cut, anchor=anchor_local @ turn.T + at, smooth=smooth), item)

    _i_shell = _i_cutaway

    def _i_sphere(self, item: dict) -> None:
        c = _v3(item.get("at"), (0, 0, 0))
        r = self._size_of(item, "r", 1.0)
        rgb = self._color(item)
        self.balls.append(_Ball(c, r, rgb, self._material(item), item))
        self._label(item, ("ball", len(self.balls) - 1), rgb)

    _i_ball = _i_planet = _i_sphere

    def _i_torus(self, item: dict) -> None:
        self._grounded = True
        at, turn, _axis = self._placement(item)
        big = self._size_of(item, "r", 1.0)
        small = _f(item.get("tube"), big * 0.2, 1e-3, big * 0.95)
        v, q = _m_torus(big, small)
        self._add(_Solid(v, q, color=self._color(item), material=self._material(item), closed=True, item=item,
                         at=at, turn=turn, anchor=np.array([big, 0, 0]) @ turn.T + at, smooth=True), item)

    _i_ring = _i_torus

    def _i_gear(self, item: dict) -> None:
        self._grounded = True
        at, turn, _axis = self._placement(item)
        r = self._size_of(item, "r", 0.6)
        teeth = int(_f(item.get("teeth"), 14, 6, 48))
        v, q, ends = _m_prism(_gear_outline(r, teeth), _f(item.get("thickness"), r * 0.25, 1e-3, 1e4))
        self._add(_Solid(v, q, color=self._color(item), material=self._material(item, "metal"), closed=True,
                         item=item, at=at, turn=turn, ends=ends), item)

    def _i_tube(self, item: dict) -> None:
        self._grounded = True
        pts = [p for p in (_v3(q) for q in (item.get("points") or [])[:80]) if p is not None]
        if len(pts) < 2:
            return
        path = np.array(pts)
        # Smooth the corners a little so a pipe bends rather than kinks.
        if len(path) >= 3:
            fine = [path[0]]
            for a, b, c in zip(path, path[1:], path[2:]):
                fine += [a * 0.25 + b * 0.75, b, b * 0.75 + c * 0.25]
            fine.append(path[-1])
            path = np.array(fine)
        r = self._size_of(item, "r", 0.08)
        v, q = _m_tube(path, r, 12)
        mid = path[len(path) // 2]
        self._add(_Solid(v, q, color=self._color(item), material=self._material(item), closed=False, item=item,
                         anchor=mid, smooth=True), item)

    _i_pipe = _i_wire = _i_tube

    def _i_plane(self, item: dict) -> None:
        at = _v3(item.get("at"), (0, 0, 0))
        normal = _unit(_v3(item.get("normal"), (0, 0, 1)))
        size = item.get("size") if isinstance(item.get("size"), (list, tuple)) else [2, 2]
        w, h = _f(size[0], 2, 1e-3, 1e4), _f(size[1] if len(size) > 1 else size[0], 2, 1e-3, 1e4)
        v = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]])
        material = self._material(item, "glass")
        self._add(_Solid(v, np.array([[0, 1, 2, 3]]), color=self._color(item), material=material, closed=False,
                         item=item, at=at, turn=_turn_to(normal), edges=True), item)

    def _i_molecule(self, item: dict) -> None:
        atoms = []
        for a in (item.get("atoms") or [])[:60]:
            if isinstance(a, (list, tuple)) and len(a) >= 4 and isinstance(a[0], str):
                p = _v3(a[1:4])
                if p is not None:
                    atoms.append((a[0].strip()[:2].capitalize(), p))
            elif isinstance(a, dict) and isinstance(a.get("element"), str) and _v3(a.get("at")) is not None:
                atoms.append((a["element"].strip()[:2].capitalize(), _v3(a["at"])))
        if not atoms:
            return
        bonds = []
        for b in (item.get("bonds") or [])[:90]:
            if isinstance(b, (list, tuple)) and len(b) >= 2:
                try:
                    i, j = int(b[0]), int(b[1])
                    order = int(b[2]) if len(b) > 2 else 1
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(atoms) and 0 <= j < len(atoms) and i != j:
                    bonds.append((i, j, max(1, min(3, order))))
        pos = np.array([p for _e, p in atoms])
        # Scale the balls to the molecule's own units: a typical bond is about 1.1 Å.
        if bonds:
            lengths = [float(np.linalg.norm(pos[i] - pos[j])) for i, j, _o in bonds]
        elif len(pos) > 1:
            d = np.linalg.norm(pos[:, None] - pos[None], axis=2) + np.eye(len(pos)) * 1e9
            lengths = list(d.min(1))
        else:
            lengths = [1.1]
        s = max(1e-3, float(np.median(lengths)) / 1.1)
        space = str(item.get("style", "")).lower().startswith("space")
        first = len(self.balls)
        many = len(atoms) > 12
        for element, p in atoms:
            rgb, rad = _ELEMENTS.get(element, ((0.85, 0.55, 0.75), 0.40))
            symbol = "" if many and element in ("C", "H") else element
            self.balls.append(_Ball(p, rad * s * (2.1 if space else 1.0), rgb, "plastic", symbol=symbol))
        if not space:
            stick = 0.075 * s
            for i, j, order in bonds:
                a, b = self.balls[first + i], self.balls[first + j]
                d = _unit(b.centre - a.centre)
                side = _unit(np.cross(d, [0.0, 0, 1]) if abs(d[2]) < 0.9 else np.cross(d, [1.0, 0, 0]))
                offsets = [0.0] if order == 1 else [-1.0, 1.0] if order == 2 else [-1.6, 0.0, 1.6]
                for o in offsets:
                    shift = side * o * stick * 1.6
                    start, end = a.centre + d * a.r * 0.8 + shift, b.centre - d * b.r * 0.8 + shift
                    mid = (start + end) / 2
                    for p0, p1, ball in ((start, mid, a), (mid, end, b)):
                        length = float(np.linalg.norm(p1 - p0))
                        if length < 1e-6:
                            continue
                        v, q, _e = _m_prism(_circle(stick * (0.7 if order > 1 else 1.0), 10), length, caps=False)
                        v[:, 2] += length / 2
                        self._add(_Solid(v, q, color=ball.color, material="plastic", closed=False, at=p0,
                                         turn=_turn_to(_unit(p1 - p0)), smooth=True))
        if item.get("label"):
            hub = first + int(np.argmin(np.linalg.norm(pos - pos.mean(0), axis=1)))
            self.labels.append(_Label(str(item["label"])[:28], "side", ("ball", hub), QColor(_INK)))

    def _i_surface(self, item: dict) -> None:
        fn = self._expr(item.get("expr", ""), ("x", "y"))
        if fn is None:
            return
        span = self.range or 2.0
        xr = item.get("x") if isinstance(item.get("x"), (list, tuple)) and len(item["x"]) == 2 else [-span, span]
        yr = item.get("y") if isinstance(item.get("y"), (list, tuple)) and len(item["y"]) == 2 else [-span, span]
        x0, x1 = _f(xr[0], -span, -1e4, 1e4), _f(xr[1], span, -1e4, 1e4)
        y0, y1 = _f(yr[0], -span, -1e4, 1e4), _f(yr[1], span, -1e4, 1e4)
        if x1 <= x0 or y1 <= y0:
            return
        n = 24
        xs, ys = np.linspace(x0, x1, n + 1), np.linspace(y0, y1, n + 1)
        z = np.array([[fn(x, y) for x in xs] for y in ys])
        if not np.isfinite(z).any():
            return
        reach = 2.5 * max(x1 - x0, y1 - y0)
        z = np.clip(z, -reach, reach)
        gx, gy = np.meshgrid(xs, ys)
        v = np.column_stack([gx.ravel(), gy.ravel(), np.nan_to_num(z).ravel()])
        i, j = np.meshgrid(np.arange(n), np.arange(n))
        a = (j * (n + 1) + i).ravel()
        q = np.column_stack([a, a + 1, a + n + 2, a + n + 1])
        ok = np.isfinite(z.ravel())[q].all(1)
        q = q[ok]
        if not len(q):
            return
        heights = v[q][:, :, 2].mean(1)
        lo, hi = float(heights.min()), float(heights.max())
        if item.get("color"):
            face_rgb = vertex_rgb = None
            color = _rgb(item["color"], SOLID_COLORS["blue"])
        else:
            def ramp(h):
                u = np.clip((h - lo) / (hi - lo), 0, 1) if hi > lo else np.full(len(h), 0.5)
                stops = np.array([[0.24, 0.38, 0.86], [0.18, 0.66, 0.74], [0.55, 0.80, 0.45], [0.98, 0.80, 0.30]])
                pos = u * (len(stops) - 1)
                k = np.minimum(pos.astype(int), len(stops) - 2)
                return stops[k] + (stops[k + 1] - stops[k]) * (pos - k)[:, None]
            face_rgb, vertex_rgb = ramp(heights), ramp(v[:, 2])
            color = face_rgb.mean(0)
        mid = np.array([(x0 + x1) / 2, (y0 + y1) / 2, 0.0])
        mz = fn(mid[0], mid[1])
        mid[2] = mz if math.isfinite(mz) else 0.0
        self._add(_Solid(v, q, color=color, material=self._material(item, "plastic"), closed=False, item=item,
                         anchor=mid, face_rgb=face_rgb, vertex_rgb=vertex_rgb, smooth=True, lift=0.3), item)

    def _i_vector3d(self, item: dict) -> None:
        a, b = _v3(item.get("from"), (0, 0, 0)), _v3(item.get("to"))
        if b is None or np.linalg.norm(b - a) < 1e-9:
            return
        length = float(np.linalg.norm(b - a))
        r = min(self.unit * 0.03, length * 0.07)
        v, q, ends = _m_arrow(length, r)
        rgb = self._color(item, "blue" if not item.get("color") else None)
        solid = _Solid(v, q, color=rgb, material=self._material(item), closed=True, item=item, at=a,
                       turn=_turn_to(_unit(b - a)), anchor=b, ends=ends, smooth=True, lift=0.3)
        if self._add(solid, None):
            self._vectors.append((a, b))
            named = len(self.labels)
            self._label(item, ("solid", len(self.solids) - 1), rgb, "tip")
            if len(self.labels) > named:
                self.labels[-1].toward = a

    _i_arrow3d = _i_vector3d

    def _i_point3d(self, item: dict) -> None:
        c = _v3(item.get("at"))
        if c is None:
            return
        rgb = _rgb(item.get("color"), SOLID_COLORS["red"])
        self.balls.append(_Ball(c, self.unit * 0.035, rgb, "plastic", item))
        self._label(item, ("ball", len(self.balls) - 1), rgb, "tip")

    def _i_line3d(self, item: dict) -> None:
        a, b = _v3(item.get("from"), (0, 0, 0)), _v3(item.get("to"))
        if b is None:
            return
        color = _pen(item.get("color"), "ink")
        self.lines.append(_Line(np.linspace(a, b, 12), color, 1.8, bool(item.get("dashed"))))
        self._label(item, ("centre", (a + b) / 2), _rgb(item.get("color"), SOLID_COLORS["ink"]), "tip")

    def _i_curve3d(self, item: dict) -> None:
        fx, fy, fz = (self._expr(item.get(k, "0"), ("t",)) for k in ("x", "y", "z"))
        if None in (fx, fy, fz):
            return
        a, b = _f(item.get("from"), 0.0, -1e4, 1e4), _f(item.get("to"), 2 * math.pi, -1e4, 1e4)
        ts = np.linspace(a, b, 241)
        pts = np.array([[fx(t), fy(t), fz(t)] for t in ts])
        pts = pts[np.isfinite(pts).all(1)]
        if len(pts) < 2:
            return
        color = _pen(item.get("color"), "blue")
        self.lines.append(_Line(pts, color, 2.4))
        rgb = _rgb(item.get("color"), SOLID_COLORS["blue"])
        if item.get("travel"):
            body = _rgb(item.get("body_color"), SOLID_COLORS["orange"])
            self.balls.append(_Ball(pts[0], self.unit * 0.07, body, "plastic", path=pts,
                                    rate=_f(item.get("rate"), 0.08, 0.01, 2.0)))
            self._label(item, ("ball", len(self.balls) - 1), rgb, "tip")
        else:
            self._label(item, ("centre", pts[-1]), rgb, "tip")

    def _i_field(self, item: dict) -> None:
        fx, fy, fz = (self._expr(item.get(k, "0"), ("x", "y", "z")) for k in ("x", "y", "z"))
        if None in (fx, fy, fz):
            return
        span = _f(item.get("range"), self.range or 2.0, 1e-3, 1e4)
        flat = str(item.get("z", "0")).strip() in ("0", "0.0") and not item.get("layers")
        count = int(_f(item.get("count"), 5 if flat else 4, 2, 7 if flat else 4))
        g = np.linspace(-span, span, count)
        pts = [(x, y, 0.0) for x in g for y in g] if flat else [(x, y, z) for x in g for y in g for z in g]
        vecs = np.array([[fx(*p), fy(*p), fz(*p)] for p in pts])
        ok = np.isfinite(vecs).all(1)
        pts, vecs = np.array(pts)[ok], vecs[ok]
        if not len(pts):
            return
        mags = np.linalg.norm(vecs, axis=1)
        top = float(mags.max()) or 1.0
        spacing = 2 * span / max(1, count - 1)
        drawn = []
        for p, vec, m in zip(pts, vecs, mags):
            if m < top * 0.04:
                continue
            length = spacing * 0.78 * (0.35 + 0.65 * m / top)
            u = m / top
            rgb = _rgb(item.get("color"), (0.30 + 0.62 * u, 0.50 - 0.18 * u, 0.92 - 0.62 * u)) \
                if item.get("color") else np.array([0.30 + 0.62 * u, 0.50 - 0.18 * u, 0.92 - 0.62 * u])
            v, q, ends = _m_arrow(length, spacing * 0.035, 8)
            start = p - _unit(vec) * length / 2
            if self._add(_Solid(v, q, color=rgb, material="plastic", closed=True, at=start,
                                turn=_turn_to(_unit(vec)), ends=ends, smooth=True, lift=0.3)):
                drawn.append(len(self.solids) - 1)
        if item.get("label") and drawn:
            near = min(drawn, key=lambda k: float(np.linalg.norm(self.solids[k].anchor - np.array([0, span * 0.7, 0]))))
            self.labels.append(_Label(str(item["label"])[:28], "side", ("solid", near), QColor(_INK)))

    def _i_text(self, item: dict) -> None:
        text = str(item.get("text") or "").strip()[:60]
        if not text:
            return
        at = _v3(item.get("at"))
        if at is not None and len(self.labels) < 12:
            self.labels.append(_Label(text, "tip", ("centre", at), _pen_color(_rgb(item.get("color"), SOLID_COLORS["ink"]))))
        elif len(self.captions) < 2:
            self.captions.append(text)

    def _i_axes3d(self, item: dict) -> None:
        lo, hi = self._bounds()
        reach = self.range or max(1.0, float(np.max(np.abs(np.vstack([lo, hi])))) * 1.15)
        names = [str(n)[:10] for n in (item.get("labels") or ["x", "y", "z"])] + ["", "", ""]
        grey = QColor(120, 120, 132)
        for k in range(3):
            end = np.zeros(3)
            end[k] = reach
            start = np.zeros(3)
            if lo[k] < -0.15 * reach:
                start[k] = -reach
            self.lines.append(_Line(np.linspace(start, end, 14), grey, 1.6, head=True))
            if names[k]:
                self.labels.append(_Label(names[k], "tip", ("centre", end), grey))
                self.labels[-1].toward = start

    # --- Size and shape ---------------------------------------------------------------------
    @staticmethod
    def _coords(item: dict) -> list:
        out = []
        for key in ("at", "from", "to"):
            p = _v3(item.get(key))
            if p is not None:
                out.append(p)
        return out

    def _bounds(self):
        pts = [s.rest for s in self.solids if s.link is None]
        pts += [b.centre[None] + b.r for b in self.balls] + [b.centre[None] - b.r for b in self.balls]
        pts += [ln.pts for ln in self.lines]
        if not pts:
            return np.full(3, -1.0), np.full(3, 1.0)
        allp = np.vstack(pts)
        return allp.min(0), allp.max(0)

    def _size(self, extra=()) -> float:
        lo, hi = self._bounds() if (self.solids or self.balls or self.lines) else (None, None)
        pts = list(extra)
        if lo is not None:
            pts += [lo, hi]
        if not pts:
            return 1.0
        allp = np.vstack(pts)
        return max(1e-3, float(np.linalg.norm(allp.max(0) - allp.min(0)) / 2))

    def _finish(self) -> None:
        """Every face of every part in one set of arrays, so a frame is a few numpy calls."""
        lo, hi = self._bounds()
        self.target = (lo + hi) / 2
        self.radius = max(1e-3, float(np.linalg.norm(hi - lo) / 2))
        self.dist = self.radius * 3.4
        offset, quads, rgb, spec, shine, alpha, closed, edges, glow, smooth = 0, [], [], [], [], [], [], [], [], []
        lift, vrgb, vspec, vshine, vlift, vglow = [], [], [], [], [], []
        for s in self.solids:
            s.offset = offset
            quads.append(s.quads + offset)
            n = len(s.quads)
            if s.face_rgb is not None:
                face = np.asarray(s.face_rgb, float)
            else:
                face = np.repeat(s.color[None], n, 0)
                if s.sections:
                    face[-s.sections:] = face[-s.sections:] * 0.55 + 0.45
            rgb.append(face)
            spec.append(np.full(n, s.spec))
            shine.append(np.full(n, s.shine))
            alpha.append(np.full(n, s.alpha))
            closed.append(np.full(n, s.closed))
            e = np.full(n, s.edges)
            if s.sections:
                e[-s.sections:] = True
            edges.append(e)
            glow.append(np.full(n, s.glow))
            smooth.append(s.smooth)
            # Gradient fills cost more, so only small curved parts get them (a big
            # surface has enough faces to look smooth with one colour each).
            vspec.append(np.full(len(s.rest), s.spec))
            vshine.append(np.full(len(s.rest), s.shine))
            vlift.append(np.full(len(s.rest), s.lift))
            vglow.append(np.full(len(s.rest), s.glow))
            lift.append(np.full(n, s.lift))
            vrgb.append(np.asarray(s.vertex_rgb, float) if s.vertex_rgb is not None
                        else np.repeat(s.color[None], len(s.rest), 0))
            offset += len(s.rest)
        if self.solids:
            self._q = np.vstack(quads)
            self._rgb = np.vstack(rgb)
            self._spec, self._shine, self._alpha = np.concatenate(spec), np.concatenate(shine), np.concatenate(alpha)
            self._closed, self._edges, self._glow = np.concatenate(closed), np.concatenate(edges), np.concatenate(glow)
            self._smooth = np.concatenate(smooth)
            self._lift = np.concatenate(lift)
            self._vrgb = np.vstack(vrgb)
            self._vspec, self._vshine, self._vlift = np.concatenate(vspec), np.concatenate(vshine), np.concatenate(vlift)
            self._vglow = np.concatenate(vglow)
            self._v0 = np.vstack([s.rest for s in self.solids])
        else:
            self._q = np.zeros((0, 4), int)
            self._v0 = np.zeros((0, 3))
            self._smooth = np.zeros(0, bool)
            self._lift = np.zeros(0)
            self._vrgb = np.zeros((0, 3))
        self._tri = self._q[:, 2] == self._q[:, 3]
        self._strips, self._in_strip = self._find_strips()
        # Flat ends (caps), grouped by corner count so each group is one numpy pass.
        groups: dict[int, list] = {}
        for k, s in enumerate(self.solids):
            for e in s.ends:
                groups.setdefault(len(e), []).append((e + s.offset, k))
        self._ends = [(np.array([e for e, _k in g]), np.array([k for _e, k in g])) for g in groups.values()]
        self._movers = sorted((i for i, s in enumerate(self.solids) if s.motion.moves and s.link is None),
                              key=lambda i: bool(self.solids[i].motion.follow))
        self._links = [i for i, s in enumerate(self.solids) if s.link is not None]
        # A soft shadow grounds a machine or a solid; not a molecule, a field or a graph.
        self._shadow = self._grounded and not any(ln.head for ln in self.lines)
        self._canvas = None

    def _find_strips(self):
        """Runs of curved faces side by side (a cylinder's sides, a pipe's wall, a
        ring's tube). Each frame the visible part of a run is filled as one polygon
        with a gradient through all its columns: rounder than separate faces, and
        one fill instead of a dozen."""
        strips, mask, first = [], np.zeros(len(self._q), bool), 0
        for k, s in enumerate(self.solids):
            q, n = s.quads, len(s.quads)
            if n >= 3 and s.smooth.any() and s.vertex_rgb is None:
                links = ((q[:-1, 1] == q[1:, 0]) & (q[:-1, 2] == q[1:, 3]),     # columns 0-3 then 1-2
                         (q[:-1, 3] == q[1:, 0]) & (q[:-1, 2] == q[1:, 1]))     # columns 0-1 then 3-2
                f = 0
                while f < n - 1:
                    layout = 0 if links[0][f] else 1 if links[1][f] else None
                    if layout is None or not s.smooth[f]:
                        f += 1
                        continue
                    e = f
                    while e < n - 1 and links[layout][e] and s.smooth[e + 1]:
                        e += 1
                    a, b = q[e], q[f]
                    ring = bool(a[1] == b[0] and a[2] == b[3]) if layout == 0 else bool(a[3] == b[0] and a[2] == b[1])
                    if e - f >= 1:
                        faces = np.arange(f, e + 1) + first
                        g = self._q[faces]
                        # Each face's left and right columns (bottom and top corners).
                        cols = (g[:, 0], g[:, 1], g[:, 3], g[:, 2]) if layout == 0 else (g[:, 0], g[:, 3], g[:, 1], g[:, 2])
                        strips.append((faces, ring, k, int(s.alpha * 255)) + cols)
                        mask[first + f:first + e + 1] = True
                    f = e + 1
            first += n
        return strips, mask

    # --- A frame -----------------------------------------------------------------------------
    def _frame(self, t: float):
        """Where everything is at time ``t``: the vertices, the balls, each solid's anchor."""
        v = self._v0.copy()
        anchors = np.array([s.anchor for s in self.solids]) if self.solids else np.zeros((0, 3))
        moved: dict[str, np.ndarray] = {}
        centres: dict[str, np.ndarray] = {s.id: s.anchor for s in self.solids if s.id}
        for i in self._movers:
            s = self.solids[i]
            pts = s.motion.apply(np.vstack([s.rest, s.anchor[None]]), t, moved)
            v[s.offset:s.offset + len(s.rest)] = pts[:-1]
            anchors[i] = pts[-1]
            if s.id:
                moved[s.id] = pts[-1] - s.anchor
                centres[s.id] = pts[-1]
        balls = np.array([b.at(t, moved) for b in self.balls]) if self.balls else np.zeros((0, 3))
        for b, c in zip(self.balls, balls):
            if b.id:
                centres[b.id] = c
        for i in self._links:
            s = self.solids[i]
            ends = []
            for e in s.link[:2]:
                ends.append(centres.get(e) if isinstance(e, str) else _v3(e))
            if ends[0] is None or ends[1] is None or np.linalg.norm(ends[1] - ends[0]) < 1e-9:
                a = self.target
                b = self.target + 1e-6
            else:
                a, b = ends
            length = float(np.linalg.norm(b - a))
            r = s.link[2]
            v[s.offset:s.offset + len(s.unit)] = (s.unit * [r, r, length]) @ _turn_to(_unit(b - a)).T + a
            anchors[i] = (a + b) / 2
        return v, balls, anchors

    def _camera(self, turn: float):
        az, el = math.radians(self.az0 + turn), math.radians(self.el)
        e = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
        fwd = -e
        right = _unit(np.cross(fwd, [0.0, 0, 1]), (1, 0, 0))
        up = np.cross(right, fwd)
        return self.target + e * self.dist, np.stack([right, up, fwd])

    def _ref_point(self, ref: tuple, balls, anchors) -> np.ndarray:
        kind, what = ref
        if kind == "solid":
            return anchors[what]
        if kind == "ball":
            return balls[what]
        return what

    # --- Fitting it on the card ---------------------------------------------------------------
    def fit(self, canvas: QRectF) -> None:
        """Choose the scale and place so the model fills the space beside its labels
        at every turn, and which side each label goes."""
        self._canvas = QRectF(canvas)
        us, vs = [], []
        times = (0.0, 0.7, 1.4, 2.1, 2.9, 3.6, 4.3, 5.1)
        for turn in (-self.TURN, -self.TURN / 2, 0.0, self.TURN / 2, self.TURN):
            eye, basis = self._camera(turn)
            for t in times if (self._movers or self._links or any(b.motion.moves or b.path is not None
                                                                  for b in self.balls)) else (0.0,):
                v, balls, _anchors = self._frame(t)
                cam = (v - eye) @ basis.T
                if len(cam):
                    us.append(cam[:, 0] / cam[:, 2])
                    vs.append(cam[:, 1] / cam[:, 2])
                if len(balls):
                    bc = (balls - eye) @ basis.T
                    rr = np.array([b.r for b in self.balls]) / bc[:, 2]
                    us += [bc[:, 0] / bc[:, 2] - rr, bc[:, 0] / bc[:, 2] + rr]
                    vs += [bc[:, 1] / bc[:, 2] - rr, bc[:, 1] / bc[:, 2] + rr]
            for ln in self.lines:
                cam = (ln.pts - eye) @ basis.T
                us.append(cam[:, 0] / cam[:, 2])
                vs.append(cam[:, 1] / cam[:, 2])
        u, w = np.concatenate(us), np.concatenate(vs)
        u0, u1, v0, v1 = float(u.min()), float(u.max()), float(w.min()), float(w.max())
        # Sides: each side label goes on the side its part is on, balanced.
        eye, basis = self._camera(0.0)
        _v, balls, anchors = self._frame(0.0)
        side = [lb for lb in self.labels if lb.kind == "side"]
        mid = (u0 + u1) / 2
        xs = []
        for lb in side:
            p = (self._ref_point(lb.ref, balls, anchors) - eye) @ basis.T
            xs.append(p[0] / p[2])
            lb.side = -1 if xs[-1] < mid else 1
        for _ in range(len(side)):
            lefts = [k for k, lb in enumerate(side) if lb.side < 0]
            rights = [k for k, lb in enumerate(side) if lb.side > 0]
            if len(lefts) > len(rights) + 1:
                k = max(lefts, key=lambda k: xs[k])
                side[k].side = 1
            elif len(rights) > len(lefts) + 1:
                k = min(rights, key=lambda k: xs[k])
                side[k].side = -1
            else:
                break
        fm = QFontMetricsF(theme.hand_font(self.FONT))
        self._widths = {id(lb): fm.horizontalAdvance(lb.text) for lb in self.labels}
        left = max([self._widths[id(lb)] for lb in side if lb.side < 0], default=0.0)
        right = max([self._widths[id(lb)] for lb in side if lb.side > 0], default=0.0)
        cap = self.ROW * len(self.captions) + (6 if self.captions else 0)
        gap = 16.0
        area = QRectF(canvas.left() + (left + gap if left else 4), canvas.top() + 8,
                      canvas.width() - (left + gap if left else 4) - (right + gap if right else 4),
                      canvas.height() - cap - 12)
        if any(lb.kind == "tip" for lb in self.labels):
            area.adjust(10, 12, -10, -6)          # room for names at the tips
        self._left_x = canvas.left() + left
        self._right_x = canvas.right() - right
        self._area = area
        scale = min(area.width() / max(u1 - u0, 1e-6), area.height() / max(v1 - v0, 1e-6))
        self._scale = scale
        self._ox = area.center().x() - scale * (u0 + u1) / 2
        self._oy = area.center().y() + scale * (v0 + v1) / 2
        # Each column keeps its order (top to bottom), so labels never swap places.
        for s in (-1, 1):
            col = [lb for lb in side if lb.side == s]
            ys = {}
            for lb in col:
                p = (self._ref_point(lb.ref, balls, anchors) - eye) @ basis.T
                ys[id(lb)] = -p[1] / p[2]
            col.sort(key=lambda lb: ys[id(lb)])
            setattr(self, "_col_left" if s < 0 else "_col_right", col)
        for lb in self.labels:
            lb.spot = None

    # --- Drawing ------------------------------------------------------------------------------
    def label_count(self) -> int:
        return len(self.labels) + len(self.captions)

    def reveal_end(self) -> float:
        return 0.75 + 0.32 * self.label_count() + 0.5

    def paint(self, p: QPainter, canvas: QRectF, *, turn: float = 0.0, t: float = 0.0, reveal: float = 99.0) -> None:
        if self._canvas is None or self._canvas != canvas:
            self.fit(canvas)
        show = max(0.0, min(1.0, (reveal + 0.05) / 0.55))
        if show <= 0:
            return
        eye, basis = self._camera(turn)
        v, balls, anchors = self._frame(t)
        scale, ox, oy = self._scale, self._ox, self._oy

        def project(pts):
            cam = (pts - eye) @ basis.T
            z = np.maximum(cam[:, 2], 1e-6)
            return np.column_stack([ox + scale * cam[:, 0] / z, oy - scale * cam[:, 1] / z]), z

        p.save()
        ease = 1 - (1 - show) ** 3
        c = self._area.center()
        p.translate(c)
        p.scale(0.93 + 0.07 * ease, 0.93 + 0.07 * ease)
        p.translate(-c)
        p.setOpacity(p.opacity() * ease)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._shadow:
            self._paint_shadow(p, project)
        prims = self._primitives(v, balls, eye, basis, project)
        self._draw(p, prims, eye, basis)
        p.restore()
        self._paint_labels(p, canvas, project, balls, anchors, reveal)

    def _paint_shadow(self, p: QPainter, project) -> None:
        lo, hi = self._bounds()
        ring = _circle(1.0, 28)
        rx, ry = (hi[0] - lo[0]) * 0.62, (hi[1] - lo[1]) * 0.62
        pts = np.column_stack([self.target[0] + ring[:, 0] * rx, self.target[1] + ring[:, 1] * ry,
                               np.full(len(ring), lo[2])])
        scr, _z = project(pts)
        centre, _z = project(np.array([[self.target[0], self.target[1], lo[2]]]))
        reach = float(np.max(np.linalg.norm(scr - centre[0], axis=1))) or 1.0
        grad = QRadialGradient(QPointF(*centre[0]), reach)
        grad.setColorAt(0.0, QColor(60, 50, 40, 46))
        grad.setColorAt(0.7, QColor(60, 50, 40, 14))
        grad.setColorAt(1.0, QColor(60, 50, 40, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in scr]))

    def _primitives(self, v, balls, eye, basis, project):
        right, up, fwd = basis
        light = _unit(-0.45 * right + 0.78 * up - 0.42 * fwd)
        out = {"faces": None, "balls": None, "lines": [], "runs": []}
        depth_all, kind_all, index_all = [], [], []
        if len(self._q):
            q = self._q
            a, b, c, d = v[q[:, 0]], v[q[:, 1]], v[q[:, 2]], v[q[:, 3]]
            n_raw = np.cross(c - a, d - b)
            length = np.linalg.norm(n_raw, axis=1)
            centre = (a + b + c + d) / 4
            view = eye - centre
            facing = np.einsum("ij,ij->i", n_raw, view) > 0
            visible = (length > 1e-12) & (facing | ~self._closed)
            keep = visible & ~self._in_strip
            idx = np.nonzero(keep)[0]
            face_depth = (centre - eye) @ fwd
            n = n_raw[idx] / length[idx, None]
            n = np.where(facing[idx, None], n, -n)
            rgb = _shade(n, self._rgb[idx], self._spec[idx], self._shine[idx], self._glow[idx], light, -fwd,
                         self._lift[idx])
            scr, z = project(v)
            poly = scr[q[idx]]                                  # (k, 4, 2)
            alpha = (self._alpha[idx] * 255).astype(int)
            colors = np.column_stack([(rgb * 255).astype(int), alpha])
            # Curved parts are shaded by their vertex normals (smooth); runs of them
            # side by side become strips (see _find_strips), and the rest (a big
            # surface) take the average of their corners.
            vn = None
            if self._smooth.any():
                vn = np.zeros_like(v)
                use = self._smooth
                for k in range(4):
                    np.add.at(vn, q[use, k], n_raw[use])
                vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12
                sel = np.nonzero(self._smooth[idx] & ~self._glow[idx])[0]
                if len(sel):
                    corners = vn[q[idx[sel]]] * np.where(facing[idx[sel]], 1.0, -1.0)[:, None, None]
                    m = len(sel)
                    cc = _shade(corners.reshape(-1, 3), self._vrgb[q[idx[sel]]].reshape(-1, 3),
                                np.repeat(self._spec[idx[sel]], 4), np.repeat(self._shine[idx[sel]], 4),
                                np.zeros(4 * m, bool), light, -fwd, np.repeat(self._lift[idx[sel]], 4))
                    colors[sel, :3] = (np.clip(cc.reshape(m, 4, 3).mean(1), 0, 1) * 255).astype(int)
            poly = _grow(poly, self._tri[idx], self._alpha[idx] >= 1.0)
            depth = ((centre[idx] - eye) @ fwd)
            out["faces"] = (poly.tolist(), colors.tolist(), self._tri[idx].tolist(), self._edges[idx].tolist())
            depth_all.append(depth)
            kind_all.append(np.zeros(len(idx), int))
            index_all.append(np.arange(len(idx)))
            runs = self._strip_runs(scr, vn, visible, facing, face_depth, light, -fwd)
            out["runs"] = runs
            if runs:
                depth_all.append(np.array([r[0] for r in runs]))
                kind_all.append(np.full(len(runs), 4))
                index_all.append(np.arange(len(runs)))
        ends = []
        scr_all = None
        side_depth: dict[int, float] = {}
        for r in out["runs"]:
            side_depth[r[6]] = min(side_depth.get(r[6], math.inf), r[0])
        for group, owner in self._ends:
            pts = v[group]                                          # (g, m, 3)
            normal = 0.5 * np.cross(pts, np.roll(pts, -1, axis=1)).sum(1)
            length = np.linalg.norm(normal, axis=1)
            centre = pts.mean(1)
            facing = np.einsum("ij,ij->i", normal, eye - centre) > 0
            closed = np.array([self.solids[k].closed for k in owner])
            keep = (length > 1e-12) & (facing | ~closed)
            if not keep.any():
                continue
            n = normal[keep] / length[keep, None]
            n = np.where(facing[keep, None], n, -n)
            solids = [self.solids[k] for k in owner[keep]]
            rgb = _shade(n, np.array([s.color for s in solids]), np.array([s.spec for s in solids]),
                         np.array([s.shine for s in solids]), np.array([s.glow for s in solids]), light, -fwd)
            if scr_all is None:
                scr_all, _z = project(v)
            for k, (g, s) in enumerate(zip(group[keep], solids)):
                ends.append((scr_all[g].tolist(), (*(int(c * 255) for c in rgb[k]), int(s.alpha * 255))))
            depth = (centre[keep] - eye) @ fwd
            for j, k in enumerate(owner[keep].tolist()):
                if k in side_depth:
                    depth[j] = min(depth[j], side_depth[k] - 1e-6)
            depth_all.append(depth)
            kind_all.append(np.full(int(keep.sum()), 3))
            index_all.append(np.arange(len(ends) - int(keep.sum()), len(ends)))
        out["ends"] = ends
        if len(balls):
            scr, z = project(balls)
            radii = np.array([b.r for b in self.balls]) * self._scale / z
            depth = (balls - eye) @ fwd
            out["balls"] = (scr.tolist(), radii.tolist())
            # A ball sorts by its nearest point, so things touching it from behind stay behind.
            depth_all.append(depth - np.array([b.r for b in self.balls]) * 0.35)
            kind_all.append(np.ones(len(balls), int))
            index_all.append(np.arange(len(balls)))
        chunks = []
        for k, ln in enumerate(self.lines):
            scr, _z = project(ln.pts)
            depth = (ln.pts - eye) @ fwd
            step = 8
            for s in range(0, len(ln.pts) - 1, step):
                e = min(len(ln.pts), s + step + 1)
                chunks.append((k, scr[s:e].tolist(), e == len(ln.pts)))
                depth_all.append(np.array([float(depth[s:e].mean()) - 0.03 * self.radius]))
                kind_all.append(np.array([2]))
                index_all.append(np.array([len(chunks) - 1]))
        out["lines"] = chunks
        if depth_all:
            depth = np.concatenate(depth_all)
            order = np.argsort(-depth, kind="stable")
            out["order"] = list(zip(np.concatenate(kind_all)[order].tolist(), np.concatenate(index_all)[order].tolist()))
        else:
            out["order"] = []
        out["light"] = (float(light @ right), float(light @ up))
        return out

    def _strip_runs(self, scr, vn, visible, facing, face_depth, light, view) -> list:
        """The visible stretches of each strip: (depth, polygon, gradient ends, stops, alpha, part)."""
        if not self._strips or vn is None:
            return []
        shade = {1: np.clip(_shade(vn, self._vrgb, self._vspec, self._vshine, self._vglow, light, view,
                                   self._vlift), 0, 1) * 255}
        runs = []
        for faces, ring, k, alpha, cb, cb2, ct, ct2 in self._strips:
            vis = visible[faces]
            if not vis.any():
                continue
            c = np.where(vis, np.where(facing[faces], 1, 2), 0).tolist()   # hidden, front, back (open parts)
            order = list(range(len(c)))
            if ring and c[0] and c[-1] == c[0] and any(x != c[0] for x in c):
                cut = max(i for i, x in enumerate(c) if x != c[0]) + 1        # start after the last break
                order = order[cut:] + order[:cut]
            groups, run = [], []
            for j in order:
                if c[j] and run and c[run[-1]] == c[j]:
                    run.append(j)
                    continue
                if run:
                    groups.append(run)
                run = [j] if c[j] else []
            if run:
                groups.append(run)
            for run in groups:
                side = c[run[0]]
                if side not in shade:
                    shade[side] = np.clip(_shade(-vn, self._vrgb, self._vspec, self._vshine, self._vglow, light,
                                                 view, self._vlift), 0, 1) * 255
                r = np.array(run)
                bottom = np.concatenate((cb[r], cb2[r[-1:]]))
                top = np.concatenate((ct[r], ct2[r[-1:]]))
                col = (shade[side][bottom] + shade[side][top]) / 2
                sb, st = scr[bottom], scr[top]
                mids = (sb + st) / 2
                axis = mids[-1] - mids[0]
                length = float(axis @ axis)
                if length > 0.25:
                    t = np.maximum.accumulate(np.clip((mids - mids[0]) @ axis / length, 0, 1))
                    stops = list(zip(t.tolist(), col.astype(int).tolist()))
                else:
                    stops = [(0.0, col.mean(0).astype(int).tolist())]
                runs.append((float(face_depth[faces[r]].mean()), sb.tolist() + st[::-1].tolist(),
                             mids[0].tolist(), mids[-1].tolist(), stops, alpha, k))
        return runs

    def _draw(self, p: QPainter, prims: dict, eye, basis) -> None:
        faces, balls, chunks = prims["faces"], prims["balls"], prims["lines"]
        lx, ly = prims["light"]
        no_pen = Qt.PenStyle.NoPen
        last_color, pen_on = None, None
        p.setPen(no_pen)
        for kind, i in prims["order"]:
            if kind == 0:
                poly, colors, tris, edges = faces
                col = colors[i]
                if col != last_color:
                    p.setBrush(QColor(col[0], col[1], col[2], col[3]))
                    last_color = col
                pts = poly[i]
                if edges[i]:
                    pen = QPen(QColor(int(col[0] * 0.72), int(col[1] * 0.72), int(col[2] * 0.72), max(col[3], 160)), 1.0)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    p.setPen(pen)
                    pen_on = True
                elif pen_on:
                    p.setPen(no_pen)
                    pen_on = False
                if tris[i]:
                    p.drawPolygon(QPolygonF([QPointF(*pts[0]), QPointF(*pts[1]), QPointF(*pts[2])]))
                else:
                    p.drawPolygon(QPolygonF([QPointF(*pts[0]), QPointF(*pts[1]), QPointF(*pts[2]), QPointF(*pts[3])]))
            elif kind == 1:
                self._draw_ball(p, self.balls[i], balls[0][i], balls[1][i], lx, ly)
                last_color, pen_on = None, None
                p.setPen(no_pen)
            elif kind == 4:
                _d, pts, pa, pb, stops, alpha, _k = prims["runs"][i]
                if len(stops) > 1:
                    grad = QLinearGradient(QPointF(*pa), QPointF(*pb))
                    for t, c in stops:
                        grad.setColorAt(t, QColor(c[0], c[1], c[2], alpha))
                    brush = QBrush(grad)
                else:
                    c = stops[0][1]
                    brush = QBrush(QColor(c[0], c[1], c[2], alpha))
                p.setBrush(brush)
                if pen_on:
                    p.setPen(no_pen)
                    pen_on = False
                p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
                last_color = None
            elif kind == 3:
                pts, col = prims["ends"][i]
                if col != last_color:
                    p.setBrush(QColor(*col))
                    last_color = col
                if pen_on:
                    p.setPen(no_pen)
                    pen_on = False
                p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
            else:
                k, pts, last = chunks[i]
                ln = self.lines[k]
                pen = QPen(ln.color, ln.width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                if ln.dashed:
                    pen.setDashPattern([3.0, 3.0])
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))
                if ln.head and last and len(pts) >= 2:
                    (x0, y0), (x1, y1) = pts[-2], pts[-1]
                    ang = math.atan2(y1 - y0, x1 - x0)
                    size = 4 + ln.width * 2.2
                    head = [QPointF(x1 + 2 * math.cos(ang), y1 + 2 * math.sin(ang)),
                            QPointF(x1 - size * math.cos(ang - 0.42), y1 - size * math.sin(ang - 0.42)),
                            QPointF(x1 - size * math.cos(ang + 0.42), y1 - size * math.sin(ang + 0.42))]
                    p.setPen(no_pen)
                    p.setBrush(ln.color)
                    p.drawPolygon(QPolygonF(head))
                p.setPen(no_pen)
                last_color, pen_on = None, None

    def _draw_ball(self, p: QPainter, ball: _Ball, at, radius: float, lx: float, ly: float) -> None:
        if radius < 0.6:
            return
        c = QPointF(at[0], at[1])
        r, g, b = (float(x) for x in ball.color)
        alpha = int(ball.alpha * 255)
        if ball.glow:
            grad = QRadialGradient(c, radius)
            grad.setColorAt(0.0, QColor.fromRgbF(min(1, r + 0.3), min(1, g + 0.3), min(1, b + 0.3)))
            grad.setColorAt(0.8, QColor.fromRgbF(r, g, b))
            grad.setColorAt(1.0, QColor.fromRgbF(r * 0.9, g * 0.9, b * 0.9))
            p.setPen(Qt.PenStyle.NoPen)
        else:
            focus = QPointF(c.x() + lx * radius * 0.42, c.y() - ly * radius * 0.42)
            grad = QRadialGradient(c, radius, focus)
            spec = 0.25 + ball.spec
            grad.setColorAt(0.0, QColor.fromRgbF(min(1, r + spec * (1 - r)), min(1, g + spec * (1 - g)),
                                                 min(1, b + spec * (1 - b)), ball.alpha))
            grad.setColorAt(0.45, QColor.fromRgbF(r * 0.96, g * 0.96, b * 0.96, ball.alpha))
            grad.setColorAt(1.0, QColor.fromRgbF(r * 0.52, g * 0.52, b * 0.52, ball.alpha))
            p.setPen(QPen(QColor(int(r * 120), int(g * 120), int(b * 120), min(alpha, 120)), 0.8))
        p.setBrush(grad)
        p.drawEllipse(c, radius, radius)
        if ball.symbol and radius >= 8:
            lum = 0.3 * r + 0.59 * g + 0.11 * b
            p.setFont(theme.ui_font(max(7.0, min(13.0, radius * 0.62)), QFont.Weight.DemiBold))
            p.setPen(QColor(40, 40, 48) if lum > 0.62 else QColor(255, 255, 255))
            p.drawText(QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius), Qt.AlignmentFlag.AlignCenter,
                       ball.symbol)
            p.setPen(Qt.PenStyle.NoPen)

    # --- Labels ------------------------------------------------------------------------------
    def _paint_labels(self, p: QPainter, canvas: QRectF, project, balls, anchors, reveal: float) -> None:
        if not self.labels and not self.captions:
            return
        font = theme.hand_font(self.FONT)
        p.setFont(font)
        row = self.ROW
        taken: list[QRectF] = []
        order = {id(lb): k for k, lb in enumerate(self.labels)}
        anchor_px = {}
        for lb in self.labels:
            scr, _z = project(self._ref_point(lb.ref, balls, anchors)[None])
            anchor_px[id(lb)] = QPointF(float(scr[0][0]), float(scr[0][1]))
        top, bottom = self._area.top() + 2, self._area.bottom() + 4
        for col, s in ((getattr(self, "_col_left", []), -1), (getattr(self, "_col_right", []), 1)):
            if not col:
                continue
            ys = [anchor_px[id(lb)].y() for lb in col]
            ys = _spread(ys, row, top + row / 2, bottom - row / 2)
            for lb, y in zip(col, ys):
                w = self._widths[id(lb)]
                x = self._left_x - w if s < 0 else self._right_x
                lb.rect = QRectF(x, y - row / 2, w, row)
                taken.append(lb.rect)
        for k, cap in enumerate(self.captions):
            taken.append(QRectF(canvas.left(), canvas.bottom() - row * (len(self.captions) - k), canvas.width(), row))
        bounds = canvas.adjusted(-2, -6, 2, 2)
        for lb in self.labels:
            if lb.kind != "tip":
                continue
            a = anchor_px[id(lb)]
            w, h = self._widths[id(lb)], row
            spots = [(8, -h - 2), (8, 2), (-w - 8, -h - 2), (-w - 8, 2), (-w / 2, -h - 8), (-w / 2, 8),
                     (10, -h / 2), (-w - 10, -h / 2)]
            spots += [(dx * 2.2 if dx > 0 else dx - 12, dy * 2.0) for dx, dy in spots]
            if lb.toward is not None:   # an arrow's name goes just beyond its tip
                tail, _z = project(np.asarray(lb.toward, float)[None])
                dx, dy = a.x() - float(tail[0][0]), a.y() - float(tail[0][1])
                norm = math.hypot(dx, dy)
                if norm > 1e-6:
                    dx, dy = dx / norm, dy / norm
                    reach = abs(dx) * w / 2 + abs(dy) * h / 2 + 6
                    spots.insert(0, (dx * reach - w / 2, dy * reach - h / 2))
                else:
                    spots.insert(0, spots[0])
            choice = None
            tries = ([lb.spot] if lb.spot is not None else []) + list(range(len(spots)))
            for k in tries:
                dx, dy = spots[k]
                r = QRectF(a.x() + dx, a.y() + dy, w, h)
                if bounds.contains(r) and not any(r.adjusted(-2, -1, 2, 1).intersects(o) for o in taken):
                    choice = k
                    break
            if choice is None:
                lb.rect = QRectF()
                continue
            lb.spot = choice
            dx, dy = spots[choice]
            lb.rect = QRectF(a.x() + dx, a.y() + dy, w, h)
            taken.append(lb.rect)
        # Draw them in the order they were written, one after another.
        for lb in self.labels:
            if lb.rect.isEmpty():
                continue
            start = 0.75 + 0.32 * order[id(lb)]
            u = reveal - start
            if u <= 0:
                continue
            a = anchor_px[id(lb)]
            if lb.kind == "side":
                grow = min(1.0, u / 0.22)
                y = lb.rect.center().y()
                edge = QPointF(lb.rect.right() + 4, y) if lb.side < 0 else QPointF(lb.rect.left() - 4, y)
                elbow = QPointF(edge.x() + (8 if lb.side < 0 else -8), y)
                pen = QPen(QColor(lb.color.red(), lb.color.green(), lb.color.blue(), 150), 1.2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                end = QPointF(elbow.x() + (a.x() - elbow.x()) * grow, elbow.y() + (a.y() - elbow.y()) * grow)
                p.drawPolyline(QPolygonF([edge, elbow, end]))
                if grow >= 1:
                    p.setPen(QPen(QColor(255, 255, 255, 230), 1.3))
                    p.setBrush(lb.color)
                    p.drawEllipse(a, 2.7, 2.7)
            text_u = max(0.0, min(1.0, (u - 0.12) / 0.3))
            if text_u <= 0:
                continue
            p.save()
            p.setClipRect(QRectF(lb.rect.left() - 2, lb.rect.top() - 6, (lb.rect.width() + 4) * text_u,
                                 lb.rect.height() + 12))
            at = QPointF(lb.rect.left(), lb.rect.bottom() - 5)
            if lb.kind == "tip":   # a paper-coloured halo keeps it readable where it sits on the model
                p.setPen(_PAPER)
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    p.drawText(at + QPointF(dx, dy), lb.text)
            p.setPen(lb.color)
            p.drawText(at, lb.text)
            p.restore()
        for k, cap in enumerate(self.captions):
            u = reveal - (0.75 + 0.32 * (len(self.labels) + k))
            if u <= 0:
                continue
            y = canvas.bottom() - row * (len(self.captions) - k - 1) - 5
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(cap)
            p.save()
            p.setClipRect(QRectF(canvas.left(), y - row, (w + 6) * min(1.0, u / 0.4), row + 8))
            p.setPen(_INK)
            p.drawText(QPointF(canvas.left() + 2, y), cap)
            p.restore()


def _pen(name, default: str) -> QColor:
    pens = {"ink": QColor(42, 42, 51), "blue": QColor(47, 111, 224), "red": QColor(226, 76, 56),
            "green": QColor(40, 150, 90), "orange": QColor(232, 132, 36), "purple": QColor(122, 90, 245),
            "grey": QColor(138, 138, 150), "gray": QColor(138, 138, 150), "teal": QColor(30, 150, 155)}
    key = str(name or default).lower()
    if key.startswith("#") and QColor(key).isValid():
        return QColor(key)
    return QColor(pens.get(key, pens[default]))


def _shade(n, rgb, spec, shine, glow, light, view, lift=0.0) -> np.ndarray:
    """Face colours: soft sky light, one key light from the upper left, a
    highlight, and edges turned away a touch darker. ``view`` is one direction
    for all faces, so faces that share a normal share a colour exactly."""
    diffuse = np.clip(n @ light, 0, 1)
    sky = 0.5 + 0.5 * n[:, 2]
    half = _unit(light + view)
    highlight = np.clip(n @ half, 0, 1) ** shine * spec
    rim = np.clip(n @ view, 0, 1)
    shade = (0.35 + 0.15 * sky + 0.56 * diffuse) * (0.84 + 0.16 * rim)
    shade = lift + (1 - lift) * shade
    out = rgb * shade[:, None] + highlight[:, None]
    out[glow] = rgb[glow]
    return np.clip(out, 0, 1)


def _grow(poly: np.ndarray, tri: np.ndarray, opaque: np.ndarray, by: float = 0.5) -> np.ndarray:
    """Push every edge of each opaque face out by ``by`` pixels (mitred, capped),
    so neighbouring faces overlap a little and antialiasing leaves no seams."""
    out = poly.copy()
    for mask, m in ((tri & opaque, 3), (~tri & opaque, 4)):
        if not mask.any():
            continue
        pts = poly[mask][:, :m]                                  # (k, m, 2)
        centre = pts.mean(1, keepdims=True)
        edge = np.roll(pts, -1, axis=1) - pts                    # edge i: point i -> i+1
        normal = np.stack([edge[..., 1], -edge[..., 0]], -1)
        normal /= np.linalg.norm(normal, axis=2, keepdims=True) + 1e-9
        mid = pts + edge / 2
        flip = np.einsum("kmi,kmi->km", normal, mid - centre) < 0
        normal[flip] *= -1
        prev = np.roll(normal, 1, axis=1)                        # the edge arriving at point i
        miter = prev + normal
        cos = 1 + np.einsum("kmi,kmi->km", prev, normal)
        miter = miter / np.maximum(cos, 0.32)[..., None]         # capped: sharp corners grow at most ~2.5x
        grown = pts + miter * by
        block = poly[mask].copy()
        block[:, :m] = grown
        if m == 3:
            block[:, 3] = grown[:, 2]
        out[mask] = block
    return out


def _spread(ys: list[float], gap: float, top: float, bottom: float) -> list[float]:
    """Positions as near ``ys`` as can be, in the same order, at least ``gap``
    apart and inside top..bottom (so labels in a column never touch)."""
    out = list(ys)
    for k in range(len(out)):
        out[k] = max(out[k], top if k == 0 else out[k - 1] + gap)
    if out and out[-1] > bottom:
        out[-1] = bottom
        for k in range(len(out) - 2, -1, -1):
            out[k] = min(out[k], out[k + 1] - gap)
    if out and out[0] < top:
        out[0] = top
        for k in range(1, len(out)):
            out[k] = max(out[k], out[k - 1] + gap)
    return out
