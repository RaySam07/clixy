# Working on Clixy (local copy)

Clixy is Rayan Sameer's Windows AI buddy (PyQt6). This folder is the local dev copy.

## Rules
- **Never push to GitHub** and never add a remote. This copy stays on this laptop. The public
  repo (RaySam07/clixy) only gets changes when Rayan explicitly asks to ship one.
- **Never handle API keys.** They live in Windows Credential Manager (keyring). Don't read,
  print or paste them.
- `main` is the live app (v1.0.1). Build on `local-dev`. Commit locally as you go.

## Running it
- `tools\dev\Start-Clixy-Dev.bat` runs Clixy from this folder in dev mode. It restarts by
  itself within a couple of seconds whenever a watched file changes (`devmode.py`). A
  file that doesn't compile shows in the island instead.
- Tests: `.venv\Scripts\python -m pytest tests -q`.

## What's next
- `docs/plans/guide-mode.md`: exact marks from Windows UI Automation and guidance one
  step at a time. Built; verify it on real Windows first (UIA listing in `snap.py`, the
  pynput input watch in `guide.py`) and fix what doesn't hold up.
- The island is a notch at the top centre by default (`island.py`, side "top"): resting
  notch, "Hey <name>!" on start and once a day, a hint only when the mouse rests on it, a
  listening pill, and the answer as a drop-down card. Left and right edges are still in
  Settings. The 3D explainers were removed on purpose (too heavy); don't bring them back
  unless Rayan asks.
- Resting the mouse on the notch drops down the dashboard (`dashboard.py`): greeting, time,
  date, weather (Open-Meteo, city from Settings or the timetable's time zone) and today's
  classes from an iCal timetable (link or .ics file in Settings > General), on frosted glass.
  Rayan's timetable is a University of Birmingham Dubai MyTimetable export; never commit it.
- Brain: answers on Claude Sonnet 5.5 (thinking off for a fast first word); guide steps and
  sketch notes on Sonnet 5.5 with low-effort adaptive thinking (`ai.thinking_variant`).
  `apps.py` opens apps, sites and ms-settings pages ([OPEN:...]).
- Sketch notes (`sketch.py`): any learning question gets a hand-drawn note card (graphs,
  3D wireframes, steps, cycle, compare, timeline, mind map, key points) with follow-up
  buttons and Clear; notes are kept in ~/.clixy/notes. Formulas go through a whitelist
  evaluator in floating point; keep it that way.
