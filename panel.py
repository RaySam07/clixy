"""The Clixy panel: it grows out of the notch.

Click the notch (island.py) and the panel drops down from the top of the
screen, its shoulders flaring into the screen edge like the notch's own. It
leaves the notch black and blooms into the skin's colours as it grows
(ui_kit: bright Daylight by default, or Charcoal), with a breath of the
buddy's colour at the top. Everything in it is tactile: buttons sink and
spring back, tiles lift, and each move has its little sound (sfx.py, through
ui_kit.sound):

- Home (board J, 4 Nov 2026): what Clixy is doing now and what's waiting, no
  note card (drawings show on the screen when asked). While a job runs (a
  guide, guide.py): the job, its steps, your screen with a ring on the next
  click, I did it or Take over, Stop; Your call when a step needs your yes; Up
  next (jobs typed meanwhile wait their turn, jobs.py); today's done jobs.
  When nothing runs: jobs to do again, Do it for me and Done today (a note in
  it opens its card again, sketch.NoteCard).
- Settings: the gear turns the panel into Settings (panel_settings.py), all
  in one place, saved through settings_dialog.SettingsDialog's own logic.

Click outside it or press Esc and it tucks back into the notch. It never
shows the words of Clixy's spoken answers: those are only ever heard.

The accent everywhere is your buddy's colour (theme.current_palette().flood).

Threading: Qt main thread only. A guide runs on a worker thread; the panel
reads its progress (guide.current().progress()) and calls confirm(), stop(),
approve() and set_drive(), which are thread-safe.
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
    QRadialGradient,
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
from island import tab_path
from ui_kit import Motion, Tactile, skin, sound

# --- Colours and sizes -------------------------------------------------------------------------------
# The panel's own colours come from the skin (ui_kit.skin()); these are the note paper's.

INK = QColor("#1D1B18")

NOTCH = (150.0, 24.0)          # where it grows from (island.SideIsland.NOTCH)
HOME = (880.0, 650.0)
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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        activate = getattr(self.window(), "_activate", None)   # the panel opened without the keyboard (a job runs)
        if callable(activate) and not self.window().isActiveWindow():
            activate()
        super().mousePressEvent(event)

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
    PLACEHOLDER = "Ask anything, or tell me what to do"

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


# --- The pages -------------------------------------------------------------------------------------

def _greeting(name: str, now: dt.datetime | None = None) -> str:
    hour = (now or dt.datetime.now()).hour
    part = "Morning" if hour < 12 else "Afternoon" if hour < 17 else "Evening"
    return f"{part}, {name}" if name else f"Good {part.lower()}"


def _sentence(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:1].upper() + text[1:]


def _clock(t: float) -> str:
    """A wall time as "11:42" (12-hour, no am or pm)."""
    when = dt.datetime.fromtimestamp(t)
    return f"{when.hour % 12 or 12}:{when.minute:02d}"


def _qimage(image) -> QImage | None:
    """A screenshot (a PIL image, or already a QImage) as a QImage."""
    if image is None or isinstance(image, QImage):
        return image
    try:
        rgb = image.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        return QImage(data, rgb.width, rgb.height, 3 * rgb.width, QImage.Format.Format_RGB888).copy()
    except Exception:
        return None


def _live_key(info: dict | None) -> tuple | None:
    """What the live card shows, to tell when it changed (the picture by identity)."""
    if not info:
        return None
    view = info.get("view") or (None, None)
    return (info.get("goal"), info.get("n"), info.get("total"), info.get("now"), tuple(info.get("done") or ()),
            tuple(info.get("plan") or ()), bool(info.get("driving")), bool(info.get("asking")),
            bool(info.get("can_drive", True)), id(view[0]), view[1])


BUDDY_SPOT = QRectF(14, 14, 36, 40)      # the buddy in the top bar: point at it and it hops
VIOLET = ("#C29BFF", "#8C5CFF", "#6538EC")
SUNNY = ("#FFD970", "#FFAE3D", "#FF8A1F")
MINT = ("#8FF0C0", "#34C77B", "#1FA463")
SUNNY_INK, MINT_INK = "#3B2200", "#06331C"
EXAMPLES = ("For me, open my display settings", "Show my battery and CPU side by side", "Help me turn on dark mode")
"""Jobs to try on Home before any of yours are done."""


def paint_tile_face(p: QPainter, rect: QRectF, colors, *, hover: float = 0.0) -> QPainterPath:
    """A colourful tile's face: a glossy gradient with a soft circle of light in its corner,
    a shine across the top and a light rim, over a shadow in its own colour."""
    top, mid, bottom = (QColor(c) for c in colors)
    shape = QPainterPath()
    shape.addRoundedRect(rect, 24, 24)
    ui_kit.soft_shadow(p, shape, dy=6 + 4 * hover, spread=10 + 6 * hover, color=_rgba(bottom, 0.32 + 0.16 * hover))
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
    return shape


def paint_caps(p: QPainter, text: str, x: float, y: float, color: QColor, px: float = 11) -> float:
    """A small spaced-out capitals label ("LIVE NOW") with its top at ``y``; its width."""
    f = font(px, 800, 0.08)
    fm = QFontMetricsF(f)
    text = text.upper()
    w = fm.horizontalAdvance(text)
    p.setFont(f)
    p.setPen(color)
    p.drawText(QRectF(x, y, w + 4, fm.height()), Qt.AlignmentFlag.AlignVCenter, text)
    return w


def paint_count(p: QPainter, n: int, x: float, cy: float, ink: QColor, fill: QColor) -> None:
    """A little number pill beside a label ("UP NEXT  2")."""
    f = font(11, 800)
    text = str(n)
    w = max(20.0, QFontMetricsF(f).horizontalAdvance(text) + 14)
    pill = QRectF(x, cy - 9, w, 18)
    p.fillPath(_pill(pill), fill)
    p.setFont(f)
    p.setPen(ink)
    p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


def paint_lines(p: QPainter, text: str, rect: QRectF, f: QFont, color: QColor, max_lines: int) -> float:
    """``text`` wrapped into ``rect`` from its top, up to ``max_lines``; the height used."""
    fm = QFontMetricsF(f)
    lines = _wrap(text, fm, rect.width(), max_lines) if text else []
    p.setFont(f)
    p.setPen(color)
    y = rect.top()
    for line in lines:
        p.drawText(QRectF(rect.left(), y, rect.width(), fm.height()), Qt.AlignmentFlag.AlignVCenter, line)
        y += fm.height()
    return y - rect.top()


def _check(p: QPainter, centre: QPointF, r: float, *, fill: QColor | None = None, tick: QColor | None = None) -> None:
    """A round mint tick (done)."""
    p.setPen(Qt.PenStyle.NoPen)
    if fill is None:
        grad = QLinearGradient(QPointF(centre.x(), centre.y() - r), QPointF(centre.x(), centre.y() + r))
        for at, c in zip((0.0, 0.6, 1.0), MINT):
            grad.setColorAt(at, QColor(c))
        p.setBrush(QBrush(grad))
    else:
        p.setBrush(fill)
    p.drawEllipse(centre, r, r)
    s = r * 0.55
    paint_icon(p, "check", QRectF(centre.x() - s, centre.y() - s, 2 * s, 2 * s), tick or QColor("#FFFFFF"), 3.4)


class Tile(Tactile, QAbstractButton):
    """A big colourful tile on Home (Do it for me): a glossy gradient, an icon, a title
    and a line. It lifts under the mouse, its shadow glowing in its own colour, and
    sinks when pressed."""

    def __init__(self, title: str, line: str, colors: tuple[str, str, str], *, icon: str = "",
                 ink: str = "#FFFFFF", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(title)
        self.setAccessibleName(title)
        self.line = line
        self.colors = colors
        self.icon = icon
        self.ink = QColor(ink)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tactile_init()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        hover, press = self.val("hover"), self.val("press")
        rect = QRectF(self.rect()).adjusted(4, 6, -4, -12).translated(0, -3.0 * hover * (1 - press))
        self.squish(p, rect, 0.03)
        paint_tile_face(p, rect, self.colors, hover=hover)
        if self.icon:
            box = QRectF(rect.left() + 16, rect.top() + 16, 42, 42)
            p.fillPath(ui_kit.squircle(box, 0.32), QColor(255, 255, 255, 58))
            ui_kit.paint_icon(p, self.icon, box.adjusted(10, 10, -10, -10), self.ink, 2.3)
        lf = font(12.5, 600)
        lfm = QFontMetricsF(lf)
        lines = _wrap(self.line, lfm, rect.width() - 32, 2) if self.line else []
        y = rect.bottom() - 16 - len(lines) * (lfm.height() + 1)
        p.setFont(font(18, 800, -0.01))
        p.setPen(self.ink)
        p.drawText(QRectF(rect.left() + 16, y - 26, rect.width() - 32, 24), Qt.AlignmentFlag.AlignVCenter, self.text())
        faint = QColor(self.ink)
        faint.setAlphaF(0.88)
        paint_lines(p, self.line, QRectF(rect.left() + 16, y, rect.width() - 32, 99), lf, faint, 2)
        p.end()


class InkButton(Tactile, QPushButton):
    """A small pill on a colourful tile: solid ink ("Yes, go on") or soft white ("I'll do it")."""

    def __init__(self, text: str, fill, fg, parent: QWidget | None = None, *, px: float = 13) -> None:
        super().__init__(text, parent)
        self.fill, self.fg = QColor(fill), QColor(fg)
        self._font = font(px, 800)
        self.setFont(self._font)
        self.setAccessibleName(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tactile_init()
        self.resize(self.sizeHint())

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetricsF(self._font)
        return QSize(round(fm.horizontalAdvance(self.text()) + 28), round(fm.height() + 14))

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect())
        self.squish(p, rect, 0.05)
        fill = QColor(self.fill)
        if fill.alpha() == 255:
            fill = _mix(fill, QColor(255, 255, 255), 0.14 * self.val("hover"))
        else:
            fill.setAlphaF(min(1.0, fill.alphaF() + 0.18 * self.val("hover")))
        p.fillPath(_pill(rect), fill)
        p.setFont(self._font)
        p.setPen(self.fg)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class SideTile(QWidget):
    """A colourful tile in Home's right column. Its face sits inside room for its
    shadow, like ``Tile``'s; subclasses paint their words and own their buttons."""

    def __init__(self, colors, ink: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.colors = colors
        self.ink = QColor(ink)

    def face(self) -> QRectF:
        return QRectF(self.rect()).adjusted(4, 6, -4, -12)

    def faint(self, alpha: float = 0.86) -> QColor:
        c = QColor(self.ink)
        c.setAlphaF(alpha)
        return c

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        face = self.face()
        paint_tile_face(p, face, self.colors)
        self.paint_words(p, face)
        p.end()

    def paint_words(self, p: QPainter, face: QRectF) -> None:
        pass

    def paint_title(self, p: QPainter, face: QRectF, title: str, line: str) -> None:
        """A title and a line at the tile's foot (an empty tile)."""
        lf = font(12.5, 600)
        lines = _wrap(line, QFontMetricsF(lf), face.width() - 32, 2)
        y = face.bottom() - 16 - len(lines) * QFontMetricsF(lf).height()
        p.setFont(font(18, 800, -0.01))
        p.setPen(self.ink)
        p.drawText(QRectF(face.left() + 16, y - 28, face.width() - 32, 26), Qt.AlignmentFlag.AlignVCenter, title)
        paint_lines(p, line, QRectF(face.left() + 16, y, face.width() - 32, 99), lf, self.faint(), 2)


class CallTile(SideTile):
    """Sunny, while a job runs. When a step is yours to say yes to (paying, sending,
    deleting): "Your call", with Yes, go on and I'll do it. Otherwise who's clicking,
    with a button to swap: "I'm clicking" (Let me click) or "You're clicking" (Do it for me)."""

    yes = pyqtSignal()
    mine = pyqtSignal()
    switch = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(SUNNY, SUNNY_INK, parent)
        self.info: dict = {}
        self.yes_btn = InkButton("Yes, go on", SUNNY_INK, "#FFFFFF", self)
        self.mine_btn = InkButton("I'll do it", QColor(255, 255, 255, 140), SUNNY_INK, self)
        self.switch_btn = InkButton("Do it for me", SUNNY_INK, "#FFFFFF", self)
        self.yes_btn.clicked.connect(self.yes)
        self.mine_btn.clicked.connect(self.mine)
        self.switch_btn.clicked.connect(self.switch)

    def set_info(self, info: dict) -> None:
        self.info = dict(info or {})
        asking = bool(self.info.get("asking"))
        driving = bool(self.info.get("driving"))
        self.yes_btn.setVisible(asking)
        self.mine_btn.setVisible(asking)
        self.switch_btn.setText("Let me click" if driving else "Do it for me")
        self.switch_btn.setAccessibleName(self.switch_btn.text())
        self.switch_btn.resize(self.switch_btn.sizeHint())
        self.switch_btn.setVisible(not asking and (driving or bool(self.info.get("can_drive", True))))
        self._place()
        self.update()

    def resizeEvent(self, _event) -> None:  # noqa: N802
        self._place()

    def _place(self) -> None:
        face = self.face()
        x, y = round(face.left() + 16), round(face.bottom() - 16 - self.yes_btn.height())
        self.yes_btn.move(x, y)
        self.mine_btn.move(x + self.yes_btn.width() + 8, y)
        self.switch_btn.move(x, y)

    def paint_words(self, p: QPainter, face: QRectF) -> None:
        x, w = face.left() + 16, face.width() - 32
        if self.info.get("asking"):
            cw = paint_caps(p, "Your call", x, face.top() + 16, self.ink)
            paint_count(p, 1, x + cw + 8, face.top() + 16 + 8, self.ink, QColor(255, 255, 255, 115))
            step = _sentence(self.info.get("now") or "This step").rstrip(".?!")
            used = paint_lines(p, f"{step}?", QRectF(x, face.top() + 42, w, 60), font(17, 800, -0.01), self.ink, 2)
            paint_lines(p, "That one's always yours to say yes to.", QRectF(x, face.top() + 46 + used, w, 40),
                        font(12.5, 600), self.faint(), 2)
            return
        driving = bool(self.info.get("driving"))
        paint_caps(p, "Doing it for you" if driving else "Showing you", x, face.top() + 16, self.ink)
        p.setFont(font(18, 800, -0.01))
        p.setPen(self.ink)
        p.drawText(QRectF(x, face.top() + 40, w, 26), Qt.AlignmentFlag.AlignVCenter,
                   "I'm clicking" if driving else "You're clicking")
        paint_lines(p, "Touch the mouse and it's yours again" if driving else "I point, you click, I see each step",
                    QRectF(x, face.top() + 68, w, 40), font(12.5, 600), self.faint(), 2)


class UpNextTile(SideTile):
    """Violet, while a job runs: the jobs waiting their turn (jobs.queued()), in order."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(VIOLET, "#FFFFFF", parent)
        self.jobs: list[str] = []

    def set_jobs(self, queued: list[str]) -> None:
        self.jobs = list(queued)
        self.update()

    def paint_words(self, p: QPainter, face: QRectF) -> None:
        x, w = face.left() + 16, face.width() - 32
        if not self.jobs:
            paint_caps(p, "Up next", x, face.top() + 16, self.ink)
            self.paint_title(p, face, "Nothing queued", "Type a job below and it joins the line")
            return
        cw = paint_caps(p, "Up next", x, face.top() + 16, self.ink)
        paint_count(p, len(self.jobs), x + cw + 8, face.top() + 16 + 8, self.ink, QColor(255, 255, 255, 56))
        y = face.top() + 44
        room = int((face.bottom() - 14 - y + 8) // 54)
        shown = self.jobs[:max(1, room)]
        for i, job in enumerate(shown):
            row = QRectF(x, y, w, 46)
            shape = QPainterPath()
            shape.addRoundedRect(row, 14, 14)
            p.fillPath(shape, QColor(255, 255, 255, 40))
            p.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
            p.drawLine(QPointF(row.left() + 12, row.top() + 0.5), QPointF(row.right() - 12, row.top() + 0.5))
            badge = QRectF(row.left() + 9, row.top() + 9, 28, 28)
            p.fillPath(ui_kit.squircle(badge, 0.32), QColor(255, 255, 255, 230))
            p.setFont(font(13, 800))
            p.setPen(QColor(VIOLET[2]))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            tf = font(13.5, 800)
            p.setFont(tf)
            p.setPen(self.ink)
            tw = row.width() - 56
            p.drawText(QRectF(row.left() + 46, row.top() + 5, tw, 20), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(tf).elidedText(_sentence(job), Qt.TextElideMode.ElideRight, tw))
            p.setFont(font(11.5, 600))
            p.setPen(self.faint(0.85))
            p.drawText(QRectF(row.left() + 46, row.top() + 24, tw, 16), Qt.AlignmentFlag.AlignVCenter,
                       "when this one's done" if i == 0 else "after that")
            y += 54
        if len(self.jobs) > len(shown):
            p.setFont(font(12, 700))
            p.setPen(self.faint())
            p.drawText(QRectF(x, face.bottom() - 30, w, 18), Qt.AlignmentFlag.AlignVCenter,
                       f"and {len(self.jobs) - len(shown)} more")


class DoneTile(SideTile):
    """Mint, when nothing runs: what got done today, newest first. A note in the list
    opens its card again (``open_note``)."""

    open_note = pyqtSignal(object)

    ROW = 24.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(MINT, MINT_INK, parent)
        self.items: list[dict] = []
        self.setMouseTracking(True)

    def set_items(self, items: list[dict]) -> None:
        self.items = list(items)
        self.update()

    def _rows(self) -> list[tuple[QRectF, dict]]:
        face = self.face()
        y = face.top() + 44
        rows = []
        for item in self.items:
            if y + self.ROW > face.bottom() - 30:
                break
            rows.append((QRectF(face.left() + 16, y, face.width() - 32, self.ROW), item))
            y += self.ROW
        return rows

    def _note_at(self, pos: QPointF):
        for rect, item in self._rows():
            if rect.contains(pos) and item.get("note") is not None:
                return item["note"]
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        on = self._note_at(event.position()) is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        note = self._note_at(event.position())
        if note is not None:
            sound("press")
            self.open_note.emit(note)

    def paint_words(self, p: QPainter, face: QRectF) -> None:
        x = face.left() + 16
        cw = paint_caps(p, "Done today", x, face.top() + 16, self.ink)
        if not self.items:
            self.paint_title(p, face, "Nothing yet today", "The jobs I finish and the notes I show land here")
            return
        paint_count(p, len(self.items), x + cw + 8, face.top() + 16 + 8, self.ink, QColor(255, 255, 255, 115))
        rows = self._rows()
        tf = font(13, 800)
        for rect, item in rows:
            _check(p, QPointF(rect.left() + 7, rect.center().y()), 7, fill=QColor(255, 255, 255, 120), tick=self.ink)
            p.setFont(tf)
            p.setPen(self.ink)
            p.drawText(QRectF(rect.left() + 22, rect.top(), rect.width() - 22, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(tf).elidedText(item["label"], Qt.TextElideMode.ElideRight, rect.width() - 22))
        if len(self.items) > len(rows):
            p.setFont(font(12, 700))
            p.setPen(self.faint())
            p.drawText(QRectF(x, face.bottom() - 30, face.width() - 32, 18), Qt.AlignmentFlag.AlignVCenter,
                       f"and {len(self.items) - len(rows)} more")


class IdeaButton(Tactile, QAbstractButton):
    """A job to do again (or to try), on the idle card: a small gel play key and its words."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tactile_init()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        sk = skin()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = self.val("hover")
        self.squish(p, rect, 0.02)
        shape = QPainterPath()
        shape.addRoundedRect(rect, 16, 16)
        base = sk.canvas_top if not sk.dark else sk.raised
        p.fillPath(shape, _mix(base, accent(), 0.07 * hover))
        p.setPen(QPen(_mix(sk.border, accent(), 0.4 * hover), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(shape)
        key = QRectF(rect.left() + 10, rect.center().y() - 13, 26, 26)
        paint_glossy(p, key, accent(), radius=9)
        tri = QPainterPath(QPointF(key.left() + 10, key.top() + 7.5))
        tri.lineTo(key.left() + 19, key.center().y())
        tri.lineTo(key.left() + 10, key.bottom() - 7.5)
        tri.closeSubpath()
        p.fillPath(tri, on_accent(accent()))
        tf = font(14, 700)
        p.setFont(tf)
        p.setPen(sk.text)
        tw = rect.width() - 60
        p.drawText(QRectF(key.right() + 12, rect.top(), tw, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(tf).elidedText(self.text(), Qt.TextElideMode.ElideRight, tw))
        p.end()


class LiveCard(QWidget):
    """Home's big card. While a job runs: LIVE NOW, who's clicking, when it started,
    the job, the steps (the last ones done, the one now, the next one planned) and
    your screen as Clixy last saw it with a ring on what it's about to click, then
    I did it (you're clicking) or Take over (Clixy is), and Stop. When nothing runs:
    "Give me a job and watch me do it" and up to three jobs to do again."""

    TOP, SIDE, BOTTOM = 34.0, 14.0, 22.0     # room for the buddy peeking over it and for its shadow
    IDEA_H = 44

    primary = pyqtSignal()
    stop = pyqtSignal()
    picked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info: dict = {}
        self.ideas: list[str] = []
        self.mine = False
        self.primary_btn = PanelButton("I did it", "glossy", self, px=14, weight=800, pad=(10, 18))
        self.stop_btn = PanelButton("Stop", "frost", self, px=14, weight=700, pad=(9, 16))
        self.primary_btn.clicked.connect(self.primary)
        self.stop_btn.clicked.connect(self.stop)
        self.idea_btns = [IdeaButton(self) for _ in range(3)]
        for b in self.idea_btns:
            b.clicked.connect(lambda _c=False, b=b: self.picked.emit(b.text()))
        self._picture: QImage | None = None      # the screen, cut and scaled for the frame
        self._picture_key = None
        self._ring: QRectF | None = None
        self.set_live({})

    def card(self) -> QRectF:
        return QRectF(self.SIDE, self.TOP, self.width() - 2 * self.SIDE, self.height() - self.TOP - self.BOTTOM)

    def set_live(self, info: dict | None) -> None:
        self.info = dict(info or {})
        live = bool(self.info)
        driving = bool(self.info.get("driving"))
        self.primary_btn.setText("Take over" if driving else "I did it")
        self.primary_btn.setAccessibleName(self.primary_btn.text())
        self.primary_btn.resize(self.primary_btn.sizeHint())
        self.primary_btn.setVisible(live)
        self.stop_btn.setVisible(live)
        for i, b in enumerate(self.idea_btns):
            b.setVisible(not live and i < len(self.ideas))
        self._place()
        self.update()

    def set_ideas(self, ideas: list[str], mine: bool) -> None:
        self.ideas, self.mine = [_sentence(i) for i in ideas[:3]], mine
        for i, b in enumerate(self.idea_btns):
            text = self.ideas[i] if i < len(self.ideas) else ""
            b.setText(text)
            b.setAccessibleName(text)
        self.set_live(self.info)

    def resizeEvent(self, _event) -> None:  # noqa: N802
        self._picture_key = None
        self._place()

    def _place(self) -> None:
        card = self.card()
        x = round(card.left() + 18)
        y = round(card.bottom() - 18 - self.primary_btn.height())
        self.primary_btn.move(x, y)
        self.stop_btn.move(x + self.primary_btn.width() + 8, y + (self.primary_btn.height() - self.stop_btn.height()) // 2)
        n = len(self.ideas)
        top = card.bottom() - 18 - n * self.IDEA_H - max(0, n - 1) * 8
        for i, b in enumerate(self.idea_btns):
            b.setGeometry(x, round(top + i * (self.IDEA_H + 8)), round(card.width() - 36), self.IDEA_H)

    # --- The screen, as Clixy last saw it ---------------------------------------------

    def _frame(self) -> QRectF:
        card = self.card()
        top = card.top() + 16 + 24 + 36
        bottom = card.bottom() - 18 - self.primary_btn.height() - 16
        return QRectF(card.left() + 18 + 212, top, card.width() - 36 - 212, max(40.0, bottom - top))

    def _cut(self, frame: QRectF) -> None:
        """Cut the part of the screen around the target, sized for the frame (once per look)."""
        view = self.info.get("view") or (None, None)
        key = (id(view[0]), view[1], round(frame.width()), round(frame.height()))
        if key == self._picture_key:
            return
        self._picture_key, self._picture, self._ring = key, None, None
        image = _qimage(view[0])
        if image is None or image.isNull():
            return
        iw, ih = image.width(), image.height()
        aspect = frame.width() / frame.height()
        box = view[1]
        if box:
            bw = max(1.0, box[2] - box[0])
            cw = min(float(iw), max(bw * 3.2, iw * 0.34))
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        else:
            cw, cx, cy = float(iw), iw / 2, ih / 2
        ch = cw / aspect
        if ch > ih:
            ch, cw = float(ih), ih * aspect
        left = min(max(0.0, cx - cw / 2), iw - cw)
        top = min(max(0.0, cy - ch / 2), ih - ch)
        dpr = self.devicePixelRatioF()
        cut = image.copy(QRect(round(left), round(top), max(1, round(cw)), max(1, round(ch))))
        self._picture = cut.scaled(max(1, round(frame.width() * dpr)), max(1, round(frame.height() * dpr)),
                                   Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._picture.setDevicePixelRatio(dpr)
        if box:
            k = frame.width() / cw
            self._ring = QRectF((box[0] - left) * k, (box[1] - top) * k, (box[2] - box[0]) * k, (box[3] - box[1]) * k)

    # --- Painting -----------------------------------------------------------------------

    def _t(self) -> float:
        win = self.window()
        return float(win.property("t") or 0.0) if win is not None else 0.0

    def buddy_box(self) -> QRect:
        card = self.card()
        if self.info:
            return QRectF(card.right() - 28 - 50, 0, 54, card.top() + 20).toAlignedRect()
        return QRectF(card.right() - 190, card.top(), 190, 170).toAlignedRect()

    def _buddy(self, p: QPainter, rect: QRectF, cells: float) -> None:
        dpr = self.devicePixelRatioF()
        device = max(1, int(cells * dpr + 0.25))      # whole device pixels per sprite cell
        theme.paint_avatar_in(p, rect, t=self._t(), scale=device / (2 * dpr), device_pixel_ratio=dpr)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        sk = skin()
        card = self.card()
        shape = QPainterPath()
        shape.addRoundedRect(card, 24, 24)
        ui_kit.soft_shadow(p, shape, dy=8, spread=14, color=sk.shadow)
        p.fillPath(shape, sk.surface)
        p.setPen(QPen(sk.border, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 23.5, 23.5)
        if self.info:
            self._paint_live(p, card, sk)
        else:
            self._paint_idle(p, card, sk)
        p.end()

    def _strong(self, sk) -> QColor:
        """The accent as text: deeper on Daylight, lighter on Charcoal."""
        acc = accent()
        return gloss(acc)[0] if sk.dark else _mix(acc, QColor("#000000"), 0.22)

    def _paint_live(self, p: QPainter, card: QRectF, sk) -> None:
        info, acc = self.info, accent()
        self._buddy(p, QRectF(card.right() - 28 - 46, card.top() - 34, 46, 48), 3.0)
        x0, y = card.left() + 18, card.top() + 16
        cw = paint_caps(p, "Live now", x0, y, self._strong(sk))
        driving = bool(info.get("driving"))
        chip_text = "Doing it for you" if driving else "Showing you"
        cf = font(11.5, 800)
        chip = QRectF(x0 + cw + 10, y - 2, QFontMetricsF(cf).horizontalAdvance(chip_text) + 18, 20)
        p.fillPath(_pill(chip), _rgba(sk.text, 0.07 if not sk.dark else 0.09))
        p.setFont(cf)
        p.setPen(sk.text2)
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)
        if info.get("started"):
            p.setFont(font(12, 700))
            p.setPen(sk.text3)
            p.drawText(QRectF(card.right() - 18 - 160, y - 2, 160, 20),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"started {_clock(info['started'])}")
        tf = font(22, 800, -0.02)
        p.setFont(tf)
        p.setPen(sk.text)
        tw = card.width() - 36
        p.drawText(QRectF(x0, y + 22, tw, 32), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(tf).elidedText(_sentence(info.get("goal") or "Your job"), Qt.TextElideMode.ElideRight, tw))
        frame = self._frame()
        self._paint_steps(p, QRectF(x0, frame.top() + 2, 196, frame.height()), sk)
        self._paint_screen(p, frame, sk)
        hint = ("Pointing only? Say “let me do it”" if driving else
                "Want me to click? Say “do it for me”" if info.get("can_drive", True) else "")
        if hint:
            hf = font(12.5, 700)
            left = self.stop_btn.geometry().right() + 16
            p.setFont(hf)
            p.setPen(sk.text3)
            rect = QRectF(left, self.primary_btn.y(), card.right() - 18 - left, self.primary_btn.height())
            p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(hf).elidedText(hint, Qt.TextElideMode.ElideLeft, rect.width()))

    def _paint_steps(self, p: QPainter, area: QRectF, sk) -> None:
        info, acc = self.info, accent()
        done = list(info.get("done") or [])
        n, total = int(info.get("n") or 0), int(info.get("total") or 0)
        plan = list(info.get("plan") or [])
        nxt = _sentence(plan[0]) if plan else (f"{total - n} more step{'s' if total - n != 1 else ''} after this"
                                               if total > n else "Then I check it worked")
        now_sub = "doing it now" if info.get("driving") else "your turn, I'm watching"
        rows = [("done", _sentence(d), "") for d in done[-2:]]
        rows.append(("now", _sentence(info.get("now") or "Looking at your screen"), now_sub))
        rows.append(("next", nxt, ""))
        lf, sf = font(13.5, 800), font(12, 600)
        lfm, sfm = QFontMetricsF(lf), QFontMetricsF(sf)
        y = area.top()
        dot_x = area.left() + 11
        beat = 0.5 + 0.5 * math.sin(self._t() * 5.0)
        for i, (kind, label, sub) in enumerate(rows):
            lines = _wrap(label, lfm, area.width() - 34, 2)
            h = max(22.0, len(lines) * lfm.height() + (sfm.height() if sub else 0)) + 14
            if y + 22 > area.bottom():
                break
            centre = QPointF(dot_x, y + 11)
            if i < len(rows) - 1:                 # the line down to the next step
                p.setPen(QPen(QColor(MINT[1]) if kind == "done" else sk.track, 2.0))
                p.drawLine(QPointF(dot_x, y + 24), QPointF(dot_x, min(area.bottom(), y + h)))
            if kind == "done":
                _check(p, centre, 11)
                colour, f = sk.text, lf
            elif kind == "now":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_rgba(acc, 0.28 * (1 - beat)))
                p.drawEllipse(centre, 11 + 7 * beat, 11 + 7 * beat)
                paint_glossy(p, QRectF(centre.x() - 11, centre.y() - 11, 22, 22), acc, radius=11)
                colour, f = self._strong(sk), lf
            else:
                p.setPen(QPen(_rgba(sk.text3, 0.7), 2.0, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(centre, 10, 10)
                colour, f = sk.text2, font(13.5, 700)
            ty = y + 2
            p.setFont(f)
            p.setPen(colour)
            for line in lines:
                p.drawText(QRectF(area.left() + 34, ty, area.width() - 34, lfm.height()),
                           Qt.AlignmentFlag.AlignVCenter, line)
                ty += lfm.height()
            if sub:
                p.setFont(sf)
                p.setPen(sk.text3)
                p.drawText(QRectF(area.left() + 34, ty, area.width() - 34, sfm.height()),
                           Qt.AlignmentFlag.AlignVCenter, sub)
            y += h

    def _paint_screen(self, p: QPainter, frame: QRectF, sk) -> None:
        acc = accent()
        shape = QPainterPath()
        shape.addRoundedRect(frame, 16, 16)
        ui_kit.soft_shadow(p, shape, dy=5, spread=10, color=_rgba(sk.shadow, sk.shadow.alphaF() * 0.6))
        self._cut(frame)
        if self._picture is None:
            p.fillPath(shape, sk.raised)
            p.setFont(font(13, 700))
            p.setPen(sk.text3)
            p.drawText(frame, Qt.AlignmentFlag.AlignCenter, "Looking at your screen…")
        else:
            p.save()
            p.setClipPath(shape)
            p.drawImage(frame.topLeft(), self._picture)
            if self._ring is not None:
                ring = self._ring.translated(frame.topLeft()).adjusted(-4, -4, 4, 4)
                p.setPen(QPen(_rgba(acc, 0.28), 6.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(ring, 9, 9)
                p.setPen(QPen(acc, 2.2))
                p.drawRoundedRect(ring, 9, 9)
                tip = QPointF(ring.center().x() + min(26.0, ring.width() / 2 - 4), ring.center().y() + 4)
                arrow = QPainterPath(tip)
                for dx, dy in ((14, 8), (7.8, 9.6), (5, 16)):
                    arrow.lineTo(tip.x() + dx, tip.y() + dy)
                arrow.closeSubpath()
                p.setPen(QPen(QColor("#FFFFFF"), 1.6))
                p.setBrush(acc)
                p.drawPath(arrow)
            p.restore()
        p.setPen(QPen(sk.border, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(frame.adjusted(0.5, 0.5, -0.5, -0.5), 15.5, 15.5)
        if self._picture is not None:
            cf = font(11, 800)
            text = "Live from your screen"
            chip = QRectF(frame.left() + 10, frame.bottom() - 10 - 22,
                          QFontMetricsF(cf).horizontalAdvance(text) + 32, 22)
            p.fillPath(_pill(chip), QColor(18, 19, 24, 190))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FF5A5A"))
            p.drawEllipse(QPointF(chip.left() + 12, chip.center().y()), 3, 3)
            p.setFont(cf)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(chip.adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_idle(self, p: QPainter, card: QRectF, sk) -> None:
        acc = accent()
        centre = QPointF(card.right() - 96, card.top() + 84)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_rgba(acc, 0.13 if not sk.dark else 0.16))
        p.drawEllipse(centre, 66, 66)
        bob = 3.0 * math.sin(self._t() * 2.0)
        self._buddy(p, QRectF(centre.x() - 50, centre.y() - 52 + bob, 100, 104), 6.0)
        x0, w = card.left() + 22, card.width() - 44 - 170
        paint_caps(p, "Nothing running", x0, card.top() + 22, self._strong(sk))
        used = paint_lines(p, "Give me a job and watch me do it", QRectF(x0, card.top() + 44, w, 80),
                           font(28, 800, -0.03), sk.text, 2)
        paint_lines(p, "Say “for me” and I click and type it through. Leave it out and I point the way while you click.",
                    QRectF(x0, card.top() + 54 + used, w, 66), font(14, 600), sk.text2, 3)
        if self.ideas:
            top = self.idea_btns[0].y()
            paint_caps(p, "Again, from your week" if self.mine else "Try one", x0, top - 26, sk.text3)


class HomePage(Motion, QWidget):
    """Home, as board J (Rayan, 4 Nov 2026): what Clixy is doing now and what's waiting,
    no note card (drawings show on the screen when asked). The top bar; a hello (or "On
    it" with how to take over); the big live card; on the right, while a job runs, a
    sunny tile (Your call, or who's clicking) and the violet Up next, with today's done
    jobs in a strip below; when nothing runs, the idle card with jobs to do again, the
    violet Do it for me and the mint Done today. Then the ask bar, with What Clixy
    remembers inside it. The buddy in the top bar hops when you point at it."""

    SIZE = HOME
    GRID_TOP = 150.0
    GAP = 12.0
    STRIP = 34.0

    def __init__(self, panel: "ClixyPanel") -> None:
        super().__init__(panel._body)
        self.panel = panel
        self.resize(int(HOME[0]), int(HOME[1]))
        self.settings_btn = PanelButton("", "frost", self, icon="sliders", round_=38)
        self.settings_btn.setAccessibleName("Settings")
        self.settings_btn.setToolTip("Settings")
        self.live = LiveCard(self)
        self.call = CallTile(self)
        self.next = UpNextTile(self)
        self.do_it = Tile("Do it for me", "Type a job and watch it happen", VIOLET, icon="cursor", parent=self)
        self.done = DoneTile(self)
        self.ask = AskField(self)
        self.memory = PanelButton("What Clixy remembers", "text", None, px=12.5, weight=800, pad=(6, 8))
        self.ask.set_extra(self.memory)
        self.info: dict | None = None
        self.queue: list[str] = []
        self.done_items: list[dict] = []
        self.setMouseTracking(True)
        self._on_buddy = False
        self.spring("hop", 0.0, 260.0, 9.0)
        self._show_mode()

    def set_state(self, info: dict | None, queue: list[str], done_items: list[dict], ideas: list[str],
                  mine: bool = False) -> None:
        """Everything Home shows: the job running (or None), the line waiting, today's
        done jobs and notes, and the jobs to offer again (``mine``: from his own week)."""
        self.done_items = list(done_items)
        self.done.set_items(self.done_items)
        self.live.set_ideas(ideas, mine)
        self.set_live(info, queue)

    def set_live(self, info: dict | None, queue: list[str]) -> None:
        """The running job changed (a step, who's clicking, a yes wanted) or the line did."""
        was = bool(self.info)
        self.info = dict(info) if info else None
        self.queue = list(queue)
        self.live.set_live(self.info)
        self.call.set_info(self.info or {})
        self.next.set_jobs(self.queue)
        if was != bool(self.info):
            self._show_mode()
        self.update()

    def _show_mode(self) -> None:
        live = bool(self.info)
        for w in (self.call, self.next):
            w.setVisible(live)
        for w in (self.do_it, self.done):
            w.setVisible(not live)
        self.ask.edit.setPlaceholderText("Add a job, or ask anything" if live else AskField.PLACEHOLDER)
        self._layout()

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

    def grid_bottom(self) -> float:
        return self.height() - 20 - 50 - 14 - ((self.STRIP + 12) if self.info else 0)

    def _layout(self) -> None:
        w, h = self.width(), self.height()
        inner = w - 44
        col = (inner - 2 * self.GAP) / 3
        ask_h = 50
        bottom = self.grid_bottom()
        row = (bottom - self.GRID_TOP - self.GAP) / 2
        self.settings_btn.setGeometry(w - 22 - 38, 15, 38, 38)
        big_w = 2 * col + self.GAP
        c = LiveCard
        self.live.setGeometry(round(22 - c.SIDE), round(self.GRID_TOP - c.TOP), round(big_w + 2 * c.SIDE),
                              round(bottom - self.GRID_TOP + c.TOP + c.BOTTOM))
        x = 22 + big_w + self.GAP
        top = (round(x - 4), round(self.GRID_TOP - 6), round(col + 8), round(row + 18))
        low = (round(x - 4), round(self.GRID_TOP + row + self.GAP - 6), round(col + 8), round(row + 18))
        self.call.setGeometry(*top)
        self.do_it.setGeometry(*top)
        self.next.setGeometry(*low)
        self.done.setGeometry(*low)
        self.ask.setGeometry(22, round(h - 20 - ask_h), inner, ask_h)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        sk = skin()
        paint_top_bar(p, self, self.panel.status_text(), hop=self.val("hop"))
        live = bool(self.info)
        f = font(30 if live else 32, 800, -0.03)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(sk.text)
        name = self.panel.name
        hello = (f"On it, {name}" if name else "On it") if live else _greeting(name)
        p.drawText(QRectF(22, 62, self.width() - 44, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   hello)
        line_y = 62 + fm.height() + 16
        if live:
            first = ("Touch the mouse to take over, or press" if self.info.get("driving")
                     else "Do each step and I'll see it, or press")
            paint_key_line(p, [("text", first), ("key", "Esc"), ("text", "to stop")], QPointF(0, line_y),
                           px=14.5, color=sk.text2, left=24)
            self._paint_strip(p, sk)
        else:
            keys = [k for k in self.panel.hotkey.split(" + ") if k]
            segments: list[tuple[str, str]] = [("text", "Hold")] + [("key", k) for k in keys] + \
                [("text", "and ask, or type below")]
            t = float(self.window().property("t") or 0.0) if self.window() else 0.0
            paint_key_line(p, segments, QPointF(0, line_y), px=14.5, color=sk.text2, press_at=t, left=24)
        p.end()

    def _paint_strip(self, p: QPainter, sk) -> None:
        """Today's done jobs and notes as chips under the grid, as many as fit."""
        y = self.grid_bottom() + 12
        cy = y + self.STRIP / 2
        x = 22 + paint_caps(p, "Done today", 22, cy - 8, sk.text3) + 14
        if not self.done_items:
            p.setFont(font(12.5, 700))
            p.setPen(sk.text3)
            p.drawText(QRectF(x, y, 300, self.STRIP), Qt.AlignmentFlag.AlignVCenter, "nothing yet")
            return
        cf = font(12.5, 700)
        cfm = QFontMetricsF(cf)
        right = self.width() - 22
        for i, item in enumerate(self.done_items):
            label = cfm.elidedText(item["label"], Qt.TextElideMode.ElideRight, 230)
            w = cfm.horizontalAdvance(label) + 8 + 16 + 8 + 12
            more = len(self.done_items) - i
            if x + w > right - (40 if more > 1 else 0):
                p.setFont(font(12.5, 800))
                p.setPen(sk.text3)
                p.drawText(QRectF(x, y, 60, self.STRIP), Qt.AlignmentFlag.AlignVCenter, f"+{more}")
                return
            chip = QRectF(x, cy - 15, w, 30)
            p.fillPath(_pill(chip), sk.surface)
            p.setPen(QPen(sk.border, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(_pill(chip.adjusted(0.5, 0.5, -0.5, -0.5)))
            _check(p, QPointF(chip.left() + 16, cy), 8)
            p.setFont(cf)
            p.setPen(sk.text)
            p.drawText(QRectF(chip.left() + 30, chip.top(), w - 30, chip.height()), Qt.AlignmentFlag.AlignVCenter, label)
            x += w + 8


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
        self.settings = None
        """The Settings view (panel_settings.SettingsPage), made the first time."""
        self.view = "home"

        self.home.live.primary.connect(self._primary)
        self.home.live.stop.connect(self._stop)
        self.home.live.picked.connect(self._ask)
        self.home.call.yes.connect(self._approve)
        self.home.call.mine.connect(lambda: self._drive(False))
        self.home.call.switch.connect(self._switch_drive)
        self.home.do_it.clicked.connect(self._do_it_for_me)
        self.home.done.open_note.connect(self._open_note)
        self.home.memory.clicked.connect(lambda: self._open_folder(memory_dir()))
        self.home.settings_btn.clicked.connect(self._settings)
        self.home.ask.submitted.connect(self._ask)

        self._w, self._h = NOTCH
        self._vw = self._vh = 0.0
        self._fade = 0.0
        self._open = False
        self._hidden_for_capture = False
        self._was_down = True
        self._was_esc = True
        self._live = None                # what the live card last showed (_live_key)
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
        if not self._guide_info():       # a job watches the front window: leave it be (click the ask bar to type)
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
        self._for_me = False
        self.home.ask.edit.setPlaceholderText(AskField.PLACEHOLDER)
        if self.settings is not None and self.view == "settings":
            self.settings.discard()      # closing without Done keeps the old settings
        if instant:
            self._finish_close()

    def _finish_close(self) -> None:
        self._tick.stop()
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
        """Home afresh: the job running (or not), the line waiting, today's done jobs
        and notes, and the jobs to offer again."""
        import jobs
        info = self._guide_info()
        ideas = jobs.again()
        self.home.set_state(info, jobs.queued(), self._done_items(), ideas or list(EXAMPLES), mine=bool(ideas))
        self._live = _live_key(info)
        self._set_view("home")
        self.home.update()

    def _done_items(self) -> list[dict]:
        """Today's finished jobs and the notes shown today, newest first."""
        import jobs
        today = dt.date.today()
        items = [{"label": _sentence(j["goal"]), "note": None, "at": j["at"]} for j in jobs.done_today()]
        items += [{"label": n.title, "note": n, "at": n.when} for n in load_notes(self.notes_folder)
                  if n.when.date() == today]
        return sorted(items, key=lambda i: i["at"], reverse=True)

    def show_settings(self, section: str = "") -> bool:
        """Turn the panel into Settings. False if they couldn't be read."""
        try:
            if self.settings is None:
                from panel_settings import SettingsPage
                self.settings = SettingsPage(self._body)
                self.settings.hide()
                self.settings.saved.connect(self._settings_done)
                self.settings.left.connect(lambda: self._set_view("home"))
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
        self._set_view("home")
        self.settings_saved.emit(restart)

    def _guide_info(self) -> dict | None:
        try:
            return self.guide_fn() if self.guide_fn else None
        except Exception:
            return None

    def status_text(self) -> str:
        try:
            return (self.status_fn() if self.status_fn else "") or "Ready"
        except Exception:
            return "Ready"

    def _set_view(self, view: str) -> None:
        if view != self.view:
            if self.view == "settings" and self.settings is not None:
                self.settings.discard()
            self.view = view
            self.home.setVisible(view == "home")
            if self.settings is not None:
                self.settings.setVisible(view == "settings")
            self._fade = min(self._fade, 0.0)
            self._fade_fx.setEnabled(True)
            self._fade_fx.setOpacity(0.0)

    def _target(self) -> tuple[float, float]:
        if not self._open:
            return NOTCH
        if self.view == "settings" and self.settings is not None:
            return float(self.settings.width()), float(self.settings.height())
        return HOME

    def _page(self) -> QWidget:
        if self.view == "settings" and self.settings is not None:
            return self.settings
        return self.home

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
                if self.home.info:
                    self.home.live.update()               # the step light pulses
                else:
                    self.home.update(QRect(0, 100, 600, 46))           # the keycaps press themselves
                    self.home.live.update(self.home.live.buddy_box())  # the buddy bobs
            if self._frame % 15 == 0 and self.view == "home":
                self._follow_job()

    def _follow_job(self) -> None:
        """Keep Home on the job: a new step, who's clicking, a yes wanted, the line; and
        when a job begins or ends, everything afresh (today's list, the ideas)."""
        import jobs
        info = self._guide_info()
        key, queue = _live_key(info), jobs.queued()
        if key == self._live and queue == self.home.queue:
            return
        if (key is None) != (self._live is None):
            self.refresh()
        else:
            self._live = key
            self.home.set_live(info, queue)

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
        """A typed question or job (or one of the jobs offered again). While a job runs it
        waits its turn in Up next instead (jobs.py); the app starts it when that one ends."""
        text = " ".join((text or "").split())
        if not text:
            return
        if getattr(self, "_for_me", False):
            import guide
            if not guide.wants_drive(text):
                text = f"{text.rstrip('.!? ')} for me"       # from the Do it for me tile: Clixy does it
            self._for_me = False
        self.home.ask.edit.clear()
        if self._guide_info():
            import jobs
            jobs.enqueue(text)
            sound("select")
            self.home.set_live(self._guide_info(), jobs.queued())
            return
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

    def _primary(self) -> None:
        """The live card's big button: Take over while Clixy clicks, I did it while you do."""
        g = _running_guide()
        if g is None:
            return
        if g.driving:
            g.set_drive(False)
        else:
            g.confirm()
        self._follow_job()

    def _approve(self) -> None:
        """Your call: yes, go on."""
        g = _running_guide()
        if g is not None:
            g.approve()
        self._follow_job()

    def _drive(self, on: bool) -> None:
        """Clixy clicks (on) or you do (off); switched on, it does the step on show now."""
        g = _running_guide()
        if g is None:
            return
        g.set_drive(on)
        if on:
            g.do_this_one()
        self._follow_job()

    def _switch_drive(self) -> None:
        g = _running_guide()
        if g is not None:
            self._drive(not g.driving)

    def _stop(self) -> None:
        """Stop the job, and the ones waiting after it."""
        import jobs
        jobs.clear_queue()
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
