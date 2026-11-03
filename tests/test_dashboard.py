"""The notch dashboard: the time and the weather (classes are no longer shown;
reading a timetable stays for Settings' import and the weather city)."""
from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest

import dashboard

DUBAI = zoneinfo.ZoneInfo("Asia/Dubai")

# A made-up timetable in the shape a school exports (Eveoh MyTimetable): classes
# named only "Lecture" or "12345/lecture", the real name inside, times in UTC.
ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Timetable//EN
X-WR-TIMEZONE:Asia/Dubai
BEGIN:VEVENT
UID:a@test
DTSTART:20261102T080000Z
DTEND:20261102T110000Z
SUMMARY:Lecture
LOCATION:1211/1211A - Seminar Room
DESCRIPTION:Type: Lecture\\n\\nModule code: 11111\\n\\nModule description: LI Fluid Dynamics\\n\\nLocation(s):\\n1211
END:VEVENT
BEGIN:VEVENT
UID:b@test
DTSTART:20261102T110000Z
DTEND:20261102T140000Z
SUMMARY:22222/lecture
LOCATION:0411 - IT Cluster (PC Lab)
DESCRIPTION:Type: Laboratory\\n\\nModule description: LI Circuits 1
END:VEVENT
BEGIN:VEVENT
UID:c@test
DTSTART:20261103T060000Z
DTEND:20261103T080000Z
SUMMARY:LI Linear Algebra 2 - Group B
LOCATION:0401 - Main Lecture Theatre
DESCRIPTION:Type: Tutorial
END:VEVENT
BEGIN:VEVENT
UID:d@test
DTSTART;TZID=Asia/Dubai:20261104T090000
DTEND;TZID=Asia/Dubai:20261104T100000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Robotics Club
END:VEVENT
BEGIN:VEVENT
UID:e@test
DTSTART;VALUE=DATE:20261105
SUMMARY:Reading week
END:VEVENT
END:VCALENDAR
"""


def _classes(days=14):
    return dashboard.parse_timetable(ICS, dt.datetime(2026, 11, 2, tzinfo=DUBAI), days=days)


def test_classes_get_their_real_names_rooms_and_kinds():
    ks = _classes()
    first = ks[0]
    assert (first.title, first.kind, first.room) == ("Fluid Dynamics", "Lecture", "Seminar Room 1211/1211A")
    assert first.start == dt.datetime(2026, 11, 2, 12, 0, tzinfo=DUBAI)   # 08:00 UTC is noon in Dubai
    assert (ks[1].title, ks[1].kind) == ("Circuits 1", "Lab")
    assert ks[2].title == "Linear Algebra 2 - Group B" and ks[2].kind == "Tutorial"


def test_weekly_classes_repeat_and_all_day_entries_dont_count():
    titles = [k.title for k in _classes(days=21)]
    assert titles.count("Robotics Club") == 3
    assert "Reading week" not in titles


@pytest.mark.parametrize("summary, description, want", [
    ("Lecture", "Module description: LI Statics", "Statics"),
    ("40856/lecture", "Module description: LH Design 1", "Design 1"),
    ("LI Artificial Intelligence 2", "Module description: LI Something else", "Artificial Intelligence 2"),
    ("Chemistry", "", "Chemistry"),
    ("", "", "Class"),
])
def test_class_titles(summary, description, want):
    assert dashboard.class_title(summary, description) == want


def test_the_city_comes_from_the_timetable():
    assert dashboard.city_from_calendar(ICS) == "Dubai"
    assert dashboard.city_from_calendar(b"X-WR-TIMEZONE:America/New_York\n") == "New York"
    assert dashboard.city_from_calendar(b"") == ""


def test_weather_from_open_meteo():
    w = dashboard.parse_weather({"current": {"temperature_2m": 31.4, "weather_code": 1},
                                 "daily": {"temperature_2m_max": [33.0], "temperature_2m_min": [26.2]}}, "Dubai")
    assert (round(w.temp), w.text, w.icon, w.place, w.high, w.low) == (31, "Partly cloudy", "partly", "Dubai", 33.0, 26.2)
    assert dashboard.describe(95)[1] == "storm" and dashboard.describe(63)[0] == "Rain"


def test_importing_a_timetable_keeps_a_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "_start_of_today", lambda: dt.datetime(2026, 11, 1, tzinfo=DUBAI))
    monkeypatch.setattr(dashboard, "refresh", lambda force=False: None)
    source = tmp_path / "timetable_2026-11-01.ics"
    source.write_bytes(ICS)
    assert dashboard.import_timetable(source) >= 5
    assert dashboard.TIMETABLE_FILE.read_bytes() == ICS
    bad = tmp_path / "notes.ics"
    bad.write_text("hello")
    with pytest.raises(ValueError):
        dashboard.import_timetable(bad)


def test_offline_keeps_going(monkeypatch):
    monkeypatch.setattr(dashboard, "_setting", lambda name: "Dubai" if name == "WEATHER_CITY" else "")
    dashboard._refresh(force=True)  # no network in tests: no weather, no crash
    assert dashboard.snapshot().weather is None and dashboard.snapshot().city == "Dubai"


def test_the_timetable_is_never_fetched_but_still_names_the_city(monkeypatch):
    fetched = []
    monkeypatch.setattr(dashboard, "_fetch", lambda url: fetched.append(url) or b"")
    monkeypatch.setattr(dashboard, "_setting", lambda name: "https://example.test/tt.ics" if name == "TIMETABLE_URL" else "")
    dashboard.TIMETABLE_FILE.write_bytes(ICS)
    dashboard._refresh(force=True)
    assert not any("tt.ics" in url for url in fetched), "the timetable link is left alone"
    assert dashboard.snapshot().city == "Dubai"
    assert not hasattr(dashboard.snapshot(), "classes")


# --- In the notch ------------------------------------------------------------------------

@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_resting_on_the_notch_opens_the_dashboard(qapp, monkeypatch):
    from island import SideIsland
    monkeypatch.setattr(dashboard, "refresh", lambda force=False: None)
    monkeypatch.setattr(dashboard, "_state", {"snapshot": dashboard.Snapshot(
        weather=dashboard.Weather(31, 1, "Dubai", 33, 26), city="Dubai"),
        "weather_at": 0.0, "busy": False, "places": {}})
    notch = SideIsland(side="top")
    try:
        notch._hello_until = 0
        notch._near = True
        w, h = notch._target()
        assert w == notch.DASH_W and 120 < h < 200, "the hello, the time and the weather: no classes"
        for _ in range(150):
            notch._update_near = lambda: None
            notch._animate()
        assert notch._fade_dash > 0.9
        notch._glass = None
        notch.grab()
        from PyQt6.QtGui import QImage
        backdrop = QImage(notch.width(), notch.height(), QImage.Format.Format_ARGB32_Premultiplied)
        backdrop.fill(0xFF3366CC)
        notch._glass = __import__("island").frost(backdrop)
        notch.grab()  # on frosted glass
        notch.dashboard = False
        assert notch._target() == notch.HINT
    finally:
        notch.close()


def test_settings_has_the_timetable_and_city(qapp, mocker):
    mocker.patch("settings_dialog.keyring.get_password", return_value=None)
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    try:
        assert dlg._tt_row is not None and dlg._city is not None
        assert "notch" in dlg._tt_row.sub.text()
    finally:
        dlg.close()
