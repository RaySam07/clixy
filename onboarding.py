"""First launch: three steps, about a minute.

1. Paste your Claude key. A paste zone instead of a text box: it checks the
   key's shape and turns butter when it looks right.
2. Say hi to your buddies. Pick who points for you.
3. Hold to talk. The shortcut as big keycaps; press Change and hold any
   keys you like.

On the left an ink panel carries the step number in big butter type and
your buddy, alive. Finishing saves the key to Windows Credential Manager
and picks Claude, voice on this PC, the buddy and the shortcut.

Threading: Qt main thread only (modal).
"""
from __future__ import annotations

import time

import keyring

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QPainter
from PyQt6.QtWidgets import (
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import theme
from config import KEYRING_SERVICE
from widgets import KeyCaps, PasteZone, buddy_grid

_STEPS = ("Connect Claude", "Meet your buddy", "Your shortcut")
_MOODS = ("listening", "happy", "idle")


def needs_onboarding() -> bool:
    """True when no AI is set up yet: no saved key for any cloud AI and
    Ollama not chosen. A setup missing some other key goes to Settings."""
    from config import resolve_api_key, resolve_setting
    if resolve_setting("LLM_PROVIDER", default="") == "ollama":
        return False
    return not any(resolve_api_key(slot) for slot in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"))


class _ArtPanel(QWidget):
    """The ink side: mark and wordmark, the step number, what the step is,
    progress dots, and the buddy at the bottom."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(330)
        self.step = 0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(100)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), theme.INK)
        x = 40.0
        theme.paint_avatar_in(p, QRectF(x - 2, 38, 30, 32), t=time.monotonic() - self._t0, mood="happy",
                              avatar_id=theme.LOGO_BUDDY, scale=30 / (18 * theme.CELL),
                              device_pixel_ratio=self.devicePixelRatioF())
        p.setPen(theme.NOTCH_TEXT)
        p.setFont(theme.display_font(17))
        p.drawText(QRectF(x + 40, 36, 200, 36), Qt.AlignmentFlag.AlignVCenter, "clixy")

        p.setPen(theme.BUTTER)
        p.setFont(theme.ui_font(96, QFont.Weight.ExtraLight))
        p.drawText(QRectF(x - 6, 92, 240, 150), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, str(self.step + 1))
        p.setPen(theme.NOTCH_TEXT)
        p.setFont(theme.display_font(16))
        p.drawText(QRectF(x, 240, 260, 30), Qt.AlignmentFlag.AlignLeft, _STEPS[self.step])
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(len(_STEPS)):
            p.setBrush(theme.BUTTER if i == self.step else theme.NOTCH_CHIP_DIM)
            width = 22.0 if i == self.step else 8.0
            dx = sum(30.0 if j == self.step else 16.0 for j in range(i))
            p.drawRoundedRect(QRectF(x + dx, 286, width, 8), 4, 4)

        # The buddy, big and glowing, at the bottom of the panel.
        area = QRectF(0, self.height() - 210, self.width(), 170)
        theme.paint_avatar_in(p, area, t=time.monotonic() - self._t0, mood=_MOODS[self.step],
                              scale=4.0, device_pixel_ratio=self.devicePixelRatioF(), glow=True)
        p.setPen(theme.NOTCH_MUTED)
        p.setFont(theme.ui_font(9.5))
        p.drawText(QRectF(0, self.height() - 40, self.width(), 20), Qt.AlignmentFlag.AlignHCenter,
                   f"{theme.current_avatar().name} says hi")


class Onboarding(QDialog):
    """The three-step first launch. ``exec()`` returns Accepted once the
    key, buddy and shortcut are saved; Rejected if the window is closed."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Clixy")
        self.setWindowIcon(theme.app_icon())
        self.setModal(True)
        self.setFixedSize(900, 600)
        self._avatar = self._avatar_at_open = theme.current_avatar_id()
        from config import resolve_setting
        self._hotkey = resolve_setting("HOTKEY", default="ctrl+win")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._art = _ArtPanel()
        root.addWidget(self._art)
        body = QWidget()
        body.setObjectName("pane")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(52, 56, 48, 36)
        bv.setSpacing(0)
        self._stack = QStackedWidget()
        bv.addWidget(self._stack)
        root.addWidget(body, stretch=1)

        self._stack.addWidget(self._step_key())
        self._stack.addWidget(self._step_buddy())
        self._stack.addWidget(self._step_shortcut())
        self._fade = None
        self.go(0)

    # ---------- Steps -------------------------------------------------------------

    def _step(self, title: str, lead: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        h = QLabel(title)
        h.setObjectName("h1")
        v.addWidget(h)
        label = QLabel(lead)
        label.setObjectName("lead")
        label.setWordWrap(True)
        v.addWidget(label)
        v.addSpacing(14)
        return page, v

    def _actions(self, v: QVBoxLayout, *, back: bool, primary: QPushButton, extra: QWidget | None = None) -> None:
        v.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(10)
        if back:
            back_button = QPushButton("Back")
            back_button.setCursor(Qt.CursorShape.PointingHandCursor)
            back_button.clicked.connect(lambda: self.go(self._stack.currentIndex() - 1))
            row.addWidget(back_button)
        if extra is not None:
            row.addWidget(extra)
        row.addStretch(1)
        primary.setObjectName("primary")
        primary.setCursor(Qt.CursorShape.PointingHandCursor)
        primary.setMinimumWidth(150)
        row.addWidget(primary)
        v.addLayout(row)

    def _step_key(self) -> QWidget:
        page, v = self._step(
            "Paste your Claude key.",
            "Clixy uses Claude Haiku 5.5 to see your screen and answer. About $0.0003 a question.",
        )
        self.paste = PasteZone()
        self.paste.keyChanged.connect(lambda _text: self._next_key.setEnabled(self.paste.kind is not None))
        v.addWidget(self.paste)
        note = QLabel("Only your screenshot and question go to Claude, and only when you ask.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        get_key = QPushButton("Get a key from Anthropic")
        get_key.setObjectName("quiet")
        get_key.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://console.anthropic.com/settings/keys"))
        )
        other = QPushButton("Use another AI")
        other.setObjectName("quiet")
        other.setCursor(Qt.CursorShape.PointingHandCursor)
        other.setToolTip("OpenAI, Google Gemini, OpenRouter, or a model on this PC with Ollama")
        other.clicked.connect(self._use_another_ai)
        links = QWidget()
        row = QHBoxLayout(links)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addWidget(get_key)
        row.addWidget(other)
        self._next_key = QPushButton("Continue")
        self._next_key.setEnabled(False)
        self._next_key.clicked.connect(lambda: self.go(1))
        self._actions(v, back=False, primary=self._next_key, extra=links)
        return page

    def _use_another_ai(self) -> None:
        """Set up any other provider in Settings > Advanced; once it has
        what it needs, setup is done."""
        from settings_dialog import SettingsDialog, required_keys_present
        dialog = SettingsDialog(self)
        dialog.show_page("advanced")
        if dialog.exec() == dialog.DialogCode.Accepted and required_keys_present():
            theme.set_avatar(self._avatar)
            self.accept()

    def _step_buddy(self) -> QWidget:
        page, v = self._step(
            "Say hi to your buddies.",
            "Pick who points for you. Switch any time from the tray.",
        )
        grid, self.cards = buddy_grid(self._pick, compact=True, columns=3)
        v.addWidget(grid)
        next_button = QPushButton("Continue")
        next_button.clicked.connect(lambda: self.go(2))
        self._actions(v, back=True, primary=next_button)
        return page

    def _step_shortcut(self) -> QWidget:
        page, v = self._step(
            "Hold to talk.",
            "Hold these keys, ask out loud, let go. Clixy looks at your screen, "
            "points and tells you.",
        )
        self.caps = KeyCaps(self._hotkey, big=True)
        self.caps.recorded.connect(self._on_recorded)
        v.addWidget(self.caps)
        v.addSpacing(6)
        change = QPushButton("Change")
        change.setObjectName("quiet")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(self.caps.record)
        hint = QLabel("Press Change, then hold the keys you want. Esc keeps these.")
        hint.setObjectName("muted")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(change)
        row.addWidget(hint, stretch=1)
        v.addLayout(row)
        note = QLabel("Your first question takes a moment while listening and voice download, once.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addSpacing(10)
        v.addWidget(note)
        finish = QPushButton("Start using Clixy")
        finish.clicked.connect(self.finish)
        self._actions(v, back=True, primary=finish)
        return page

    # ---------- Flow ----------------------------------------------------------------

    def go(self, index: int) -> None:
        index = max(0, min(index, self._stack.count() - 1))
        self._stack.setCurrentIndex(index)
        self._art.step = index
        self._art.update()
        page = self._stack.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        self._fade = QPropertyAnimation(effect, b"opacity", self)
        self._fade.setDuration(240)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(lambda: page.setGraphicsEffect(None))
        self._fade.start()
        if index == 0:
            self.paste.field.setFocus()

    def _pick(self, avatar_id: str) -> None:
        self._avatar = avatar_id
        theme.set_avatar(avatar_id, persist=False)
        self._art.update()

    def _on_recorded(self, combo: str) -> None:
        self._hotkey = combo

    def finish(self) -> None:
        """Save everything and close. Claude through the Anthropic provider
        takes an Anthropic key or an OpenRouter key alike."""
        from config import resolve_setting

        key = self.paste.text()
        if self.paste.kind is None:
            self.go(0)
            return
        keyring.set_password(KEYRING_SERVICE, "ANTHROPIC_API_KEY", key)
        keyring.set_password(KEYRING_SERVICE, "LLM_PROVIDER", "anthropic")
        # Voice on this PC unless one was already chosen.
        for slot, provider in (("STT_PROVIDER", "faster-whisper"), ("TTS_PROVIDER", "kokoro")):
            if not resolve_setting(slot, default=""):
                keyring.set_password(KEYRING_SERVICE, slot, provider)
        keyring.set_password(KEYRING_SERVICE, "HOTKEY", self._hotkey)
        theme.set_avatar(self._avatar)
        self.accept()

    def reject(self) -> None:
        """Closing the window puts back the buddy it started with."""
        theme.set_avatar(self._avatar_at_open, persist=False)
        super().reject()
