"""Clixy's look and feel: the two skins, springs, little sounds, and the tactile
controls the panel and Settings are made of.

- Skins. Daylight (the default) is bright: cool white cards on a soft grey desk,
  your buddy's colour as glossy gel. Midnight is the dark one: deep plum ink,
  never flat black. "system" follows Windows' own light or dark choice.
  ``skin()`` is the one in use; ``set_appearance()`` switches live and tells
  every listener (``on_skin_changed``).
- Springs. Things move like objects: a button sinks when pressed and springs
  back, a switch's knob overshoots a hair, the Settings highlight slides. A
  widget mixes in ``Motion``, names its springs, and repaints while any of them
  moves (one shared 60 Hz ticker, idle when nothing moves).
- Sounds. ``sound(name)`` plays one of sfx.py's little effects ("press",
  "toggle_on", "open"...), and quietly does nothing if sound is off or broken.
- Controls: ``Toggle``, ``Segmented``, ``Slider``, ``NavRail`` (the Settings
  list with coloured icon tiles and a sliding highlight), and painting helpers
  for gel, cards, tiles and line icons.

Threading: Qt main thread only.
"""
from __future__ import annotations

import math
import time
import weakref
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QAbstractButton, QWidget

import theme

# --- Colour helpers --------------------------------------------------------------------------------


def rgba(color: QColor | str, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def mix(a: QColor | str, b: QColor | str, k: float) -> QColor:
    a, b = QColor(a), QColor(b)
    k = max(0.0, min(1.0, k))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * k, a.greenF() + (b.greenF() - a.greenF()) * k,
                           a.blueF() + (b.blueF() - a.blueF()) * k, a.alphaF() + (b.alphaF() - a.alphaF()) * k)


def luminance(color: QColor) -> float:
    """Relative luminance (0 black - 1 white), as WCAG counts it."""
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(color.redF()) + 0.7152 * lin(color.greenF()) + 0.0722 * lin(color.blueF())


def ink_on(color: QColor) -> QColor:
    """White on a deep colour, near-black (tinted by it) on a pale one (Twinkle's yellow)."""
    if luminance(color) < 0.45:
        return QColor(255, 255, 255)
    return mix(color, QColor(0, 0, 0), 0.86)


def accent() -> QColor:
    """Your buddy's colour, the one accent everywhere."""
    return QColor(theme.current_palette().flood)


# --- Skins -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Skin:
    name: str
    dark: bool
    canvas_top: QColor          # the panel's own surface, top to bottom
    canvas_bottom: QColor
    surface: QColor             # cards
    surface_hover: QColor
    raised: QColor              # soft pill buttons, top to bottom
    raised_bottom: QColor
    border: QColor              # around cards and pills
    hairline: QColor            # between rows
    text: QColor
    text2: QColor               # secondary
    text3: QColor               # quiet hints
    field: QColor               # text boxes
    shadow: QColor              # under cards
    track: QColor               # a switch that's off, a slider's empty part
    key_top: QColor             # keycaps
    key_bottom: QColor
    key_edge: QColor
    key_text: QColor
    dots: QColor                # the canvas's faint dot grid


DAYLIGHT = Skin(
    name="light", dark=False,
    canvas_top=QColor("#F7F8FC"), canvas_bottom=QColor("#ECEEF5"),
    surface=QColor("#FFFFFF"), surface_hover=QColor("#FBFBFF"),
    raised=QColor("#FFFFFF"), raised_bottom=QColor("#F0F1F7"),
    border=QColor(22, 26, 48, 24), hairline=QColor(22, 26, 48, 18),
    text=QColor("#121318"), text2=QColor("#575C6E"), text3=QColor("#8E93A5"),
    field=QColor("#FFFFFF"), shadow=QColor(36, 42, 80, 46), track=QColor("#E2E4EC"),
    key_top=QColor("#FFFFFF"), key_bottom=QColor("#EEF0F6"), key_edge=QColor("#C6CAD6"),
    key_text=QColor("#22242C"), dots=QColor(30, 36, 70, 26),
)

MIDNIGHT = Skin(
    name="dark", dark=True,
    canvas_top=QColor("#1D1B29"), canvas_bottom=QColor("#14131D"),
    surface=QColor("#25232F"), surface_hover=QColor("#2B2937"),
    raised=QColor("#302E3D"), raised_bottom=QColor("#25232F"),
    border=QColor(255, 255, 255, 18), hairline=QColor(255, 255, 255, 13),
    text=QColor("#F6F5FB"), text2=QColor("#ADABC0"), text3=QColor("#77758A"),
    field=QColor("#1A1924"), shadow=QColor(0, 0, 0, 120), track=QColor("#363443"),
    key_top=QColor("#3A3847"), key_bottom=QColor("#2D2B39"), key_edge=QColor("#0F0E15"),
    key_text=QColor("#EEEDF4"), dots=QColor(255, 255, 255, 16),
)

APPEARANCES = (("light", "Daylight"), ("dark", "Midnight"), ("system", "Match Windows"))

_appearance: str | None = None
_listeners: list = []


def appearance() -> str:
    """The stored choice: "light" (default), "dark" or "system"."""
    global _appearance
    if _appearance is None:
        try:
            from config import resolve_setting
            _appearance = resolve_setting("APPEARANCE", default="light").lower()
        except Exception:
            _appearance = "light"
        if _appearance not in ("light", "dark", "system"):
            _appearance = "light"
    return _appearance


def windows_is_dark() -> bool:
    """Windows' own app mode (Settings > Personalisation > Colours)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def skin() -> Skin:
    choice = appearance()
    if choice == "system":
        return MIDNIGHT if windows_is_dark() else DAYLIGHT
    return MIDNIGHT if choice == "dark" else DAYLIGHT


def set_appearance(choice: str) -> None:
    """Switch live (Settings previews it; Done stores it)."""
    global _appearance
    choice = choice if choice in ("light", "dark", "system") else "light"
    if choice == _appearance:
        return
    _appearance = choice
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def on_skin_changed(fn) -> None:
    _listeners.append(fn)


# --- Type ----------------------------------------------------------------------------------------------

_WEIGHTS = {400: QFont.Weight.Normal, 500: QFont.Weight.Medium, 600: QFont.Weight.DemiBold,
            700: QFont.Weight.Bold, 800: QFont.Weight.ExtraBold}


def font(px: float, weight: int = 600, tracking_em: float = 0.0) -> QFont:
    """Manrope at ``px`` pixels."""
    f = theme.ui_font(10, _WEIGHTS.get(weight, QFont.Weight.DemiBold))
    f.setPixelSize(max(1, round(px)))
    if tracking_em:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking_em * px)
    return f


# --- Sound ----------------------------------------------------------------------------------------------


SILENT = False
"""True in tests: nothing ever reaches the speakers."""


def sound(name: str, gain: float = 1.0) -> None:
    """One of sfx.py's little sounds; silent if sound is off or unavailable, and
    always silent off screen (tests, the dev picture tools)."""
    if SILENT:
        return
    try:
        from PyQt6.QtGui import QGuiApplication
        if QGuiApplication.platformName() in ("offscreen", "minimal"):
            return
        import sfx
        sfx.play(name, gain)
    except Exception:
        pass


# --- Springs ---------------------------------------------------------------------------------------------


class Spring:
    """A damped spring toward ``target`` (unit mass). Stiffness ~300-900 and
    damping a little under critical (2*sqrt(k)) give a lively settle."""

    __slots__ = ("x", "v", "target", "k", "c")

    def __init__(self, value: float = 0.0, stiffness: float = 420.0, damping: float = 30.0) -> None:
        self.x = self.target = float(value)
        self.v = 0.0
        self.k, self.c = stiffness, damping

    def step(self, dt: float) -> bool:
        """Advance by ``dt`` seconds; False once it has settled."""
        n = max(1, math.ceil(dt / (1 / 240)))      # small sub-steps keep stiff springs stable
        h = dt / n
        for _ in range(n):
            a = self.k * (self.target - self.x) - self.c * self.v
            self.v += a * h
            self.x += self.v * h
        if abs(self.target - self.x) < 1e-3 and abs(self.v) < 1e-2:
            self.x, self.v = self.target, 0.0
            return False
        return True


class _Ticker(QObject):
    """One 60 Hz timer for every moving widget; stops when nothing moves."""

    def __init__(self) -> None:
        super().__init__()
        self._items: "weakref.WeakSet" = weakref.WeakSet()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._last = time.monotonic()

    def add(self, item) -> None:
        if not self._items:
            self._last = time.monotonic()
        self._items.add(item)
        if not self._timer.isActive():
            self._timer.start(16)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(1 / 20, max(1 / 240, now - self._last))
        self._last = now
        for item in list(self._items):
            try:
                moving = item._motion_step(dt)
                item.update()
            except RuntimeError:          # the widget is gone
                moving = False
            if not moving:
                self._items.discard(item)
        if not self._items:
            self._timer.stop()


_ticker: _Ticker | None = None


def _the_ticker() -> _Ticker:
    global _ticker
    if _ticker is None:
        _ticker = _Ticker()
    return _ticker


class Motion:
    """Mixin for a QWidget: named springs that repaint the widget while they move."""

    def spring(self, name: str, value: float = 0.0, stiffness: float = 420.0, damping: float = 30.0) -> Spring:
        springs = self.__dict__.setdefault("_springs", {})
        s = springs.get(name)
        if s is None:
            s = springs[name] = Spring(value, stiffness, damping)
        return s

    def go(self, name: str, target: float, *, snap: bool = False, kick: float = 0.0) -> None:
        """Send spring ``name`` to ``target`` (``snap``: be there now; ``kick``: a push of speed)."""
        s = self.spring(name)
        s.target = float(target)
        if snap:
            s.x, s.v = s.target, 0.0
            self.update()
            return
        s.v += kick
        _the_ticker().add(self)

    def val(self, name: str) -> float:
        return self.spring(name).x

    def _motion_step(self, dt: float) -> bool:
        moving = False
        for s in self.__dict__.get("_springs", {}).values():
            if s.x != s.target or s.v:
                moving = s.step(dt) or moving
        return moving or bool(getattr(self, "_always_moving", False))


# --- Painting helpers -----------------------------------------------------------------------------------


def pill(rect: QRectF, radius: float | None = None) -> QPainterPath:
    path = QPainterPath()
    r = rect.height() / 2 if radius is None else min(radius, rect.height() / 2, rect.width() / 2)
    path.addRoundedRect(rect, r, r)
    return path


def squircle(rect: QRectF, k: float = 0.3) -> QPainterPath:
    """An app-icon squircle: a rounded square with smooth (not circular) corners."""
    r = min(rect.width(), rect.height()) * k
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    c = r * 0.18          # how far the curve hugs the straight edge before turning
    path = QPainterPath(QPointF(x0 + r, y0))
    path.lineTo(x1 - r, y0)
    path.cubicTo(QPointF(x1 - c, y0), QPointF(x1, y0 + c), QPointF(x1, y0 + r))
    path.lineTo(x1, y1 - r)
    path.cubicTo(QPointF(x1, y1 - c), QPointF(x1 - c, y1), QPointF(x1 - r, y1))
    path.lineTo(x0 + r, y1)
    path.cubicTo(QPointF(x0 + c, y1), QPointF(x0, y1 - c), QPointF(x0, y1 - r))
    path.lineTo(x0, y0 + r)
    path.cubicTo(QPointF(x0, y0 + c), QPointF(x0 + c, y0), QPointF(x0 + r, y0))
    path.closeSubpath()
    return path


def soft_shadow(painter: QPainter, shape: QPainterPath, *, dy: float, spread: float, color: QColor,
                layers: int = 6) -> None:
    """A soft shadow made of a few widening, fading strokes of ``shape`` (cheap,
    no blur pass): ``spread`` px of softness, ``dy`` px down."""
    if color.alpha() == 0:
        return
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    moved = shape.translated(0, dy)
    for i in range(layers, 0, -1):
        k = i / layers
        c = QColor(color)
        c.setAlphaF(color.alphaF() * (1 - k) ** 1.6 / layers * 2.2)
        pen = QPen(c, 2 * spread * k)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(moved)
    painter.fillPath(moved, rgba(color, color.alphaF() * 0.5))
    painter.restore()


def gel_colors(color: QColor) -> tuple[QColor, QColor, QColor]:
    """Top, middle and bottom of a gel button in ``color``."""
    h, s, l, _a = color.getHslF()
    h = h if h >= 0 else 0.0
    top = mix(color, QColor(255, 255, 255), 0.38)
    bottom = QColor.fromHslF((h - 0.012) % 1.0, min(1.0, s * 1.04), max(0.0, l * 0.8))
    return top, QColor(color), bottom


def paint_gel(painter: QPainter, rect: QRectF, color: QColor, *, radius: float | None = None,
              hover: float = 0.0, press: float = 0.0, shape: QPainterPath | None = None) -> None:
    """Candy gel: a deep gradient, a glow welling up from the bottom, a glassy
    highlight across the top and a crisp rim. ``hover`` brightens it, ``press``
    darkens it and dims the highlight (0-1 each)."""
    shape = shape if shape is not None else pill(rect, radius)
    top, mid, bottom = gel_colors(color)
    if hover:
        top, mid, bottom = (mix(c, QColor(255, 255, 255), 0.08 * hover) for c in (top, mid, bottom))
    if press:
        top, mid, bottom = (mix(c, QColor(0, 0, 0), 0.1 * press) for c in (top, mid, bottom))
    painter.save()
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, top)
    grad.setColorAt(0.55, mid)
    grad.setColorAt(1.0, bottom)
    painter.fillPath(shape, QBrush(grad))
    painter.setClipPath(shape)
    # Light welling up inside the bottom, like a lit sweet.
    glow = QRadialGradient(QPointF(rect.center().x(), rect.bottom() + rect.height() * 0.15),
                           max(rect.width(), rect.height()) * 0.62)
    glow.setColorAt(0.0, rgba(mix(color, QColor(255, 255, 255), 0.55), 0.55 * (1 - 0.5 * press)))
    glow.setColorAt(1.0, rgba(color, 0.0))
    painter.fillRect(rect, QBrush(glow))
    # The glassy highlight on the top half.
    inset = min(rect.height() * 0.16, 6.0)
    hl = QRectF(rect.left() + inset * 1.4, rect.top() + 1.2, rect.width() - inset * 2.8, rect.height() * 0.46)
    hg = QLinearGradient(hl.topLeft(), hl.bottomLeft())
    hg.setColorAt(0.0, QColor(255, 255, 255, round(150 * (1 - 0.6 * press))))
    hg.setColorAt(1.0, QColor(255, 255, 255, 8))
    painter.fillPath(pill(hl, None if radius is None else max(2.0, radius - inset)), QBrush(hg))
    painter.restore()
    # A crisp rim: darker below, so it reads on white and on ink.
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    rim.setColorAt(0.0, rgba(mix(color, QColor(0, 0, 0), 0.05), 0.35))
    rim.setColorAt(1.0, rgba(mix(color, QColor(0, 0, 0), 0.35), 0.75))
    painter.setPen(QPen(QBrush(rim), 1.0))
    painter.drawPath(shape)
    painter.restore()


def paint_raised(painter: QPainter, rect: QRectF, sk: Skin, *, radius: float | None = None,
                 hover: float = 0.0, press: float = 0.0) -> None:
    """A soft pill that stands up off the canvas (the quieter buttons)."""
    shape = pill(rect, radius)
    soft_shadow(painter, shape, dy=1.5 + 1.5 * hover * (1 - press), spread=3 + 3 * hover,
                color=rgba(sk.shadow, sk.shadow.alphaF() * (0.7 + 0.5 * hover) * (1 - 0.6 * press)))
    top, bottom = sk.raised, sk.raised_bottom
    if hover:
        lift = QColor(255, 255, 255) if not sk.dark else QColor(255, 255, 255)
        top, bottom = mix(top, lift, 0.06 * hover), mix(bottom, lift, 0.05 * hover)
    if press:
        top, bottom = mix(top, bottom, 0.8 * press), bottom
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bottom)
    painter.fillPath(shape, QBrush(grad))
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(sk.border, 1.0))
    painter.drawPath(pill(rect.adjusted(0.5, 0.5, -0.5, -0.5), None if radius is None else radius - 0.5))
    if not press:
        hi = QPainterPath(shape)
        painter.setClipPath(shape)
        painter.setPen(QPen(QColor(255, 255, 255, 30 if sk.dark else 200), 1.0))
        painter.drawPath(hi.translated(0, 1))
    painter.restore()


def paint_card(painter: QPainter, rect: QRectF, sk: Skin, *, radius: float = 18.0, lift: float = 0.0,
               fill: QColor | None = None, border: QColor | None = None) -> QPainterPath:
    """A card: surface, hairline border and a soft shadow that grows as it lifts (0-1)."""
    shape = QPainterPath()
    shape.addRoundedRect(rect, radius, radius)
    soft_shadow(painter, shape, dy=3 + 6 * lift, spread=8 + 10 * lift,
                color=rgba(sk.shadow, sk.shadow.alphaF() * (0.75 + 0.6 * lift)))
    painter.fillPath(shape, fill if fill is not None else (mix(sk.surface, sk.surface_hover, lift)))
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(border if border is not None else sk.border, 1.0))
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
    painter.restore()
    return shape


def paint_canvas(painter: QPainter, shape: QPainterPath, rect: QRectF, sk: Skin, *, dots: bool = True,
                 dpr: float = 1.0, glow: float = 1.0) -> None:
    """The panel's own surface: a soft vertical gradient, a faint dot grid and, at
    the top, a breath of the buddy's colour so it never reads as plain."""
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, sk.canvas_top)
    grad.setColorAt(1.0, sk.canvas_bottom)
    painter.fillPath(shape, QBrush(grad))
    if glow > 0:
        painter.save()
        painter.setClipPath(shape)
        tint = QRadialGradient(QPointF(rect.center().x(), rect.top() - rect.height() * 0.15), rect.width() * 0.62)
        tint.setColorAt(0.0, rgba(accent(), (0.2 if sk.dark else 0.14) * glow))
        tint.setColorAt(0.6, rgba(accent(), (0.05 if sk.dark else 0.03) * glow))
        tint.setColorAt(1.0, rgba(accent(), 0.0))
        painter.fillRect(rect, QBrush(tint))
        painter.restore()
    if dots:
        painter.fillPath(shape, dot_brush(sk.dots, dpr, rect.topLeft()))


_dot_cache: dict = {}


def dot_brush(color: QColor, dpr: float, origin: QPointF, step: float = 16.0) -> QBrush:
    from PyQt6.QtGui import QPixmap, QTransform
    key = (color.rgba(), round(dpr * 100), step)
    tile = _dot_cache.get(key)
    if tile is None:
        tile = QPixmap(round(step * dpr), round(step * dpr))
        tile.setDevicePixelRatio(dpr)
        tile.fill(QColor(0, 0, 0, 0))
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(step / 2, step / 2), 1.0, 1.0)
        p.end()
        _dot_cache[key] = tile
    brush = QBrush(tile)
    brush.setTransform(QTransform.fromTranslate(origin.x(), origin.y()))
    return brush


# --- Icon tiles and line icons ---------------------------------------------------------------------------

# Each Settings section's tile: (top, bottom) of its gel. "buddy" wears the buddy's colour.
TILES = {
    "general": ("#A3ABBD", "#5C6477"),
    "look": ("#FF8BD1", "#A54BFF"),
    "sounds": ("#FF8AA5", "#F0335E"),
    "voice": ("#B793FF", "#6A42F4"),
    "buddy": None,
    "shortcut": ("#63D3FF", "#1C86F0"),
    "model": ("#8A97FF", "#3B4BE6"),
    "privacy": ("#63E6A0", "#15A657"),
    "about": ("#5EE6D6", "#11A096"),
    # Cards that pick something (an AI, a voice engine).
    "claude": ("#FFB48C", "#E0613A"),
    "openai": ("#7EE3C2", "#12967A"),
    "gemini": ("#92B6FF", "#4466F0"),
    "ollama": ("#C9CED9", "#687084"),
    "cartesia": ("#B793FF", "#6A42F4"),
    "voicestudio": ("#FF8AA5", "#F0335E"),
    "kokoro": ("#63E6A0", "#15A657"),
}

GLYPHS = {"model": "brain", "claude": "brain", "openai": "brain", "gemini": "brain", "ollama": "monitor",
          "cartesia": "voice", "voicestudio": "mic", "kokoro": "monitor"}


def tile_colors(key: str) -> tuple[QColor, QColor]:
    pair = TILES.get(key)
    if pair is None:
        top, _mid, bottom = gel_colors(accent())
        return top, bottom
    return QColor(pair[0]), QColor(pair[1])


def paint_tile(painter: QPainter, rect: QRectF, key: str, glyph: str | None = None, *, press: float = 0.0) -> None:
    """A glossy app-icon tile in the section's colours with a white line glyph."""
    top, bottom = tile_colors(key)
    shape = squircle(rect, 0.3)
    painter.save()
    soft_shadow(painter, shape, dy=1.5, spread=2.5, color=rgba(bottom, 0.45))
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bottom)
    painter.fillPath(shape, QBrush(grad))
    painter.setClipPath(shape)
    hl = QRectF(rect.left(), rect.top(), rect.width(), rect.height() * 0.5)
    hg = QLinearGradient(hl.topLeft(), hl.bottomLeft())
    hg.setColorAt(0.0, QColor(255, 255, 255, round(90 * (1 - press))))
    hg.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(hl, QBrush(hg))
    painter.setClipping(False)
    painter.setPen(QPen(rgba(mix(bottom, QColor(0, 0, 0), 0.2), 0.5), 0.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(shape)
    pad = rect.width() * 0.2
    paint_icon(painter, glyph or GLYPHS.get(key, key), rect.adjusted(pad, pad, -pad, -pad), QColor(255, 255, 255), 2.1)
    painter.restore()


def icon_path(kind: str, rect: QRectF) -> tuple[QPainterPath, QPainterPath]:
    """A 24-unit line icon fitted to ``rect``: (strokes, solid fills)."""
    s = rect.width() / 24.0
    path, solid = QPainterPath(), QPainterPath()

    def pt(x: float, y: float) -> QPointF:
        return QPointF(rect.left() + x * s, rect.top() + y * s)

    def line(*pts) -> None:
        path.moveTo(pt(*pts[0]))
        for p in pts[1:]:
            path.lineTo(pt(*p))

    def circle(x: float, y: float, r: float, fill: bool = False) -> None:
        (solid if fill else path).addEllipse(pt(x, y), r * s, r * s)

    if kind == "general":                     # a cog
        teeth = 8
        cog = QPainterPath()
        for i in range(teeth * 4):
            a = i / (teeth * 4) * 2 * math.pi - math.pi / 2
            r = 9.4 if (i % 4) in (1, 2) else 7.2
            q = pt(12 + r * math.cos(a), 12 + r * math.sin(a))
            if i == 0:
                cog.moveTo(q)
            else:
                cog.lineTo(q)
        cog.closeSubpath()
        path.addPath(cog)
        circle(12, 12, 3.0)
    elif kind == "look":                      # half sun, half moon
        circle(12, 12, 7.5)
        half = QPainterPath()
        half.moveTo(pt(12, 4.5))
        half.arcTo(QRectF(pt(4.5, 4.5), pt(19.5, 19.5)), 90, -180)
        half.closeSubpath()
        solid.addPath(half)
    elif kind == "sounds":                    # a speaker and two waves
        line((4, 9.5), (7.5, 9.5), (12, 5.5), (12, 18.5), (7.5, 14.5), (4, 14.5))
        path.closeSubpath()
        path.moveTo(pt(15.5, 9))
        path.quadTo(pt(17.5, 12), pt(15.5, 15))
        path.moveTo(pt(18.2, 6.5))
        path.quadTo(pt(22, 12), pt(18.2, 17.5))
    elif kind == "voice":                     # a waveform
        for x, h in ((5, 3), (8.5, 7), (12, 11), (15.5, 7), (19, 3)):
            line((x, 12 - h / 2), (x, 12 + h / 2))
    elif kind == "buddy":                     # a happy face
        circle(12, 12, 8)
        circle(9.2, 10.4, 1.25, True)
        circle(14.8, 10.4, 1.25, True)
        path.moveTo(pt(8.6, 14.2))
        path.quadTo(pt(12, 17.6), pt(15.4, 14.2))
    elif kind == "shortcut":                  # a keyboard
        path.addRoundedRect(QRectF(pt(3, 6.5), pt(21, 17.5)), 2.4 * s, 2.4 * s)
        for x in (7, 10.3, 13.7, 17):
            circle(x, 10, 0.95, True)
        line((8, 14), (16, 14))
    elif kind == "brain":                     # a sparkle
        star = QPainterPath(pt(12, 3.5))
        star.quadTo(pt(13, 11), pt(20.5, 12))
        star.quadTo(pt(13, 13), pt(12, 20.5))
        star.quadTo(pt(11, 13), pt(3.5, 12))
        star.quadTo(pt(11, 11), pt(12, 3.5))
        path.addPath(star)
        circle(18.6, 5.6, 1.15, True)
    elif kind == "privacy":                   # a padlock
        path.addRoundedRect(QRectF(pt(5.5, 10.5), pt(18.5, 20)), 2.4 * s, 2.4 * s)
        path.moveTo(pt(8.5, 10.5))
        path.lineTo(pt(8.5, 8))
        path.cubicTo(pt(8.5, 3.6), pt(15.5, 3.6), pt(15.5, 8))
        path.lineTo(pt(15.5, 10.5))
        circle(12, 15, 1.3, True)
    elif kind == "about":                     # info
        circle(12, 12, 8)
        line((12, 11), (12, 16.2))
        circle(12, 7.9, 1.2, True)
    elif kind == "pencil":
        line((4, 20), (8, 20), (19, 9), (15, 5), (4, 16))
        path.closeSubpath()
        line((13.5, 6.5), (17.5, 10.5))
    elif kind == "sliders":
        for a, b in (((4, 7), (14, 7)), ((18, 7), (20, 7)), ((4, 17), (8, 17)), ((12, 17), (20, 17))):
            line(a, b)
        circle(16, 7, 2)
        circle(10, 17, 2)
    elif kind == "arrow":
        line((5, 12), (19, 12))
        line((13, 6), (19, 12), (13, 18))
    elif kind == "up":
        line((12, 19), (12, 5))
        line((6, 11), (12, 5), (18, 11))
    elif kind == "check":
        line((5, 12.5), (10, 17), (19, 7))
    elif kind == "back":
        line((15, 5), (8, 12), (15, 19))
    elif kind == "play":
        tri = QPainterPath(pt(8, 5.5))
        tri.lineTo(pt(18.5, 12))
        tri.lineTo(pt(8, 18.5))
        tri.closeSubpath()
        solid.addPath(tri)
    elif kind == "folder":
        line((3.5, 7), (3.5, 18.5), (20.5, 18.5), (20.5, 9), (11.5, 9), (9.5, 6), (3.5, 6), (3.5, 7))
    elif kind == "memory":                    # a bookmark-ish notebook
        path.addRoundedRect(QRectF(pt(5, 3.5), pt(19, 20.5)), 2.2 * s, 2.2 * s)
        line((9, 3.5), (9, 20.5))
        line((12, 8.5), (16, 8.5))
        line((12, 12), (16, 12))
    elif kind == "mic":
        path.addRoundedRect(QRectF(pt(9, 3.5), pt(15, 14)), 3 * s, 3 * s)
        path.moveTo(pt(6, 11))
        path.cubicTo(pt(6, 19), pt(18, 19), pt(18, 11))
        line((12, 17.5), (12, 20.5))
    elif kind == "sun":
        circle(12, 12, 4)
        for i in range(8):
            a = i * math.pi / 4
            line((12 + 6.6 * math.cos(a), 12 + 6.6 * math.sin(a)), (12 + 8.8 * math.cos(a), 12 + 8.8 * math.sin(a)))
    elif kind == "moon":
        moon = QPainterPath()
        moon.moveTo(pt(15.5, 4.5))
        moon.cubicTo(pt(8, 4.5), pt(4.5, 9), pt(4.5, 12.5))
        moon.cubicTo(pt(4.5, 17), pt(8.5, 20), pt(12.5, 20))
        moon.cubicTo(pt(16, 20), pt(18.5, 18), pt(19.5, 15.5))
        moon.cubicTo(pt(13.5, 17), pt(9.5, 11), pt(15.5, 4.5))
        path.addPath(moon)
    elif kind == "monitor":
        path.addRoundedRect(QRectF(pt(3.5, 4.5), pt(20.5, 16)), 2 * s, 2 * s)
        line((9, 20), (15, 20))
        line((12, 16), (12, 20))
    elif kind == "close":
        line((6.5, 6.5), (17.5, 17.5))
        line((17.5, 6.5), (6.5, 17.5))
    return path, solid


def paint_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor, width: float = 2.0) -> None:
    strokes, solid = icon_path(kind, rect)
    painter.save()
    pen = QPen(color, width * rect.width() / 24.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(strokes)
    if not solid.isEmpty():
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(solid)
    painter.restore()


# --- Controls -------------------------------------------------------------------------------------------


class Tactile(Motion):
    """Hover and press springs plus their sounds, for a QAbstractButton subclass.
    Call ``_tactile_init()`` in __init__."""

    PRESS_SOUND = "press"

    def _tactile_init(self) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.spring("hover", 0.0, 520.0, 34.0)
        self.spring("press", 0.0, 900.0, 38.0)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled():
            self.go("hover", 1.0)
            sound("hover")
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.go("hover", 0.0)
        self.go("press", 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.go("press", 1.0)
            if self.PRESS_SOUND:
                sound(self.PRESS_SOUND)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.go("press", 0.0, kick=-6.0)
        super().mouseReleaseEvent(event)

    def squish(self, painter: QPainter, rect: QRectF, amount: float = 0.05) -> None:
        """Shrink about the centre as it's pressed (and grow a hair on hover)."""
        k = 1.0 - amount * self.val("press") + 0.012 * self.val("hover")
        c = rect.center()
        painter.translate(c)
        painter.scale(k, k)
        painter.translate(-c)


class Toggle(Tactile, QAbstractButton):
    """A switch: a glossy track in the buddy's colour when on, a knob that
    springs across (stretching as you press it), and a little click."""

    PRESS_SOUND = ""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(50, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tactile_init()
        self.spring("on", 1.0 if checked else 0.0, 520.0, 26.0)
        self.toggled.connect(self._moved)

    def _moved(self, on: bool) -> None:
        self.go("on", 1.0 if on else 0.0)
        if self.underMouse():
            sound("toggle_on" if on else "toggle_off")

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        if "_springs" in self.__dict__ and not self.underMouse():
            self.go("on", 1.0 if on else 0.0, snap=True)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        rect = QRectF(self.rect()).adjusted(1, 2, -1, -2)
        on = max(0.0, min(1.0, self.val("on")))
        track = pill(rect)
        p.fillPath(track, sk.track)
        p.save()                                  # an inset shadow along the top of the empty track
        p.setClipPath(track)
        g = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(0, 6))
        g.setColorAt(0.0, QColor(0, 0, 0, 60 if sk.dark else 26))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 6), QBrush(g))
        p.restore()
        if on > 0.01:
            p.setOpacity(on)
            paint_gel(p, rect, accent(), hover=self.val("hover") * 0.6)
            p.setOpacity(1.0)
        press = self.val("press")
        d = rect.height() - 6
        stretch = 6.0 * press
        travel = rect.width() - 6 - d - stretch
        x = rect.left() + 3 + travel * self.val("on")
        knob = QRectF(x, rect.top() + 3, d + stretch, d)
        soft_shadow(p, pill(knob), dy=1.5, spread=2.5, color=QColor(0, 0, 0, 70))
        kg = QLinearGradient(knob.topLeft(), knob.bottomLeft())
        kg.setColorAt(0.0, QColor("#FFFFFF"))
        kg.setColorAt(1.0, QColor("#ECEDF2"))
        p.fillPath(pill(knob), QBrush(kg))
        p.end()


class Segmented(Motion, QWidget):
    """Options in an inset track with a raised thumb that slides to your pick."""

    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, parent: QWidget | None = None, *,
                 px: float = 13) -> None:
        super().__init__(parent)
        self.options = options
        self.value = value
        self._font = font(px, 700)
        fm = QFontMetricsF(font(px, 800))
        self._widths = [fm.horizontalAdvance(label) + 30 for _v, label in options]
        self.setFixedSize(round(sum(self._widths) + 8), round(fm.height()) + 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = -1
        self.spring("at", float(self._index(value)), 480.0, 30.0)

    def _index(self, value: str) -> int:
        for i, (v, _l) in enumerate(self.options):
            if v == value:
                return i
        return 0

    def set_value(self, value: str) -> None:
        self.value = value
        self.go("at", float(self._index(value)))

    def _rects(self) -> list[QRectF]:
        x, out = 4.0, []
        for w in self._widths:
            out.append(QRectF(x, 4, w, self.height() - 8))
            x += w
        return out

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hit = next((i for i, r in enumerate(self._rects()) if r.contains(event.position())), -1)
        if hit != self._hover:
            self._hover = hit
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        for (value, _label), rect in zip(self.options, self._rects()):
            if rect.contains(event.position()) and value != self.value:
                self.set_value(value)
                sound("select")
                self.changed.emit(value)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(pill(track, 13), sk.track if not sk.dark else sk.field)
        p.setPen(QPen(sk.border, 1.0))
        p.drawPath(pill(track, 13))
        rects = self._rects()
        at = max(0.0, min(len(rects) - 1.0, self.val("at")))
        i0 = int(math.floor(at))
        i1 = min(len(rects) - 1, i0 + 1)
        f = at - i0
        a, b = rects[i0], rects[i1]
        thumb = QRectF(a.left() + (b.left() - a.left()) * f, a.top(), a.width() + (b.width() - a.width()) * f,
                       a.height())
        paint_raised(p, thumb, sk, radius=10)
        for i, ((_value, label), rect) in enumerate(zip(self.options, rects)):
            near = max(0.0, 1.0 - abs(at - i))
            p.setFont(self._font)
            colour = mix(sk.text2, sk.text, near)
            if i == self._hover and near < 0.5:
                colour = mix(colour, sk.text, 0.5)
            p.setPen(colour)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class Slider(Motion, QWidget):
    """A volume slider (0-100): a gel fill, a knob that swells under the mouse,
    a soft tick every 10 while you drag. ``released`` fires when you let go."""

    changed = pyqtSignal(int)
    released = pyqtSignal(int)

    def __init__(self, value: int = 70, parent: QWidget | None = None, *, width: int = 220) -> None:
        super().__init__(parent)
        self.value = max(0, min(100, int(value)))
        self.setFixedSize(width, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._drag = False
        self.spring("hover", 0.0, 520.0, 34.0)
        self.spring("pos", self.value / 100.0, 700.0, 40.0)

    def _track(self) -> QRectF:
        return QRectF(12, self.height() / 2 - 3, self.width() - 24, 6)

    def _set_from(self, x: float) -> None:
        t = self._track()
        v = round(max(0.0, min(1.0, (x - t.left()) / t.width())) * 100)
        if v != self.value:
            if v // 10 != self.value // 10:
                sound("type")
            self.value = v
            self.go("pos", v / 100.0)
            self.changed.emit(v)

    def enterEvent(self, _event) -> None:  # noqa: N802
        self.go("hover", 1.0)

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if not self._drag:
            self.go("hover", 0.0)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._drag = True
        self._set_from(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag:
            self._set_from(event.position().x())

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag = False
        if not self.underMouse():
            self.go("hover", 0.0)
        self.released.emit(self.value)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        t = self._track()
        p.fillPath(pill(t), sk.track)
        k = max(0.0, min(1.0, self.val("pos")))
        fill = QRectF(t.left(), t.top(), max(t.height(), t.width() * k), t.height())
        paint_gel(p, fill, accent())
        h = self.val("hover")
        r = 10 + 2 * h
        c = QPointF(t.left() + t.width() * k, t.center().y())
        knob = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        soft_shadow(p, pill(knob), dy=1.5, spread=3, color=QColor(0, 0, 0, 70))
        kg = QLinearGradient(knob.topLeft(), knob.bottomLeft())
        kg.setColorAt(0.0, QColor("#FFFFFF"))
        kg.setColorAt(1.0, QColor("#ECEDF2"))
        p.fillPath(pill(knob), QBrush(kg))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rgba(accent(), 0.25 + 0.75 * h))
        p.drawEllipse(c, 3.2 * h, 3.2 * h)
        p.end()


class NavRail(Motion, QWidget):
    """The Settings sections on the left: a coloured tile and a name each, a
    highlight that slides to the one you pick, and a soft hover."""

    picked = pyqtSignal(str)
    ROW = 42.0

    def __init__(self, sections: list[tuple[str, str]], parent: QWidget | None = None, *, width: int = 196) -> None:
        super().__init__(parent)
        self.sections = sections
        self.current = sections[0][0] if sections else ""
        self._hover = -1
        self.setFixedSize(width, round(self.ROW * len(sections)))
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.spring("at", 0.0, 460.0, 32.0)
        self.spring("hover", -1.0, 700.0, 40.0)

    def index(self, key: str) -> int:
        return next((i for i, (k, _l) in enumerate(self.sections) if k == key), 0)

    def select(self, key: str, *, animate: bool = True) -> None:
        self.current = key
        self.go("at", float(self.index(key)), snap=not animate)

    def _row_at(self, y: float) -> int:
        i = int(y // self.ROW)
        return i if 0 <= i < len(self.sections) else -1

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        i = self._row_at(event.position().y())
        if i != self._hover:
            self._hover = i
            if i >= 0:
                self.go("hover", float(i), snap=self.val("hover") < 0)
                sound("hover", 0.7)
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        i = self._row_at(event.position().y())
        if i >= 0 and self.sections[i][0] != self.current:
            sound("page")
            self.select(self.sections[i][0])
            self.picked.emit(self.current)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        sk = skin()
        w = float(self.width())
        if self._hover >= 0 and self.sections[self._hover][0] != self.current:
            y = self.val("hover") * self.ROW
            p.fillPath(pill(QRectF(0, y + 2, w, self.ROW - 4), 12), rgba(sk.text, 0.05 if not sk.dark else 0.06))
        y = self.val("at") * self.ROW
        paint_raised(p, QRectF(0, y + 2, w, self.ROW - 4), sk, radius=12)
        at = self.val("at")
        for i, (key, label) in enumerate(self.sections):
            row = QRectF(0, i * self.ROW, w, self.ROW)
            tile = QRectF(row.left() + 9, row.center().y() - 13, 26, 26)
            near = max(0.0, 1.0 - abs(at - i))
            p.save()
            if near > 0.01:                       # the chosen tile pops a little
                c = tile.center()
                p.translate(c)
                p.scale(1 + 0.06 * near, 1 + 0.06 * near)
                p.translate(-c)
            paint_tile(p, tile, key)
            p.restore()
            p.setFont(font(14, 800 if key == self.current else 600))
            p.setPen(mix(sk.text2, sk.text, max(near, 0.6 if i == self._hover else 0.0)))
            p.drawText(QRectF(tile.right() + 12, row.top(), w - tile.right() - 16, row.height()),
                       Qt.AlignmentFlag.AlignVCenter, label)
        p.end()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()
