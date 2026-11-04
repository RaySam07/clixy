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
- 4 Nov 2026, the UI level-up ("black seems dull", "HeyClicky was very interactive"):
  `ui_kit.py` is the look and feel. Two skins: Daylight (default, bright) and Midnight
  (deep plum ink, never flat black), plus Match Windows (`APPEARANCE`). The buddy's colour
  is candy gel (`paint_gel`). Everything moves on springs (`Motion`, `Tactile`) and makes a
  little sound (`ui_kit.sound` -> `sfx.py`, original synthesised sounds; `SOUND_EFFECTS`,
  `SOUND_VOLUME`; never copy HeyClicky's sound files). The notch stays black (it is the
  notch) but has voice bars, hopping thinking dots, talking bars, a colour thread and sparks.
  Settings has nine sections with coloured tiles (General, Look, Sounds, Voice, Buddy,
  Shortcut, Brain, Privacy, About). A 3D note shows its live model in the panel; Open note
  brings back the real note card (not a picture). Check the look with
  `tools\dev\ui_shots.py OUT --skin light|dark` before calling UI work done.

## What's next
- Done 4 Nov 2026: the black notch with a buddy-coloured light (`island.py`), the panel
  (`panel.py`, `panel_settings.py`), smarter guide mode (below). Not yet tried by Rayan with
  a real click: the panel opening.
- Real 3D (night of 4 Nov 2026, after Rayan called the first 3D heart "balls and tubes"):
  `model3d.py` turns parts written as JSON (blended ellipsoids, tubes, lathes, extrusions,
  gears, cuts, shells, mirrors, repeats) into smooth meshes (signed distance fields, surface
  nets, ambient occlusion), cached in ~/.clixy/cache/3d; `render3d.py` draws them on the GPU
  (moderngl, offscreen OpenGL 3.3): physically based materials, soft shadows, labels on
  leader lines, drag to turn, scroll to zoom. Hand-built showpiece models live in
  `models3d/` (heart, engine, cells...) and Sonnet uses them by name; anything else it
  sculpts from parts. 3D maths (vectors, surfaces) is real 3D too. `scene3d.NoteModel` is
  only the fallback (no OpenGL, or old linked-part notes). The language is
  `docs/3d-models.md`; check any model by eye with `tools\dev\render_note.py`. His bar:
  premium 3D-app quality, never toy shapes.
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
- Listening: faster-whisper "auto" = small.en on the GPU (0.09 s; base.en misheard "draw me"
  as "don't be"), base.en on the CPU.
- Brain: answers on Claude Haiku 5.5 (thinking off, about twice as fast to the first word as
  Sonnet; Rayan chose this 4 Nov 2026); guide steps and
  sketch notes on Sonnet 5.5 with low-effort adaptive thinking (`ai.thinking_variant`).
  `apps.py` opens apps, sites and ms-settings pages ([OPEN:...]).
- Sketch notes (`sketch.py`): any learning question gets a hand-drawn note card (graphs,
  real 3D models (render3d, above), steps, cycle, compare, timeline, mind map, key points) with follow-up
  buttons and Clear; notes are kept in ~/.clixy/notes. Formulas go through a whitelist
  evaluator in floating point; keep it that way.
