"""Settings, inside the Clixy panel.

The gear in the panel turns it into Settings: a back button, "Settings" and a
glossy Done on top; the sections on the left (General, Voice, Buddy,
Shortcut, Model, Privacy) and the chosen one on the right.

Every setting is read, checked and saved by settings_dialog.SettingsDialog,
kept hidden behind the panel: the panel's controls set the dialog's own
controls, and Done runs its Save (keyring, provider ids, shared keys, a
pasted key moving to its provider, Save held back while a key is missing).
So the storage lives in one place, and the old dialog (the tray's
Settings...) keeps working as it was.

Only what has a real setting behind it is here. A key is never shown: only
whether one is saved, and Replace takes a new one in a masked box.

Threading: Qt main thread only.
"""
from __future__ import annotations

import re

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

import theme
from panel import (
    BG,
    CHIP,
    MUTED,
    ON_FROST,
    TEXT,
    PanelButton,
    _inset_top,
    _mix,
    _pill,
    _rgba,
    accent,
    font,
    keycap_size,
    mono,
    on_accent,
    paint_glossy,
    paint_icon,
    paint_keycap,
)

CARD_BG = QColor("#121215")
SECTIONS = (("general", "General"), ("voice", "Voice"), ("buddy", "Buddy"), ("shortcut", "Shortcut"),
            ("model", "Model"), ("privacy", "Privacy"))
CONTENT_W = 880 - 22 - 176 - 18 - 22      # 642


def _label(text: str, px: float, weight: int, color: QColor, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setFont(font(px, weight))
    label.setStyleSheet(f"QLabel {{ color: {color.name()}; background: transparent; }}")
    label.setWordWrap(wrap)
    return label


def _line_edit_style(radius: int = 12) -> str:
    return (f"QLineEdit {{ background: #1A1A1F; color: {TEXT.name()}; border: 1px solid rgba(255,255,255,0.08); "
            f"border-radius: {radius}px; padding: 7px 12px; min-height: 0; "
            f"selection-background-color: {_rgba(accent(), 0.45).name(QColor.NameFormat.HexArgb)}; "
            f"selection-color: #FFFFFF; }}"
            f"QLineEdit:focus {{ border-color: {_rgba(accent(), 0.6).name(QColor.NameFormat.HexArgb)}; }}")


# --- Controls ---------------------------------------------------------------------------------------

class Switch(QAbstractButton):
    """The mockup's switch: glossy accent when on, an inset dark track when off."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(46, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            paint_glossy(p, rect, accent())
        else:
            p.fillPath(_pill(rect), QColor("#22222A"))
            g = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(0, 5))
            g.setColorAt(0.0, QColor(0, 0, 0, 150))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.save()
            p.setClipPath(_pill(rect))
            p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 5), QBrush(g))
            p.restore()
        knob = QRectF(rect.right() - 3 - 22 if self.isChecked() else rect.left() + 3, rect.top() + 3, 22, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawEllipse(knob.translated(0, 2))
        g = QLinearGradient(knob.topLeft(), knob.bottomLeft())
        g.setColorAt(0.0, QColor("#FFFFFF"))
        g.setColorAt(1.0, QColor("#EDEDF0") if self.isChecked() else QColor("#D9D9DE"))
        p.setBrush(QBrush(g))
        p.drawEllipse(knob)
        p.end()


class Segment(QWidget):
    """Mutually exclusive options in an inset track (Top / Left / Right)."""

    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.options = options
        self.value = value
        self._font = font(13, 700)
        self._bold = font(13, 800)
        fm = QFontMetricsF(self._bold)
        self._widths = [fm.horizontalAdvance(label) + 28 for _v, label in options]
        self.setFixedSize(round(sum(self._widths) + 6), 6 + 7 + round(fm.height()) + 7)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _rects(self) -> list[QRectF]:
        x, out = 3.0, []
        for w in self._widths:
            out.append(QRectF(x, 3, w, self.height() - 6))
            x += w
        return out

    def mousePressEvent(self, event) -> None:  # noqa: N802
        for (value, _label), rect in zip(self.options, self._rects()):
            if rect.contains(event.position()) and value != self.value:
                self.value = value
                self.update()
                self.changed.emit(value)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(_pill(track, 12), BG)
        p.setPen(QPen(QColor(255, 255, 255, 15), 1.0))
        p.drawPath(_pill(track, 12))
        for (value, label), rect in zip(self.options, self._rects()):
            on = value == self.value
            if on:
                g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                g.setColorAt(0.0, QColor("#33333B"))
                g.setColorAt(1.0, QColor("#25252B"))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 120))
                p.drawRoundedRect(rect.translated(0, 2), 9, 9)
                p.fillPath(_pill(rect, 9), QBrush(g))
                _inset_top(p, _pill(rect, 9), QColor(255, 255, 255, 36))
            p.setFont(self._bold if on else self._font)
            p.setPen(QColor("#FFFFFF") if on else MUTED)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class NavButton(QAbstractButton):
    """A section in the list on the left."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedSize(176, 40)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            g.setColorAt(0.0, QColor("#1F1F25"))
            g.setColorAt(1.0, QColor("#17171B"))
            p.fillPath(_pill(rect, 12), QBrush(g))
            _inset_top(p, _pill(rect, 12), QColor(255, 255, 255, 20))
            p.setPen(QPen(QColor(255, 255, 255, 13), 1.0))
            p.drawPath(_pill(rect, 12))
            acc = accent()
            dot = QPointF(rect.right() - 14 - 3.5, rect.center().y())
            glow = QColor(acc)
            for r, a in ((8, 0.12), (6, 0.22)):
                glow.setAlphaF(a)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(glow)
                p.drawEllipse(dot, r, r)
            p.setBrush(acc)
            p.drawEllipse(dot, 3.5, 3.5)
        elif self.underMouse():
            p.fillPath(_pill(rect, 12), QColor("#141418"))
        p.setFont(font(14, 800 if self.isChecked() else 600))
        p.setPen(QColor("#FFFFFF") if self.isChecked() else (TEXT if self.underMouse() else MUTED))
        p.drawText(rect.adjusted(14, 0, -30, 0), Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()


class ChoiceCard(QAbstractButton):
    """A card to pick (a voice engine, an AI, a buddy): a ring and a check when chosen."""

    def __init__(self, title: str, line2: str = "", line3: str = "", parent: QWidget | None = None, *,
                 avatar_id: str = "", disabled_note: str = "") -> None:
        super().__init__(parent)
        self.setText(title)
        self.line2, self.line3 = line2, line3
        self.avatar_id = avatar_id
        self.disabled_note = disabled_note
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor if not disabled_note else Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setEnabled(not disabled_note)
        self.setAccessibleName(title)
        self.setMinimumHeight(113 if avatar_id else 94)
        self._t = 0.0

    def ring(self) -> QColor:
        if self.avatar_id:
            return QColor(theme.avatar(self.avatar_id).palette.flood)
        return accent()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        shape = _pill(rect, 16)
        on = self.isChecked()
        if on:
            g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            g.setColorAt(0.0, QColor("#1F1F25"))
            g.setColorAt(1.0, QColor("#151518"))
            p.fillPath(shape, QBrush(g))
            _inset_top(p, shape, QColor(255, 255, 255, 20))
            p.setPen(QPen(self.ring(), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(_pill(rect.adjusted(-1, -1, 1, 1), 17))
        else:
            p.fillPath(shape, CARD_BG if not self.underMouse() or not self.isEnabled() else QColor("#17171B"))
            p.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(_pill(rect.adjusted(-0.5, -0.5, 0.5, 0.5), 16.5))
        if not self.isEnabled():
            p.setOpacity(0.55)
        if self.avatar_id:
            self._paint_buddy(p, rect, on)
        else:
            self._paint_text(p, rect, on)
        p.end()

    def _paint_text(self, p: QPainter, rect: QRectF, on: bool) -> None:
        x, w = rect.left() + 16, rect.width() - 32
        y = rect.top() + 14
        f = font(15, 800)
        p.setFont(f)
        p.setPen(TEXT)
        fm = QFontMetricsF(f)
        p.drawText(QRectF(x, y, w - 24, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, w - 24))
        if on:
            badge = QRectF(rect.right() - 16 - 18, y + (fm.height() - 18) / 2, 18, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent())
            p.drawEllipse(badge)
            paint_icon(p, "check", badge.adjusted(4, 4, -4, -4), on_accent(accent()), 3.5)
        y += fm.height() + 6
        for text, px, weight, colour in ((self.line2, 13, 700, ON_FROST), (self.line3, 12, 500, MUTED)):
            if not text:
                continue
            f = font(px, weight)
            fm = QFontMetricsF(f)
            p.setFont(f)
            p.setPen(colour)
            p.drawText(QRectF(x, y, w, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, w))
            y += fm.height() + 6

    def _paint_buddy(self, p: QPainter, rect: QRectF, on: bool) -> None:
        dpr = self.devicePixelRatioF()
        av = theme.avatar(self.avatar_id)
        min_x, min_y, max_x, max_y = av.extent
        cells_w, cells_h = max_x - min_x + 1, max_y - min_y + 1
        device = max(1, int(min(40 / cells_w, 44 / cells_h) * dpr))
        box = QRectF(rect.center().x() - 22, rect.top() + 12, 44, 46)
        theme.paint_avatar_in(p, box, t=self._t, avatar_id=self.avatar_id, scale=device / (2 * dpr),
                              device_pixel_ratio=dpr, mood="happy" if on else "idle")
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = font(14, 800 if on else 700)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(TEXT if on else QColor("#D4D4D8"))
        name_y = box.bottom() + 6
        p.drawText(QRectF(rect.left(), name_y, rect.width(), fm.height()), Qt.AlignmentFlag.AlignCenter, self.text())
        dot = QPointF(rect.center().x(), name_y + fm.height() + 8 + 5)
        colour = self.ring()
        p.setPen(Qt.PenStyle.NoPen)
        if on:
            glow = QColor(colour)
            for r, a in ((10, 0.12), (7.5, 0.25)):
                glow.setAlphaF(a)
                p.setBrush(glow)
                p.drawEllipse(dot, r, r)
        p.setBrush(colour)
        p.drawEllipse(dot, 5, 5)


class DarkKeyCaps(QWidget):
    """The shortcut as dark keycaps; ``record()`` captures a new one. The
    capture is widgets.KeyCaps's own (two keys at least, Esc keeps the old)."""

    recorded = pyqtSignal(str)

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
        self._fit()

    def set_combo(self, combo: str) -> None:
        self._caps.set_combo(combo)
        self._fit()

    def _done(self, combo: str) -> None:
        self._fit()
        self.recorded.emit(combo)

    def _labels(self) -> list[str]:
        labels = self._caps._labels()
        return labels

    def _fit(self) -> None:
        widths = [keycap_size(label, 13, (11, 5, 6)).width() for label in self._labels()]
        plus = QFontMetricsF(font(14, 600)).horizontalAdvance("+") + 2 * 8
        self.setFixedSize(round(sum(widths) + plus * (len(widths) - 1)) + 2, keycap_size("W", 13, (11, 5, 6)).height() + 5)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = 1.0
        labels = self._labels()
        recording = self.recording
        plus_f = font(14, 600)
        plus = QFontMetricsF(plus_f).horizontalAdvance("+")
        for i, label in enumerate(labels):
            size = keycap_size(label, 13, (11, 5, 6))
            rect = QRectF(x, 1, size.width(), size.height())
            paint_keycap(p, rect, label, px=13, radius=8, edge=3)
            if recording:
                p.setPen(QPen(_rgba(accent(), 0.8), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 9, 9)
            x += size.width()
            if i < len(labels) - 1:
                p.setFont(plus_f)
                p.setPen(QColor("#8B8B92"))
                p.drawText(QRectF(x, 1, plus + 16, size.height()), Qt.AlignmentFlag.AlignCenter, "+")
                x += plus + 16
        p.end()


def _capture(combo: str, owner: "DarkKeyCaps"):
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


class DarkCombo(QComboBox):
    """A dark dropdown with a chevron (the microphone)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(font(13, 700))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QComboBox {{ background: #1A1A1F; color: {ON_FROST.name()}; border: 1px solid rgba(255,255,255,0.08);"
            f" border-radius: 12px; padding: 8px 34px 8px 14px; min-height: 0; }}"
            f"QComboBox:on, QComboBox:focus {{ border-color: rgba(255,255,255,0.18); }}"
            "QComboBox::drop-down { border: none; width: 30px; }"
            "QComboBox::down-arrow { image: none; width: 0; height: 0; }"
            f"QComboBox QAbstractItemView {{ background: #17171B; color: {ON_FROST.name()};"
            " border: 1px solid #2A2A31; selection-background-color: #2A2A31; selection-color: #FFFFFF;"
            " outline: none; padding: 4px; }")

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = QRectF(self.width() - 26, self.height() / 2 - 6, 12, 12)
        pen = QPen(ON_FROST, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(QPointF(box.left() + 1, box.top() + 4))
        path.lineTo(box.center().x(), box.bottom() - 3)
        path.lineTo(box.right() - 1, box.top() + 4)
        p.drawPath(path)
        p.end()


class Group(QWidget):
    """Rows on one dark card, with hairlines between them."""

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
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.fillPath(_pill(rect, 18), CARD_BG)
        p.setPen(QPen(QColor(255, 255, 255, 15), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_pill(rect.adjusted(-0.5, -0.5, 0.5, 0.5), 18.5))
        visible = [r for r in self.rows if r.isVisible()]
        for row in visible[:-1]:
            y = row.geometry().bottom() + 0.5
            p.drawLine(QPointF(rect.left() + 1, y), QPointF(rect.right() - 1, y))
        p.end()


class Row(QWidget):
    """A setting: its name and a line about it on the left, controls on the right."""

    def __init__(self, title: str, sub: str = "", *controls: QWidget, gap: int = 14) -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(gap)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = _label(title, 14, 700, TEXT)
        text.addWidget(self.title)
        self.sub = _label(sub, 12, 500, MUTED, wrap=True)
        self.sub.setVisible(bool(sub))
        text.addWidget(self.sub)
        h.addLayout(text, stretch=1)
        self.controls = h
        for control in controls:
            h.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_sub(self, text: str, color: QColor = MUTED) -> None:
        self.sub.setText(text)
        self.sub.setStyleSheet(f"QLabel {{ color: {color.name()}; background: transparent; }}")
        self.sub.setVisible(bool(text))


class Heading(QWidget):
    """A section's title (22 px, 800) and, beside it, a quiet line."""

    def __init__(self, text: str, aside: str = "") -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        title = QLabel(text)
        title.setFont(font(22, 800, -0.02))
        title.setStyleSheet(f"QLabel {{ color: {TEXT.name()}; background: transparent; }}")
        h.addWidget(title, alignment=Qt.AlignmentFlag.AlignBottom)
        if aside:
            note = _label(aside, 13, 500, MUTED)
            h.addWidget(note, alignment=Qt.AlignmentFlag.AlignBottom)
            note.setContentsMargins(0, 0, 0, 3)
        h.addStretch(1)


class KeyRow(Row):
    """A key: whether it's saved (never the key), and Replace, which opens a
    masked box for a new one."""

    def __init__(self, title: str, on_key) -> None:
        super().__init__(title, "")
        self._button = PanelButton("Replace", "frost", None, px=13, weight=700, pad=(8, 14))
        self._entry = QLineEdit()
        self._entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._entry.setFont(font(13, 500))
        self._entry.setFixedWidth(230)
        self._entry.setStyleSheet(_line_edit_style())
        self._entry.setPlaceholderText("Paste the key")
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
            self.set_sub(f"Needed for {provider}. Paste it here.", _mix(accent(), QColor(255, 255, 255), 0.35))
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
            self._on_key(text)


# --- The Settings view ------------------------------------------------------------------------------

def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


class SettingsPage(QWidget):
    """Settings inside the panel. ``load()`` reads everything fresh (a hidden
    SettingsDialog); Done saves it (``saved(restart_needed)``); back leaves
    without saving (``left``)."""

    saved = pyqtSignal(bool)
    left = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.dlg = None
        self.section = "general"
        self.back = PanelButton("", "frost", self, icon="back", round_=36, icon_px=16, icon_stroke=2.2)
        self.back.setAccessibleName("Back")
        self.back.setToolTip("Back, without saving")
        self.done = PanelButton("Done", "glossy", self, px=14, weight=800, pad=(8, 18))
        self.hint = _label("", 12, 700, _mix(accent(), QColor(255, 255, 255), 0.35))
        self.hint.setParent(self)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.nav: dict[str, NavButton] = {}
        for i, (key, label) in enumerate(SECTIONS):
            button = NavButton(label, self)
            button.move(22, 70 + i * 44)
            button.clicked.connect(lambda _c=False, k=key: self.show_section(k))
            self.nav[key] = button
        self.pages: dict[str, QWidget] = {}
        self.back.clicked.connect(self.leave)
        self.done.clicked.connect(self.finish)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._live)
        self.resize(880, 560)

    # --- Loading, saving, leaving ----------------------------------------------------------------

    def load(self) -> None:
        """Read every setting fresh and build the sections."""
        from settings_dialog import SettingsDialog
        self.discard()
        self.dlg = SettingsDialog()
        for page in self.pages.values():
            page.deleteLater()
        self.pages = {key: getattr(self, f"_build_{key}")() for key, _label in SECTIONS}
        tallest = 0
        for page in self.pages.values():
            page.setParent(self)
            page.setFixedWidth(CONTENT_W)
            page.adjustSize()
            tallest = max(tallest, page.sizeHint().height())
        nav_h = len(SECTIONS) * 44 - 4
        height = 16 + 36 + 18 + max(nav_h, tallest) + 22
        self.resize(880, min(720, max(440, height)))
        for page in self.pages.values():
            page.setGeometry(22 + 176 + 18, 70, CONTENT_W, self.height() - 70 - 22)
        self.hint.setText("")
        self._layout_top()
        self.show_section(self.section)
        self._tick.start(250)

    def discard(self) -> None:
        """Forget unsaved changes (puts back a buddy previewed here)."""
        self._tick.stop()
        if self.dlg is not None:
            try:
                self.dlg.cancel()
            except Exception:
                pass
            self.dlg = None

    def leave(self) -> None:
        self.discard()
        self.left.emit()

    def finish(self) -> None:
        """Done: save everything, as the dialog's Save does."""
        if self.dlg is None:
            self.left.emit()
            return
        blocker = self.dlg.save_blocker()
        if blocker:
            self.hint.setText(blocker)
            self._layout_top()
            self.show_section("model" if "Claude" in blocker or self.dlg.key_state("LLM") == "missing" else "voice")
            return
        if not self.dlg.save():
            return
        restart = self.dlg.needs_restart()
        self._tick.stop()
        self.dlg = None
        self.saved.emit(restart)

    @property
    def busy(self) -> bool:
        """Capturing a new shortcut (Esc belongs to it then)."""
        caps = getattr(self, "_caps", None)
        return caps is not None and caps.recording

    def show_section(self, key: str) -> None:
        self.section = key
        for k, page in self.pages.items():
            page.setVisible(k == key)
        if key in self.nav:
            self.nav[key].setChecked(True)

    def _layout_top(self) -> None:
        self.back.setGeometry(22, 16, 36, 36)
        self.done.move(self.width() - 22 - self.done.width(), 16 + (36 - self.done.height()) // 2)
        hint_w = 320
        self.hint.setGeometry(self.done.x() - 14 - hint_w, 16, hint_w, 36)

    def resizeEvent(self, _event) -> None:  # noqa: N802
        self._layout_top()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(font(17, 700))
        p.setPen(TEXT)
        p.drawText(QRectF(22 + 36 + 12, 16, 200, 36), Qt.AlignmentFlag.AlignVCenter, "Settings")
        p.end()

    def _live(self) -> None:
        """What changes by itself: the microphone line, the voice rows, the keys."""
        if self.dlg is None:
            return
        if hasattr(self, "_mic_row"):
            text = self.dlg._mic_row.sub.text()
            if self._mic_row.sub.text() != text:
                self._mic_row.set_sub(text)
        for card in getattr(self, "_buddy_cards", {}).values():
            card._t += 0.25
            card.update()

    # --- Sections ----------------------------------------------------------------------------------

    def _section(self, heading: QWidget) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        v.addWidget(heading)
        return page, v

    def _build_general(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section(Heading("General", "Where Clixy lives on your screen"))
        group = Group()
        side = Segment([("top", "Top"), ("left", "Left"), ("right", "Right")], dlg._side.value() or "top")
        side.changed.connect(dlg._side.set_value)
        group.add(Row("Where Clixy sits", "The top of the screen like a notch, or a side edge", side))
        glow = Switch(dlg._glow.isChecked())
        glow.toggled.connect(dlg._glow.setChecked)
        group.add(Row("Edge glow", "Your buddy's colours flow around the screen while you talk", glow))
        mics = DarkCombo()
        for i in range(dlg._mic_box.count()):
            mics.addItem(dlg._mic_box.itemText(i), dlg._mic_box.itemData(i))
        mics.setCurrentIndex(dlg._mic_box.currentIndex())
        mics.setMaximumWidth(240)
        mics.currentIndexChanged.connect(dlg._mic_box.setCurrentIndex)
        self._mic_row = group.add(Row("Microphone", dlg._mic_row.sub.text(), mics))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _build_voice(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section(Heading("How Clixy sounds"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        tts = dlg.provider_id("TTS")
        cards = {
            "cartesia": ChoiceCard("Cartesia", "Most human", "Cloud · uses your Cartesia key"),
            "voicestudio": ChoiceCard("VoiceStudio", "Your own voice", "Coming soon", disabled_note="Coming soon"),
            "kokoro": ChoiceCard("Kokoro", "Offline", "On this PC · no key needed"),
        }
        for i, (pid, card) in enumerate(cards.items()):
            card.setChecked(pid == tts)
            grid.addWidget(card, 0, i)
            if pid != "voicestudio":
                card.clicked.connect(lambda _c=False, pid=pid: self._pick_voice(pid))
        self._voice_cards = cards
        v.addLayout(grid)
        self._other_voice = _label("", 12, 500, MUTED, wrap=True)
        v.addWidget(self._other_voice)
        group = Group()
        self._listen = Switch(dlg.provider_id("STT") != "faster-whisper")
        self._listen.toggled.connect(self._pick_listening)
        cloud = _label("Cloud listening", 12, 600, MUTED)
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
        self._listen.update()
        self._listen_row.set_sub(where.get(stt, stt))
        uses = "cartesia" in (tts, stt)
        self._voice_key.setVisible(uses)
        if uses:
            category = "TTS" if tts == "cartesia" else "STT"
            self._voice_key.set_state(dlg.key_state(category), "Cartesia")

    def _build_buddy(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section(Heading("Your buddy", "Its colour lights the notch"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
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
        self.window().update()

    def _build_shortcut(self) -> QWidget:
        dlg = self.dlg
        page, v = self._section(Heading("Shortcut"))
        group = Group()
        self._caps = DarkKeyCaps(dlg._hotkey)
        change = PanelButton("Change", "frost", None, px=13, weight=700, pad=(8, 14))
        change.setFixedSize(change.sizeHint())
        change.clicked.connect(self._caps.record)
        self._caps.recorded.connect(self._hotkey_recorded)
        self._hold_row = group.add(Row("Hold to talk", "Hold, ask out loud, let go", self._caps, change, gap=12))
        group.add(Row("Type instead", "Click the notch: this panel opens with the cursor in the box"))
        v.addWidget(group)
        v.addStretch(1)
        return page

    def _hotkey_recorded(self, combo: str) -> None:
        self.dlg._on_hotkey_recorded(combo)
        self.dlg._caps.set_combo(combo)

    def _build_model(self) -> QWidget:
        page, v = self._section(Heading("The AI", "Who answers you"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        cards = {
            "anthropic": ChoiceCard("Claude", "Anthropic", "Key or OpenRouter"),
            "openai": ChoiceCard("OpenAI", "GPT", "OpenAI key"),
            "gemini": ChoiceCard("Gemini", "Google", "Key or OpenRouter"),
            "ollama": ChoiceCard("Ollama", "On this PC", "Free · no key"),
        }
        for i, (pid, card) in enumerate(cards.items()):
            grid.addWidget(card, 0, i)
            card.clicked.connect(lambda _c=False, pid=pid: self._pick_ai(pid))
        self._ai_cards = cards
        v.addLayout(grid)
        group = Group()
        self._ai_row = group.add(Row("Model", ""))
        self._ai_key = group.add(KeyRow("Key", self._set_ai_key))
        self._ollama_host = QLineEdit()
        self._ollama_host.setFont(font(13, 500))
        self._ollama_host.setFixedWidth(240)
        self._ollama_host.setStyleSheet(_line_edit_style())
        self._ollama_host.editingFinished.connect(lambda: self.dlg.set_key("LLM", self._ollama_host.text()))
        self._ollama_row = group.add(Row("Ollama address", "Leave it unless you moved Ollama", self._ollama_host))
        self._ollama_model = QLineEdit()
        self._ollama_model.setFont(font(13, 500))
        self._ollama_model.setFixedWidth(240)
        self._ollama_model.setStyleSheet(_line_edit_style())
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
        page, v = self._section(Heading("Privacy", "Where each thing goes"))
        dlg._refresh_where()
        where = Group()
        for key, title in (("key", "Your keys"), ("voice", "What you say"), ("ai", "Screenshot + question")):
            chip = dlg._where[key]
            here = chip.objectName() == "whereHere"
            label = QLabel(chip.text())
            label.setFont(mono(12))
            colour = accent() if here else MUTED
            label.setStyleSheet(f"QLabel {{ color: {colour.name()}; background: {CHIP.name()}; "
                                "border-radius: 11px; padding: 4px 11px; }")
            where.add(Row(title, "", label))
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
