"""Clixy's status island: a notch at the top of the screen (the default),
like a laptop's notch, or a tab on the left or right edge.

The look: a flat, pure-black tab fused to the screen edge. Where it meets
the edge its shoulders flare out in a concave curve; its lower corners are
gently rounded. One light glows at its end in your buddy's colour (Ember
burns red-orange, Splash blue), bright at the centre, deeper at the rim.

At the top:
- Resting: a slim black notch; the light glows faintly and breathes, and a
  thread of its colour runs along the notch's lower edge.
- Hello: "Hey Rayan!" (and a warm chime) when Clixy starts and the first
  time each day.
- Listening: it widens and says "Listening"; little gel bars in the buddy's
  colours dance with your voice and the light swells with it.
- Thinking: "Thinking", with a soft shimmer, and three dots hopping in turn.
- Speaking: back to the slim notch with the light alive and tiny bars
  talking beside it while the answer plays. The answer is only ever heard,
  never shown. When it settles, the light throws a little burst of sparks.
- A notice (an error, "I didn't catch that") in a line or two, then it
  tucks away. A guide step shows only "Step 2 of 3".
- Rest the mouse on it and it shows the keys to hold; click it and the
  Clixy panel grows out of it (panel.py): your newest sketch note, a box to
  type a question, and a guide's steps while one runs. (The older hover
  dashboard, dashboard.py, is still here behind ``dashboard = True``.)

On a side edge the same black tab grows out of the edge: a sliver at rest,
a tall capsule with the light and a waveform while listening, and a short
tab for hello, notices and guide steps.

Size follows a spring, so every change of state is one fluid motion; words
fade in once the shape is nearly there. Like the overlay it is
click-through (except the shape itself while the mouse is on it, so a
click there opens the panel instead of landing in the window below), never
takes focus, and hides while the screen is captured so the model never
sees it.

Threading: Qt main thread only; app.py reaches it through the overlay
controller's island_* methods via pyqtSignal slots.
"""
from __future__ import annotations

import datetime
import math
import sys
import time

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
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
    QPen,
    QRadialGradient,
    QScreen,
    QTransform,
)
from PyQt6.QtWidgets import QWidget

import theme
from ui_kit import sound


class SideIsland(QWidget):
    # At the top of the screen, like a laptop's notch.
    NOTCH = (150.0, 24.0)
    HELLO = (196.0, 36.0)
    HINT = (292.0, 36.0)
    PILL = (232.0, 40.0)
    DASH_W = 440.0
    NOTE_W = 380.0               # the widest a notice gets
    _TOP_WIN = (540, 380)        # room for the shoulders and the shadow
    _HELLO_S = 3.2
    _DWELL = 0.5                 # s the mouse rests on it before it shows anything
    _INSET = 22.0                # the words' left margin

    # On a side edge.
    SLIVER = (8.0, 58.0)
    GREET = (232.0, 72.0)
    CAPSULE = (56.0, 172.0)
    _WIN_W = 400   # room for a notice plus its shadow
    _WIN_H = 340
    _TOP = 0.34    # vertical centre, as a fraction of the screen's height
    _STIFF, _DAMP = 0.16, 0.70   # per 60 Hz frame: slight overshoot, settles in ~0.5 s
    _PEEK_REACH = 16             # px from the edge: the mouse has to be on the sliver
    _LINGER = 0.6                # s it stays open after the mouse leaves
    _BARS = 13
    _BURST_S = 0.75              # how long the sparks fly

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
        """What Clixy is saying: kept for the turn, only ever heard, never shown."""
        self._highlights: list[str] = []
        self._meta = ""
        self._note = ""
        self._step = ""
        """Guide mode: "Step 2 of 3", the only words a step shows."""
        self._level = 0.0
        self._smooth_level = 0.0
        self.model_name = ""
        """The model in use; app.py sets it, e.g. 'Haiku 5.5'."""
        self.hotkey = _hotkey_text()
        """The keys to hold, as shown when it greets you, e.g. 'Ctrl + Win'."""
        self.name = _first_name()
        """Who it says hello to ("Rayan"); empty says "Hey there!"."""
        self.dashboard = False
        """Resting the mouse on the top notch shows the dashboard (else just the
        keys). Off: a click opens the Clixy panel, the main surface now."""
        if self.side == "top" and self.dashboard:
            _refresh_dashboard()  # ready for the first time you rest on it
        self.panel = None
        """The Clixy panel (panel.py), made the first time you click the notch."""
        self.on_ask = None
        """app.py: called with a question typed in the panel (ClickyApp.ask_text)."""
        self.on_settings = None
        """app.py: opens the Settings window (if the panel's own Settings can't open)."""
        self.on_settings_saved = None
        """app.py: called with restart_needed after Done in the panel's Settings."""
        self.on_open_note = None
        """app.py: shows a saved note (its dict) again as the real note card."""
        self._catching = False
        self._was_down = False
        self._t0 = time.monotonic()
        self._hello_until = self._t0 + self._HELLO_S
        self._greeted_on = datetime.date.today()
        self._hover_since: float | None = None
        self._frame = 0
        self._last_tick = self._t0
        self._wrap_cache: dict = {}

        # Spring state: width, height (the shape is centred in the window).
        self._w, self._h = self.NOTCH if self.side == "top" else self.SLIVER
        self._vw = self._vh = 0.0
        self._fade_voice = 0.0
        self._fade_note = 0.0
        self._fade_greet = 0.0
        self._fade_notch = 0.0
        self._fade_dash = 0.0
        # The light: its core's radius and how bright it burns (0-1), eased.
        self._light_r, self._light_b = 2.5, 0.3
        self._glass: QImage | None = None
        """A blur of the screen behind, taken as the dashboard opens."""
        self._near = False
        self._near_until = 0.0
        self._burst_t0 = -10.0
        """When the light last threw its sparks (an answer or a guide settling)."""
        self._hello_sounded = 0.0

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
        self._catching = False
        try:
            from overlay import apply_clickthrough_styles
            apply_clickthrough_styles(int(self.winId()))
        except Exception:
            pass  # not on Windows (tests, previews)

    def _set_catching(self, on: bool) -> None:
        """Take clicks (``on``) or let them through to the window below."""
        if on == self._catching:
            return
        self._catching = on
        if sys.platform != "win32":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            style = user32.GetWindowLongW(hwnd, -20)          # GWL_EXSTYLE
            user32.SetWindowLongW(hwnd, -20, (style & ~0x20) if on else (style | 0x20))  # WS_EX_TRANSPARENT
        except Exception:
            pass

    def hide_for_capture(self) -> None:
        self.hide()
        if self.panel is not None:
            self.panel.hide_for_capture()

    def show_after_capture(self) -> None:
        self.show()
        self._apply_clickthrough()
        if self.panel is not None:
            self.panel.show_after_capture()

    # --- The Clixy panel -----------------------------------------------------

    def _on_shape(self) -> bool:
        geo = self.geometry()
        box = self._shape_path().boundingRect().translated(geo.x(), geo.y()).adjusted(-2, -2, 2, 4)
        return box.contains(QPointF(QCursor.pos()))

    def _watch_click(self) -> None:
        """A click on the shape opens the Clixy panel. While the mouse is on
        it the island takes clicks, so the click never lands in the window
        under the notch; everywhere else it stays click-through."""
        panel_open = self.panel is not None and self.panel.isVisible()
        if not self.isVisible() or panel_open or self._state in ("listening", "thinking"):
            self._set_catching(False)
            self._was_down = _left_down()
            return
        on = self._on_shape()
        self._set_catching(on)
        down = _left_down()
        if on and down and not self._was_down:
            self.open_panel()
        self._was_down = down

    def _status_word(self) -> str:
        if self._state == "listening":
            return "Listening"
        if self._state == "thinking":
            return "Thinking"
        if self._state == "answer":
            return "Guiding" if self._step else "Speaking"
        return "Ready"

    def open_panel(self, focus_ask: bool = False) -> None:
        """Grow the Clixy panel out of the notch (top centre, whichever edge
        the island lives on)."""
        try:
            from panel import ClixyPanel, guide_progress
        except Exception:
            return
        if self.panel is None:
            self.panel = ClixyPanel()
            self.panel.status_fn = self._status_word
            self.panel.guide_fn = guide_progress
            self.panel.asked.connect(self._panel_ask)
            self.panel.settings_requested.connect(self._panel_settings)
            self.panel.settings_saved.connect(self._panel_settings_saved)
            self.panel.stopped.connect(self._collapse)
            self.panel.note_opened.connect(self._panel_open_note)
        self.panel.hotkey = self.hotkey
        self.panel.name = self.name
        self._near, self._near_until, self._hover_since = False, 0.0, None
        self.panel.open_on(self._screen or self.screen(), focus_ask=focus_ask)

    def _panel_ask(self, question: str) -> None:
        if callable(self.on_ask):
            self.on_ask(question)

    def _panel_settings(self) -> None:
        if callable(self.on_settings):
            self.on_settings()

    def _panel_open_note(self, note: dict) -> None:
        if callable(self.on_open_note):
            self.on_open_note(note)

    def _panel_settings_saved(self, restart: bool) -> None:
        if callable(self.on_settings_saved):
            self.on_settings_saved(restart)

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
        self._step = ""
        self._meta = self.model_name
        self.update()

    def thinking(self) -> None:
        self._collapse_timer.stop()
        if self._state == "listening":
            sound("think")
        if self._state != "answer":
            self._state = "thinking"
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(float(level), 1.0))

    def set_transcript(self, text: str) -> None:
        self._transcript = text.strip()

    def say(self, sentence: str) -> None:
        """A sentence of the answer as it goes to the voice. It is heard, not
        shown: the notch settles back and its light comes alive while it plays."""
        sentence = sentence.strip()
        if not sentence:
            return
        self._collapse_timer.stop()
        self._state = "answer"
        self._sentences.append(sentence)
        self.update()

    def highlight(self, label: str) -> None:
        """What the pointer points at; the pointer shows it, so it's only kept."""
        label = (label or "").strip()
        if label and label.lower() not in (h.lower() for h in self._highlights):
            self._highlights.append(label)

    def step(self, text: str, n: int, total: int = 0, keep_clear: QRect | None = None) -> None:
        """Guide mode: this step ("Step 2 of 3") until the next one; the step
        itself is spoken. ``keep_clear`` (global coords) is what you have to
        click: if the island would cover it, it moves to another edge."""
        side = self.home_side
        if keep_clear is not None and self.geometry().adjusted(-24, -24, 24, 24).intersects(keep_clear):
            if self.home_side == "top":  # the far side from what you have to click
                screen = self._screen.geometry() if self._screen is not None else self.geometry()
                side = "left" if keep_clear.center().x() > screen.center().x() else "right"
            else:
                side = "left" if self.home_side == "right" else "right"
        self._move_to(side)
        self._collapse_timer.stop()
        if n > 1:
            sound("select")          # on to the next step
        self._state = "answer"
        self._sentences = [text.strip()] if text.strip() else []
        self._highlights = []
        self._meta = f"step {n} of {total}" if total >= n > 0 else (f"step {n}" if n > 0 else "")
        self._step = self._meta.capitalize() or "Next step"
        self.update()

    def set_meta(self, text: str) -> None:
        self._meta = text

    def note(self, text: str) -> None:
        """A short notice in place of an answer ("I didn't catch that")."""
        self._collapse_timer.stop()
        self._state = "note"
        self._note = " ".join(text.split())
        sound("error", 0.7)
        self.update()
        self._collapse_timer.start(6000)

    def done(self) -> None:
        """The turn is over. The answer may still be playing, so the light stays
        alive about as long as it takes to say, then the notch rests."""
        if self._state == "note":
            return  # the note set its own timer
        if self._state in ("listening", "thinking"):
            self._collapse()
            return
        if self._step:                   # a guide finished: a sparkle and a little fanfare
            sound("done")
            self._burst_t0 = time.monotonic()
        words = sum(len(s.split()) for s in self._sentences)
        self._collapse_timer.start(int(1000 * max(2.0, min(30.0, 1.0 + 0.36 * words))))

    def _collapse(self) -> None:
        if self._state == "answer" and not self._step:
            self._burst_t0 = time.monotonic()      # the answer settles with a little burst of sparks
        self._state = "idle"
        self._step = ""
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

    def _hello(self) -> bool:
        return self.top and self._state == "idle" and time.monotonic() < self._hello_until

    def _notice(self) -> str:
        """The words of a notice or a guide step, if one is showing."""
        if self._state == "note":
            return self._note
        if self._state == "answer" and self._step:
            return self._step
        return ""

    def _target(self) -> tuple[float, float]:
        if self._state in ("listening", "thinking"):
            return self.PILL if self.top else self.CAPSULE
        if self._notice():
            return self._notice_size()
        if self._state == "answer":  # speaking: heard, not shown
            return self.NOTCH if self.top else self.SLIVER
        if self.top:
            if self._hello():
                return self.HELLO
            if self._near:
                return (self.DASH_W, self._dash_height()) if self.dashboard else self.HINT
            return self.NOTCH
        return self.GREET if self._near else self.SLIVER

    def _dash_height(self) -> float:
        return 36 + 70 + 12 + 18 + 18   # the hello, the time and weather, the keys

    def _notice_lines(self) -> tuple[QFont, float, list[str]]:
        """The notice wrapped to at most three lines: (font, line height, lines).
        A guide step is one line in the state words' type."""
        step = self._state == "answer"
        font = _word_font() if step else _note_font()
        width = self.NOTE_W - self._INSET - 52
        key = (self._notice(), step, round(width))
        cached = self._wrap_cache.get(key)
        if cached is None:
            metrics = QFontMetricsF(font)
            lines = [self._notice()] if step else _wrap(self._notice(), metrics, width, 3)
            cached = (font, metrics.height() * 1.12, lines)
            if len(self._wrap_cache) > 16:
                self._wrap_cache.clear()
            self._wrap_cache[key] = cached
        return cached

    def _notice_size(self) -> tuple[float, float]:
        font, line_h, lines = self._notice_lines()
        metrics = QFontMetricsF(font)
        text_w = max((metrics.horizontalAdvance(line) for line in lines), default=0.0)
        width = min(self.NOTE_W, max(self.PILL[0], self._INSET + text_w + 52))
        return width, self.PILL[1] + max(0, len(lines) - 1) * line_h

    def _shape_rect(self) -> QRectF:
        """The black shape's body, flush with the screen edge (its shoulders
        flare out a little past it, along the edge)."""
        if self.top:
            return QRectF((self.width() - self._w) / 2.0, 0.0, self._w, self._h)
        cy = self.height() / 2.0
        if self.side == "right":
            return QRectF(self.width() - self._w, cy - self._h / 2.0, self._w, self._h)
        return QRectF(0.0, cy - self._h / 2.0, self._w, self._h)

    def _shape_path(self) -> QPainterPath:
        rect = self._shape_rect()
        if self.top:
            return tab_path(rect.width(), rect.height()).translated(rect.left(), rect.top())
        # A side tab is the top one turned: its length runs along the edge.
        local = tab_path(rect.height(), rect.width())
        if self.side == "left":
            turn = QTransform(0, 1, 1, 0, 0, rect.top())                 # (u, v) -> (v, top + u)
        else:
            turn = QTransform(0, 1, -1, 0, self.width(), rect.top())     # (u, v) -> (W - v, top + u)
        return turn.map(local)

    def _row(self, rect: QRectF) -> float:
        """The centre line of the words and the light: the middle of the slim
        notch, settling at the middle of the listening tab as it grows."""
        return rect.top() + min(rect.height(), self.PILL[1]) / 2.0

    def _light_pos(self, rect: QRectF) -> QPointF:
        """The light sits at the notch's right end."""
        depth = min(rect.height(), self.PILL[1])
        return QPointF(rect.right() - (8.0 + 0.4 * depth), self._row(rect))

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
        if self._near and not was_near:
            sound("hover")
        if self._near and not was_near and self.top and self.dashboard:
            _refresh_dashboard()  # fresh weather and classes while it opens
            self._glass = self._frosted_backdrop()

    def _light_target(self, t: float) -> tuple[float, float]:
        """(core radius, brightness) for the light in this state, at time ``t``."""
        state = self._state
        if state == "listening":  # swells with your voice
            level = self._smooth_level
            return 3.9 + 2.2 * level, 0.7 + 0.3 * min(1.0, 1.6 * level)
        if state == "thinking":   # a slow breath
            b = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.7)
            return 3.4 + 1.0 * b, 0.42 + 0.45 * b
        if state == "answer":
            if self._step:
                return 3.6, 0.85
            e = _speech(t)        # alive, like a voice, while the answer plays
            return 3.0 + 1.6 * e, 0.55 + 0.4 * e
        if state == "note":
            return 3.2, 0.6
        if self._hello():
            return 3.8, 0.9
        if self._near:
            return 3.6, 0.8
        b = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 5.0)  # resting: barely there
        return 2.5 + 0.2 * b, 0.3 + 0.12 * b

    def _animate(self) -> None:
        now = time.monotonic()
        frames = max(1, min(4, round((now - self._last_tick) * 60)))
        self._last_tick = now
        self._update_near()
        self._watch_click()
        if self.top and self._state == "idle" and datetime.date.today() != self._greeted_on:
            self._greeted_on = datetime.date.today()  # first time each day: say hello again
            self._hello_until = now + self._HELLO_S
        if self._hello() and self._hello_sounded != self._hello_until \
                and now > self._hello_until - self._HELLO_S + 0.6:
            self._hello_sounded = self._hello_until
            sound("hello")
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
            voice_on = self._state in ("listening", "thinking") and self._h > self.CAPSULE[1] * 0.8 and self._w > 40
            greet_on = self._state == "idle" and self._near and self._w > self.GREET[0] * 0.8
        note_on = bool(self._notice()) and self._w > tw * 0.85
        notch_on = self.top and self._state == "idle" and self._h < 44
        dash_on = (self.top and self.dashboard and self._state == "idle" and self._near
                   and self._w > self.DASH_W * 0.85 and self._h > 90)
        for attr, on, rate in (("_fade_voice", voice_on, 0.22), ("_fade_note", note_on, 0.2),
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
        radius, bright = self._light_target(now - self._t0)
        ease = min(1.0, (0.4 if self._state == "listening" else 0.16) * frames)
        self._light_r += (radius - self._light_r) * ease
        self._light_b += (bright - self._light_b) * ease

        self._frame += 1
        if moving or self._state != "idle" or now - self._burst_t0 < self._BURST_S:
            self.update()
        elif self._fade_dash > 0.01 and self._frame % 20 == 0:
            self.update()  # the dashboard's clock
        elif self._frame % 3 == 0:
            # Resting, the light breathes: repaint just the shape.
            self.update(self._shape_path().boundingRect().toAlignedRect().adjusted(-2, -2, 2, 2))

    # --- Painting ------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self._shape_path()
        rect = self._shape_rect()
        self._paint_shadow(painter, path)

        painter.setPen(Qt.PenStyle.NoPen)
        glass = self._fade_dash if self._glass is not None else 0.0
        if glass > 0.01:
            # Frosted glass: a blur of what's behind, darkened enough to read on.
            painter.save()
            painter.setClipPath(path)
            painter.drawImage(QRectF(self.rect()), self._glass)
            painter.restore()
        painter.setBrush(QBrush(self._depth_gradient(rect, theme.NOTCH, theme.NOTCH_NAVY, 1.0 - 0.3 * glass)))
        painter.drawPath(path)
        # A hairline, fading in away from the screen edge, so black still reads on a dark page.
        edge = QColor(theme.NOTCH_EDGE)
        edge.setAlphaF(min(1.0, edge.alphaF() * (1.0 + 1.2 * glass)))
        clear = QColor(edge)
        clear.setAlphaF(0.0)
        painter.setPen(QPen(QBrush(self._depth_gradient(rect, clear, edge, 1.0, reach=18.0)), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)

        painter.setClipPath(path)
        t = time.monotonic() - self._t0
        accent = theme.current_palette().flood
        if self.top:
            self._paint_thread(painter, rect, accent)
            if self._fade_notch > 0.01:
                self._paint_notch(painter, rect)
            if self._fade_dash > 0.01:
                self._paint_dash(painter, rect)
            if self._fade_voice > 0.01:
                self._paint_pill(painter, rect, t)
            if self._state == "answer" and not self._step and self._fade_voice < 0.5:
                light = self._light_pos(rect)
                self._paint_bars(painter, light.x() - 14.0, light.y(), t, "speak", accent, 1.0)
            if self._fade_note > 0.01:
                self._paint_notice(painter, rect, accent)
            room = min(rect.height(), self.PILL[1]) / 2.0
            paint_light(painter, self._light_pos(rect), self._light_r, self._light_b, accent, room)
            painter.setClipping(False)
            self._paint_burst(painter, self._light_pos(rect), accent)
            return
        if self._w < 24:
            self._paint_sliver(painter, rect, accent)
        if self._fade_voice > 0.01:
            self._paint_voice(painter, rect, t, accent)
        if self._fade_greet > 0.01:
            self._paint_greet(painter, rect, accent)
        if self._fade_note > 0.01:
            self._paint_notice(painter, rect, accent)

    def _depth_gradient(self, rect: QRectF, at_edge: QColor, inward: QColor, alpha: float,
                        reach: float | None = None) -> QLinearGradient:
        """A gradient from the screen edge into the screen: black at the edge,
        a breath of navy at the far end (or over ``reach`` px)."""
        if self.top:
            start, depth = QPointF(rect.center().x(), rect.top()), rect.height()
            direction = QPointF(0.0, 1.0)
        elif self.side == "right":
            start, depth = QPointF(rect.right(), rect.center().y()), rect.width()
            direction = QPointF(-1.0, 0.0)
        else:
            start, depth = QPointF(rect.left(), rect.center().y()), rect.width()
            direction = QPointF(1.0, 0.0)
        span = reach if reach is not None else depth
        gradient = QLinearGradient(start, start + direction * max(1.0, span))
        a, b = QColor(at_edge), QColor(inward)
        a.setAlphaF(a.alphaF() * alpha)
        b.setAlphaF(b.alphaF() * alpha)
        gradient.setColorAt(0.0, a)
        if reach is None:
            gradient.setColorAt(0.45, a)  # pure black for the first half
        gradient.setColorAt(1.0, b)
        return gradient

    def _paint_shadow(self, painter: QPainter, path: QPainterPath) -> None:
        """A soft shadow onto the screen under the bigger shapes (the dashboard,
        a notice, the side tabs): stacked wide strokes of faint black, fading
        out toward the screen edge so the shoulders stay crisp."""
        depth = self._h if self.top else self._w
        strength = max(0.0, min(1.0, (depth - 42.0) / 36.0))
        if strength <= 0:
            return
        rect = self._shape_rect()
        if self.top:
            shadow = path.translated(0.0, 4.0)
        else:
            shadow = path.translated(-3.0 if self.side == "right" else 3.0, 4.0)
        dark = QColor(0, 0, 0, round(9 * strength))
        clear = QColor(0, 0, 0, 0)
        brush = QBrush(self._depth_gradient(rect, clear, dark, 1.0, reach=26.0))
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(1, 9):
            pen = QPen(brush, i * 4.0)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(shadow)
        painter.restore()

    def _scaled(self, painter: QPainter, rect: QRectF, fade: float) -> None:
        """Fade in with a little grow, from the edge the shape grows out of."""
        painter.setOpacity(fade)
        if self.top:
            anchor = QPointF(rect.center().x(), rect.top())
        else:
            anchor = QPointF(rect.right() if self.side == "right" else rect.left(), rect.center().y())
        scale = 0.96 + 0.04 * fade
        painter.translate(anchor)
        painter.scale(scale, scale)
        painter.translate(-anchor)

    def _paint_word(self, painter: QPainter, x: float, cy: float, word: str, shimmer: float | None = None) -> None:
        """A state word ("Listening") in white SemiBold, centred on ``cy``.
        ``shimmer`` (0-1, where the shine is) runs a soft light across it."""
        painter.setFont(_word_font())
        if shimmer is None:
            painter.setPen(theme.NOTCH_TEXT)
        else:
            w = painter.fontMetrics().horizontalAdvance(word)
            sweep = x - 40 + (w + 80) * shimmer
            shine = QLinearGradient(sweep - 34, 0, sweep + 34, 0)
            shine.setColorAt(0.0, QColor(255, 255, 255, 140))
            shine.setColorAt(0.5, QColor(255, 255, 255, 255))
            shine.setColorAt(1.0, QColor(255, 255, 255, 140))
            painter.setPen(QPen(QBrush(shine), 1.0))
        painter.drawText(QRectF(x, cy - 14, 400, 28), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, word)

    def _paint_notch(self, painter: QPainter, rect: QRectF) -> None:
        """The idle notch's words: "Hey Rayan!" while saying hello; the keys
        to hold while the mouse rests on it (with the dashboard off)."""
        hello, hint = self._hello(), self._near and not self._hello()
        if not (hello or hint):
            return  # resting: just the light
        painter.save()
        # How far the shape has opened toward its wider form.
        wide = self.HELLO[0] if hello else self.HINT[0]
        k = max(0.0, min(1.0, (rect.width() - self.NOTCH[0]) / (wide - self.NOTCH[0])))
        painter.setOpacity(self._fade_notch * k)
        x, cy = rect.left() + self._INSET, self._row(rect)
        if hello:
            self._paint_word(painter, x, cy, f"Hey {self.name}!" if self.name else "Hey there!")
        else:
            x = self._paint_mini_keys(painter, x, cy - 9.0, 18.0)
            painter.setFont(theme.ui_font(9.0, QFont.Weight.Medium))
            painter.setPen(theme.NOTCH_MUTED)
            painter.drawText(QRectF(x + 8, cy - 9.0, max(0.0, rect.right() - x - 48), 18),
                             Qt.AlignmentFlag.AlignVCenter, "hold to ask, or click")
        painter.restore()

    def _paint_pill(self, painter: QPainter, rect: QRectF, t: float) -> None:
        """Top of the screen, listening or thinking: one word, left of centre,
        and beside the light the bars (listening) or hopping dots (thinking)
        (the light at the right end is painted for every state)."""
        painter.save()
        painter.setOpacity(self._fade_voice)
        listening = self._state == "listening"
        self._paint_word(painter, rect.left() + self._INSET, self._row(rect),
                         "Listening" if listening else "Thinking",
                         None if listening else (t * 0.62) % 1.0)
        light = self._light_pos(rect)
        self._paint_bars(painter, light.x() - 17.0, light.y(), t, "listen" if listening else "think",
                         theme.current_palette().flood, self._fade_voice)
        painter.restore()

    def _paint_bars(self, painter: QPainter, right: float, cy: float, t: float, mode: str, accent: QColor,
                    fade: float) -> None:
        """Little gel bars ending at ``right``: dancing with your voice ("listen"),
        talking while the answer plays ("speak"), or three dots hopping ("think")."""
        hot, mid, deep = accent_shades(accent)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        if mode == "think":
            for i in range(3):
                hop = max(0.0, math.sin(t * 6.2 - i * 0.95))
                c = QPointF(right - 4 - (2 - i) * 8.0, cy - 4.5 * hop)
                g = QRadialGradient(c - QPointF(0.6, 0.8), 3.2)
                g.setColorAt(0.0, hot)
                g.setColorAt(1.0, _mix(mid, deep, 0.3))
                painter.setOpacity(fade * (0.55 + 0.45 * hop))
                painter.setBrush(QBrush(g))
                painter.drawEllipse(c, 2.6, 2.6)
            painter.restore()
            return
        n, w, gap, top = (5, 3.0, 2.6, 17.0) if mode == "listen" else (3, 2.4, 2.4, 11.0)
        level = self._smooth_level
        centre = (n - 1) / 2
        for i in range(n):
            weight = 1.0 - abs(i - centre) / (centre + 1.0)
            if mode == "listen":
                wobble = 0.65 + 0.35 * math.sin(t * 10.0 + i * 1.7)
                h = 3.5 + (top - 3.5) * min(1.0, level * 1.6) * (0.45 + 0.55 * weight) * wobble
            else:
                h = 3.0 + (top - 3.0) * _speech(t * 1.15 + i * 0.41) * (0.6 + 0.4 * weight)
            x = right - (n - i) * (w + gap) + gap
            bar = QRectF(x, cy - h / 2.0, w, h)
            g = QLinearGradient(bar.topLeft(), bar.bottomLeft())
            g.setColorAt(0.0, hot)
            g.setColorAt(0.5, mid)
            g.setColorAt(1.0, deep)
            painter.setOpacity(fade)
            painter.setBrush(QBrush(g))
            painter.drawRoundedRect(bar, w / 2.0, w / 2.0)
        painter.restore()

    def _paint_thread(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        """A thread of the buddy's colour along the notch's lower edge, brightest
        under the light, as if the light were spilling along it."""
        if rect.height() < 12:
            return
        light = self._light_pos(rect)
        y = rect.bottom() - 0.8
        line = QLinearGradient(rect.left(), y, rect.right(), y)
        clear = QColor(accent)
        clear.setAlpha(0)
        lit = QColor(accent)
        lit.setAlphaF(min(1.0, 0.25 + 0.6 * self._light_b))
        at = max(0.05, min(0.95, (light.x() - rect.left()) / max(1.0, rect.width())))
        line.setColorAt(0.0, clear)
        line.setColorAt(max(0.0, at - 0.42), clear)
        line.setColorAt(at, lit)
        line.setColorAt(min(1.0, at + 0.12), clear)
        line.setColorAt(1.0, clear)
        painter.save()
        pen = QPen(QBrush(line), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        r = min(14.0, rect.height() * 0.4)
        painter.drawLine(QPointF(rect.left() + r, y), QPointF(rect.right() - r, y))
        painter.restore()

    def _paint_burst(self, painter: QPainter, centre: QPointF, accent: QColor) -> None:
        """Sparks flying out of the light (an answer or a guide just settled)."""
        k = (time.monotonic() - self._burst_t0) / self._BURST_S
        if not 0.0 <= k < 1.0:
            return
        hot, mid, _deep = accent_shades(accent)
        ease = 1.0 - (1.0 - k) ** 3
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(9):
            a = -math.pi * 0.95 + i * (math.pi * 1.9 / 8.0) + math.pi / 2
            reach = (10.0 + 22.0 * ease) * (0.8 + 0.25 * ((i * 7) % 3))
            c = QPointF(centre.x() + math.cos(a) * reach, centre.y() + 4.0 + math.sin(a) * reach * 0.8)
            size = (2.6 if i % 2 else 1.8) * (1.0 - 0.6 * k)
            painter.setOpacity((1.0 - k) ** 1.4)
            painter.setBrush(hot if i % 2 else mid)
            sparkle = QPainterPath(c + QPointF(0, -size * 1.8))
            for dx, dy in ((0.35, -0.35), (1.8, 0.0), (0.35, 0.35), (0.0, 1.8), (-0.35, 0.35), (-1.8, 0.0),
                           (-0.35, -0.35)):
                sparkle.lineTo(c + QPointF(dx * size, dy * size))
            sparkle.closeSubpath()
            painter.drawPath(sparkle)
        painter.restore()

    def _paint_notice(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        """A notice in a line or two, or a guide step's "Step 2 of 3"."""
        painter.save()
        painter.setOpacity(self._fade_note)
        font, line_h, lines = self._notice_lines()
        x, cy = rect.left() + self._INSET, rect.top() + self.PILL[1] / 2.0
        if self._state == "answer":
            self._paint_word(painter, x, cy, lines[0] if lines else "")
        else:
            painter.setFont(font)
            painter.setPen(theme.NOTCH_TEXT)
            width = rect.width() - self._INSET - 46
            for i, line in enumerate(lines):
                painter.drawText(QRectF(x, cy - line_h / 2 + i * line_h, width, line_h),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        if not self.top:  # at the top the light is always painted
            paint_light(painter, QPointF(rect.right() - 24.0, cy), self._light_r, self._light_b, accent, 18.0)
        painter.restore()

    def _paint_sliver(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        """A side edge at rest, or speaking: a thin line of light down the sliver."""
        painter.save()
        glow = self._light_b
        line = QRectF(rect.center().x() - 1.25, rect.top() + 15, 2.5, max(0.0, rect.height() - 30))
        halo = QColor(accent)
        halo.setAlphaF(0.22 * glow)
        painter.setBrush(halo)
        painter.drawRoundedRect(line.adjusted(-2.0, -2.0, 2.0, 2.0), 3.0, 3.0)
        _hot, mid, deep = accent_shades(accent)
        fill = QLinearGradient(line.topLeft(), line.bottomLeft())
        for at, colour in ((0.0, deep), (0.5, mid), (1.0, deep)):
            colour = QColor(colour)
            colour.setAlphaF(min(1.0, 0.35 + 0.65 * glow))
            fill.setColorAt(at, colour)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(line, 1.25, 1.25)
        painter.restore()

    def _paint_voice(self, painter: QPainter, rect: QRectF, t: float, accent: QColor) -> None:
        """A side edge, listening or thinking: the light on top and a vertical
        waveform under it in the light's colours."""
        painter.save()
        self._scaled(painter, rect, self._fade_voice)
        cx = rect.center().x()
        listening = self._state == "listening"
        paint_light(painter, QPointF(cx, rect.top() + 24), self._light_r, self._light_b, accent, 20.0)

        _hot, mid, deep = accent_shades(accent)
        gap, bar_h = 5.0, 3.0
        y0 = rect.top() + 50
        centre = (self._BARS - 1) / 2
        for i in range(self._BARS):
            weight = 1 - abs(i - centre) / centre
            if listening:
                level = self._smooth_level
                width = 6 + 26 * level * (0.35 + 0.65 * weight) * (0.7 + 0.3 * math.sin(t * 9 + i * 1.3))
                color = _mix(deep, mid, weight)
            else:
                width = 4.0
                color = QColor(mid)
                color.setAlphaF(0.2 + 0.7 * (0.5 + 0.5 * math.sin(t * 5.7 - i * 0.55)))
            painter.setBrush(color)
            y = y0 + i * (bar_h + gap)
            painter.drawRoundedRect(QRectF(cx - width / 2, y, width, bar_h), 1.5, 1.5)
        painter.restore()

    def _paint_greet(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        """A side edge, when the mouse rests on it: "Clixy here." and the keys to hold."""
        painter.save()
        self._scaled(painter, rect, self._fade_greet)
        x, cy = rect.left() + self._INSET, rect.top() + 23.0
        self._paint_word(painter, x, cy, "Clixy here.")
        paint_light(painter, QPointF(rect.right() - 24.0, cy), self._light_r, self._light_b, accent, 18.0)
        end = self._paint_mini_keys(painter, x, cy + 16.0, 18.0)
        painter.setFont(theme.ui_font(9.0, QFont.Weight.Medium))
        hint = "hold to ask"
        if end + 8 + painter.fontMetrics().horizontalAdvance(hint) <= rect.right() - 14:
            painter.setPen(theme.NOTCH_MUTED)
            painter.drawText(QRectF(end + 8, cy + 16.0, rect.right() - end - 8, 18),
                             Qt.AlignmentFlag.AlignVCenter, hint)
        painter.restore()

    def _paint_mini_keys(self, painter: QPainter, x: float, y: float, cap_h: float) -> float:
        """The shortcut as small flat keycaps of frosted glass; returns where they end."""
        from widgets import _paint_windows_logo
        font = theme.ui_font(8.4, QFont.Weight.DemiBold)
        fm = QFontMetricsF(font)
        radius, pad = 5.0, 7.0
        labels = self.hotkey.split(" + ")
        white = QColor(255, 255, 255, 235)
        for i, label in enumerate(labels):
            logo = fm.capHeight() * 1.05 + 4 if label == "Win" else 0.0
            w = max(cap_h, fm.horizontalAdvance(label) + logo + 2 * pad)
            body = QRectF(x, y, w, cap_h)
            painter.setBrush(theme.NOTCH_KEY)
            painter.setPen(QPen(theme.NOTCH_KEY_EDGE, 1.0))
            painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
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
                painter.drawText(QRectF(x, y, 14, cap_h), Qt.AlignmentFlag.AlignCenter, "+")
                x += 14
        painter.setPen(Qt.PenStyle.NoPen)
        return x

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
        """The dashboard: a hello on the notch's own row (the light at its
        end), the time, big and thin, the date, and the weather."""
        import dashboard
        painter.save()
        self._scaled(painter, rect, self._fade_dash)
        snap = dashboard.snapshot()
        now = datetime.datetime.now().astimezone()
        x0, right = rect.left() + 24, rect.right() - 24

        part = "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"
        painter.setFont(theme.ui_font(10.0, QFont.Weight.DemiBold))
        painter.setPen(theme.NOTCH_TEXT)
        painter.drawText(QRectF(x0, self._row(rect) - 12, right - x0 - 30, 24), Qt.AlignmentFlag.AlignVCenter,
                         f"Good {part}" + (f", {self.name}" if self.name else ""))
        y = rect.top() + 36
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
            line = painter.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, 210)
            painter.drawText(QRectF(right - 210, y + 44, 210, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, line)
        elif not snap.city:
            painter.setFont(theme.ui_font(9))
            painter.setPen(theme.NOTCH_MUTED)
            painter.drawText(QRectF(right - 210, y + 20, 210, 36),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                             "Add your city in Settings for the weather")

        # The keys to ask.
        y += 82
        x = self._paint_mini_keys(painter, x0, y, 18.0)
        painter.setFont(theme.ui_font(9.0, QFont.Weight.Medium))
        painter.setPen(theme.NOTCH_MUTED)
        painter.drawText(QRectF(x + 8, y, 160, 18), Qt.AlignmentFlag.AlignVCenter, "hold to ask")
        painter.restore()


# --- The shape and the light -------------------------------------------------------

def tab_path(width: float, depth: float, shoulder: float | None = None,
             radius: float | None = None) -> QPainterPath:
    """A tab hanging from an edge along y = 0, ``width`` long and ``depth``
    deep: its shoulders flare into the edge in a concave curve (reaching a
    little past 0 and ``width``), its lower corners are gently rounded.
    ``shoulder`` and ``radius`` override the notch's own proportions (the
    Clixy panel, panel.py, is the same shape, bigger)."""
    if shoulder is None:
        shoulder = max(0.0, min(5.5 + 0.06 * depth, 10.0, depth * 0.45, width * 0.25))
    if radius is None:
        radius = max(0.0, min(max(8.0, 0.36 * depth), 22.0, depth - shoulder, width / 2.0))
    shoulder = max(0.0, min(shoulder, width * 0.25, depth))
    radius = max(0.0, min(radius, depth - shoulder, width / 2.0))
    k = 0.5523  # a quarter circle, as a cubic
    s, r, w, h = shoulder, radius, width, depth
    path = QPainterPath(QPointF(-s, 0.0))
    path.cubicTo(QPointF(-s + k * s, 0.0), QPointF(0.0, s - k * s), QPointF(0.0, s))
    path.lineTo(0.0, h - r)
    path.cubicTo(QPointF(0.0, h - r + k * r), QPointF(r - k * r, h), QPointF(r, h))
    path.lineTo(w - r, h)
    path.cubicTo(QPointF(w - r + k * r, h), QPointF(w, h - r + k * r), QPointF(w, h - r))
    path.lineTo(w, s)
    path.cubicTo(QPointF(w, s - k * s), QPointF(w + s - k * s, 0.0), QPointF(w + s, 0.0))
    path.closeSubpath()
    return path


def accent_shades(color: QColor) -> tuple[QColor, QColor, QColor]:
    """The light's three tones from the buddy's colour: a hot, pale centre,
    the colour itself, and a deeper rim. Ember burns pale gold, orange, red;
    Splash pale cyan, blue, deep blue."""
    h, s, l, _a = QColor(color).getHslF()
    h = max(0.0, h)
    warm = h < 0.17 or h > 0.9
    hot = QColor.fromHslF((h + (0.035 if warm else -0.02)) % 1.0, s, min(0.93, l + (1 - l) * (0.5 if warm else 0.72)))
    deep = QColor.fromHslF((h + (-0.045 if warm else 0.035)) % 1.0, min(1.0, s * 1.05), l * 0.7)
    return hot, QColor(color), deep


def paint_light(painter: QPainter, centre: QPointF, radius: float, bright: float, color: QColor,
                room: float = 1e9) -> None:
    """The light: a small core, pale at the centre, the buddy's colour, then a
    deeper rim, in a soft glow of the same colour. ``bright`` (0-1) is how
    hard it burns; ``room`` caps the glow to the space around it."""
    if bright <= 0.01 or radius <= 0.2:
        return
    hot, mid, deep = accent_shades(color)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    halo_r = min(radius * (3.0 + 1.8 * bright), room * 1.15)
    if halo_r > radius:
        halo = QRadialGradient(centre, halo_r)
        for at, colour, alpha in ((0.0, mid, 0.6), (0.3, mid, 0.3), (0.62, deep, 0.1), (1.0, deep, 0.0)):
            colour = QColor(colour)
            colour.setAlphaF(alpha * bright)
            halo.setColorAt(at, colour)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(centre, halo_r, halo_r)
    # The core: hot at the centre, the colour at its edge, which melts into the glow.
    core = QRadialGradient(centre, radius * 1.3)
    edge = QColor(mid)
    edge.setAlphaF(0.0)
    core.setColorAt(0.0, hot)
    core.setColorAt(0.38, _mix(hot, mid, 0.55))
    core.setColorAt(0.72, mid)
    core.setColorAt(1.0, edge)
    painter.setOpacity(painter.opacity() * (0.4 + 0.6 * bright))
    painter.setBrush(QBrush(core))
    painter.drawEllipse(centre, radius * 1.3, radius * 1.3)
    painter.restore()


def _speech(t: float) -> float:
    """0-1: a lively flicker, like a voice, for the light while the answer plays."""
    v = 0.5 + 0.24 * math.sin(t * 7.3) + 0.16 * math.sin(t * 12.9 + 1.3) + 0.1 * math.sin(t * 3.1 + 0.4)
    return max(0.0, min(1.0, v))


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    k = max(0.0, min(1.0, k))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * k, a.greenF() + (b.greenF() - a.greenF()) * k,
                           a.blueF() + (b.blueF() - a.blueF()) * k)


def _word_font() -> QFont:
    """The state words ("Listening", "Hey Rayan!"): Manrope SemiBold."""
    return theme.ui_font(11.0, QFont.Weight.DemiBold)


def _note_font() -> QFont:
    return theme.ui_font(9.6, QFont.Weight.Medium)


def _wrap(text: str, metrics: QFontMetricsF, width: float, max_lines: int) -> list[str]:
    """``text`` wrapped to ``width``, at most ``max_lines``; the last one elided."""
    lines: list[str] = []
    line = ""
    words = text.split()
    for i, word in enumerate(words):
        trial = f"{line} {word}" if line else word
        if line and metrics.horizontalAdvance(trial) > width:
            lines.append(line)
            line = word
            if len(lines) == max_lines - 1:
                line = " ".join(words[i:])
                break
        else:
            line = trial
    if line:
        lines.append(metrics.elidedText(line, Qt.TextElideMode.ElideRight, width))
    return lines


# --- Helpers ------------------------------------------------------------------------

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


def _left_down() -> bool:
    """Is the left mouse button down right now (Windows; False elsewhere)?"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False


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


def model_label(model_id: str) -> str:
    """'anthropic/claude-haiku-5-5' -> 'Haiku 5.5'; other ids lightly tidied."""
    name = model_id.split("/", 1)[-1]
    if name.startswith("claude-"):
        parts = name.removeprefix("claude-").split("-")
        if len(parts) >= 2 and all(p.isdigit() for p in parts[1:]):
            return f"{parts[0].title()} {'.'.join(parts[1:])}"
    return name.split(":")[0]
