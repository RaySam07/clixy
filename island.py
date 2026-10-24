"""Side island: Clicky's little status panel on the right edge of the screen.

Tucked away it is a small tab with the avatar peeking out. When you hold the
hotkey it springs open into a rounded panel, in the avatar's theme colors:

- Listening: level bars that move with your voice
- Thinking: what you asked, and three bobbing dots
- Answer: the answer as captions, sentence by sentence as it is spoken,
  plus how long it took and what it cost
- Note: a short message instead of an answer ("didn't catch that")

A few seconds after the answer it tucks itself away again. Like the overlay
it is click-through, never takes focus, and hides while the screen is
captured so the model never sees it.

Threading: Qt main thread only; app.py reaches it through the overlay
controller's island_* methods via pyqtSignal slots.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QFont, QFontMetricsF, QGuiApplication, QPainter, QScreen
from PyQt6.QtWidgets import QWidget

import theme


class SideIsland(QWidget):
    COLLAPSED = (30.0, 58.0)
    OPEN_WIDTH = 360.0
    MAX_HEIGHT = 250.0
    _MARGIN = 14         # room for the shadow on the left and below
    _RADIUS = 26.0
    _TOP_FRACTION = 0.22  # how far down the screen the island sits
    _PAD = 22.0
    _HEADER = 52.0

    def __init__(self) -> None:
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
        self.resize(int(self.OPEN_WIDTH) + self._MARGIN, int(self.MAX_HEIGHT) + self._MARGIN)

        self._state = "idle"
        self._transcript = ""
        self._sentences: list[str] = []
        self._meta = ""
        self._note = ""
        self._level = 0.0
        self.model_name = ""
        """Shown in the chip while listening; app.py sets it, e.g. 'Haiku 5.5'."""
        self._text_heights: dict[str, float] = {}
        self._t0 = time.monotonic()

        # Spring-animated size of the panel and fade of its contents.
        self._w, self._h = self.COLLAPSED
        self._vw = self._vh = 0.0
        self._content = 0.0

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._animate)
        self._tick.start(16)
        self._last_frame = -1

        theme.on_avatar_changed(lambda _id: self.update())
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            self.dock(primary)
        self.show()
        self._apply_clickthrough()

    # --- Placement -----------------------------------------------------------

    def dock(self, screen: QScreen) -> None:
        """Sit on the right edge of ``screen``, clear of the taskbar."""
        area = screen.availableGeometry()
        self.move(
            area.x() + area.width() - self.width(),
            area.y() + int(area.height() * self._TOP_FRACTION),
        )

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

    # --- Animation -----------------------------------------------------------

    def _target(self) -> tuple[float, float]:
        if self._state == "idle":
            return self.COLLAPSED
        if self._state == "listening":
            return self.OPEN_WIDTH, 118.0
        if self._state == "thinking":
            return self.OPEN_WIDTH, 118.0 + (22.0 if self._transcript else 0.0)
        if self._state == "note":
            return self.OPEN_WIDTH, min(self.MAX_HEIGHT, self._HEADER + 18 + self._text_height(self._note) + 16)
        body = self._text_height(" ".join(self._sentences))
        extra = 26.0 if self._transcript else 0.0
        return self.OPEN_WIDTH, min(self.MAX_HEIGHT, self._HEADER + extra + body + 26)

    def _text_height(self, text: str) -> float:
        """Wrapped height of ``text`` in the caption font (cached: the spring
        asks for it every frame)."""
        if text not in self._text_heights:
            if len(self._text_heights) > 64:
                self._text_heights.clear()
            metrics = QFontMetricsF(theme.ui_font(12.5, QFont.Weight.ExtraBold))
            width = self.OPEN_WIDTH - 2 * self._PAD
            self._text_heights[text] = metrics.boundingRect(
                QRectF(0, 0, width, 10_000), Qt.TextFlag.TextWordWrap, text
            ).height()
        return self._text_heights[text]

    def _animate(self) -> None:
        tw, th = self._target()
        moving = False
        # A soft spring: a touch of overshoot so it feels fluid, not mechanical.
        for attr, vattr, target in (("_w", "_vw", tw), ("_h", "_vh", th)):
            value, velocity = getattr(self, attr), getattr(self, vattr)
            velocity = (velocity + (target - value) * 0.2) * 0.62
            value += velocity
            if abs(target - value) < 0.3 and abs(velocity) < 0.3:
                value, velocity = target, 0.0
            else:
                moving = True
            setattr(self, attr, value)
            setattr(self, vattr, velocity)
        open_enough = self._state != "idle" and self._w > self.OPEN_WIDTH * 0.85
        target_content = 1.0 if open_enough else 0.0
        if abs(self._content - target_content) > 0.01:
            self._content += (target_content - self._content) * (0.25 if open_enough else 0.45)
            moving = True
        else:
            self._content = target_content

        frame = int((time.monotonic() - self._t0) * theme.FPS)
        if moving or self._state in ("listening", "thinking") or frame != self._last_frame:
            self._last_frame = frame
            self.update()

    # --- Painting ------------------------------------------------------------

    def _panel_rect(self) -> QRectF:
        return QRectF(self.width() - self._w, 0.0, self._w, self._h)

    def paintEvent(self, _event) -> None:
        palette = theme.current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._panel_rect()
        radius = min(self._RADIUS, self._h / 2.0)
        theme.paint_island_shell(painter, rect, radius, palette)

        t = time.monotonic() - self._t0
        dpr = self.devicePixelRatioF()
        mood = {"listening": "listening", "thinking": "thinking", "answer": "happy"}.get(self._state, "idle")

        # The tab: just the avatar peeking out, fading as the panel opens.
        if self._content < 0.99:
            painter.save()
            painter.setOpacity(1.0 - self._content)
            painter.setClipRect(rect)
            theme.paint_avatar(painter, rect.x() + 8, rect.y() + 12, t=t, mood=mood, scale=0.5,
                               device_pixel_ratio=dpr, glow=False)
            painter.restore()
        if self._content <= 0.01:
            return

        painter.save()
        painter.setOpacity(self._content)
        painter.setClipRect(rect)
        x0, y0 = rect.x() + self._PAD, rect.y()
        theme.paint_avatar(painter, x0 - 4, y0 + 10, t=t, mood=mood, device_pixel_ratio=dpr)

        avatar = theme.current_avatar()
        title = {
            "listening": "Listening…",
            "thinking": "Thinking…",
            "answer": f"{avatar.name} says",
            "note": "Hmm…",
        }.get(self._state, "")
        painter.setPen(palette.text)
        painter.setFont(theme.ui_font(13.5, QFont.Weight.ExtraBold))
        painter.drawText(QPointF(x0 + 38, y0 + 36), title)
        self._paint_chip(painter, rect, palette)

        y = y0 + self._HEADER
        width = self.OPEN_WIDTH - 2 * self._PAD
        if self._transcript and self._state in ("thinking", "answer"):
            painter.setPen(palette.muted)
            painter.setFont(theme.ui_font(10.5, QFont.Weight.Bold))
            quoted = painter.fontMetrics().elidedText(f"“{self._transcript}”", Qt.TextElideMode.ElideRight, int(width))
            painter.drawText(QPointF(x0, y + 4), quoted)
            y += 26

        if self._state == "listening":
            heights = [_bar_height(i, self._level, t) for i in range(5)]
            theme.paint_bars(painter, x0, rect.bottom() - 30, heights, palette)
            self._hint(painter, x0 + 40, rect.bottom() - 25, "let go to ask", palette)
        elif self._state == "thinking":
            theme.paint_dots(painter, x0 + 6, rect.bottom() - 30, math.radians(_dots_phase(t)), palette)
            self._hint(painter, x0 + 46, rect.bottom() - 25, "reading your screen…", palette)
        elif self._state == "answer":
            self._paint_captions(painter, QRectF(x0, y - 4, width, rect.bottom() - y - 10), palette)
        elif self._state == "note":
            painter.setPen(palette.text)
            painter.setFont(theme.ui_font(12.5, QFont.Weight.ExtraBold))
            painter.drawText(QRectF(x0, y - 4, width, rect.bottom() - y), Qt.TextFlag.TextWordWrap, self._note)
        painter.restore()

    def _paint_chip(self, painter: QPainter, rect: QRectF, palette: theme.Palette) -> None:
        if not self._meta:
            return
        painter.save()
        painter.setFont(theme.pixel_font(7.5))
        text = self._meta.upper()
        width = painter.fontMetrics().horizontalAdvance(text) + 16
        chip = QRectF(rect.right() - width - self._PAD, rect.y() + 21, width, 20)
        fill = palette.accent.lighter(100)
        fill.setAlpha(46)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(chip, 10, 10)
        painter.setPen(palette.chip)
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    @staticmethod
    def _hint(painter: QPainter, x: float, y: float, text: str, palette: theme.Palette) -> None:
        painter.setPen(palette.muted)
        painter.setFont(theme.ui_font(9.5, QFont.Weight.Bold))
        painter.drawText(QPointF(x, y), text)

    def _paint_captions(self, painter: QPainter, area: QRectF, palette: theme.Palette) -> None:
        """Earlier sentences dimmed, the newest one bright. When they don't
        all fit, the oldest scroll off the top."""
        font = theme.ui_font(12.5, QFont.Weight.ExtraBold)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        flags = Qt.TextFlag.TextWordWrap
        shown: list[str] = []
        for sentence in reversed(self._sentences):
            candidate = [sentence] + shown
            if shown and metrics.boundingRect(QRectF(0, 0, area.width(), 10_000), flags, " ".join(candidate)).height() > area.height():
                break
            shown = candidate
        if not shown:
            return
        older, newest = " ".join(shown[:-1]), shown[-1]
        dim = palette.text.darker(100)
        dim.setAlpha(120)
        if older:
            painter.setPen(dim)
            painter.drawText(area, flags, older)
            # The newest sentence starts on a fresh line under the older ones.
            used = metrics.boundingRect(QRectF(0, 0, area.width(), 10_000), flags, older).height()
            area = QRectF(area.x(), area.y() + used + 2, area.width(), area.height() - used - 2)
        painter.setPen(palette.text)
        painter.drawText(area, flags, newest)


def _bar_height(index: int, level: float, t: float) -> float:
    from overlay import _waveform_bar_height
    return 1.6 * _waveform_bar_height(index, level, t)


def _dots_phase(t: float) -> float:
    from overlay import _spinner_angle_deg
    return _spinner_angle_deg(t)


def model_label(model_id: str) -> str:
    """'anthropic/claude-haiku-5-5' -> 'Haiku 5.5'; other ids lightly tidied."""
    name = model_id.split("/", 1)[-1]
    if name.startswith("claude-"):
        parts = name.removeprefix("claude-").split("-")
        if len(parts) >= 2 and all(p.isdigit() for p in parts[1:]):
            return f"{parts[0].title()} {'.'.join(parts[1:])}"
    return name.split(":")[0]
