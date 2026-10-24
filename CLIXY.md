# Clixy: my private Clicky

A personal build of [Clicky Windows](https://github.com/AbhishekVulla/clicky-windows)
with a new look, a side notch, pixel buddies and private defaults.

## Install on your laptop

1. Download this repo as a zip (or `git clone` it) and unzip it anywhere.
2. Double-click **`Install-Clicky.bat`**.

It finds Python 3.13 (and installs it with winget if it's missing), builds
`Clicky.exe`, installs it for your Windows user in
`%LOCALAPPDATA%\Programs\Clicky`, and adds Start menu and desktop
shortcuts. It asks whether Clicky should start when you sign in. Run it
again any time to update.

If Abhishek's "Clicky Windows" is installed, uninstall it first
(Settings > Apps) so two copies don't run at once. Your saved keys carry
over: both read Windows Credential Manager.

## Use it

Hold **Ctrl + Win**, ask out loud, let go. Your buddy flies to the thing
you asked about, a label pops up next to it, and the notch on the screen
edge shows the answer while Clicky says it. Change the shortcut in
Settings > General > Shortcut by pressing the keys you want.

## What's new

- **New look.** Ink, paper, butter and coral, set in Manrope Light. A new
  mark: a C with a notch bitten out of it.
- **Side notch.** A black island grows out of the screen edge like the
  Dynamic Island on its side: a sliver that peeks out when your mouse
  comes near, a capsule with a waveform while you talk, and a card with
  the answer as captions. Names of things to click show as butter chips.
  It springs between states in one fluid motion, and can live on either
  edge.
- **Five pixel buddies.** Ember (fire), Splash (water), Sprout (leaf),
  Mochi (cat) and Twinkle (star). Your buddy is the pointer, listens in
  the notch, tops every answer, and its colors run around the screen
  edge while you talk. Say hi to them in Settings > Buddies or switch
  from the tray.
- **First launch in three steps.** Paste your Claude key into a zone that
  checks itself, pick a buddy, pick a shortcut.
- **New Settings.** An ink rail with General, Buddies, Look, Privacy and
  Advanced. Your AI connection shows as a card with its status, key
  ending and cost. The shortcut, glow, notch side, history and buddy apply
  at once; only a new AI or voice provider needs a restart.
- **Ctrl + Win** instead of Ctrl + Alt + Space (which Luma uses). Clicky
  keeps the Start menu from opening when you let go.
- **Your buddy draws** (Settings > General > Draw on screen). It flies over
  and traces each mark with its tip: a snug loop around a button, a
  hand-drawn box around a chart, a highlighter over text, an underline or
  an arrow. Several marks get step numbers.
  - *Precise:* after answering, Clixy looks again at a close-up around
    each mark and asks for its exact box, and on Windows snaps buttons and
    menus to their real edges. A mark it can't find isn't drawn.
  - *Concise:* at most three marks, no giant circles, nothing that covers
    half the screen or repeats another mark.
  - *Readable labels:* each label tries the spots around its mark and picks
    the one clear of text, other marks, the screen edge and the notch, with
    a thin line back to its mark when it has to sit further away.
  - *Interactive:* move your mouse onto a marked thing and that mark says
    "got it" and fades. The rest fade a few seconds after Clixy finishes
    talking, and all vanish when you ask the next question.

## Private by default

- Keys stay on this PC, encrypted by Windows Credential Manager.
- Listening and the voice run on this PC (faster-whisper and Kokoro). They
  download once on first use (about 150 MB and 330 MB).
- Only your screenshot, your question and Clicky's notes for that app go
  to Claude, and only when you ask. Settings > Privacy shows where each
  thing goes.
- No question log on disk unless you set `DEBUG_LOG=on`.
- Settings > Privacy > Forget deletes Clicky's notes, their index and any
  logs. You can also turn history off.

## Cost

Claude Haiku 5.5: about $0.0003 a question (screenshot plus a short spoken
answer), so roughly 250,000 questions per $100. With drawing on, each mark's
close-up look adds about $0.0001-0.0002. The notch shows what each answer
cost (not counting the close-ups). An OpenRouter key (`sk-or-`) works too.

## Develop

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Tests: `python -m pytest`. `python tools/make_icon.py` redraws the EXE icon
and the logo from the mark (`python tools/make_icon.py mochi` uses a buddy).

## Credits

Built on [Clicky Windows](https://github.com/AbhishekVulla/clicky-windows) by
Abhishek Vulla (MIT), itself built on [Clicky](https://github.com/farzaa/clicky)
by Farza Majeed. Fonts: [Manrope](https://github.com/sharanda/manrope) and
[JetBrains Mono](https://github.com/JetBrains/JetBrainsMono), both under the
SIL Open Font License (`assets/fonts/OFL-*.txt`).
