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
- 3D engine: after changing `explainer/web/src/`, run `npm install` and `npm run build` in
  `explainer/web`.

## What's next
- `docs/plans/guide-mode.md`: exact marks from Windows UI Automation, and guidance one
  step at a time. This comes first.
- `docs/plans/explainers.md`: 3D explainers (built). Next for them: the AI designs any topic.
