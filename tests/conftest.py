"""Shared pytest fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _debug_sessions_in_tmp(tmp_path, monkeypatch):
    """Per-interaction debug folders go to a temp dir, never the user's real
    one. Logging is on here so the tests exercise it; it is off by default."""
    import debug_log

    monkeypatch.setattr(debug_log, "_DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(debug_log, "_ENABLED", True)
