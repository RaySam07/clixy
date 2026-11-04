"""What Clixy knows about this laptop from using it with you: the apps you use, the sites you
keep in your browser (WhatsApp Web in a Chrome tab, Hark in its own Chrome window), and the
browser you live in. Guide steps and answers get it as a few lines, so "go to WhatsApp"
switches to the tab you already have instead of opening something new.

Only names are kept, never what a page says: "(95) WhatsApp - Google Chrome" becomes the site
"WhatsApp" in Chrome. It lives in the memory folder (laptop.json, next to what Clixy
remembers) and is only kept while Settings > Privacy keeps history.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path

_BROWSERS = {"chrome.exe": "chrome", "msedge.exe": "edge", "firefox.exe": "firefox", "brave.exe": "brave",
             "opera.exe": "opera"}
_BROWSER_TAIL = re.compile(r"\s+(?:and \d+ more pages?\s*)?[-–]\s+(?:(?:Personal|Work|Profile \d+)\s+[-–]\s+)?"
                           r"(?:Google Chrome|Microsoft​?\s?Edge|Mozilla Firefox|Brave|Opera)\s*$", re.IGNORECASE)
_COUNT = re.compile(r"^\(\d+\)\s*")
_SKIP_APPS = {"pythonw.exe", "python.exe", "clixy.exe", "explorer.exe", "searchhost.exe", "lockapp.exe",
              "shellexperiencehost.exe", "startmenuexperiencehost.exe", "applicationframehost.exe",
              "textinputhost.exe", "unknown"}
_SKIP_SITES = {"new tab", "untitled", "clixy", "settings", "downloads", "history", "extensions", "google chrome",
               "microsoft edge", "mozilla firefox", "brave", "opera", ""}
_NAMES = {"code.exe": "VS Code", "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint",
          "outlook.exe": "Outlook", "olk.exe": "Outlook", "ms-teams.exe": "Teams", "spotify.exe": "Spotify",
          "whatsapp.exe": "WhatsApp", "taskmgr.exe": "Task Manager", "systemsettings.exe": "Settings"}


def place_of(exe: str, title: str) -> tuple[str, str, str]:
    """("site", name, browser) for a page in a browser, ("app", name, "") for an app, or
    ("", "", "") for what isn't worth knowing (Clixy itself, the desktop, a new tab)."""
    exe = (exe or "").lower()
    title = " ".join((title or "").split())
    if exe in _SKIP_APPS or not title:
        return "", "", ""
    browser = _BROWSERS.get(exe, "")
    if browser:
        page = _BROWSER_TAIL.sub("", title)
        in_tab = page != title            # a Chrome app window (Hark) has no browser name in its title
        name = _COUNT.sub("", re.split(r"\s+[-–]\s+", page)[-1])
        name = re.split(r"\s+[·|/]\s+", name)[-1].strip()
        if name.lower() in _SKIP_SITES or len(name) > 40:
            return "", "", ""
        return "site", name, browser + ("" if in_tab else " app window")
    name = _NAMES.get(exe) or (title if len(title.split()) <= 3 and " - " not in title else exe.removesuffix(".exe"))
    return "app", name, ""


class LaptopMap:
    """Counts of what you use, by name. ``see(exe, title)`` notes the window in front;
    ``summary()`` is the few lines the models get."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.apps: dict[str, dict] = {}
        self.sites: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._dirty = False

    # --- Keeping it ---------------------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "LaptopMap":
        found = cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.apps = dict(data.get("apps") or {})
            found.sites = dict(data.get("sites") or {})
        except (OSError, ValueError):
            pass
        return found

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        with self._lock:
            data = {"apps": self.apps, "sites": self.sites}
            self._dirty = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    def see(self, exe: str, title: str, when: dt.datetime | None = None) -> None:
        kind, name, where = place_of(exe, title)
        if not kind:
            return
        stamp = (when or dt.datetime.now()).isoformat(timespec="minutes")
        with self._lock:
            book = self.sites if kind == "site" else self.apps
            entry = book.setdefault(name, {"seen": 0})
            entry["seen"] += 1
            entry["last"] = stamp
            if kind == "site":
                entry["in"] = where
            else:
                entry["exe"] = exe.lower()
            self._dirty = True

    def seed_from_sessions(self, debug_dir: Path) -> int:
        """Learn from the windows Clixy was asked about before there was a map."""
        n = 0
        for log in sorted(Path(debug_dir).glob("*/interaction.log")):
            try:
                head = log.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
            except OSError:
                continue
            exe = next((line.split("APP:", 1)[1].strip() for line in head if "APP:" in line), "")
            title = next((line.split("WINDOW:", 1)[1].strip() for line in head if "WINDOW:" in line), "")
            try:
                when = dt.datetime.strptime(log.parent.name[:19], "%Y-%m-%d_%H-%M-%S")
            except ValueError:
                when = None
            if exe and title:
                self.see(exe, title, when)
                n += 1
        return n

    # --- Telling the models -------------------------------------------------------------------------

    def browser(self) -> str:
        """The browser most of your sites live in ("chrome"), "" if none seen."""
        counts: dict[str, int] = {}
        for site in self.sites.values():
            name = (site.get("in") or "").split()[0] if site.get("in") else ""
            if name:
                counts[name] = counts.get(name, 0) + site.get("seen", 0)
        if not counts:
            return ""
        best = max(counts, key=counts.get)
        others = {k: v for k, v in counts.items() if k != "edge"}
        # Edge often only shows up because links fell into Windows' default browser.
        return max(others, key=others.get) if best == "edge" and others else best

    def summary(self, top: int = 8) -> str:
        with self._lock:
            sites = sorted(self.sites.items(), key=lambda kv: -kv[1].get("seen", 0))[:top]
            apps = sorted(self.apps.items(), key=lambda kv: -kv[1].get("seen", 0))[:top]
        if not sites and not apps:
            return ""
        browser = self.browser()
        parts = []
        if browser:
            parts.append(f"he browses in {browser}" + (" (not edge)" if browser != "edge" else ""))
        if sites:
            def where(info: dict) -> str:
                place = info.get("in") or ""
                return f" ({place})" if place.endswith("app window") or (browser and not place.startswith(browser)) else ""
            parts.append("sites he uses, most first: " + ", ".join(f"{name}{where(info)}" for name, info in sites))
        if apps:
            parts.append("apps he uses: " + ", ".join(name for name, _ in apps))
        parts.append("a site of his that's open already: switch to its tab or window (in chrome, ctrl+shift+a, "
                     "type its name, enter) rather than opening it again; his sites open in his browser")
        return "; ".join(parts) + "."


# --- The one map, kept up to date while Clixy runs ----------------------------------------------------

_map: LaptopMap | None = None
_watching = False


def _path() -> Path:
    try:
        from config import MEMORY_DIR
        return Path(MEMORY_DIR) / "laptop.json"
    except Exception:
        return Path.home() / ".clixy" / "memory" / "laptop.json"


def _keeping() -> bool:
    try:
        from config import resolve_setting
        return resolve_setting("KEEP_HISTORY", default="on").lower() == "on"
    except Exception:
        return True


def current() -> LaptopMap:
    """The map, loaded (and seeded from past sessions the first time)."""
    global _map
    if _map is None:
        path = _path()
        fresh = not path.exists()
        _map = LaptopMap.load(path)
        if fresh and _keeping():
            try:
                from debug_log import _DEBUG_DIR
                if _map.seed_from_sessions(Path(_DEBUG_DIR)):
                    _map.save()
            except Exception:
                pass
    return _map


def summary() -> str:
    """The lines the models get; "" while history is off."""
    return current().summary() if _keeping() else ""


def watch(front, every_s: float = 20.0) -> None:
    """Note the window in front every ``every_s`` while Clixy runs (``front`` returns
    (exe, title)); saved every few minutes. Nothing is noted while history is off."""
    global _watching
    if _watching:
        return
    _watching = True

    def loop() -> None:
        last_save = time.monotonic()
        while True:
            time.sleep(every_s)
            if not _keeping():
                continue
            try:
                exe, title = front()
                current().see(exe, title)
                if time.monotonic() - last_save > 300:
                    current().save()
                    last_save = time.monotonic()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name="clixy-laptop").start()


def forget() -> None:
    """Forget history: start an empty map, not seeded again (the file goes with the memory folder)."""
    global _map
    _map = LaptopMap(_path())
