"""Unit tests for hotkey.py.

All tests are mock-based with zero real keyboard hook installation.
Mirrors the DI-mock pattern from tests/test_overlay.py: PushToTalkHotkey
accepts a `listener_class` parameter so tests inject a MagicMock factory
instead of pynput.keyboard.Listener.

The state-machine tests use the old Ctrl+Alt+Space combo (all 3 must be
held for RECORDING; any release ends it); TestCombos covers the default
Ctrl+Win, the Start-menu mask key and other combos. suppress=False means the listener
observes but never consumes keys, so global typing still works.

Not Ctrl+Shift+Space: Excel + Google Sheets bind it to "Select entire
worksheet". The hotkey.py module docstring covers the other alternatives.

What's NOT tested here (manual verification gate only):
- Actual Windows low-level hook installation
- suppress=False actually NOT blocking global typing
- Excel "Select entire worksheet" NOT firing on Ctrl+Alt+Space
- Callback latency (<50ms)
- Windows window menu NOT opening on the combo (only Alt+Space alone opens it)
All of the above are verified by `py -3.13 -m hotkey`.
"""
from unittest.mock import MagicMock

from pynput.keyboard import Key


def test_hotkey_module_importable():
    import hotkey  # noqa: F401


class TestStateMachine:
    """Tests for the IDLE <-> RECORDING state-machine transitions.

    Each test constructs a fresh PushToTalkHotkey with MagicMock listener_class
    (never called here -- we don't invoke start()), then calls _handle_press /
    _handle_release directly to drive the transitions the listener thread
    would normally trigger. This isolates the state logic from pynput itself.

    The combo is Ctrl+Alt+Space: all 3 must be held together to transition
    to RECORDING. Any release of any of the 3 while RECORDING ends it.
    """

    def _make_hk(self):
        """Helper: fresh PushToTalkHotkey with mock callbacks + mock listener."""
        from hotkey import PushToTalkHotkey
        on_press = MagicMock()
        on_release = MagicMock()
        hk = PushToTalkHotkey(
            on_press=on_press,
            on_release=on_release,
            listener_class=MagicMock(),
            combo="ctrl+alt+space",
        )
        return hk, on_press, on_release

    def test_ctrl_alt_space_in_order_fires_on_press(self):
        """Typical order: Ctrl, then Alt, then Space. on_press fires once
        on the space-down transition (the last key of the combo), state
        becomes RECORDING."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.alt)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.space)
        assert on_press.call_count == 1
        assert on_release.call_count == 0
        assert hk.state == HotkeyState.RECORDING

    def test_space_then_ctrl_then_alt_also_fires_on_press(self):
        """Reverse order: Space first, then Ctrl, then Alt. Still valid --
        all 3 down means RECORDING, regardless of press order. Tests the
        order-independence of the state machine."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.space)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.ctrl)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.alt)
        assert on_press.call_count == 1
        assert hk.state == HotkeyState.RECORDING

    def test_alt_gr_is_treated_as_alt(self):
        """AltGr (Alt_Gr) on international keyboards must be recognized as
        the alt modifier. Without this, international users pressing
        Ctrl+AltGr+Space would see no Clicky response."""
        from hotkey import HotkeyState
        hk, on_press, _ = self._make_hk()

        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt_gr)
        hk._handle_press(Key.space)
        assert on_press.call_count == 1
        assert hk.state == HotkeyState.RECORDING

    def test_release_space_while_recording_fires_on_release(self):
        """From RECORDING, releasing Space must fire on_release exactly once
        and return to IDLE. All 3 flags cleared per spec."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        # Drive into RECORDING
        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt)
        hk._handle_press(Key.space)
        assert hk.state == HotkeyState.RECORDING

        hk._handle_release(Key.space)
        assert on_release.call_count == 1
        assert hk.state == HotkeyState.IDLE
        # All 3 flags cleared per spec
        assert hk._ctrl_down is False
        assert hk._alt_down is False
        assert hk._space_down is False

    def test_release_ctrl_while_recording_also_fires_on_release(self):
        """Releasing Ctrl (not Space) while RECORDING also ends recording.
        Any of the 3 keys going up is enough to break the combo."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt)
        hk._handle_press(Key.space)
        assert hk.state == HotkeyState.RECORDING

        hk._handle_release(Key.ctrl)
        assert on_release.call_count == 1
        assert hk.state == HotkeyState.IDLE

    def test_ctrl_alone_does_nothing(self):
        """Pressing Ctrl alone from IDLE: no callbacks, state stays IDLE,
        but _ctrl_down flag goes True so a subsequent Alt+Space can
        complete the combo."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        assert on_press.call_count == 0
        assert on_release.call_count == 0
        assert hk.state == HotkeyState.IDLE
        assert hk._ctrl_down is True
        assert hk._alt_down is False
        assert hk._space_down is False

    def test_ctrl_alt_without_space_does_not_fire(self):
        """Pressing Ctrl+Alt but NOT Space should NOT fire on_press --
        the combo is incomplete without Space. Guards against the bug
        where the state machine accidentally transitions on 2 of 3 keys."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE
        assert hk._ctrl_down is True
        assert hk._alt_down is True
        assert hk._space_down is False

    def test_non_hotkey_key_ignored(self):
        """Pressing an unrelated key (Enter) from IDLE: nothing happens at all.
        No callbacks, no state change, no flag set. The state machine only
        cares about Ctrl, Alt, and Space."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.enter)
        assert on_press.call_count == 0
        assert on_release.call_count == 0
        assert hk.state == HotkeyState.IDLE
        assert hk._ctrl_down is False
        assert hk._alt_down is False
        assert hk._space_down is False


class TestStartLifecycle:
    """Tests for start() listener construction via injected listener_class."""

    def test_start_creates_listener_with_suppress_false(self):
        """start() must instantiate the injected listener_class exactly once
        with suppress=False (pynput's suppress is all-or-nothing
        global, so suppress=True would disable ALL typing globally).

        on_press and on_release handlers must be wired to self._handle_press
        / self._handle_release."""
        from hotkey import PushToTalkHotkey
        fake_listener_instance = MagicMock()
        fake_listener_class = MagicMock(return_value=fake_listener_instance)

        hk = PushToTalkHotkey(
            on_press=MagicMock(),
            on_release=MagicMock(),
            listener_class=fake_listener_class,
        )
        hk.start()

        assert fake_listener_class.call_count == 1
        kwargs = fake_listener_class.call_args.kwargs
        assert kwargs["suppress"] is False, (
            "suppress MUST be False for Ctrl+Alt+Space -- pynput suppress=True "
            "is globally destructive and blocks all typing."
        )
        assert kwargs["on_press"] == hk._handle_press
        assert kwargs["on_release"] == hk._handle_release
        fake_listener_instance.start.assert_called_once()

    def test_start_is_idempotent(self):
        """Calling start() twice must only construct one listener. Prevents
        double-hook installation which pynput would silently accept but
        would then deliver every key event twice."""
        from hotkey import PushToTalkHotkey
        fake_listener_instance = MagicMock()
        fake_listener_class = MagicMock(return_value=fake_listener_instance)

        hk = PushToTalkHotkey(
            on_press=MagicMock(),
            on_release=MagicMock(),
            listener_class=fake_listener_class,
        )
        hk.start()
        hk.start()

        assert fake_listener_class.call_count == 1
        assert fake_listener_instance.start.call_count == 1



class TestCombos:
    """Configurable combos: the default Ctrl+Win, parsing, letters."""

    def _make(self, combo="ctrl+win"):
        from hotkey import PushToTalkHotkey
        on_press, on_release, injector = MagicMock(), MagicMock(), MagicMock()
        hk = PushToTalkHotkey(on_press=on_press, on_release=on_release,
                              listener_class=MagicMock(), combo=combo, injector=injector)
        return hk, on_press, on_release, injector

    def test_default_is_ctrl_win(self):
        from hotkey import DEFAULT_COMBO, PushToTalkHotkey
        assert DEFAULT_COMBO == "ctrl+win"
        hk = PushToTalkHotkey(on_press=MagicMock(), on_release=MagicMock(), listener_class=MagicMock())
        assert hk.tokens == ("ctrl", "win")

    def test_ctrl_win_in_either_order(self):
        from hotkey import HotkeyState
        for first, second in ((Key.ctrl_l, Key.cmd), (Key.cmd_r, Key.ctrl_r)):
            hk, on_press, on_release, _ = self._make()
            hk._handle_press(first)
            assert on_press.call_count == 0
            hk._handle_press(second)
            assert on_press.call_count == 1 and hk.state == HotkeyState.RECORDING
            hk._handle_release(Key.cmd)
            assert on_release.call_count == 1 and hk.state == HotkeyState.IDLE

    def test_win_combo_taps_the_mask_key_so_start_stays_shut(self):
        from hotkey import MASK_VK
        hk, _, _, injector = self._make()
        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.cmd)
        pressed = injector.press.call_args.args[0]
        assert pressed.vk == MASK_VK
        injector.release.assert_called_once()

    def test_combos_without_win_never_inject(self):
        hk, _, _, injector = self._make("ctrl+alt+space")
        for key in (Key.ctrl, Key.alt, Key.space):
            hk._handle_press(key)
        injector.press.assert_not_called()

    def test_space_alone_does_not_fire_ctrl_win(self):
        hk, on_press, _, _ = self._make()
        hk._handle_press(Key.space)
        hk._handle_press(Key.ctrl)
        assert on_press.call_count == 0

    def test_letter_combo_matches_by_virtual_key(self):
        from pynput.keyboard import KeyCode
        hk, on_press, _, _ = self._make("ctrl+shift+k")
        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.shift)
        hk._handle_press(KeyCode.from_vk(ord("K")))
        assert on_press.call_count == 1

    def test_parse_normalizes_names_and_order(self):
        from hotkey import parse_combo, describe
        assert parse_combo("Win + Control") == ("ctrl", "win")
        assert parse_combo("ctrl+alt+space") == ("ctrl", "alt", "space")
        assert describe("win+ctrl") == "Ctrl + Win"
        assert describe("ctrl+f9") == "Ctrl + F9"

    def test_parse_rejects_unknown_keys(self):
        import pytest
        from hotkey import parse_combo
        with pytest.raises(ValueError):
            parse_combo("ctrl+banana")
        with pytest.raises(ValueError):
            parse_combo("")
