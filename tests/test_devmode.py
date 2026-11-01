"""Dev mode: restart when the code changes, but only once edits settle, the
code compiles, and Clixy isn't busy."""
from __future__ import annotations

import os

import pytest

import devmode


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(devmode.time, "monotonic", lambda: now[0])
    return now


def _touch(path, text=None):
    if text is not None:
        path.write_text(text, encoding="utf-8")
    stamp = path.stat().st_mtime + 5
    os.utime(path, (stamp, stamp))


def _watcher(root, **kwargs):
    calls = {"change": 0, "errors": []}
    w = devmode.Watcher(root, on_change=lambda: calls.__setitem__("change", calls["change"] + 1),
                        on_error=calls["errors"].append, interval_ms=10_000_000, **kwargs)
    return w, calls


def test_only_on_when_asked_and_running_from_source(monkeypatch):
    monkeypatch.delenv("CLIXY_DEV", raising=False)
    assert not devmode.enabled()
    monkeypatch.setenv("CLIXY_DEV", "1")
    assert devmode.enabled()
    monkeypatch.setattr(devmode.sys, "frozen", True, raising=False)
    assert not devmode.enabled()


def test_restarts_once_edits_settle(qapp, tmp_path, clock):
    app_py = tmp_path / "app.py"
    app_py.write_text("x = 1\n", encoding="utf-8")
    w, calls = _watcher(tmp_path)
    _touch(app_py, "x = 2\n")
    w.poll()
    clock[0] += devmode.SETTLE_S / 2
    w.poll()
    assert calls["change"] == 0, "still settling"
    clock[0] += devmode.SETTLE_S
    w.poll()
    assert calls["change"] == 1


def test_waits_while_busy(qapp, tmp_path, clock):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    busy = [True]
    w, calls = _watcher(tmp_path, busy=lambda: busy[0])
    _touch(tmp_path / "app.py", "x = 2\n")
    w.poll()
    clock[0] += 10
    w.poll()
    assert calls["change"] == 0
    busy[0] = False
    w.poll()
    assert calls["change"] == 1


def test_broken_code_is_reported_not_loaded(qapp, tmp_path, clock):
    (tmp_path / "island.py").write_text("x = 1\n", encoding="utf-8")
    w, calls = _watcher(tmp_path)
    _touch(tmp_path / "island.py", "def oops(:\n")
    w.poll()
    clock[0] += 10
    w.poll()
    assert calls["change"] == 0
    assert calls["errors"] and "island.py line 1" in calls["errors"][0]
    _touch(tmp_path / "island.py", "def fixed():\n    pass\n")  # fixing it reloads
    w.poll()
    clock[0] += 10
    w.poll()
    assert calls["change"] == 1


def test_tests_and_docs_dont_restart_it(qapp, tmp_path, clock):
    (tmp_path / "tests").mkdir()
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_x.py"
    test_file.write_text("", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("", encoding="utf-8")
    w, calls = _watcher(tmp_path)
    _touch(test_file, "x = 1\n")
    _touch(readme, "# hi")
    w.poll()
    clock[0] += 10
    w.poll()
    assert calls["change"] == 0
    _touch(tmp_path / "app.py", "x = 2\n")
    w.poll()
    clock[0] += 10
    w.poll()
    assert calls["change"] == 1


def test_restart_lets_go_of_the_lock_and_starts_the_same_command(monkeypatch):
    started = []
    monkeypatch.setattr(devmode.subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    monkeypatch.setattr(devmode.sys, "argv", ["app.py"])
    devmode.restart(None)
    assert started and started[0][0] == [devmode.sys.executable, "app.py"]


def test_app_is_busy_while_the_key_is_held(mocker):
    from app import ClickyApp
    app = ClickyApp(ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(), tts_client=mocker.MagicMock(),
                    memory_store=mocker.MagicMock(), overlay_controller=mocker.MagicMock(),
                    hotkey_instance=mocker.MagicMock())
    assert not app.busy()
    app._holding = True
    assert app.busy()
