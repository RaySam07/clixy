"""Tests for the new UI: Settings pages, first launch and the shared controls."""
from __future__ import annotations

import pytest

ANT_KEY = "sk-ant-api03-" + "k" * 80 + "3f9a"
OR_KEY = "sk-or-v1-" + "r" * 60


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def stored(monkeypatch):
    """Keyring as a dict, and no API keys or settings from the environment."""
    values: dict[str, str] = {}
    for name in ("LLM_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                 "GEMINI_API_KEY", "CARTESIA_API_KEY", "MODEL_ID", "HOTKEY", "AVATAR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("keyring.get_password", lambda s, n: values.get(n))
    monkeypatch.setattr("keyring.set_password", lambda s, n, v: values.update({n: v}))
    yield values
    import theme
    theme.set_avatar(theme.DEFAULT_AVATAR, persist=False)


# --- Shared controls -----------------------------------------------------------

def test_key_shapes():
    from widgets import classify_key
    assert classify_key(ANT_KEY)[0] == "anthropic"
    assert classify_key(OR_KEY)[0] == "openrouter"
    assert classify_key("sk-ant-short")[0] is None
    assert classify_key("hello")[0] is None
    assert classify_key("") == (None, "")


def test_qt_keys_map_to_hotkey_tokens():
    from PyQt6.QtCore import Qt
    from widgets import token_for_qt_key
    assert token_for_qt_key(Qt.Key.Key_Control) == "ctrl"
    assert token_for_qt_key(Qt.Key.Key_Meta) == "win"
    assert token_for_qt_key(Qt.Key.Key_K) == "k"
    assert token_for_qt_key(Qt.Key.Key_F9) == "f9"
    assert token_for_qt_key(Qt.Key.Key_Escape) is None


def test_keycaps_record_a_two_key_combo(qapp):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from widgets import KeyCaps
    caps = KeyCaps("ctrl+win")
    seen = []
    caps.recorded.connect(seen.append)
    caps.record()

    def key(kind, code):
        return QKeyEvent(kind, code, Qt.KeyboardModifier.NoModifier)

    caps.keyPressEvent(key(QEvent.Type.KeyPress, Qt.Key.Key_Alt))
    caps.keyPressEvent(key(QEvent.Type.KeyPress, Qt.Key.Key_K))
    caps.keyReleaseEvent(key(QEvent.Type.KeyRelease, Qt.Key.Key_K))
    caps.keyReleaseEvent(key(QEvent.Type.KeyRelease, Qt.Key.Key_Alt))
    assert seen == ["alt+k"] and caps.combo == "alt+k"


def test_keycaps_ignore_a_single_key(qapp):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from widgets import KeyCaps
    caps = KeyCaps("ctrl+win")
    caps.record()
    caps.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier))
    caps.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier))
    assert caps.combo == "ctrl+win"


def test_paste_zone_reports_the_key_kind(qapp):
    from widgets import PasteZone
    zone = PasteZone()
    zone.setText("nope")
    assert zone.kind is None
    zone.setText(ANT_KEY)
    assert zone.kind == "anthropic" and zone.text() == ANT_KEY


# --- Settings -------------------------------------------------------------------

def test_settings_has_five_pages_and_buddies_say_hi(qapp, stored):
    from PyQt6.QtWidgets import QLabel
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    assert list(dlg._pages) == ["general", "buddies", "look", "privacy", "advanced"]
    texts = [label.text() for label in dlg.findChildren(QLabel)]
    assert "Say hi to your buddies" in texts
    assert set(dlg._avatar_cards) == {"ember", "splash", "sprout", "mochi", "twinkle"}


def test_a_pasted_claude_key_lands_in_the_ai_row(qapp, stored):
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    assert dlg._paste_box.isVisibleTo(dlg)  # no key yet: the paste zone shows
    dlg._paste.setText(ANT_KEY)
    assert dlg._key_inputs["LLM"].text() == ANT_KEY
    assert dlg._dropdowns["LLM"].currentData() == "anthropic"
    dlg._after_paste()
    assert dlg._conn_card.isVisibleTo(dlg) and not dlg._paste_box.isVisibleTo(dlg)


def test_the_connection_card_shows_claude_and_the_key_ending(qapp, stored):
    stored.update({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ANT_KEY})
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    model, key, cost, local = dlg._connection()
    assert "Claude" in model and "Haiku 5.5" in model
    assert key == ANT_KEY and "$0.0003" in cost and not local
    assert dlg._conn_card.key.text().endswith("3f9a")


def test_voice_switch_picks_both_providers(qapp, stored):
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    assert dlg._voice.value() == "local"
    dlg._on_voice_changed("cloud")
    assert dlg._dropdowns["STT"].currentData() == "cartesia"
    assert dlg._dropdowns["TTS"].currentData() == "cartesia"
    dlg._on_voice_changed("local")
    assert dlg._dropdowns["STT"].currentData() == "faster-whisper"
    assert dlg._dropdowns["TTS"].currentData() == "kokoro"


def test_look_changes_save_without_a_restart(qapp, stored):
    stored.update({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ANT_KEY,
                   "STT_PROVIDER": "faster-whisper", "TTS_PROVIDER": "kokoro", "ANNOTATION_MODE": "off"})
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    dlg._glow.setChecked(False)
    dlg._side.set_value("left")
    dlg._keep_history.setChecked(False)
    dlg._on_hotkey_recorded("ctrl+shift+k")
    dlg._on_avatar_picked("mochi")
    dlg._on_save()
    assert stored["EDGE_GLOW"] == "off" and stored["ISLAND_SIDE"] == "left"
    assert stored["KEEP_HISTORY"] == "off" and stored["HOTKEY"] == "ctrl+shift+k"
    assert stored["AVATAR"] == "mochi"
    assert not dlg.needs_restart()


def test_a_new_provider_needs_a_restart(qapp, stored):
    stored.update({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ANT_KEY,
                   "STT_PROVIDER": "faster-whisper", "TTS_PROVIDER": "kokoro"})
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    dlg._on_voice_changed("cloud")
    dlg._key_inputs["TTS"].setText("cartesia-key")
    dlg._on_save()
    assert stored["TTS_PROVIDER"] == "cartesia" and dlg.needs_restart()


def test_cancel_puts_the_old_buddy_back(qapp, stored):
    import theme
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    dlg._on_avatar_picked("twinkle")
    assert theme.current_avatar_id() == "twinkle"
    dlg.reject()
    assert theme.current_avatar_id() == theme.DEFAULT_AVATAR
    assert "AVATAR" not in stored


def test_privacy_says_where_things_go(qapp, stored):
    stored.update({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ANT_KEY})
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    assert dlg._where["key"].text() == "stays on this PC"
    assert dlg._where["voice"].text() == "stays on this PC"
    assert dlg._where["ai"].text() == "goes to Claude"
    dlg._on_voice_changed("cloud")
    assert dlg._where["voice"].text() == "goes to Cartesia"


# --- First launch -------------------------------------------------------------------

def test_a_new_install_needs_the_welcome(stored):
    from onboarding import needs_onboarding
    assert needs_onboarding()
    stored["ANTHROPIC_API_KEY"] = ANT_KEY
    assert not needs_onboarding()


def test_ollama_needs_no_welcome(stored):
    from onboarding import needs_onboarding
    stored["LLM_PROVIDER"] = "ollama"
    assert not needs_onboarding()


def test_welcome_waits_for_a_real_looking_key(qapp, stored):
    from onboarding import Onboarding
    dlg = Onboarding()
    assert not dlg._next_key.isEnabled()
    dlg.paste.setText("not a key")
    assert not dlg._next_key.isEnabled()
    dlg.paste.setText(ANT_KEY)
    assert dlg._next_key.isEnabled()


def test_welcome_saves_claude_local_voice_buddy_and_shortcut(qapp, stored):
    from settings_dialog import required_keys_present
    from onboarding import Onboarding
    dlg = Onboarding()
    dlg.paste.setText(OR_KEY)
    dlg.go(1)
    dlg.cards["sprout"].click()
    dlg.go(2)
    dlg._on_recorded("ctrl+alt+k")
    dlg.finish()
    assert dlg.result() == dlg.DialogCode.Accepted
    assert stored["ANTHROPIC_API_KEY"] == OR_KEY and stored["LLM_PROVIDER"] == "anthropic"
    assert stored["STT_PROVIDER"] == "faster-whisper" and stored["TTS_PROVIDER"] == "kokoro"
    assert stored["AVATAR"] == "sprout" and stored["HOTKEY"] == "ctrl+alt+k"
    assert required_keys_present()


def test_welcome_keeps_a_voice_already_chosen(qapp, stored):
    stored.update({"STT_PROVIDER": "assemblyai"})
    from onboarding import Onboarding
    dlg = Onboarding()
    dlg.paste.setText(ANT_KEY)
    dlg.finish()
    assert stored["STT_PROVIDER"] == "assemblyai"


# --- Live settings ---------------------------------------------------------------------

def test_overlay_applies_glow_and_island_side_live(qapp, monkeypatch):
    import overlay
    monkeypatch.setattr(overlay, "apply_clickthrough_styles", lambda hwnd: None)

    class FakeOverlay:
        def __init__(self, screen):
            self.glow_enabled = True

        def show(self):
            pass

        def apply_win32_clickthrough(self):
            pass

        def update(self):
            pass

    controller = overlay.OverlayController(overlay_factory=FakeOverlay)
    from island import SideIsland
    controller.island = SideIsland(side="right")
    try:
        controller.apply_look(glow_enabled=False, island_side="left")
        assert all(not o.glow_enabled for o in controller.overlays)
        assert controller.island.side == "left"
    finally:
        controller._follow_timer.stop()
        controller.island.close()
