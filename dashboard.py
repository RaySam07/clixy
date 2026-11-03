"""The notch's dashboard: the time and date and the weather where you are.
Rest the mouse on the notch and it drops down. (It no longer shows classes:
Rayan asked for the timetable to go, 4 Nov 2026.)

Weather comes from Open-Meteo (free, no account): only the city name and its
coordinates are sent. With no city set, the time zone of a timetable picked
in Settings (an .ics kept as ~/.clixy/timetable.ics) gives one ("Asia/Dubai"
is Dubai). Reading a timetable is still here for Settings' import check.

Threading: fetching runs on a background thread; the island reads the last
snapshot on the Qt thread.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from config import APP_HOME

TIMETABLE_FILE = APP_HOME / "timetable.ics"
WEATHER_EVERY_S = 15 * 60
DAYS_AHEAD = 14


@dataclass(frozen=True)
class Klass:
    """One class: when, what, and where."""
    start: dt.datetime
    end: dt.datetime
    title: str
    kind: str = ""     # Lecture, Lab, Tutorial
    room: str = ""


@dataclass(frozen=True)
class Weather:
    temp: float
    code: int
    place: str
    high: float | None = None
    low: float | None = None

    @property
    def text(self) -> str:
        return describe(self.code)[0]

    @property
    def icon(self) -> str:
        return describe(self.code)[1]


@dataclass
class Snapshot:
    weather: Weather | None = None
    city: str = ""


# --- Reading a timetable -----------------------------------------------------------

_GENERIC = re.compile(r"(?i)^\s*(?:\d+\s*/\s*)?(lecture|tutorial|seminar|workshop|class|lab(?:oratory)?"
                      r"(?: practicals?)?|practicals?|\d+)\s*$")
_LEVEL = re.compile(r"^(?:L[CIHM]|L\d)\s+")   # UK module levels: "LI Artificial Intelligence 2"
_KINDS = {"laboratory": "Lab", "lab practicals": "Lab", "lab": "Lab", "lecture": "Lecture",
          "tutorial": "Tutorial", "seminar": "Seminar", "workshop": "Workshop"}


def class_title(summary: str, description: str = "") -> str:
    """What the class is called: the summary, unless it's just "Lecture" or a
    code, then the module name from the description."""
    summary = (summary or "").strip()
    module = re.search(r"Module description:\s*(.+?)\s*(?:\n|$)", description or "")
    name = module.group(1) if module and (not summary or _GENERIC.match(summary)) else summary
    return _LEVEL.sub("", name).strip() or "Class"


def class_kind(description: str = "", summary: str = "") -> str:
    found = re.search(r"Type:\s*([A-Za-z ]+?)\s*(?:\n|$)", description or "")
    word = (found.group(1) if found else "").strip().lower()
    if not word:
        generic = _GENERIC.match(summary or "")
        word = (generic.group(1) if generic else "").lower()
    return _KINDS.get(word, word.title() if word and not word.isdigit() else "")


def short_room(location: str) -> str:
    """"1211/1211A - Seminar Room" -> "Seminar Room 1211/1211A"."""
    location = " ".join((location or "").split())
    if " - " in location:
        code, name = location.split(" - ", 1)
        return f"{name} {code}".strip()
    return location


def parse_timetable(data: bytes, start: dt.datetime, days: int = DAYS_AHEAD) -> list[Klass]:
    """The classes from ``start`` for ``days`` days, in local time, in order.
    Repeating classes (weekly timetables) are expanded."""
    import icalendar
    import recurring_ical_events

    tz = start.tzinfo or dt.datetime.now().astimezone().tzinfo
    calendar = icalendar.Calendar.from_ical(data)
    out: list[Klass] = []
    for event in recurring_ical_events.of(calendar).between(start, start + dt.timedelta(days=days)):
        begin, finish = event.get("DTSTART"), event.get("DTEND")
        if begin is None:
            continue
        begin = begin.dt
        if not isinstance(begin, dt.datetime):
            continue  # all-day entries (holidays, deadlines) aren't classes
        finish = finish.dt if finish is not None else begin + dt.timedelta(hours=1)
        if begin.tzinfo is None:
            begin, finish = begin.replace(tzinfo=tz), finish.replace(tzinfo=tz)
        summary = str(event.get("SUMMARY", ""))
        description = str(event.get("DESCRIPTION", ""))
        out.append(Klass(begin.astimezone(tz), finish.astimezone(tz), class_title(summary, description),
                         class_kind(description, summary), short_room(str(event.get("LOCATION", "")))))
    out.sort(key=lambda k: k.start)
    return out


def city_from_calendar(data: bytes) -> str:
    """The city in the calendar's time zone ("Asia/Dubai" -> "Dubai")."""
    found = re.search(rb"^(?:X-WR-TIMEZONE|TZID):\s*([A-Za-z_]+/([A-Za-z_]+))\s*$", data or b"", re.M)
    return found.group(2).decode().replace("_", " ") if found else ""


def import_timetable(path: str | Path) -> int:
    """Use this .ics file as the timetable. Returns how many classes it has
    in the coming weeks; raises ValueError when it isn't a calendar."""
    data = Path(path).read_bytes()
    if b"BEGIN:VCALENDAR" not in data[:4096]:
        raise ValueError("That file isn't a calendar (.ics).")
    try:
        count = len(parse_timetable(data, _start_of_today(), days=120))
    except Exception as exc:
        raise ValueError("That calendar couldn't be read.") from exc
    TIMETABLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, TIMETABLE_FILE)
    refresh(force=True)  # its time zone may name the city for the weather
    return count


# --- What to show ----------------------------------------------------------------------

def clock(moment: dt.datetime | dt.time) -> str:
    """A time the way this PC shows times (12 or 24 hour)."""
    try:
        from PyQt6.QtCore import QLocale, QTime
        return QLocale.system().toString(QTime(moment.hour, moment.minute), QLocale.FormatType.ShortFormat)
    except Exception:
        return f"{moment.hour:02d}:{moment.minute:02d}"


def describe(code: int) -> tuple[str, str]:
    """A WMO weather code as words and an icon name."""
    if code == 0:
        return "Clear", "sun"
    if code in (1, 2):
        return "Partly cloudy", "partly"
    if code == 3:
        return "Cloudy", "cloud"
    if code in (45, 48):
        return "Foggy", "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return ("Drizzle" if code < 60 else "Rain"), "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "Snow", "snow"
    if code >= 95:
        return "Thunderstorms", "storm"
    return "Weather", "cloud"


def parse_weather(forecast: dict, place: str) -> Weather:
    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}

    def first(name):
        values = daily.get(name) or []
        return float(values[0]) if values and values[0] is not None else None

    return Weather(float(current["temperature_2m"]), int(current.get("weather_code", 3)), place,
                   first("temperature_2m_max"), first("temperature_2m_min"))


# --- Fetching, in the background -------------------------------------------------------

_lock = threading.Lock()
_state: dict = {"snapshot": Snapshot(), "weather_at": 0.0, "busy": False, "places": {}}


def snapshot() -> Snapshot:
    with _lock:
        return _state["snapshot"]


def refresh(force: bool = False) -> None:
    """Fetch the weather if it's stale, on a background thread; never waits."""
    with _lock:
        if _state["busy"]:
            return
        if not force and time.monotonic() - _state["weather_at"] <= WEATHER_EVERY_S:
            return
        _state["busy"] = True
    threading.Thread(target=_refresh, args=(force,), name="clixy-dashboard", daemon=True).start()


def _refresh(force: bool) -> None:
    try:
        snap = Snapshot(**vars(snapshot()))
        now = time.monotonic()
        if force or now - _state["weather_at"] > WEATHER_EVERY_S or snap.weather is None:
            city = _setting("WEATHER_CITY") or city_from_calendar(_file_bytes())
            snap.city = city
            if city:
                try:
                    snap.weather = _weather(city)
                except Exception:
                    pass  # offline: keep the last weather
            with _lock:
                _state["weather_at"] = now
        with _lock:
            _state["snapshot"] = snap
    finally:
        with _lock:
            _state["busy"] = False


def _file_bytes() -> bytes:
    try:
        return TIMETABLE_FILE.read_bytes()
    except OSError:
        return b""


def _weather(city: str) -> Weather:
    places = _state["places"]
    if city not in places:
        found = _fetch_json("https://geocoding-api.open-meteo.com/v1/search?"
                            + urllib.parse.urlencode({"name": city, "count": 1}))
        result = (found.get("results") or [None])[0]
        if not result:
            raise LookupError(f"no place called {city}")
        places[city] = (result["latitude"], result["longitude"], result.get("name", city))
    lat, lon, name = places[city]
    forecast = _fetch_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min", "timezone": "auto", "forecast_days": 1,
    }))
    return parse_weather(forecast, name)


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Clixy"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return response.read(2_000_000)


def _fetch_json(url: str) -> dict:
    return json.loads(_fetch(url).decode("utf-8"))


def _setting(name: str) -> str:
    try:
        from config import resolve_setting
        return resolve_setting(name, default="") or ""
    except Exception:
        return ""


def _start_of_today() -> dt.datetime:
    now = dt.datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
