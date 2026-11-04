"""Clixy's jobs, for the panel's Home: what's waiting to start (Up next) and what
got done (Done today, and the "again" suggestions when nothing is running).

Up next is a line kept in memory: a job typed into the panel while another runs
waits here and starts when that one finishes. Stop clears it. Done jobs are kept
in ~/.clixy/jobs.jsonl, one line each, only while Settings > Privacy keeps history.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

_lock = threading.Lock()
_line: list[str] = []
_KEEP = 300                      # done jobs kept on disk (the oldest go first)


# --- Up next ---------------------------------------------------------------------------------------

def enqueue(job: str) -> None:
    job = " ".join((job or "").split())
    if job:
        with _lock:
            _line.append(job)


def queued() -> list[str]:
    with _lock:
        return list(_line)


def next_job() -> str | None:
    """The next job in line, taken off it; None when the line is empty."""
    with _lock:
        return _line.pop(0) if _line else None


def clear_queue() -> None:
    with _lock:
        _line.clear()


# --- Done --------------------------------------------------------------------------------------------

def log_path() -> Path:
    try:
        from config import APP_HOME
        return Path(APP_HOME) / "jobs.jsonl"
    except Exception:
        return Path.home() / ".clixy" / "jobs.jsonl"


def record(goal: str, *, did: bool, steps: int, when: dt.datetime | None = None, path: Path | None = None) -> None:
    """A finished job: ``did`` when Clixy did the clicking, ``steps`` how many it took."""
    goal = " ".join((goal or "").split())
    if not goal:
        return
    path = path or log_path()
    line = json.dumps({"goal": goal, "did": bool(did), "steps": int(steps),
                       "at": (when or dt.datetime.now()).isoformat(timespec="seconds")})
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            lines = (lines + [line])[-_KEEP:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass


def _load(path: Path | None = None) -> list[dict]:
    path = path or log_path()
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        try:
            job = json.loads(raw)
            job["at"] = dt.datetime.fromisoformat(job["at"])
            out.append(job)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def done_today(now: dt.datetime | None = None, path: Path | None = None) -> list[dict]:
    """Today's finished jobs, newest first: {"goal", "did", "steps", "at"}."""
    today = (now or dt.datetime.now()).date()
    return sorted((j for j in _load(path) if j["at"].date() == today), key=lambda j: j["at"], reverse=True)


def again(n: int = 3, days: int = 7, now: dt.datetime | None = None, path: Path | None = None) -> list[str]:
    """Jobs from the last ``days`` days worth offering again, newest first, each once."""
    since = (now or dt.datetime.now()) - dt.timedelta(days=days)
    seen, out = set(), []
    for job in sorted(_load(path), key=lambda j: j["at"], reverse=True):
        key = job["goal"].lower().rstrip(".!? ")
        if job["at"] >= since and key not in seen:
            seen.add(key)
            out.append(job["goal"])
            if len(out) == n:
                break
    return out
