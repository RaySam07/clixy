"""Keeps Clixy dev running while its code is being edited.

Dev mode restarts Clixy by itself when the code changes (devmode.py). If that
restart lands while a change is half made, the new Clixy can fail before it
logs a thing, and nothing is left to bring it back. This watches for exactly
that: a restart in the dev log with no "Listening" after it and no Clixy
running. Then it checks the code imports, writes the error to the dev log if
it doesn't, and starts Clixy again once it does. Quitting Clixy yourself
logs no restart, so it stays quit.

Start-Clixy-Dev.bat starts this, hidden. Only one runs at a time.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = Path.home() / ".clixy" / "dev.log"
CLIXY_MUTEX = "Local\\Clixy-SingleInstance-v1"
OWN_MUTEX = "Local\\Clixy-DevKeepAlive"
RESTARTING = "Dev mode: the code changed, restarting..."
READY = "Listening for"
GRACE_S = 12.0
"""How long a restart gets to reach "Listening" before it counts as failed."""

_kernel32 = ctypes.windll.kernel32
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def clixy_running() -> bool:
    handle = _kernel32.OpenMutexW(0x00100000, False, CLIXY_MUTEX)  # SYNCHRONIZE
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    return False


def restart_failed(tail: str, log_age_s: float) -> bool:
    """A restart was asked for, never reached "Listening", and has had time to."""
    return tail.rfind(RESTARTING) > tail.rfind(READY) and log_age_s > GRACE_S


def code_stamp() -> float:
    return max((p.stat().st_mtime for p in ROOT.glob("*.py")), default=0.0)


def import_error() -> str:
    """The last line of the error importing app.py, or "" when it imports."""
    python = Path(sys.executable).with_name("python.exe")
    result = subprocess.run([str(python), "-c", "import app"], cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, creationflags=_NO_WINDOW,
                            timeout=120)
    if result.returncode == 0:
        return ""
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    return lines[-1] if lines else f"exit code {result.returncode}"


def note(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[dev keep-alive {time.strftime('%H:%M:%S')}] {line}\n")


def main() -> int:
    _own = _kernel32.CreateMutexW(None, False, OWN_MUTEX)
    if ctypes.GetLastError() == 183:  # ERROR_ALREADY_EXISTS: one is already watching
        return 0
    failed_on = None  # the code version that last failed to import
    while True:
        time.sleep(2.0)
        try:
            tail = LOG.read_text(encoding="utf-8", errors="replace")[-4000:]
            age = time.time() - LOG.stat().st_mtime
        except OSError:
            continue
        if not restart_failed(tail, age) or clixy_running():
            continue
        stamp = code_stamp()
        if stamp == failed_on:
            continue  # still broken: wait for the next save
        error = import_error()
        if error:
            failed_on = stamp
            note(f"Clixy didn't start: {error}. Trying again after the next save.")
            continue
        failed_on = None
        subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), "app.py"], cwd=ROOT,
                         env={**os.environ, "CLIXY_DEV": "1"}, close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        note("A restart didn't come back, so I started Clixy again.")
        time.sleep(GRACE_S)


if __name__ == "__main__":
    sys.exit(main())
