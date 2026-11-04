"""The Clixy panel: it grows out of the notch.

Click the notch (island.py) and the panel drops down from the top of the
screen, its shoulders flaring into the screen edge like the notch's own. It
leaves the notch black and blooms into the skin's colours as it grows
(ui_kit: bright Daylight by default, or deep Midnight), with a breath of the
buddy's colour at the top. Everything in it is tactile: buttons sink and
spring back, the front note tilts toward the mouse, the cards behind lift,
and each move has its little sound (sfx.py, through ui_kit.sound):

- Home: a hello with your real shortcut as keycaps; your newest sketch note on
  warm paper (the two before it tucked behind, tilted), with Open note and Ask
  a follow-up; All notes, What Clixy remembers, and a box to type a question.
  A 3D note shows its real model, live on the GPU (render3d), rocking gently:
  drag it to turn it. Open note brings the note back as the pad it first
  appeared on (sketch.NoteCard), drawing itself again, not a flat picture.
- Working: while a guide runs (guide.py), what you asked, the steps done, the
  step now in your buddy's colour, I did it and Stop.
- Settings: the gear turns the panel into Settings (panel_settings.py), all
  in one place, saved through settings_dialog.SettingsDialog's own logic.

Click outside it or press Esc and it tucks back into the notch. It never
shows the words of Clixy's spoken answers: those are only ever heard.

The accent everywhere is your buddy's colour (theme.current_palette().flood).

Threading: Qt main thread only. A guide runs on a worker thread; the panel
reads its progress (guide.current().progress()) and calls confirm()/stop(),
which are thread-safe.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
    QTransform,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QLineEdit,
    QPushButton,
    QWidget,
)

import theme
import ui_kit
from island import paint_light, tab_path
from ui_kit import Motion, Tactile, skin, sound

# --- Colours and sizes -------------------------------------------------------------------------------
# The panel's own colours come from the skin (ui_kit.skin()); these are the note paper's.

PAPER = QColor("#FBF8F2")
PAPER_BAND = QColor("#F2EDE4")
PAPER_DOT = QColor("#DCD3C3")
INK = QColor("#1D1B18")
BAND_TEXT = QColor("#6B655B")
NOTE_META = QColor("#7A7366")
PAPER_MUTED = QColor("#8A8274")
PAPER_RING = QColor("#CFC6B5")

NOTCH = (150.0, 24.0)          # where it grows from (island.SideIsland.NOTCH)
HOME = (880.0, 650.0)
WORK_W = 720.0
SHOULDER = 22.0
PAD_X, PAD_B = 84, 96          # room around the panel for its shadow
MAX_H = 720.0
_STIFF, _DAMP = 0.2, 0.66      # per 60 Hz frame: quick, a touch of overshoot


def _rgba(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    k = max(0.0, min(1.0, k))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * k, a.greenF() + (b.greenF() - a.greenF()) * k,
                           a.blueF() + (b.blueF() - a.blueF()) * k, a.alphaF() + (b.alphaF() - a.alphaF()) * k)


_WEIGHTS = {400: QFont.Weight.Normal, 500: QFont.Weight.Medium, 600: QFont.Weight.DemiBold,
            700: QFont.Weight.Bold, 800: QFont.Weight.ExtraBold}


def font(px: float, weight: int = 600, tracking_em: float = 0.0) -> QFont:
    """Manrope at ``px`` CSS pixels (the mockups' sizes)."""
    f = theme.ui_font(10, _WEIGHTS.get(weight, QFont.Weight.DemiBold))
    f.setPixelSize(max(1, round(px)))
    if tracking_em:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking_em * px)
    return f


def mono(px: float) -> QFont:
    """JetBrains Mono, for keycaps."""
    f = theme.mono_font(10, QFont.Weight.Medium)
    f.setPixelSize(max(1, round(px)))
    return f


def accent() -> QColor:
    """The buddy's colour."""
    return QColor(theme.current_palette().flood)


def gloss(color: QColor) -> tuple[QColor, QColor, QColor]:
    """A glossy accent button's gradient: lighter at the top, the colour, a
    little deeper at the bottom (Ember: #FFB98C, the orange, #E0571F)."""
    top = _mix(color, QColor(255, 255, 255), 0.47)
    h, s, l, _a = color.getHslF()
    bottom = QColor.fromHslF(max(0.0, (h if h >= 0 else 0.0) - 0.01) % 1.0, min(1.0, s * 1.02), l * 0.84)
    return top, QColor(color), bottom


def on_accent(color: QColor) -> QColor:
    """Text on the accent's gel: white on a deep colour, near-black on a pale one."""
    return ui_kit.ink_on(color)


# --- Small painting helpers -----------------------------------------------------------------------

def _pill(rect: QRectF, radius: float | None = None) -> QPainterPath:
    path = QPainterPath()
    r = rect.height() / 2 if radius is None else radius
    path.addRoundedRect(rect, r, r)
    return path


def _inset_top(painter: QPainter, shape: QPainterPath, color: QColor, dy: float = 1.0) -> None:
    """CSS ``inset 0 1px 0 color``: a hairline of light along the inside top."""
    painter.fillPath(shape.subtracted(shape.translated(0, dy)), color)


def paint_glossy(painter: QPainter, rect: QRectF, color: QColor, *, radius: float | None = None,
                 hover: float = 0.0, down: float = 0.0) -> None:
    """The accent's candy gel (ui_kit.paint_gel)."""
    ui_kit.paint_gel(painter, rect, color, radius=radius, hover=float(hover), press=float(down))


def paint_frost(painter: QPainter, rect: QRectF, *, radius: float | None = None, hover: float = 0.0,
                down: float = 0.0) -> None:
    """A soft raised pill in the skin's colours (the quieter buttons)."""
    ui_kit.paint_raised(painter, rect, skin(), radius=radius, hover=float(hover), press=float(down))


def keycap_size(label: str, px: float = 12.0, pad=(8.0, 3.0, 4.0)) -> QSize:
    fm = QFontMetricsF(mono(px))
    w = fm.horizontalAdvance(label) + 2 * pad[0]
    h = fm.height() + pad[1] + pad[2]
    return QSize(round(max(w, h)), round(h))


def paint_keycap(painter: QPainter, rect: QRectF, label: str, *, px: float = 12.0, light: bool = False,
                 radius: float = 7.0, edge: float = 2.0, press: float = 0.0) -> None:
    """A tactile keycap in the skin's colours (or paper-light on a note), with a
    bottom edge it sinks into as it's ``press``ed (0-1)."""
    painter.save()
    sink = edge * 0.8 * press
    rect = rect.translated(0, sink)
    edge -= sink
    shape = _pill(rect, radius)
    sk = skin()
    if not light:
        painter.fillPath(shape.translated(0, edge), sk.key_edge)
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, sk.key_top)
        grad.setColorAt(1.0, sk.key_bottom)
        painter.fillPath(shape, QBrush(grad))
        painter.setPen(QPen(sk.border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_pill(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5))
        _inset_top(painter, shape, QColor(255, 255, 255, 40 if sk.dark else 230))
        painter.setPen(sk.key_text)
    elif light:
        painter.fillPath(shape.translated(0, edge), QColor("#CFC6B5"))
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, QColor("#FFFFFF"))
        grad.setColorAt(1.0, QColor("#EFEAE1"))
        painter.fillPath(shape, QBrush(grad))
        painter.setPen(QPen(QColor("#D9D0BF"), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_pill(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5))
        painter.setPen(INK)
    painter.setFont(mono(px))
    painter.drawText(rect.adjusted(0, -0.5, 0, -0.5), Qt.AlignmentFlag.AlignCenter, label)
    painter.restore()


def soft_shadow(path: QPainterPath, blur: float, color: QColor, scale: float = 0.25) -> tuple[QImage, QRectF]:
    """CSS ``box-shadow``'s soft blur of ``path``: an image and where to draw it.
    Rendered small and blurred with three box passes (close to a Gaussian)."""
    sigma = blur / 2.0
    pad = sigma * 3.0
    bounds = path.boundingRect().adjusted(-pad, -pad, pad, pad)
    w, h = max(1, int(bounds.width() * scale) + 2), max(1, int(bounds.height() * scale) + 2)
    mask = QImage(w, h, QImage.Format.Format_Alpha8)
    mask.fill(0)
    p = QPainter(mask)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(scale, scale)
    p.translate(-bounds.left(), -bounds.top())
    p.fillPath(path, QColor(0, 0, 0, 255))
    p.end()
    try:
        import numpy as np
        bits = mask.constBits()
        bits.setsize(mask.sizeInBytes())
        a = np.frombuffer(bits, np.uint8).reshape(h, mask.bytesPerLine())[:, :w].astype(np.float32)
        r = max(1, round(sigma * scale * 1.15))
        for _ in range(3):
            a = _box(_box(a, r, 0), r, 1)
        alpha = np.clip(a * color.alphaF(), 0, 255).astype(np.uint32)
        argb = ((alpha << 24) | ((alpha * color.red() // 255) << 16) | ((alpha * color.green() // 255) << 8)
                | (alpha * color.blue() // 255)).astype(np.uint32)
        out = QImage(argb.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    except Exception:  # no numpy: a cheaper blur by scaling
        smooth = Qt.TransformationMode.SmoothTransformation
        small = mask.scaled(max(1, w // 4), max(1, h // 4), Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
        out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(0)
        q = QPainter(out)
        q.drawImage(QRectF(0, 0, w, h), small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, smooth))
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        q.fillRect(QRectF(0, 0, w, h), color)
        q.end()
    return out, QRectF(bounds.left(), bounds.top(), w / scale, h / scale)


def _box(a, r: int, axis: int):
    """A box blur of radius ``r`` along ``axis`` (edges padded with zeros)."""
    import numpy as np
    n = a.shape[axis]
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad), axis=axis)
    hi = np.take(c, range(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, range(0, n), axis=axis)
    return (hi - lo) / (2 * r + 1)


_shadow_cache: dict = {}


def draw_shadow(painter: QPainter, path: QPainterPath, *, dy: float, blur: float, color: QColor,
                key=None) -> None:
    """Paint a soft shadow of ``path`` (cached when ``key`` is given)."""
    cached = _shadow_cache.get(key) if key is not None else None
    if cached is None:
        cached = soft_shadow(path, blur, color)
        if key is not None:
            if len(_shadow_cache) > 24:
                _shadow_cache.clear()
            _shadow_cache[key] = cached
    image, where = cached
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawImage(where.translated(0, dy), image)
    painter.restore()


def _wrap(text: str, fm: QFontMetricsF, width: float, max_lines: int) -> list[str]:
    lines: list[str] = []
    words = text.split()
    line = ""
    for i, word in enumerate(words):
        trial = f"{line} {word}" if line else word
        if line and fm.horizontalAdvance(trial) > width:
            lines.append(line)
            line = word
            if len(lines) == max_lines - 1:
                line = " ".join(words[i:])
                break
        else:
            line = trial
    if line:
        lines.append(fm.elidedText(line, Qt.TextElideMode.ElideRight, width))
    return lines


def _icon_path(kind: str, rect: QRectF) -> QPainterPath:
    """The mockups' 24-unit line icons, fitted to ``rect``."""
    s = rect.width() / 24.0
    path = QPainterPath()

    def pt(x, y):
        return QPointF(rect.left() + x * s, rect.top() + y * s)

    if kind == "pencil":           # M4 20h4L19 9l-4-4L4 16z  m13.5 6.5 4 4
        path.moveTo(pt(4, 20)); path.lineTo(pt(8, 20)); path.lineTo(pt(19, 9)); path.lineTo(pt(15, 5))
        path.lineTo(pt(4, 16)); path.closeSubpath()
        path.moveTo(pt(13.5, 6.5)); path.lineTo(pt(17.5, 10.5))
    elif kind == "sliders":        # M4 7h10M18 7h2M4 17h4M12 17h8 + circles
        for a, b in (((4, 7), (14, 7)), ((18, 7), (20, 7)), ((4, 17), (8, 17)), ((12, 17), (20, 17))):
            path.moveTo(pt(*a)); path.lineTo(pt(*b))
        path.addEllipse(pt(16, 7), 2 * s, 2 * s)
        path.addEllipse(pt(10, 17), 2 * s, 2 * s)
    elif kind == "arrow":          # M5 12h14M13 6l6 6-6 6
        path.moveTo(pt(5, 12)); path.lineTo(pt(19, 12))
        path.moveTo(pt(13, 6)); path.lineTo(pt(19, 12)); path.lineTo(pt(13, 18))
    elif kind == "check":          # m5 12 5 5 9-10
        path.moveTo(pt(5, 12)); path.lineTo(pt(10, 17)); path.lineTo(pt(19, 7))
    elif kind == "back":           # m15 5-7 7 7 7
        path.moveTo(pt(15, 5)); path.lineTo(pt(8, 12)); path.lineTo(pt(15, 19))
    return path


def paint_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor, width: float) -> None:
    painter.save()
    pen = QPen(color, width * rect.width() / 24.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_icon_path(kind, rect))
    painter.restore()


# --- Buttons ---------------------------------------------------------------------------------------

class PanelButton(Tactile, QPushButton):
    """A tactile button: ``glossy`` (the accent's candy gel), ``frost`` (a soft
    raised pill in the skin's colours), ``paper`` (on a note card), ``text``
    (quiet), or a round icon button (``icon`` with ``round_``). It grows a hair
    under the mouse, sinks when pressed and springs back, with a sound each."""

    def __init__(self, text: str, kind: str = "frost", parent: QWidget | None = None, *, px: float = 14,
                 weight: int = 700, pad: tuple[float, float] = (10, 16), icon: str = "", round_: float = 0,
                 icon_px: float = 17, icon_stroke: float = 1.9, border: bool = False) -> None:
        super().__init__(text, parent)
        self.kind = kind
        self._font = font(px, weight)
        self._pad = pad
        self._icon = icon
        self._round = round_
        self._icon_px = icon_px
        self._icon_stroke = icon_stroke
        self._border = border
        self.setFont(self._font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tactile_init()
        if text:
            self.setAccessibleName(text)
        self._shadow: QGraphicsDropShadowEffect | None = None
        if kind == "glossy":
            self._shadow = QGraphicsDropShadowEffect(self)
            self._shadow.setOffset(0, 6 if not round_ or round_ > 34 else 4)
            self._shadow.setBlurRadius(22)
            self.setGraphicsEffect(self._shadow)
        self.recolor()
        self.resize(self.sizeHint())

    def recolor(self) -> None:
        if self._shadow is not None:
            self._shadow.setColor(_rgba(accent(), 0.45))
        self.update()

    def _motion_step(self, dt: float) -> bool:
        moving = super()._motion_step(dt)
        if self._shadow is not None:       # the glow follows the press: it sinks onto the page
            k = self.val("press")
            base = 6 if not self._round or self._round > 34 else 4
            self._shadow.setOffset(0, base * (1 - 0.6 * k) + 2 * self.val("hover") * (1 - k))
            self._shadow.setBlurRadius(22 - 8 * k + 6 * self.val("hover"))
        return moving

    def sizeHint(self) -> QSize:  # noqa: N802
        if self._round:
            return QSize(round(self._round), round(self._round))
        fm = QFontMetricsF(self._font)
        border = 2 if self._border or self.kind in ("glossy", "paper", "frost") else 0
        icon = self._icon_px + 8 if self._icon and self.text() else 0
        return QSize(round(fm.horizontalAdvance(self.text()) + icon + 2 * self._pad[1] + border),
                     round(fm.height() + 2 * self._pad[0] + border))

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect())
        if self.kind == "frost" and not self._round:
            rect = rect.adjusted(1, 1, -1, -2)       # room for its shadow
        hover = self.val("hover") if self.isEnabled() else 0.0
        down = self.val("press")
        self.squish(p, rect, 0.06 if self._round else 0.045)
        acc = accent()
        sk = skin()
        if not self.isEnabled():
            p.setOpacity(0.5)
        if self.kind == "glossy":
            paint_glossy(p, rect, acc, hover=hover, down=down)
            fg = on_accent(acc)
        elif self.kind == "paper":
            shape = _pill(rect)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, _mix(QColor("#FFFFFF"), QColor("#F3EEE5"), down))
            grad.setColorAt(1.0, _mix(QColor("#F3EEE5"), QColor("#EAE2D4"), hover))
            p.fillPath(shape, QBrush(grad))
            p.setPen(QPen(_mix(QColor("#DCD3C3"), acc, 0.5 * hover), 1.0))
            p.drawPath(_pill(rect.adjusted(0.5, 0.5, -0.5, -0.5)))
            fg = INK
        elif self.kind == "text":
            fg = _mix(sk.text2, sk.text, hover)
        else:
            paint_frost(p, rect if not self._round else rect.adjusted(1, 1, -1, -2), hover=hover, down=down)
            fg = _mix(sk.text2 if self._icon and not self.text() else sk.text, sk.text, hover)
        if self._icon and self.text():          # an icon, then the words, centred together
            size = self._icon_px
            fm = QFontMetricsF(self._font)
            total = size + 8 + fm.horizontalAdvance(self.text())
            x = rect.center().x() - total / 2
            ui_kit.paint_icon(p, self._icon, QRectF(x, rect.center().y() - size / 2, size, size),
                              _mix(fg, accent(), 0.85), self._icon_stroke)
            rect = QRectF(x + size + 8, rect.top(), total - size - 8 + 2, rect.height())
        elif self._icon:
            size = self._icon_px
            box = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size)
            ui_kit.paint_icon(p, self._icon, box, fg, self._icon_stroke)
        if self.text():
            p.setFont(self._font)
            if self.kind == "glossy" and fg.lightness() > 128:   # a soft engraved shadow under white text
                p.setPen(QColor(0, 0, 0, 50))
                p.drawText(rect.translated(0, 1), Qt.AlignmentFlag.AlignCenter, self.text())
            p.setPen(fg)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class _Edit(QLineEdit):
    """The ask box's line: it tells its field when it gains or loses the cursor."""

    focus_changed = pyqtSignal(bool)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.focus_changed.emit(False)


class AskField(Motion, QWidget):
    """The box to type a question: a white (or ink) well that lights a ring in
    the buddy's colour when you type, soft key ticks, and a gel send button
    that pops forward once there's something to send."""

    submitted = pyqtSignal(str)
    PLACEHOLDER = "Type a question"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.edit = _Edit(self)
        self.edit.setFrame(False)
        self.edit.setFont(font(14, 600))
        self.edit.setPlaceholderText(self.PLACEHOLDER)
        self.edit.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.restyle()
        self.edit.returnPressed.connect(self._submit)
        self.edit.textEdited.connect(self._typed)
        self.edit.focus_changed.connect(lambda on: self.go("focus", 1.0 if on else 0.0))
        self.send = PanelButton("", "glossy", self, icon="up", round_=34, icon_px=16, icon_stroke=2.6)
        self.send.setAccessibleName("Ask")
        self.send.clicked.connect(self._submit)
        self.spring("focus", 0.0, 420.0, 30.0)
        self.resize(330, 46)

    def restyle(self) -> None:
        """Colours from the skin (and again whenever the skin changes)."""
        sk = skin()
        pal = self.edit.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.Text, sk.text)
        pal.setColor(QPalette.ColorRole.PlaceholderText, sk.text3)
        pal.setColor(QPalette.ColorRole.Highlight, _rgba(accent(), 0.35))
        pal.setColor(QPalette.ColorRole.HighlightedText, sk.text)
        self.edit.setPalette(pal)
        # Spelled out in full: the app's stylesheet paints every QLineEdit dark on white.
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: 0; padding: 0; margin: 0; min-height: 0;"
            f" color: {sk.text.name()}; selection-color: {sk.text.name()};"
            f" selection-background-color: {_rgba(accent(), 0.35).name(QColor.NameFormat.HexArgb)}; }}")
        self.update()

    def _typed(self, text: str) -> None:
        sound("type")
        if text.strip() and len(text.strip()) == 1:     # the first letter: the send button hops
            self.send.go("press", 0.0, kick=-9.0)

    def set_extra(self, widget: QWidget) -> None:
        """A small control inside the bar, just before the send button."""
        widget.setParent(self)
        widget.resize(widget.sizeHint())
        widget.show()
        self._extra = widget
        self.resizeEvent(None)

    def resizeEvent(self, _event) -> None:  # noqa: N802
        h = self.height()
        self.send.setGeometry(self.width() - 6 - 34, (h - 34) // 2, 34, 34)
        right = self.width() - 6 - 34 - 8
        extra = getattr(self, "_extra", None)
        if extra is not None:
            ew, eh = extra.sizeHint().width(), extra.sizeHint().height()
            extra.setGeometry(right - ew, (h - eh) // 2, ew, eh)
            right -= ew + 8
        self.edit.setGeometry(18, 0, right - 18, h)

    def _submit(self) -> None:
        text = self.edit.text().strip()
        if text:
            sound("send")
            self.submitted.emit(text)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -3)
        shape = _pill(rect)
        focus = self.val("focus")
        ui_kit.soft_shadow(p, shape, dy=2, spread=5 + 4 * focus,
                           color=_rgba(_mix(sk.shadow, accent(), 0.5 * focus), sk.shadow.alphaF() * (0.8 + 0.6 * focus)))
        p.fillPath(shape, sk.field)
        p.save()                                    # a soft inset along the top: it's a well
        p.setClipPath(shape)
        grad = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(0, 5))
        grad.setColorAt(0.0, QColor(0, 0, 0, 90 if sk.dark else 18))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 5), QBrush(grad))
        p.restore()
        ring = _mix(sk.border, accent(), focus)
        ring.setAlphaF(sk.border.alphaF() + (0.75 - sk.border.alphaF()) * focus)
        p.setPen(QPen(ring, 1.0 + 0.6 * focus))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_pill(rect.adjusted(0.5, 0.5, -0.5, -0.5)))
        p.end()


# --- Sketch notes ----------------------------------------------------------------------------------

@dataclass
class Note:
    """A saved sketch note: its picture, title and when it was made, and the
    note itself (``data``, what sketch.NoteCard draws) when it was kept."""
    png: Path | None
    title: str
    when: dt.datetime
    followups: list = field(default_factory=list)
    data: dict = field(default_factory=dict)

    @property
    def drawable(self) -> bool:
        """It can be opened as a real note card (not just its picture)."""
        return bool(self.data.get("items"))

    @property
    def three_d(self) -> bool:
        return self.drawable and bool((self.data.get("view") or {}).get("3d"))


def notes_dir() -> Path:
    try:
        from sketch import NOTES_DIR
        return Path(NOTES_DIR)
    except Exception:
        from config import APP_HOME
        return Path(APP_HOME) / "notes"


def memory_dir() -> Path:
    try:
        from config import MEMORY_DIR
        return Path(MEMORY_DIR)
    except Exception:
        return Path.home() / ".clixy" / "memory"


_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2})(\d{2}) (.*)$")


def load_notes(folder: Path | None = None) -> list[Note]:
    """Every note in the folder, newest first (sketch.save_note names them
    "2026-11-04 0009 Title" with a .png and a .json)."""
    folder = folder or notes_dir()
    try:
        stems = {f.stem for f in folder.iterdir() if f.suffix.lower() in (".png", ".json")}
    except OSError:
        return []
    notes: list[Note] = []
    for stem in stems:
        png = folder / f"{stem}.png"
        meta: dict = {}
        try:
            meta = json.loads((folder / f"{stem}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        m = _STAMP.match(stem)
        try:
            when = dt.datetime(*map(int, m.groups()[:5])) if m else None
        except ValueError:
            when = None
        if when is None:
            try:
                when = dt.datetime.fromtimestamp((png if png.exists() else folder / f"{stem}.json").stat().st_mtime)
            except OSError:
                continue
        title = str(meta.get("title") or (m.group(6) if m else stem)).strip() or "Sketch note"
        notes.append(Note(png if png.exists() else None, title, when, list(meta.get("followups") or []),
                          meta if isinstance(meta, dict) else {}))
    notes.sort(key=lambda n: n.when, reverse=True)
    return notes


def when_text(when: dt.datetime, now: dt.datetime | None = None) -> str:
    """'12:09 am' today, 'Yesterday' or '3 Nov' before."""
    now = now or dt.datetime.now()
    if when.date() == now.date():
        hour = when.hour % 12 or 12
        return f"{hour}:{when.minute:02d} {'am' if when.hour < 12 else 'pm'}"
    if when.date() == (now - dt.timedelta(days=1)).date():
        return "Yesterday"
    return f"{when.day} {when:%b}"


def drawing_of(png: Path) -> QImage | None:
    """The drawing in a saved note's picture: the sketch.NoteCard's grid area,
    without its band, title and buttons (we draw our own)."""
    image = QImage(str(png))
    if image.isNull():
        return None
    try:
        from sketch import NoteCard
        card_w, card_h, margin = float(NoteCard.W), float(NoteCard.H), float(NoteCard._M)
    except Exception:
        card_w, card_h, margin = 470.0, 352.0, 18.0
    full_w, full_h = card_w + 2 * margin, card_h + 2 * margin
    k = image.width() / full_w
    if abs(image.height() / k - full_h) > 0.04 * full_h:
        return image  # not the card we know: show it whole
    top, bottom = margin + 89, margin + card_h - 47     # under the subtitle, above the follow-ups
    crop = QRectF(margin + 3, top, card_w - 6, bottom - top)
    part = image.copy(QRect(round(crop.left() * k), round(crop.top() * k), round(crop.width() * k),
                            round(crop.height() * k)))
    return _feather(part.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), round(14 * k))


def _feather(image: QImage, edge: int) -> QImage:
    """Fade the picture's edges out, so the drawing melts into our paper."""
    w, h = image.width(), image.height()
    p = QPainter(image)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    for start, end, rect in (((0, 0), (edge, 0), QRectF(0, 0, edge, h)), ((w, 0), (w - edge, 0), QRectF(w - edge, 0, edge, h)),
                             ((0, 0), (0, edge), QRectF(0, 0, w, edge)), ((0, h), (0, h - edge), QRectF(0, h - edge, w, edge))):
        g = QLinearGradient(QPointF(*start), QPointF(*end))
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(rect, QBrush(g))
    p.end()
    return image


def _dot_brush(dpr: float) -> QBrush:
    """The paper's faint dot grid: one dot every 14 px."""
    tile = QPixmap(round(14 * dpr), round(14 * dpr))
    tile.setDevicePixelRatio(dpr)
    tile.fill(QColor(0, 0, 0, 0))
    p = QPainter(tile)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(PAPER_DOT)
    p.drawEllipse(QPointF(7, 7), 1.15, 1.15)
    p.end()
    return QBrush(tile)


def paint_paper(painter: QPainter, rect: QRectF, radius: float, band_h: float = 0.0,
                dpr: float = 1.0) -> QPainterPath:
    """Warm paper with a faint dot grid and, if asked, its header strip."""
    shape = QPainterPath()
    shape.addRoundedRect(rect, radius, radius)
    painter.fillPath(shape, PAPER)
    brush = _dot_brush(dpr)
    brush.setTransform(QTransform.fromTranslate(rect.left(), rect.top()))
    painter.fillPath(shape, brush)
    if band_h:
        painter.save()
        painter.setClipPath(shape)
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), band_h), PAPER_BAND)
        painter.restore()
    return shape


class NoteStack(Motion, QWidget):
    """Your newest note, big, on warm paper held by a strip of tape, with your buddy
    peeking over its top edge. It lifts under the mouse with a sheen that follows it
    (and the buddy pops up a little); a 3D note's model turns live (drag it, double-click
    to reset); the ‹ › in its corner, the wheel or the arrow keys bring the older notes
    forward, with a paper sound."""

    open_note = pyqtSignal(object)
    follow_up = pyqtSignal(object)

    TOP = 40.0                       # room above the card for the tape and the buddy peeking over
    BAND = 34.0                      # the card's first line: when, 3D, ‹ 1 of 13 ›
    SHADOW = 30.0                    # room below the card for its shadow
    SIDE = 18.0                      # and either side of it

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.notes: list[Note] = []
        self.index = 0
        self._drawings: dict = {}
        self._mouse = QPointF(-1, -1)
        self._model = None            # the front note's live 3D model (render3d.RealModel), if it's a 3D note
        self._model_for = None
        self._shown_at = 0.0
        self._orbit_from: QPointF | None = None
        self._spin = QTimer(self)     # a 3D model rocks gently (30 frames a second while the panel shows it)
        self._spin.timeout.connect(self._spin_tick)
        self.open_btn = PanelButton("Open note", "glossy", self, px=14, weight=800, pad=(10, 19))
        self.ask_btn = PanelButton("Ask a follow-up", "paper", self, px=14, weight=700, pad=(9, 16))
        self.open_btn.clicked.connect(lambda: self.current and self.open_note.emit(self.current))
        self.ask_btn.clicked.connect(lambda: self.current and self.follow_up.emit(self.current))
        self.setMouseTracking(True)
        self.spring("lift", 0.0, 380.0, 28.0)
        self.spring("enter", 1.0, 300.0, 22.0)
        self.resize(round(553 + 2 * self.SIDE), round(418 + self.TOP + self.SHADOW))
        self._place()

    @property
    def current(self) -> Note | None:
        return self.notes[self.index] if 0 <= self.index < len(self.notes) else None

    def set_notes(self, notes: list[Note]) -> None:
        self.notes = notes
        self.index = 0
        self._place()
        self._use_model()
        self.update()

    def resizeEvent(self, _event) -> None:  # noqa: N802
        self._place()

    # --- The live 3D model --------------------------------------------------------------------
    def _title_font(self) -> QFont:
        f = theme.hand_font(10)
        f.setPixelSize(30)
        return f

    def _slot(self) -> QRectF:
        """Where the drawing (or the model) goes on the card."""
        card = self._card().translated(0, self._lift_px())
        top = card.top() + self.BAND + QFontMetricsF(self._title_font()).height() + 2
        return QRectF(card.left() + 14, top, card.width() - 28, self.open_btn.y() - 8 - top)

    def _use_model(self) -> None:
        """Build (or keep) the live model for the front note; let go of any other."""
        note = self.current
        key = (str(note.png), note.title, note.when) if note is not None and note.three_d else None
        if key == self._model_for:
            return
        self.release_model()
        self._model_for = key
        if key is None:
            return
        try:
            import model3d
            import render3d
            self._model = render3d.RealModel.from_note(model3d.upgrade_note(note.data))
        except Exception:
            self._model = None
        if self._model is not None:
            import time
            self._model.captions = []        # the takeaway is on the note itself; here, just the model
            self._shown_at = time.monotonic()
            if self.isVisible():
                self._spin.start(33)

    def release_model(self) -> None:
        """Free the model's GPU buffers (the panel closed, or another note came forward)."""
        self._spin.stop()
        if self._model is not None:
            try:
                self._model.release()
            except Exception:
                pass
        self._model = None
        self._model_for = None

    def _spin_tick(self) -> None:
        if self._model is None or not self.isVisible():
            self._spin.stop()
            return
        self.update(self._slot().adjusted(-60, -30, 60, 30).toAlignedRect())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._use_model()
        if self._model is not None:
            self._spin.start(33)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._spin.stop()

    # --- Where things are -------------------------------------------------------------------
    def _card(self) -> QRectF:
        return QRectF(self.SIDE, self.TOP, self.width() - 2 * self.SIDE, self.height() - self.TOP - self.SHADOW)

    def _backs(self) -> list[Note]:
        """The notes after this one (up to two): what › brings forward next."""
        return self.notes[self.index + 1:self.index + 3]

    def _lift_px(self) -> float:
        return -4.0 * self.val("lift") + 26.0 * (1.0 - self.val("enter"))

    def _nav(self) -> tuple[QRectF, QRectF, QRectF] | None:
        """The ‹, the "1 of 13" and the › in the card's corner (None with one note)."""
        if len(self.notes) < 2:
            return None
        card = self._card().translated(0, self._lift_px())
        f = font(12, 800)
        count = f"{self.index + 1} of {len(self.notes)}"
        cw = QFontMetricsF(f).horizontalAdvance(count) + 8
        cy = card.top() + self.BAND / 2 + 4
        nxt = QRectF(card.right() - 16 - 26, cy - 13, 26, 26)
        mid = QRectF(nxt.left() - cw, cy - 13, cw, 26)
        prev = QRectF(mid.left() - 26, cy - 13, 26, 26)
        return prev, mid, nxt

    def _place(self) -> None:
        card = self._card().translated(0, self._lift_px())
        has = self.current is not None
        for b in (self.open_btn, self.ask_btn):
            b.setVisible(has)
        if not has:
            return
        self.open_btn.setEnabled(self.current.png is not None or self.current.drawable)
        y = card.bottom() - 16 - self.open_btn.height()
        self.open_btn.move(round(card.left() + 18), round(y))
        self.ask_btn.move(round(card.left() + 18 + self.open_btn.width() + 10),
                          round(y + (self.open_btn.height() - self.ask_btn.height()) / 2))

    def _motion_step(self, dt: float) -> bool:
        moving = super()._motion_step(dt)
        self._place()                     # the buttons ride on the card as it lifts
        return moving

    def _drawing(self, note: Note) -> QImage | None:
        if note.png is None:
            return None
        key = str(note.png)
        if key not in self._drawings:
            if len(self._drawings) > 12:
                self._drawings.clear()
            self._drawings[key] = drawing_of(note.png)
        return self._drawings[key]

    # --- The mouse --------------------------------------------------------------------------
    def _nav_at(self, pos: QPointF) -> int:
        nav = self._nav()
        if nav is None:
            return 0
        if nav[0].contains(pos):
            return -1
        if nav[2].contains(pos):
            return 1
        return 0

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self._orbit_from is not None and self._model is not None:
            self._model.drag(pos.x() - self._orbit_from.x(), pos.y() - self._orbit_from.y())
            self._orbit_from = pos
            self.update()
            return
        on_model = self._model is not None and self._slot().contains(pos)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._nav_at(pos)
                       else Qt.CursorShape.OpenHandCursor if on_model else Qt.CursorShape.ArrowCursor)
        on_card = self._card().contains(pos)
        self.go("lift", 1.0 if on_card else 0.0)
        self._mouse = pos
        if on_card:
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self.go("lift", 0.0)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        step = self._nav_at(event.position())
        if step:
            self.show_index(self.index + step)
        elif self._model is not None and self._slot().contains(event.position()):
            self._orbit_from = event.position()          # turn the model by hand
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        if self._orbit_from is not None:
            self._orbit_from = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._model is not None and self._slot().contains(event.position()):
            self._model.reset_view()
            self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        step = -1 if event.angleDelta().y() > 0 else 1
        self.show_index(self.index + step)

    def show_index(self, i: int) -> None:
        if self.notes and 0 <= i < len(self.notes) and i != self.index:
            self.index = i
            self.go("enter", 0.0, snap=True)      # the new note slides up into place
            self.go("enter", 1.0)
            sound("slide")
            self._place()
            self._use_model()
            self.update()

    # --- Painting ---------------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        dpr = self.devicePixelRatioF()
        sk = skin()
        lift = self.val("lift")
        enter = max(0.0, min(1.2, self.val("enter")))
        card = self._card().translated(0, self._lift_px())
        note = self.current

        # The buddy, peeking over the top edge (the card hides its lower half). It pops up a
        # little when you point at the card.
        t = float(self.window().property("t") or 0.0) if self.window() else 0.0
        device = max(1, int(2.6 * dpr + 0.25))
        peek = QRectF(card.right() - 92, card.top() - 31 - 8 * lift, 52, 54)
        theme.paint_avatar_in(p, peek, t=t, scale=device / (2 * dpr), device_pixel_ratio=dpr,
                              mood="happy" if lift > 0.5 else "idle")
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        shape = QPainterPath()
        shape.addRoundedRect(card, 22, 22)
        p.setOpacity(min(1.0, 0.25 + 0.75 * enter))
        shadow = _rgba(sk.shadow, min(1.0, sk.shadow.alphaF() * 1.9))
        draw_shadow(p, shape, dy=8 + 6 * lift, blur=22, color=shadow, key=("card", card.width(), card.height(), sk.name))
        paint_paper(p, card, 22, 0, dpr)
        if lift > 0.01 and card.contains(self._mouse):
            # A sheen on the paper that follows the mouse: it feels like a real card.
            sheen = QRadialGradient(self._mouse, 240)
            sheen.setColorAt(0.0, QColor(255, 255, 255, round(70 * lift)))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.save()
            p.setClipPath(shape)
            p.fillRect(card, QBrush(sheen))
            p.restore()
        p.setPen(QPen(QColor(0, 0, 0, 22) if not sk.dark else QColor(255, 255, 255, 20), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(card.adjusted(-0.5, -0.5, 0.5, 0.5), 22.5, 22.5)
        _paint_tape(p, QPointF(card.left() + 60, card.top() + 1), -4.0)

        band = QRectF(card.left() + 18, card.top() + 8, card.width() - 36, self.BAND - 8)
        p.setFont(font(12, 700))
        p.setPen(NOTE_META)
        if note is None:
            p.drawText(band, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Sketch notes")
            title = self._title_font()
            p.setFont(title)
            p.setPen(INK)
            p.drawText(QRectF(card.left() + 18, card.top() + self.BAND + 18, card.width() - 36, 40),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "No notes yet")
            p.setFont(font(15, 600))
            p.setPen(BAND_TEXT)
            p.drawText(QRectF(card.left() + 18, card.top() + self.BAND + 66, card.width() - 36, 80),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       "Ask Clixy to explain something and a note draws itself here, in 3D when it helps.")
            p.end()
            return
        when = ("Latest note · " if self.index == 0 else "Note · ") + when_text(note.when)
        p.drawText(band, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, when)
        if note.three_d:
            wf = QFontMetricsF(font(12, 700)).horizontalAdvance(when)
            chip = QRectF(band.left() + wf + 10, band.center().y() - 10, 30, 20)
            p.fillPath(_pill(chip), QColor("#F1E2FF"))
            p.setFont(font(10.5, 800))
            p.setPen(QColor("#6B2BB8"))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "3D")
        nav = self._nav()
        if nav is not None:
            prev, mid, nxt = nav
            p.setFont(font(12, 800))
            p.setPen(NOTE_META)
            p.drawText(mid, Qt.AlignmentFlag.AlignCenter, f"{self.index + 1} of {len(self.notes)}")
            for rect, glyph, on in ((prev, "‹", self.index > 0), (nxt, "›", self.index < len(self.notes) - 1)):
                hover = rect.contains(self._mouse)
                if hover and on:
                    p.fillPath(_pill(rect), QColor(0, 0, 0, 16))
                p.setFont(font(19, 800))
                p.setPen(INK if on else PAPER_RING)
                p.drawText(rect.translated(0, -1.5), Qt.AlignmentFlag.AlignCenter, glyph)
        title_font = self._title_font()
        tfm = QFontMetricsF(title_font)
        top = card.top() + self.BAND
        p.setFont(title_font)
        p.setPen(INK)
        p.drawText(QRectF(card.left() + 18, top, card.width() - 36, tfm.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   tfm.elidedText(note.title, Qt.TextElideMode.ElideRight, card.width() - 36))
        slot = self._slot()
        if self._model is not None and not self._model.failed:
            import time
            seen = time.monotonic() - self._shown_at
            try:
                self._model.paint(p, slot, turn=self._model.TURN * 0.7 * math.sin(seen * 0.5), t=seen,
                                  reveal=seen + 0.3)
                p.end()
                return
            except Exception:
                self._model.failed = True       # its picture instead
        drawing = self._drawing(note)
        if drawing is not None and slot.height() > 20:
            k = min(slot.width() / drawing.width(), slot.height() / drawing.height())
            w, h = drawing.width() * k, drawing.height() * k
            target = QRectF(slot.center().x() - w / 2, slot.top() + (slot.height() - h) / 2, w, h)
            p.save()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            p.drawImage(target, drawing)
            p.restore()
        p.end()


def _paint_tape(p: QPainter, centre: QPointF, angle: float, size: tuple[float, float] = (78.0, 21.0)) -> None:
    """A strip of washi tape holding the note down: translucent gold with fine stripes
    and torn ends."""
    w, h = size
    p.save()
    p.translate(centre)
    p.rotate(angle)
    strip = QPainterPath()
    teeth = 5
    strip.moveTo(-w / 2, -h / 2)
    strip.lineTo(w / 2, -h / 2)
    for i in range(teeth):                      # the torn right end
        y0 = -h / 2 + h * i / teeth
        strip.lineTo(w / 2 - (3 if i % 2 == 0 else 0), y0 + h / teeth / 2)
        strip.lineTo(w / 2, y0 + h / teeth)
    strip.lineTo(-w / 2, h / 2)
    for i in range(teeth):                      # and the left
        y0 = h / 2 - h * i / teeth
        strip.lineTo(-w / 2 + (3 if i % 2 == 0 else 0), y0 - h / teeth / 2)
        strip.lineTo(-w / 2, y0 - h / teeth)
    strip.closeSubpath()
    p.fillPath(strip.translated(0, 1.5), QColor(0, 0, 0, 18))
    p.fillPath(strip, QColor(255, 214, 120, 228))
    p.setClipPath(strip)
    p.setPen(QPen(QColor(255, 236, 182, 200), 2.0))
    x = -w / 2
    while x < w / 2:
        p.drawLine(QPointF(x, -h / 2), QPointF(x, h / 2))
        x += 7
    p.restore()


# --- The pages -------------------------------------------------------------------------------------

def _greeting(name: str, now: dt.datetime | None = None) -> str:
    hour = (now or dt.datetime.now()).hour
    part = "Morning" if hour < 12 else "Afternoon" if hour < 17 else "Evening"
    return f"{part}, {name}" if name else f"Good {part.lower()}"


BUDDY_SPOT = QRectF(14, 14, 36, 40)      # the buddy in the top bar: point at it and it hops
VIOLET = ("#C29BFF", "#8C5CFF", "#6538EC")
SUNNY = ("#FFD970", "#FFAE3D", "#FF8A1F")


class Tile(Tactile, QAbstractButton):
    """A big colourful tile on Home: a glossy gradient with a soft circle of light in its
    corner, a picture (an icon, or a fan of real note thumbnails), a title and a line.
    It lifts under the mouse, its shadow glowing in its own colour, and sinks when pressed."""

    def __init__(self, title: str, line: str, colors: tuple[str, str, str], *, icon: str = "",
                 ink: str = "#FFFFFF", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(title)
        self.setAccessibleName(title)
        self.line = line
        self.colors = tuple(QColor(c) for c in colors)
        self.icon = icon
        self.ink = QColor(ink)
        self.pictures: list[QImage] = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tactile_init()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        hover, press = self.val("hover"), self.val("press")
        rect = QRectF(self.rect()).adjusted(4, 6, -4, -12).translated(0, -3.0 * hover * (1 - press))
        self.squish(p, rect, 0.03)
        top, mid, bottom = self.colors
        shape = QPainterPath()
        shape.addRoundedRect(rect, 24, 24)
        ui_kit.soft_shadow(p, shape, dy=6 + 4 * hover, spread=10 + 6 * hover,
                           color=_rgba(bottom, 0.32 + 0.16 * hover))
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, _mix(top, QColor(255, 255, 255), 0.06 * hover))
        grad.setColorAt(0.55, mid)
        grad.setColorAt(1.0, bottom)
        p.fillPath(shape, QBrush(grad))
        p.save()
        p.setClipPath(shape)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawEllipse(QPointF(rect.right() - 24 + 6 * hover, rect.top() + 20 - 4 * hover), 64, 64)
        shine = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.45))
        shine.setColorAt(0.0, QColor(255, 255, 255, 70))
        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(rect, QBrush(shine))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 23.5, 23.5)

        if self.pictures:
            self._paint_fan(p, QRectF(rect.left() + 16, rect.top() + 16, rect.width() - 32, 70), hover)
        elif self.icon:
            box = QRectF(rect.left() + 16, rect.top() + 16, 42, 42)
            p.fillPath(ui_kit.squircle(box, 0.32), QColor(255, 255, 255, 58))
            ui_kit.paint_icon(p, self.icon, box.adjusted(10, 10, -10, -10), self.ink, 2.3)
        tf = font(18, 800, -0.01)
        lf = font(12.5, 600)
        lfm = QFontMetricsF(lf)
        lines = _wrap(self.line, lfm, rect.width() - 32, 2) if self.line else []
        y = rect.bottom() - 16 - len(lines) * (lfm.height() + 1)
        p.setFont(tf)
        p.setPen(self.ink)
        p.drawText(QRectF(rect.left() + 16, y - 26, rect.width() - 32, 24), Qt.AlignmentFlag.AlignVCenter,
                   self.text())
        p.setFont(lf)
        faint = QColor(self.ink)
        faint.setAlphaF(0.88)
        p.setPen(faint)
        for line in lines:
            p.drawText(QRectF(rect.left() + 16, y, rect.width() - 32, lfm.height()), Qt.AlignmentFlag.AlignVCenter,
                       line)
            y += lfm.height() + 1
        p.end()

    def _paint_fan(self, p: QPainter, area: QRectF, hover: float) -> None:
        """Up to three note thumbnails fanned out like photos; they spread a little on hover."""
        w, h = 82.0, 56.0
        for i, (picture, angle) in enumerate(zip(self.pictures[:3], (-8.0, 5.0, -3.0))):
            spread = 48 + 10 * hover
            c = QPointF(area.left() + w / 2 + i * spread, area.top() + h / 2 + (6 if i != 1 else 0))
            p.save()
            p.translate(c)
            p.rotate(angle * (1 + 0.4 * hover))
            frame = QRectF(-w / 2, -h / 2, w, h)
            shape = QPainterPath()
            shape.addRoundedRect(frame, 10, 10)
            ui_kit.soft_shadow(p, shape, dy=3, spread=5, color=QColor(0, 0, 0, 60))
            p.fillPath(shape, QColor("#FFFFFF"))
            inner = frame.adjusted(2.5, 2.5, -2.5, -2.5)
            clip = QPainterPath()
            clip.addRoundedRect(inner, 8, 8)
            p.setClipPath(clip)
            p.fillRect(inner, PAPER)
            k = max(inner.width() / picture.width(), inner.height() / picture.height())
            pw, ph = picture.width() * k, picture.height() * k
            p.drawImage(QRectF(inner.center().x() - pw / 2, inner.center().y() - ph / 2, pw, ph), picture)
            p.restore()


class HomePage(Motion, QWidget):
    """Home, as colourful tiles: the top bar; a hello with your keys (which press
    themselves now and then); your newest note big on taped paper with your buddy peeking
    over it; a violet Do it for me tile and a sunny All your notes tile (real
    thumbnails); and the ask bar, with What Clixy remembers inside it. The buddy in the
    top bar hops when you point at it."""

    SIZE = HOME
    GRID_TOP = 150.0
    GAP = 12.0

    def __init__(self, panel: "ClixyPanel") -> None:
        super().__init__(panel._body)
        self.panel = panel
        self.resize(int(HOME[0]), int(HOME[1]))
        self.settings_btn = PanelButton("", "frost", self, icon="sliders", round_=38)
        self.settings_btn.setAccessibleName("Settings")
        self.settings_btn.setToolTip("Settings")
        self.stack = NoteStack(self)
        self.do_it = Tile("Do it for me", "Say “for me” and I'll click through it while you watch",
                          VIOLET, icon="cursor", parent=self)
        self.all_notes = Tile("All your notes", "", SUNNY, ink="#3B2200", parent=self)
        self.ask = AskField(self)
        self.memory = PanelButton("What Clixy remembers", "text", None, px=12.5, weight=800, pad=(6, 8))
        self.ask.set_extra(self.memory)
        self.setMouseTracking(True)
        self._on_buddy = False
        self.spring("hop", 0.0, 260.0, 9.0)
        self._layout()

    def set_notes(self, notes: list[Note]) -> None:
        """Fresh notes: the big card, and the All your notes tile's thumbnails and count."""
        self.stack.set_notes(notes)
        three_d = sum(1 for n in notes if n.three_d)
        if not notes:
            self.all_notes.line = "Every note Clixy draws for you gathers here"
        else:
            self.all_notes.line = (f"{len(notes)} drawn so far" + (f", {three_d} in 3D" if three_d else ""))
        pictures = []
        for note in notes[:6]:
            picture = self.stack._drawing(note)
            if picture is not None:
                pictures.append(picture)
            if len(pictures) == 3:
                break
        self.all_notes.pictures = pictures
        self.all_notes.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        on = BUDDY_SPOT.contains(event.position())
        if on and not self._on_buddy:
            self.hop()
        self._on_buddy = on
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if BUDDY_SPOT.contains(event.position()):
            self.hop(big=True)

    def hop(self, big: bool = False) -> None:
        """The buddy jumps (and says so)."""
        self.go("hop", 0.0, kick=-(150.0 if big else 95.0))
        sound("buddy")

    def _layout(self) -> None:
        w, h = self.width(), self.height()
        inner = w - 44
        col = (inner - 2 * self.GAP) / 3
        ask_h = 50
        grid_bottom = h - 20 - ask_h - 14
        row = (grid_bottom - self.GRID_TOP - self.GAP) / 2
        self.settings_btn.setGeometry(w - 22 - 38, 15, 38, 38)
        big_w = 2 * col + self.GAP
        self.stack.setGeometry(round(22 - NoteStack.SIDE), round(self.GRID_TOP - NoteStack.TOP),
                               round(big_w + 2 * NoteStack.SIDE),
                               round(2 * row + self.GAP + NoteStack.TOP + NoteStack.SHADOW))
        x = 22 + big_w + self.GAP
        self.do_it.setGeometry(round(x - 4), round(self.GRID_TOP - 6), round(col + 8), round(row + 18))
        self.all_notes.setGeometry(round(x - 4), round(self.GRID_TOP + row + self.GAP - 6), round(col + 8),
                                   round(row + 18))
        self.ask.setGeometry(22, round(h - 20 - ask_h), inner, ask_h)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        sk = skin()
        paint_top_bar(p, self, self.panel.status_text(), hop=self.val("hop"))
        # The hello and your shortcut, on the left.
        f = font(32, 800, -0.03)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(sk.text)
        p.drawText(QRectF(22, 62, self.width() - 44, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   _greeting(self.panel.name))
        keys = [k for k in self.panel.hotkey.split(" + ") if k]
        segments: list[tuple[str, str]] = [("text", "Hold")] + [("key", k) for k in keys] + \
            [("text", "and ask, or type below")]
        t = float(self.window().property("t") or 0.0) if self.window() else 0.0
        paint_key_line(p, segments, QPointF(0, 62 + fm.height() + 16), px=14.5, color=sk.text2, press_at=t, left=24)
        p.end()


def paint_top_bar(p: QPainter, page: QWidget, status: str, *, hop: float = 0.0) -> None:
    """The buddy, "Clixy" and a status chip, on the panel's first row (y 16-52)."""
    sk = skin()
    cy = 16 + 18
    dpr = page.devicePixelRatioF()
    device = max(1, int(2.0 * dpr + 0.25))      # whole device pixels per sprite cell
    t = (page.window().property("t") or 0.0) if page.window() else 0.0
    theme.paint_avatar_in(p, QRectF(18, cy - 17 + hop, 30, 32), t=float(t), scale=device / (2 * dpr),
                          device_pixel_ratio=dpr, mood="happy" if hop < -2 else "idle")
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    x = 22 + 26 + 10
    f = font(17, 800, -0.01)
    p.setFont(f)
    p.setPen(sk.text)
    w = QFontMetricsF(f).horizontalAdvance("Clixy")
    p.drawText(QRectF(x, cy - 13, w + 4, 26), Qt.AlignmentFlag.AlignVCenter, "Clixy")
    x += w + 10
    chip_font = font(12, 700)
    cw = QFontMetricsF(chip_font).horizontalAdvance(status)
    chip = QRectF(x, cy - 12, 10 + 8 + 6 + cw + 12, 24)
    acc = accent()
    p.fillPath(_pill(chip), _rgba(acc, 0.13 if not sk.dark else 0.2))
    p.setPen(QPen(_rgba(acc, 0.28), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(_pill(chip.adjusted(0.5, 0.5, -0.5, -0.5)))
    live = status not in ("Ready", "")
    beat = 0.5 + 0.5 * math.sin(float(t) * (6.0 if live else 2.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_rgba(acc, 0.25 * beat))
    p.drawEllipse(QPointF(chip.left() + 14, cy), 4 + 3 * beat, 4 + 3 * beat)
    p.setBrush(acc)
    p.drawEllipse(QPointF(chip.left() + 14, cy), 4, 4)
    p.setFont(chip_font)
    p.setPen(_mix(acc, sk.text, 0.55))
    p.drawText(QRectF(chip.left() + 24, chip.top(), cw + 4, chip.height()), Qt.AlignmentFlag.AlignVCenter, status)


def paint_key_line(p: QPainter, segments: list[tuple[str, str]], centre: QPointF, *, px: float = 15,
                   color: QColor | None = None, light: bool = False, press_at: float | None = None,
                   left: float | None = None) -> None:
    """A line of words and keycaps ("Hold [Ctrl] [Win] and ask"), centred on ``centre``
    (or starting at ``left``). With ``press_at`` (seconds), the keys press themselves
    together every few seconds."""
    color = color if color is not None else skin().text2
    f = font(px, 600)
    fm = QFontMetricsF(f)
    gap = 7.0
    widths = []
    for kind, text in segments:
        widths.append(keycap_size(text, 13, (9, 4, 5)).width() if kind == "key" else fm.horizontalAdvance(text))
    total = sum(widths) + gap * (len(segments) - 1)
    x = left if left is not None else centre.x() - total / 2
    press = 0.0
    if press_at is not None:
        phase = press_at % 4.0                # every 4 s: down for a moment, then up
        press = max(0.0, 1.0 - abs(phase - 0.35) / 0.25) if phase < 0.6 else 0.0
    for (kind, text), w in zip(segments, widths):
        if kind == "key":
            size = keycap_size(text, 13, (9, 4, 5))
            paint_keycap(p, QRectF(x, centre.y() - size.height() / 2 - 2, w, size.height()), text, px=13,
                         light=light, edge=3.0, radius=8.0, press=press)
        else:
            p.setFont(f)
            p.setPen(color)
            p.drawText(QRectF(x, centre.y() - fm.height() / 2, w + 2, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                       text)
        x += w + gap


class WorkingPage(QWidget):
    """While a guide runs: what you asked, the steps done, the step now, I did it and Stop."""

    def __init__(self, panel: "ClixyPanel") -> None:
        super().__init__(panel._body)
        self.panel = panel
        self.info: dict = {}
        self.did_it = PanelButton("I did it", "glossy", self, px=14, weight=800, pad=(10, 18))
        self.stop = PanelButton("Stop", "text", self, px=14, weight=700, pad=(10, 6))
        self.resize(int(WORK_W), 300)

    # Rows of the step list: (kind, label, keys).
    def _rows(self) -> list[tuple[str, str, str]]:
        info = self.info
        done = list(info.get("done") or [])
        rows = [("done", label, "") for label in done[-3:]]
        if len(done) > 3:
            rows.insert(0, ("more", f"{len(done) - 3} earlier step{'s' if len(done) - 3 != 1 else ''} done", ""))
        now = info.get("now") or ("Looking at your screen" if not done else "Working out the next step")
        rows.append(("now", now, info.get("keys") or ""))
        n, total = int(info.get("n") or 0), int(info.get("total") or 0)
        if total > n > 0:
            left = total - n
            rows.append(("next", f"{left} more step{'s' if left != 1 else ''} after this", ""))
        return rows

    def card_height(self) -> float:
        rows = self._rows()
        list_h = len(rows) * 24 + max(0, len(rows) - 1) * 12
        hint_h = 92 if self.info.get("shortcut") else 0
        return 36 + 16 + max(list_h, hint_h) + 20

    def page_height(self) -> float:
        return 16 + 24 + 16 + self.card_height() + 16 + self.did_it.height() + 20

    def set_info(self, info: dict) -> None:
        self.info = dict(info or {})
        self.resize(int(WORK_W), round(self.page_height()))
        y = round(16 + 24 + 16 + self.card_height() + 16)
        self.did_it.move(20, y)
        self.did_it.setVisible(bool(self.info.get("now")))
        self.stop.move(self.width() - 20 - self.stop.width(), y + (self.did_it.height() - self.stop.height()) // 2)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        info = self.info
        acc = accent()
        sk = skin()
        # The first row: "Guiding you", the step chip, the light.
        cy = 16 + 12
        f = font(16, 800)
        p.setFont(f)
        p.setPen(sk.text)
        w = QFontMetricsF(f).horizontalAdvance("Guiding you")
        p.drawText(QRectF(20, cy - 12, w + 4, 24), Qt.AlignmentFlag.AlignVCenter, "Guiding you")
        n, total = int(info.get("n") or 0), int(info.get("total") or 0)
        chip_text = f"{n} of {total}" if total >= n > 0 else (f"Step {n}" if n else "")
        if chip_text:
            cf = font(12, 800)
            cw = QFontMetricsF(cf).horizontalAdvance(chip_text)
            chip = QRectF(20 + w + 10, cy - 12, cw + 22, 24)
            p.fillPath(_pill(chip), _rgba(acc, 0.14 if not sk.dark else 0.22))
            p.setFont(cf)
            p.setPen(_mix(acc, sk.text, 0.5))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)
        paint_light(p, QPointF(self.width() - 20 - 5, cy), 4.2, 0.95, acc, 13.0)

        # The paper card.
        card = QRectF(20, 16 + 24 + 16, self.width() - 40, self.card_height())
        shape = QPainterPath()
        shape.addRoundedRect(card, 18, 18)
        ui_kit.soft_shadow(p, shape, dy=6, spread=12, color=sk.shadow)
        paint_paper(p, card, 18, 36, self.devicePixelRatioF())
        p.setPen(QPen(QColor(0, 0, 0, 20) if not sk.dark else QColor(255, 255, 255, 20), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(card.adjusted(-0.5, -0.5, 0.5, 0.5), 18.5, 18.5)
        bf = font(12, 700)
        p.setFont(bf)
        p.setPen(BAND_TEXT)
        goal = info.get("goal") or ""
        band = QRectF(card.left() + 18, card.top(), card.width() - 36, 36)
        p.drawText(band, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   QFontMetricsF(bf).elidedText(f"You asked: {goal}" if goal else "Step by step",
                                                Qt.TextElideMode.ElideRight, band.width()))
        shortcut = info.get("shortcut") or ""
        hint_w = 220 if shortcut else 0
        x0, y = card.left() + 20, card.top() + 36 + 16
        text_w = card.width() - 40 - (hint_w + 22 if hint_w else 0) - 36
        for kind, label, keys in self._rows():
            mark = QRectF(x0, y, 24, 24)
            if kind == "done":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(INK)
                p.drawEllipse(mark)
                paint_icon(p, "check", QRectF(mark.center().x() - 6, mark.center().y() - 6, 12, 12), PAPER, 3.2)
                tf, colour = font(15, 600), BAND_TEXT
            elif kind == "now":
                p.setPen(QPen(acc, 2.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(mark.adjusted(1, 1, -1, -1))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(acc)
                p.drawEllipse(mark.center(), 5, 5)
                tf, colour = font(16, 800), INK
            elif kind == "more":
                tf, colour = font(13, 600), PAPER_MUTED
            else:
                p.setPen(QPen(PAPER_RING, 2.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(mark.adjusted(1, 1, -1, -1))
                tf, colour = font(15, 600), PAPER_MUTED
            tx = x0 + 36 if kind != "more" else x0 + 36
            if kind == "now" and keys:
                from annotations import key_names
                segs = [("text", "Press")]
                for i, k in enumerate(key_names(keys)):
                    if i:
                        segs.append(("text", "+"))
                    segs.append(("key", k))
                self._paint_inline(p, segs, QPointF(tx, y + 12), tf, colour)
            else:
                p.setFont(tf)
                p.setPen(colour)
                p.drawText(QRectF(tx, y, text_w, 24), Qt.AlignmentFlag.AlignVCenter,
                           QFontMetricsF(tf).elidedText(label, Qt.TextElideMode.ElideRight, text_w))
            y += 24 + 12
        if shortcut:
            self._paint_hint(p, QRectF(card.right() - 20 - hint_w, card.top() + 36 + 16, hint_w, 92), shortcut)
        p.end()

    @staticmethod
    def _paint_inline(p: QPainter, segs, start: QPointF, f: QFont, colour: QColor) -> None:
        fm = QFontMetricsF(f)
        x = start.x()
        for kind, text in segs:
            if kind == "key":
                size = keycap_size(text)
                paint_keycap(p, QRectF(x, start.y() - size.height() / 2 - 1, size.width(), size.height()), text,
                             light=True)
                x += size.width() + 6
            else:
                p.setFont(f)
                p.setPen(colour)
                w = fm.horizontalAdvance(text)
                p.drawText(QRectF(x, start.y() - 12, w + 2, 24), Qt.AlignmentFlag.AlignVCenter, text)
                x += w + 6

    def _paint_hint(self, p: QPainter, rect: QRectF, shortcut: str) -> None:
        """"Faster next time": the guide's shortcut as keycaps on a white tab."""
        from annotations import key_names
        p.save()
        shape = QPainterPath()
        shape.addRoundedRect(rect, 14, 14)
        draw_shadow(p, shape, dy=6, blur=16, color=QColor(0, 0, 0, 15), key=("hint", rect.width(), rect.height()))
        p.fillPath(shape.translated(0, 1), QColor("#E3DBCC"))
        p.fillPath(shape, QColor("#FFFFFF"))
        p.setFont(font(12, 800, 0.06))
        p.setPen(PAPER_MUTED)
        p.drawText(QRectF(rect.left() + 16, rect.top() + 14, rect.width() - 32, 18), Qt.AlignmentFlag.AlignVCenter,
                   "FASTER NEXT TIME")
        segs = []
        for i, k in enumerate(key_names(shortcut)):
            if i:
                segs.append(("text", "+"))
            segs.append(("key", k))
        self._paint_inline(p, segs, QPointF(rect.left() + 16, rect.top() + 14 + 18 + 10 + 12), font(14, 700), INK)
        p.restore()


# --- The panel -------------------------------------------------------------------------------------

def _key_down(vk: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


class ClixyPanel(QWidget):
    """The panel window. ``open_on(screen)`` grows it out of the notch;
    ``close_panel()`` tucks it back. Signals: ``asked`` (a typed question),
    ``settings_requested``, ``closed``."""

    asked = pyqtSignal(str)
    settings_requested = pyqtSignal()
    """The built-in Settings couldn't open: show the Settings window instead."""
    settings_saved = pyqtSignal(bool)
    """Settings were saved here; True when a new provider needs a restart."""
    stopped = pyqtSignal()
    closed = pyqtSignal()
    note_opened = pyqtSignal(dict)
    """Open note: show this saved note again as the real note card (sketch.NoteCard)."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Clixy")
        self.resize(int(HOME[0] + 2 * PAD_X), int(MAX_H + PAD_B))
        self.hotkey = "Ctrl + Win"
        self.name = ""
        self.status_fn = None
        """Returns the buddy's state in a word ("Ready", "Listening"...)."""
        self.guide_fn = None
        """Returns the running guide's progress() dict, or None."""
        self.notes_folder: Path | None = None

        self._body = QWidget(self)
        self._fade_fx = QGraphicsOpacityEffect(self._body)
        self._fade_fx.setOpacity(0.0)
        self._body.setGraphicsEffect(self._fade_fx)
        self.home = HomePage(self)
        self.working = WorkingPage(self)
        self.settings = None
        """The Settings view (panel_settings.SettingsPage), made the first time."""
        self.view = "home"
        self.working.hide()

        self.home.stack.open_note.connect(self._open_note)
        self.home.stack.follow_up.connect(self._follow_up)
        self.home.do_it.clicked.connect(self._do_it_for_me)
        self.home.all_notes.clicked.connect(lambda: self._open_folder(self.notes_folder or notes_dir()))
        self.home.memory.clicked.connect(lambda: self._open_folder(memory_dir()))
        self.home.settings_btn.clicked.connect(self._settings)
        self.home.ask.submitted.connect(self._ask)
        self.working.did_it.clicked.connect(self._did_it)
        self.working.stop.clicked.connect(self._stop)

        self._w, self._h = NOTCH
        self._vw = self._vh = 0.0
        self._fade = 0.0
        self._open = False
        self._hidden_for_capture = False
        self._was_down = True
        self._was_esc = True
        self._follow_note: Note | None = None
        self._last_box: QRect | None = None
        self._frame = 0
        import time
        self._clock = time.monotonic
        self._t0 = self._clock()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._animate)
        theme.on_avatar_changed(lambda _id: self._recolor())
        ui_kit.on_skin_changed(self._reskin)

    def _reskin(self) -> None:
        """The skin changed (Settings > Look): repaint everything in it."""
        try:
            self.home.ask.restyle()
            for w in self.findChildren(QWidget):
                w.update()
            self.update()
        except RuntimeError:
            pass

    # --- Opening and closing ---------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._open

    def open_on(self, screen=None, *, focus_ask: bool = False) -> None:
        """Grow out of the notch on ``screen`` (top centre)."""
        screen = screen or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.x() + (area.width() - self.width()) // 2, area.y())
        self.refresh()
        if not self._open:
            self._w, self._h = NOTCH
            self._vw = self._vh = 0.0
            self._fade = 0.0
            sound("open")
        self._open = True
        self._was_down = True
        self._was_esc = True
        self._place_body()
        self.show()
        self.raise_()
        if self.view != "working":       # a guide watches the foreground window: leave it be
            self._activate()
            if focus_ask:
                self.home.ask.edit.setFocus()
        self._tick.start(16)

    def close_panel(self, *, instant: bool = False) -> None:
        """Tuck back into the notch (``instant``: just go, e.g. before a screen capture)."""
        if not self._open and not self.isVisible():
            return
        if self._open and not instant:
            sound("close")
        self._open = False
        self._follow_note = None
        self._for_me = False
        self.home.ask.edit.setPlaceholderText(AskField.PLACEHOLDER)
        if self.settings is not None and self.view == "settings":
            self.settings.discard()      # closing without Done keeps the old settings
        if instant:
            self._finish_close()

    def _finish_close(self) -> None:
        self._tick.stop()
        self.home.stack.release_model()
        self._last_box = None
        self._w, self._h = NOTCH
        self._fade = 0.0
        self._fade_fx.setEnabled(True)
        self._fade_fx.setOpacity(0.0)
        self.hide()
        self.closed.emit()

    def hide_for_capture(self) -> None:
        if self.isVisible():
            self._hidden_for_capture = True
            self.hide()

    def show_after_capture(self) -> None:
        if self._hidden_for_capture and self._open:
            self.show()
        self._hidden_for_capture = False

    def _activate(self) -> None:
        """Take the keyboard (to type a question). The click on the notch was
        ours, so Windows lets us come to the front."""
        self.activateWindow()
        if sys.platform != "win32":
            return
        try:
            import ctypes
            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            hwnd = int(self.winId())
            fg = user32.GetForegroundWindow()
            ours = kernel32.GetCurrentThreadId()
            theirs = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(theirs and theirs != ours and user32.AttachThreadInput(theirs, ours, True))
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            if attached:
                user32.AttachThreadInput(theirs, ours, False)
        except Exception:
            pass

    # --- What it shows -----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Fresh notes, guide progress and greeting."""
        self.home.set_notes(load_notes(self.notes_folder))
        info = self._guide_info()
        self._set_view("working" if info else "home", info)
        self.home.update()

    def show_settings(self, section: str = "") -> bool:
        """Turn the panel into Settings. False if they couldn't be read."""
        try:
            if self.settings is None:
                from panel_settings import SettingsPage
                self.settings = SettingsPage(self._body)
                self.settings.hide()
                self.settings.saved.connect(self._settings_done)
                self.settings.left.connect(lambda: self._set_view(self._home_view(), self._guide_info()))
            if section:
                self.settings.section = section
            self.settings.load()
        except Exception:
            import traceback
            traceback.print_exc()
            return False
        self._set_view("settings")
        self._activate()
        return True

    def _settings_done(self, restart: bool) -> None:
        self._set_view(self._home_view(), self._guide_info())
        self.settings_saved.emit(restart)

    def _guide_info(self) -> dict | None:
        try:
            return self.guide_fn() if self.guide_fn else None
        except Exception:
            return None

    def _home_view(self) -> str:
        return "working" if self._guide_info() else "home"

    def status_text(self) -> str:
        try:
            return (self.status_fn() if self.status_fn else "") or "Ready"
        except Exception:
            return "Ready"

    def _set_view(self, view: str, info: dict | None = None) -> None:
        if view == "working":
            self.working.set_info(info or {})
        if view != self.view:
            if self.view == "settings" and self.settings is not None:
                self.settings.discard()
            self.view = view
            self.home.setVisible(view == "home")
            self.working.setVisible(view == "working")
            if self.settings is not None:
                self.settings.setVisible(view == "settings")
            self._fade = min(self._fade, 0.0)
            self._fade_fx.setEnabled(True)
            self._fade_fx.setOpacity(0.0)

    def _target(self) -> tuple[float, float]:
        if not self._open:
            return NOTCH
        if self.view == "working":
            return WORK_W, float(self.working.height())
        if self.view == "settings" and self.settings is not None:
            return float(self.settings.width()), float(self.settings.height())
        return HOME

    def _page(self) -> QWidget:
        if self.view == "settings" and self.settings is not None:
            return self.settings
        return self.working if self.view == "working" else self.home

    def _recolor(self) -> None:
        for button in self.findChildren(PanelButton):
            button.recolor()
        self.update()

    # --- Animation ---------------------------------------------------------------------------------

    def _animate(self) -> None:
        self._frame += 1
        self.setProperty("t", self._clock() - self._t0)
        tw, th = self._target()
        for _ in range(1):
            self._vw = (self._vw + (tw - self._w) * _STIFF) * _DAMP
            self._vh = (self._vh + (th - self._h) * _STIFF) * _DAMP
            self._w += self._vw
            self._h += self._vh
        settled = abs(tw - self._w) < 0.3 and abs(th - self._h) < 0.3 and abs(self._vw) < 0.3 and abs(self._vh) < 0.3
        if settled:
            self._w, self._h, self._vw, self._vh = tw, th, 0.0, 0.0
        if self._open:
            there = self._w > tw * 0.9 and self._h > th * 0.86
            if there:
                self._fade = min(1.0, self._fade + 0.16)
        else:
            self._fade = max(0.0, self._fade - 0.34)
            if self._h < NOTCH[1] + 30 or settled:
                self._finish_close()
                return
        moving = not settled or self._fade < 1.0
        if moving or self._fade_fx.isEnabled():
            self._fade_fx.setOpacity(self._fade)
            self._fade_fx.setEnabled(self._fade < 0.999)
            self._place_body()
            # Repaint only around the shape (and its shadow), and only while it moves.
            box = self._shape().boundingRect().adjusted(-70, -4, 70, 96).toAlignedRect()
            self.update(box.united(self._last_box) if self._last_box is not None else box)
            self._last_box = box
        if self._open:
            self._watch_input()
            if self._frame % 4 == 0 and self.view == "home":
                self.home.update(QRect(0, 0, 440, 62))    # the buddy breathes, the status light beats
                self.home.update(QRect(0, 100, 600, 46))  # the keycaps press themselves
                stack = self.home.stack                   # the buddy peeking over the note
                stack.update(QRect(stack.width() - 110, 0, 110, round(NoteStack.TOP) + 20))
            if self._frame % 15 == 0 and self.view != "settings":
                info = self._guide_info()
                if info:
                    if info != self.working.info or self.view != "working":
                        self._set_view("working", info)
                elif self.view == "working":
                    self._set_view("home")

    def _place_body(self) -> None:
        cx = self.width() / 2.0
        w, h = max(1.0, self._w), max(1.0, self._h)
        self._body.setGeometry(round(cx - w / 2), 0, round(w), round(h))
        page = self._page()
        page.move(round((w - page.width()) / 2), round(12 * (1.0 - self._fade)))   # it settles up into place

    def _shape(self) -> QPainterPath:
        w, h = self._w, self._h
        k = max(0.0, min(1.0, (h - NOTCH[1]) / (HOME[1] - NOTCH[1])))
        radius = 9.0 + (34.0 - 9.0) * min(1.0, k * 2.4)
        if self.view == "working" and self._open:
            radius = min(radius, 32.0)
        shoulder = 7.0 + (SHOULDER - 7.0) * min(1.0, k * 3.0)
        return tab_path(w, h, shoulder, radius).translated(self.width() / 2.0 - w / 2.0, 0.0)

    def shape_global(self) -> QPainterPath:
        return self._shape().translated(self.x(), self.y())

    def _watch_input(self) -> None:
        """Close on a click outside the panel or on Esc (works whether or not
        the panel has the keyboard)."""
        down = _key_down(0x01) or _key_down(0x02)
        if down and not self._was_down:
            if not self.shape_global().contains(QPointF(QCursor.pos())) and not self._popup_open():
                self.close_panel()
        self._was_down = down
        esc = _key_down(0x1B)
        capturing = self.view == "settings" and self.settings is not None and self.settings.busy
        if esc and not self._was_esc and not self._popup_open() and not capturing:
            self.close_panel()
        self._was_esc = esc

    @staticmethod
    def _popup_open() -> bool:
        return QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if not (self.view == "settings" and self.settings is not None and self.settings.busy):
                self.close_panel()
            return
        if self.view == "home" and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) \
                and not self.home.ask.edit.hasFocus():
            step = 1 if event.key() == Qt.Key.Key_Right else -1
            self.home.stack.show_index(self.home.stack.index + step)
            return
        super().keyPressEvent(event)

    # --- Painting ----------------------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        shape = self._shape()
        sk = skin()
        big = max(0.0, min(1.0, (self._h - NOTCH[1]) / 120.0))
        if big > 0.02:
            settled = self._vw == 0 and self._vh == 0
            draw_shadow(p, shape, dy=24, blur=44, color=QColor(10, 12, 30, round((70 if not sk.dark else 110) * big)),
                        key=("panel", round(self._w), round(self._h), self.width(), sk.name) if settled else None)
        # It leaves the notch black and blooms into the skin's colours as it grows.
        bloom = max(0.0, min(1.0, (self._h - NOTCH[1]) / 160.0)) ** 0.8
        box = shape.boundingRect()
        if bloom < 0.999:
            p.fillPath(shape, theme.NOTCH)
        if bloom > 0.001:
            p.save()
            p.setOpacity(bloom)
            ui_kit.paint_canvas(p, shape, QRectF(box.left(), 0, box.width(), max(1.0, self._h)), sk,
                                dpr=self.devicePixelRatioF(), glow=self._fade)
            p.restore()
        # A hairline that fades in away from the screen edge, as on the notch.
        edge = QLinearGradient(0, 0, 0, 22)
        rim = QColor(sk.border) if bloom > 0.5 else QColor(255, 255, 255, 16)
        clear = QColor(rim)
        clear.setAlpha(0)
        edge.setColorAt(0.0, clear)
        edge.setColorAt(1.0, rim)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(shape)
        p.end()

    # --- Actions -----------------------------------------------------------------------------------

    def _ask(self, text: str) -> None:
        note = self._follow_note
        if note is not None:
            text = f'{text} (about my sketch note "{note.title}")'
        if getattr(self, "_for_me", False):
            import guide
            if not guide.wants_drive(text):
                text = f"{text.rstrip('.!? ')} for me"       # from the Do it for me tile: Clixy does it
            self._for_me = False
        self.home.ask.edit.clear()
        self.close_panel(instant=True)
        # Let the panel leave the screen before the question's screenshot.
        QTimer.singleShot(80, lambda: self.asked.emit(text))

    def _do_it_for_me(self) -> None:
        """The Do it for me tile: say what to do, and Clixy does the steps itself."""
        self._for_me = True
        edit = self.home.ask.edit
        edit.setPlaceholderText("What should I do for you?")
        self._activate()
        edit.setFocus()

    def _follow_up(self, note: Note) -> None:
        self._follow_note = note
        edit = self.home.ask.edit
        edit.setPlaceholderText(QFontMetricsF(edit.font()).elidedText(
            f"Ask about {note.title}", Qt.TextElideMode.ElideRight, edit.width() - 4))
        self._activate()
        edit.setFocus()

    def _open_note(self, note: Note) -> None:
        if note.drawable and self.receivers(self.note_opened) > 0:
            self.close_panel()
            self.note_opened.emit(_best_version(note.data))   # the pad it first appeared on, drawing itself again
        elif note.png is not None:
            self.close_panel(instant=True)
            _start(note.png)

    def _open_folder(self, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        self.close_panel(instant=True)
        _start(folder)

    def _settings(self) -> None:
        if not self.show_settings():
            self.close_panel(instant=True)
            self.settings_requested.emit()     # the Settings window, as before

    def _did_it(self) -> None:
        g = _running_guide()
        if g is not None:
            g.confirm()

    def _stop(self) -> None:
        g = _running_guide()
        if g is not None:
            g.stop()
        self.close_panel()
        self.stopped.emit()


def _best_version(data: dict) -> dict:
    """A kept note as it should look now: an older 3D note shown with the hand-built
    model it's about (model3d.upgrade_note), or just as it was."""
    try:
        import model3d
        return model3d.upgrade_note(dict(data))
    except Exception:
        return dict(data)


def _running_guide():
    try:
        import guide
        return guide.current()
    except Exception:
        return None


def guide_progress() -> dict | None:
    """The running guide's progress for the Working view, or None."""
    g = _running_guide()
    try:
        return g.progress() if g is not None else None
    except Exception:
        return None


def _start(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass
