"""Look of Clixy: brand, fonts, the pixel buddies and app styles.

Brand: ink, butter and warm paper, with coral only for "live". Type is
Manrope (light for display, regular for text) with JetBrains Mono for small
numbers. The mark is a circle with a pill bitten out of its side: a C, and
the side notch growing out of the screen edge.

Buddies: five pixel-art pointers (Ember, Splash, Sprout, Mochi, Twinkle).
Each is drawn on a grid of square cells with its pointing tip at cell
(0, 0): the tip's top-left corner is the pixel the model asked for. Sprites
step at 10 fps like real pixel art, and each frame is cached. Each buddy
brings its own colors for its glow, its landing burst and the screen-edge
glow; the app's chrome stays in the brand colors.

Nothing here keeps app state except which buddy is current; callers pass
the time and mood, the helpers draw.
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
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)


def _c(r: int, g: int, b: int, a: int = 255) -> QColor:
    return QColor(r, g, b, a)


# --- Brand -----------------------------------------------------------------------

INK = _c(10, 10, 18)
PAPER = _c(245, 242, 234)
CARD = _c(255, 255, 255)
BUTTER = _c(255, 241, 161)
SUN = _c(255, 210, 63)
"""Bold yellow for the big cards (the connection, a key that looks right)."""
CORAL = _c(255, 90, 61)
LINE = _c(226, 221, 208)
MIST = _c(241, 237, 226)
MUTED = _c(106, 106, 119)

NOTCH = _c(0, 0, 0)
NOTCH_TEXT = _c(255, 255, 255)
NOTCH_MUTED = _c(141, 141, 153)
NOTCH_DIM = _c(104, 104, 117)
NOTCH_CHIP_DIM = _c(52, 52, 62)
NOTCH_CHIP_DIM_TEXT = _c(182, 182, 194)


LOGO_BUDDY = "ember"
"""Clixy's logo is Ember, the pixel fire."""


def logo_image(size: int) -> QImage:
    """The app icon: Ember on an ink tile with a warm glow, its pixels
    crisp. Readable on light and dark taskbars alike. Small sizes are drawn
    big and scaled down smoothly."""
    if size < 48:
        return logo_image(96).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(INK)
    radius = size * 0.24
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    glow = QRadialGradient(QPointF(size * 0.5, size * 0.58), size * 0.42)
    glow.setColorAt(0.0, QColor(255, 120, 30, 120))
    glow.setColorAt(1.0, QColor(255, 120, 30, 0))
    p.setBrush(glow)
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    av = avatar(LOGO_BUDDY)
    min_x, min_y, max_x, max_y = av.extent
    span = max(max_x - min_x + 1, max_y - min_y + 1)
    cell = max(1, int(size * 0.76 // span))
    w, h = (max_x - min_x + 1) * cell, (max_y - min_y + 1) * cell
    ox = (size - w) // 2 - min_x * cell
    oy = (size - h) // 2 - min_y * cell + size // 40
    for (cx, cy), color in sprite(av.id, 3, "happy"):
        p.fillRect(QRectF(ox + cx * cell, oy + cy * cell, cell, cell), color)
    p.end()
    return image


def app_icon_image(size: int) -> QImage:
    """App, window and EXE icon: the logo."""
    return logo_image(size)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(app_icon_image(size)))
    return icon


# --- Buddy palettes ----------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    """A buddy's own colors: its glow, its landing burst and the screen-edge
    glow while you talk. ``glow`` stops run around the screen edge.
    ``flood`` is the bold colour the app wears for this buddy (the Settings
    rail, the setup panel, its card), with ``on_flood`` for text on it."""

    name: str
    glow: tuple[tuple[float, QColor], ...]
    accent: QColor
    halo: QColor
    dots: tuple[QColor, QColor, QColor]
    flood: QColor
    on_flood: QColor


CAMPFIRE = Palette(
    name="Campfire",
    glow=((0.00, _c(255, 70, 30)), (0.18, _c(255, 140, 30)), (0.33, _c(255, 214, 90)),
          (0.50, _c(255, 110, 40)), (0.68, _c(230, 40, 50)), (0.84, _c(255, 160, 50)),
          (1.00, _c(255, 70, 30))),
    accent=_c(255, 122, 36), halo=_c(255, 120, 30),
    dots=(_c(255, 104, 30), _c(255, 166, 30), _c(255, 218, 80)),
    flood=_c(255, 106, 31), on_flood=INK,
)

LAGOON = Palette(
    name="Lagoon",
    glow=((0.00, _c(40, 110, 255)), (0.20, _c(40, 200, 255)), (0.36, _c(130, 255, 225)),
          (0.52, _c(60, 150, 255)), (0.70, _c(90, 90, 255)), (0.86, _c(60, 220, 240)),
          (1.00, _c(40, 110, 255))),
    accent=_c(62, 190, 255), halo=_c(60, 170, 255),
    dots=(_c(60, 140, 255), _c(60, 200, 255), _c(130, 250, 220)),
    flood=_c(30, 150, 255), on_flood=INK,
)

MATCHA = Palette(
    name="Matcha",
    glow=((0.00, _c(60, 170, 90)), (0.20, _c(150, 220, 90)), (0.36, _c(240, 236, 160)),
          (0.52, _c(90, 200, 120)), (0.70, _c(40, 140, 110)), (0.86, _c(170, 230, 110)),
          (1.00, _c(60, 170, 90))),
    accent=_c(132, 210, 104), halo=_c(110, 210, 110),
    dots=(_c(80, 180, 90), _c(150, 220, 100), _c(230, 236, 150)),
    flood=_c(46, 190, 106), on_flood=INK,
)

MIDNIGHT = Palette(
    name="Midnight",
    glow=((0.00, _c(90, 80, 230)), (0.20, _c(160, 100, 255)), (0.36, _c(255, 212, 100)),
          (0.52, _c(140, 90, 250)), (0.70, _c(70, 70, 200)), (0.86, _c(200, 150, 255)),
          (1.00, _c(90, 80, 230))),
    accent=_c(178, 140, 255), halo=_c(150, 110, 255),
    dots=(_c(150, 110, 255), _c(200, 160, 255), _c(255, 212, 100)),
    flood=_c(112, 84, 255), on_flood=_c(255, 255, 255),
)

DREAMY = Palette(
    name="Dreamy",
    glow=((0.00, _c(170, 150, 255)), (0.20, _c(120, 165, 255)), (0.36, _c(120, 225, 255)),
          (0.52, _c(255, 226, 140)), (0.70, _c(160, 140, 255)), (0.86, _c(110, 200, 255)),
          (1.00, _c(170, 150, 255))),
    accent=_c(255, 210, 90), halo=_c(255, 220, 120),
    dots=(_c(160, 150, 255), _c(120, 210, 255), _c(255, 222, 140)),
    flood=_c(255, 201, 51), on_flood=INK,
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


def paint_avatar_in(painter: QPainter, rect: QRectF, *, t: float, mood: str = "idle",
                    avatar_id: str | None = None, scale: float = 1.0,
                    device_pixel_ratio: float = 1.0, glow: bool = False) -> None:
    """Draw an avatar centered in ``rect`` using its framing box, so every
    buddy sits in the same spot however its tip is placed."""
    av = avatar(avatar_id or current_avatar_id())
    min_x, min_y, max_x, max_y = av.extent
    cell = snapped_cell(scale, device_pixel_ratio)
    w, h = (max_x - min_x + 1) * cell, (max_y - min_y + 1) * cell
    paint_avatar(painter, rect.center().x() - w / 2 - min_x * cell, rect.center().y() - h / 2 - min_y * cell,
                 t=t, mood=mood, avatar_id=av.id, scale=scale,
                 device_pixel_ratio=device_pixel_ratio, glow=glow)


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
_FONT_FILES = {
    QFont.Weight.ExtraLight.value: "Manrope-ExtraLight.ttf",
    QFont.Weight.Light.value: "Manrope-Light.ttf",
    QFont.Weight.Normal.value: "Manrope-Regular.ttf",
    QFont.Weight.Medium.value: "Manrope-Medium.ttf",
    QFont.Weight.DemiBold.value: "Manrope-SemiBold.ttf",
}
_MONO_FILES = {
    QFont.Weight.Normal.value: "JetBrainsMono-Regular.ttf",
    QFont.Weight.Medium.value: "JetBrainsMono-Medium.ttf",
}
_families: dict[int, str] = {}
"""Loaded Manrope family per weight. Each static file can register under its
own legacy family name ("Manrope Light"), so a weight keeps the family its
file actually got."""
_mono_families: dict[int, str] = {}


def _load(files: dict[int, str], into: dict[int, str]) -> None:
    for weight, filename in files.items():
        if weight in into:
            continue
        font_id = QFontDatabase.addApplicationFont(str(_FONT_DIR / filename))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            into[weight] = families[0]


def load_fonts() -> None:
    """Register the bundled fonts. Needs a QGuiApplication; safe to call more
    than once. A missing file just falls back to Segoe UI."""
    _load(_FONT_FILES, _families)
    _load(_MONO_FILES, _mono_families)


def _pick(families: dict[int, str], fallback: str, point_size: float, weight: QFont.Weight) -> QFont:
    if families:
        nearest = min(families, key=lambda w: abs(w - weight.value))
        font = QFont(families[nearest])
    else:
        font = QFont(fallback)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def ui_font(point_size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Manrope at the closest bundled weight, or Segoe UI if none loaded."""
    return _pick(_families, _FALLBACK_FAMILY, point_size, weight)


def display_font(point_size: float) -> QFont:
    """Manrope Light with tight tracking, for headings and the answer."""
    font = ui_font(point_size, QFont.Weight.Light)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 97)
    return font


def mono_font(point_size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    return _pick(_mono_families, "Cascadia Mono", point_size, weight)


def family(weight: QFont.Weight = QFont.Weight.Normal) -> str:
    return ui_font(10, weight).family()


# --- App-wide styles -----------------------------------------------------------------------

def _hex(color: QColor) -> str:
    return color.name(QColor.NameFormat.HexRgb)


def _chevron_file() -> str:
    """A small ink chevron for dropdowns, drawn once into the temp folder
    (style sheets take images only from files). '' if that fails."""
    try:
        import tempfile
        path = Path(tempfile.gettempdir()) / "clixy-ui" / "chevron-1.png"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            image = QImage(48, 48, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            p = QPainter(image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QPen(INK, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline([QPointF(13, 19), QPointF(24, 30), QPointF(35, 19)])
            p.end()
            image.save(str(path))
        return path.as_posix()
    except Exception:
        return ""


def app_stylesheet() -> str:
    ink, paper, card, butter, coral = _hex(INK), _hex(PAPER), _hex(CARD), _hex(BUTTER), _hex(CORAL)
    sun = _hex(SUN)
    line, mist, muted = _hex(LINE), _hex(MIST), _hex(MUTED)
    chevron = _chevron_file()
    thin, light, regular, medium, semi = (family(w) for w in (QFont.Weight.ExtraLight, QFont.Weight.Light,
                                                             QFont.Weight.Normal, QFont.Weight.Medium,
                                                             QFont.Weight.DemiBold))
    return f"""
QDialog, QMessageBox {{ background: {paper}; color: {ink}; }}
QDialog QLabel, QMessageBox QLabel, QDialog QCheckBox {{ color: {ink}; }}
QMessageBox QLabel#qt_msgbox_label {{ font-family: "{light}"; font-size: 20px; }}
QMessageBox QLabel#qt_msgbox_informativelabel {{ color: {muted}; }}

QLabel#h1 {{ font-family: "{thin}"; font-size: 44px; color: {ink}; }}
QLabel#h2 {{ font-family: "{light}"; font-size: 24px; color: {ink}; }}
QLabel#lead {{ color: #4a4a57; font-size: 14px; }}
QLabel#muted {{ color: {muted}; font-size: 13px; }}
QLabel#rowTitle {{ font-family: "{medium}"; font-size: 14px; color: {ink}; }}
QLabel#rowSub {{ color: {muted}; font-size: 12.5px; }}
QLabel#good {{ color: #157f3b; }}
QLabel#bad {{ color: {coral}; }}

QWidget#rail {{ background: {ink}; }}
QLabel#railWordmark {{ font-family: "{light}"; font-size: 22px; color: #ffffff; }}
QPushButton#nav {{ text-align: left; background: transparent; border: none; border-radius: 12px;
    padding: 9px 12px; font-family: "{light}"; font-size: 16px; color: #9c9cab; }}
QPushButton#nav:hover {{ color: #ffffff; }}
QPushButton#nav:checked {{ background: {butter}; color: {ink}; }}
QLabel#railVersion {{ font-family: "{regular}"; font-size: 10.5px; color: #5c5c6b; }}
QLabel#railBuddy {{ font-family: "{regular}"; font-size: 12px; color: #9c9cab; padding-bottom: 4px; }}
QWidget#pane {{ background: {paper}; }}
QScrollArea#pageScroll, QWidget#page {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 6px 2px; }}
QScrollBar::handle:vertical {{ background: #d9d4c6; border-radius: 3px; min-height: 40px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ height: 0; background: transparent; }}
QFrame#rows {{ background: {card}; border: 1px solid #e8e2d3; border-radius: 18px; }}
QFrame#rowSep {{ background: #f1ede2; max-height: 1px; min-height: 1px; border: none; margin: 0 18px; }}
QFrame#conn {{ background: {sun}; border-radius: 22px; }}
QLabel#connModel {{ font-family: "{light}"; font-size: 28px; color: {ink}; }}
QLabel#connChip {{ background: transparent; border: 1.4px solid rgba(10, 10, 18, 0.45); border-radius: 11px;
    padding: 3px 10px; color: {ink}; font-size: 11.5px; }}
QLabel#whereHere {{ background: {sun}; border-radius: 11px; padding: 4px 11px; color: {ink}; }}
QLabel#whereAway {{ background: {mist}; border-radius: 11px; padding: 4px 11px; color: {ink}; }}
QLabel#liveChip {{ background: {ink}; border-radius: 11px; padding: 4px 10px; color: {sun}; font-size: 11.5px; }}

QLineEdit, QComboBox {{
    background: {card}; color: {ink}; min-height: 22px;
    border: 1.5px solid {line}; border-radius: 12px; padding: 7px 12px;
    selection-background-color: {butter}; selection-color: {ink};
}}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {ink}; }}
QComboBox::drop-down {{ border: none; width: 34px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {card}; color: {ink}; border: 1px solid {line};
    selection-background-color: {butter}; selection-color: {ink}; outline: none; padding: 4px; }}

QPushButton {{ background: {card}; color: {ink}; border: 1.5px solid {ink}; border-radius: 19px;
    padding: 8px 18px; font-family: "{semi}"; font-size: 13.5px; }}
QPushButton:hover {{ background: {mist}; }}
QPushButton:disabled {{ color: #a39d8f; border-color: #d9d4c6; background: transparent; }}
QPushButton#primary {{ background: {ink}; color: #ffffff; border-color: {ink}; }}
QPushButton#primary:hover {{ background: #24242e; }}
QPushButton#primary:disabled {{ background: #d9d4c6; border-color: #d9d4c6; color: #8f887a; }}
QPushButton#quiet {{ border: none; background: transparent; color: {ink}; text-decoration: underline; padding: 6px 4px; }}
QPushButton#danger {{ color: {coral}; border-color: {coral}; background: transparent; }}
QPushButton#seg {{ border: none; background: transparent; border-radius: 15px; padding: 6px 14px;
    font-family: "{medium}"; font-size: 13px; color: {muted}; }}
QPushButton#seg:checked {{ background: {ink}; color: #ffffff; }}
QFrame#segTrack {{ background: {mist}; border-radius: 19px; }}

QMenu {{ background: {ink}; color: #ffffff; border: 1px solid #23232e; border-radius: 14px; padding: 6px;
    font-family: "{regular}"; font-size: 13px; }}
QMenu::item {{ padding: 8px 22px 8px 12px; border-radius: 9px; }}
QMenu::item:selected {{ background: {butter}; color: {ink}; }}
QMenu::item:checked {{ color: {butter}; }}
QMenu::separator {{ height: 1px; background: #23232e; margin: 5px 10px; }}
QToolTip {{ background: {ink}; color: #ffffff; border: none; border-radius: 8px; padding: 6px 9px; }}
"""


def _rgba(color: QColor, alpha: float) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha:g})"


def rail_stylesheet(palette: Palette | None = None) -> str:
    """The Settings rail in the buddy's colour: set on the rail itself, so it
    changes the moment you pick another buddy."""
    pal = palette or current_palette()
    fg = pal.on_flood
    pill_bg, pill_fg = (INK, CARD) if fg == INK else (CARD, INK)
    light, regular, medium = (family(w) for w in (QFont.Weight.Light, QFont.Weight.Normal, QFont.Weight.Medium))
    return f"""
QWidget#rail {{ background: {_hex(pal.flood)}; }}
QLabel#railWordmark {{ font-family: "{family(QFont.Weight.ExtraLight)}"; font-size: 24px; color: {_hex(fg)}; }}
QPushButton#nav {{ text-align: left; background: transparent; border: none; border-radius: 19px;
    padding: 9px 16px; font-family: "{light}"; font-size: 16px; color: {_rgba(fg, 0.82)}; }}
QPushButton#nav:hover {{ color: {_hex(fg)}; background: {_rgba(fg, 0.1)}; }}
QPushButton#nav:checked {{ background: {_hex(pill_bg)}; color: {_hex(pill_fg)}; font-family: "{medium}"; }}
QLabel#railBuddy {{ font-family: "{medium}"; font-size: 12.5px; color: {_hex(fg)}; padding-bottom: 4px; }}
QLabel#railVersion {{ font-family: "{regular}"; font-size: 10.5px; color: {_rgba(fg, 0.6)}; }}
"""


def heading_html(text: str, accent: str = "") -> str:
    """A headline in the video's style: ``accent`` (a word in ``text``) in
    coral, and a coral full stop when the text doesn't end with one."""
    import html
    body = html.escape(text.rstrip("."))
    if accent and accent in text:
        body = body.replace(html.escape(accent), f'<span style="color:{_hex(CORAL)}">{html.escape(accent)}</span>', 1)
    dot_color = INK if accent else CORAL
    return f'{body}<span style="color:{_hex(dot_color)}">.</span>'


def apply(app) -> None:
    """Load the fonts and set the app font and stylesheet."""
    load_fonts()
    app.setFont(ui_font(10))
    app.setStyleSheet(app_stylesheet())
