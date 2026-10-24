"""Tests for debug_log: where per-interaction debug folders are written."""
from pathlib import Path


def test_debug_sessions_never_write_to_the_real_home_folder(tmp_path, monkeypatch):
    """The suite must not create folders in the user's real ~/.clixy.
    Home points at a sandbox, so a session that ignored the redirect would
    write there and fail the check instead of touching the real folder."""
    import debug_log

    sandbox_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox_home))
    session = debug_log.DebugSession.start("TEST.EXE", "Window")
    session.log("hello")
    session.close()
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written, "the session wrote nothing"
    assert all(Path(debug_log._DEBUG_DIR) in p.parents for p in written), written
    assert not sandbox_home.exists()


def test_screenshots_are_not_saved_unless_turned_on(monkeypatch):
    from PIL import Image
    import debug_log
    monkeypatch.setattr(debug_log, "_SAVE_SCREENSHOTS", False)
    session = debug_log.DebugSession.start("TEST.EXE", "Window")
    session.save_screenshot(Image.new("RGB", (8, 8)), "shot.jpg")
    session.close()
    assert not list(session._folder.glob("*.jpg"))


def test_screenshots_are_saved_when_turned_on(monkeypatch):
    from PIL import Image
    import debug_log
    monkeypatch.setattr(debug_log, "_SAVE_SCREENSHOTS", True)
    session = debug_log.DebugSession.start("TEST.EXE", "Window")
    session.save_screenshot(Image.new("RGB", (8, 8)), "shot.jpg")
    session.close()
    assert (session._folder / "shot.jpg").exists()


def test_nothing_is_written_when_the_log_is_off(tmp_path, monkeypatch):
    """Off by default: no folder, no file, and logging calls are harmless."""
    from PIL import Image
    import debug_log
    monkeypatch.setattr(debug_log, "_ENABLED", False)
    monkeypatch.setattr(debug_log, "_SAVE_SCREENSHOTS", True)
    session = debug_log.DebugSession.start("TEST.EXE", "Window")
    session.log("what the user asked")
    session.save_screenshot(Image.new("RGB", (8, 8)), "shot.jpg")
    session.close()
    assert not any(tmp_path.rglob("*.log")) and not any(tmp_path.rglob("*.jpg"))
