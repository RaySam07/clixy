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

# More pages, for guide mode only (the everyday answer prompt stays lean). All
# open on Windows 11; power and battery share one page there.
MORE_SETTINGS_PAGES = {
    "battery usage by app": "ms-settings:batterysaver-usagedetails",
    "advanced display, refresh rate": "ms-settings:display-advanced",
    "graphics, gpu per app": "ms-settings:display-advancedgraphics",
    "volume mixer, app volume": "ms-settings:apps-volume",
    "sound devices": "ms-settings:sound-devices",
    "multitasking, snap windows": "ms-settings:multitasking",
    "clipboard history": "ms-settings:clipboard",
    "projecting to this pc": "ms-settings:project",
    "phone link, mobile devices": "ms-settings:mobile-devices",
    "airplane mode": "ms-settings:network-airplanemode",
    "proxy": "ms-settings:network-proxy",
    "data usage": "ms-settings:datausage",
    "start menu": "ms-settings:personalization-start",
    "fonts": "ms-settings:fonts",
    "optional features": "ms-settings:optionalfeatures",
    "game mode": "ms-settings:gaming-gamemode",
    "game bar": "ms-settings:gaming-gamebar",
    "windows security, antivirus": "ms-settings:windowsdefender",
    "recovery, reset this pc": "ms-settings:recovery",
    "activation": "ms-settings:activation",
    "troubleshoot": "ms-settings:troubleshoot",
    "for developers": "ms-settings:developers",
    "other users, family": "ms-settings:otherusers",
    "email and accounts": "ms-settings:emailandaccounts",
    "windows backup": "ms-settings:backup",
    "update history": "ms-settings:windowsupdate-history",
    "advanced update options": "ms-settings:windowsupdate-options",
    "optional updates, drivers": "ms-settings:windowsupdate-optionalupdates",
    "speech": "ms-settings:speech",
    "narrator": "ms-settings:easeofaccess-narrator",
    "magnifier": "ms-settings:easeofaccess-magnifier",
    "colour filters": "ms-settings:easeofaccess-colorfilter",
    "contrast themes": "ms-settings:easeofaccess-highcontrast",
    "captions": "ms-settings:easeofaccess-closedcaptioning",
    "keyboard accessibility, sticky keys": "ms-settings:easeofaccess-keyboard",
    "mouse pointer size": "ms-settings:easeofaccess-mousepointer",
    "usb": "ms-settings:usb",
    "autoplay": "ms-settings:autoplay",
    "pen": "ms-settings:pen",
    "find my device": "ms-settings:findmydevice",
    "remote desktop": "ms-settings:remotedesktop",
}

# Windows tools Clixy can open by their run names, and the window each one makes.
RUN_COMMANDS = {
    "taskmgr": "task manager", "msinfo32": "system information", "dxdiag": "directx diagnostic tool",
    "devmgmt.msc": "device manager", "diskmgmt.msc": "disk management", "services.msc": "services",
    "eventvwr.msc": "event viewer", "taskschd.msc": "task scheduler", "compmgmt.msc": "computer management",
    "resmon": "resource monitor", "control": "control panel", "appwiz.cpl": "programs and features",
    "ncpa.cpl": "network connections", "mmsys.cpl": "sound", "main.cpl": "mouse properties",
    "powercfg.cpl": "power options", "sysdm.cpl": "system properties", "firewall.cpl": "firewall",
    "inetcpl.cpl": "internet properties", "timedate.cpl": "date and time", "winver": "about windows",
    "cleanmgr": "disk cleanup", "mdsched": "memory diagnostic", "osk": "on-screen keyboard",
    "magnify": "magnifier", "charmap": "character map", "mstsc": "remote desktop connection",
}
# Only these take arguments ("control /name Microsoft.PowerOptions", "control printers").
_ARG_COMMANDS = {"control"}
# Windows' own app links Clixy opens directly (spotify:search:lofi, mailto:, ms-screenclip:).
_URI_RE = re.compile(r"^(?!ms-settings:)[a-z][a-z0-9+.-]{1,30}:.*$", re.IGNORECASE)   # never c:\ (one letter)
BATTERY_REPORT = "battery report"

# Shortcuts that open a window, and how to recognise it (for waiting until it's there).
SHORTCUT_WINDOWS = {
    frozenset({"ctrl", "shift", "esc"}): "task manager",
    frozenset({"win", "e"}): "file explorer",
    frozenset({"win", "i"}): "settings",
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
    """"settings", "web", "uri" (an app link like spotify:search:lofi or
    shell:startup), "command" (a Windows tool by its run name), "report"
    (the battery report) or "app"."""
    t = target.strip().lower()
    if t.startswith("ms-settings:"):
        return "settings"
    if re.match(r"^(https?://|www\.)", t) or re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?", t):
        if t.split("/")[0] not in RUN_COMMANDS:   # devmgmt.msc looks like a web address
            return "web"
    if t == BATTERY_REPORT:
        return "report"
    if _URI_RE.match(t):
        return "uri"
    first, _, rest = t.partition(" ")
    if first in RUN_COMMANDS and (not rest or first in _ARG_COMMANDS):
        return "command"
    return "app"


def window_hint(target: str, opened: str = "") -> str:
    """How to recognise the window that opening ``target`` makes, in the
    words its title or app name carries ("settings", "task manager"); ""
    when it can't be told (a website opens in whatever browser is in front)."""
    kind, t = kind_of(target), target.strip().lower()
    if t.startswith("%"):
        return "file explorer"   # %temp%, %appdata%
    if kind == "settings":
        return "settings"
    if kind == "report":
        return ""   # it opens in the browser: its title changing is the sign
    if kind == "command":
        return RUN_COMMANDS[t.partition(" ")[0]]
    if kind == "uri":
        scheme = t.split(":", 1)[0]
        if scheme in ("shell",) or t.startswith("%"):
            return "file explorer"
        return {"spotify": "spotify", "whatsapp": "whatsapp", "msteams": "teams"}.get(scheme, "")
    if kind == "app":
        return (opened or target).strip().lower()
    return ""


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


_BROWSERS = {"google chrome": "chrome", "chrome": "chrome", "microsoft edge": "msedge", "edge": "msedge",
             "firefox": "firefox", "brave": "brave", "opera": "opera"}
_BROWSER_RE = re.compile(r"\b(" + "|".join(_BROWSERS) + r")\b", re.IGNORECASE)


def browser_named(text: str) -> str:
    """The browser a request names ("search it on Chrome" -> "chrome"), as Windows
    knows its program; "" when it names none (links then open in the default one)."""
    m = _BROWSER_RE.search(text or "")
    return _BROWSERS[m.group(1).lower()] if m else ""


def open_target(target: str, *, start=None, browser: str = "") -> str:
    """Open an app, a website or a Settings page. Returns what was opened
    ("Spotify", "youtube.com"), or "" when nothing matched. A website opens in
    ``browser`` ("chrome") when given, else in the default browser."""
    start = start or _start
    kind = kind_of(target)
    if kind == "settings":
        start(target.strip())
        return "Settings"
    if kind == "report":
        return _battery_report(start)
    if kind == "uri":
        link = target.strip()
        start(link.replace(" ", "%20") if not link.lower().startswith("shell:") else link)
        return link
    if kind == "command":
        name, _, args = target.strip().partition(" ")
        path = _system_tool(name.lower())
        if args:
            start(path, args.strip())
        else:
            start(path)
        return RUN_COMMANDS[name.lower()].title()
    if "%" in target:
        folder = os.path.expandvars(target.strip())
        if folder != target.strip() and os.path.isdir(folder):
            start(folder)
            return os.path.basename(folder.rstrip("\\/")) or folder
    if kind == "web":
        url = target.strip()
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        try:
            start(browser, url) if browser else start(url)
        except OSError:
            start(url)                   # that browser isn't installed: the default one
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


def _start(what: str, args: str = "") -> None:
    if sys.platform != "win32":
        raise OSError("opening things works on Windows")
    if what.lower().startswith("shell:appsfolder\\"):
        subprocess.Popen(["explorer.exe", what], close_fds=True)
    elif args:
        os.startfile(what, arguments=args)  # noqa: S606 - only for the tools in _ARG_COMMANDS
    else:
        os.startfile(what)  # noqa: S606 - opening what the user asked for is the point


def _system_tool(name: str) -> str:
    """The full path of a Windows tool ("taskmgr" -> C:\\Windows\\System32\\taskmgr.exe)."""
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    for candidate in (name, name + ".exe"):
        path = os.path.join(system32, candidate)
        if os.path.isfile(path):
            return path
    return name


def _battery_report(start) -> str:
    """Make Windows' battery report (its health: design capacity against what
    it holds now, and recent use) and open it in the browser. powercfg writes
    the file; nothing else changes and no admin rights are needed."""
    if sys.platform != "win32":
        return ""
    from config import APP_HOME
    out = Path(APP_HOME) / "battery-report.html"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([_system_tool("powercfg"), "/batteryreport", "/output", str(out)], capture_output=True,
                       timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ""
    if not out.is_file():
        return ""   # a desktop with no battery
    start(str(out))
    return "the battery report"


# --- The apps on this PC --------------------------------------------------------------

def load_in_background() -> None:
    """Read the Start menu's apps once, without holding anything up."""
    if _loaded.is_set():
        return
    threading.Thread(target=_load, name="clixy-apps", daemon=True).start()


def names() -> list[str]:
    with _lock:
        return sorted(_apps)


def prompt_block(guide: bool = False) -> str:
    """What the model is told about opening things, with this PC's apps.
    The guide also gets more Settings pages, Windows tools by run name, app
    links, the battery report and opening two things side by side."""
    pages = "; ".join(f"{what}: {uri}" for what, uri in SETTINGS_PAGES.items())
    listed = ", ".join(names()[:220])
    apps_line = f"apps installed on this pc: {listed}." if listed else ""
    if guide:
        more = "; ".join(f"{what}: {uri}" for what, uri in MORE_SETTINGS_PAGES.items())
        tools = ", ".join(f"{cmd} ({what})" for cmd, what in RUN_COMMANDS.items())
        return (
            "\n\nopening things:\n"
            "[OPEN:x] opens it at once, before any other tag. x is one of: an app by its name in this pc's list; "
            "a website's real address; a settings page (ms-settings:...); a windows tool by its run name; "
            "\"control /name Microsoft.PowerOptions\" style control panel pages (control only); a folder like "
            "shell:startup, shell:downloads, %temp% or %appdata%; an app link whose scheme the know-how gives "
            "(spotify:search:lofi, mailto:, ms-screenclip:); or \"battery report\", which makes windows' battery "
            "health report and opens it in the browser.\n"
            "two at once: [OPEN:a][OPEN:b] opens both and puts them side by side, a on the left. only when the "
            "goal needs both on screen together.\n"
            f"settings pages: {pages}; {more}.\n"
            f"windows tools: {tools}. resmon may ask for admin permission.\n{apps_line}"
        )
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
