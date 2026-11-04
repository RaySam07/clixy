"""The laptop map: his apps and sites, learned from the windows in front, by name only."""

from __future__ import annotations


import pytest

import laptop


@pytest.mark.parametrize("exe, title, place", [
    ("chrome.exe", "(96) WhatsApp - Google Chrome", ("site", "WhatsApp", "chrome")),
    ("chrome.exe", "Hark - Chat · Hark", ("site", "Hark", "chrome app window")),
    ("chrome.exe", "Domain Name Search | Free Domain Availability Tool - Spaceship - Google Chrome",
     ("site", "Spaceship", "chrome")),
    ("chrome.exe", "Sam Altman On AI | FO583 Raj Shamani - YouTube - Google Chrome", ("site", "YouTube", "chrome")),
    ("msedge.exe", "One way to Kozhikode | Google Flights and 10 more pages - Personal - Microsoft​ Edge",
     ("site", "Google Flights", "edge")),
    ("chrome.exe", "Search / X - Google Chrome", ("site", "X", "chrome")),
    ("claude.exe", "Claude", ("app", "Claude", "")),
    ("Code.exe", "laptop.py - clixy - Visual Studio Code", ("app", "VS Code", "")),
    ("pythonw.exe", "Clixy", ("", "", "")),
    ("chrome.exe", "New Tab - Google Chrome", ("", "", "")),
    ("chrome.exe", "Google Chrome", ("", "", "")),
])
def test_a_window_becomes_a_name_never_its_contents(exe, title, place):
    assert laptop.place_of(exe, title) == place


def test_the_summary_names_his_browser_sites_and_apps(tmp_path):
    m = laptop.LaptopMap(tmp_path / "laptop.json")
    for _ in range(5):
        m.see("chrome.exe", "(95) WhatsApp - Google Chrome")
    m.see("chrome.exe", "Hark - Chat · Hark")
    m.see("msedge.exe", "Flights - Personal - Microsoft Edge")
    m.see("VoiceStudio.exe", "VoiceStudio")
    text = m.summary()
    assert text.startswith("he browses in chrome (not edge)")
    assert "WhatsApp, Hark (chrome app window), Flights (edge)" in text
    assert "apps he uses: VoiceStudio" in text and "ctrl+shift+a" in text
    m.save()
    again = laptop.LaptopMap.load(tmp_path / "laptop.json")
    assert again.sites["WhatsApp"]["seen"] == 5 and again.sites["WhatsApp"]["in"] == "chrome"


def test_edge_is_not_his_browser_when_only_clixy_opened_links_there(tmp_path):
    m = laptop.LaptopMap()
    for _ in range(4):
        m.see("msedge.exe", "Google Flights - Personal - Microsoft Edge")
    m.see("chrome.exe", "(95) WhatsApp - Google Chrome")
    assert m.browser() == "chrome"


def test_it_learns_from_past_sessions_the_first_time(tmp_path, monkeypatch):
    import debug_log
    session = tmp_path / "debug" / "2026-11-04_12-45-41_chrome.exe"
    session.mkdir(parents=True)
    (session / "interaction.log").write_text("[+0ms] APP: chrome.exe\n[+0ms] WINDOW: (95) WhatsApp - Google Chrome\n",
                                             encoding="utf-8")
    monkeypatch.setattr(debug_log, "_DEBUG_DIR", tmp_path / "debug")
    assert "WhatsApp" in laptop.summary()
    assert laptop.current().sites["WhatsApp"]["last"] == "2026-11-04T12:45"
    assert (tmp_path / "memory" / "laptop.json").exists(), "kept in the memory folder"


def test_nothing_is_kept_or_told_while_history_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(laptop, "_keeping", lambda: False)
    laptop.current().see("chrome.exe", "(95) WhatsApp - Google Chrome")
    assert laptop.summary() == ""


def test_the_guide_is_told_and_opens_links_in_his_browser():
    import guide
    assert guide._browser_in("he browses in chrome (not edge); sites he uses...") == "chrome"
    assert guide._browser_in("he browses in edge; ...") == "msedge"
    assert guide._browser_in("") == ""
