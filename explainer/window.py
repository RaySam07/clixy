"""The explainer window: a 3D model over the whole screen you're on.

It covers the monitor under your mouse, with a blurred, dimmed picture of
that screen behind the model, so the model seems to sit on your desktop.
Esc or the close button puts it away; it stays loaded, hidden, so the next
one opens at once.

The page runs in an off-the-record profile (nothing is cached or written to
disk), can't navigate anywhere or open windows, and loads nothing from the
network: the engine, recipes, fonts and pictures are all local.
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QGuiApplication
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

import explainer
import theme

BACKDROP_WIDTH = 1280
"""The screen picture behind the model is blurred, so a small one will do."""


def _png(image) -> str:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return explainer.data_url(bytes(data), "image/png")


def _screen_picture(screen) -> str:
    shot = screen.grabWindow(0)
    if shot.isNull():
        return ""
    if shot.width() > BACKDROP_WIDTH:
        shot = shot.scaledToWidth(BACKDROP_WIDTH, Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    shot.save(buf, "JPEG", 72)
    return explainer.data_url(bytes(data), "image/jpeg")


def _buddy_frames() -> list[str]:
    avatar_id = theme.current_avatar_id()
    return [_png(theme.avatar_image(avatar_id, 96, mood="happy", frame=f)) for f in range(4)]


class _LocalPage(QWebEnginePage):
    """Stays on the explainer page: no links out, no new windows."""

    def acceptNavigationRequest(self, url: QUrl, _type, _is_main_frame: bool) -> bool:
        return url.isLocalFile() and url.toLocalFile().endswith(explainer.PAGE.name)

    def createWindow(self, _type):
        return None


class ExplainerWindow(QWidget):
    """Frameless, on top of everything, the size of one screen."""

    closed = pyqtSignal()
    topic_changed = pyqtSignal(str)
    part_clicked = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setWindowTitle("Clixy 3D")
        # The view comes first so it (and its page) is deleted before the profile.
        self._view = QWebEngineView(self)
        self._profile = QWebEngineProfile(self)  # no storage name: off the record
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        page = _LocalPage(self._profile, self._view)
        page.setBackgroundColor(QColor(10, 10, 18))
        settings = page.settings()
        attr = QWebEngineSettings.WebAttribute
        settings.setAttribute(attr.JavascriptCanAccessClipboard, False)
        settings.setAttribute(attr.JavascriptCanOpenWindows, False)
        settings.setAttribute(attr.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(attr.ShowScrollBars, False)
        settings.setAttribute(attr.WebGLEnabled, True)
        self._view.setPage(page)
        page.loadFinished.connect(self._on_loaded)
        page.titleChanged.connect(self._on_title)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        self._loaded = False
        self._booted = False
        self._pending: dict | None = None
        self.topic = ""
        self._view.setUrl(QUrl.fromLocalFile(str(explainer.PAGE)))

    # --- Opening and closing --------------------------------------------------

    def open(self, topic: str) -> None:
        """Show ``topic`` over the screen the mouse is on."""
        showing = self.isVisible()  # asked for another while one is up: stay put
        screen = self.screen() if showing else (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        palette = theme.current_palette()
        self._pending = explainer.payload(
            topic,
            backdrop="" if showing else _screen_picture(screen),  # before the window covers it
            accent=palette.flood.name(),
            on_accent=palette.on_flood.name(),
            buddy=_buddy_frames(),
        )
        self.topic = topic
        if not showing:
            self.setGeometry(screen.geometry())
        if self._loaded:
            self._boot()

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._view.page().runJavaScript("window.Clixy3D && Clixy3D.pause()")
        self.hide()
        self.closed.emit()

    def closeEvent(self, event) -> None:  # Alt+F4 hides it like Esc does
        event.ignore()
        self.dismiss()

    # --- Talking to the page --------------------------------------------------

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = ok
        if ok and self._pending is not None:
            self._boot()

    def _boot(self) -> None:
        p, self._pending = self._pending, None
        if p is None:
            return
        if self._booted:
            light = {k: p[k] for k in ("open", "backdrop", "accent", "onAccent", "buddy")}
            script = f"Clixy3D.reopen({json.dumps(light)})"
        else:
            script = f"Clixy3D.boot({json.dumps(p)})"
            self._booted = True
        self._view.page().runJavaScript(script)
        self.show()
        self.raise_()
        self.activateWindow()
        self._view.setFocus()

    def _on_title(self, title: str) -> None:
        # The page speaks by setting its title: clixy:<what>[:<detail>]:<count>.
        bits = title.split(":")
        if len(bits) < 3 or bits[0] != "clixy":
            return
        what = bits[1]
        if what == "close":
            self.dismiss()
        elif what == "topic" and len(bits) >= 4:
            self.topic = bits[2]
            self.topic_changed.emit(bits[2])
        elif what == "part" and len(bits) >= 4:
            self.part_clicked.emit(bits[2])


_window: ExplainerWindow | None = None


def open_explainer(topic: str) -> ExplainerWindow:
    """Open ``topic`` in the one explainer window (made on first use)."""
    global _window
    if _window is None:
        _window = ExplainerWindow()
    _window.open(topic)
    return _window
