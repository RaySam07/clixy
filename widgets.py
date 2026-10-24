"""Custom controls shared by Settings and first launch.

Toggle, segmented control, keycaps with a shortcut recorder, the paste zone
for the Claude key, the connection card, and the animated buddy cards. All
painted in the brand (theme.py): ink, butter, paper, coral.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QGuiApplication, QKeyEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import theme


# --- Toggle ----------------------------------------------------------------------

class Toggle(QCheckBox):
    """A switch. It is a QCheckBox underneath, so isChecked()/setChecked()
    and the toggled signal work as usual; only the painting differs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 28)
        self._pos = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate_to)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 (Qt name)
        super().setChecked(checked)
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def _animate_to(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._pos = float(value)
        self.update()

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(46, 28)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        on = max(0.0, min(1.0, self._pos))
        off_c, on_c = QColor(227, 222, 206), theme.INK
        mix = QColor(
            int(off_c.red() + (on_c.red() - off_c.red()) * on),
            int(off_c.green() + (on_c.green() - off_c.green()) * on),
            int(off_c.blue() + (on_c.blue() - off_c.blue()) * on),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob_d = self.height() - 8
        x = 4 + (self.width() - 8 - knob_d) * self._pos
        p.setBrush(theme.BUTTER if on > 0.5 else QColor(255, 255, 255))
        p.drawEllipse(QRectF(x, 4, knob_d, knob_d))
        if self.hasFocus():
            p.setPen(QPen(theme.INK, 1.5, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-0.5, -0.5, 0.5, 0.5), track.height() / 2, track.height() / 2)


# --- Segmented control ---------------------------------------------------------

class Segmented(QFrame):
    """A pill of mutually exclusive options. Emits changed(value)."""

    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("segTrack")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, label in options:
            button = QPushButton(label)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, v=value: self.changed.emit(v))
            self._group.addButton(button)
            layout.addWidget(button)
            self._buttons[value] = button
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def value(self) -> str:
        for value, button in self._buttons.items():
            if button.isChecked():
                return value
        return ""

    def set_value(self, value: str) -> None:
        button = self._buttons.get(value)
        if button is not None:
            button.setChecked(True)
        else:
            self._group.setExclusive(False)
            for b in self._buttons.values():
                b.setChecked(False)
            self._group.setExclusive(True)


# --- Keycaps + shortcut recorder ------------------------------------------------

_QT_TOKENS = {
    Qt.Key.Key_Control: "ctrl", Qt.Key.Key_Alt: "alt", Qt.Key.Key_AltGr: "alt",
    Qt.Key.Key_Shift: "shift", Qt.Key.Key_Meta: "win", Qt.Key.Key_Super_L: "win",
    Qt.Key.Key_Super_R: "win", Qt.Key.Key_Space: "space",
}


def token_for_qt_key(key: int) -> str | None:
    """Qt key code -> hotkey token ('ctrl', 'win', 'k', 'f9'...), or None."""
    try:
        key = Qt.Key(key)
    except ValueError:
        return None
    if key in _QT_TOKENS:
        return _QT_TOKENS[key]
    if Qt.Key.Key_A.value <= key.value <= Qt.Key.Key_Z.value:
        return chr(ord("a") + key.value - Qt.Key.Key_A.value)
    if Qt.Key.Key_0.value <= key.value <= Qt.Key.Key_9.value:
        return chr(ord("0") + key.value - Qt.Key.Key_0.value)
    if Qt.Key.Key_F1.value <= key.value <= Qt.Key.Key_F24.value:
        return f"f{key.value - Qt.Key.Key_F1.value + 1}"
    return None


class KeyCaps(QWidget):
    """The shortcut drawn as keycaps. Call record() to capture a new one by
    having the person press it; emits recorded(combo) or stays as it was on
    Esc. Big for first launch, small for Settings."""

    recorded = pyqtSignal(str)

    def __init__(self, combo: str, *, big: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._big = big
        self._combo = combo
        self._recording = False
        self._held: list[str] = []
        self._pressed: set[str] = set()
        self._blink = QTimer(self)
        self._blink.timeout.connect(self.update)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._resize()

    @property
    def combo(self) -> str:
        return self._combo

    def set_combo(self, combo: str) -> None:
        self._combo = combo
        self._resize()
        self.update()

    def _labels(self) -> list[str]:
        if self._recording:
            if not self._held:
                return ["Press keys…"]
            from hotkey import describe
            return describe("+".join(self._held)).split(" + ")
        from hotkey import describe
        return describe(self._combo).split(" + ")

    def _cap_font(self) -> QFont:
        return theme.display_font(20 if self._big else 10.5)

    def _metrics(self) -> tuple[float, float, float, float]:
        """cap height, padding, gap, plus-sign width."""
        if self._big:
            return 78.0, 24.0, 14.0, 26.0
        return 30.0, 11.0, 6.0, 12.0

    def _resize(self) -> None:
        cap_h, pad, gap, plus = self._metrics()
        fm = QFontMetricsF(self._cap_font())
        labels = self._labels()
        width = sum(max(cap_h, fm.horizontalAdvance(l) + 2 * pad) for l in labels)
        width += (len(labels) - 1) * (2 * gap + plus)
        self.setFixedSize(int(math.ceil(width)) + 4, int(cap_h) + 6)

    def record(self) -> None:
        self._recording = True
        self._held, self._pressed = [], set()
        self._blink.start(500)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        self._resize()
        self.update()

    def _stop(self) -> None:
        self._recording = False
        self._blink.stop()
        self.releaseKeyboard()
        self._resize()
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._recording:
            return super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Escape:
            self._stop()
            return
        token = token_for_qt_key(event.key())
        if token is not None and not event.isAutoRepeat():
            self._pressed.add(token)
            if token not in self._held:
                self._held.append(token)
            self._resize()
            self.update()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._recording:
            return super().keyReleaseEvent(event)
        if event.isAutoRepeat():
            return
        token = token_for_qt_key(event.key())
        self._pressed.discard(token)
        if not self._pressed and self._held:
            # Everything let go: that was the combo. Two keys at least, so a
            # stray single key can't become a push-to-talk trap.
            if len(self._held) >= 2:
                from hotkey import format_combo, parse_combo
                self._combo = format_combo(parse_combo("+".join(self._held)))
                self._stop()
                self.recorded.emit(self._combo)
            else:
                self._held = []
                self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cap_h, pad, gap, plus = self._metrics()
        font = self._cap_font()
        fm = QFontMetricsF(font)
        x = 2.0
        labels = self._labels()
        blink_on = not self._recording or int(time.monotonic() * 2) % 2 == 0
        for i, label in enumerate(labels):
            w = max(cap_h, fm.horizontalAdvance(label) + 2 * pad)
            lip = 5.0 if self._big else 3.0
            radius = 16.0 if self._big else 8.0
            body = QRectF(x, 1, w, cap_h)
            ink = theme.INK if not self._recording else QColor(theme.INK.red(), theme.INK.green(), theme.INK.blue(), 255 if blink_on else 110)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawRoundedRect(body.adjusted(0, lip, 0, lip), radius, radius)  # the key's lip
            p.setBrush(theme.BUTTER if (self._big and not self._recording) else theme.CARD)
            p.setPen(QPen(ink, 1.5))
            p.drawRoundedRect(body.adjusted(0.75, 0.75, -0.75, -0.75), radius, radius)
            p.setFont(font)
            p.setPen(ink)
            p.drawText(body, Qt.AlignmentFlag.AlignCenter, label)
            x += w
            if i < len(labels) - 1:
                p.setPen(theme.MUTED)
                p.setFont(theme.display_font(18 if self._big else 10))
                p.drawText(QRectF(x + gap, 1, plus, cap_h), Qt.AlignmentFlag.AlignCenter, "+")
                x += 2 * gap + plus


# --- Paste zone -------------------------------------------------------------------

def classify_key(text: str) -> tuple[str | None, str]:
    """What kind of key this looks like. Returns (kind, message):
    'anthropic' or 'openrouter' when it looks right, None when it doesn't.
    A shape check only; the key is first used on your first question."""
    key = text.strip()
    if not key:
        return None, ""
    if key.startswith("sk-ant-") and len(key) > 30:
        return "anthropic", "Looks like a Claude key."
    if key.startswith("sk-or-") and len(key) > 30:
        return "openrouter", "Looks like an OpenRouter key. Claude runs through OpenRouter."
    return None, "That doesn't look like a Claude key. It starts with sk-ant-."


_PASTE_HINT = "Ctrl + V works too · encrypted in Windows Credential Manager"


class PasteZone(QFrame):
    """A big drop-in field for the Claude key. Paste reads the clipboard; the
    field can also be typed into. Turns butter with a check when the key looks
    right. Emits keyChanged(text) on every change."""

    keyChanged = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind: str | None = None
        self._check = 0.0
        self.setMinimumHeight(118)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 18, 16)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Paste your Claude key")
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setFrame(False)
        self.field.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0; min-height: 34px; }")
        self.field.setFont(theme.display_font(17))
        self.field.textChanged.connect(self._on_text)
        row.addWidget(self.field, stretch=1)
        self.paste_button = QPushButton("Paste")
        self.paste_button.setObjectName("primary")
        self.paste_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paste_button.clicked.connect(self.paste)
        row.addWidget(self.paste_button)
        layout.addLayout(row)
        self.hint = QLabel(_PASTE_HINT)
        self.hint.setFont(theme.mono_font(8.5))
        self.hint.setStyleSheet(f"color: {theme.MUTED.name()};")
        layout.addWidget(self.hint)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(380)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_check", float(v)), self.update()))

    def text(self) -> str:
        return self.field.text().strip()

    def setText(self, text: str) -> None:  # noqa: N802
        self.field.setText(text)

    def paste(self) -> None:
        clip = QGuiApplication.clipboard().text().strip()
        if clip:
            self.field.setText(clip)
        self.field.setFocus()

    @property
    def kind(self) -> str | None:
        return self._kind

    def _on_text(self, text: str) -> None:
        kind, message = classify_key(text)
        was_ok = self._kind is not None
        self._kind = kind
        self.hint.setText(message or _PASTE_HINT)
        color = theme.INK if kind else (theme.CORAL if text.strip() else theme.MUTED)
        self.hint.setStyleSheet(f"color: {color.name()};")
        if kind and not was_ok:
            self._anim.stop()
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        elif not kind:
            self._check = 0.0
        self.update()
        self.keyChanged.emit(text.strip())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bad = self._kind is None and bool(self.text())
        if self._kind:
            p.setBrush(theme.BUTTER)
            p.setPen(QPen(theme.INK, 1.5))
        else:
            p.setBrush(theme.PAPER)
            pen = QPen(theme.CORAL if bad else QColor(207, 200, 182), 1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
        p.drawRoundedRect(rect, 20, 20)
        if self._check > 0.01:
            r = 15 * self._check
            c = QPointF(rect.right() - 32, rect.bottom() - 28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.INK)
            p.drawEllipse(c, r, r)
            if self._check > 0.6:
                p.setPen(QPen(theme.BUTTER, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                path = QPainterPath(QPointF(c.x() - 6, c.y() + 0.5))
                path.lineTo(QPointF(c.x() - 1.5, c.y() + 5))
                path.lineTo(QPointF(c.x() + 6.5, c.y() - 4.5))
                p.drawPath(path)


# --- Connection card -------------------------------------------------------------

class ConnectionCard(QFrame):
    """Butter card showing the AI connection: model, status, key ending,
    cost. Replace swaps it for the paste zone."""

    replaceClicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("conn")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 20, 20, 20)
        layout.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(8)
        self.model = QLabel()
        self.model.setObjectName("connModel")
        text.addWidget(self.model)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.live = QLabel()
        self.live.setObjectName("liveChip")
        self.key = QLabel()
        self.key.setObjectName("connChip")
        self.cost = QLabel()
        self.cost.setObjectName("connChip")
        for chip in (self.live, self.key, self.cost):
            chip.setFont(theme.mono_font(8.5))
            chips.addWidget(chip)
        chips.addStretch(1)
        text.addLayout(chips)
        layout.addLayout(text, stretch=1)
        self.replace = QPushButton("Replace")
        self.replace.setCursor(Qt.CursorShape.PointingHandCursor)
        self.replace.clicked.connect(self.replaceClicked.emit)
        layout.addWidget(self.replace, alignment=Qt.AlignmentFlag.AlignVCenter)

    def show_connection(self, model: str, key: str, cost: str, *, local: bool = False) -> None:
        """``model`` may be rich text. ``local``: a model on this PC, no key."""
        self.model.setText(model)
        connected = bool(key) or local
        self.live.setText("● on this PC" if local else ("● connected" if connected else "○ no key yet"))
        self.key.setText(f"key ···{key[-4:]}" if key and not local else "")
        self.key.setVisible(bool(key) and not local)
        self.cost.setText(cost)
        self.cost.setVisible(bool(cost))


# --- Microphone meter ----------------------------------------------------------------

class MicMeter(QWidget):
    """Nine little bars that rise with your voice, like the notch's."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(58, 24)
        self._level = 0.0
        self._t0 = time.monotonic()

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        if abs(level - self._level) > 0.005 or level > 0:
            self._level = level
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        t = time.monotonic() - self._t0
        n, gap = 9, 3.0
        w = (self.width() - gap * (n - 1)) / n
        mid = (n - 1) / 2
        for i in range(n):
            weight = 1 - abs(i - mid) / (mid + 1)
            h = 3 + (self.height() - 3) * self._level * weight * (0.75 + 0.25 * math.sin(t * 11 + i))
            p.setBrush(theme.INK if self._level > 0.03 else QColor(217, 212, 198))
            p.drawRoundedRect(QRectF(i * (w + gap), (self.height() - h) / 2, w, h), w / 2, w / 2)


# --- Look preview --------------------------------------------------------------------

class LookPreview(QWidget):
    """A small live screen showing the edge glow and the notch on the chosen
    side, with your buddy listening in it. Slides when the side changes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._glow = True
        self._side = 1.0          # 0 = left edge, 1 = right edge
        self._side_target = 1.0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_glow(self, on: bool) -> None:
        self._glow = on
        self.update()

    def set_side(self, side: str) -> None:
        self._side_target = 0.0 if side == "left" else 1.0

    def showEvent(self, event) -> None:  # noqa: N802
        self._timer.start(33)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        self._side += (self._side_target - self._side) * 0.18
        if abs(self._side_target - self._side) < 0.002:
            self._side = self._side_target
        self.update()

    def paintEvent(self, _event) -> None:
        from PyQt6.QtGui import QConicalGradient, QLinearGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = time.monotonic() - self._t0
        screen = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(screen, 18, 18)
        p.setClipPath(clip)

        # A calm desktop with two windows on it.
        bg = QLinearGradient(screen.topLeft(), screen.bottomRight())
        bg.setColorAt(0, QColor(233, 235, 246))
        bg.setColorAt(1, QColor(207, 211, 234))
        p.fillRect(screen, bg)
        p.setPen(Qt.PenStyle.NoPen)
        for i, (fx, fy, fw, fh) in enumerate(((0.16, 0.2, 0.42, 0.62), (0.47, 0.34, 0.34, 0.5))):
            win = QRectF(screen.x() + screen.width() * fx, screen.y() + screen.height() * fy,
                         screen.width() * fw, screen.height() * fh)
            p.setBrush(QColor(255, 255, 255, 235 if i else 200))
            p.drawRoundedRect(win, 10, 10)
            p.setBrush(QColor(10, 10, 18, 18))
            for line in range(3):
                p.drawRoundedRect(QRectF(win.x() + 14, win.y() + 18 + line * 14, win.width() * (0.7 - 0.15 * line), 6), 3, 3)

        # The glow, flowing around the edges in the buddy's colors.
        if self._glow:
            gradient = QConicalGradient(screen.center(), (t * 70.0) % 360.0)
            for pos, color in theme.current_palette().glow:
                gradient.setColorAt(pos, color)
            p.setBrush(Qt.BrushStyle.NoBrush)
            swell = 1.0 + 0.35 * (0.5 + 0.5 * math.sin(t * 3.1))
            for i in range(8):
                p.setOpacity(0.5 if i == 0 else 0.2 * (1 - i / 8))
                p.setPen(QPen(QBrush(gradient), (2.0 + i * 2.2) * swell))
                p.drawRoundedRect(screen, 18, 18)
            p.setOpacity(1.0)

        # The notch, grown into its listening capsule.
        # It runs 20 px past the screen edge, so only its inner corners show
        # rounded; sliding sides, it crosses the screen as a free capsule.
        w, h, past = 40.0, 132.0, 20.0
        cy = screen.y() + screen.height() * 0.46
        left_x, right_x = screen.left() - past, screen.right() - w
        x = left_x + (right_x - left_x) * self._side
        notch = QRectF(x, cy - h / 2, w + past, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0))
        p.drawRoundedRect(notch, 20, 20)
        cx = x + past * (1 - self._side) + w / 2
        p.setBrush(theme.CORAL)
        p.drawEllipse(QPointF(cx, notch.top() + 14), 3, 3)
        for i in range(7):
            weight = 1 - abs(i - 3) / 3
            bar_w = 4 + 16 * (0.35 + 0.65 * weight) * (0.55 + 0.45 * math.sin(t * 8 + i * 1.3))
            p.setBrush(theme.BUTTER)
            p.drawRoundedRect(QRectF(cx - bar_w / 2, notch.top() + 28 + i * 7, bar_w, 3), 1.5, 1.5)
        theme.paint_avatar_in(p, QRectF(cx - 20, notch.bottom() - 48, 40, 40), t=t, mood="listening",
                              scale=0.8, device_pixel_ratio=self.devicePixelRatioF())


# --- Buddy cards ---------------------------------------------------------------------

class BuddyView(QWidget):
    """A buddy on its own, alive at 10 fps. Shows the current buddy unless
    given one, so it follows a switch made anywhere."""

    def __init__(self, size: int, *, scale: float = 2.0, mood: str = "idle", glow: bool = False,
                 avatar_id: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.avatar_id = avatar_id
        self.mood = mood
        self._scale, self._glow = scale, glow
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(100)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        theme.paint_avatar_in(p, QRectF(self.rect()), t=time.monotonic() - self._t0, mood=self.mood,
                              avatar_id=self.avatar_id, scale=self._scale,
                              device_pixel_ratio=self.devicePixelRatioF(), glow=self._glow)


class AvatarCard(QAbstractButton):
    """A buddy, alive: blinks and flickers at its own 10 fps, with its name
    and what it is. Checked: butter with an ink border."""

    def __init__(self, avatar_id: str, *, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.avatar_id = avatar_id
        self._compact = compact
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(124, 150) if compact else QSize(168, 188))
        av = theme.avatar(avatar_id)
        self.setToolTip(f"{av.name}: {av.tagline}")
        self._t0 = time.monotonic() + hash(avatar_id) % 7 * 0.37  # don't blink in sync
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(100)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        checked = self.isChecked()
        hover = self.underMouse()
        p.setBrush(theme.BUTTER if checked else theme.CARD)
        p.setPen(QPen(theme.INK if (checked or hover) else theme.LINE, 2 if checked else 1.5))
        p.drawRoundedRect(rect, 22, 22)

        av = theme.avatar(self.avatar_id)
        top_h = rect.height() - (48 if self._compact else 58)
        mood = "happy" if checked else ("listening" if hover else "idle")
        theme.paint_avatar_in(p, QRectF(rect.left(), rect.top() + 6, rect.width(), top_h),
                              t=time.monotonic() - self._t0, mood=mood, avatar_id=self.avatar_id,
                              scale=1.8 if self._compact else 2.4, device_pixel_ratio=self.devicePixelRatioF())

        p.setPen(theme.INK)
        p.setFont(theme.ui_font(11 if self._compact else 12, QFont.Weight.DemiBold))
        name_y = rect.bottom() - (34 if self._compact else 47)
        p.drawText(QRectF(rect.left(), name_y, rect.width(), 20), Qt.AlignmentFlag.AlignHCenter, av.name)
        if not self._compact:
            p.setPen(theme.MUTED)
            p.setFont(theme.ui_font(9, QFont.Weight.Light))
            p.drawText(QRectF(rect.left() + 8, name_y + 19, rect.width() - 16, 18), Qt.AlignmentFlag.AlignHCenter, av.tagline)


def buddy_grid(on_pick, *, compact: bool = False, columns: int = 3) -> tuple[QWidget, dict[str, AvatarCard]]:
    """The five buddies as a grid of cards; picking one calls on_pick(id)."""
    from PyQt6.QtWidgets import QGridLayout
    container = QWidget()
    grid = QGridLayout(container)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(12)
    group = QButtonGroup(container)
    group.setExclusive(True)
    cards: dict[str, AvatarCard] = {}
    for i, av in enumerate(theme.AVATARS):
        card = AvatarCard(av.id, compact=compact)
        card.setChecked(av.id == theme.current_avatar_id())
        card.clicked.connect(lambda _c=False, avatar_id=av.id: on_pick(avatar_id))
        group.addButton(card)
        grid.addWidget(card, i // columns, i % columns)
        cards[av.id] = card
    grid.setColumnStretch(columns, 1)
    return container, cards


def logo_view(size: int) -> "BuddyView":
    """Clixy's logo, alive: Ember flickering at its own pace."""
    scale = size / (18 * theme.CELL)
    return BuddyView(size, scale=scale, mood="happy", avatar_id=theme.LOGO_BUDDY)
