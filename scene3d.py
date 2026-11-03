"""On-screen 3D: a model of the thing being explained, floating over your
screen. It turns slowly, its moving parts move (a fan spins), and numbered
chips name each part ("1 · Fan", "2 · Compressor") one at a time while the
voice explains.

The model is drawn natively: shaded triangles with numpy and QPainter, no
web engine. The AI describes the object as parts from the library below
(boxes, cylinders, cones, spheres, rings, fan blades, gears, pipes,
cutaway shells), each with a place, a turn, a material and maybe a spin.
It never writes code.

Threading: the scene is described on a worker thread (the sketch call); the
overlay is drawn on the Qt thread.
"""
from __future__ import annotations

import math
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QPolygonF, QRadialGradient
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
