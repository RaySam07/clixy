"""Clixy's status island: a notch at the top of the screen (the default),
like a laptop's notch, or a sliver on the left or right edge.

At the top it's a small black notch with your buddy and soft audio lines.
It says hello ("Hey Rayan!") when Clixy starts and the first time each day.
Rest the mouse on it and it drops down your dashboard (the time, the date,
the weather, today's classes; see dashboard.py), and tucks back up when the
mouse leaves. It widens into a pill while you talk and drops the answer
down as a card.

On a side edge, a pure-black shape grows out of the edge, like the Dynamic
Island turned on its side:

- Idle: a thin sliver on the edge. When the mouse rests on it, it springs
  out into a pill that says hello: your buddy, "Clixy here." and the keys to
  hold. It lingers a moment after the mouse leaves, then tucks back in.
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

import datetime
import math
import re
import time

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QScreen
from PyQt6.QtWidgets import QWidget

import theme


class SideIsland(QWidget):
    # At the top of the screen, like a laptop's notch.
    NOTCH = (132.0, 30.0)
    HELLO = (214.0, 34.0)
    HINT = (262.0, 34.0)
    PILL = (300.0, 38.0)
    TOP_CARD_W = 430.0
    DASH_W = 470.0
    _TOP_WIN = (520, 380)
    _HELLO_S = 3.2
    _DWELL = 0.5                 # s the mouse rests on it before it shows anything

    # On a side edge.
    SLIVER = (8.0, 58.0)
    GREET = (256.0, 78.0)
    CAPSULE = (60.0, 214.0)
    CARD_W = 360.0
    MAX_CARD_H = 300.0
    _WIN_W = 400   # room for the card plus its shadow
    _WIN_H = 340
    _PAD = 24.0
    _TOP = 0.34    # vertical centre, as a fraction of the screen's height
    _STIFF, _DAMP = 0.16, 0.70   # per 60 Hz frame: slight overshoot, settles in ~0.5 s
    _PEEK_REACH = 16             # px from the edge: the mouse has to be on the sliver
    _LINGER = 0.6                # s it stays open after the mouse leaves
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
        if side is None:
            try:
                from config import ISLAND_SIDE
                side = ISLAND_SIDE
            except Exception:
                side = "top"
        self.side = side if side in ("left", "right", "top") else "top"
        self.home_side = self.side
        """Where Settings put it; a guide step may borrow another edge."""
        self._fit_window()

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
        self.hotkey = _hotkey_text()
        """The keys to hold, as shown when it greets you, e.g. 'Ctrl + Win'."""
        self.name = _first_name()
        """Who it says hello to ("Rayan"); empty says "Hey there!"."""
        self.dashboard = True
        """Resting the mouse on the top notch shows the dashboard (else just the keys)."""
        if self.side == "top":
            _refresh_dashboard()  # ready for the first time you rest on it
        self._t0 = time.monotonic()
        self._hello_until = self._t0 + self._HELLO_S
        self._greeted_on = datetime.date.today()
        self._hover_since: float | None = None
        self._frame = 0
        self._last_tick = self._t0
        self._layout_cache: dict = {}

        # Spring state: width, height (the shape is centred in the window).
        self._w, self._h = self.NOTCH if self.side == "top" else self.SLIVER
        self._vw = self._vh = 0.0
        self._fade_voice = 0.0
        self._fade_card = 0.0
        self._fade_greet = 0.0
        self._fade_notch = 0.0
        self._fade_dash = 0.0
        self._glass: QImage | None = None
        """A blur of the screen behind, taken as the dashboard opens."""
        self._near = False
        self._near_until = 0.0

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
        """Sit on ``screen``'s top edge (centred) or side edge, clear of the taskbar."""
        self._screen = screen
        area = screen.availableGeometry()
        if self.side == "top":
            self.move(area.x() + (area.width() - self.width()) // 2, area.y())
            return
        x = area.x() + area.width() - self.width() if self.side == "right" else area.x()
        y = area.y() + int(area.height() * self._TOP) - self.height() // 2
        self.move(x, y)

    def _fit_window(self) -> None:
        self.resize(*(self._TOP_WIN if self.side == "top" else (self._WIN_W, self._WIN_H)))

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
        self._move_to(self.home_side)
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

    def step(self, text: str, n: int, total: int = 0, keep_clear: QRect | None = None) -> None:
        """Guide mode: show just this step ("step 2 of 3") until the next one.
        ``keep_clear`` (global coords) is what you have to click: if the card
        would cover it, the island moves to the other edge for this step."""
        side = self.home_side
        if keep_clear is not None and self.geometry().adjusted(-24, -24, 24, 24).intersects(keep_clear):
            if self.home_side == "top":  # the far side from what you have to click
                screen = self._screen.geometry() if self._screen is not None else self.geometry()
                side = "left" if keep_clear.center().x() > screen.center().x() else "right"
            else:
                side = "left" if self.home_side == "right" else "right"
        self._move_to(side)
        self._collapse_timer.stop()
        self._state = "answer"
        self._sentences = [text.strip()] if text.strip() else []
        self._highlights = []
        self._meta = f"step {n} of {total}" if total >= n > 0 else (f"step {n}" if n > 0 else "")
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
        self._move_to(self.home_side)
        self.update()

    def set_home(self, side: str) -> None:
        """Live there from now on: "top", "left" or "right" (Settings)."""
        self.home_side = side if side in ("left", "right", "top") else "top"
        self._move_to(self.home_side)

    def _move_to(self, side: str) -> None:
        if side != self.side:
            was_top = self.side == "top"
            self.side = side
            if was_top != (side == "top"):
                self._fit_window()
            if self._screen is not None:
                self.dock(self._screen)
            self.update()

    # --- Geometry ------------------------------------------------------------

    @property
    def top(self) -> bool:
        return self.side == "top"

    def _card_w(self) -> float:
        return self.TOP_CARD_W if self.top else self.CARD_W

    def _hello(self) -> bool:
        return self.top and self._state == "idle" and time.monotonic() < self._hello_until

    def _target(self) -> tuple[float, float]:
        if self._state in ("listening", "thinking"):
            return self.PILL if self.top else self.CAPSULE
        if self._state == "answer":
            return self._card_w(), self._card_height()
        if self._state == "note":
            return self._card_w(), min(self.MAX_CARD_H, 2 * self._PAD + self._HEAD + 14.0 + self._note_height())
        if self.top:
            if self._hello():
                return self.HELLO
            if self._near:
                return (self.DASH_W, self._dash_height()) if self.dashboard else self.HINT
            return self.NOTCH
        return self.GREET if self._near else self.SLIVER

    def _dash_height(self) -> float:
        import dashboard
        _label, classes = dashboard.day_view(datetime.datetime.now().astimezone(), dashboard.snapshot().classes)
        return 14 + 92 + 26 + 42 * max(1, len(classes)) + 8 + 30 + 10

    def _card_height(self) -> float:
        body = self._captions_layout(self._card_w() - 2 * self._PAD)[1]
        return min(self.MAX_CARD_H, self._PAD + self._HEAD + 14.0 + body + self._PAD)

    def _note_height(self) -> float:
        metrics = QFontMetricsF(theme.display_font(13.5))
        return metrics.boundingRect(QRectF(0, 0, self._card_w() - 2 * self._PAD, 10_000),
                                    Qt.TextFlag.TextWordWrap, self._note).height()

    def _shape_rect(self) -> QRectF:
        """The visible black shape, flush with the screen edge."""
        if self.top:
            return QRectF((self.width() - self._w) / 2.0, 0.0, self._w, self._h)
        cy = self.height() / 2.0
        if self.side == "right":
            return QRectF(self.width() - self._w, cy - self._h / 2.0, self._w, self._h)
        return QRectF(0.0, cy - self._h / 2.0, self._w, self._h)

    def _shape_path(self) -> QPainterPath:
        rect = self._shape_rect()
        if self.top:
            # Flush with the top edge: only the lower corners are rounded.
            radius = min(15.0, self._h / 2.0) if self._h < 52 else min(26.0, self._h / 2.0)
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect.x(), rect.y() - radius, rect.width(), rect.height() + radius),
                                radius, radius)
            return path
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
        """Open only for a mouse that rests on the shape itself for a moment,
        never for one passing by."""
        if self._state != "idle" or self._screen is None:
            self._near = False
            self._near_until = 0.0
            self._hover_since = None
            return
        pos = QCursor.pos()
        geo = self.geometry()
        now = time.monotonic()
        if self.top:
            shape = self._shape_rect().translated(geo.x(), geo.y())
            on = shape.adjusted(-4, -4, 4, 6).contains(QPointF(pos))
        else:
            edge = geo.right() if self.side == "right" else geo.left()
            # Once it's open the whole pill counts, so it stays while you look at it.
            reach = max(self._PEEK_REACH, self._w + 24) if self._near else self._PEEK_REACH
            on = abs(pos.x() - edge) < reach and abs(pos.y() - geo.center().y()) < self._h / 2 + 12
        if on:
            if self._hover_since is None:
                self._hover_since = now
            if self._near or now - self._hover_since >= self._DWELL:
                self._near_until = now + (0.4 if self.top else self._LINGER)
        else:
            self._hover_since = None
        was_near = self._near
        self._near = now < self._near_until
        if self._near and not was_near and self.top and self.dashboard:
            _refresh_dashboard()  # fresh weather and classes while it opens
            self._glass = self._frosted_backdrop()

    def _animate(self) -> None:
        now = time.monotonic()
        frames = max(1, min(4, round((now - self._last_tick) * 60)))
        self._last_tick = now
        self._update_near()
        if self.top and self._state == "idle" and datetime.date.today() != self._greeted_on:
            self._greeted_on = datetime.date.today()  # first time each day: say hello again
            self._hello_until = now + self._HELLO_S
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

        if self.top:
            voice_on = self._state in ("listening", "thinking") and self._w > self.PILL[0] * 0.8
            greet_on = False
        else:
            voice_on = self._state in ("listening", "thinking") and self._h > 150 and self._w > 44
            greet_on = self._state == "idle" and self._near and self._w > self.GREET[0] * 0.8
        card_on = self._state in ("answer", "note") and self._w > tw * 0.85
        notch_on = self.top and self._state == "idle" and self._h < 44
        dash_on = (self.top and self.dashboard and self._state == "idle" and self._near
                   and self._w > self.DASH_W * 0.85 and self._h > 90)
        for attr, on, rate in (("_fade_voice", voice_on, 0.22), ("_fade_card", card_on, 0.18),
                               ("_fade_greet", greet_on, 0.3), ("_fade_notch", notch_on, 0.25),
                               ("_fade_dash", dash_on, 0.22)):
            value = getattr(self, attr)
            target = 1.0 if on else 0.0
            if abs(value - target) > 0.005:
                value += (target - value) * min(1.0, rate * frames)
                moving = True
            else:
                value = target
            setattr(self, attr, value)
        self._smooth_level += (self._level - self._smooth_level) * 0.25

        self._frame += 1
        if moving or self._state in ("listening", "thinking"):
            self.update()
        elif self._fade_dash > 0.01 and self._frame % 20 == 0:
            self.update()  # the dashboard's clock
        elif self.top and self._state == "idle" and self._frame % 3 == 0:
            # The resting notch's audio lines breathe: repaint just the notch.
            self.update(self._shape_rect().toAlignedRect().adjusted(-4, 0, 4, 4))

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
            direction = 0 if self.top else (-1 if self.side == "right" else 1)
            shadow = path.translated(direction * 3.0, 4.0 if self.top else 6.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(1, 9):
                pen = QPen(QColor(0, 0, 0, round(8 * strength)), i * 4.0)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(shadow)
        painter.setPen(Qt.PenStyle.NoPen)
        glass = self._fade_dash if self._glass is not None else 0.0
        if glass > 0.01:
            # Frosted glass: a blur of what's behind, darkened enough to read on.
            painter.save()
            painter.setClipPath(path)
            painter.drawImage(QRectF(self.rect()), self._glass)
            painter.restore()
        tint = QColor(theme.NOTCH)
        tint.setAlphaF(1.0 - 0.3 * glass)
        painter.setBrush(tint)
        painter.drawPath(path)
        if self.top:
            # A faint edge, so a black notch still reads on a dark page; brighter on glass.
            painter.setPen(QPen(QColor(255, 255, 255, round(26 + 30 * glass)), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)

        painter.setClipPath(path)
        t = time.monotonic() - self._t0
        if self._fade_notch > 0.01:
            self._paint_notch(painter, rect, t)
        if self._fade_dash > 0.01:
            self._paint_dash(painter, rect)
        if self._fade_voice > 0.01:
            (self._paint_pill if self.top else self._paint_voice)(painter, rect, t)
        if self._fade_card > 0.01:
            self._paint_card(painter, rect)
        if self._fade_greet > 0.01:
            self._paint_greet(painter, rect, t)

    def _scaled(self, painter: QPainter, rect: QRectF, fade: float) -> None:
        """Fade in with a little grow, from the edge the shape grows out of."""
        painter.setOpacity(fade)
        if self.top:
            anchor = QPointF(rect.center().x(), rect.top())
        else:
            anchor = QPointF(rect.right() if self.side == "right" else rect.left(), rect.center().y())
        scale = 0.94 + 0.06 * fade
        painter.translate(anchor)
        painter.scale(scale, scale)
        painter.translate(-anchor)

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

    def _paint_notch(self, painter: QPainter, rect: QRectF, t: float) -> None:
        """The resting notch: your buddy and soft audio lines; while saying
        hello, "Hey Rayan!"; while the mouse rests on it, the keys to hold."""
        painter.save()
        painter.setOpacity(self._fade_notch)
        band = min(rect.height(), self.HINT[1])
        cy = rect.top() + band / 2.0
        hello, hint = self._hello(), self._near and not self._hello()
        size = 24.0 if hello else 22.0
        theme.paint_avatar_in(painter, QRectF(rect.left() + 10, cy - size / 2, size, size), t=t,
                              mood="happy" if hello else "idle", scale=size / 48.0,
                              device_pixel_ratio=self.devicePixelRatioF(), glow=True)
        # How far the shape has opened toward its wider form, for the words in the middle.
        wide = self.HELLO[0] if hello else self.HINT[0]
        k = max(0.0, min(1.0, (rect.width() - self.NOTCH[0]) / (wide - self.NOTCH[0])))
        if not hint:
            self._paint_voice_pill(painter, QPointF(rect.right() - 26, cy), t, 30.0, 15.0, None, glow=0.6)
        if k > 0.02 and (hello or hint):
            painter.setOpacity(self._fade_notch * k)
            x = rect.left() + 42
            if hello:
                words = f"Hey {self.name}" if self.name else "Hey there"
                painter.setFont(theme.display_font(13.5))
                painter.setPen(theme.NOTCH_TEXT)
                painter.drawText(QPointF(x, cy + 5.5), words)
                painter.setPen(theme.current_palette().flood)
                painter.drawText(QPointF(x + painter.fontMetrics().horizontalAdvance(words), cy + 5.5), "!")
            else:
                x = self._paint_mini_keys(painter, x, cy - 9.0, 18.0)
                painter.setFont(theme.ui_font(8.8, QFont.Weight.Light))
                painter.setPen(theme.NOTCH_MUTED)
                painter.drawText(QRectF(x + 7, cy - 9.0, rect.right() - x - 14, 16), Qt.AlignmentFlag.AlignVCenter,
                                 "hold to ask")
        painter.restore()

    def _frosted_backdrop(self) -> QImage | None:
        """What's on screen behind the island, blurred; None if it can't be read."""
        screen = self.screen() or self._screen
        if screen is None:
            return None
        try:
            origin = screen.geometry().topLeft()
            geo = self.geometry()
            shot = screen.grabWindow(0, geo.x() - origin.x(), geo.y() - origin.y(), geo.width(), geo.height())
        except Exception:
            return None
        return None if shot.isNull() else frost(shot.toImage())

    def _paint_dash(self, painter: QPainter, rect: QRectF) -> None:
        """The dashboard: the time and date, the weather, and today's classes
        (or the next day's), with the class on now in your buddy's colour."""
        import dashboard
        painter.save()
        self._scaled(painter, rect, self._fade_dash)
        snap = dashboard.snapshot()
        now = datetime.datetime.now().astimezone()
        flood = theme.current_palette().flood
        x0, right = rect.left() + 24, rect.right() - 24
        y = rect.top() + 14

        # Your buddy and a hello, then the time, big and thin, and the date.
        theme.paint_avatar_in(painter, QRectF(x0 - 3, y, 18, 18), t=time.monotonic() - self._t0, mood="happy",
                              scale=18 / 48.0, device_pixel_ratio=self.devicePixelRatioF(), glow=True)
        part = "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"
        painter.setFont(theme.ui_font(9.5))
        painter.setPen(theme.NOTCH_MUTED)
        painter.drawText(QPointF(x0 + 20, y + 13), f"Good {part}" + (f", {self.name}" if self.name else ""))
        y += 22
        painter.setPen(theme.NOTCH_TEXT)
        painter.setFont(theme.ui_font(26, QFont.Weight.ExtraLight))
        painter.drawText(QPointF(x0 - 2, y + 36), dashboard.clock(now))
        painter.setPen(theme.NOTCH_MUTED)
        painter.setFont(theme.ui_font(10))
        painter.drawText(QPointF(x0, y + 58), f"{now:%A}, {now.day} {now:%B}")

        # The weather, on the right.
        weather = snap.weather
        if weather is not None:
            painter.setFont(theme.ui_font(20, QFont.Weight.Light))
            temp = f"{round(weather.temp)}°"
            tw = painter.fontMetrics().horizontalAdvance(temp)
            painter.setPen(theme.NOTCH_TEXT)
            painter.drawText(QPointF(right - tw, y + 32), temp)
            _paint_weather_icon(painter, QPointF(right - tw - 24, y + 24), 15.0, weather.icon)
            painter.setFont(theme.ui_font(9))
            painter.setPen(theme.NOTCH_MUTED)
            line = f"{weather.text} · {weather.place}"
            if weather.high is not None and weather.low is not None:
                line += f" · {round(weather.high)}°/{round(weather.low)}°"
            line = painter.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, 230)
            painter.drawText(QRectF(right - 230, y + 44, 230, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, line)
        elif not snap.city:
            painter.setFont(theme.ui_font(9))
            painter.setPen(theme.NOTCH_MUTED)
            painter.drawText(QRectF(right - 230, y + 20, 230, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             "Add your city in Settings for the weather")
        y += 70
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        painter.drawLine(QPointF(x0, y), QPointF(right, y))

        # Today's classes, or the next day's.
        label, classes = dashboard.day_view(now, snap.classes)
        y += 8
        painter.setFont(theme.ui_font(8.6, QFont.Weight.DemiBold))
        painter.setPen(theme.NOTCH_MUTED)
        painter.drawText(QRectF(x0, y, 200, 16), Qt.AlignmentFlag.AlignVCenter, (label or "Classes").upper())
        note = dashboard.status(now, classes) if label == "Today" else ""
        if note:
            painter.setPen(flood)
            painter.drawText(QRectF(right - 220, y, 220, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, note)
        y += 22
        if not classes:
            painter.setFont(theme.ui_font(10, QFont.Weight.Light))
            painter.setPen(theme.NOTCH_TEXT)
            text = ("No classes in the next two weeks." if snap.timetable
                    else "Add your school timetable in Settings > General.")
            painter.drawText(QRectF(x0, y, right - x0, 26), Qt.AlignmentFlag.AlignVCenter, text)
            y += 30
        tx = x0 + 70
        for k in classes:
            ongoing = k.start <= now < k.end
            base = painter.opacity()
            if k.end <= now:
                painter.setOpacity(base * 0.4)  # already over today
            if ongoing:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(flood)
                painter.drawRoundedRect(QRectF(x0 - 12, y + 4, 3, 30), 1.5, 1.5)
            painter.setFont(theme.mono_font(8.6))
            painter.setPen(flood if ongoing else theme.NOTCH_MUTED)
            painter.drawText(QRectF(x0, y + 1, 66, 20), Qt.AlignmentFlag.AlignVCenter, dashboard.clock(k.start))
            painter.setFont(theme.ui_font(10.4, QFont.Weight.Medium if ongoing else QFont.Weight.Normal))
            painter.setPen(theme.NOTCH_TEXT)
            title = painter.fontMetrics().elidedText(k.title, Qt.TextElideMode.ElideRight, int(right - tx))
            painter.drawText(QRectF(tx, y, right - tx, 22), Qt.AlignmentFlag.AlignVCenter, title)
            painter.setFont(theme.ui_font(8.8))
            painter.setPen(theme.NOTCH_MUTED)
            where = " · ".join(b for b in (k.room, k.kind, f"until {dashboard.clock(k.end)}") if b)
            where = painter.fontMetrics().elidedText(where, Qt.TextElideMode.ElideRight, int(right - tx))
            painter.drawText(QRectF(tx, y + 19, right - tx, 18), Qt.AlignmentFlag.AlignVCenter, where)
            painter.setOpacity(base)
            y += 42

        # The keys to ask.
        y += 6
        x = self._paint_mini_keys(painter, x0, y + 2, 18.0)
        painter.setFont(theme.ui_font(8.8, QFont.Weight.Light))
        painter.setPen(theme.NOTCH_MUTED)
        painter.drawText(QRectF(x + 7, y + 2, 160, 16), Qt.AlignmentFlag.AlignVCenter, "hold to ask")
        painter.restore()

    def _paint_pill(self, painter: QPainter, rect: QRectF, t: float) -> None:
        """Top of the screen, listening or thinking: your buddy, one clean word,
        and a glowing pill in your buddy's colour whose dots move with your voice."""
        painter.save()
        self._scaled(painter, rect, self._fade_voice)
        listening = self._state == "listening"
        cy = rect.top() + rect.height() / 2.0
        theme.paint_avatar_in(painter, QRectF(rect.left() + 10, cy - 12, 24, 24), t=t,
                              mood="listening" if listening else "thinking", scale=24 / 48.0,
                              device_pixel_ratio=self.devicePixelRatioF(), glow=True)
        word = "Listening" if listening else "Thinking"
        painter.setFont(theme.ui_font(10.5, QFont.Weight.DemiBold))
        if listening:
            painter.setPen(theme.NOTCH_TEXT)
        else:  # a soft shimmer runs across the word while it thinks
            from PyQt6.QtGui import QLinearGradient
            w = painter.fontMetrics().horizontalAdvance(word)
            x = rect.center().x() - w / 2
            sweep = x - 40 + (w + 80) * ((t * 0.7) % 1.0)
            shine = QLinearGradient(sweep - 30, 0, sweep + 30, 0)
            shine.setColorAt(0.0, QColor(150, 150, 160))
            shine.setColorAt(0.5, QColor(255, 255, 255))
            shine.setColorAt(1.0, QColor(150, 150, 160))
            painter.setPen(QPen(shine, 1.0))
        painter.drawText(QRectF(rect.left(), rect.top(), rect.width(), rect.height()), Qt.AlignmentFlag.AlignCenter, word)
        self._paint_voice_pill(painter, QPointF(rect.right() - 34, cy), t, 46.0, 24.0,
                               self._smooth_level if listening else None)
        painter.restore()

    def _paint_voice_pill(self, painter: QPainter, centre: QPointF, t: float, w: float, h: float,
                          level: float | None, glow: float = 1.0) -> None:
        """The glowing pill: your buddy's colour, four dots. ``level`` (0-1)
        makes them jump with your voice; None makes them ripple (thinking, resting)."""
        flood = QColor(theme.current_palette().flood)
        pill = QRectF(centre.x() - w / 2, centre.y() - h / 2, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        for k in range(1, 6):  # the glow
            halo = QColor(flood)
            halo.setAlphaF(0.07 * glow * (1 - k / 6))
            painter.setBrush(halo)
            painter.drawRoundedRect(pill.adjusted(-k * 2.4, -k * 2.4, k * 2.4, k * 2.4), h / 2 + k * 2.4, h / 2 + k * 2.4)
        painter.setBrush(flood)
        painter.drawRoundedRect(pill, h / 2, h / 2)
        on = theme.current_palette().on_flood
        dot = QColor(on)
        gap = w / 5.0
        for k in range(4):
            if level is None:
                lift = math.sin(t * 5.0 - k * 0.8) * h * 0.12
                r = h * 0.11
            else:
                lift = 0.0
                r = h * (0.1 + 0.16 * level * (0.6 + 0.4 * math.sin(t * 11 + k * 1.7)))
            dot.setAlphaF(0.9)
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(pill.left() + gap * (k + 1), centre.y() - lift), r, r)

    def _paint_greet(self, painter: QPainter, rect: QRectF, t: float) -> None:
        """Your buddy, "Clixy here." and the keys to hold."""
        painter.save()
        self._scaled(painter, rect, self._fade_greet)
        width, height = self.GREET
        # Laid out at full size from the screen edge, so it slides out with the shape.
        x0 = rect.right() - width
        top = rect.center().y() - height / 2
        pad, buddy = 16.0, 46.0
        theme.paint_avatar_in(painter, QRectF(x0 + pad, top + (height - buddy) / 2, buddy, buddy), t=t,
                              mood="happy", scale=1.0, device_pixel_ratio=self.devicePixelRatioF(), glow=True)
        tx = x0 + pad + buddy + 12
        right = x0 + width - pad

        hello = "Clixy here"
        painter.setFont(theme.display_font(15.5))
        fm = painter.fontMetrics()
        base = top + 33
        painter.setPen(theme.NOTCH_TEXT)
        painter.drawText(QPointF(tx, base), hello)
        painter.setPen(theme.current_palette().flood)
        painter.drawText(QPointF(tx + fm.horizontalAdvance(hello), base), ".")

        x = self._paint_mini_keys(painter, tx, top + 44, 19.0)
        painter.setFont(theme.ui_font(8.8, QFont.Weight.Light))
        hint = "hold to ask"
        if x + 7 + painter.fontMetrics().horizontalAdvance(hint) <= right:
            painter.setPen(theme.NOTCH_MUTED)
            painter.drawText(QRectF(x + 7, top + 44, right - x - 7, 17), Qt.AlignmentFlag.AlignVCenter, hint)
        painter.restore()

    def _paint_mini_keys(self, painter: QPainter, x: float, y: float, cap_h: float) -> float:
        """The shortcut as small coral keycaps (the look of Settings' keycaps);
        returns where they end."""
        from widgets import _paint_windows_logo
        font = theme.ui_font(8.4, QFont.Weight.DemiBold)
        fm = QFontMetricsF(font)
        lip, radius, pad = 2.5, 5.0, 7.0
        labels = self.hotkey.split(" + ")
        for i, label in enumerate(labels):
            logo = fm.capHeight() * 1.05 + 4 if label == "Win" else 0.0
            w = max(cap_h, fm.horizontalAdvance(label) + logo + 2 * pad)
            body = QRectF(x, y, w, cap_h - lip)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(190, 60, 36))
            painter.drawRoundedRect(body.translated(0, lip), radius, radius)
            painter.setBrush(theme.CORAL)
            painter.setPen(QPen(QColor(255, 130, 100), 1.0))
            painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            white = QColor(255, 255, 255)
            painter.setFont(font)
            painter.setPen(white)
            if logo:
                size = fm.capHeight() * 1.05
                left = body.center().x() - (logo + fm.horizontalAdvance(label)) / 2
                _paint_windows_logo(painter, QRectF(left, body.center().y() - size / 2, size, size), white)
                painter.setPen(white)
                painter.drawText(QRectF(left + logo, body.top(), body.width(), body.height()),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            else:
                painter.drawText(body, Qt.AlignmentFlag.AlignCenter, label)
            x += w
            if i < len(labels) - 1:
                painter.setPen(theme.NOTCH_MUTED)
                painter.setFont(theme.ui_font(9.0))
                painter.drawText(QRectF(x, y, 14, cap_h - lip), Qt.AlignmentFlag.AlignCenter, "+")
                x += 14
        return x

    def _paint_card(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        self._scaled(painter, rect, self._fade_card)
        # Content is laid out at full card width from the inner edge.
        x0 = (rect.right() - self.CARD_W if self.side == "right" else rect.left()) + self._PAD
        if self.top:
            x0 = rect.left() + self._PAD
        y = rect.top() + self._PAD
        width = self._card_w() - 2 * self._PAD

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


def _refresh_dashboard() -> None:
    try:
        import dashboard
        dashboard.refresh()
    except Exception:
        pass


def frost(image: QImage, radius: int = 12) -> QImage:
    """A soft blur, cheaply: shrink a lot, then grow back smoothly, twice."""
    if image.isNull():
        return image
    w, h = image.width(), image.height()
    smooth = Qt.TransformationMode.SmoothTransformation
    small = image.scaled(max(1, w // radius), max(1, h // radius), Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
    mid = small.scaled(max(1, w // 3), max(1, h // 3), Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
    return mid.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, smooth)


def _round_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def _paint_weather_icon(painter: QPainter, c: QPointF, r: float, kind: str) -> None:
    """A small weather glyph centred on ``c``: sun, partly, cloud, rain, snow, storm, fog."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    sun = kind in ("sun", "partly")
    if sun:
        centre = QPointF(c.x() + (r * 0.35 if kind == "partly" else 0), c.y() - (r * 0.3 if kind == "partly" else 0))
        rad = r * (0.42 if kind == "partly" else 0.5)
        painter.setPen(_round_pen(theme.SUN, 1.6))
        for i in range(8):
            a = math.pi * i / 4
            painter.drawLine(QPointF(centre.x() + math.cos(a) * rad * 1.35, centre.y() + math.sin(a) * rad * 1.35),
                             QPointF(centre.x() + math.cos(a) * rad * 1.75, centre.y() + math.sin(a) * rad * 1.75))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.SUN)
        painter.drawEllipse(centre, rad, rad)
    if kind != "sun":
        cloud = QColor(235, 238, 245) if kind != "storm" else QColor(150, 155, 170)
        painter.setBrush(cloud)
        base = QPointF(c.x() - r * 0.15, c.y() + r * 0.15)
        for dx, dy, cr in ((-0.45, 0.1, 0.38), (0.0, -0.15, 0.5), (0.45, 0.1, 0.36)):
            painter.drawEllipse(QPointF(base.x() + dx * r, base.y() + dy * r), cr * r, cr * r)
        painter.drawRoundedRect(QRectF(base.x() - 0.8 * r, base.y(), 1.6 * r, 0.48 * r), 0.24 * r, 0.24 * r)
        below = base.y() + 0.75 * r
        if kind == "rain":
            painter.setPen(_round_pen(QColor(110, 180, 255), 1.6))
            for dx in (-0.4, 0.0, 0.4):
                painter.drawLine(QPointF(base.x() + dx * r, below), QPointF(base.x() + dx * r - 3, below + 6))
        elif kind == "snow":
            painter.setBrush(QColor(255, 255, 255))
            for dx in (-0.4, 0.0, 0.4):
                painter.drawEllipse(QPointF(base.x() + dx * r, below + 3), 1.6, 1.6)
        elif kind == "storm":
            painter.setBrush(theme.SUN)
            bolt = [QPointF(base.x() + 1, below - 2), QPointF(base.x() - 4, below + 5), QPointF(base.x(), below + 5),
                    QPointF(base.x() - 3, below + 11), QPointF(base.x() + 5, below + 2), QPointF(base.x() + 1, below + 2)]
            from PyQt6.QtGui import QPolygonF
            painter.drawPolygon(QPolygonF(bolt))
        elif kind == "fog":
            painter.setPen(_round_pen(QColor(200, 205, 215), 1.6))
            for i in range(2):
                painter.drawLine(QPointF(base.x() - 0.7 * r, below + i * 4), QPointF(base.x() + 0.7 * r, below + i * 4))
    painter.restore()


def _first_name() -> str:
    try:
        import sysinfo
        return sysinfo.first_name()
    except Exception:
        return ""


def _hotkey_text() -> str:
    try:
        from config import resolve_setting
        from hotkey import DEFAULT_COMBO, describe
        return describe(resolve_setting("HOTKEY", DEFAULT_COMBO))
    except Exception:
        return "Ctrl + Win"


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
