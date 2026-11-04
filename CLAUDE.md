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
  `ui_kit.py` is the look and feel. Two skins: Daylight (default, bright) and Charcoal
  (charcoal greys with the same bright tiles, never flat black; `ui_kit.MIDNIGHT`), plus Match Windows (`APPEARANCE`). The buddy's colour
  is candy gel (`paint_gel`). Everything moves on springs (`Motion`, `Tactile`) and makes a
  little sound (`ui_kit.sound` -> `sfx.py`, original synthesised sounds; `SOUND_EFFECTS`,
  `SOUND_VOLUME`; never copy HeyClicky's sound files). The notch stays black (it is the
  notch) but has voice bars, hopping thinking dots, talking bars, a colour thread and sparks.
  Settings has nine sections with coloured tiles (General, Look, Sounds, Voice, Buddy,
  Shortcut, Brain, Privacy, About). Check the look with
  `tools\dev\ui_shots.py OUT --skin light|dark` before calling UI work done.
- 4 Nov 2026, Home is board J, "Live now, with what's waiting" (Rayan chose it from the
  proposal canvas, both skins): no note card on Home (drawings show on the screen when
  asked). While a job (a guide) runs, the big card shows the job, its steps (done, now,
  the next planned one), his screen with a ring on the next click, I did it or Take over,
  and Stop; a sunny tile is Your call when a step needs his yes, else who's clicking (tap
  to swap); the violet Up next lists jobs typed meanwhile (they wait their turn: `jobs.py`,
  started by the app when a job finishes; a job stopped short clears the line); today's
  done jobs and notes run along the bottom. When nothing runs: jobs to do again
  (`jobs.again`), Do it for me and Done today (a note there opens its card again). Done
  jobs are kept in ~/.clixy/jobs.jsonl only while history is on. The panel leaves the
  keyboard alone while a job runs, till he clicks the ask bar.

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
- 4 Nov 2026, "clixy must do a complete answer, from step 1 to whatever, carefully,
  intelligently, quick": any do-something request ("I want to access my YouTube Studio",
  "help me add a panel", "where's the export button") goes to guide mode, not only
  "how do I"; the guide stays till done (an empty press or "next" carries on, "I can't
  find it" makes it look again, only a new question ends it: `guide.during_guide`,
  `Guide.hear`, `app._guiding`); it plans the route ([PLAN:...]) and shows a planned click
  at once when that button is on screen (no model call), and checks each step worked.
- 4 Nov 2026, "a smooth humane interaction": a learning question that names a hand-built
  model shows it at once (`sketch.library_note`, no sketch call; cut open for "dissect" or
  "inside"); the voice is told a card is coming (`sketch.VOICE_HINT`) and never narrates
  the screen or says it can't show something; the live card's model builds below the
  voice's priority (Kokoro runs on the CPU).
- Guide mode (`guide.py`, `docs/plans/guide-mode.md`): speaks each step as soon as its
  sentence is in, waits for windows to settle, sees open windows (`snap.windows`, FOCUS,
  side by side), uses know-how packs (`knowhow/*.md`, extended by the KB folder) and
  remembers routes (`memory.RouteBook`). Admin windows like Task Manager hide their
  controls and Clixy's own hotkey while in front; that limit is real (needs uiAccess).
- The island (`island.py`, side "top"): a flat black tab with curved shoulders; "Hey <name>!"
  on start and once a day; Listening / Thinking with the light moving; a hint on hover;
  click opens the panel (Home, live while a job runs, and Settings). Left and right edges are
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
- 4 Nov 2026, "ultra quick, humane, made for you": his own logs (~/.clixy/debug) showed
  "for me, can you search ... on Chrome" never reached drive mode, and that the wait is
  mostly Kokoro (~0.6 s for 7 words, 1.5 s for 12, on the CPU). `guide._DO_TASK` now catches
  jobs said the natural way; searches open their results page straight away and the guide
  reads out what it finds. "show me how a piston works" stays a learning note. The voice
  starts on a short first phrase (`tts.first_phrase`, answers and guide steps alike).
  Faster still: Cartesia (his key, Settings -> Voice) or Kokoro on the RTX 4060 (needs
  onnxruntime-gpu plus CUDA 13 libraries, ~1 GB; not done).
- 4 Nov 2026, after his first real "do it for me" runs ("hesitates, talks way too much,
  too much lag; do it as I command, just show steps; better listening; more autonomous"):
  doing it, the guide says "on it." and then only the result (steps are shown, not told),
  only a click, key or real mouse sweep takes over (`take_over_moves`), drive steps after
  the first skip the thinking pass (`app._quick_ai`), a planned click on what was just
  clicked is skipped, links open in the browser the goal names (`apps.browser_named`), and
  a plain command is done for him (`guide.does_it`; "how do I / where's / help me" point).
  Listening: Whisper transcribes while he talks and reuses it when the rest is quiet
  (`stt._early_text`), with name hotwords; on the GPU it uses large-v3-turbo when it's
  already downloaded (`stt.GPU_EAR`, never fetched by itself), else small.en. Measured on
  his RTX 4060: turbo ~0.48 s vs small.en ~0.34 s per question, turbo hears "codecs",
  "top 5" and "Clixy" right.
- 4 Nov 2026, "not fully conquered GPT computer use": the answer model hands any job the
  phrase rules miss to Do it for me by replying [DO] (`app._DO_TAG_RE`; "go on to Hark and
  ask what's planned" had only opened Hark). The guide has more moves: [SCROLL:down:3]
  (`hands.scroll`), [WAIT:3] for a page or an AI's answer, and in drive mode sure moves
  chain in one reply ([CLICK][TYPE][KEYS:enter]) with no look between (`Guide._chain`,
  stopping at anything theirs_to_do). A question to an AI or search box isn't "sending".
- 4 Nov 2026, "it's late, doesn't keep up with my speed, so it lags and draws in the wrong
  places": what he does while a step is being decided is read first (`Guide._ahead`, from
  `InputWatch.pending`): a click on the very thing counts the step done (no mark), anything
  else means a fresh look before any mark or click; doing it, twice in a row hands it over.
  Every step after the first answers without thinking (`app._quick_ai`). Links open in the
  browser he uses (`guide.usual_browser`, not Edge when another is open). Whisper retries
  without the hints when it "hears" them (`stt._echoes`).
- 4 Nov 2026 (13:00), "not even able to go to WhatsApp or change tabs": the guide was fine
  (WhatsApp Web, tab switch, search and open a chat in 27 s); his jobs never reached it.
  Haiku ignored the [DO] rule ("i can't type the message to wasif for you"). Now a one-word
  do/show/answer verdict from the quick model runs alongside every answer (`app._intent`,
  text only, `_INTENT_PROMPT`); the first spoken word waits for it (<=1.5 s), a "do" goes
  to the hands, a sketch waits for it too. "How do I..." always shows, even naming a job.
  And when he clicks something else and the page changes, the guide follows him within
  ~0.5 s (`Guide._screen_moved`, outcome "moved") instead of waiting 40 s on step one.
- 4 Nov 2026, "it should know my laptop ins and outs": `laptop.py` keeps a map of what he
  uses, by name only ("(95) WhatsApp - Google Chrome" -> site WhatsApp in Chrome; Hark in a
  Chrome app window; apps Claude, VoiceStudio...). Learned from the window in front every
  20 s while Clixy runs, seeded once from past sessions, kept in the memory folder
  (laptop.json) only while history is on. Guide steps get it as "his laptop" (with: switch
  to an open tab, ctrl+shift+a, rather than opening again), answers get it with sysinfo,
  and links open in the browser it says he lives in. Tests keep it in tmp (conftest).
- 4 Nov 2026: in a window where messages go to people (`guide.talks_to_people`: WhatsApp,
  Outlook/Gmail/mail, Teams, Slack, Discord, Telegram...), Enter (or a typed new line) is
  "risky": doing it for him, Clixy types the message and asks "want me to send it?". Enter
  in a field named search/find there, or in an AI chat (Hark, ChatGPT), still goes through.
