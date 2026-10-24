# Clicky Windows: Cozy Edition

A personal build of [Clicky Windows](https://github.com/AbhishekVulla/clicky-windows)
with a pixel-art buddy, a side island, a glowing screen edge and private defaults.

## What's new

- **Five pixel buddies, each with its own color theme.** The buddy's tip is the
  pointer, so it lands on the exact pixel the model picked.

  | Buddy   | What it is          | Theme    |
  |---------|---------------------|----------|
  | Ember   | a cozy campfire     | Campfire |
  | Splash  | a calm water drop   | Lagoon   |
  | Sprout  | a fresh little leaf | Matcha   |
  | Mochi   | a sleepy black cat  | Midnight |
  | Twinkle | a dreamy star       | Dreamy   |

  Each one blinks and has faces for idle, listening, thinking and "found it!",
  and pops a little pixel burst when it lands. Switch instantly from the tray
  (right-click → Avatar) or in Settings. The theme recolors everything.
- **Side island.** A small tab on the right edge of the screen. Hold the hotkey
  and it springs open: level bars while you talk, your question and bobbing
  dots while it thinks, then the answer as captions while it speaks, with the
  time and cost of the answer. It tucks itself away after a few seconds.
- **Screen-edge glow.** The screen edges glow in your theme's colors while
  you talk, swelling with your voice, and dim while Clicky thinks.
- **Smoother and lighter.** The overlay repaints only the buddy's area instead
  of the whole screen 60 times a second; the island and the glow animate at 60 fps.

## Private by default

- **Listening and voice run on this PC** (faster-whisper and Kokoro). Your
  voice never leaves the machine. They download once on first use (about
  150 MB and 330 MB). Cartesia is still in Settings if you want the faster
  cloud voice.
- **Only your screenshot and question go to the AI you pick.** Keys stay in
  Windows Credential Manager, encrypted for your Windows account.
- **No question log on disk.** The per-question debug log is off unless you set
  `DEBUG_LOG=on` (for a bug report).
- **Tray → Forget My History...** deletes Clicky's notes about what you asked,
  their index and any debug logs. Your keys, settings and Knowledge Folder stay.

## Best brain for the money

Paste a Claude API key (`sk-ant-...`) in Settings; the AI model row switches
to Anthropic and uses **Claude Haiku 5.5**. It was the fork's fastest model
that still pointed 7/7, and one question (screenshot + short spoken answer)
costs about $0.0003-$0.0004. That's roughly 250,000 questions per $100. The
island shows the real cost of each answer.

## Run it

On Windows 10/11 with Python 3.13:

```powershell
git clone <this repo>
cd clicky-windows
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Hold **Ctrl+Alt+Space**, ask, let go.

## Build your own .exe

```powershell
pip install "pyinstaller>=6.20"
py -3.13 -m PyInstaller clicky.spec --noconfirm
```

The app is `dist\Clicky\Clicky.exe`. To change the .exe's own icon to another buddy, run
`python tools/make_icon.py mochi` before building.

## Credits

Built on [Clicky Windows](https://github.com/AbhishekVulla/clicky-windows) by
Abhishek Vulla (MIT), itself built on [Clicky](https://github.com/farzaa/clicky)
by Farza Majeed. Fonts: [Nunito](https://github.com/googlefonts/nunito) and
[Silkscreen](https://github.com/googlefonts/silkscreen), both under the SIL Open
Font License (`assets/fonts/OFL-*.txt`).
