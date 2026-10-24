"""Side notch: Clicky's status island on the edge of the screen.

A pure-black shape that grows out of the right (or left) screen edge, like
the Dynamic Island turned on its side:

- Idle: a thin sliver on the edge. It peeks out a little when the mouse
  comes near.
- Listening: stretches down the edge into a tall capsule with a coral
  recording dot, a vertical waveform that moves with your voice, and your
  pixel buddy at the bottom, listening.
- Thinking: the waveform settles into a slow shimmer; the buddy thinks.
- Answer: widens into a card with the buddy and your question on top and
  the answer as captions, sentence by sentence as it's spoken. Names of
  things to click show as butter chips, the same color as the label next
  to the pointer.
- Note: a short message instead of an answer ("didn't catch that").

Size, height and corner radius follow a spring, so every change of state is
one fluid motion; content fades in once the shape is nearly there. Like the
overlay it is click-through, never takes focus, and hides while the screen
is captured so the model never sees it.

Threading: Qt main thread only; app.py reaches it through the overlay
controller's island_* methods via pyqtSignal slots.
"""
from __future__ import annotations

import math
import re
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen, QScreen
from PyQt6.QtWidgets import QWidget

import theme


class SideIsland(QWidget):
    SLIVER = (8.0, 58.0)
    PEEK = (14.0, 76.0)
    CAPSULE = (60.0, 214.0)
    CARD_W = 360.0
    MAX_CARD_H = 300.0
    _WIN_W = 400   # room for the card plus its shadow
    _WIN_H = 340
    _PAD = 24.0
    _TOP = 0.34    # vertical centre, as a fraction of the screen's height
    _STIFF, _DAMP = 0.16, 0.70   # per 60 Hz frame: slight overshoot, settles in ~0.5 s
    _PEEK_REACH = 70             # px from the edge where the sliver starts to peek
    _BARS = 13
    _HEAD = 36.0   # card header: the buddy, its name and your question

    def __init__(self, side: str | None = None) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(self._WIN_W, self._WIN_H)

        if side is None:
            try:
                from config import ISLAND_SIDE
                side = ISLAND_SIDE
            except Exception:
                side = "right"
        self.side = "left" if side == "left" else "right"

        self._state = "idle"
        self._transcript = ""
        self._sentences: list[str] = []
        self._highlights: list[str] = []
        self._meta = ""
        self._note = ""
        self._level = 0.0
        self._smooth_level = 0.0
        self.model_name = ""
        """Shown in the chip while listening; app.py sets it, e.g. 'Haiku 5.5'."""
        self._t0 = time.monotonic()
        self._last_tick = self._t0
        self._layout_cache: dict = {}

        # Spring state: width, height (the shape is centred in the window).
        self._w, self._h = self.SLIVER
        self._vw = self._vh = 0.0
        self._fade_voice = 0.0
        self._fade_card = 0.0
        self._near = False

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._animate)
        self._tick.start(16)

        self._screen: QScreen | None = QGuiApplication.primaryScreen()
        if self._screen is not None:
            self.dock(self._screen)
        self.show()
        self._apply_clickthrough()

    # --- Placement -----------------------------------------------------------

    def dock(self, screen: QScreen) -> None:
        """Sit on ``screen``'s edge, clear of the taskbar."""
        self._screen = screen
        area = screen.availableGeometry()
        x = area.x() + area.width() - self.width() if self.side == "right" else area.x()
        y = area.y() + int(area.height() * self._TOP) - self.height() // 2
        self.move(x, y)

    def _apply_clickthrough(self) -> None:
        try:
            from overlay import apply_clickthrough_styles
            apply_clickthrough_styles(int(self.winId()))
        except Exception:
            pass  # not on Windows (tests, previews)

    def hide_for_capture(self) -> None:
        self.hide()

    def show_after_capture(self) -> None:
        self.show()
        self._apply_clickthrough()

    # --- States --------------------------------------------------------------

    def listening(self, screen: QScreen | None = None) -> None:
        if screen is not None:
            self.dock(screen)
        self._collapse_timer.stop()
        self._state = "listening"
        self._transcript = ""
        self._sentences = []
        self._highlights = []
        self._note = ""
        self._meta = self.model_name
        self.update()

    def thinking(self) -> None:
        self._collapse_timer.stop()
        if self._state != "answer":
            self._state = "thinking"
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(float(level), 1.0))

    def set_transcript(self, text: str) -> None:
        self._transcript = text.strip()
        self.update()

    def say(self, sentence: str) -> None:
        sentence = sentence.strip()
        if not sentence:
            return
        self._collapse_timer.stop()
        self._state = "answer"
        self._sentences.append(sentence)
        self.update()

    def highlight(self, label: str) -> None:
        """Show ``label`` (what the pointer points at) as a chip in the captions."""
        label = (label or "").strip()
        if label and label.lower() not in (h.lower() for h in self._highlights):
            self._highlights.append(label)
            self.update()

    def set_meta(self, text: str) -> None:
        self._meta = text
        self.update()

    def note(self, text: str) -> None:
        self._collapse_timer.stop()
        self._state = "note"
        self._note = text.strip()
        self.update()
        self._collapse_timer.start(6000)

    def done(self) -> None:
        """The turn is over: stay open long enough to read, then tuck away."""
        if self._state == "note":
            return  # the note set its own timer
        if self._state in ("listening", "thinking"):
            self._collapse()
            return
        words = sum(len(s.split()) for s in self._sentences)
        self._collapse_timer.start(int(1000 * max(4.0, min(14.0, 2.5 + 0.3 * words))))

    def _collapse(self) -> None:
        self._state = "idle"
        self.update()

    # --- Geometry ------------------------------------------------------------

    def _target(self) -> tuple[float, float]:
        if self._state in ("listening", "thinking"):
            return self.CAPSULE
        if self._state == "answer":
            return self.CARD_W, self._card_height()
        if self._state == "note":
            return self.CARD_W, min(self.MAX_CARD_H, 2 * self._PAD + self._HEAD + 14.0 + self._note_height())
        return self.PEEK if self._near else self.SLIVER

    def _card_height(self) -> float:
        body = self._captions_layout(self.CARD_W - 2 * self._PAD)[1]
        return min(self.MAX_CARD_H, self._PAD + self._HEAD + 14.0 + body + self._PAD)

    def _note_height(self) -> float:
        metrics = QFontMetricsF(theme.display_font(13.5))
        return metrics.boundingRect(QRectF(0, 0, self.CARD_W - 2 * self._PAD, 10_000),
                                    Qt.TextFlag.TextWordWrap, self._note).height()

    def _shape_rect(self) -> QRectF:
        """The visible black shape, flush with the screen edge."""
        cy = self.height() / 2.0
        if self.side == "right":
            return QRectF(self.width() - self._w, cy - self._h / 2.0, self._w, self._h)
        return QRectF(0.0, cy - self._h / 2.0, self._w, self._h)

    def _shape_path(self) -> QPainterPath:
        rect = self._shape_rect()
        radius = 10.0 if self._w < 24 else min(32.0, self._h / 2.0, self._w / 2.0 + 12.0)
        # Extend past the edge so only the inner corners are rounded.
        if self.side == "right":
            full = QRectF(rect.x(), rect.y(), rect.width() + radius, rect.height())
        else:
            full = QRectF(rect.x() - radius, rect.y(), rect.width() + radius, rect.height())
        path = QPainterPath()
        path.addRoundedRect(full, radius, radius)
        return path

    # --- Animation -----------------------------------------------------------

    def _update_near(self) -> None:
        if self._state != "idle" or self._screen is None:
            self._near = False
            return
        pos = QCursor.pos()
        geo = self.geometry()
        edge = geo.right() if self.side == "right" else geo.left()
        close_x = abs(pos.x() - edge) < self._PEEK_REACH
        close_y = abs(pos.y() - geo.center().y()) < 160
        self._near = close_x and close_y

    def _animate(self) -> None:
        now = time.monotonic()
        frames = max(1, min(4, round((now - self._last_tick) * 60)))
        self._last_tick = now
        self._update_near()
        tw, th = self._target()
        moving = False
        for _ in range(frames):
            self._vw = (self._vw + (tw - self._w) * self._STIFF) * self._DAMP
            self._vh = (self._vh + (th - self._h) * self._STIFF) * self._DAMP
            self._w += self._vw
            self._h += self._vh
        if abs(tw - self._w) < 0.25 and abs(th - self._h) < 0.25 and abs(self._vw) < 0.25 and abs(self._vh) < 0.25:
            self._w, self._h, self._vw, self._vh = tw, th, 0.0, 0.0
        else:
            moving = True

        voice_on = self._state in ("listening", "thinking") and self._h > 150 and self._w > 44
        card_on = self._state in ("answer", "note") and self._w > tw * 0.85
        for attr, on, rate in (("_fade_voice", voice_on, 0.22), ("_fade_card", card_on, 0.18)):
            value = getattr(self, attr)
            target = 1.0 if on else 0.0
            if abs(value - target) > 0.005:
                value += (target - value) * min(1.0, rate * frames)
                moving = True
            else:
                value = target
            setattr(self, attr, value)
        self._smooth_level += (self._level - self._smooth_level) * 0.25

        if moving or self._state in ("listening", "thinking"):
            self.update()

    # --- Painting ------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self._shape_path()
        rect = self._shape_rect()

        # Soft shadow toward the screen while open: stacked wide strokes of
        # faint black, darkest at the shape's edge, fading out over ~16 px.
        strength = max(0.0, min(1.0, (self._w - 16.0) / 40.0))
        if strength > 0:
            direction = -1 if self.side == "right" else 1
            shadow = path.translated(direction * 3.0, 6.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(1, 9):
                pen = QPen(QColor(0, 0, 0, round(8 * strength)), i * 4.0)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(shadow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.NOTCH)
        painter.drawPath(path)

        painter.setClipPath(path)
        t = time.monotonic() - self._t0
        if self._fade_voice > 0.01:
            self._paint_voice(painter, rect, t)
        if self._fade_card > 0.01:
            self._paint_card(painter, rect)

    def _scaled(self, painter: QPainter, rect: QRectF, fade: float) -> None:
        """Fade in with a little grow, from the edge the shape grows out of."""
        painter.setOpacity(fade)
        anchor_x = rect.right() if self.side == "right" else rect.left()
        scale = 0.94 + 0.06 * fade
        painter.translate(anchor_x, rect.center().y())
        painter.scale(scale, scale)
        painter.translate(-anchor_x, -rect.center().y())

    def _paint_voice(self, painter: QPainter, rect: QRectF, t: float) -> None:
        painter.save()
        self._scaled(painter, rect, self._fade_voice)
        cx = rect.center().x()
        listening = self._state == "listening"

        # Recording dot: coral and pulsing while listening, dim while thinking.
        dot = QPointF(cx, rect.top() + 18)
        if listening:
            pulse = (t % 1.4) / 1.4
            ring = QColor(theme.CORAL)
            ring.setAlphaF(0.55 * (1 - pulse))
            painter.setBrush(ring)
            painter.drawEllipse(dot, 3.5 + 7 * pulse, 3.5 + 7 * pulse)
            painter.setBrush(theme.CORAL)
        else:
            painter.setBrush(QColor(255, 255, 255, 110))
        painter.drawEllipse(dot, 3.5, 3.5)

        # Vertical waveform: horizontal bars stacked down the capsule.
        gap, bar_h = 5.0, 3.0
        y0 = rect.top() + 38
        mid = (self._BARS - 1) / 2
        for i in range(self._BARS):
            weight = 1 - abs(i - mid) / mid
            if listening:
                level = self._smooth_level
                width = 6 + 32 * level * (0.35 + 0.65 * weight) * (0.7 + 0.3 * math.sin(t * 9 + i * 1.3))
                color = QColor(theme.BUTTER)
            else:
                width = 4.0
                color = QColor(255, 255, 255)
                color.setAlphaF(0.2 + 0.8 * (0.5 + 0.5 * math.sin(t * 5.7 - i * 0.55)))
            painter.setBrush(color)
            y = y0 + i * (bar_h + gap)
            painter.drawRoundedRect(QRectF(cx - width / 2, y, width, bar_h), 1.5, 1.5)

        # The buddy at the bottom, listening, then thinking.
        theme.paint_avatar_in(painter, QRectF(rect.left(), rect.bottom() - 62, rect.width(), 52), t=t,
                              mood="listening" if listening else "thinking", scale=1.0,
                              device_pixel_ratio=self.devicePixelRatioF(), glow=True)
        painter.restore()

    def _paint_card(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        self._scaled(painter, rect, self._fade_card)
        # Content is laid out at full card width from the inner edge.
        x0 = (rect.right() - self.CARD_W if self.side == "right" else rect.left()) + self._PAD
        y = rect.top() + self._PAD
        width = self.CARD_W - 2 * self._PAD

        # Header: the buddy, its name and the meta on the first line, your
        # question under it.
        t = time.monotonic() - self._t0
        mood = "thinking" if self._state == "note" else "happy"
        theme.paint_avatar_in(painter, QRectF(x0, y, self._HEAD, self._HEAD), t=t, mood=mood, scale=1.0,
                              device_pixel_ratio=self.devicePixelRatioF(), glow=True)
        tx = x0 + self._HEAD + 12
        two_lines = bool(self._transcript) and self._state == "answer"
        name_y = y + (15 if two_lines else 23)
        painter.setPen(theme.NOTCH_TEXT)
        painter.setFont(theme.ui_font(10.5, QFont.Weight.DemiBold))
        painter.drawText(QPointF(tx, name_y), theme.current_avatar().name)
        if self._meta and self._state == "answer":
            painter.setFont(theme.mono_font(8.2))
            painter.setPen(theme.NOTCH_MUTED)
            meta_w = painter.fontMetrics().horizontalAdvance(self._meta)
            painter.drawText(QPointF(x0 + width - meta_w, name_y - 1), self._meta)
        if two_lines:
            painter.setPen(theme.NOTCH_MUTED)
            painter.setFont(theme.ui_font(9.5, QFont.Weight.Light))
            quoted = painter.fontMetrics().elidedText(f"“{self._transcript}”", Qt.TextElideMode.ElideRight,
                                                      int(x0 + width - tx))
            painter.drawText(QPointF(tx, y + 32), quoted)
        y += self._HEAD + 14

        if self._state == "note":
            painter.setPen(theme.NOTCH_TEXT)
            painter.setFont(theme.display_font(13.5))
            painter.drawText(QRectF(x0, y, width, rect.bottom() - y - self._PAD + 4), Qt.TextFlag.TextWordWrap, self._note)
            painter.restore()
            return

        layout, height = self._captions_layout(width)
        available = rect.bottom() - self._PAD + 4 - y
        # When the answer is taller than the card, show its end.
        shift = max(0.0, height - available)
        painter.setClipRect(QRectF(x0 - 2, y, width + 4, available), Qt.ClipOperation.IntersectClip)
        self._paint_captions(painter, layout, x0, y - shift)
        painter.restore()

    # --- Captions: words with chips ------------------------------------------

    def _captions_layout(self, width: float):
        """Lay out the sentences as wrapped words, with chips for the names of
        things to click. Earlier sentences are dimmed.

        Returns ((font, chip_font, metrics, line_h, items), height), where
        each item is (kind, text, x, y, width, current)."""
        key = (tuple(self._sentences), tuple(self._highlights), round(width))
        cached = self._layout_cache.get(key)
        if cached is not None:
            return cached
        font = theme.display_font(14.5)
        chip_font = theme.ui_font(11.5, QFont.Weight.DemiBold)
        metrics, chip_metrics = QFontMetricsF(font), QFontMetricsF(chip_font)
        line_h = metrics.height() * 1.18
        space = metrics.horizontalAdvance(" ")
        items: list[tuple] = []
        x, line = 0.0, 0
        last = len(self._sentences) - 1
        for index, sentence in enumerate(self._sentences):
            current = index == last
            for text, is_chip, glued in split_chips(sentence, self._highlights):
                w = chip_metrics.horizontalAdvance(text) + 12 if is_chip else metrics.horizontalAdvance(text)
                if glued and items:
                    x -= space  # punctuation sticks to the chip before it
                if x > 0 and x + w > width and not glued:
                    x, line = 0.0, line + 1
                items.append(("chip" if is_chip else "word", text, x, line * line_h, w, current))
                x += w + space
        height = (line + 1) * line_h if items else 0.0
        result = ((font, chip_font, metrics, line_h, items), height)
        if len(self._layout_cache) > 32:
            self._layout_cache.clear()
        self._layout_cache[key] = result
        return result

    @staticmethod
    def _paint_captions(painter: QPainter, layout, x0: float, y0: float) -> None:
        font, chip_font, metrics, line_h, items = layout
        for kind, text, x, y, w, current in items:
            baseline = y0 + y + metrics.ascent() + (line_h - metrics.height()) / 2
            if kind == "chip":
                chip = QRectF(x0 + x, baseline - metrics.ascent() + 2, w, metrics.height() - 2)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.BUTTER if current else theme.NOTCH_CHIP_DIM)
                painter.drawRoundedRect(chip, 6, 6)
                painter.setFont(chip_font)
                painter.setPen(theme.INK if current else theme.NOTCH_CHIP_DIM_TEXT)
                painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
            else:
                painter.setFont(font)
                painter.setPen(theme.NOTCH_TEXT if current else theme.NOTCH_DIM)
                painter.drawText(QPointF(x0 + x, baseline), text)


_QUOTED = re.compile(r"[\"“‘']([^\"”’']{1,40})[\"”’']")


def split_chips(sentence: str, highlights: list[str]) -> list[tuple[str, bool, bool]]:
    """Split a sentence into (text, is_chip, glued) tokens. A chip is a
    highlighted label (case-insensitive, may be several words) or text in
    quotes. ``glued`` marks text that touches the token before it with no
    space, like the period in "Export.", so it isn't pushed apart."""
    spans: list[tuple[int, int]] = []
    for label in sorted(highlights, key=len, reverse=True):
        for m in re.finditer(r"(?<!\w)" + re.escape(label) + r"(?!\w)", sentence, re.IGNORECASE):
            if not any(a < m.end() and m.start() < b for a, b in spans):
                spans.append((m.start(), m.end()))
    for m in _QUOTED.finditer(sentence):
        if not any(a < m.end() and m.start() < b for a, b in spans):
            spans.append((m.start(), m.end()))
    spans.sort()

    out: list[tuple[str, bool, bool]] = []

    def words(text: str, after_chip: bool) -> None:
        for i, word in enumerate(text.split()):
            glued = i == 0 and after_chip and not text[:1].isspace()
            out.append((word, False, glued))

    pos, after_chip = 0, False
    for a, b in spans:
        words(sentence[pos:a], after_chip)
        out.append((sentence[a:b].strip("\"“”‘’'"), True, False))
        pos, after_chip = b, True
    words(sentence[pos:], after_chip)
    return out


def model_label(model_id: str) -> str:
    """'anthropic/claude-haiku-5-5' -> 'Haiku 5.5'; other ids lightly tidied."""
    name = model_id.split("/", 1)[-1]
    if name.startswith("claude-"):
        parts = name.removeprefix("claude-").split("-")
        if len(parts) >= 2 and all(p.isdigit() for p in parts[1:]):
            return f"{parts[0].title()} {'.'.join(parts[1:])}"
    return name.split(":")[0]
