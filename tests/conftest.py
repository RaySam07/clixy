"""Shared pytest fixtures."""
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
