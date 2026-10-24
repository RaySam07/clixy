"""System tray icon for Clicky Windows.

Provides the ONLY clean exit path from the running app: without this
icon, users have no menu/taskbar entry to right-click and quit, and
must reach for Task Manager (Ctrl+Shift+Esc) to kill ``Clicky.exe``.

Menu structure (right-click the tray icon):
    - Settings...                   ← reopen the BYOK keyring dialog
    - Avatar >                      ← switch buddy (and theme) instantly
    - Open Knowledge Folder         ← jump to ~/Documents/Clicky Wiki/
    - Open Memory Folder            ← jump to ~/.clicky-windows/memory/
    - Forget My History...          ← delete memory notes, index and debug logs
    - --------
    - Quit Clicky                   ← clean shutdown via callback

Implementation notes:
- ``QSystemTrayIcon`` is the PyQt6 native widget: no extra deps,
  and it plays nicely with our existing Qt event loop.
- Tray icon: the current avatar, drawn by theme.avatar_icon at every
  tray size; it changes when the avatar does.
- "Open Folder" actions use ``os.startfile`` (Windows native, opens
  in File Explorer). Folders are auto-created if missing so the user
  doesn't get an error on first click before they've dropped any
  files.
- The Quit action calls a parent-supplied callback rather than
  closing windows directly. The callback in app.py runs ``stop()``
  on STT/TTS/hotkey before ``QApplication.quit()`` to avoid leaking
  worker threads / WebSocket connections.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QAction, QActionGroup, QPixmap
from PyQt6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

import theme
from config import INDEX_DB_PATH, KB_DIR, MEMORY_DIR


class ClickyTray(QObject):
    """Tray icon + right-click menu wrapper.

    The icon is shown as soon as ``__init__`` completes. Pass
    ``on_quit`` (called when the user clicks Quit) and
    ``on_settings`` (called when they click Settings...). Both fire
    on the Qt main thread.
    """

    def __init__(
        self,
        *,
        on_quit: Callable[[], None],
        on_settings: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        # Guard against weird Windows configs without a system tray
        # (kiosk mode, custom shells, certain VMs). Without this check
        # the tray icon silently fails to appear and the user has no
        # diagnostic, just an apparently-running app with no quit
        # menu. Caller in app.py wraps with try/except + QMessageBox
        # so the user gets an actionable dialog before exit.
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError(
                "System tray is not available on this Windows "
                "configuration. Clicky needs the system tray to "
                "provide its quit menu and settings access. Check "
                "Windows taskbar settings (Settings -> "
                "Personalisation -> Taskbar -> Other system tray "
                "icons)."
            )

        self._on_quit = on_quit
        self._on_settings = on_settings

        self._icon = QSystemTrayIcon(parent=self)
        from hotkey import DEFAULT_COMBO, describe
        from config import resolve_setting
        self._icon.setToolTip(f"Clicky: hold {describe(resolve_setting('HOTKEY', DEFAULT_COMBO))} and ask")
        self._icon.setIcon(theme.avatar_icon(theme.current_avatar_id()))

        self._menu = QMenu()
        # Frameless + translucent so the stylesheet's rounded corners show.
        self._menu.setWindowFlags(
            self._menu.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self._menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._avatar_actions: dict[str, QAction] = {}
        self._build_menu()
        theme.on_avatar_changed(self._on_avatar_changed)
        self._icon.setContextMenu(self._menu)
        self._icon.show()

    # ---------- Menu construction ---------------------------------------

    def _build_menu(self) -> None:
        act_settings = QAction("Settings...", self)
        act_settings.triggered.connect(self._on_settings)
        self._menu.addAction(act_settings)

        avatars = self._menu.addMenu("Buddy")
        avatars.setWindowFlags(self._menu.windowFlags())
        avatars.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        group = QActionGroup(self)
        group.setExclusive(True)
        for av in theme.AVATARS:
            action = QAction(theme.avatar_icon(av.id), f"{av.name}  ·  {av.palette.name}", self)
            action.setCheckable(True)
            action.setChecked(av.id == theme.current_avatar_id())
            action.triggered.connect(lambda _checked=False, avatar_id=av.id: theme.set_avatar(avatar_id))
            group.addAction(action)
            avatars.addAction(action)
            self._avatar_actions[av.id] = action

        act_open_kb = QAction("Open Knowledge Folder", self)
        act_open_kb.triggered.connect(self._open_kb_folder)
        self._menu.addAction(act_open_kb)

        act_open_mem = QAction("Open Memory Folder", self)
        act_open_mem.triggered.connect(self._open_memory_folder)
        self._menu.addAction(act_open_mem)

        act_forget = QAction("Forget My History...", self)
        act_forget.triggered.connect(self._forget_history)
        self._menu.addAction(act_forget)

        self._menu.addSeparator()

        act_quit = QAction("Quit Clicky", self)
        act_quit.triggered.connect(self._on_quit)
        self._menu.addAction(act_quit)

    # ---------- Folder actions ------------------------------------------

    @staticmethod
    def _open_in_explorer(path: Path) -> None:
        """Open ``path`` in Windows File Explorer; create-if-missing.

        Folders that don't exist yet (first launch, user hasn't dropped
        anything) get created so the user lands in an empty folder
        rather than an error dialog.
        """
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def notify(self, message: str) -> None:
        """Show a short balloon from the tray icon."""
        self._icon.showMessage("Clicky", message, QSystemTrayIcon.MessageIcon.Warning, 8000)

    def _open_kb_folder(self) -> None:
        self._open_in_explorer(KB_DIR)

    def _open_memory_folder(self) -> None:
        self._open_in_explorer(MEMORY_DIR)

    def _on_avatar_changed(self, avatar_id: str) -> None:
        self._icon.setIcon(theme.avatar_icon(avatar_id))
        action = self._avatar_actions.get(avatar_id)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def _forget_history(self) -> None:
        """Ask, then delete what Clicky keeps about past questions: the
        per-app memory notes, their index and any debug logs. Keys, settings
        and the knowledge folder (your own files) are left alone."""
        if forget_dialog().exec() != QMessageBox.StandardButton.Yes:
            return
        failed = forget_history()
        if failed:
            self.notify("Some files were in use and stayed. Quit Clicky and try again to remove them.")
        else:
            self._icon.showMessage("Clicky", "Done. Your history is gone.", QSystemTrayIcon.MessageIcon.Information, 4000)


def forget_dialog() -> QMessageBox:
    """The confirmation for Forget My History, with the buddy as its icon."""
    box = QMessageBox()
    box.setWindowTitle("Clicky")
    box.setText("Forget my history?")
    box.setInformativeText(
        "Clicky will delete its notes about what you asked in each app, and any "
        "debug logs. Your keys, settings and Knowledge Folder stay."
    )
    box.setIconPixmap(QPixmap.fromImage(theme.avatar_image(theme.current_avatar_id(), 56, mood="thinking")))
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    forget = box.button(QMessageBox.StandardButton.Yes)
    forget.setText("Forget")
    forget.setObjectName("primary")
    forget.style().unpolish(forget)  # restyle now that it has the accent's name
    forget.style().polish(forget)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box


def forget_history() -> list[Path]:
    """Delete the memory folder's contents, the memory index and the debug
    folder. Returns the paths that couldn't be removed."""
    failed: list[Path] = []
    debug_dir = Path(MEMORY_DIR).parent / "debug"
    for folder in (Path(MEMORY_DIR), debug_dir):
        if not folder.is_dir():
            continue
        for child in folder.iterdir():
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            except OSError:
                failed.append(child)
    index = Path(INDEX_DB_PATH)
    for path in (index, index.with_name(index.name + "-wal"), index.with_name(index.name + "-shm")):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failed.append(path)
    return failed
