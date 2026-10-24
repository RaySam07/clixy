"""Clicky Windows transparent click-through pointer overlay.

Per-monitor `OverlayWindow(QWidget)` overlays routed by `OverlayController`.
Each overlay covers exactly one physical monitor in DIP (logical) coords.
The pixel avatar (theme.py) is drawn via `QPainter.paintEvent` and flown
by a `QVariantAnimation`; its tip is the pointer. While listening the screen
edges glow in the avatar's theme colors, and the side island (island.py)
shows the words. Click-through is enforced by Win32 extended window styles
applied via ctypes AFTER `QWidget.show()`.

Overlays are per-monitor rather than one window spanning the virtual
desktop because of Qt 6's "islands of screens" behavior on mixed-DPI
Windows 11: each screen gets its own logical coordinate space.

Responsibility boundary:
- THIS MODULE lives in Space A (physical pixels from capture.py) and
  Space B (Qt logical/DIP pixels). It owns the math that maps A -> B
  per-screen via devicePixelRatio().
- capture.py owns Space A -> Space C (Claude declared resolution).
- app.py owns threading and calls OverlayController methods from the
  main Qt thread only (PyQt6 is not thread-safe).

The `__main__` block (manual click-through verification) stays last so
`python -m overlay` works.
"""
from __future__ import annotations

import ctypes
import math
import time
from itertools import cycle

from enum import Enum, auto

from PyQt6.QtCore import (
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QScreen
from PyQt6.QtWidgets import QWidget

import theme


class _OverlayState(Enum):
    IDLE = auto()
    POINTING = auto()
    HIDDEN = auto()


# --- Quadratic bezier flight arc math ----------------------------------------
#
# Ports farzaa/clicky OverlayWindow.swift:491-568 with ONE
# deliberate deviation: no tangent rotation. Our cursor is a tip-anchored
# polygon whose tip IS the pointer, so we keep it pointing at the target
# throughout flight instead of rotating along the tangent like Clicky's
# isosceles triangle.

def _bezier_position(
    t: float,
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> tuple[float, float]:
    """Quadratic Bezier: B(t) = (1-t)²·P0 + 2(1-t)t·P1 + t²·P2."""
    one_minus = 1.0 - t
    x = one_minus * one_minus * p0[0] + 2.0 * one_minus * t * p1[0] + t * t * p2[0]
    y = one_minus * one_minus * p0[1] + 2.0 * one_minus * t * p1[1] + t * t * p2[1]
    return (x, y)


def _smoothstep(t: float) -> float:
    """Hermite smoothstep: 3t² - 2t³. Eases in and out for natural motion."""
    return t * t * (3.0 - 2.0 * t)


def _flight_duration_s(distance_px: float) -> float:
    """Distance-scaled flight duration, clamped to [0.6s, 1.4s].
    Ports OverlayWindow.swift:509."""
    return max(0.6, min(distance_px / 800.0, 1.4))


def _scale_pulse(linear_t: float) -> float:
    """Sine scale pulse: 1.0 at endpoints → 1.3 at midpoint. Not eased:
    runs on LINEAR progress (not smoothstep'd) so the peak lands dead-center.
    Ports OverlayWindow.swift:567."""
    return 1.0 + math.sin(linear_t * math.pi) * 0.3


# --- Level bars (LISTENING state visual) -------------------------------------
#
# Ports farzaa/clicky OverlayWindow.swift:705-743 verbatim.
# While PTT is held, the side island shows these 5 bars. Bar heights are
# driven by mic RMS (from stt.py) × a profile curve + an independent sine
# idle-pulse so bars are never fully flat.

_WAVEFORM_BAR_COUNT = 5
_WAVEFORM_BAR_PROFILE: tuple[float, ...] = (0.4, 0.7, 1.0, 0.7, 0.4)
"""Per-bar amplitude multiplier. Center bar (idx 2) scales by 1.0 = taller.
Edges (idx 0, 4) scale by 0.4 = shorter. Matches Clicky's visual rhythm."""
_WAVEFORM_BASE_HEIGHT = 3.0  # minimum px, so bars are never fully flat
_WAVEFORM_MAX_REACTIVE = 10.0  # max extra px from audio-driven component
_WAVEFORM_IDLE_PULSE_AMP = 1.5  # max extra px from independent sine pulse


def _waveform_bar_height(bar_index: int, audio_level: float, phase_seconds: float) -> float:
    """Compute a single bar's height in px.

    Formula (port of OverlayWindow.swift:728-740):
        normalized = max(audio_level - 0.008, 0)
        eased = (min(normalized * 2.85, 1))^0.76
        reactive = eased * 10 * profile[bar_index]
        idle_pulse = (sin(phase * 3.6 + bar_index * 0.35) + 1) / 2 * 1.5
        height = 3 + reactive + idle_pulse

    - The 0.008 dead zone prevents flickering on near-silent chunks.
    - The 2.85× boost + 0.76 power curve make quiet speech visually punchy
      without saturating on loud speech.
    - The per-bar phase offset (0.35 rad) gives a subtle wave pattern even
      at silence.

    Args:
        bar_index: 0..4. Must be within _WAVEFORM_BAR_PROFILE bounds.
        audio_level: RMS-derived level in [0, 1] (from stt.py's on_audio_level).
        phase_seconds: elapsed seconds (drives the idle pulse).

    Returns:
        Bar height in pixels, in range ~[3, 14.5].
    """
    normalized_level = max(audio_level - 0.008, 0.0)
    eased = pow(min(normalized_level * 2.85, 1.0), 0.76)
    reactive = eased * _WAVEFORM_MAX_REACTIVE * _WAVEFORM_BAR_PROFILE[bar_index]
    animation_phase = phase_seconds * 3.6 + bar_index * 0.35
    idle_pulse = (math.sin(animation_phase) + 1.0) / 2.0 * _WAVEFORM_IDLE_PULSE_AMP
    return _WAVEFORM_BASE_HEIGHT + reactive + idle_pulse

_CURSOR_FOLLOW_LERP = 0.15
"""Spring interpolation factor for cursor following. Each frame, cursor moves
15% of the remaining distance toward the target. Lower = smoother/laggier.
0.15 gives a natural 'following you' feel, like a puppy trotting after you.
"""


# --- Win32 constants ---------------------------------------------------------

_GWL_EXSTYLE = -20
"""SetWindowLongW index for the extended window style field."""

_WS_EX_LAYERED = 0x00080000
"""Required for WS_EX_TRANSPARENT to function on top-level windows."""
_WS_EX_TRANSPARENT = 0x00000020
"""The actual click-through flag (only works on layered windows)."""
_WS_EX_TOPMOST = 0x00000008
"""Always-on-top. Redundant with Qt.WindowStaysOnTopHint but harmless."""
_WS_EX_NOACTIVATE = 0x08000000
"""Prevents focus theft when the overlay receives any event."""
_WS_EX_TOOLWINDOW = 0x00000080
"""Hides the window from the taskbar and Alt-Tab list."""

_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
"""Forces WM_NCCALCSIZE so style changes take effect immediately."""
_HWND_TOPMOST = -1

_CLICKTHROUGH_FLAGS = (
    _WS_EX_LAYERED
    | _WS_EX_TRANSPARENT
    | _WS_EX_TOPMOST
    | _WS_EX_NOACTIVATE
    | _WS_EX_TOOLWINDOW
)
"""OR of all ex-styles to apply to overlay windows after show().

Bit pattern should be 0x080800A8. The test in test_overlay.py guards
against silent drift in the individual constants.
"""


# --- Win32 click-through helper ----------------------------------------------

def apply_clickthrough_styles(hwnd: int) -> None:
    """Apply Win32 extended window styles for click-through + no-taskbar
    + no-focus-theft on an existing top-level window.

    MUST be called AFTER QWidget.show() so the HWND exists. Reads the
    current GWL_EXSTYLE via GetWindowLongW, ORs in _CLICKTHROUGH_FLAGS
    (NEVER overwrites -- that would wipe Qt's own flags), then calls
    SetWindowLongW and forces the style change to take effect via
    SetWindowPos with SWP_FRAMECHANGED.

    This is the core of the click-through mechanism on Windows 11.
    Without SWP_FRAMECHANGED the new styles don't take effect until the
    window is resized or moved.

    Raises:
        RuntimeError: if SetWindowLongW returns 0, indicating the Win32
            call failed. Error details from ctypes.WinError() are included.
            This catches silent click-through breakage that would otherwise
            leave the user with no diagnostic signal.

    Args:
        hwnd: native window handle from int(QWidget.winId()).
    """
    user32 = ctypes.windll.user32
    current = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    new_style = current | _CLICKTHROUGH_FLAGS
    result = user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new_style)
    # SetWindowLongW returns the previous value on success, 0 on failure.
    # Previous value could legitimately be 0 if no ex-styles were set yet,
    # so a 0 return only counts as failure when current != 0. In practice
    # current != 0 (Qt sets some ex-styles) so a 0 return is always a failure.
    if result == 0 and current != 0:
        raise RuntimeError(
            f"SetWindowLongW failed for HWND {hwnd}: {ctypes.WinError()}"
        )
    user32.SetWindowPos(
        hwnd,
        _HWND_TOPMOST,
        0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    )


# --- Pure coordinate math ----------------------------------------------------

def screen_for_monitor(monitor: dict, screens: list[QScreen]) -> QScreen:
    """Find the QScreen whose physical geometry matches a capture.py monitor dict.

    capture.py produces CaptureResult.monitor = {"left": phys_x, "top": phys_y,
    "width": phys_w, "height": phys_h} where all fields are in virtual-desktop
    physical pixel coordinates (from mss). Qt 6 keeps each QScreen.geometry()
    top-left at the monitor's physical position and divides only the size by
    that screen's devicePixelRatio (QScreenPrivate::updateGeometry), so the
    origin compares as-is and the size is scaled back up. The size allows one
    logical pixel of rounding at fractional scales like 125%.

    Args:
        monitor: mss-style dict with 'left', 'top', 'width', 'height' keys
            (all values in physical pixels).
        screens: list of QScreen-compatible objects, each with geometry() ->
            QRect-like (physical top-left, DIP size) and devicePixelRatio() ->
            float. In tests these are _MockScreen duck-types, not real QScreens.

    Returns:
        The QScreen whose physical bounds match the monitor dict. Falls back
        to screens[0] (primary) if no match is found -- this can happen if
        mss state is stale or the monitor config changed mid-session.
    """
    target_w = monitor["width"]
    target_h = monitor["height"]
    target_left = monitor["left"]
    target_top = monitor["top"]
    for screen in screens:
        ratio = screen.devicePixelRatio()
        geom = screen.geometry()
        if (geom.left() == target_left
                and geom.top() == target_top
                and abs(geom.width() * ratio - target_w) <= ratio
                and abs(geom.height() * ratio - target_h) <= ratio):
            return screen
    return screens[0]


def physical_to_local_logical(
    physical_x: int,
    physical_y: int,
    screen: QScreen,
) -> tuple[int, int]:
    """Map a physical-pixel point (Space A) to within-screen logical DIP
    coords (Space B) inside the target QScreen's local coordinate system.

    Returns (local_x, local_y) where (0, 0) is the screen's top-left in
    the overlay widget's coordinate system. The per-monitor architecture
    means we never need global virtual-desktop coordinates -- each overlay
    lives in its own screen's local space.

    Critical: uses the PER-SCREEN devicePixelRatio(). Do NOT cache a
    global ratio. Mixed-DPI setups (e.g., laptop at 200% + external
    monitor at 100%) have different ratios per screen, and using the
    wrong one would land the pointer in the wrong place on one of them.

    Args:
        physical_x: virtual-desktop physical pixel x (from capture.py).
        physical_y: virtual-desktop physical pixel y.
        screen: QScreen-compatible object with geometry() returning a
            QRect-like (physical top-left, DIP size) and devicePixelRatio()
            returning a float.

    Returns:
        (local_log_x, local_log_y) integer tuple in the screen's local
        logical coordinate space, ready to pass to QWidget.move or the
        pointer animation target.
    """
    ratio = screen.devicePixelRatio()
    geom = screen.geometry()
    # Qt keeps the screen's top-left at its physical position; only sizes scale.
    local_phys_x = physical_x - geom.left()
    local_phys_y = physical_y - geom.top()
    local_log_x = int(local_phys_x / ratio)
    local_log_y = int(local_phys_y / ratio)
    return local_log_x, local_log_y


def annotations_to_local(annotations: list, screen) -> list:
    """Map teaching-annotation coords from PHYSICAL virtual-desktop pixels
    (Space A) to a screen's LOCAL-logical DIP coords (Space B).

    Points (centers, endpoints) go through physical_to_local_logical (which
    subtracts the screen origin + divides by the per-screen devicePixelRatio).
    Lengths (radius, width) are NOT positions: they only divide by the ratio,
    no origin subtraction. Returns NEW annotation objects (inputs untouched).

    Pure function so the coordinate math is unit-testable without a
    QApplication. The per-screen ratio is never cached globally: mixed-DPI
    setups have a different ratio per screen.
    """
    from annotations import Angle, Arrow, Box, Circle, Dot, Highlight, Label, Line, Ring, Underline

    ratio = screen.devicePixelRatio() or 1.0
    out: list = []
    for a in annotations:
        if isinstance(a, Circle):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Circle(lx, ly, int(a.r / ratio), a.label))
        elif isinstance(a, Arrow):
            x1, y1 = physical_to_local_logical(a.x1, a.y1, screen)
            x2, y2 = physical_to_local_logical(a.x2, a.y2, screen)
            out.append(Arrow(x1, y1, x2, y2))
        elif isinstance(a, Underline):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Underline(lx, ly, int(a.w / ratio), a.label))
        elif isinstance(a, Label):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Label(lx, ly, a.text))
        elif isinstance(a, (Box, Highlight)):
            x1, y1 = physical_to_local_logical(a.x1, a.y1, screen)
            x2, y2 = physical_to_local_logical(a.x2, a.y2, screen)
            out.append((Box if isinstance(a, Box) else Highlight)(x1, y1, x2, y2, a.label))
        elif isinstance(a, Ring):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Ring(lx, ly, int(a.r / ratio), a.label))
        elif isinstance(a, Line):
            x1, y1 = physical_to_local_logical(a.x1, a.y1, screen)
            x2, y2 = physical_to_local_logical(a.x2, a.y2, screen)
            out.append(Line(x1, y1, x2, y2, a.label))
        elif isinstance(a, Angle):
            vx, vy = physical_to_local_logical(a.vx, a.vy, screen)
            ax, ay = physical_to_local_logical(a.ax, a.ay, screen)
            bx, by = physical_to_local_logical(a.bx, a.by, screen)
            out.append(Angle(vx, vy, ax, ay, bx, by, a.label))
        elif isinstance(a, Dot):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Dot(lx, ly, a.label))
    return out


# --- Overlay window ----------------------------------------------------------

class OverlayWindow(QWidget):
    """One transparent click-through overlay for a single QScreen.

    Responsibilities:
    - Cover exactly one physical monitor with a frameless transparent window
    - Paint the pixel avatar (theme.paint_avatar), its landing burst, the
      teaching annotations and the screen-edge glow via QPainter
    - Fly the avatar along a Bezier arc with a QVariantAnimation
    - Apply Win32 click-through ex-styles via ctypes after show()

    Each OverlayWindow operates entirely in its own screen's local DIP
    coordinate space. No global virtual-desktop coordinates are ever used
    here.

    Thread safety: PyQt6 is NOT thread-safe. All methods must be called
    from the main Qt thread only. app.py enforces this via pyqtSignal
    cross-thread communication.
    """

    _AVATAR_BOX = 60
    """Half-size of the square around the tip that holds the avatar, its glow
    and its landing burst; the only area repainted while it sits still."""

    _BURST_S = 0.7
    _LIFE_INTERVAL_MS = 33

    def __init__(self, screen: QScreen) -> None:
        """Construct the overlay window for a given QScreen.

        Args:
            screen: QScreen for this overlay to cover. Production uses real
                QScreens from QGuiApplication.screens(); tests never call
                this constructor directly (they use _MockOverlayWindow via
                OverlayController dependency injection).
        """
        super().__init__()

        # Qt window flags: frameless, always-on-top, Tool (no taskbar entry)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        # Attribute-based transparency -- NOT stylesheet. Stylesheet
        # transparency is the #1 flicker source on Win 11 per forum.qt.io.
        # Also: do NOT setWindowOpacity(<1.0), that forces Qt's own layered
        # path and overrides the Win32 ex-styles we apply later.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Cover this screen exactly. QScreen.geometry() returns DIP coords,
        # which is what setGeometry expects -- no conversion needed.
        self.setGeometry(screen.geometry())
        self.screen_name = screen.name()  # used by OverlayController._overlay_for_screen

        # Pointer state
        self._pointer_pos = QPoint(0, 0)
        self._pointer_visible = False

        # LISTENING / THINKING flags. They change the avatar's face and light
        # the screen-edge glow; the words and level bars live in the side island.
        self._waveform_visible = False
        self._spinner_visible = False
        self._audio_level = 0.0
        self._glow_level = 0.0
        self._glow_painted = False

        # Bezier flight animation: Farza's quadratic-bezier-arc + smoothstep +
        # scale-pulse. Port of OverlayWindow.swift:491-568 with no tangent
        # rotation: our avatar is tip-anchored, so the tip stays on target
        # through flight.
        self._flight_anim = QVariantAnimation(self)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._flight_anim.valueChanged.connect(self._on_flight_value)
        self._flight_p0: tuple[float, float] = (0.0, 0.0)
        self._flight_p1: tuple[float, float] = (0.0, 0.0)
        self._flight_p2: tuple[float, float] = (0.0, 0.0)
        self._flight_scale: float = 1.0

        # Set by OverlayController.point_at so only a flight TO a target
        # celebrates; the flight back to the mouse lands quietly.
        self._celebrate_next_landing = False
        self._landed_at: float | None = None
        # Name of what the avatar points at, shown as a butter tag beside it
        # while it dwells there. Set by the controller, cleared on fly-back.
        self.point_label: str = ""
        # Screen-edge glow can be turned off in Settings.
        self.glow_enabled = True
        self._life_t0 = time.monotonic()

        # Teaching marks (drawing.py) in this window's LOCAL-logical coords:
        # the controller maps physical->local first, like point_at. While a
        # tour runs the buddy flies along them and they draw behind its tip.
        # They fade when the answer is over, one by one as you hover what they
        # mark, after a minute at most, and vanish on the next question.
        self._marks: list = []
        self._tour = None
        self._tour_t0 = 0.0
        self._tour_end = 0.0
        self._tour_done_cb = None
        self._marks_fade_at: float | None = None
        self._fade_after_tour = False
        self._marks_rect = QRect()
        self._annotation_clear_timer = QTimer(self)
        self._annotation_clear_timer.setSingleShot(True)
        self._annotation_clear_timer.timeout.connect(self.fade_annotations)

        # Blinking, flicker and the glow need repaints even while nothing
        # moves. Only the avatar's square and the glowing edge strips repaint.
        self._life_timer = QTimer(self)
        self._life_timer.timeout.connect(self._on_life_tick)
        self._life_timer.start(self._LIFE_INTERVAL_MS)

    def set_annotations(self, annotations: list) -> None:
        """Show these shapes (LOCAL-logical coords) as marks, drawn by the
        buddy. An empty list clears them."""
        self.start_drawing(annotations)

    def start_drawing(self, annotations: list, on_done=None, *, avoid=None, busy=None) -> None:
        """The buddy flies to each shape and draws it with its tip, then
        rests beside the last one and calls ``on_done``. Labels keep out of
        ``avoid`` (local rects) and off busy parts of the screen (``busy``,
        a drawing.BusyMap)."""
        import drawing

        self._flight_anim.stop()
        self._drop_marks()
        marks = drawing.build_marks(annotations, QRectF(self.rect()), avoid=avoid, busy=busy)
        if not marks:
            if on_done is not None:
                QTimer.singleShot(0, on_done)
            return
        start = QPointF(self._pointer_pos) if self._pointer_visible else marks[0].points[0]
        self._marks = marks
        self._tour = drawing.plan_tour(marks, start)
        self._tour_t0 = time.monotonic()
        self._tour_end = self._tour_t0 + self._tour.total
        self._tour_done_cb = on_done
        rect = QRectF()
        for mark in marks:
            rect = rect.united(mark.bounds())
        self._marks_rect = rect.toAlignedRect()
        self._pointer_visible = True
        self.point_label = ""
        self._annotation_clear_timer.start(60_000)
        self._speed_up()

    def fade_annotations(self) -> None:
        """Fade the marks out: the answer is over. Waits for the drawing."""
        if not self._marks or self._marks_fade_at is not None:
            return
        if self._tour is not None:
            self._fade_after_tour = True
            return
        self._marks_fade_at = time.monotonic()
        self._speed_up()

    def clear_annotations(self) -> None:
        """Remove the marks at once (a new question is coming)."""
        if self._marks:
            self._drop_marks()

    def _drop_marks(self) -> None:
        if not self._marks_rect.isNull():
            self.update(self._marks_rect)
        self._marks = []
        self._tour = None
        self._tour_done_cb = None
        self._marks_fade_at = None
        self._fade_after_tour = False
        self._marks_rect = QRect()
        self._annotation_clear_timer.stop()

    def _speed_up(self) -> None:
        if self._life_timer.interval() != 16:
            self._life_timer.setInterval(16)

    def _step_marks(self, now: float) -> bool:
        """Move the buddy along its drawing tour, notice hovers, run fades.
        Returns True while anything is still moving."""
        import drawing

        if not self._marks:
            return False
        moving = False
        if self._tour is not None:
            t = now - self._tour_t0
            pos, scale, _mark = self._tour.pen(t)
            old = self.avatar_rect()
            self._pointer_pos = QPoint(round(pos.x()), round(pos.y()))
            self._flight_scale = scale
            self.update(old.united(self.avatar_rect()))
            if t >= self._tour.total:
                self._finish_tour(now)
            moving = True
        elif self._marks_fade_at is None:
            self._check_hover(now)
        if now - self._tour_end < 0.6:
            moving = True  # labels and step numbers still popping in
        if any(m.acked_at is not None and now - m.acked_at < drawing.ACK_S for m in self._marks):
            moving = True
        if self._marks_fade_at is not None:
            if now - self._marks_fade_at >= _MARKS_FADE_S:
                self._drop_marks()
                return False
            moving = True
        found = [m for m in self._marks if _hoverable(m)]
        if found and all(m.acked_at is not None and now - m.acked_at >= drawing.ACK_S for m in found) \
                and len(found) == len(self._marks):
            self._drop_marks()  # every mark found: nothing left to show
            return False
        if moving:
            self.update(self._marks_rect)
        return moving

    def _finish_tour(self, now: float) -> None:
        self._tour = None
        self._flight_scale = 1.0
        self._landed_at = now  # squash and burst: done!
        callback, self._tour_done_cb = self._tour_done_cb, None
        if self._fade_after_tour:
            self._fade_after_tour = False
            QTimer.singleShot(2500, self.fade_annotations)
        if callback is not None:
            callback()

    def _check_hover(self, now: float) -> None:
        """Your mouse on a marked thing means you found it: that mark (and
        any arrow pointing at it) says "got it" and fades. A mark only
        reacts once the mouse has been outside it, so one drawn under the
        mouse doesn't vanish at once."""
        local = QPointF(self.mapFromGlobal(QCursor.pos()))
        for mark in self._marks:
            if not _hoverable(mark) or mark.acked_at is not None:
                continue
            inside = mark.target.adjusted(-6, -6, 6, 6).contains(local)
            if not inside:
                mark.armed = True
            elif mark.armed:
                mark.acked_at = now
                near = mark.target.adjusted(-36, -36, 36, 36)
                for other in self._marks:
                    if other.kind == "arrow" and other.acked_at is None and near.contains(other.head[1]):
                        other.acked_at = now

    @property
    def drawing_labels(self) -> set[str]:
        return {m.label.strip().lower() for m in self._marks if m.label}

    # --- Life: mood, glow, partial repaints ------------------------------------

    def _mood(self) -> str:
        """Which face the avatar makes, from what the overlay is showing."""
        if self._flight_anim.state() == QVariantAnimation.State.Running or self._burst_progress() is not None \
                or self._tour is not None:
            return "happy"
        if self._waveform_visible:
            return "listening"
        if self._spinner_visible:
            return "thinking"
        return "idle"

    def _burst_progress(self) -> float | None:
        if self._landed_at is None:
            return None
        progress = (time.monotonic() - self._landed_at) / self._BURST_S
        return progress if progress < 1.0 else None

    def _glow_target(self) -> float:
        if not self.glow_enabled:
            return 0.0
        if self._waveform_visible:
            return 1.0
        if self._spinner_visible:
            return 0.55
        return 0.0

    def avatar_rect(self, pos: QPoint | None = None) -> QRect:
        pos = self._pointer_pos if pos is None else pos
        box = self._AVATAR_BOX
        return QRect(pos.x() - box, pos.y() - box, 2 * box + 20, 2 * box + 20)

    def _edge_rects(self) -> list[QRect]:
        w, h, reach = self.width(), self.height(), theme.GLOW_REACH
        return [QRect(0, 0, w, reach), QRect(0, h - reach, w, reach),
                QRect(0, 0, reach, h), QRect(w - reach, 0, reach, h)]

    def _on_life_tick(self) -> None:
        if not self.isVisible():
            return
        self._glow_level += (self._glow_target() - self._glow_level) * 0.12
        if self._glow_level < 0.01:
            self._glow_level = 0.0
        drawing_busy = self._step_marks(time.monotonic())
        # 60 fps while the glow flows or the buddy draws, 30 fps for blinking
        # and flicker alone.
        busy = drawing_busy or self._glow_level > 0.0 or self._glow_target() > 0.0
        interval = 16 if busy else self._LIFE_INTERVAL_MS
        if self._life_timer.interval() != interval:
            self._life_timer.setInterval(interval)
        if self._glow_level > 0.0 or self._glow_painted:
            for rect in self._edge_rects():
                self.update(rect)
        if self._pointer_visible:
            self.update(self.avatar_rect())

    def paintEvent(self, _event) -> None:
        """Draw the edge glow, the annotations, then the avatar.

        The avatar's tip is anchored at pointer_pos so point_at(x,y) puts it
        exactly on the target UI element. During FLYING, self._flight_scale
        rises to 1.3 at mid-flight and back to 1.0 on landing; the avatar
        grows around its tip so the tip keeps tracking the Bezier curve.
        """
        painter = QPainter(self)
        now = time.monotonic()
        t = now - self._life_t0

        self._glow_painted = self._glow_level > 0.0
        if self._glow_painted:
            theme.paint_edge_glow(
                painter, QRectF(self.rect()), t=t, level=self._glow_level,
                audio=self._audio_level if self._waveform_visible else 0.0,
            )

        # The marks go under the buddy, so it draws on top of its own ink.
        if self._marks:
            import drawing
            fade = 1.0
            if self._marks_fade_at is not None:
                fade = max(0.0, 1.0 - (now - self._marks_fade_at) / _MARKS_FADE_S)
            drawing.paint_marks(painter, self._marks, now - self._tour_t0, now=now, fade=fade)

        if not self._pointer_visible:
            return
        px, py = float(self._pointer_pos.x()), float(self._pointer_pos.y())
        squash = 0.0 if self._landed_at is None else theme.landing_squash(now - self._landed_at)
        theme.paint_avatar(
            painter, px, py, t=t, mood=self._mood(), scale=self._flight_scale,
            device_pixel_ratio=self.devicePixelRatioF(), squash=squash,
        )
        burst = self._burst_progress()
        if burst is not None:
            theme.paint_burst(painter, px, py, burst)
        # The tag names what the buddy points at, unless a drawing already
        # labels it with the same name.
        if self.point_label and self._landed_at is not None \
                and self.point_label.strip().lower() not in self.drawing_labels \
                and self._flight_anim.state() != QVariantAnimation.State.Running:
            self._paint_point_tag(painter, px, py, now - self._landed_at)

    def _paint_point_tag(self, painter: QPainter, px: float, py: float, since: float) -> None:
        """Butter tag with the name of what the avatar points at, popping in
        just after it lands. The side island shows the same name as a chip."""
        grow = min(1.0, max(0.0, (since - 0.12) / 0.3))
        if grow <= 0.0:
            return
        pop = 1.0 + 0.12 * math.sin(grow * math.pi)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(grow)
        font = theme.ui_font(9.5, QFont.Weight.DemiBold)
        painter.setFont(font)
        w = painter.fontMetrics().horizontalAdvance(self.point_label) + 20
        h = painter.fontMetrics().height() + 8
        x, y = px + 30, py + 22
        if x + w > self.width() - 8:  # flip to the left near the screen edge
            x = px - w - 10
        painter.translate(x, y + h / 2)
        painter.scale(pop, pop)
        painter.translate(-x, -(y + h / 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawRoundedRect(QRectF(x, y + 3, w, h), h / 2, h / 2)
        painter.setBrush(theme.BUTTER)
        painter.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        painter.setPen(theme.INK)
        painter.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, self.point_label)
        painter.restore()

    def animate_pointer_to(self, local_logical_x: int, local_logical_y: int) -> None:
        """Fly the pointer along a quadratic Bezier arc to (x, y).

        Ports farzaa/clicky OverlayWindow.swift:491-568 verbatim with ONE
        deliberate deviation: no tangent rotation. Our avatar is tip-anchored:
        the tip IS the pointer, so it keeps pointing at the target through
        flight instead of rotating along the tangent.

        Curve: P0=current pointer, P1=midpoint lifted up by min(dist*0.2, 80px),
        P2=target. Duration = clamp(distance/800 s, 0.6s, 1.4s). Smoothstep
        eases progress before bezier interpolation. Scale pulse 1.0→1.3→1.0
        applied on LINEAR progress (not eased) so the peak lands mid-arc.

        Args:
            local_logical_x: within-screen logical DIP x (from physical_to_local_logical)
            local_logical_y: within-screen logical DIP y
        """
        start_x = float(self._pointer_pos.x())
        start_y = float(self._pointer_pos.y())
        end_x = float(local_logical_x)
        end_y = float(local_logical_y)
        dx, dy = end_x - start_x, end_y - start_y
        distance = math.hypot(dx, dy)

        mid_x = (start_x + end_x) / 2.0
        mid_y = (start_y + end_y) / 2.0
        arc_height = min(distance * 0.2, 80.0)

        self._flight_p0 = (start_x, start_y)
        self._flight_p1 = (mid_x, mid_y - arc_height)
        self._flight_p2 = (end_x, end_y)

        duration_ms = int(_flight_duration_s(distance) * 1000.0)

        self._flight_anim.stop()
        self._flight_anim.setDuration(duration_ms)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._pointer_visible = True
        self._flight_anim.start()

    def _on_flight_value(self, linear_t) -> None:
        """QVariantAnimation.valueChanged callback: interpolate bezier + pulse.

        linear_t is Qt's raw interpolated value 0.0..1.0. We apply smoothstep
        BEFORE the bezier sample (eased position) but use LINEAR t for the
        scale pulse (peak lands at true midpoint, not eased midpoint).
        """
        t = float(linear_t)
        eased_t = _smoothstep(t)
        old_rect = self.avatar_rect()
        x, y = _bezier_position(eased_t, self._flight_p0, self._flight_p1, self._flight_p2)
        self._pointer_pos = QPoint(int(x), int(y))
        self._flight_scale = _scale_pulse(t)
        # On completion, snap to P2 and reset scale (defensive: Qt sometimes
        # emits valueChanged(1.0) slightly early and we want exact landing).
        if t >= 0.9999:
            self._pointer_pos = QPoint(int(self._flight_p2[0]), int(self._flight_p2[1]))
            self._flight_scale = 1.0
            if self._celebrate_next_landing:
                self._celebrate_next_landing = False
                self._landed_at = time.monotonic()
        self.update(old_rect.united(self.avatar_rect()))

    def apply_win32_clickthrough(self) -> None:
        """Apply Win32 ex-styles for click-through. MUST be called after show()."""
        hwnd = int(self.winId())
        apply_clickthrough_styles(hwnd)

    # --- LISTENING / THINKING state ------------------------------------------
    #
    # The avatar stays visible in every state and changes its face; the edge
    # glow fades in while listening (swelling with the voice) and dims while
    # thinking. The island beside the screen shows the words.

    def show_waveform(self) -> None:
        """Enter LISTENING state."""
        self._waveform_visible = True
        self._spinner_visible = False

    def hide_waveform(self) -> None:
        """Exit LISTENING state; the caller moves on to THINKING or IDLE."""
        self._waveform_visible = False
        self._audio_level = 0.0

    def show_spinner(self) -> None:
        """Enter THINKING state."""
        self._spinner_visible = True

    def hide_spinner(self) -> None:
        """Exit THINKING state (the answer is arriving, or the turn ended)."""
        self._spinner_visible = False

    def set_audio_level(self, level: float) -> None:
        """Live mic level in [0, 1]; swells the glow while listening."""
        self._audio_level = max(0.0, min(float(level), 1.0))


_MARKS_FADE_S = 0.45
"""Seconds the marks take to fade out once the answer is over."""


def _hoverable(mark) -> bool:
    """Marks that fade when you move onto what they mark. Not written
    labels, and not geometry: you study a figure, you don't click it."""
    return mark.kind != "label" and not mark.exact


# --- Thinking phase ------------------------------------------------------------

_SPINNER_PERIOD_S = 0.8
"""Seconds per thinking-dots cycle. Matches Clicky's spinner rotation."""


def _spinner_angle_deg(elapsed_s: float) -> float:
    """Cycle phase in degrees, wrapping at the full period (0.8s).

    Returns angle in [0, 360). Pure function (no Qt), easy to unit test.
    Drives the island's three bobbing dots.
    """
    return (elapsed_s / _SPINNER_PERIOD_S * 360.0) % 360.0


# --- Controller --------------------------------------------------------------

class OverlayController:
    """Manages one OverlayWindow per physical monitor + cursor following.

    State machine:
    - IDLE: 16ms timer polls QCursor.pos(), cursor follows mouse with offset
    - POINTING: timer stopped, animation drives cursor to Claude's target,
      3s dwell, then fly back to mouse, resume IDLE
    - HIDDEN: timer stopped, overlays hidden for screen capture

    Always-visible mode only: the cursor is visible from launch.

    The controller also owns the side island (island.py) and keeps it in
    step: listening, thinking, hidden for screen captures.

    Dependency injection: overlay_factory, screens, and cursor_pos_fn are
    injectable so tests can substitute mocks without real QWidgets. With a
    mock factory there is no island.
    """

    _FOLLOW_OFFSET_X = 35
    _FOLLOW_OFFSET_Y = 25
    _DWELL_MS = 3000
    _MARKS_LINGER_MS = 5000
    _FIGURE_LINGER_MS = 20000
    _FOLLOW_INTERVAL_MS = 16

    def __init__(
        self,
        overlay_factory=None,
        screens: list[QScreen] | None = None,
        cursor_pos_fn=None,
    ) -> None:
        self.island = None
        self._pending_label = ""
        glow_enabled = True
        if overlay_factory is None:
            overlay_factory = OverlayWindow
            from island import SideIsland
            self.island = SideIsland()
            try:
                from config import EDGE_GLOW
                glow_enabled = EDGE_GLOW
            except Exception:
                pass
        if screens is None:
            screens = QGuiApplication.screens()
        self._cursor_pos_fn = cursor_pos_fn or QCursor.pos

        self.overlays: list[OverlayWindow] = []
        for qscreen in screens:
            overlay = overlay_factory(qscreen)
            overlay.show()
            overlay.apply_win32_clickthrough()
            overlay.glow_enabled = glow_enabled
            self.overlays.append(overlay)

        self._state = _OverlayState.IDLE
        self._pointing_overlay: OverlayWindow | None = None

        self._follow_timer = QTimer()
        self._follow_timer.setInterval(self._FOLLOW_INTERVAL_MS)
        self._follow_timer.timeout.connect(self._on_follow_tick)
        self._follow_timer.start()
        # After pointing or drawing the buddy waits, then flies back to the
        # mouse. A timer, not singleShot, so a new question can cancel it.
        self._dwell_timer = QTimer()
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._fly_back)
        # The marks fade a few seconds after the answer is spoken.
        self._marks_fade_timer = QTimer()
        self._marks_fade_timer.setSingleShot(True)
        self._marks_fade_timer.timeout.connect(self._fade_marks)

    def _on_follow_tick(self) -> None:
        """Poll cursor position and lerp the Clicky cursor toward it.

        Instead of snapping directly to the mouse position (which looks like
        teleporting), each frame moves 15% of the remaining distance. This
        creates a smooth 'following you' feel: the cursor lazily
        drifts toward your mouse like a puppy trotting after you.
        """
        if self._state != _OverlayState.IDLE:
            return
        global_pos = self._cursor_pos_fn()
        screen = QGuiApplication.screenAt(global_pos)
        if screen is None:
            return
        target_overlay = self._overlay_for_screen(screen)
        if target_overlay is None:
            return
        for ov in self.overlays:
            if ov is not target_overlay and ov._pointer_visible:
                ov._pointer_visible = False
                ov.update(ov.avatar_rect())

        local = target_overlay.mapFromGlobal(global_pos)
        target_x = local.x() + self._FOLLOW_OFFSET_X
        target_y = local.y() + self._FOLLOW_OFFSET_Y

        current_x = target_overlay._pointer_pos.x()
        current_y = target_overlay._pointer_pos.y()

        dx = target_x - current_x
        dy = target_y - current_y
        dist_sq = dx * dx + dy * dy

        if dist_sq < 4:
            new_x, new_y = target_x, target_y
        else:
            step_x = int(dx * _CURSOR_FOLLOW_LERP)
            step_y = int(dy * _CURSOR_FOLLOW_LERP)
            if dx != 0 and step_x == 0:
                step_x = 1 if dx > 0 else -1
            if dy != 0 and step_y == 0:
                step_y = 1 if dy > 0 else -1
            new_x = current_x + step_x
            new_y = current_y + step_y

        old_pos = target_overlay._pointer_pos
        was_visible = target_overlay._pointer_visible
        target_overlay._pointer_pos = QPoint(new_x, new_y)
        target_overlay._pointer_visible = True
        # Repaint only where the avatar was and is; the rest of the screen
        # stays untouched (the old code repainted every monitor at 60 Hz).
        if not was_visible or old_pos != target_overlay._pointer_pos:
            target_overlay.update(
                target_overlay.avatar_rect(old_pos).united(target_overlay.avatar_rect())
            )

    def point_at(
        self,
        physical_x: int,
        physical_y: int,
        monitor: dict,
    ) -> None:
        """Fly the cursor from current position to Claude's target coordinate."""
        self._follow_timer.stop()
        self._dwell_timer.stop()
        self._state = _OverlayState.POINTING

        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target_overlay = self._overlay_for_screen(target_screen)
        if target_overlay is None:
            if not self.overlays:
                return
            target_overlay = self.overlays[0]

        self._pointing_overlay = target_overlay
        local_x, local_y = physical_to_local_logical(
            physical_x, physical_y, target_screen
        )
        target_overlay._pointer_visible = True
        target_overlay._celebrate_next_landing = True
        target_overlay.point_label = self._pending_label
        self._pending_label = ""
        target_overlay.animate_pointer_to(local_x, local_y)
        target_overlay._flight_anim.finished.connect(self._on_point_animation_finished)

    def _on_point_animation_finished(self) -> None:
        """After arriving at target, dwell 3s then fly back to mouse."""
        if self._pointing_overlay:
            self._pointing_overlay._flight_anim.finished.disconnect(
                self._on_point_animation_finished
            )
        self._dwell_timer.start(self._DWELL_MS)

    def _fly_back(self) -> None:
        """Animate the cursor back to the current mouse position."""
        if self._state == _OverlayState.HIDDEN:
            return
        if self._pointing_overlay is None:
            self._resume_idle()
            return
        self._pointing_overlay.point_label = ""
        global_pos = self._cursor_pos_fn()
        local = self._pointing_overlay.mapFromGlobal(global_pos)
        target = QPoint(
            local.x() + self._FOLLOW_OFFSET_X,
            local.y() + self._FOLLOW_OFFSET_Y,
        )
        self._pointing_overlay._flight_anim.finished.connect(self._on_return_finished)
        self._pointing_overlay.animate_pointer_to(target.x(), target.y())

    def _on_return_finished(self) -> None:
        """Return flight complete: resume mouse following."""
        if self._pointing_overlay:
            self._pointing_overlay._flight_anim.finished.disconnect(
                self._on_return_finished
            )
        self._pointing_overlay = None
        self._resume_idle()

    def _resume_idle(self) -> None:
        if self._state == _OverlayState.HIDDEN:
            return
        self._state = _OverlayState.IDLE
        self._follow_timer.start()

    def _overlay_for_screen(self, screen: QScreen) -> OverlayWindow | None:
        target_name = screen.name()
        for overlay in self.overlays:
            if overlay.screen_name == target_name:
                return overlay
        return None

    def show_annotations(self, annotations: list, monitor: dict, busy=None) -> None:
        """Draw teaching marks (PHYSICAL-pixel coords) on the monitor's
        overlay: the buddy flies over and draws them one by one, then waits
        beside them and flies back to the mouse. Other overlays are cleared.
        An empty list clears any old marks."""
        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target_overlay = self._overlay_for_screen(target_screen)
        if target_overlay is None:
            if not self.overlays:
                return
            target_overlay = self.overlays[0]

        local = annotations_to_local(annotations, target_screen)
        for overlay in self.overlays:
            if overlay is not target_overlay:
                overlay.clear_annotations()
        if not local:
            target_overlay.clear_annotations()
            return

        # The buddy is the pen: take it over from following or pointing.
        self._follow_timer.stop()
        self._dwell_timer.stop()
        self._marks_fade_timer.stop()
        self._state = _OverlayState.POINTING
        for handler in (self._on_point_animation_finished, self._on_return_finished):
            for overlay in self.overlays:
                try:
                    overlay._flight_anim.finished.disconnect(handler)
                except TypeError:
                    pass  # not connected
        for overlay in self.overlays:
            if overlay is not target_overlay and overlay._pointer_visible:
                overlay._pointer_visible = False
                overlay.update(overlay.avatar_rect())
        self._pointing_overlay = target_overlay
        self._pending_label = ""
        # Labels keep clear of the island, where the answer's words show.
        avoid = []
        if self.island is not None and self.island.isVisible():
            geo = self.island.geometry()
            top_left = target_overlay.mapFromGlobal(geo.topLeft())
            avoid.append(QRectF(top_left.x(), top_left.y(), geo.width(), geo.height()))
        target_overlay.start_drawing(local, on_done=self._on_drawing_done, avoid=avoid, busy=busy)
        if self.island is not None:
            for label in sorted(target_overlay.drawing_labels):
                self.island.highlight(label)

    def _on_drawing_done(self) -> None:
        """The buddy finished drawing: rest a moment beside the marks."""
        if self._state == _OverlayState.POINTING:
            self._dwell_timer.start(self._DWELL_MS)

    def _fade_marks(self) -> None:
        for overlay in self.overlays:
            overlay.fade_annotations()

    def clear_all_annotations(self) -> None:
        """Clear teaching annotations on every overlay. Called at the start of
        each new interaction so stale shapes never survive a no-speech /
        cancelled / errored turn."""
        self._marks_fade_timer.stop()
        for overlay in self.overlays:
            overlay.clear_annotations()

    def hide_for_capture(self) -> None:
        """Hide ALL overlays + stop timer for screen capture."""
        self._follow_timer.stop()
        self._dwell_timer.stop()
        if self._pointing_overlay and self._pointing_overlay._flight_anim.state() == QVariantAnimation.State.Running:
            self._pointing_overlay._flight_anim.stop()
            try:
                self._pointing_overlay._flight_anim.finished.disconnect()
            except TypeError:
                pass
        self._state = _OverlayState.HIDDEN
        for overlay in self.overlays:
            overlay._pointer_visible = False
            overlay.hide()
        if self.island is not None:
            self.island.hide_for_capture()

    def show_after_capture(self) -> None:
        """Re-show ALL overlays + restart cursor following."""
        for overlay in self.overlays:
            overlay.show()
            overlay.apply_win32_clickthrough()
        if self.island is not None:
            self.island.show_after_capture()
        self._pointing_overlay = None
        self._state = _OverlayState.IDLE
        self._follow_timer.start()

    # --- LISTENING / THINKING delegation (called by app.py state machine) ---
    #
    # The names are kept from the waveform + spinner widgets these used to
    # show. Now they set the avatar's face and the edge glow on the overlay
    # whose screen the cursor is on AT PRESS/RELEASE time (the monitor arg),
    # and move the side island to that screen. If the cursor crosses monitors
    # mid-hold, the glow stays on the original monitor (known limitation).

    def show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        """LISTENING on the overlay containing (physical_x, physical_y), and
        open the island on that screen. Called by app.py on hotkey PRESS."""
        target_overlay = self._pick_overlay_for_point(physical_x, physical_y, monitor)
        if target_overlay is not None and hasattr(target_overlay, "show_waveform"):
            target_overlay.show_waveform()
        if self.island is not None:
            self.island.listening(screen_for_monitor(monitor, QGuiApplication.screens()))

    def hide_waveform(self) -> None:
        """End LISTENING on all overlays. app.py calls this on hotkey RELEASE."""
        for overlay in self.overlays:
            if hasattr(overlay, "hide_waveform"):
                overlay.hide_waveform()

    def show_spinner(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        """THINKING on the overlay containing the cursor, and in the island.
        Called by app.py on hotkey RELEASE, immediately after hide_waveform."""
        target_overlay = self._pick_overlay_for_point(physical_x, physical_y, monitor)
        if target_overlay is not None and hasattr(target_overlay, "show_spinner"):
            target_overlay.show_spinner()
        if self.island is not None:
            self.island.thinking()

    def hide_spinner(self) -> None:
        """End THINKING on all overlays. Called by app.py when:
        - Claude returns a coordinate (just before sig_point_at → bezier fires)
        - Text-only response path (no coordinate)
        - Pipeline error / cancel paths (don't leave it thinking)
        - Top of _handle_press (clear stale from prior interaction)"""
        for overlay in self.overlays:
            if hasattr(overlay, "hide_spinner"):
                overlay.hide_spinner()

    def set_audio_level(self, level: float) -> None:
        """Forward the mic level to every overlay (only a LISTENING one uses
        it, to swell its glow) and to the island's level bars."""
        for overlay in self.overlays:
            if hasattr(overlay, "set_audio_level"):
                overlay.set_audio_level(level)
        if self.island is not None:
            self.island.set_level(level)

    # --- Island text (called by app.py on the Qt main thread) ---------------

    def island_transcript(self, text: str) -> None:
        """What the user asked, once speech-to-text has it."""
        if self.island is not None:
            self.island.set_transcript(text)

    def island_say(self, sentence: str) -> None:
        """A sentence of the answer, as it goes to the voice."""
        if self.island is not None:
            self.island.say(sentence)

    def point_label(self, label: str) -> None:
        """Name of what the next point_at points at: a tag beside the avatar
        when it lands, and a chip in the island's captions."""
        self._pending_label = (label or "").strip()
        if self.island is not None and self._pending_label:
            self.island.highlight(self._pending_label)

    def island_meta(self, text: str) -> None:
        """The small chip: model, or time and cost of the answer."""
        if self.island is not None:
            self.island.set_meta(text)

    def island_note(self, text: str) -> None:
        """A short note in place of an answer ("didn't catch that")."""
        if self.island is not None:
            self.island.note(text)

    def island_done(self) -> None:
        """The turn is over; the island tucks itself away after a pause, and
        the marks fade a few seconds later."""
        if self.island is not None:
            self.island.done()
        marks = [m for overlay in self.overlays for m in overlay._marks]
        if marks:
            # A figure gets longer to study than a pointed-at button.
            figure = any(m.exact for m in marks)
            self._marks_fade_timer.start(self._FIGURE_LINGER_MS if figure else self._MARKS_LINGER_MS)

    def apply_look(self, *, glow_enabled: bool, island_side: str) -> None:
        """Settings that change without a restart: the edge glow and which
        side of the screen the island lives on."""
        for overlay in self.overlays:
            overlay.glow_enabled = glow_enabled
            overlay.update()
        if self.island is not None:
            side = "left" if island_side == "left" else "right"
            if side != self.island.side:
                self.island.side = side
                if self.island._screen is not None:
                    self.island.dock(self.island._screen)
                self.island.update()

    def _pick_overlay_for_point(
        self, physical_x: int, physical_y: int, monitor: dict,
    ):
        """Route a physical-pixel point to the right OverlayWindow.
        Returns None if no overlay exists (empty screens list)."""
        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target = self._overlay_for_screen(target_screen)
        if target is None and self.overlays:
            target = self.overlays[0]
        return target


# --- Manual verification entry point ----------------------------------------

if __name__ == "__main__":
    # Manual click-through verification. Run: py -3.13 -m overlay
    #
    # Opens one overlay per physical monitor and flies the avatar
    # through 5 positions (4 corners + center) of the primary overlay,
    # cycling every 1.5 seconds. User confirms the 5-point checklist below
    # by watching the overlay and trying to click on apps underneath.
    import sys

    from PyQt6.QtWidgets import QApplication

    from capture import set_dpi_awareness

    set_dpi_awareness()  # Idempotent if already set by PyQt6

    print("=" * 70)
    print("Clicky Windows -- overlay.py manual click-through verification")
    print("=" * 70)

    app = QApplication(sys.argv)
    controller = OverlayController()

    print(f"\nCreated {len(controller.overlays)} overlay(s):")
    for i, overlay in enumerate(controller.overlays):
        geom = overlay.geometry()
        print(
            f"  [{i}] screen={overlay.screen_name} "
            f"geometry=({geom.x()}, {geom.y()}, {geom.width()}, {geom.height()}) DIP"
        )

    # Build a 5-point test pattern for the primary overlay:
    # top-left, top-right, bottom-right, bottom-left, center
    primary = controller.overlays[0]
    primary_geom = primary.geometry()
    primary_w = primary_geom.width()
    primary_h = primary_geom.height()
    margin = 100  # DIP
    test_positions = [
        (margin, margin),
        (primary_w - margin, margin),
        (primary_w - margin, primary_h - margin),
        (margin, primary_h - margin),
        (primary_w // 2, primary_h // 2),
    ]

    # itertools.cycle gives us an infinite iterator over the positions
    # without any mutable external state. Cleaner than a [0] counter.
    _positions_iter = cycle(test_positions)

    def _animate_next() -> None:
        x, y = next(_positions_iter)
        primary.animate_pointer_to(x, y)
        print(f"  -> pointer target: ({x}, {y}) DIP on {primary.screen_name}")

    _timer = QTimer()
    _timer.timeout.connect(_animate_next)
    _timer.start(1500)  # move every 1.5 seconds
    _animate_next()  # first position immediately

    print("\nManual verification checklist (confirm each):")
    print("  1. Pixel avatar visible, flies smoothly through 5 positions")
    print("  2. Clicks PASS THROUGH to apps underneath (try clicking desktop icons)")
    print("  3. No taskbar entry for overlay")
    print("  4. Overlay doesn't steal focus from the active app")
    print("  5. Pointer lands on plausible screen positions (corners, center)")
    print("\nClose with Ctrl+C in this terminal or close the Python process.")
    sys.exit(app.exec())
