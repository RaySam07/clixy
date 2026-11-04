"""Clixy's hands (hands.py): key codes and text, worked out without ever sending input."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("combo, codes", [
    ("ctrl+shift+esc", [0x11, 0x10, 0x1B]), ("win+i", [0x5B, ord("I")]), ("Enter", [0x0D]),
    ("alt+f4", [0x12, 0x73]), ("ctrl+1", [0x11, ord("1")]), ("ctrl + s", [0x11, ord("S")]),
])
def test_keys_become_virtual_key_codes(combo, codes):
    import hands
    assert hands.vk_codes(combo) == codes


@pytest.mark.parametrize("combo", ["", "ctrl+banana", "+"])
def test_unknown_keys_are_refused(combo):
    import hands
    assert hands.vk_codes(combo) is None


def test_text_goes_as_utf16_units_so_any_language_types():
    import hands
    assert hands._utf16_units("aé") == [0x61, 0xE9]
    assert hands._utf16_units("😀") == [0xD83D, 0xDE00], "an emoji is two units, sent in turn"
    assert hands._utf16_units("مرحبا")[0] == 0x0645
