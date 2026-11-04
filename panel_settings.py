"""Settings, inside the Clixy panel.

The gear in the panel turns it into Settings: a back button, "Settings" and a
glossy Done on top; the sections down the left, each with its own coloured
tile (General, Look, Sounds, Voice, Buddy, Shortcut, Brain, Privacy, About)
and a highlight that slides to the one you pick; the chosen section on the
right, sliding in, under a big header with its tile.

Every control is tactile and makes its little sound (ui_kit): switches spring
across, segments slide, cards lift under the mouse and pop a check when
picked, the volume slider ticks as you drag.

Every setting is read, checked and saved by settings_dialog.SettingsDialog,
kept hidden behind the panel: the panel's controls set the dialog's own
controls, and Done runs its Save (keyring, provider ids, shared keys, a
pasted key moving to its provider, Save held back while a key is missing).
So the storage lives in one place, and the old dialog (the tray's
Settings...) keeps working as it was. The three settings that are the
panel's own (APPEARANCE, SOUND_EFFECTS, SOUND_VOLUME) preview live and are
stored by Done too; Back puts them back.

Only what has a real setting behind it is here. A key is never shown: only
whether one is saved, and Replace takes a new one in a masked box.

Threading: Qt main thread only.
"""
from __future__ import annotations

import re
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
import ui_kit
from panel import PanelButton, _mix, _pill, _rgba, accent, font, keycap_size, paint_keycap
from ui_kit import Motion, Tactile, skin, sound

SECTIONS = (("general", "General"), ("look", "Look"), ("sounds", "Sounds"), ("voice", "Voice"),
            ("buddy", "Buddy"), ("shortcut", "Shortcut"), ("model", "Brain"), ("privacy", "Privacy"),
            ("about", "About"))
HEROES = {
    "general": ("General", "Where Clixy lives on your screen and how it hears you"),
    "look": ("Look", "Bright or dark: pick how Clixy's panel looks"),
    "sounds": ("Sounds", "Little pops, clicks and chimes as things move"),
    "voice": ("Voice", "How Clixy sounds, and where it listens"),
    "buddy": ("Buddy", "Who keeps you company. Its colour lights up Clixy"),
    "shortcut": ("Shortcut", "The keys you hold to talk"),
    "model": ("Brain", "The AI that answers you"),
    "privacy": ("Privacy", "Where each thing goes"),
    "about": ("About", "Your AI buddy that lives on your screen"),
}
W, H = 880, 600
NAV_X, NAV_Y, NAV_W = 22, 74, 200
CONTENT_X = NAV_X + NAV_W + 22
CONTENT_W = W - CONTENT_X - 14          # 622: the page plus room for its scroll bar
PAGE_W = CONTENT_W - 14                  # 608
CONTENT_Y = 66

# --- Labels that follow the skin ---------------------------------------------------------------------


def _ink(label: QLabel) -> None:
    sk = skin()
    role = label.property("ink") or "text"
    colour = {"text": sk.text, "text2": sk.text2, "text3": sk.text3,
              "accent": _mix(accent(), sk.text, 0.3)}.get(role, sk.text)
    label.setStyleSheet(f"QLabel {{ color: {colour.name()}; background: transparent; }}")


def _label(text: str, px: float, weight: int, role: str = "text", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setFont(font(px, weight))
    label.setProperty("ink", role)
    label.setWordWrap(wrap)
    _ink(label)
    return label


def _line_edit_style(radius: int = 12) -> str:
    sk = skin()
    return (f"QLineEdit {{ background: {sk.field.name()}; color: {sk.text.name()};"
            f" border: 1px solid {sk.border.name(QColor.NameFormat.HexArgb)};"
            f" border-radius: {radius}px; padding: 8px 12px; min-height: 0;"
            f" selection-background-color: {_rgba(accent(), 0.35).name(QColor.NameFormat.HexArgb)};"
            f" selection-color: {sk.text.name()}; }}"
            f"QLineEdit:focus {{ border: 1.5px solid {_rgba(accent(), 0.75).name(QColor.NameFormat.HexArgb)}; }}")


def _edit(width: int, *, password: bool = False, placeholder: str = "") -> QLineEdit:
    edit = QLineEdit()
    edit.setFont(font(13, 600))
    edit.setFixedWidth(width)
    edit.setProperty("skin_edit", True)
    edit.setStyleSheet(_line_edit_style())
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    if placeholder:
        edit.setPlaceholderText(placeholder)
    return edit


def restyle(root: QWidget) -> None:
    """Re-colour a tree of widgets for the skin now in use."""
    for label in root.findChildren(QLabel):
        if label.property("ink"):
            _ink(label)
    for edit in root.findChildren(QLineEdit):
        if edit.property("skin_edit"):
            edit.setStyleSheet(_line_edit_style())
    for combo in root.findChildren(SkinCombo):
        combo.restyle()
    for scroll in root.findChildren(QScrollArea):
        scroll.setStyleSheet(_scroll_style())
    for w in root.findChildren(QWidget):
        w.update()
    root.update()


def _scroll_style() -> str:
    handle = _rgba(skin().text, 0.18).name(QColor.NameFormat.HexArgb)
    return ("QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 6px 0 6px 0; }"
            f"QScrollBar::handle:vertical {{ background: {handle}; border-radius: 4px; min-height: 36px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }")


def _sfx():
    try:
        import sfx
        return sfx
    except Exception:
        return None


# --- Controls ---------------------------------------------------------------------------------------

Switch = ui_kit.Toggle


class Segment(ui_kit.Segmented):
    """Mutually exclusive options (Top / Left / Right) with a sliding thumb."""


class ChoiceCard(Tactile, QAbstractButton):
    """A card to pick (a look, a voice engine, an AI, a buddy). It lifts under
    the mouse; picked, it wears a ring in the accent and a check that pops in."""

    def __init__(self, title: str, line2: str = "", line3: str = "", parent: QWidget | None = None, *,
                 avatar_id: str = "", disabled_note: str = "", tile: str = "", preview: str = "") -> None:
        super().__init__(parent)
        self.setText(title)
        self.line2, self.line3 = line2, line3
        self.avatar_id = avatar_id
        self.disabled_note = disabled_note
        self.tile = tile
        self.preview = preview
        self.PRESS_SOUND = "buddy" if avatar_id else "select"
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor if not disabled_note else Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setEnabled(not disabled_note)
        self.setAccessibleName(title)
        self.setMinimumHeight(164 if preview else 136 if avatar_id else 136 if tile else 112)
        self._t = 0.0
        self._tactile_init()
        self.spring("pick", 0.0, 520.0, 20.0)
        self.toggled.connect(self._picked)

    def nextCheckState(self) -> None:  # noqa: N802
        self.setChecked(True)         # a click picks it; picking another one un-picks it

    def _picked(self, on: bool) -> None:
        if on and self.underMouse():
            self.go("pick", 0.0, snap=True)
            self.go("pick", 1.0)
        else:
            self.go("pick", 1.0 if on else 0.0, snap=True)

    def ring(self) -> QColor:
        if self.avatar_id:
            return QColor(theme.avatar(self.avatar_id).palette.flood)
        return accent()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        sk = skin()
        hover = self.val("hover") if self.isEnabled() else 0.0
        on = self.isChecked()
        rect = QRectF(self.rect()).adjusted(5, 6, -5, -8).translated(0, -2.5 * hover)
        self.squish(p, rect, 0.03)
        ring = self.ring()
        fill = sk.surface
        if on and self.avatar_id:
            fill = _mix(sk.surface, ring, 0.1 if not sk.dark else 0.16)
        shape = ui_kit.paint_card(p, rect, sk, radius=18, lift=0.25 + 0.75 * hover if self.isEnabled() else 0.0,
                                  fill=fill)
        if on:
            p.setPen(QPen(ring, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 19, 19)
        elif hover > 0.01:
            p.setPen(QPen(_rgba(ring, 0.35 * hover), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), 18.5, 18.5)
        if not self.isEnabled():
            p.setOpacity(0.5)
        if self.preview:
            self._paint_preview(p, rect, shape)
        elif self.avatar_id:
            self._paint_buddy(p, rect, on)
        else:
            self._paint_text(p, rect)
        pick = self.val("pick")
        if pick > 0.01:
            r = 11.0 * pick
            c = QPointF(rect.right() - 18, rect.top() + 18)
            badge = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            p.setOpacity(1.0)
            ui_kit.paint_gel(p, badge, ring)
            ui_kit.paint_icon(p, "check", badge.adjusted(r * 0.42, r * 0.42, -r * 0.42, -r * 0.42),
                              ui_kit.ink_on(ring), 3.4)
        p.end()

    def _paint_text(self, p: QPainter, rect: QRectF) -> None:
        sk = skin()
        x, w = rect.left() + 16, rect.width() - 32
        y = rect.top() + 14
        if self.tile:
            ui_kit.paint_tile(p, QRectF(x, y, 30, 30), self.tile)
            y += 30 + 10
        f = font(15, 800)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(sk.text)
        p.drawText(QRectF(x, y, w - (26 if not self.tile else 0), fm.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, w - (26 if not self.tile else 0)))
        y += fm.height() + 2
        for text, px, weight, colour in ((self.line2, 13, 700, sk.text2), (self.line3, 12, 600, sk.text3)):
            if not text:
                continue
            f = font(px, weight)
            fm = QFontMetricsF(f)
            p.setFont(f)
            p.setPen(colour)
            p.drawText(QRectF(x, y, w, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, w))
            y += fm.height() + 1

    def _paint_buddy(self, p: QPainter, rect: QRectF, on: bool) -> None:
        sk = skin()
        dpr = self.devicePixelRatioF()
        av = theme.avatar(self.avatar_id)
        min_x, min_y, max_x, max_y = av.extent
        cells_w, cells_h = max_x - min_x + 1, max_y - min_y + 1
        device = max(1, int(min(50 / cells_w, 54 / cells_h) * dpr))
        hop = -5.0 * self.val("hover") + 3.0 * self.val("press")
        box = QRectF(rect.center().x() - 27, rect.top() + 14 + hop, 54, 56)
        theme.paint_avatar_in(p, box, t=self._t, avatar_id=self.avatar_id, scale=device / (2 * dpr),
                              device_pixel_ratio=dpr, mood="happy" if on or self.val("hover") > 0.5 else "idle")
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = font(14, 800)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(sk.text)
        name_y = rect.top() + 14 + 56 + 8
        p.drawText(QRectF(rect.left(), name_y, rect.width(), fm.height()), Qt.AlignmentFlag.AlignCenter, self.text())
        f2 = font(11, 700)
        p.setFont(f2)
        p.setPen(self.ring() if on else sk.text3)
        tag = QFontMetricsF(f2).elidedText(av.tagline, Qt.TextElideMode.ElideRight, rect.width() - 16)
        p.drawText(QRectF(rect.left() + 8, name_y + fm.height(), rect.width() - 16, 18), Qt.AlignmentFlag.AlignCenter,
                   tag)

    def _paint_preview(self, p: QPainter, rect: QRectF, shape: QPainterPath) -> None:
        """A tiny Clixy panel in this look (Match Windows: half and half)."""
        sk = skin()
        box = QRectF(rect.left() + 12, rect.top() + 12, rect.width() - 24, 80)
        looks = {"light": [ui_kit.DAYLIGHT], "dark": [ui_kit.MIDNIGHT],
                 "system": [ui_kit.DAYLIGHT, ui_kit.MIDNIGHT]}[self.preview]
        clip = QPainterPath()
        clip.addRoundedRect(box, 12, 12)
        p.save()
        p.setClipPath(clip)
        for i, look in enumerate(looks):
            part = QPainterPath()
            if len(looks) == 1:
                part = clip
            elif i == 0:
                part.moveTo(box.topLeft())
                part.lineTo(box.topRight())
                part.lineTo(box.bottomLeft())
                part.closeSubpath()
            else:
                part.moveTo(box.topRight())
                part.lineTo(box.bottomRight())
                part.lineTo(box.bottomLeft())
                part.closeSubpath()
            p.save()
            p.setClipPath(part, Qt.ClipOperation.IntersectClip)
            ui_kit.paint_canvas(p, clip, box, look, dots=False)
            card = QRectF(box.left() + 14, box.top() + 30, box.width() * 0.5, 38)
            p.fillPath(_pill(card, 8), look.surface)
            p.setPen(QPen(look.border, 1.0))
            p.drawPath(_pill(card, 8))
            p.setPen(Qt.PenStyle.NoPen)
            for k, (dy, frac) in enumerate(((10, 0.7), (20, 0.5))):
                p.fillPath(_pill(QRectF(card.left() + 10, card.top() + dy - 2, card.width() * frac - 10, 4)),
                           look.text3 if k else look.text2)
            p.fillPath(_pill(QRectF(box.left() + 14, box.top() + 13, 44, 6)), look.text)
            gel = QRectF(box.right() - 54, box.top() + 40, 40, 18)
            ui_kit.paint_gel(p, gel, accent())
            p.restore()
        p.restore()
        p.setPen(QPen(sk.border, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 11.5, 11.5)
        f = font(14, 800)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(sk.text)
        y = box.bottom() + 10
        p.drawText(QRectF(rect.left() + 14, y, rect.width() - 28, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                   self.text())
        if self.line2:
            p.setFont(font(12, 600))
            p.setPen(sk.text3)
            p.drawText(QRectF(rect.left() + 14, y + fm.height(), rect.width() - 28, 18), Qt.AlignmentFlag.AlignVCenter,
                       self.line2)


class ShortcutCaps(QWidget):
    """The shortcut as big tactile keycaps; ``record()`` captures a new one. The
    capture is widgets.KeyCaps's own (two keys at least, Esc keeps the old)."""

    recorded = pyqtSignal(str)
    PX, PAD = 17.0, (16.0, 8.0, 9.0)

    def __init__(self, combo: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caps = _capture(combo, self)     # widgets.KeyCaps does the capturing, unseen
        self._caps.move(0, 0)
        self._caps.recorded.connect(self._done)
        self._caps._blink.timeout.connect(self.update)
        self._fit()

    @property
    def recording(self) -> bool:
        return bool(self._caps._recording)

    def record(self) -> None:
        self._caps.record()
        sound("select")
        self._fit()

    def set_combo(self, combo: str) -> None:
        self._caps.set_combo(combo)
        self._fit()

    def _done(self, combo: str) -> None:
        self._fit()
        sound("success")
        self.recorded.emit(combo)

    def _labels(self) -> list[str]:
        return self._caps._labels()

    def _fit(self) -> None:
        widths = [keycap_size(label, self.PX, self.PAD).width() for label in self._labels()]
        plus = QFontMetricsF(font(18, 700)).horizontalAdvance("+") + 2 * 12
        h = keycap_size("W", self.PX, self.PAD).height() + 8
        self.setFixedSize(round(sum(widths) + plus * (len(widths) - 1)) + 4, h)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        x = 2.0
        labels = self._labels()
        plus_f = font(18, 700)
        plus = QFontMetricsF(plus_f).horizontalAdvance("+")
        for i, label in enumerate(labels):
            size = keycap_size(label, self.PX, self.PAD)
            rect = QRectF(x, 1, size.width(), size.height())
            paint_keycap(p, rect, label, px=self.PX, radius=11, edge=5)
            if self.recording:
                p.setPen(QPen(_rgba(accent(), 0.85), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(-2, -2, 2, 7), 13, 13)
            x += size.width()
            if i < len(labels) - 1:
                p.setFont(plus_f)
                p.setPen(sk.text3)
                p.drawText(QRectF(x, 1, plus + 24, size.height()), Qt.AlignmentFlag.AlignCenter, "+")
                x += plus + 24
        p.end()


DarkKeyCaps = ShortcutCaps


def _capture(combo: str, owner: ShortcutCaps):
    """widgets.KeyCaps, kept for its capture only: 1 px, painting nothing,
    telling ``owner`` whenever the keys it shows change."""
    from widgets import KeyCaps

    class _Capture(KeyCaps):
        def _resize(self) -> None:
            self.setFixedSize(1, 1)
            if getattr(owner, "_caps", None) is self:
                owner._fit()

        def paintEvent(self, _event) -> None:  # noqa: N802
            pass

    return _Capture(combo, parent=owner)


class SkinCombo(QComboBox):
    """A dropdown in the skin's colours, with a chevron (the microphone)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(font(13, 700))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restyle()
        self.activated.connect(lambda _i: sound("select"))

    def restyle(self) -> None:
        sk = skin()
        self.setStyleSheet(
            f"QComboBox {{ background: {sk.surface.name()}; color: {sk.text.name()};"
            f" border: 1px solid {sk.border.name(QColor.NameFormat.HexArgb)};"
            f" border-radius: 12px; padding: 8px 34px 8px 14px; min-height: 0; }}"
            f"QComboBox:hover {{ border-color: {_rgba(accent(), 0.5).name(QColor.NameFormat.HexArgb)}; }}"
            "QComboBox::drop-down { border: none; width: 30px; }"
            "QComboBox::down-arrow { image: none; width: 0; height: 0; }"
            f"QComboBox QAbstractItemView {{ background: {sk.surface.name()}; color: {sk.text.name()};"
            f" border: 1px solid {sk.border.name(QColor.NameFormat.HexArgb)};"
            f" selection-background-color: {_rgba(accent(), 0.18).name(QColor.NameFormat.HexArgb)};"
            f" selection-color: {sk.text.name()}; outline: none; padding: 4px; }}")

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = QRectF(self.width() - 26, self.height() / 2 - 6, 12, 12)
        pen = QPen(skin().text2, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(QPointF(box.left() + 1, box.top() + 4))
        path.lineTo(box.center().x(), box.bottom() - 3)
        path.lineTo(box.right() - 1, box.top() + 4)
        p.drawPath(path)
        p.end()


DarkCombo = SkinCombo


class LevelBars(QWidget):
    """Little bars that rise with your voice, in the buddy's gel, to test the microphone."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(64, 26)
        self.level = 0.0
        self._t = 0.0

    def set_level(self, level: float) -> None:
        self.level += (max(0.0, min(1.0, level)) - self.level) * 0.5
        self._t += 0.05
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        n, gap = 9, 3.0
        w = (self.width() - gap * (n - 1)) / n
        mid = (n - 1) / 2
        live = self.level > 0.03
        for i in range(n):
            weight = 1 - abs(i - mid) / (mid + 1)
            h = 4 + (self.height() - 4) * self.level * weight * (0.75 + 0.25 * math.sin(self._t * 11 + i))
            bar = QRectF(i * (w + gap), (self.height() - h) / 2, w, h)
            if live:
                ui_kit.paint_gel(p, bar, accent())
            else:
                p.fillPath(_pill(bar), skin().track)
        p.end()


class Group(QWidget):
    """Rows on one card, with hairlines between them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.rows: list[QWidget] = []

    def add(self, row: QWidget) -> QWidget:
        self.rows.append(row)
        self._layout.addWidget(row)
        return row

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        ui_kit.paint_card(p, rect, sk, radius=18)
        visible = [r for r in self.rows if r.isVisible()]
        p.setPen(QPen(sk.hairline, 1.0))
        for row in visible[:-1]:
            y = row.geometry().bottom() + 0.5
            p.drawLine(QPointF(rect.left() + 18, y), QPointF(rect.right() - 18, y))
        p.end()


class Row(QWidget):
    """A setting: its name and a line about it on the left, controls on the right."""

    def __init__(self, title: str, sub: str = "", *controls: QWidget, gap: int = 14) -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 15, 18, 15)
        h.setSpacing(gap)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = _label(title, 14, 800, "text")
        text.addWidget(self.title)
        self.sub = _label(sub, 12.5, 600, "text2", wrap=True)
        self.sub.setVisible(bool(sub))
        text.addWidget(self.sub)
        h.addLayout(text, stretch=1)
        self.controls = h
        for control in controls:
            h.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_sub(self, text: str, role: str = "text2") -> None:
        self.sub.setText(text)
        self.sub.setProperty("ink", role)
        _ink(self.sub)
        self.sub.setVisible(bool(text))


class Hero(Motion, QWidget):
    """A section's header: its big tile (which bounces in), title and a line about it."""

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.title, self.line = HEROES.get(key, (key.title(), ""))
        self.setFixedHeight(70)
        self.spring("pop", 1.0, 420.0, 16.0)

    def pop(self) -> None:
        self.go("pop", 0.82, snap=True)
        self.go("pop", 1.0)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        sk = skin()
        tile = QRectF(4, 8, 50, 50)
        k = self.val("pop")
        p.save()
        c = tile.center()
        p.translate(c)
        p.scale(k, k)
        p.translate(-c)
        ui_kit.paint_tile(p, tile, self.key)
        p.restore()
        p.setFont(font(25, 800, -0.02))
        p.setPen(sk.text)
        p.drawText(QRectF(70, 6, self.width() - 70, 32), Qt.AlignmentFlag.AlignVCenter, self.title)
        p.setFont(font(13.5, 600))
        p.setPen(sk.text2)
        p.drawText(QRectF(70, 38, self.width() - 70, 22), Qt.AlignmentFlag.AlignVCenter, self.line)
        p.end()


class KeyRow(Row):
    """A key: whether it's saved (never the key), and Replace, which opens a
    masked box for a new one."""

    def __init__(self, title: str, on_key) -> None:
        super().__init__(title, "")
        self._button = PanelButton("Replace", "frost", None, px=13, weight=700, pad=(8, 14))
        self._entry = _edit(240, password=True, placeholder="Paste the key")
        self._entry.hide()
        self.controls.addWidget(self._entry, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.controls.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._on_key = on_key
        self._button.clicked.connect(self._open)
        self._entry.returnPressed.connect(self._commit)
        self._entry.editingFinished.connect(self._commit)

    def set_state(self, state: str, provider: str) -> None:
        if state == "set":
            self.set_sub("Saved in Windows Credential Manager, never shown")
            self._button.setText("Replace")
        else:
            self.set_sub(f"Needed for {provider}. Paste it here.", "accent")
            self._button.setText("Add key")
        self._button.resize(self._button.sizeHint())
        self._button.setFixedSize(self._button.sizeHint())

    def _open(self) -> None:
        self._button.hide()
        self._entry.clear()
        self._entry.show()
        self._entry.setFocus()

    def _commit(self) -> None:
        if not self._entry.isVisible():
            return
        text = self._entry.text().strip()
        self._entry.clear()
        self._entry.hide()
        self._button.show()
        if text:
            sound("success")
            self._on_key(text)


class Chip(QLabel):
    """A small rounded label: where something goes ("stays on this PC")."""

    def __init__(self, text: str, here: bool) -> None:
        super().__init__(text)
        self.here = here
        self.setFont(font(12, 800))
        self.setContentsMargins(12, 5, 12, 5)
        self.setProperty("ink", "chip")
        self.restyle()

    def restyle(self) -> None:
        sk = skin()
        colour = accent() if self.here else sk.text3
        fg = _mix(colour, sk.text, 0.35) if self.here else sk.text2
        bg = _rgba(colour, 0.14 if not sk.dark else 0.22)
        self.setStyleSheet(f"QLabel {{ color: {fg.name()}; background: {bg.name(QColor.NameFormat.HexArgb)};"
                           " border-radius: 12px; }")


# --- The Settings view ------------------------------------------------------------------------------

def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


class SettingsPage(Motion, QWidget):
    """Settings inside the panel. ``load()`` reads everything fresh (a hidden
    SettingsDialog); Done saves it (``saved(restart_needed)``); back leaves
    without saving (``left``)."""

    saved = pyqtSignal(bool)
    left = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.dlg = None
        self.section = "general"
        self.back = PanelButton("", "frost", self, icon="back", round_=38, icon_px=16, icon_stroke=2.4)
        self.back.setAccessibleName("Back")
        self.back.setToolTip("Back, without saving")
        self.done = PanelButton("Done", "glossy", self, px=14, weight=800, pad=(9, 22))
        self.hint = _label("", 12.5, 800, "accent")
        self.hint.setParent(self)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.rail = ui_kit.NavRail(list(SECTIONS), self, width=NAV_W)
        self.rail.move(NAV_X, NAV_Y)
        self.rail.picked.connect(lambda key: self.show_section(key, animate=True))
        self.nav = {key: self.rail for key, _label in SECTIONS}
        self.pages: dict[str, QWidget] = {}
        self._heroes: dict[str, Hero] = {}
        self._own: dict[str, str] = {}
        """The panel's own settings as they were when Settings opened (Back puts them back)."""
        self.back.clicked.connect(self.leave)
        self.done.clicked.connect(self.finish)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._live)
        self.spring("slide", 0.0, 380.0, 30.0)
        ui_kit.on_skin_changed(self._reskin)
        self.resize(W, H)

    # --- Loading, saving, leaving ----------------------------------------------------------------

    def load(self) -> None:
        """Read every setting fresh and build the sections."""
        from settings_dialog import SettingsDialog
        self.discard()
        self.dlg = SettingsDialog()
        fx = _sfx()
        from config import resolve_setting
        self._own = {"APPEARANCE": ui_kit.appearance(),
                     "SOUND_EFFECTS": "on" if (fx.enabled() if fx else True) else "off",
                     "SOUND_VOLUME": str(round(100 * (fx.volume() if fx else 0.7))),
                     "DO_IT_FOR_ME": "off" if resolve_setting("DO_IT_FOR_ME", "on").lower() == "off" else "on"}
        for page in self.pages.values():
            page.hide()
            page.deleteLater()
        self._heroes = {}
        self.pages = {}
        for key, _label in SECTIONS:
            scroll = QScrollArea(self)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # the wheel still scrolls
            scroll.setStyleSheet(_scroll_style())
            scroll.viewport().setAutoFillBackground(False)
            body = getattr(self, f"_build_{key}")()
            body.setFixedWidth(PAGE_W)
            scroll.setWidget(body)
            self.pages[key] = scroll
        self.resize(W, H)
        for page in self.pages.values():
            page.setGeometry(CONTENT_X, CONTENT_Y, CONTENT_W, H - CONTENT_Y - 12)
        self.hint.setText("")
        self._layout_top()
        self.show_section(self.section)
        self._tick.start(50)

    def discard(self) -> None:
        """Forget unsaved changes (puts back a buddy, a look or sounds previewed here)."""
        self._tick.stop()
        if self.dlg is not None:
            try:
                self.dlg.cancel()
            except Exception:
                pass
            self.dlg = None
            self._apply_own(self._own)

    @staticmethod
    def _apply_own(own: dict) -> None:
        if not own:
            return
        ui_kit.set_appearance(own.get("APPEARANCE", "light"))
        fx = _sfx()
        if fx is not None:
            try:
                fx.set_enabled(own.get("SOUND_EFFECTS", "on") == "on")
                fx.set_volume(int(own.get("SOUND_VOLUME", "70")) / 100.0)
            except Exception:
                pass

    def leave(self) -> None:
        self.discard()
        self.left.emit()

    def finish(self) -> None:
        """Done: save everything, as the dialog's Save does, plus the panel's own settings."""
        if self.dlg is None:
            self.left.emit()
            return
        blocker = self.dlg.save_blocker()
        if blocker:
            self.hint.setText(blocker)
            self._layout_top()
            sound("error")
            self.show_section("model" if "Claude" in blocker or self.dlg.key_state("LLM") == "missing" else "voice",
                              animate=True)
            return
        if not self.dlg.save():
            sound("error")
            return
        self._store_own()
        restart = self.dlg.needs_restart()
        self._tick.stop()
        self.dlg = None
        sound("success")
        self.saved.emit(restart)

    def _store_own(self) -> None:
        import keyring
        from config import KEYRING_SERVICE
        fx = _sfx()
        now = {"APPEARANCE": ui_kit.appearance(),
               "SOUND_EFFECTS": "on" if (fx.enabled() if fx else True) else "off",
               "SOUND_VOLUME": str(round(100 * (fx.volume() if fx else 0.7))),
               "DO_IT_FOR_ME": "on" if getattr(self, "_drive_switch", None) is None
               or self._drive_switch.isChecked() else "off"}
        for name, value in now.items():
            if value != self._own.get(name):
                try:
                    keyring.set_password(KEYRING_SERVICE, name, value)
                except Exception:
                    pass
        self._own = now

    @property
    def busy(self) -> bool:
        """Capturing a new shortcut (Esc belongs to it then)."""
        caps = getattr(self, "_caps", None)
        return caps is not None and caps.recording

    def show_section(self, key: str, *, animate: bool = False) -> None:
        if key not in self.pages and self.pages:
            key = "general"
        changed = key != self.section
        self.section = key
        for k, page in self.pages.items():
            page.setVisible(k == key)
        self.rail.select(key, animate=animate)
        if animate and changed:
            self.go("slide", 26.0, snap=True)      # the new section slides in from the right
            self.go("slide", 0.0)
            hero = self._heroes.get(key)
            if hero is not None:
                hero.pop()
        else:
            self.go("slide", 0.0, snap=True)
        self._place_pages()

    def _place_pages(self) -> None:
        dx = round(self.val("slide"))
        for page in self.pages.values():
            if page.isVisible():
                page.move(CONTENT_X + dx, CONTENT_Y)

    def _motion_step(self, dt: float) -> bool:
        moving = super()._motion_step(dt)
        self._place_pages()
        return moving

    def _layout_top(self) -> None:
        self.back.setGeometry(22, 15, 38, 38)
        self.done.move(self.width() - 22 - self.done.width(), 16 + (36 - self.done.height()) // 2)
        hint_w = 360
        self.hint.setGeometry(self.done.x() - 14 - hint_w, 16, hint_w, 36)

    def resizeEvent(self, _event) -> None:  # noqa: N802
        self._layout_top()

    def _reskin(self) -> None:
        try:
            restyle(self)
            for chip in self.findChildren(Chip):
                chip.restyle()
        except RuntimeError:
            pass

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(font(20, 800, -0.01))
        p.setPen(skin().text)
        p.drawText(QRectF(22 + 38 + 14, 15, 220, 38), Qt.AlignmentFlag.AlignVCenter, "Settings")
        p.end()

    def _live(self) -> None:
        """What changes by itself: the microphone line and level, the buddies breathing."""
        if self.dlg is None:
            return
        if self.section == "general" and hasattr(self, "_mic_row"):
            text = self.dlg._mic_row.sub.text()
            if self._mic_row.sub.text() != text:
                self._mic_row.set_sub(text)
            meter = getattr(self.dlg, "_mic_meter", None)
            self._level.set_level(getattr(meter, "_level", 0.0) if meter is not None else 0.0)
        if self.section == "buddy":
            for card in getattr(self, "_buddy_cards", {}).values():
                card._t += 0.05
                card.update()

    # --- Sections ----------------------------------------------------------------------------------

    def _section(self, key: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 6, 6, 18)
        v.setSpacing(16)
        hero = Hero(key)
        self._heroes[key] = hero
        v.addWidget(hero)
        return page, v

    def _build_general(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section("general")
        group = Group()
        side = Segment([("top", "Top"), ("left", "Left"), ("right", "Right")], dlg._side.value() or "top")
        side.changed.connect(dlg._side.set_value)
        group.add(Row("Where Clixy sits", "The top of your screen like a notch, or a side edge", side))
        glow = Switch(dlg._glow.isChecked())
        glow.toggled.connect(dlg._glow.setChecked)
        group.add(Row("Edge glow", "Your buddy's colours flow around the screen while you talk", glow))
        mics = SkinCombo()
        for i in range(dlg._mic_box.count()):
            mics.addItem(dlg._mic_box.itemText(i), dlg._mic_box.itemData(i))
        mics.setCurrentIndex(dlg._mic_box.currentIndex())
        mics.setMaximumWidth(230)
        mics.currentIndexChanged.connect(dlg._mic_box.setCurrentIndex)
        self._level = LevelBars()
        self._mic_row = group.add(Row("Microphone", dlg._mic_row.sub.text(), self._level, mics, gap=12))
        self._drive_switch = Switch(self._own.get("DO_IT_FOR_ME", "on") == "on")
        group.add(Row("Do it for me", "Say “for me” and Clixy clicks and types the steps itself. It always asks "
                      "before anything that sends, buys or deletes, and never types passwords", self._drive_switch))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _build_look(self) -> QWidget:
        page, v = self._section("look")
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        self._look_cards: dict[str, ChoiceCard] = {}
        lines = {"light": "Bright and airy", "dark": "Deep ink, easy at night", "system": "Follows Windows"}
        for i, (value, name) in enumerate(ui_kit.APPEARANCES):
            card = ChoiceCard(name, lines[value], preview=value)
            card.setChecked(value == ui_kit.appearance())
            card.clicked.connect(lambda _c=False, val=value: self._pick_look(val))
            grid.addWidget(card, 0, i)
            self._look_cards[value] = card
        v.addLayout(grid)
        group = Group()
        buddy = PanelButton("Pick a buddy", "frost", None, px=13, weight=700, pad=(8, 14))
        buddy.setFixedSize(buddy.sizeHint())
        buddy.clicked.connect(lambda: self.show_section("buddy", animate=True))
        group.add(Row("Accent colour", "Clixy wears your buddy's colour: on buttons, switches and the notch's light",
                      buddy))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _pick_look(self, value: str) -> None:
        for val, card in self._look_cards.items():
            card.setChecked(val == value)
        ui_kit.set_appearance(value)          # previews it everywhere; Done keeps it

    def _build_sounds(self) -> QWidget:
        page, v = self._section("sounds")
        fx = _sfx()
        group = Group()
        on = fx.enabled() if fx is not None else False
        self._fx_switch = Switch(on)
        self._fx_switch.toggled.connect(self._set_sounds)
        group.add(Row("Sound effects", "Pops, clicks and chimes as you use Clixy", self._fx_switch))
        self._volume = ui_kit.Slider(round(100 * (fx.volume() if fx is not None else 0.7)), width=210)
        self._volume.changed.connect(lambda val: fx is not None and fx.set_volume(val / 100.0))
        self._volume.released.connect(lambda _v: fx is not None and fx.preview("select"))
        self._volume.setEnabled(on)
        group.add(Row("Volume", "How loud they are, under Clixy's voice", self._volume))
        play = PanelButton("Play some", "frost", None, icon="play", px=13, weight=700, pad=(8, 14), icon_px=13)
        play.setFixedSize(play.sizeHint())
        play.clicked.connect(self._play_samples)
        group.add(Row("Hear them", "A few, one after another", play))
        if fx is None:
            group.add(Row("Sounds aren't available", "This copy of Clixy has no sound effects"))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _set_sounds(self, on: bool) -> None:
        fx = _sfx()
        if fx is not None:
            fx.set_enabled(on)
        self._volume.setEnabled(on)
        self._volume.update()

    def _play_samples(self) -> None:
        fx = _sfx()
        if fx is None:
            return
        for i, name in enumerate(("press", "toggle_on", "select", "open", "note", "build", "done")):
            QTimer.singleShot(120 + i * 420, lambda n=name: fx.preview(n))

    def _build_voice(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section("voice")
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        tts = dlg.provider_id("TTS")
        cards = {
            "cartesia": ChoiceCard("Cartesia", "Most human", "Cloud · your Cartesia key", tile="cartesia"),
            "voicestudio": ChoiceCard("VoiceStudio", "Your own voice", "Coming soon", disabled_note="Coming soon",
                                      tile="voicestudio"),
            "kokoro": ChoiceCard("Kokoro", "Offline", "On this PC · no key", tile="kokoro"),
        }
        for i, (pid, card) in enumerate(cards.items()):
            card.setChecked(pid == tts)
            grid.addWidget(card, 0, i)
            if pid != "voicestudio":
                card.clicked.connect(lambda _c=False, pid=pid: self._pick_voice(pid))
        self._voice_cards = cards
        v.addLayout(grid)
        self._other_voice = _label("", 12.5, 600, "text2", wrap=True)
        v.addWidget(self._other_voice)
        group = Group()
        self._listen = Switch(dlg.provider_id("STT") != "faster-whisper")
        self._listen.toggled.connect(self._pick_listening)
        cloud = _label("Cloud listening", 12.5, 700, "text2")
        self._listen_row = group.add(Row("Listening", "", cloud, self._listen, gap=12))
        self._voice_key = group.add(KeyRow("Cartesia key", self._set_voice_key))
        v.addWidget(group)
        v.addStretch(1)
        self._sync_voice()
        return page

    def _pick_voice(self, provider_id: str) -> None:
        self.dlg.set_provider("TTS", provider_id)
        self._sync_voice()

    def _pick_listening(self, cloud: bool) -> None:
        self.dlg.set_provider("STT", "cartesia" if cloud else "faster-whisper")
        self._sync_voice()

    def _set_voice_key(self, text: str) -> None:
        category = "TTS" if self.dlg.provider_id("TTS") == "cartesia" else "STT"
        self.dlg.set_key(category, text)
        self._sync_voice()

    def _sync_voice(self) -> None:
        dlg = self.dlg
        tts, stt = dlg.provider_id("TTS"), dlg.provider_id("STT")
        for pid, card in self._voice_cards.items():
            card.setChecked(pid == tts)
            card.update()
        names = {"elevenlabs": "ElevenLabs"}
        self._other_voice.setText(f"Speaking with {names.get(tts, tts)} now. Pick another above to switch."
                                  if tts not in self._voice_cards else "")
        self._other_voice.setVisible(tts not in self._voice_cards)
        where = {"faster-whisper": "On this PC · what you say stays here",
                 "cartesia": "Cartesia, in the cloud · uses your Cartesia key",
                 "assemblyai": "AssemblyAI, in the cloud"}
        self._listen.blockSignals(True)
        self._listen.setChecked(stt != "faster-whisper")
        self._listen.blockSignals(False)
        self._listen.go("on", 1.0 if stt != "faster-whisper" else 0.0)
        self._listen_row.set_sub(where.get(stt, stt))
        uses = "cartesia" in (tts, stt)
        self._voice_key.setVisible(uses)
        if uses:
            category = "TTS" if tts == "cartesia" else "STT"
            self._voice_key.set_state(dlg.key_state(category), "Cartesia")

    def _build_buddy(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section("buddy")
        grid = QGridLayout()
        grid.setHorizontalSpacing(2)
        self._buddy_cards: dict[str, ChoiceCard] = {}
        for i, av in enumerate(theme.AVATARS):
            card = ChoiceCard(av.name, parent=None, avatar_id=av.id)
            card.setChecked(av.id == theme.current_avatar_id())
            card.clicked.connect(lambda _c=False, a=av.id: self._pick_buddy(a))
            grid.addWidget(card, 0, i)
            self._buddy_cards[av.id] = card
        v.addLayout(grid)
        group = Group()
        draw = Switch(dlg._draw_checkbox.isChecked())
        draw.toggled.connect(dlg._draw_checkbox.setChecked)
        group.add(Row("Draw on screen", "Your buddy marks what to click. Off, it still draws when you ask", draw))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _pick_buddy(self, avatar_id: str) -> None:
        self.dlg._on_avatar_picked(avatar_id)      # previews it everywhere; Done keeps it
        for aid, card in self._buddy_cards.items():
            card.setChecked(aid == avatar_id)
        self._reskin()
        self.window().update()

    def _build_shortcut(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section("shortcut")
        stage = _Stage()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(24, 26, 24, 22)
        sv.setSpacing(14)
        self._caps = ShortcutCaps(dlg._hotkey)
        sv.addWidget(self._caps, alignment=Qt.AlignmentFlag.AlignHCenter)
        sv.addWidget(_label("Hold, ask out loud, let go", 13.5, 700, "text2"), alignment=Qt.AlignmentFlag.AlignHCenter)
        change = PanelButton("Change shortcut", "glossy", None, px=13, weight=800, pad=(8, 16))
        change.setFixedSize(change.sizeHint())
        change.clicked.connect(self._caps.record)
        sv.addWidget(change, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._caps.recorded.connect(self._hotkey_recorded)
        v.addWidget(stage)
        group = Group()
        group.add(Row("Type instead", "Click the notch: the panel opens with the cursor in the box"))
        self._hold_row = group.add(Row("To change it", "Press Change, then hold your new keys together. Esc keeps "
                                                       "the old ones"))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _hotkey_recorded(self, combo: str) -> None:
        self.dlg._on_hotkey_recorded(combo)
        self.dlg._caps.set_combo(combo)

    def _build_model(self) -> QWidget:
        page, v = self._section("model")
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        cards = {
            "anthropic": ChoiceCard("Claude", "Anthropic", "Key or OpenRouter", tile="claude"),
            "openai": ChoiceCard("OpenAI", "GPT", "OpenAI key", tile="openai"),
            "gemini": ChoiceCard("Gemini", "Google", "Key or OpenRouter", tile="gemini"),
            "ollama": ChoiceCard("Ollama", "On this PC", "Free · no key", tile="ollama"),
        }
        for i, (pid, card) in enumerate(cards.items()):
            grid.addWidget(card, 0, i)
            card.clicked.connect(lambda _c=False, pid=pid: self._pick_ai(pid))
        self._ai_cards = cards
        v.addLayout(grid)
        group = Group()
        self._ai_row = group.add(Row("Model", ""))
        self._ai_key = group.add(KeyRow("Key", self._set_ai_key))
        self._ollama_host = _edit(240)
        self._ollama_host.editingFinished.connect(lambda: self.dlg.set_key("LLM", self._ollama_host.text()))
        self._ollama_row = group.add(Row("Ollama address", "Leave it unless you moved Ollama", self._ollama_host))
        self._ollama_model = _edit(240)
        self._ollama_model.editingFinished.connect(self._set_ollama_model)
        self._ollama_model_row = group.add(Row("Local model", "A vision model you've pulled in Ollama",
                                               self._ollama_model))
        v.addWidget(group)
        v.addStretch(1)
        self._sync_ai()
        return page

    def _pick_ai(self, provider_id: str) -> None:
        self.dlg.set_provider("LLM", provider_id)
        self._sync_ai()

    def _set_ai_key(self, text: str) -> None:
        self.dlg.set_key("LLM", text)      # a key for another AI moves the choice there, as in the dialog
        self._sync_ai()

    def _set_ollama_model(self) -> None:
        combo = self.dlg._model_combos.get("LLM")
        if combo is not None and self._ollama_model.text().strip():
            combo.setCurrentText(self._ollama_model.text().strip())

    def _sync_ai(self) -> None:
        dlg = self.dlg
        pid = dlg.provider_id("LLM")
        for p, card in self._ai_cards.items():
            card.setChecked(p == pid)
            card.update()
        model, _key, cost, local = dlg._connection()
        self._ai_row.set_sub(" · ".join(x for x in (_plain(model), cost) if x))
        ollama = pid == "ollama"
        self._ai_key.setVisible(not ollama)
        self._ollama_row.setVisible(ollama)
        self._ollama_model_row.setVisible(ollama)
        names = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Gemini"}
        if not ollama:
            self._ai_key.title.setText(f"{names.get(pid, pid)} key")
            self._ai_key.set_state(dlg.key_state("LLM"), names.get(pid, pid))
        else:
            self._ollama_host.setText(dlg._key_inputs["LLM"].text())
            combo = dlg._model_combos.get("LLM")
            self._ollama_model.setText(combo.currentText() if combo is not None else "")

    def _build_privacy(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section("privacy")
        dlg._refresh_where()
        where = Group()
        for key, title in (("key", "Your keys"), ("voice", "What you say"), ("ai", "Screenshot + question")):
            chip = dlg._where[key]
            where.add(Row(title, "", Chip(chip.text(), chip.objectName() == "whereHere")))
        v.addWidget(where)
        group = Group()
        keep = Switch(dlg._keep_history.isChecked())
        keep.toggled.connect(dlg._keep_history.setChecked)
        group.add(Row("Keep history", "Short notes about what you asked in each app, so answers get better", keep))
        forget = PanelButton("Forget…", "frost", None, px=13, weight=700, pad=(8, 14))
        forget.setFixedSize(forget.sizeHint())
        forget.clicked.connect(dlg._on_forget)
        group.add(Row("Forget history", "Deletes those notes and any logs. Keeps your keys and settings", forget))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _build_about(self) -> QWidget:
        from panel import _start, memory_dir, notes_dir
        page, v = self._section("about")
        stage = _AboutStage()
        v.addWidget(stage)
        group = Group()
        try:
            from version import VERSION
        except Exception:
            VERSION = ""
        group.add(Row("Version", "", Chip(f"Clixy {VERSION}" if VERSION else "Clixy", True)))
        for title, sub, folder in (("Your sketch notes", "Every note Clixy has drawn for you", notes_dir),
                                   ("What Clixy remembers", "The short notes it keeps to answer better", memory_dir)):
            button = PanelButton("Open", "frost", None, icon="folder", px=13, weight=700, pad=(8, 14), icon_px=14)
            button.setFixedSize(button.sizeHint())
            button.clicked.connect(lambda _c=False, f=folder: _open_folder(f(), _start))
            group.add(Row(title, sub, button))
        v.addWidget(group)
        v.addStretch(1)
        return page


def _open_folder(folder, start) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    start(folder)


class _Stage(QWidget):
    """A plain card that holds a showpiece (the shortcut's big keycaps)."""

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = ui_kit.paint_card(p, rect, sk, radius=20)
        p.save()
        p.setClipPath(shape)
        p.fillPath(shape, ui_kit.dot_brush(sk.dots, self.devicePixelRatioF(), rect.topLeft()))
        glow = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        glow.setColorAt(0.0, _rgba(accent(), 0.08))
        glow.setColorAt(1.0, _rgba(accent(), 0.0))
        p.fillRect(rect, QBrush(glow))
        p.restore()
        p.end()


class _AboutStage(Motion, QWidget):
    """The About card: your buddy, big, bobbing; "Clixy" and a line under it.
    Point at the buddy and it hops."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(158)
        self.setMouseTracking(True)
        self._on = False
        self._always_moving = True        # the buddy breathes the whole time it's shown
        self.spring("hop", 0.0, 260.0, 9.0)
        self._t0 = time.monotonic()

    def showEvent(self, event) -> None:  # noqa: N802
        self.go("hop", 0.0, kick=-0.01)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._always_moving = False
        super().hideEvent(event)

    def _buddy_rect(self) -> QRectF:
        return QRectF(self.width() / 2 - 40, 10, 80, 84)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        on = self._buddy_rect().contains(event.position())
        if on and not self._on:
            self.go("hop", 0.0, kick=-120.0)
            sound("buddy")
        self._on = on

    def paintEvent(self, _event) -> None:  # noqa: N802
        self._always_moving = self.isVisible()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sk = skin()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = ui_kit.paint_card(p, rect, sk, radius=20)
        p.save()
        p.setClipPath(shape)
        p.fillPath(shape, ui_kit.dot_brush(sk.dots, self.devicePixelRatioF(), rect.topLeft()))
        glow = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        glow.setColorAt(0.0, _rgba(accent(), 0.12))
        glow.setColorAt(1.0, _rgba(accent(), 0.0))
        p.fillRect(rect, QBrush(glow))
        p.restore()
        dpr = self.devicePixelRatioF()
        t = time.monotonic() - self._t0
        box = self._buddy_rect().translated(0, self.val("hop"))
        theme.paint_avatar_in(p, box, t=t, scale=max(1, int(3 * dpr)) / (2 * dpr), device_pixel_ratio=dpr,
                              mood="happy" if self.val("hop") < -2 else "idle")
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(font(22, 800, -0.02))
        p.setPen(sk.text)
        p.drawText(QRectF(0, 98, self.width(), 30), Qt.AlignmentFlag.AlignCenter, "Clixy")
        p.setFont(font(13, 600))
        p.setPen(sk.text2)
        p.drawText(QRectF(0, 126, self.width(), 22), Qt.AlignmentFlag.AlignCenter,
                   f"{theme.avatar(theme.current_avatar_id()).name} is keeping you company")
        p.end()
