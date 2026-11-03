"""Dev mode: run Clixy from your own folder and every code change is live.

On when Clixy runs from source with CLIXY_DEV=1 (tools/dev/Start-Clixy-Dev.bat
sets it). A watcher looks at the app's files once a second (no extra
packages), waits until edits settle, checks the changed Python files still
compile, and restarts Clixy when it isn't in the middle of answering. A file
that doesn't compile is reported in the island instead, and Clixy keeps
running the last good code until it's fixed.

Threading: the watcher runs on the Qt main thread (a QTimer).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QTimer

WATCHED = ("*.py",)
"""What a change restarts Clixy for: the app's own code (tests and docs don't)."""

SETTLE_S = 1.2
"""Quiet time after the last edit before restarting, so a save of several files is one restart."""


LOG = Path.home() / ".clixy" / "dev.log"
"""Where dev mode's printed log goes: pythonw has no console to print to."""


def enabled() -> bool:
    return os.environ.get("CLIXY_DEV") == "1" and not getattr(sys, "frozen", False)


def log_to_file(path: Path = LOG) -> None:
    """Send print output (the app's timeline, errors) to ``path``, appended."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open(path, "a", encoding="utf-8", errors="replace", buffering=1)


def snapshot(root: Path) -> dict[Path, float]:
    """Modification time of every watched file."""
    times = {}
    for pattern in WATCHED:
        for path in root.glob(pattern):
            try:
                times[path] = path.stat().st_mtime
            except OSError:
                pass
    return times


def first_syntax_error(paths: list[Path]) -> str | None:
    """A plain sentence about the first file that doesn't compile, or None.
    Compiles in memory, so nothing is written next to the code."""
    for path in sorted(paths):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            return f"Didn't reload: {path.name} line {exc.lineno} has a mistake ({exc.msg})."
        except (OSError, UnicodeDecodeError, ValueError):
            continue  # mid-save or unreadable: the next look decides
    return None


class Watcher(QObject):
    """Calls ``on_change`` once the watched files have changed and settled,
    the changed Python compiles, and ``busy()`` says Clixy is free."""

    def __init__(
        self,
        root: Path,
        *,
        on_change: Callable[[], None],
        busy: Callable[[], bool] = lambda: False,
        on_error: Callable[[str], None] = lambda _message: None,
        interval_ms: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = Path(root)
        self._on_change = on_change
        self._busy = busy
        self._on_error = on_error
        self._seen = snapshot(self._root)
        self._changed: set[Path] = set()
        self._last_edit = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(interval_ms)

    def poll(self) -> None:
        now = snapshot(self._root)
        changed = {p for p, mtime in now.items() if self._seen.get(p) != mtime} | (set(self._seen) - set(now))
        self._seen = now
        if changed:
            self._changed |= changed
            self._last_edit = time.monotonic()
            return
        if not self._changed or time.monotonic() - self._last_edit < SETTLE_S or self._busy():
            return
        problem = first_syntax_error([p for p in self._changed if p.suffix == ".py" and p.exists()])
        self._changed.clear()
        if problem:
            self._on_error(problem)
            return
        self._timer.stop()
        self._on_change()


def restart(mutex_handle=None) -> None:
    """Start a fresh Clixy with the same command line. Lets go of the
    one-Clixy-at-a-time lock first so the new one can take it; the caller
    then quits this one."""
    if mutex_handle not in (None, "fail-open"):
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(mutex_handle)
        except Exception:
            pass
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([sys.executable, *sys.argv], cwd=os.getcwd(), close_fds=True, creationflags=flags)
