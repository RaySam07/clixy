"""What Clixy knows about this computer, as one line for the model: the
Windows version, laptop or desktop, the screens. It lets "how do I project
my screen" get Windows 11 steps on a laptop with one display, not generic
ones.

Everything is read locally (platform, the registry, a power-status call,
Qt's screen list) and only ever goes to the user's own AI provider, inside
the question.
"""
from __future__ import annotations

import platform
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def windows_version() -> str:
    """'windows 11 home 23h2 (build 22631)', or the platform's name off Windows."""
    if sys.platform != "win32":
        return f"{platform.system().lower()} {platform.release()}".strip()
    build = sys.getwindowsversion().build
    edition, release = "", ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            edition = winreg.QueryValueEx(key, "EditionID")[0]
            try:
                release = winreg.QueryValueEx(key, "DisplayVersion")[0]
            except OSError:
                release = ""
    except Exception:
        pass
    name = "windows 11" if build >= 22000 else "windows 10"  # ProductName still says 10 on 11
    bits = [name, (edition or "").lower(), (release or "").lower()]
    return " ".join(b for b in bits if b) + f" (build {build})"


@lru_cache(maxsize=1)
def form_factor() -> str:
    """'laptop' when there's a battery, else 'desktop'."""
    if sys.platform != "win32":
        return "computer"
    try:
        import ctypes

        class _Power(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        status = _Power()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return "desktop" if status.BatteryFlag & 128 else "laptop"
    except Exception:
        pass
    return "computer"


def screens() -> str:
    """'2 screens: 1920x1080 at 125% (main), 2560x1440'."""
    try:
        from PyQt6.QtGui import QGuiApplication
        found = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
    except Exception:
        return ""
    parts = []
    for s in found:
        ratio = s.devicePixelRatio() or 1.0
        size = s.geometry().size()
        text = f"{round(size.width() * ratio)}x{round(size.height() * ratio)}"
        if abs(ratio - 1.0) > 0.01:
            text += f" at {round(ratio * 100)}%"
        if s is primary or (primary is not None and s.name() == primary.name()):
            text += " (main)"
        parts.append(text)
    if not parts:
        return ""
    return f"{len(parts)} screen{'s' if len(parts) != 1 else ''}: " + ", ".join(parts)


def profile() -> str:
    """One line about this computer, for the model."""
    bits = [windows_version(), form_factor(), screens()]
    return ", ".join(b for b in bits if b)
