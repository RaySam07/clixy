"""3D explainers: which questions open one, that every recipe only names
parts and parts-library shapes that exist, and the app and tray wiring."""
from __future__ import annotations

import queue
import re
import threading
from pathlib import Path

import pytest

import explainer


# --- Which questions open a 3D explainer -------------------------------------------

@pytest.mark.parametrize("question, topic", [
    ("show me how a 2-stroke engine works", "engine"),
    ("How does a two stroke engine work?", "engine"),
    ("explain how a piston works", "engine"),
    ("how does a bike work", "bicycle"),
    ("show me the gears on a bicycle in 3D", "bicycle"),
    ("explain the water cycle", "water"),
    ("how does rain form", "water"),
    ("show me a water molecule", "water_molecule"),
    ("show me the solar system", "solar"),
    ("how do planets orbit the sun", "solar"),
    ("explain DNA", "dna"),
    ("visualize the double helix", "dna"),
])
def test_questions_that_open_one(question, topic):
    assert explainer.match(question) == topic


@pytest.mark.parametrize("question", [
    "",
    "where is the save button",
    "what's this bike's price",
    "is it going to rain tomorrow in my calendar",
    "show me how to export this sheet",
    "how does this search engine rank pages",
])
def test_questions_that_stay_normal_answers(question):
    assert explainer.match(question) is None


def test_intro_is_one_spoken_line():
    assert explainer.intro("engine") == "Here's how a 2-stroke engine works."
    assert explainer.intro("solar") == "Here's the solar system."
    assert explainer.intro("dna") == "Here's DNA, the double helix."


# --- Recipes ------------------------------------------------------------------------

def _builders() -> set[str]:
    src = (explainer.WEB_DIR / "src" / "parts.js").read_text(encoding="utf-8")
    body = src.split("export const BUILDERS = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r"^  (\w+): \(", body, re.M)) | {"chain", "particles"}


@pytest.mark.parametrize("topic", explainer.ORDER)
def test_recipe_only_names_parts_that_exist(topic):
    r = explainer.recipe(topic)
    ids = {p["id"] for p in r["parts"]}
    assert len(ids) == len(r["parts"]), "part ids must be unique"
    shapes = _builders()
    for p in r["parts"]:
        assert p["type"] in shapes, f"{p['id']}: no such shape {p['type']}"
        if "attach" in p:
            assert p["attach"] in ids
        for end in ("from", "to"):
            if p["type"] == "link":
                assert p[end]["part"] in ids
        if "gate" in p:
            assert p["gate"]["crank"] in ids
    for m in r["motions"]:
        assert m["part"] in ids
        for key in ("slider", "flash"):
            if key in m:
                assert m[key]["crank"] in ids
        if "rotate" in m and "follow" in m["rotate"]:
            assert m["rotate"]["follow"] in ids
    for s in r["steps"]:
        assert set(s["highlight"]) <= ids, s["name"]
        assert s["caption"]
    for label in r["labels"]:
        assert label["part"] in ids
        assert set(label.get("also", [])) <= ids
        assert label.get("info"), f"{topic}: {label['text']} has nothing to say when clicked"


def test_every_topic_has_a_short_name_for_the_menu():
    names = dict(explainer.topics())
    assert list(names) == list(explainer.ORDER)
    assert names["engine"] == "2-stroke engine"


def test_payload_carries_everything_the_page_needs():
    p = explainer.payload("engine", backdrop="data:image/jpeg;base64,AA", buddy=["data:image/png;base64,AA"])
    assert p["open"] == "engine"
    assert set(p["recipes"]) == set(explainer.ORDER) == set(p["order"])
    assert p["fonts"] and all(url.startswith("data:font/ttf;base64,") for url in p["fonts"].values())


def test_the_page_and_its_engine_ship():
    assert explainer.PAGE.exists()
    engine = (explainer.WEB_DIR / "clixy3d.js").read_text(encoding="utf-8")
    assert "window.Clixy3D" in engine or "Clixy3D=" in engine
    page = explainer.PAGE.read_text(encoding="utf-8")
    assert '<script src="clixy3d.js"></script>' in page
    # Nothing on the page reaches the network.
    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "") and "https://" not in page


def test_app_lets_web_view_load_later():
    src = (Path(explainer.__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert src.index("AA_ShareOpenGLContexts") < src.index("qt_app = QApplication(sys.argv)")


# --- The app opens one when asked ----------------------------------------------------

def _make_app(mocker):
    from app import ClickyApp
    return ClickyApp(
        ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(),
        tts_client=mocker.MagicMock(), memory_store=mocker.MagicMock(),
        overlay_controller=mocker.MagicMock(), hotkey_instance=mocker.MagicMock(),
    )


def _run(app, mocker, transcript):
    from PIL import Image
    cap = mocker.MagicMock()
    cap.image = Image.new("RGB", (1280, 800))
    cap.label = "screen 1 of 1"
    cap.scale_x = cap.scale_y = 1.0
    cap.monitor = {"left": 0, "top": 0, "width": 1280, "height": 800}
    cap.target_width, cap.target_height = 1280, 800
    cap.is_cursor_screen = True
    app._press_captures = [cap]
    app._stt.stop_recording.return_value = transcript
    q: queue.Queue = queue.Queue(maxsize=1)
    q.put(([cap], "", "test"))
    app._pipeline_worker("TEST.EXE", "Win", threading.Event(), q)


def test_asking_to_see_it_opens_the_explainer_without_the_ai(mocker):
    app = _make_app(mocker)
    app.explainers = True
    opened = []
    app.sig_explain.disconnect()
    app.sig_explain.connect(opened.append)
    _run(app, mocker, "show me how a 2-stroke engine works")
    assert opened == ["engine"]
    app._ai.ask_stream.assert_not_called()
    app._tts.speak_sentence.assert_called_once_with("Here's how a 2-stroke engine works.")


def test_without_3d_the_question_goes_to_the_ai(mocker):
    app = _make_app(mocker)
    app.explainers = False
    opened = []
    app.sig_explain.disconnect()
    app.sig_explain.connect(opened.append)
    app._ai.ask_stream.side_effect = RuntimeError("stop here")
    _run(app, mocker, "show me how a 2-stroke engine works")
    assert opened == []
    app._ai.ask_stream.assert_called_once()


# --- The tray ------------------------------------------------------------------------

@pytest.fixture()
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_tray_lists_the_explainers_when_this_build_has_them(qapp, mocker):
    from tray import ClickyTray
    on_explain = mocker.MagicMock()
    t = ClickyTray(on_quit=mocker.MagicMock(), on_settings=mocker.MagicMock(), on_explain=on_explain)
    labels = [a.text() for a in t._menu.actions() if not a.isSeparator()]
    assert labels[:3] == ["Settings...", "Show me in 3D", "Buddy"]
    menu = next(a for a in t._menu.actions() if a.text() == "Show me in 3D").menu()
    assert [a.text() for a in menu.actions()] == [name for _, name in explainer.topics()]
    menu.actions()[0].trigger()
    on_explain.assert_called_once_with("engine")


# --- The window's messages from the page ------------------------------------------------

def test_page_messages(mocker):
    window = pytest.importorskip("explainer.window")
    stub = mocker.MagicMock()
    window.ExplainerWindow._on_title(stub, "clixy:close:3")
    stub.dismiss.assert_called_once()
    window.ExplainerWindow._on_title(stub, "clixy:topic:dna:4")
    stub.topic_changed.emit.assert_called_once_with("dna")
    window.ExplainerWindow._on_title(stub, "clixy:part:spark_plug:5")
    stub.part_clicked.emit.assert_called_once_with("spark_plug")
    window.ExplainerWindow._on_title(stub, "Some other title")
    assert stub.dismiss.call_count == 1
