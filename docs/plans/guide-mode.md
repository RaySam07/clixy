# Guide mode: exact marks, one step at a time

**Status:** built, first version (`guide.py`, `snap.elements`, `sysinfo.py`,
the `KEYS` keycaps mark, `island.step`). Tested with fakes; the UI Automation
listing and the input watch still need trying on real Windows. Run Clixy dev,
ask the "Try" questions below, and check the debug log's `GUIDE:` lines.

## The problem

- Marks land off target. The model guesses pixel coordinates from a shrunken
  screenshot; `aim.py` (a zoomed second look) and `snap.py` (UI Automation at
  that point) help, but they start from the guess, and a bad guess snaps to the
  wrong thing.
- How-to answers come all at once. "How do I project my screen?" needs a press,
  then a choice in a menu that isn't on screen yet, so the second mark can't
  be right when it's drawn.

## The fix, in two parts

### 1. Point at real elements, not pixels

Windows already knows the exact rectangle of every button, tab, menu item,
field and link (UI Automation, the same API screen readers use; Chromium
browsers expose web pages through it too).

- `snap.py` already runs UI Automation on its own COM thread. Add
  `elements(monitor, limit=150, budget_s=0.3)`. It returns
  `[{id, type, name, rect}]` for visible, enabled, on-screen interactive elements in:
  - the foreground window;
  - the taskbar (`Shell_TrayWnd`);
  - any open flyout or menu (Start, Quick Settings, the Win+P Project panel,
    context menus).

  Cache BoundingRectangle, Name, ControlType, IsOffscreen and IsEnabled in one
  CacheRequest. Drop zero-size, offscreen and duplicate entries.
- Map each rectangle into the screenshot's coordinate space (the capture's
  `scale_x`, `scale_y` and monitor offset, as `_annotations_to_physical`
  does in reverse).
- Send the list as text after the screenshot, one line each:
  `#7 button "Display settings" (412,88,530,112)`.
- The model answers with `[CLICK:#7:display settings]` rather than
  coordinates. The app looks up #7 and draws a box hugging the exact
  rectangle, so it is pixel-perfect.
- Fallback: a thing that isn't in the list (a canvas, a picture, a game) can
  still use `[BOX:x1,y1,x2,y2:label]`, which goes through `aim` and `snap` as today.
- If the model names an id that isn't in the list, retry once with "#N isn't
  on screen; pick from the list".

### 2. One step at a time (guide mode)

Trigger: navigation and how-to questions ("how do I…", "take me to…", "show
me how to…", "where do I turn on…"). Other questions answer as normal.

1. The guide prompt asks for **only the next step**: one short spoken
   sentence and one target, which is one of:
   - `[CLICK:#id:label]`
   - `[KEYS:win+p:label]` (a shortcut to press)
   - `[TYPE:text:#id]` (type into a field)

   The prompt also asks for a step estimate `[STEP:1/3]`, or `[DONE]` when
   the goal is reached.
2. Clixy shows that step:
   - a box, plus a numbered badge "1" beside it;
   - for `KEYS`, big keycaps near the bottom of the screen (the KeyCaps look
     from `widgets.py`);
   - the island reads "Step 1 · Click Display settings".
3. It waits for you to do the step:
   - **a click inside the target**, caught by a pynput mouse listener (observe
     only, like the hotkey), counted as inside if within 8 px;
   - **the shown keys pressed**, caught by a keyboard listener;
   - **nothing after 25 s:** a note says "Still there? Hold Ctrl+Win and say
     'next', or ask something new."
4. Then Clixy:
   - waits about 0.7 s for the screen to change;
   - captures again and lists the elements again;
   - asks for the next step with the goal, the steps done so far, the new
     screenshot and the list. That's one Haiku call per step, about 0.05¢.
5. It ends on `[DONE]` ("That's it, your screen is duplicated."), on Esc, or
   when you hold the hotkey and ask something else.

Clixy never clicks or types for you. It only shows the way.

Example, "how do I project my screen":
1. `[KEYS:win+p:project menu]` "press the windows key and p."
2. You press them and the Project panel opens.
3. The list now has Duplicate, Extend, Second screen only, and so on, so the
   model answers `[CLICK:#3:duplicate]` "pick duplicate to show the same
   thing on both."
4. You click it and Clixy answers `[DONE]`.

## Files

| File | Change |
|---|---|
| `snap.py` | `elements()`: the element list, on the UIA thread, with a time budget |
| `guide.py` (new) | the step state machine, mouse and keyboard listeners, timeouts |
| `annotations.py` | parse `CLICK`, `KEYS`, `TYPE`, `STEP`, `DONE` |
| `ai.py` | `_GUIDE_SYSTEM_PROMPT` and the element list in the user turn |
| `app.py` | route how-to questions to the guide, and keep the guide alive between steps |
| `overlay.py` / `drawing.py` | the step badge and the keycaps hint |

## Tests (with fakes, so they run anywhere)

- Element-list formatting, and mapping ids back to exact rectangles across
  DPI scales and monitors.
- Parsing every new tag, including a reply cut off mid-tag.
- The guide state machine with a fake AI and fake listeners:
  - step, wait, next, done;
  - a click outside the target doesn't count;
  - timeout;
  - Esc;
  - a bad id gets one retry;
  - a new question cancels the guide.

## Try on the laptop as it's built

- "how do I project my screen"
- "take me to display settings"
- "how do I change my wallpaper"
- "where do I turn on dark mode in Chrome"

## Faster and smarter navigation (4 Nov 2026)

From Rayan's "battery performance and CPU load" log: 3.7 s to the first word,
4 to 6 s a step, Task Manager "didn't show up" (it was behind Chrome, or still
drawing), and bouncing between Settings and Task Manager. Now:

- **Speaks sooner.** The step reply streams; each sentence is spoken the
  moment it's complete, before the tags arrive (`guide._EarlySpeech`,
  `Guide.speak`). `say(text, n, total, where)` still fires once per step.
- **Looks when the screen has settled** (`Guide._settle`), not after fixed
  sleeps: the window it opened is up and in front, or the front window changed
  and held still for 0.3 s, or 0.45 s passed with no change (a click inside the
  same window). Capped at 3 s. A window that opened behind is brought forward.
  The element list is made while the screenshot is taken (`Guide._look`).
- **Knows what's open.** `snap.windows()` lists the open windows front first
  (app, minimised, admin), resolving Store apps through their frame host.
  `[FOCUS:window]` brings one forward (`snap.focus`); `[OPEN:a][OPEN:b]` opens
  two and puts them side by side (`snap.side_by_side`).
- **Know-how packs** in `knowhow/` (windows, taskmgr, settings, explorer,
  chrome, edge, whatsapp, teams, outlook, word, excel, powerpoint, vscode,
  spotify, snipping). `kb.knowhow(goal, front_exe)` picks the packs the goal is
  about (or the app in front) and keeps only matching bullets. A file in the KB
  folder named after the app's .exe goes first; `replace: yes` drops the pack.
- **OPEN can open more**: Windows tools by run name (`taskmgr`, `msinfo32`,
  `devmgmt.msc`...), `control /name ...`, app links (`spotify:search:...`,
  `ms-screenclip:`, `mailto:`), folders (`shell:startup`, `%temp%`) and
  `battery report` (powercfg's battery health report, no admin needed).
- **Remembers routes** (`memory.RouteBook`, `~/.clixy/memory/guide-routes.json`,
  only when KEEP_HISTORY is on). A similar goal gets "last time this worked: ...";
  `[FASTER:win+i]` on the last step is kept as the quick way (`progress()["shortcut"]`).

Admin windows: Task Manager often runs elevated. Windows then hides its
controls (UIA) and the user's clicks and keys (pynput) from Clixy, and the
push-to-talk hotkey too while it's in front. The prompt points with a box and
finishes with `[DONE]`; the last mark stays up 8 s; a step there nudges after 12 s.

Still to try live: `snap.focus` (AttachThreadInput + SwitchToThisWindow) and
`snap.side_by_side` on real windows, including an elevated Task Manager.
