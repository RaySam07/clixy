"""Opening apps, websites and Windows Settings pages."""
from __future__ import annotations

import pytest

import apps

APPS = {"Spotify": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "Google Chrome": "Chrome",
        "Microsoft Teams": "MSTeams_8wekyb3d8bbwe!MSTeams", "Visual Studio Code": "C:\\VSCode\\Code.exe",
        "Notepad": "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\Notepad.lnk"}


def test_open_tags_are_found_and_never_spoken():
    spoken, targets = apps.parse_open("opening spotify for you. [OPEN:spotify]")
    assert spoken == "opening spotify for you." and targets == ["spotify"]
    spoken, targets = apps.parse_open("here's youtube [OPEN:https://youtube.com] [POINT:none]")
    assert targets == ["https://youtube.com"] and "OPEN" not in spoken
    assert apps.parse_open("going there. [OPEN:ms-sett")[0] == "going there."


@pytest.mark.parametrize("target, kind", [
    ("ms-settings:display", "settings"), ("https://youtube.com", "web"), ("youtube.com", "web"),
    ("www.bbc.co.uk/news", "web"), ("spotify", "app"), ("visual studio code", "app"),
])
def test_what_kind_of_thing(target, kind):
    assert apps.kind_of(target) == kind


@pytest.mark.parametrize("asked, found", [
    ("spotify", "Spotify"), ("chrome", "Google Chrome"), ("teams", "Microsoft Teams"),
    ("vs code", None), ("visual studio", "Visual Studio Code"), ("spotfy", "Spotify"), ("photoshop", None),
])
def test_finding_an_installed_app(asked, found):
    assert apps.find_app(asked, APPS) == found


def test_opening_each_kind(monkeypatch):
    monkeypatch.setattr(apps, "_apps", dict(APPS))
    started = []
    start = started.append
    assert apps.open_target("ms-settings:display", start=start) == "Settings"
    assert apps.open_target("youtube.com", start=start) == "youtube.com"
    assert apps.open_target("spotify", start=start) == "Spotify"
    assert apps.open_target("notepad", start=start) == "Notepad"
    assert started == ["ms-settings:display", "https://youtube.com",
                       "shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", APPS["Notepad"]]


def test_the_model_is_told_about_this_pcs_apps(monkeypatch):
    monkeypatch.setattr(apps, "_apps", dict(APPS))
    block = apps.prompt_block()
    assert "[OPEN:" in block and "ms-settings:display" in block and "Spotify" in block


def test_a_guide_step_can_open_a_settings_page():
    import guide
    step = guide.parse_step("let's go straight there, i'll open it. [OPEN:ms-settings:personalization-colors][STEP:1/2]")
    assert (step.action, step.text, step.n) == ("open", "ms-settings:personalization-colors", 1)
    assert step.say == "let's go straight there, i'll open it."


def test_the_guide_opens_it_then_carries_on():
    from types import SimpleNamespace
    import guide
    cap = SimpleNamespace(monitor={"left": 0, "top": 0, "width": 1280, "height": 800}, scale_x=1.0, scale_y=1.0,
                          target_width=1280, target_height=800, image=None, label="s", is_cursor_screen=True)
    replies = ["i'll open your colour settings. [OPEN:ms-settings:personalization-colors][STEP:1/2]",
               "pick dark. [DONE]"]
    prompts, opened = [], []
    g = guide.Guide(ask=lambda _c, t: prompts.append(t) or replies.pop(0), capture=lambda: cap,
                    list_elements=lambda _m: [], show=lambda *_a: None, clear=lambda: None,
                    say=lambda *_a: None, note=lambda *_a: None, cancelled=lambda: False,
                    foreground=lambda: (1, "x"), open=lambda t: opened.append(t) or "Settings",
                    sleep=lambda _s: None)
    g.watch = SimpleNamespace(start=lambda: None, stop=lambda: None, drain=lambda: None, get=lambda _t: None)
    g.run("turn on dark mode", cap)
    assert opened == ["ms-settings:personalization-colors"]
    assert "clixy opened Settings" in prompts[1]


def test_planning_runs_on_a_thinking_claude():
    import ai
    fast = ai.AnthropicClient(api_key="sk-ant-test", model_id="anthropic/claude-haiku-5-5")
    smart = ai.thinking_variant(fast)
    assert smart.model_id == "claude-sonnet-5-5" and smart.effort == "low" and smart._thinking is None
    assert fast._thinking == {"type": "disabled"}
    other = object.__new__(ai.OpenAIVisionClient)
    assert ai.thinking_variant(other) is other


def test_an_answer_that_opens_something_opens_it_and_doesnt_say_the_tag(mocker):
    import queue
    import threading
    from PIL import Image
    from app import ClickyApp
    app = ClickyApp(ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(), tts_client=mocker.MagicMock(),
                    memory_store=mocker.MagicMock(), overlay_controller=mocker.MagicMock(),
                    hotkey_instance=mocker.MagicMock())
    opened = mocker.patch("apps.open_target", return_value="Spotify")
    cap = mocker.MagicMock()
    cap.image = Image.new("RGB", (1280, 800))
    cap.label, cap.scale_x, cap.scale_y = "screen 1 of 1", 1.0, 1.0
    cap.monitor = {"left": 0, "top": 0, "width": 1280, "height": 800}
    cap.target_width, cap.target_height, cap.is_cursor_screen = 1280, 800, True
    app._press_captures = [cap]
    app._stt.stop_recording.return_value = "open spotify"
    stream = mocker.MagicMock()
    stream.text_deltas.return_value = iter(["opening spotify for you. ", "[OPEN:spotify]"])
    stream.final_result.return_value = mocker.MagicMock(spoken_text="opening spotify for you. [OPEN:spotify]",
                                                        coordinate=None, element_label=None, screen_number=None)
    stream.usage.return_value = {}
    app._ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=stream)
    app._ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)
    q: queue.Queue = queue.Queue(maxsize=1)
    q.put(([cap], "", "test"))
    app._pipeline_worker("TEST.EXE", "Win", threading.Event(), q)
    opened.assert_called_once_with("spotify")
    said = " ".join(c.args[0] for c in app._tts.speak_sentence.call_args_list)
    assert "OPEN" not in said and "opening spotify" in said
    assert "[OPEN:" in app._ai.ask_stream.call_args.kwargs["system_prompt"]


def test_a_named_browser_opens_the_link():
    assert apps.browser_named("search it up on Chrome for me") == "chrome"
    assert apps.browser_named("open it in Microsoft Edge") == "msedge"
    assert apps.browser_named("search flights to calicut") == ""
    calls = []
    apps.open_target("https://www.google.com/search?q=ai", start=lambda *a: calls.append(a), browser="chrome")
    assert calls == [("chrome", "https://www.google.com/search?q=ai")]
    calls.clear()
    apps.open_target("google.com", start=lambda *a: calls.append(a))
    assert calls == [("https://google.com",)], "no browser named: the default one"
