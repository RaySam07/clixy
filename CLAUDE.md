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
- The Python environment is `%USERPROFILE%\.venvs\clixy`, not `.venv`: Windows' 260-character
  path limit is on and breaks PyQt6 under a long project path.
- Tests: `%USERPROFILE%\.venvs\clixy\Scripts\python -m pytest tests -q`.
- When Rayan asks for a new zip: `tools\dev\Make-Clixy-Zip.bat` builds Clixy.exe and saves
  `Downloads\Clixy-<date>-<letter>.zip`; `--install` also replaces the installed app in
  `%LOCALAPPDATA%\Programs\Clixy` (only when he asks to install).

## Rayan's design rules (4 Nov 2026)
- No separate app window: everything lives in the notch and the panel that grows out of it,
  Settings included. Never show the text of what Clixy says; answers are only spoken.
- Ember (or the chosen buddy) stays; its colour (`theme.current_palette().flood`) is the one
  accent: the notch's light, the panel's glossy buttons. No bubble pill, nothing kiddish,
  no flat grey-on-black "slop"; tactile and warm (paper cards, keycaps, glossy primary).
- No timetable. Show only real features; leave out anything with no setting behind it.

## What's next
- Done 4 Nov 2026: the black notch with a buddy-coloured light (`island.py`), the panel
  (`panel.py`, `panel_settings.py`), real 3D on the note card (`scene3d.NoteModel`), and
  smarter guide mode (below). Not yet tried by Rayan with a real click: the panel opening.
- Next: "Do it for me" (Clixy acting in apps; see the end of `docs/plans/guide-mode.md`),
  the task-doing brain (ask Rayan which connectors first), a VoiceStudio voice option.
- Guide mode (`guide.py`, `docs/plans/guide-mode.md`): speaks each step as soon as its
  sentence is in, waits for windows to settle, sees open windows (`snap.windows`, FOCUS,
  side by side), uses know-how packs (`knowhow/*.md`, extended by the KB folder) and
  remembers routes (`memory.RouteBook`). Admin windows like Task Manager hide their
  controls and Clixy's own hotkey while in front; that limit is real (needs uiAccess).
- The island (`island.py`, side "top"): a flat black tab with curved shoulders; "Hey <name>!"
  on start and once a day; Listening / Thinking with the light moving; a hint on hover;
  click opens the panel (Home, Working while guiding, Settings). Left and right edges are
  still in Settings. `dashboard.py` is greeting, time, date and weather only.
- Brain: answers on Claude Haiku 5.5 (thinking off, about twice as fast to the first word as
  Sonnet; Rayan chose this 4 Nov 2026); guide steps and
  sketch notes on Sonnet 5.5 with low-effort adaptive thinking (`ai.thinking_variant`).
  `apps.py` opens apps, sites and ms-settings pages ([OPEN:...]).
- Sketch notes (`sketch.py`): any learning question gets a hand-drawn note card (graphs,
  solid lit 3D models (scene3d.NoteModel, wireframe fallback), steps, cycle, compare, timeline, mind map, key points) with follow-up
  buttons and Clear; notes are kept in ~/.clixy/notes. Formulas go through a whitelist
  evaluator in floating point; keep it that way.
