"""The Clixy panel: one near-black panel that grows out of the notch.

Click the notch (island.py) and the panel drops down from the top of the
screen, the same black, its shoulders flaring into the screen edge like the
notch's own:

- Home: a hello with your real shortcut as keycaps; your newest sketch note on
  warm paper (the two before it tucked behind, tilted), with Open note and Ask
  a follow-up; All notes, What Clixy remembers, and a box to type a question.
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
    QTransform,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QLineEdit,
    QPushButton,
    QWidget,
)

import theme
from island import paint_light, tab_path

# --- Colours and sizes (from the approved mockups) -----------------------------------------------

BG = QColor("#0B0B0D")
TEXT = QColor("#F5F5F4")
MUTED = QColor("#A3A3A8")
CHIP = QColor("#17171B")
ON_FROST = QColor("#E7E7EA")
KEY_TEXT = QColor("#EDEDF0")
FIELD = QColor("#141417")
PAPER = QColor("#F4EFE6")
PAPER_BAND = QColor("#EAE3D6")
PAPER_DOT = QColor("#DCD3C3")
INK = QColor("#1D1B18")
BAND_TEXT = QColor("#6B655B")
NOTE_META = QColor("#7A7366")
PAPER_MUTED = QColor("#8A8274")
PAPER_RING = QColor("#CFC6B5")

NOTCH = (150.0, 24.0)          # where it grows from (island.SideIsland.NOTCH)
HOME = (880.0, 610.0)
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
    """Text on the accent: near-black tinted with it, or white on a deep one."""
    if theme.current_palette().on_flood.lightness() > 128:
        return QColor(255, 255, 255)
    return _mix(color, QColor(0, 0, 0), 0.88)


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
                 hover: bool = False, down: bool = False) -> None:
    """The glossy accent surface: gradient, a light 1 px rim and a bright top edge."""
    shape = _pill(rect, radius)
    top, mid, bottom = gloss(color)
    if hover and not down:
        top, mid, bottom = (_mix(c, QColor(255, 255, 255), 0.08) for c in (top, mid, bottom))
    if down:
        top, mid, bottom = (_mix(c, QColor(0, 0, 0), 0.08) for c in (top, mid, bottom))
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, top)
    grad.setColorAt(0.6, mid)
    grad.setColorAt(1.0, bottom)
    painter.fillPath(shape, QBrush(grad))
    painter.save()
    painter.setPen(QPen(QColor(255, 255, 255, 115), 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_pill(rect.adjusted(0.5, 0.5, -0.5, -0.5), None if radius is None else radius - 0.5))
    painter.restore()
    if not down:
        _inset_top(painter, shape, QColor(255, 255, 255, 178))


def paint_frost(painter: QPainter, rect: QRectF, *, radius: float | None = None, hover: bool = False,
                down: bool = False, top: str = "#24242A", bottom: str = "#18181C") -> None:
    """A frosted dark pill: a soft vertical gradient, a faint top highlight and ring."""
    shape = _pill(rect, radius)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    a, b = QColor(top), QColor(bottom)
    if hover:
        a, b = a.lighter(118), b.lighter(118)
    if down:
        a, b = b, b
    grad.setColorAt(0.0, a)
    grad.setColorAt(1.0, b)
    painter.fillPath(shape, QBrush(grad))
    painter.save()
    painter.setPen(QPen(QColor(255, 255, 255, 15), 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_pill(rect.adjusted(-0.5, -0.5, 0.5, 0.5), None if radius is None else radius + 0.5))
    painter.restore()
    if not down:
        _inset_top(painter, shape, QColor(255, 255, 255, 26))


def keycap_size(label: str, px: float = 12.0, pad=(8.0, 3.0, 4.0)) -> QSize:
    fm = QFontMetricsF(mono(px))
    w = fm.horizontalAdvance(label) + 2 * pad[0]
    h = fm.height() + pad[1] + pad[2]
    return QSize(round(max(w, h)), round(h))


def paint_keycap(painter: QPainter, rect: QRectF, label: str, *, px: float = 12.0, light: bool = False,
                 radius: float = 7.0, edge: float = 2.0) -> None:
    """A tactile keycap: a dark gradient (or paper-light) with a 2 px bottom edge."""
    painter.save()
    shape = _pill(rect, radius)
    if light:
        painter.fillPath(shape.translated(0, edge), QColor("#CFC6B5"))
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, QColor("#FFFFFF"))
        grad.setColorAt(1.0, QColor("#EFEAE1"))
        painter.fillPath(shape, QBrush(grad))
        painter.setPen(QPen(QColor("#D9D0BF"), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_pill(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5))
        painter.setPen(INK)
    else:
        painter.fillPath(shape.translated(0, edge), QColor("#050506"))
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, QColor("#2C2C33"))
        grad.setColorAt(1.0, QColor("#1F1F25"))
        painter.fillPath(shape, QBrush(grad))
        _inset_top(painter, shape, QColor(255, 255, 255, 31))
        painter.setPen(KEY_TEXT)
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

class PanelButton(QPushButton):
    """A button drawn like the mockups: ``glossy`` (the accent), ``frost``
    (dark frosted pill), ``paper`` (on the note card), ``text`` (quiet), or a
    round icon button (``icon`` with ``round_=True``)."""

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
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
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
            self._shadow.setColor(_rgba(accent(), 0.35))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        if self._round:
            return QSize(round(self._round), round(self._round))
        fm = QFontMetricsF(self._font)
        border = 2 if self._border or self.kind in ("glossy", "paper") else 0
        return QSize(round(fm.horizontalAdvance(self.text()) + 2 * self._pad[1] + border),
                     round(fm.height() + 2 * self._pad[0] + border))

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect())
        hover, down = self.underMouse() and self.isEnabled(), self.isDown()
        acc = accent()
        if self.kind == "glossy":
            paint_glossy(p, rect, acc, hover=hover, down=down)
            fg = on_accent(acc)
        elif self.kind == "paper":
            shape = _pill(rect)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, QColor("#FFFFFF") if not down else QColor("#F3EEE5"))
            grad.setColorAt(1.0, QColor("#F3EEE5") if not hover else QColor("#EFE8DC"))
            p.fillPath(shape, QBrush(grad))
            p.setPen(QPen(QColor("#DCD3C3"), 1.0))
            p.drawPath(_pill(rect.adjusted(0.5, 0.5, -0.5, -0.5)))
            fg = INK
        elif self.kind == "text":
            fg = TEXT if hover else MUTED
        else:
            paint_frost(p, rect.adjusted(1, 1, -1, -1) if not self._round else rect.adjusted(0.5, 0.5, -0.5, -0.5),
                        hover=hover, down=down)
            fg = ON_FROST if not hover else TEXT
        if self._icon:
            size = self._icon_px
            box = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size)
            paint_icon(p, self._icon, box, fg, self._icon_stroke)
        if self.text():
            p.setFont(self._font)
            p.setPen(fg)
            p.drawText(rect.translated(0, 1 if down else 0), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class AskField(QWidget):
    """The inset box to type a question, with a glossy send button."""

    submitted = pyqtSignal(str)
    PLACEHOLDER = "Type a question"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit(self)
        self.edit.setFrame(False)
        self.edit.setFont(font(14, 500))
        self.edit.setPlaceholderText(self.PLACEHOLDER)
        self.edit.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        pal = self.edit.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.Text, TEXT)
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#7C7C84"))
        pal.setColor(QPalette.ColorRole.Highlight, _rgba(accent(), 0.45))
        pal.setColor(QPalette.ColorRole.HighlightedText, TEXT)
        self.edit.setPalette(pal)
        # Spelled out in full: the app's stylesheet paints every QLineEdit dark on white.
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: 0; padding: 0; margin: 0; min-height: 0;"
            f" color: {TEXT.name()}; selection-color: #FFFFFF;"
            f" selection-background-color: {_rgba(accent(), 0.45).name(QColor.NameFormat.HexArgb)}; }}")
        self.edit.returnPressed.connect(self._submit)
        self.send = PanelButton("", "glossy", self, icon="arrow", round_=32, icon_px=15, icon_stroke=2.4)
        self.send.setAccessibleName("Ask")
        self.send.clicked.connect(self._submit)
        self.resize(330, 44)

    def resizeEvent(self, _event) -> None:  # noqa: N802
        h = self.height()
        self.send.setGeometry(self.width() - 6 - 32, (h - 32) // 2, 32, 32)
        self.edit.setGeometry(16, 0, self.width() - 16 - 8 - 6 - 32, h)

    def _submit(self) -> None:
        text = self.edit.text().strip()
        if text:
            self.submitted.emit(text)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = _pill(rect)
        p.fillPath(shape, FIELD)
        # inset 0 1px 2px rgba(0,0,0,.6): a soft dark line inside the top.
        p.save()
        p.setClipPath(shape)
        grad = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(0, 4))
        grad.setColorAt(0.0, QColor(0, 0, 0, 150))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 4), QBrush(grad))
        p.restore()
        ring = QColor(255, 255, 255, 18)
        if self.edit.hasFocus():
            ring = _rgba(accent(), 0.4)
        p.setPen(QPen(ring, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_pill(rect.adjusted(-0.5, -0.5, 0.5, 0.5)))
        p.end()


# --- Sketch notes ----------------------------------------------------------------------------------

@dataclass
class Note:
    """A saved sketch note: its picture, title and when it was made."""
    png: Path | None
    title: str
    when: dt.datetime
    followups: list = field(default_factory=list)


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
        notes.append(Note(png if png.exists() else None, title, when, list(meta.get("followups") or [])))
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


class NoteStack(QWidget):
    """The newest note on warm paper, the two before it tilted behind."""

    open_note = pyqtSignal(object)
    follow_up = pyqtSignal(object)

    CARD = QRectF(230, 0, 470, 330)
    BACK = ((QRectF(120, 70, 220, 160), -3.0, QColor("#EFEAE0")),
            (QRectF(60, 46, 210, 150), -8.0, QColor("#E9E3D8")))
    BAND = 36.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.notes: list[Note] = []
        self.index = 0
        self._drawings: dict = {}
        self.open_btn = PanelButton("Open note", "glossy", self, px=14, weight=800, pad=(9, 18))
        self.ask_btn = PanelButton("Ask a follow-up", "paper", self, px=14, weight=700, pad=(9, 16))
        self.open_btn.clicked.connect(lambda: self.current and self.open_note.emit(self.current))
        self.ask_btn.clicked.connect(lambda: self.current and self.follow_up.emit(self.current))
        self.setMouseTracking(True)
        self.resize(836, 330)
        self._place()

    @property
    def current(self) -> Note | None:
        return self.notes[self.index] if 0 <= self.index < len(self.notes) else None

    def set_notes(self, notes: list[Note]) -> None:
        self.notes = notes
        self.index = 0
        self._place()
        self.update()

    def _card(self) -> QRectF:
        behind = len(self.notes) - self.index - 1
        if not self.notes:
            return QRectF((self.width() - 470) / 2, 55, 470, 200)
        if behind <= 0:
            return QRectF((self.width() - 470) / 2, 0, 470, 330)
        return QRectF(self.CARD)

    def _backs(self) -> list[tuple[QRectF, float, QColor, Note]]:
        out = []
        for i, (rect, angle, colour) in enumerate(self.BACK):
            j = self.index + 1 + i
            if j < len(self.notes):
                out.append((rect, angle, colour, self.notes[j]))
        return out

    def _place(self) -> None:
        card = self._card()
        has = self.current is not None
        for b in (self.open_btn, self.ask_btn):
            b.setVisible(has)
        if not has:
            return
        self.open_btn.setEnabled(self.current.png is not None)
        y = card.bottom() - 18 - self.open_btn.height()
        self.open_btn.move(round(card.left() + 20), round(y))
        self.ask_btn.move(round(card.left() + 20 + self.open_btn.width() + 10),
                          round(y + (self.open_btn.height() - self.ask_btn.height()) / 2))

    def _drawing(self, note: Note) -> QImage | None:
        if note.png is None:
            return None
        key = str(note.png)
        if key not in self._drawings:
            if len(self._drawings) > 12:
                self._drawings.clear()
            self._drawings[key] = drawing_of(note.png)
        return self._drawings[key]

    # Navigating: click a card behind to bring it forward; the wheel moves through them.
    def _back_at(self, pos: QPointF) -> int | None:
        for i, (rect, angle, _c, _n) in enumerate(self._backs()):
            turn = QTransform().translate(rect.center().x(), rect.center().y()).rotate(angle) \
                .translate(-rect.center().x(), -rect.center().y())
            inverse, ok = turn.inverted()
            if ok and rect.contains(inverse.map(pos)) and not self._card().contains(pos):
                return i
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        over = self._back_at(event.position()) is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        i = self._back_at(event.position())
        if i is not None:
            self.show_index(self.index + 1 + i)

    def wheelEvent(self, event) -> None:  # noqa: N802
        step = -1 if event.angleDelta().y() > 0 else 1
        self.show_index(self.index + step)

    def show_index(self, i: int) -> None:
        if self.notes and 0 <= i < len(self.notes) and i != self.index:
            self.index = i
            self._place()
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        dpr = self.devicePixelRatioF()
        for rect, angle, colour, note in reversed(self._backs()):
            p.save()
            c = rect.center()
            p.translate(c)
            p.rotate(angle)
            p.translate(-c)
            shape = QPainterPath()
            shape.addRoundedRect(rect, 14, 14)
            draw_shadow(p, shape, dy=10, blur=24, color=QColor(0, 0, 0, 115), key=("back", rect.width(), rect.height()))
            p.fillPath(shape, colour)
            p.setFont(font(11, 700))
            p.setPen(NOTE_META)
            p.drawText(QRectF(rect.left() + 16, rect.top() + 14, rect.width() - 32, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"Note · {when_text(note.when)}")
            f = font(15, 800)
            p.setFont(f)
            p.setPen(INK)
            fm = QFontMetricsF(f)
            y = rect.top() + 14 + 16 + 6
            for line in _wrap(note.title, fm, rect.width() - 32, 4):
                p.drawText(QRectF(rect.left() + 16, y, rect.width() - 32, 15 * 1.25),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
                y += 15 * 1.25
            p.restore()

        card = self._card()
        shape = QPainterPath()
        shape.addRoundedRect(card, 18, 18)
        draw_shadow(p, shape, dy=18, blur=40, color=QColor(0, 0, 0, 140), key=("card", card.width(), card.height()))
        note = self.current
        paint_paper(p, card, 18, self.BAND, dpr)
        p.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(card.adjusted(-0.5, -0.5, 0.5, 0.5), 18.5, 18.5)

        band = QRectF(card.left() + 18, card.top(), card.width() - 36, self.BAND)
        p.setFont(font(12, 700))
        p.setPen(BAND_TEXT)
        if note is None:
            p.drawText(band, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Sketch notes")
            p.setFont(font(21, 800, -0.015))
            p.setPen(INK)
            p.drawText(QRectF(card.left() + 20, card.top() + self.BAND + 26, card.width() - 40, 30),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "No notes yet")
            p.setFont(font(15, 600))
            p.setPen(BAND_TEXT)
            p.drawText(QRectF(card.left() + 20, card.top() + self.BAND + 64, card.width() - 40, 80),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       "Sketch notes appear here when you ask Clixy to explain something.")
            p.end()
            return
        p.drawText(band, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"Sketch note · {when_text(note.when)}")
        if len(self.notes) > 1:
            p.drawText(band, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       f"{self.index + 1} of {len(self.notes)}")
        title_font = font(21, 800, -0.015)
        tfm = QFontMetricsF(title_font)
        top = card.top() + self.BAND + 14
        p.setFont(title_font)
        p.setPen(INK)
        p.drawText(QRectF(card.left() + 20, top, card.width() - 40, tfm.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   tfm.elidedText(note.title, Qt.TextElideMode.ElideRight, card.width() - 40))
        slot = QRectF(card.left() + 20, top + tfm.height() + 6, card.width() - 40,
                      self.open_btn.y() - 8 - (top + tfm.height() + 6))
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


# --- The pages -------------------------------------------------------------------------------------

def _greeting(name: str, now: dt.datetime | None = None) -> str:
    hour = (now or dt.datetime.now()).hour
    part = "Morning" if hour < 12 else "Afternoon" if hour < 17 else "Evening"
    return f"{part}, {name}." if name else f"Good {part.lower()}."


class HomePage(QWidget):
    """Home: the top bar, the hello and your shortcut, the newest sketch note,
    and the bottom row (All notes, What Clixy remembers, the ask box)."""

    SIZE = HOME

    def __init__(self, panel: "ClixyPanel") -> None:
        super().__init__(panel._body)
        self.panel = panel
        self.resize(int(HOME[0]), int(HOME[1]))
        self.notes_btn = PanelButton("", "frost", self, icon="pencil", round_=36)
        self.notes_btn.setAccessibleName("Notes")
        self.notes_btn.setToolTip("Your sketch notes")
        self.settings_btn = PanelButton("", "frost", self, icon="sliders", round_=36)
        self.settings_btn.setAccessibleName("Settings")
        self.settings_btn.setToolTip("Settings")
        self.stack = NoteStack(self)
        self.all_notes = PanelButton("All notes", "frost", self)
        self.memory = PanelButton("What Clixy remembers", "frost", self)
        self.ask = AskField(self)
        self._layout()

    def _layout(self) -> None:
        w, h = self.width(), self.height()
        self.settings_btn.setGeometry(w - 22 - 36, 16, 36, 36)
        self.notes_btn.setGeometry(w - 22 - 36 - 10 - 36, 16, 36, 36)
        self.stack.setGeometry(22, 178, w - 44, 330)
        self.ask.setGeometry(w - 22 - 330, h - 22 - 44, 330, 44)
        y = h - 22 - 44 + (44 - self.all_notes.height()) // 2
        self.all_notes.move(22, y)
        self.memory.move(22 + self.all_notes.width() + 10, y)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        paint_top_bar(p, self, self.panel.status_text())
        # The hello and your shortcut.
        f = font(32, 700, -0.025)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(TEXT)
        p.drawText(QRectF(0, 74, self.width(), fm.height()), Qt.AlignmentFlag.AlignCenter,
                   _greeting(self.panel.name))
        keys = [k for k in self.panel.hotkey.split(" + ") if k]
        segments: list[tuple[str, str]] = [("text", "Hold")] + [("key", k) for k in keys] + \
            [("text", "and ask, or type a question below.")]
        paint_key_line(p, segments, QPointF(self.width() / 2, 74 + fm.height() + 10 + 12), px=15)
        p.end()


def paint_top_bar(p: QPainter, page: QWidget, status: str) -> None:
    """The buddy, "Clixy" and a status chip, on the panel's first row (y 16-52)."""
    cy = 16 + 18
    dpr = page.devicePixelRatioF()
    device = max(1, int(1.5 * dpr + 0.25))      # whole device pixels per sprite cell
    t = (page.window().property("t") or 0.0) if page.window() else 0.0
    theme.paint_avatar_in(p, QRectF(22 - 4, cy - 13, 26, 26), t=float(t), scale=device / (2 * dpr),
                          device_pixel_ratio=dpr)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    x = 22 + 18 + 10
    f = font(15, 700)
    p.setFont(f)
    p.setPen(TEXT)
    w = QFontMetricsF(f).horizontalAdvance("Clixy")
    p.drawText(QRectF(x, cy - 12, w + 4, 24), Qt.AlignmentFlag.AlignVCenter, "Clixy")
    x += w + 10
    chip_font = font(12, 600)
    cw = QFontMetricsF(chip_font).horizontalAdvance(status)
    chip = QRectF(x, cy - 12, 10 + 6 + 6 + cw + 10, 24)
    p.fillPath(_pill(chip), CHIP)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(accent())
    p.drawEllipse(QPointF(chip.left() + 13, cy), 3, 3)
    p.setFont(chip_font)
    p.setPen(MUTED)
    p.drawText(QRectF(chip.left() + 22, chip.top(), cw + 4, chip.height()), Qt.AlignmentFlag.AlignVCenter, status)


def paint_key_line(p: QPainter, segments: list[tuple[str, str]], centre: QPointF, *, px: float = 15,
                   color: QColor = MUTED, light: bool = False) -> None:
    """A line of words and keycaps ("Hold [Ctrl] [Win] and ask"), centred on ``centre``."""
    f = font(px, 500)
    fm = QFontMetricsF(f)
    gap = 6.0
    widths = []
    for kind, text in segments:
        widths.append(keycap_size(text).width() if kind == "key" else fm.horizontalAdvance(text))
    total = sum(widths) + gap * (len(segments) - 1)
    x = centre.x() - total / 2
    for (kind, text), w in zip(segments, widths):
        if kind == "key":
            size = keycap_size(text)
            paint_keycap(p, QRectF(x, centre.y() - size.height() / 2 - 1, w, size.height()), text, light=light)
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
        # The first row: "Guiding you", the step chip, the light.
        cy = 16 + 12
        f = font(15, 700)
        p.setFont(f)
        p.setPen(TEXT)
        w = QFontMetricsF(f).horizontalAdvance("Guiding you")
        p.drawText(QRectF(20, cy - 12, w + 4, 24), Qt.AlignmentFlag.AlignVCenter, "Guiding you")
        n, total = int(info.get("n") or 0), int(info.get("total") or 0)
        chip_text = f"{n} of {total}" if total >= n > 0 else (f"Step {n}" if n else "")
        if chip_text:
            cf = mono(12)
            cw = QFontMetricsF(cf).horizontalAdvance(chip_text)
            chip = QRectF(20 + w + 10, cy - 12, cw + 20, 24)
            p.fillPath(_pill(chip), CHIP)
            p.setFont(cf)
            p.setPen(MUTED)
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, chip_text)
        paint_light(p, QPointF(self.width() - 20 - 5, cy), 4.2, 0.95, acc, 13.0)

        # The paper card.
        card = QRectF(20, 16 + 24 + 16, self.width() - 40, self.card_height())
        paint_paper(p, card, 18, 36, self.devicePixelRatioF())
        p.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
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
        self.home.notes_btn.clicked.connect(lambda: self._open_folder(self.notes_folder or notes_dir()))
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
        self._open = False
        self._follow_note = None
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
        """Fresh notes, guide progress and greeting."""
        self.home.stack.set_notes(load_notes(self.notes_folder))
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
            if self._frame % 6 == 0 and self.view == "home":
                self.home.update(QRect(14, 16, 30, 36))   # the buddy breathes
            if self._frame % 30 == 0 and self.view == "home":
                self.home.update(QRect(0, 0, 420, 60))    # the status chip
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
        page.move(round((w - page.width()) / 2), 0)

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
        big = max(0.0, min(1.0, (self._h - NOTCH[1]) / 120.0))
        if big > 0.02:
            settled = self._vw == 0 and self._vh == 0
            draw_shadow(p, shape, dy=24, blur=40, color=QColor(10, 12, 20, round(89 * big)),
                        key=("panel", round(self._w), round(self._h), self.width()) if settled else None)
        p.fillPath(shape, BG)
        # A hairline that fades in away from the screen edge, as on the notch.
        edge = QLinearGradient(0, 0, 0, 22)
        edge.setColorAt(0.0, QColor(255, 255, 255, 0))
        edge.setColorAt(1.0, QColor(255, 255, 255, 16))
        p.setPen(QPen(QBrush(edge), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(shape)
        p.end()

    # --- Actions -----------------------------------------------------------------------------------

    def _ask(self, text: str) -> None:
        note = self._follow_note
        if note is not None:
            text = f'{text} (about my sketch note "{note.title}")'
        self.home.ask.edit.clear()
        self.close_panel(instant=True)
        # Let the panel leave the screen before the question's screenshot.
        QTimer.singleShot(80, lambda: self.asked.emit(text))

    def _follow_up(self, note: Note) -> None:
        self._follow_note = note
        edit = self.home.ask.edit
        edit.setPlaceholderText(QFontMetricsF(edit.font()).elidedText(
            f"Ask about {note.title}", Qt.TextElideMode.ElideRight, edit.width() - 4))
        self._activate()
        edit.setFocus()

    def _open_note(self, note: Note) -> None:
        if note.png is not None:
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
