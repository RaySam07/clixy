"""Shared pytest fixtures."""
import gc

import pytest


@pytest.fixture(autouse=True)
def _debug_sessions_in_tmp(tmp_path, monkeypatch):
    """Per-interaction debug folders go to a temp dir, never the user's real
    one. Logging is on here so the tests exercise it; it is off by default."""
    import debug_log

    monkeypatch.setattr(debug_log, "_DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(debug_log, "_ENABLED", True)


@pytest.fixture(autouse=True)
def _no_real_audio(monkeypatch):
    """Tests never open a real microphone. On a machine with audio (the
    Windows CI runner) Settings dialogs that tests create and drop would
    leave PortAudio streams calling into collected objects, which can crash
    the run. Microphone tests pass their own fake sound system instead."""
    import mic

    def _no_audio():
        raise OSError("no real audio in tests")

    monkeypatch.setattr(mic, "_sounddevice", _no_audio)


@pytest.fixture(autouse=True)
def _no_real_sound_effects(tmp_path, monkeypatch):
    """Interface sounds (sfx.py) never reach the real speakers in tests, and
    their render cache goes to a temp dir. Tests that press the hotkey play
    "listen_start"; with no device to open, sfx quietly does nothing. Sound
    tests pass their own fake stream instead."""
    import sfx

    def _no_device(callback, finished):
        raise OSError("no real audio in tests")

    monkeypatch.setattr(sfx, "_open_device", _no_device)
    monkeypatch.setattr(sfx, "_CACHE_DIR", tmp_path / "sfx-cache")


@pytest.fixture(autouse=True)
def _no_ui_sounds(monkeypatch):
    """The panel's and notch's little sounds (ui_kit.sound) never play in tests."""
    import ui_kit

    monkeypatch.setattr(ui_kit, "SILENT", True)


@pytest.fixture(autouse=True)
def _dashboard_offline(tmp_path, monkeypatch):
    """The notch dashboard never reaches the network or the real timetable in tests."""
    import dashboard

    def _no_network(_url):
        raise OSError("no network in tests")

    monkeypatch.setattr(dashboard, "_fetch", _no_network)
    monkeypatch.setattr(dashboard, "TIMETABLE_FILE", tmp_path / "timetable.ics")
    monkeypatch.setattr(dashboard, "_state", {"snapshot": dashboard.Snapshot(), "weather_at": 0.0,
                                              "timetable_at": 0.0, "busy": False, "places": {}})


_QT_APP = []


@pytest.fixture(autouse=True)
def _keep_qt_app_alive():
    """Tests create the QApplication lazily in their own fixtures. Once one
    exists, keep it for the rest of the run: if it is garbage-collected
    while widgets from earlier tests still exist, Qt aborts when the
    collector later frees those widgets (seen on the Windows runner)."""
    yield
    if not _QT_APP:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            _QT_APP.append(app)


@pytest.fixture(autouse=True)
def _collect_between_tests():
    """Collect garbage only between tests. Left to itself the collector can
    run in the middle of a widget's paint and free Qt objects that earlier
    tests dropped, which aborts the run on the Windows runner."""
    gc.disable()
    yield
    gc.collect()
    gc.enable()


@pytest.fixture(autouse=True)
def _jobs_in_tmp(tmp_path, monkeypatch):
    """Finished jobs (jobs.py) are logged to a temp file, never the user's real
    ~/.clixy/jobs.jsonl, and the Up next line starts and ends empty."""
    import jobs

    monkeypatch.setattr(jobs, "log_path", lambda: tmp_path / "jobs.jsonl")
    jobs.clear_queue()
    yield
    jobs.clear_queue()


@pytest.fixture(autouse=True)
def _laptop_map_in_tmp(tmp_path, monkeypatch):
    """The laptop map lives in a temp folder, never the real memory folder, and starts empty."""
    import laptop
    monkeypatch.setattr(laptop, "_path", lambda: tmp_path / "memory" / "laptop.json")
    monkeypatch.setattr(laptop, "_map", None)
