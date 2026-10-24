"""Clicky Windows global push-to-talk hotkey (default Ctrl+Win, configurable).

Installs a low-level Windows keyboard hook via pynput in OBSERVE-ONLY mode
(`suppress=False`) and fires on_press / on_release callbacks when the
configured combo becomes active / breaks. The combo is a "+"-joined list of
keys: modifiers (ctrl, alt, shift, win), space, a letter or digit, or an F
key, e.g. "ctrl+win" or "ctrl+alt+space". Settings records it by having you
press it; config.HOTKEY holds it.

Why Ctrl+Win by default:

- **Ctrl+Alt+Space** was the old default. Other apps claim it (Claude
  Desktop's quick prompt, Luma) and, because this listener is observe-only,
  both apps would react.
- **Ctrl+Win** is a two-key hold that no Windows shortcut uses by itself
  (Win+Ctrl+D or arrows need a third key) and that dictation apps such as
  Wispr Flow use on Windows. One catch: Windows opens Start when the Win key
  is released if no other key was pressed while it was down. When the combo
  includes Win, the hotkey taps an unassigned virtual key (0xE8) the moment
  the combo completes, which tells Windows the Win key was used in a chord,
  so Start stays shut. AutoHotkey uses the same "mask key" trick.
- **Alt+Space alone** is the window menu (and Copilot on Windows 11).
- **Ctrl+Shift+Space** selects the whole sheet in Excel and Google Sheets.
- **Fn+Space** never reaches Windows: the laptop's controller eats Fn.

`suppress=False` matters: pynput's suppress flag is global, all or nothing.
Setting it to True would block EVERY key event system-wide, not just ours.

Callbacks run on the pynput listener thread, NOT the Qt main thread. app.py
wires them to pyqtSignal.emit, which is thread-safe by design (Qt marshals
across threads).

The `__main__` block stays last so `py -3.13 -m hotkey` works.
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Callable, Optional

from pynput import keyboard


DEFAULT_COMBO = "ctrl+win"

_ALIASES = {
    "control": "ctrl", "ctl": "ctrl",
    "windows": "win", "cmd": "win", "meta": "win", "super": "win", "start": "win",
    "option": "alt", "opt": "alt",
    "spacebar": "space",
}
_MODIFIER_KEYS = {
    "ctrl": (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r),
    # AltGr counts as Alt so international layouts work.
    "alt": (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr),
    "shift": (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r),
    "win": (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r),
    "space": (keyboard.Key.space,),
}
_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "space": "Space"}
_ORDER = ("ctrl", "alt", "shift", "win")

MASK_VK = 0xE8
"""Unassigned virtual key tapped while Win is held, so releasing Win doesn't
open Start."""


def parse_combo(combo: str) -> tuple[str, ...]:
    """'Ctrl + Win' -> ('ctrl', 'win'). Unknown names raise ValueError."""
    tokens = []
    for raw in combo.lower().replace(" ", "").split("+"):
        if not raw:
            continue
        token = _ALIASES.get(raw, raw)
        valid = (
            token in _MODIFIER_KEYS
            or (len(token) == 1 and token.isalnum())
            or (token.startswith("f") and token[1:].isdigit() and 1 <= int(token[1:]) <= 24)
        )
        if not valid:
            raise ValueError(f"unknown key {raw!r} in hotkey {combo!r}")
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        raise ValueError("empty hotkey")
    # Modifiers first, in a fixed order, so equal combos compare equal.
    return tuple(sorted(tokens, key=lambda t: (_ORDER.index(t) if t in _ORDER else len(_ORDER), t)))


def format_combo(tokens: tuple[str, ...]) -> str:
    """('ctrl', 'win') -> 'ctrl+win' (the stored form)."""
    return "+".join(tokens)


def describe(combo: str) -> str:
    """'ctrl+win' -> 'Ctrl + Win' (what people read)."""
    try:
        tokens = parse_combo(combo)
    except ValueError:
        return combo
    return " + ".join(_LABELS.get(t, t.upper()) for t in tokens)


# --- State enum --------------------------------------------------------------

class HotkeyState(Enum):
    """Two-state push-to-talk state machine.

    IDLE:      waiting for every key of the combo to be held.
    RECORDING: every key of the combo is held. Audio capture is live. When
               any of them is released, go back to IDLE and fire on_release.
    """

    IDLE = "idle"
    RECORDING = "recording"


# --- PushToTalkHotkey --------------------------------------------------------

class PushToTalkHotkey:
    """Global push-to-talk hotkey for any combo, non-suppressing.

    Tracks each key of the combo independently so any press order works:
    IDLE -> RECORDING once all are held. Releasing any of them while
    RECORDING immediately fires on_release() and returns to IDLE, clearing
    every flag. This matches real-world PTT UX: the moment the combo breaks,
    stop recording.

    Thread model: pynput installs a low-level Windows keyboard hook on its
    own thread and invokes our handlers from that thread. A small
    threading.Lock guards the flags because start()/stop() can be called
    from the main thread while a handler runs.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        listener_class=None,
        combo: str = DEFAULT_COMBO,
        injector=None,
    ) -> None:
        """Wire the hotkey to caller callbacks.

        Args:
            on_press:       fired once when the combo becomes active. Runs on
                            the pynput listener thread.
            on_release:     fired once when the combo breaks. Listener thread.
            listener_class: DI hook for tests: factory for the keyboard
                            listener. Defaults to pynput.keyboard.Listener.
            combo:          e.g. "ctrl+win" (see parse_combo).
            injector:       DI hook for tests: object with press()/release()
                            used for the Start-menu mask key. Defaults to a
                            pynput keyboard Controller, created on first use.
        """
        self._on_press_cb = on_press
        self._on_release_cb = on_release
        self._listener_class = listener_class or keyboard.Listener
        self._injector = injector

        self.tokens = parse_combo(combo)
        self.combo = format_combo(self.tokens)
        self._lock = threading.Lock()
        self._down: dict[str, bool] = {t: False for t in self.tokens}
        self._state: HotkeyState = HotkeyState.IDLE

        self._listener = None  # set in start(), cleared in stop()

    @property
    def state(self) -> HotkeyState:
        """Current state machine position. Thread-safe read."""
        with self._lock:
            return self._state

    # Per-key flags, read by tests and debugging. False for keys not in the combo.
    @property
    def _ctrl_down(self) -> bool:
        return self._down.get("ctrl", False)

    @property
    def _alt_down(self) -> bool:
        return self._down.get("alt", False)

    @property
    def _space_down(self) -> bool:
        return self._down.get("space", False)

    @property
    def _win_down(self) -> bool:
        return self._down.get("win", False)

    # --- public lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Install the low-level Windows keyboard hook with suppress=False.

        Idempotent: calling start() twice is a no-op after the first.
        The listener runs on its own thread; this returns immediately.
        """
        with self._lock:
            if self._listener is not None:
                return  # already started -- idempotent

            self._listener = self._listener_class(
                on_press=self._handle_press,
                on_release=self._handle_release,
                suppress=False,
            )
            self._listener.start()

    def stop(self) -> None:
        """Uninstall the hook and release the listener thread. Idempotent."""
        listener = None
        with self._lock:
            if self._listener is None:
                return  # already stopped -- idempotent
            listener = self._listener
            self._listener = None
        # Call stop() outside the lock so pynput can join its own thread
        # without deadlocking on a handler that's mid-flight waiting for us.
        try:
            listener.stop()
        except Exception:
            # Best-effort: if pynput's teardown raises (e.g. already stopped
            # internally), don't bubble it up -- stop() is idempotent.
            pass

    # --- key-event handlers (invoked by pynput on listener thread) ----------

    def _token_for(self, key) -> str | None:
        """Which combo key (if any) this pynput key is. Left/right variants
        count as the same key."""
        for token in self.tokens:
            if token in _MODIFIER_KEYS:
                if key in _MODIFIER_KEYS[token]:
                    return token
            elif token.startswith("f") and len(token) > 1:
                if key == getattr(keyboard.Key, token, None):
                    return token
            else:
                # Letters and digits: compare the virtual-key code, since the
                # character changes while Ctrl is held ('a' arrives as '\x01').
                vk = getattr(key, "vk", None)
                if vk is not None and vk == ord(token.upper()):
                    return token
                char = getattr(key, "char", None)
                if char is not None and char.lower() == token:
                    return token
        return None

    def _handle_press(self, key) -> Optional[bool]:
        """Low-level key-down handler called by pynput on its listener thread."""
        fire_press = False
        with self._lock:
            token = self._token_for(key)
            if token is not None:
                self._down[token] = True
            if self._state == HotkeyState.IDLE and all(self._down.values()):
                self._state = HotkeyState.RECORDING
                fire_press = True

        if fire_press:
            if "win" in self.tokens:
                self._mask_start_menu()
            # Fire callbacks OUTSIDE the lock so a slow on_press doesn't
            # block concurrent state reads from other threads.
            self._on_press_cb()
        return None  # pynput convention: None = propagate (we're suppress=False anyway)

    def _handle_release(self, key) -> Optional[bool]:
        """Low-level key-up handler called by pynput on its listener thread."""
        fire_release = False
        with self._lock:
            token = self._token_for(key)
            if token is not None:
                if self._state == HotkeyState.RECORDING:
                    fire_release = True
                    self._state = HotkeyState.IDLE
                    for t in self._down:
                        self._down[t] = False
                else:
                    self._down[token] = False

        if fire_release:
            self._on_release_cb()
        return None

    def _mask_start_menu(self) -> None:
        """Tap an unassigned key while Win is down so releasing Win doesn't
        open Start. Best-effort: a failure only means Start may open."""
        try:
            if self._injector is None:
                self._injector = keyboard.Controller()
            mask = keyboard.KeyCode.from_vk(MASK_VK)
            self._injector.press(mask)
            self._injector.release(mask)
        except Exception:
            pass


# --- Manual verification entry point ----------------------------------------

if __name__ == "__main__":
    # Run: py -3.13 -m hotkey [combo]
    # Hold the combo (default Ctrl+Win) to trigger PRESSED, release any key of
    # it for RELEASED. Check that typing in other apps still works
    # (suppress=False) and that Start does NOT open when you let go of Win.
    import sys
    import time

    combo = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_COMBO

    print("=" * 70)
    print(f"Clicky Windows -- hotkey.py manual verification ({describe(combo)})")
    print("=" * 70)
    print("\nInstructions:")
    print(f"  1. Hold {describe(combo)} -- you should see >>> PRESSED within 50ms")
    print("  2. Release any key of it -- you should see >>> RELEASED within 50ms")
    print("  3. With Win in the combo: Start must NOT open when you let go")
    print("  4. Type 'hello world' in Notepad -- typing MUST still work")
    print("  5. Ctrl+C in this terminal to quit")
    print()

    hk = PushToTalkHotkey(
        on_press=lambda: print("  >>> PRESSED (recording started)"),
        on_release=lambda: print("  >>> RELEASED (recording stopped)"),
        combo=combo,
    )
    hk.start()
    print(f"Listener started. Waiting for {describe(combo)}...\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        hk.stop()
        print("\nListener stopped. Exiting.")
