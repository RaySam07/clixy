"""jobs.py: the Up next line and the log of finished jobs behind Done today."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

import guide
import jobs


@pytest.fixture(autouse=True)
def _empty_line():
    jobs.clear_queue()
    yield
    jobs.clear_queue()


def test_jobs_wait_in_line_and_start_in_order():
    jobs.enqueue("  clone   mum's voice ")
    jobs.enqueue("add a calendar panel")
    jobs.enqueue("   ")
    assert jobs.queued() == ["clone mum's voice", "add a calendar panel"]
    assert jobs.next_job() == "clone mum's voice"
    assert jobs.queued() == ["add a calendar panel"]
    jobs.clear_queue()
    assert jobs.next_job() is None


def test_done_today_is_todays_jobs_newest_first(tmp_path):
    log = tmp_path / "jobs.jsonl"
    now = dt.datetime(2026, 11, 4, 15, 0)
    jobs.record("open my display settings", did=False, steps=2, when=now - dt.timedelta(days=1), path=log)
    jobs.record("find flights to calicut", did=True, steps=3, when=now - dt.timedelta(hours=3), path=log)
    jobs.record("top 5 ai models", did=True, steps=1, when=now - dt.timedelta(hours=1), path=log)
    today = jobs.done_today(now=now, path=log)
    assert [j["goal"] for j in today] == ["top 5 ai models", "find flights to calicut"]
    assert today[0]["did"] is True and today[1]["steps"] == 3


def test_again_offers_each_recent_job_once(tmp_path):
    log = tmp_path / "jobs.jsonl"
    now = dt.datetime(2026, 11, 4, 15, 0)
    for hours, goal in ((200, "too old to offer"), (5, "Find flights to Calicut"), (3, "clone mum's voice"),
                        (1, "find flights to calicut.")):
        jobs.record(goal, did=True, steps=1, when=now - dt.timedelta(hours=hours), path=log)
    assert jobs.again(now=now, path=log) == ["find flights to calicut.", "clone mum's voice"]


def test_a_broken_or_missing_log_is_just_empty(tmp_path):
    log = tmp_path / "jobs.jsonl"
    assert jobs.done_today(path=log) == [] and jobs.again(path=log) == []
    log.write_text('not json\n{"goal": "x"}\n', encoding="utf-8")
    assert jobs.done_today(path=log) == []


def test_the_log_keeps_only_the_newest(tmp_path, monkeypatch):
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "_KEEP", 3)
    for i in range(5):
        jobs.record(f"job {i}", did=False, steps=1, path=log)
    assert [j["goal"] for j in jobs._load(log)] == ["job 2", "job 3", "job 4"]


def test_the_live_card_sees_the_target_in_the_screenshots_pixels():
    capture = SimpleNamespace(image="IMG", monitor={"left": 100, "top": 0}, scale_x=2.0, scale_y=2.0)
    assert guide._view_of(capture, (300, 40, 500, 80)) == ("IMG", (100.0, 20.0, 200.0, 40.0))
    assert guide._view_of(capture, None) == ("IMG", None)
    assert guide._view_of(SimpleNamespace(), (0, 0, 1, 1)) is None


def test_progress_tells_the_panel_everything_it_shows():
    nothing = lambda *a, **k: None
    g = guide.Guide(ask=lambda *a: iter(()), capture=nothing, list_elements=lambda m: [], show=nothing,
                    clear=nothing, say=nothing, note=nothing, cancelled=lambda: False, foreground=nothing)
    info = g.progress()
    for key in ("goal", "done", "now", "n", "total", "plan", "driving", "asking", "started", "view"):
        assert key in info
    assert info["driving"] is False and info["asking"] is False and g.drove is False
