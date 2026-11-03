"""Opening things for you: apps, websites and Windows Settings pages.

Clixy knows the apps installed on this PC (the Start menu's list, Store apps
included), so "open Spotify" opens Spotify and "take me to display settings"
jumps straight to that page instead of clicking through menus. The model
asks with a tag at the end of its answer:

    [OPEN:spotify]                  an installed app, by name
    [OPEN:https://youtube.com]      a website (in your default browser)
    [OPEN:ms-settings:display]      a Windows Settings page

Opening is the only thing Clixy does on its own: it never clicks, types or
sends anything for you.

Threading: the app list loads once in the background; opening runs wherever
it's called from (the pipeline's worker thread).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

# Windows Settings pages the model can jump to (the useful ones, by what people ask).
SETTINGS_PAGES = {
    "display, brightness, scale, resolution, multiple displays": "ms-settings:display",
    "night light": "ms-settings:nightlight",
    "sound, output and input devices, volume": "ms-settings:sound",
    "bluetooth and devices": "ms-settings:bluetooth",
    "wi-fi": "ms-settings:network-wifi",
    "network and internet": "ms-settings:network-status",
    "vpn": "ms-settings:network-vpn",
    "mobile hotspot": "ms-settings:network-mobilehotspot",
    "wallpaper, background": "ms-settings:personalization-background",
    "colours, dark mode, accent colour": "ms-settings:personalization-colors",
    "themes": "ms-settings:themes",
    "lock screen": "ms-settings:lockscreen",
    "taskbar": "ms-settings:taskbar",
    "notifications": "ms-settings:notifications",
    "focus, do not disturb": "ms-settings:quiethours",
    "power, sleep, battery": "ms-settings:powersleep",
    "battery saver": "ms-settings:batterysaver",
    "storage, disk space": "ms-settings:storagesense",
    "installed apps, uninstall": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps",
    "startup apps": "ms-settings:startupapps",
    "mouse": "ms-settings:mousetouchpad",
    "touchpad": "ms-settings:devices-touchpad",
    "keyboard, typing": "ms-settings:typing",
    "printers and scanners": "ms-settings:printers",
    "camera privacy": "ms-settings:privacy-webcam",
    "microphone privacy": "ms-settings:privacy-microphone",
    "location": "ms-settings:privacy-location",
    "accounts, your info": "ms-settings:yourinfo",
    "sign-in options, pin, password": "ms-settings:signinoptions",
    "date and time": "ms-settings:dateandtime",
    "language and region": "ms-settings:regionlanguage",
    "windows update": "ms-settings:windowsupdate",
    "about this pc": "ms-settings:about",
    "accessibility, text size": "ms-settings:easeofaccess-display",
}

_OPEN_RE = re.compile(r"\[\s*OPEN\s*:\s*([^\]]+?)\s*\]", re.IGNORECASE)
_CUT_OFF_RE = re.compile(r"\[\s*OPEN\b[^\]]*$", re.IGNORECASE | re.DOTALL)

_lock = threading.Lock()
_apps: dict[str, str] = {}      # name -> how to start it (an AppID or a shortcut path)
_loaded = threading.Event()


def parse_open(text: str) -> tuple[str, list[str]]:
    """The answer without its [OPEN:...] tags, and what they ask to open."""
    targets = [m.group(1).strip() for m in _OPEN_RE.finditer(text or "")]
    spoken = _CUT_OFF_RE.sub("", _OPEN_RE.sub("", text or ""))
    return re.sub(r"\s+", " ", spoken).strip(), targets


def kind_of(target: str) -> str:
    """"settings", "web" or "app"."""
    t = target.strip().lower()
    if t.startswith("ms-settings:"):
        return "settings"
    if re.match(r"^(https?://|www\.)", t) or re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?", t):
        return "web"
    return "app"


def find_app(name: str, apps: dict[str, str] | None = None) -> str | None:
    """The installed app that best matches ``name`` ("spotify" -> "Spotify")."""
    apps = _apps if apps is None else apps
    if not apps:
        return None
    wanted = name.strip().lower()
    by_lower = {n.lower(): n for n in apps}
    if wanted in by_lower:
        return by_lower[wanted]
    starts = [n for low, n in by_lower.items() if low.startswith(wanted)]
    if starts:
        return min(starts, key=len)
    words = [n for low, n in by_lower.items() if re.search(rf"\b{re.escape(wanted)}\b", low)]
    if words:
        return min(words, key=len)
    close = difflib.get_close_matches(wanted, list(by_lower), n=1, cutoff=0.75)
    return by_lower[close[0]] if close else None


def open_target(target: str, *, start=None) -> str:
    """Open an app, a website or a Settings page. Returns what was opened
    ("Spotify", "youtube.com"), or "" when nothing matched."""
    start = start or _start
    kind = kind_of(target)
    if kind == "settings":
        start(target.strip())
        return "Settings"
    if kind == "web":
        url = target.strip()
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        start(url)
        return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    name = find_app(target)
    if name is not None:
        how = _apps[name]
        if how.lower().endswith(".lnk") or os.path.isabs(how):
            start(how)
        else:
            start("shell:AppsFolder\\" + how)
        return name
    # Not in the Start menu: Windows may still know it ("notepad", "calc", "chrome").
    try:
        start(target.strip())
        return target.strip()
    except OSError:
        return ""


def _start(what: str) -> None:
    if sys.platform != "win32":
        raise OSError("opening things works on Windows")
    if what.lower().startswith("shell:appsfolder\\"):
        subprocess.Popen(["explorer.exe", what], close_fds=True)
    else:
        os.startfile(what)  # noqa: S606 - opening what the user asked for is the point


# --- The apps on this PC --------------------------------------------------------------

def load_in_background() -> None:
    """Read the Start menu's apps once, without holding anything up."""
    if _loaded.is_set():
        return
    threading.Thread(target=_load, name="clixy-apps", daemon=True).start()


def names() -> list[str]:
    with _lock:
        return sorted(_apps)


def prompt_block() -> str:
    """What the model is told about opening things, with this PC's apps."""
    pages = "; ".join(f"{what}: {uri}" for what, uri in SETTINGS_PAGES.items())
    listed = ", ".join(names()[:220])
    apps_line = f"apps installed on this pc: {listed}." if listed else ""
    return (
        "\n\nopening things:\n"
        "you can open apps, websites and windows settings pages for the user. put the tag right after your "
        "spoken words, before any other tag: [OPEN:app name], [OPEN:https://site.com] or [OPEN:ms-settings:page]. "
        "do it when they ask to open, launch, go to or show something, or when opening it is the obvious "
        "first step. say what you're opening in a few words. never open more than one thing at a time. "
        "use the app's name as it appears in this pc's list; for a website, its real address.\n"
        f"settings pages: {pages}.\n{apps_line}"
    )


def _load() -> None:
    found: dict[str, str] = {}
    if sys.platform == "win32":
        found.update(_shortcuts())
        found.update(_start_apps())
    with _lock:
        _apps.clear()
        _apps.update({n: how for n, how in found.items() if _useful(n)})
    _loaded.set()


def _useful(name: str) -> bool:
    low = name.lower()
    return bool(name.strip()) and not any(w in low for w in ("uninstall", "readme", "help", "documentation",
                                                              "release notes", "website", "license"))


def _shortcuts() -> dict[str, str]:
    roots = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
    out = {}
    for root in roots:
        if root.is_dir():
            for link in root.rglob("*.lnk"):
                out.setdefault(link.stem, str(link))
    return out


def _start_apps() -> dict[str, str]:
    """Every app in the Start menu, Store apps included, with its AppID."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = json.loads(result.stdout or "[]")
    except Exception:
        return {}
    if isinstance(rows, dict):
        rows = [rows]
    return {r["Name"]: r["AppID"] for r in rows if isinstance(r, dict) and r.get("Name") and r.get("AppID")}
