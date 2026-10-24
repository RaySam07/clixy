"""Cozy look for Clicky Windows: pixel avatars, color themes, fonts, styles.

Five pixel-art avatars, each with its own color theme. The theme colors the
screen-edge glow, the side island, the settings window and the tray menu, so
picking an avatar re-skins the whole app.

Every avatar is drawn on a grid of square cells with its pointing tip at cell
(0, 0): the tip's top-left corner is the pixel the model asked for, so the
avatar lands exactly where the old arrow cursor did. Sprites step at 10 fps
like real pixel art, and each frame is cached.

Nothing here keeps app state except which avatar is current; callers pass the
time and mood, the helpers draw.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)


def _c(r: int, g: int, b: int, a: int = 255) -> QColor:
    return QColor(r, g, b, a)


# --- Palettes ------------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    """One cozy color theme. ``glow`` stops run around the screen edge."""

    name: str
    glow: tuple[tuple[float, QColor], ...]
    accent: QColor           # annotations, highlights, primary button
    accent_text: QColor      # text on an accent-colored button
    chip: QColor             # text color of small pixel-font chips
    surface_top: QColor      # island / dialog background, top of gradient
    surface_bottom: QColor
    raised: QColor           # input fields, cards
    line: QColor             # borders
    text: QColor
    muted: QColor
    bars: tuple[QColor, QColor]
    dots: tuple[QColor, QColor, QColor]
    halo: QColor             # soft light around the avatar


CAMPFIRE = Palette(
    name="Campfire",
    glow=((0.00, _c(255, 70, 30)), (0.18, _c(255, 140, 30)), (0.33, _c(255, 214, 90)),
          (0.50, _c(255, 110, 40)), (0.68, _c(230, 40, 50)), (0.84, _c(255, 160, 50)),
          (1.00, _c(255, 70, 30))),
    accent=_c(255, 122, 36), accent_text=_c(255, 255, 255), chip=_c(255, 178, 122),
    surface_top=_c(32, 20, 17), surface_bottom=_c(21, 13, 11),
    raised=_c(46, 30, 25), line=_c(92, 54, 40),
    text=_c(255, 238, 220), muted=_c(190, 150, 128),
    bars=(_c(255, 90, 30), _c(255, 210, 80)),
    dots=(_c(255, 104, 30), _c(255, 166, 30), _c(255, 218, 80)),
    halo=_c(255, 120, 30),
)

LAGOON = Palette(
    name="Lagoon",
    glow=((0.00, _c(40, 110, 255)), (0.20, _c(40, 200, 255)), (0.36, _c(130, 255, 225)),
          (0.52, _c(60, 150, 255)), (0.70, _c(90, 90, 255)), (0.86, _c(60, 220, 240)),
          (1.00, _c(40, 110, 255))),
    accent=_c(62, 190, 255), accent_text=_c(8, 24, 40), chip=_c(140, 220, 255),
    surface_top=_c(14, 24, 38), surface_bottom=_c(9, 15, 26),
    raised=_c(22, 36, 56), line=_c(44, 74, 110),
    text=_c(228, 244, 255), muted=_c(130, 165, 195),
    bars=(_c(50, 130, 255), _c(120, 250, 220)),
    dots=(_c(60, 140, 255), _c(60, 200, 255), _c(130, 250, 220)),
    halo=_c(60, 170, 255),
)

MATCHA = Palette(
    name="Matcha",
    glow=((0.00, _c(60, 170, 90)), (0.20, _c(150, 220, 90)), (0.36, _c(240, 236, 160)),
          (0.52, _c(90, 200, 120)), (0.70, _c(40, 140, 110)), (0.86, _c(170, 230, 110)),
          (1.00, _c(60, 170, 90))),
    accent=_c(132, 210, 104), accent_text=_c(14, 34, 18), chip=_c(186, 232, 150),
    surface_top=_c(20, 30, 22), surface_bottom=_c(13, 20, 15),
    raised=_c(30, 44, 33), line=_c(58, 86, 60),
    text=_c(236, 246, 226), muted=_c(150, 178, 145),
    bars=(_c(70, 180, 90), _c(230, 236, 150)),
    dots=(_c(80, 180, 90), _c(150, 220, 100), _c(230, 236, 150)),
    halo=_c(110, 210, 110),
)

MIDNIGHT = Palette(
    name="Midnight",
    glow=((0.00, _c(90, 80, 230)), (0.20, _c(160, 100, 255)), (0.36, _c(255, 212, 100)),
          (0.52, _c(140, 90, 250)), (0.70, _c(70, 70, 200)), (0.86, _c(200, 150, 255)),
          (1.00, _c(90, 80, 230))),
    accent=_c(178, 140, 255), accent_text=_c(20, 14, 36), chip=_c(255, 214, 110),
    surface_top=_c(24, 19, 38), surface_bottom=_c(15, 12, 26),
    raised=_c(36, 29, 56), line=_c(70, 58, 108),
    text=_c(240, 234, 255), muted=_c(160, 148, 196),
    bars=(_c(140, 100, 255), _c(255, 212, 100)),
    dots=(_c(150, 110, 255), _c(200, 160, 255), _c(255, 212, 100)),
    halo=_c(150, 110, 255),
)

DREAMY = Palette(
    name="Dreamy",
    glow=((0.00, _c(170, 150, 255)), (0.20, _c(120, 165, 255)), (0.36, _c(120, 225, 255)),
          (0.52, _c(255, 226, 140)), (0.70, _c(160, 140, 255)), (0.86, _c(110, 200, 255)),
          (1.00, _c(170, 150, 255))),
    accent=_c(255, 210, 90), accent_text=_c(40, 30, 10), chip=_c(255, 222, 140),
    surface_top=_c(26, 23, 42), surface_bottom=_c(17, 15, 30),
    raised=_c(38, 34, 60), line=_c(76, 70, 116),
    text=_c(244, 240, 255), muted=_c(168, 160, 200),
    bars=(_c(150, 140, 255), _c(255, 222, 140)),
    dots=(_c(160, 150, 255), _c(120, 210, 255), _c(255, 222, 140)),
    halo=_c(255, 220, 120),
)


# --- Sprites -------------------------------------------------------------------

Cell = tuple[int, int]
NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))
MOODS = ("idle", "listening", "thinking", "happy")
FPS = 10
"""Sprite frames per second. Low on purpose: pixel art flickers in steps."""


@dataclass(frozen=True)
class Face:
    """Where the face sits and its colors. ``anchor`` is the cell between the
    eyes, one row above the mouth."""

    anchor: Cell
    eye: QColor
    blush: QColor | None = None
    cat: bool = False         # slit-pupil cat eyes and a little nose
    shine: QColor = field(default_factory=lambda: _c(255, 255, 255))
    tongue: QColor = field(default_factory=lambda: _c(255, 106, 90))


@dataclass(frozen=True)
class Avatar:
    id: str
    name: str
    tagline: str
    palette: Palette
    face: Face
    body: Callable[[int], tuple[dict[Cell, QColor], list[tuple[Cell, QColor]]]]
    """frame -> (body cells with outline, extra cells drawn over the face)."""
    extent: tuple[int, int, int, int] = field(default=(0, 0, 14, 19))
    """Cell bounds (min_x, min_y, max_x, max_y) that frame the avatar in icons."""


def _outline(cells: dict[Cell, float], color_for: Callable[[float], QColor], outline: QColor) -> dict[Cell, QColor]:
    """Color the cells, using the outline color on every cell next to empty space."""
    out: dict[Cell, QColor] = {}
    for (x, y), value in cells.items():
        edge = any((x + dx, y + dy) not in cells for dx, dy in NEIGHBOURS)
        out[(x, y)] = outline if edge else color_for(value)
    return out


def _drop_lonely(cells: dict[Cell, float]) -> None:
    """Remove stray cells the animation leaves hanging; keep the tip."""
    for _ in range(2):
        lonely = [c for c in cells if c != (0, 0)
                  and sum((c[0] + dx, c[1] + dy) in cells for dx, dy in NEIGHBOURS) <= 1]
        for c in lonely:
            del cells[c]


def _teardrop(cells: dict[Cell, float], tip: tuple[float, float], tilt_deg: float, length: float,
              widest: float, width: float, *, t: float = 0.0, flicker: float = 0.0,
              exponent: float = 0.7, lens: bool = False) -> None:
    """Rasterize a pointed shape whose tip is ``tip`` and whose axis leans
    ``tilt_deg`` from straight down. Stores a 0..1 'depth' per cell: 1 on the
    axis, 0 at the edge, scaled up toward the round end."""
    tilt = math.radians(tilt_deg)
    ax, ay = math.sin(tilt), math.cos(tilt)
    tx, ty = tip
    for gy in range(int(ty) - 2, int(ty + length) + 3):
        for gx in range(int(tx - length) - 2, int(tx + length) + 3):
            x, y = gx + 0.5 - tx, gy + 0.5 - ty
            u = x * ax + y * ay
            v = x * ay - y * ax
            if u < 0 or u > length:
                continue
            if lens:
                w = width * math.sin(math.pi * u / length) ** 0.75
            elif u <= widest:
                w = width * (u / widest) ** exponent
            else:
                k = (u - widest) / (length - widest)
                w = width * math.sqrt(max(0.0, 1.0 - k * k))
            sway = 0.0
            if flicker:
                s = (u / length) * (1.0 - u / length)
                w += flicker * s * 2.0 * (0.5 * math.sin(1.3 * u - 7.0 * t) + 0.28 * math.sin(2.4 * u + 5.0 * t))
                sway = flicker * 1.6 * math.sin(0.6 * u - 4.0 * t) * s * (1.0 - u / length)
            if w > 0 and abs(v - sway) <= w:
                depth = (1.0 - abs(v - sway) / w) * (0.3 + 0.7 * min(1.0, u / (widest + 2.5)))
                cells[(gx, gy)] = max(cells.get((gx, gy), -1.0), depth)


def _bands(*bands: tuple[float, QColor]) -> Callable[[float], QColor]:
    def color_for(value: float) -> QColor:
        for threshold, color in bands:
            if value > threshold:
                return color
        return bands[-1][1]
    return color_for


# Ember: a little campfire flame -------------------------------------------------

_FIRE = _bands((0.78, _c(255, 247, 210)), (0.58, _c(255, 218, 80)), (0.38, _c(255, 166, 30)),
               (0.18, _c(255, 108, 28)), (-9, _c(222, 48, 30)))
_EMBERS = (_c(255, 226, 110), _c(255, 160, 40), _c(255, 104, 30))


def _ember_tongue(t: float) -> tuple[tuple[float, float], float]:
    h = 4.6 + 1.2 * math.sin(6.0 * t) + 0.6 * math.sin(9.7 * t + 1)
    return (10.4 + 0.5 * math.sin(5 * t), 8.4 - h), h


def _ember_body(frame: int):
    t = frame / FPS
    cells: dict[Cell, float] = {}
    _teardrop(cells, (0, 0), 24, 18.0, 11.5, 6.3, t=t, flicker=1.0)
    (tx, ty), h = _ember_tongue(t)
    _teardrop(cells, (tx, ty), -8, h + 3.4, h + 0.7, 1.75, t=t, flicker=0.4)
    _drop_lonely(cells)
    cells[(0, 0)] = 0.0
    body = _outline(cells, _FIRE, _c(104, 20, 12))
    extras = []
    for k in range(2):
        progress = ((t + k * 0.8) % 1.6) / 1.6
        ex = round(tx + 1.2 * math.sin((t + k) * 3.0))
        ey = round(ty - 1 - progress * 7)
        color = QColor(_EMBERS[min(2, int(progress * 3))])
        color.setAlpha(int(255 * (1 - progress)))
        if (ex, ey) not in body:
            extras.append(((ex, ey), color))
    return body, extras


# Splash: a water-drop slime ------------------------------------------------------

_WATER = _bands((0.72, _c(170, 232, 255)), (0.45, _c(92, 196, 255)), (0.2, _c(56, 156, 245)),
                (-9, _c(40, 118, 228)))


def _splash_body(frame: int):
    t = frame / FPS
    cells: dict[Cell, float] = {}
    jiggle = 1.0 + 0.04 * math.sin(2 * math.pi * t / 1.6)
    _teardrop(cells, (0, 0), 24, 17.5, 11.0, 6.2 * jiggle, exponent=0.58)
    _drop_lonely(cells)
    cells[(0, 0)] = 0.0
    body = _outline(cells, _WATER, _c(18, 54, 112))
    # Glossy highlight on the upper-left shoulder.
    for cell in ((2, 5), (2, 6), (3, 4), (2, 7)):
        if cell in body and body[cell] != _c(18, 54, 112):
            body[cell] = _c(236, 250, 255)
    # A tiny bubble rising inside.
    progress = (t % 2.2) / 2.2
    bubble = (7, round(15 - progress * 7))
    extras = []
    if bubble in body and progress < 0.85:
        extras.append((bubble, _c(214, 246, 255, int(230 * (1 - progress)))))
    return body, extras


# Sprout: a leaf with a curl of stem ----------------------------------------------

_LEAF = _bands((0.62, _c(176, 232, 120)), (0.32, _c(112, 196, 88)), (-9, _c(64, 150, 70)))


def _sprout_body(frame: int):
    t = frame / FPS
    cells: dict[Cell, float] = {}
    tilt = 28 + 3.0 * math.sin(2 * math.pi * t / 3.2)  # sways around its tip
    _teardrop(cells, (0, 0), tilt, 17.0, 0.0, 6.0, lens=True)
    _drop_lonely(cells)
    cells[(0, 0)] = 0.0
    body = _outline(cells, _LEAF, _c(28, 74, 38))
    # Midrib: a lighter vein down the middle of the leaf.
    a = math.radians(tilt)
    for u in range(3, 15):
        cell = (int(u * math.sin(a) + 0.5), int(u * math.cos(a) + 0.5))
        if cell in body and body[cell] != _c(28, 74, 38) and abs(u - 10) > 2:
            body[cell] = _c(206, 244, 160)
    # Stem curling out of the round end.
    ex, ey = 17.0 * math.sin(a), 17.0 * math.cos(a)
    for dx, dy in ((0, 0), (1, 1), (2, 1), (3, 0)):
        cell = (int(ex) + dx, int(ey) + dy)
        if cell not in body:
            body[cell] = _c(120, 92, 44)
    extras = []
    if frame % 38 < 3:  # a dewdrop glint near the tip now and then
        extras.append(((2, 3), _c(230, 250, 255)))
    return body, extras


# Mochi: a little black cat -------------------------------------------------------

_CAT = (
    "O..........O",
    "OO........OO",
    "OiO......OiO",
    "OiiOOOOOOiiO",
    "ObhhhhhhhhbO",
    "ObbbbbbbbbbO",
    "ObbbbbbbbbbO",
    "ObbbbbbbbbbO",
    "ObbbbbbbbbbO",
    "ObbbbbbbbbbO",
    ".ObbbbbbbbO.",
    "..OOOOOOOO..",
)
_CAT_COLORS = {"O": _c(14, 12, 22), "b": _c(48, 43, 62), "h": _c(68, 61, 88), "i": _c(170, 136, 214)}


def _mochi_body(frame: int):
    body: dict[Cell, QColor] = {}
    for y, row in enumerate(_CAT):
        for x, ch in enumerate(row):
            if ch != ".":
                body[(x, y)] = _CAT_COLORS[ch]
    if frame % 37 < 2:  # right ear flicks
        body.pop((11, 0), None)
    extras = []
    whisker = _c(205, 200, 222, 210)
    for cell in ((-1, 7), (-2, 8), (12, 7), (13, 8)):
        extras.append((cell, whisker))
    return body, extras


# Twinkle: a chubby star ----------------------------------------------------------

_STAR = _bands((0.62, _c(255, 246, 200)), (0.38, _c(255, 216, 92)), (0.14, _c(250, 180, 60)),
               (-9, _c(232, 146, 44)))
_STAR_CENTER = (6.4, 6.4)
_STAR_R = 9.0


def _star_polygon() -> list[tuple[float, float]]:
    cx, cy = _STAR_CENTER
    pts = []
    for k in range(10):
        a = math.radians(225 + k * 36)
        r = _STAR_R if k % 2 == 0 else _STAR_R * 0.56
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _inside(poly: list[tuple[float, float]], x: float, y: float) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _twinkle_body(frame: int):
    t = frame / FPS
    poly = _star_polygon()
    cx, cy = _STAR_CENTER
    cells: dict[Cell, float] = {}
    shimmer = 0.06 * math.sin(2 * math.pi * t / 1.4)
    for gy in range(-2, 18):
        for gx in range(-3, 18):
            if _inside(poly, gx + 0.5, gy + 0.5):
                d = math.hypot(gx + 0.5 - cx, gy + 0.5 - cy) / _STAR_R
                cells[(gx, gy)] = 1.0 - d + shimmer
    _drop_lonely(cells)
    cells[(0, 0)] = 0.0
    body = _outline(cells, _STAR, _c(122, 72, 20))
    extras = []
    for i, (sx, sy) in enumerate(((15, 1), (16, 12), (-1, 14))):
        phase = (t * 1.3 + i * 0.37) % 1.0
        if phase < 0.5:
            color = _c(255, 250, 220, int(255 * (1 - phase * 2)))
            arms = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)) if phase < 0.25 else ((0, 0),)
            extras.extend((((sx + dx, sy + dy)), color) for dx, dy in arms)
    return body, extras


AVATARS: tuple[Avatar, ...] = (
    Avatar("ember", "Ember", "a cozy campfire", CAMPFIRE,
           Face((4, 10), eye=_c(40, 12, 8), blush=_c(255, 92, 70, 200)), _ember_body, extent=(-1, 0, 12, 17)),
    Avatar("splash", "Splash", "a calm water drop", LAGOON,
           Face((4, 10), eye=_c(12, 34, 74), blush=_c(255, 130, 140, 170)), _splash_body,
           extent=(-1, 0, 10, 16)),
    Avatar("sprout", "Sprout", "a fresh little leaf", MATCHA,
           Face((5, 8), eye=_c(22, 52, 26), blush=_c(255, 140, 110, 190)), _sprout_body,
           extent=(-1, 0, 13, 17)),
    Avatar("mochi", "Mochi", "a sleepy black cat", MIDNIGHT,
           Face((6, 7), eye=_c(255, 214, 77), blush=_c(190, 130, 210, 170), cat=True), _mochi_body,
           extent=(-2, -1, 13, 12)),
    Avatar("twinkle", "Twinkle", "a dreamy star", DREAMY,
           Face((6, 7), eye=_c(70, 40, 12), blush=_c(255, 150, 120, 190)), _twinkle_body,
           extent=(-2, -2, 17, 15)),
)
_BY_ID = {a.id: a for a in AVATARS}
DEFAULT_AVATAR = "ember"


def avatar(avatar_id: str) -> Avatar:
    return _BY_ID.get(avatar_id, _BY_ID[DEFAULT_AVATAR])


def is_blinking(frame: int) -> bool:
    """Blink for two frames every 4.7 s, with a double blink every third time."""
    phase = frame % 47
    return phase < 2 or ((frame // 47) % 3 == 0 and 3 <= phase < 5)


def _face_cells(face: Face, mood: str, blink: bool) -> list[tuple[Cell, QColor]]:
    fx, fy = face.anchor
    eye, shine = face.eye, face.shine
    dark = _c(14, 12, 22) if face.cat else eye
    out: list[tuple[Cell, QColor]] = []

    def block(x: int, y: int, w: int, h: int, color: QColor) -> None:
        out.extend(((x + i, y + j), color) for i in range(w) for j in range(h))

    lefts = (fx - 3, fx + 1)
    # Closed eyes on the black cat use its eye color, or they'd vanish.
    lid = eye if face.cat else dark
    if mood == "happy":
        for ex in (fx - 4, fx + 1):
            block(ex, fy, 1, 1, lid); block(ex + 1, fy - 1, 1, 1, lid); block(ex + 2, fy, 1, 1, lid)
    elif blink:
        for ex in lefts:
            block(ex, fy, 2, 1, lid)
    elif face.cat:
        tall = 3 if mood == "listening" else 2
        for i, ex in enumerate(lefts):
            block(ex, fy - tall + 1, 2, tall, eye)
            # Pupils glance up-left at the real cursor; up-right while thinking.
            px = ex + (1 if mood == "thinking" else 0)
            block(px, fy - tall + 1, 1, 1 if mood == "thinking" else tall, dark)
    elif mood == "listening":
        for ex in lefts:
            block(ex, fy - 2, 2, 3, eye); block(ex, fy - 2, 1, 1, shine)
    elif mood == "thinking":
        for ex in lefts:
            block(ex, fy - 2, 2, 2, eye); block(ex + 1, fy - 2, 1, 1, shine)
    else:
        for ex in lefts:
            block(ex, fy - 1, 2, 2, eye); block(ex, fy - 1, 1, 1, shine)

    if face.cat:
        block(fx - 1, fy + 1, 2, 1, _c(214, 150, 186))           # nose
        if mood == "happy":
            block(fx - 2, fy + 2, 4, 1, dark); block(fx - 1, fy + 3, 2, 1, face.tongue)
        elif mood == "listening":
            block(fx - 1, fy + 2, 2, 2, dark)
        else:
            block(fx - 2, fy + 2, 1, 1, dark); block(fx + 1, fy + 2, 1, 1, dark)
            block(fx - 1, fy + 3, 2, 1, dark)
    elif mood == "happy":
        block(fx - 2, fy + 1, 4, 1, dark); block(fx - 1, fy + 2, 2, 1, face.tongue)
    elif mood == "listening" and not blink:
        block(fx - 1, fy + 2, 2, 2, dark)
    elif mood == "thinking":
        block(fx, fy + 2, 2, 1, dark)
    else:
        block(fx - 2, fy + 1, 1, 1, dark); block(fx + 1, fy + 1, 1, 1, dark); block(fx - 1, fy + 2, 2, 1, dark)

    if face.blush is not None and mood != "thinking":
        block(fx - 4, fy + 1, 1, 1, face.blush)
        block(fx + 3, fy + 1, 1, 1, face.blush)
    return out


@lru_cache(maxsize=512)
def sprite(avatar_id: str, frame: int, mood: str) -> tuple[tuple[Cell, QColor], ...]:
    """All cells of one frame, in paint order: body, face, then extras."""
    av = avatar(avatar_id)
    body, extras = av.body(frame)
    cells = list(body.items())
    cells += _face_cells(av.face, mood, is_blinking(frame))
    cells += extras
    return tuple(cells)


def sprite_bounds(avatar_id: str, frames: int = 40) -> tuple[int, int, int, int]:
    """Cell bounds covered over the first ``frames`` frames of every mood."""
    xs, ys = [], []
    for frame in range(frames):
        for mood in MOODS:
            for (x, y), _color in sprite(avatar_id, frame, mood):
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


CELL = 2.0
"""Logical pixels per sprite cell at scale 1."""


def snapped_cell(scale: float, device_pixel_ratio: float) -> float:
    """Cell size in logical px that is a whole number of device pixels, so
    every cell is the same width on a 125 % or 150 % display."""
    device = max(1, round(CELL * scale * device_pixel_ratio))
    return device / device_pixel_ratio


def paint_avatar(
    painter: QPainter,
    x: float,
    y: float,
    *,
    t: float,
    mood: str = "idle",
    avatar_id: str | None = None,
    scale: float = 1.0,
    device_pixel_ratio: float = 1.0,
    glow: bool = True,
    squash: float = 0.0,
) -> None:
    """Draw an avatar with its tip at (x, y) (logical px)."""
    av = avatar(avatar_id or current_avatar_id())
    cell = snapped_cell(scale, device_pixel_ratio)
    ox = round(x * device_pixel_ratio) / device_pixel_ratio
    oy = round(y * device_pixel_ratio) / device_pixel_ratio
    painter.save()
    painter.translate(ox, oy)
    if squash:
        painter.scale(1.0 + squash, 1.0 - squash * 0.8)
    if glow:
        fx, fy = av.face.anchor
        center = QPointF((fx + 0.5) * cell, (fy + 0.5) * cell)
        radius = 15.0 * cell
        halo = QRadialGradient(center, radius)
        base = av.palette.halo
        halo.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 95))
        halo.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, radius, radius)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for (cx, cy), color in sprite(av.id, int(t * FPS), mood if mood in MOODS else "idle"):
        painter.fillRect(QRectF(cx * cell, cy * cell, cell, cell), color)
    painter.restore()


def paint_burst(painter: QPainter, x: float, y: float, progress: float, *, avatar_id: str | None = None,
                cell: float = CELL) -> None:
    """Pixel confetti popping out of the tip when the avatar lands. progress 0..1."""
    if not 0.0 <= progress < 1.0:
        return
    colors = avatar(avatar_id or current_avatar_id()).palette.dots
    eased = 1.0 - (1.0 - progress) ** 3
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for i in range(8):
        a = math.radians(i * 45 + 20)
        d = 5 * cell + 14 * cell * eased
        color = QColor(colors[i % 3])
        color.setAlpha(int(255 * (1.0 - progress)))
        size = cell * (2 if progress < 0.5 else 1)
        painter.fillRect(QRectF(round(x + d * math.cos(a)), round(y + d * math.sin(a)), size, size), color)
    painter.restore()


def landing_squash(elapsed_s: float) -> float:
    """A springy 'boop' after landing: wide and short, then settle. 0 when done."""
    if elapsed_s < 0 or elapsed_s > 0.45:
        return 0.0
    return 0.14 * math.exp(-elapsed_s * 7.0) * math.cos(elapsed_s * 22.0)


# --- Screen-edge glow -------------------------------------------------------------

GLOW_DOWNSCALE = 6
GLOW_REACH = 96
"""Logical px the glow reaches in from each edge; only these strips repaint."""


def edge_glow_image(width: int, height: int, *, t: float, level: float, audio: float = 0.0,
                    palette: Palette | None = None) -> QImage:
    """The glow drawn small; stretching it back up with smooth scaling is what
    makes it soft, and it keeps the per-frame cost tiny."""
    pal = palette or current_palette()
    w, h = max(8, width // GLOW_DOWNSCALE), max(8, height // GLOW_DOWNSCALE)
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if level <= 0.01:
        return image
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0, 0, w, h)
    gradient = QConicalGradient(rect.center(), (t * 70.0) % 360.0)
    for pos, color in pal.glow:
        gradient.setColorAt(pos, color)
    swell = 1.0 + 0.8 * max(0.0, min(1.0, audio))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(10):
        p.setOpacity(level * (0.42 if i == 0 else 0.16 * (1.0 - i / 10.0)))
        p.setPen(QPen(QBrush(gradient), (1.0 + i * 1.1) * swell))
        p.drawRect(rect)
    p.end()
    return image


def paint_edge_glow(painter: QPainter, rect: QRectF, *, t: float, level: float, audio: float = 0.0,
                    palette: Palette | None = None) -> None:
    """Fluid glow flowing around the edges of ``rect`` (a whole screen)."""
    if level <= 0.01:
        return
    pal = palette or current_palette()
    image = edge_glow_image(int(rect.width()), int(rect.height()), t=t, level=level, audio=audio, palette=pal)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    sx, sy = image.width() / rect.width(), image.height() / rect.height()
    reach = GLOW_REACH
    strips = (
        QRectF(rect.x(), rect.y(), rect.width(), reach),
        QRectF(rect.x(), rect.bottom() - reach, rect.width(), reach),
        QRectF(rect.x(), rect.y() + reach, reach, rect.height() - 2 * reach),
        QRectF(rect.right() - reach, rect.y() + reach, reach, rect.height() - 2 * reach),
    )
    for target in strips:
        source = QRectF((target.x() - rect.x()) * sx, (target.y() - rect.y()) * sy,
                        target.width() * sx, target.height() * sy)
        painter.drawImage(target, image, source)
    # A crisp bright line right at the edge, like light catching the bezel.
    gradient = QConicalGradient(rect.center(), (t * 70.0) % 360.0)
    for pos, color in pal.glow:
        gradient.setColorAt(pos, color)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setOpacity(0.9 * level)
    painter.setPen(QPen(QBrush(gradient), 3.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)
    painter.restore()


# --- Island bits --------------------------------------------------------------------

def island_path(rect: QRectF, radius: float) -> QPainterPath:
    """Rounded on the left, flush with the screen edge on the right."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect.x(), rect.y(), rect.width() + radius * 2, rect.height()), radius, radius)
    return path


def paint_island_shell(painter: QPainter, rect: QRectF, radius: float, palette: Palette | None = None) -> None:
    pal = palette or current_palette()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    for i, alpha in enumerate((30, 18, 10)):
        painter.setBrush(QColor(0, 0, 0, alpha))
        painter.drawPath(island_path(rect.adjusted(-3 - 3 * i, -1 - 2 * i, 0, 4 + 3 * i), radius + 3 * i))
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    top, bottom = QColor(pal.surface_top), QColor(pal.surface_bottom)
    top.setAlpha(246)
    bottom.setAlpha(246)
    gradient.setColorAt(0.0, top)
    gradient.setColorAt(1.0, bottom)
    painter.setBrush(gradient)
    edge = QColor(pal.accent)
    edge.setAlpha(80)
    painter.setPen(QPen(edge, 1.0))
    painter.drawPath(island_path(rect, radius))
    painter.restore()


def paint_bars(painter: QPainter, x: float, y: float, heights: list[float], palette: Palette | None = None) -> None:
    """Rounded level bars, ``heights`` in px, centered on y."""
    pal = palette or current_palette()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    gradient = QLinearGradient(QPointF(x, 0), QPointF(x + 6 * len(heights), 0))
    gradient.setColorAt(0.0, pal.bars[0])
    gradient.setColorAt(1.0, pal.bars[1])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    for i, h in enumerate(heights):
        painter.drawRoundedRect(QRectF(x + i * 6, y - h / 2.0, 3.2, h), 1.6, 1.6)
    painter.restore()


def paint_dots(painter: QPainter, x: float, y: float, phase: float, palette: Palette | None = None) -> None:
    """Three dots bobbing in turn. ``phase`` in radians."""
    pal = palette or current_palette()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    for i, color in enumerate(pal.dots):
        bob = max(0.0, math.sin(phase - i * 0.9)) * 3.5
        painter.setBrush(color)
        painter.drawEllipse(QPointF(x + i * 11, y - bob), 3.4, 3.4)
    painter.restore()


# --- Icons ---------------------------------------------------------------------------

def avatar_image(avatar_id: str, size: int, *, mood: str = "happy", frame: int = 3) -> QImage:
    """The avatar centered in a transparent square, cells as big as fit.
    Below one pixel per cell (tray sizes), it is drawn at one pixel per cell
    and scaled down smoothly."""
    av = avatar(avatar_id)
    min_x, min_y, max_x, max_y = av.extent
    span = max(max_x - min_x + 1, max_y - min_y + 1)
    canvas = max(size, span)
    cell = max(1, canvas // span)
    image = QImage(canvas, canvas, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    ox = (canvas - (max_x - min_x + 1) * cell) // 2 - min_x * cell
    oy = (canvas - (max_y - min_y + 1) * cell) // 2 - min_y * cell
    for (cx, cy), color in sprite(av.id, frame, mood):
        p.fillRect(ox + cx * cell, oy + cy * cell, cell, cell, color)
    p.end()
    if canvas != size:
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


def avatar_icon(avatar_id: str, *, mood: str = "happy") -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(avatar_image(avatar_id, size, mood=mood)))
    return icon


# --- Current avatar --------------------------------------------------------------------

_current: str | None = None
_listeners: list[Callable[[str], None]] = []


def current_avatar_id() -> str:
    global _current
    if _current is None:
        try:
            from config import resolve_setting
            _current = avatar(resolve_setting("AVATAR", DEFAULT_AVATAR)).id
        except Exception:
            _current = DEFAULT_AVATAR
    return _current


def current_avatar() -> Avatar:
    return avatar(current_avatar_id())


def current_palette() -> Palette:
    return current_avatar().palette


def set_avatar(avatar_id: str, *, persist: bool = True) -> None:
    """Switch avatar (and theme) live. Persisted like the other settings."""
    global _current
    _current = avatar(avatar_id).id
    if persist:
        try:
            import keyring
            from config import KEYRING_SERVICE
            keyring.set_password(KEYRING_SERVICE, "AVATAR", _current)
        except Exception:
            pass  # still applies for this session
    for listener in list(_listeners):
        try:
            listener(_current)
        except Exception:
            pass


def on_avatar_changed(listener: Callable[[str], None]) -> None:
    _listeners.append(listener)


# --- Fonts -------------------------------------------------------------------------------

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_FALLBACK_FAMILY = "Segoe UI"
_families: dict[int, str] = {}
"""Loaded Nunito family per weight. Static files can register under their own
legacy family name ("Nunito ExtraBold"), so each weight keeps the family its
file actually got."""
_pixel_family: str | None = None

_FONT_FILES = {
    QFont.Weight.Medium.value: "Nunito-Medium.ttf",
    QFont.Weight.Bold.value: "Nunito-Bold.ttf",
    QFont.Weight.ExtraBold.value: "Nunito-ExtraBold.ttf",
}


def load_fonts() -> None:
    """Register the bundled fonts. Needs a QGuiApplication; safe to call more
    than once. A missing file just falls back to Segoe UI."""
    global _pixel_family
    for weight, filename in _FONT_FILES.items():
        if weight in _families:
            continue
        font_id = QFontDatabase.addApplicationFont(str(_FONT_DIR / filename))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            _families[weight] = families[0]
    if _pixel_family is None:
        font_id = QFontDatabase.addApplicationFont(str(_FONT_DIR / "Silkscreen-Regular.ttf"))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        _pixel_family = families[0] if families else ""


def ui_font(point_size: float, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    """Nunito at the closest bundled weight, or Segoe UI if none loaded."""
    if _families:
        nearest = min(_families, key=lambda w: abs(w - weight.value))
        font = QFont(_families[nearest])
    else:
        font = QFont(_FALLBACK_FAMILY)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


def pixel_font(point_size: float) -> QFont:
    """Silkscreen for titles and chips, or a bold UI font if it didn't load."""
    if _pixel_family:
        font = QFont(_pixel_family)
        font.setPointSizeF(point_size)
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        return font
    return ui_font(point_size, QFont.Weight.ExtraBold)


def pixel_family() -> str:
    return _pixel_family or ui_font(10).family()


# --- App-wide styles -----------------------------------------------------------------------

def _hex(color: QColor) -> str:
    return color.name(QColor.NameFormat.HexRgb)


def _rgba(color: QColor, alpha: int) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def app_stylesheet(palette: Palette | None = None) -> str:
    pal = palette or current_palette()
    bg, raised, line = _hex(pal.surface_bottom), _hex(pal.raised), _hex(pal.line)
    text, muted, accent, on_accent = _hex(pal.text), _hex(pal.muted), _hex(pal.accent), _hex(pal.accent_text)
    return f"""
QDialog, QMessageBox {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {_hex(pal.surface_top)}, stop:1 {bg}); }}
QDialog QLabel, QMessageBox QLabel, QDialog QCheckBox {{ color: {text}; }}
QLabel#clickyTitle {{ font-family: "{pixel_family()}"; font-size: 22px; color: {_hex(pal.chip)}; }}
QLabel#clickySubtitle, QLabel#clickyMuted {{ color: {muted}; }}
QLabel#clickyNote {{ color: {accent}; }}
QLabel#clickySection {{ font-weight: 800; color: {text}; padding-top: 4px; }}
QMessageBox QLabel#qt_msgbox_label {{ font-size: 16px; font-weight: 800; }}
QMessageBox QLabel#qt_msgbox_informativelabel {{ color: {muted}; }}
QLineEdit, QComboBox {{
    background: {raised}; color: {text}; min-height: 22px;
    border: 1.5px solid {line}; border-radius: 10px; padding: 6px 10px;
    selection-background-color: {_rgba(pal.accent, 120)}; selection-color: {text};
}}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {accent}; }}
QLineEdit::placeholder {{ color: {muted}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {raised}; color: {text}; border: 1px solid {line};
    selection-background-color: {_rgba(pal.accent, 70)}; selection-color: {text};
    outline: none; padding: 4px;
}}
QPushButton {{
    background: {raised}; color: {text};
    border: 1.5px solid {line}; border-radius: 12px;
    padding: 6px 16px; font-weight: 700;
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:pressed {{ background: {_rgba(pal.accent, 50)}; }}
QPushButton:disabled {{ color: {_rgba(pal.muted, 140)}; border-color: {_rgba(pal.line, 120)}; }}
QPushButton#clickyPrimary {{ background: {accent}; border-color: {accent}; color: {on_accent}; }}
QPushButton#clickyPrimary:hover {{ background: {_rgba(pal.accent, 220)}; }}
QPushButton#clickyPrimary:disabled {{ background: {_rgba(pal.accent, 70)}; border-color: transparent; color: {_rgba(pal.accent_text, 150)}; }}
QToolButton#clickyAvatar {{
    background: {raised}; color: {muted}; border: 1.5px solid {line};
    border-radius: 14px; padding: 6px 4px 4px 4px; font-weight: 700;
}}
QToolButton#clickyAvatar:hover {{ border-color: {_rgba(pal.accent, 160)}; color: {text}; }}
QToolButton#clickyAvatar:checked {{ border: 2px solid {accent}; color: {text}; background: {_rgba(pal.accent, 34)}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid {line}; background: {raised}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QMenu {{ background: {bg}; color: {text}; border: 1px solid {line}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 8px; }}
QMenu::item:selected {{ background: {_rgba(pal.accent, 60)}; color: {text}; }}
QMenu::item:checked {{ color: {accent}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 5px 10px; }}
QToolTip {{ background: {bg}; color: {text}; border: 1px solid {line}; border-radius: 8px; padding: 6px; }}
"""


def apply(app) -> None:
    """Load the fonts, set the app font and stylesheet, and keep the
    stylesheet in step when the avatar (and so the theme) changes."""
    load_fonts()
    app.setFont(ui_font(10))
    app.setStyleSheet(app_stylesheet())
    on_avatar_changed(lambda _id: app.setStyleSheet(app_stylesheet()))
