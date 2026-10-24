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
