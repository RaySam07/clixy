"""3D explainers: ask how something works and Clixy builds it in 3D on your
screen, a model you can turn, zoom and click, with labels and your buddy
walking you through it step by step.

Each topic is a recipe in ``recipes/``: JSON naming parts from the 3D parts
library (``web/src/parts.js``), how they move, the steps and the labels. A
recipe never runs code. ``web/`` holds the page and its bundled engine,
``window.py`` the window that shows it over your screen.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import re
from functools import lru_cache
from pathlib import Path

RECIPE_DIR = Path(__file__).parent / "recipes"
WEB_DIR = Path(__file__).parent / "web"
PAGE = WEB_DIR / "index.html"

ORDER = ("engine", "bicycle", "water", "solar", "dna", "water_molecule")
"""Topics in the order the window lists them."""

# What someone might say for each topic. First match wins, so a narrower topic
# goes before a broader one ("water molecule" before "water").
_TOPICS = (
    ("water_molecule", r"water molecules?|\bh2o\b|\bh₂o\b"),
    ("water", r"water cycle|\brain\b|evaporat|precipitation|condensation"),
    ("engine", r"(?:two|2)[ -]?stroke|\b(?:an|the|car|petrol|gas|motorbike|motorcycle|combustion) engines?\b"
               r"|\bengines? works?\b|\bpistons?\b|spark plugs?|crankshaft"),
    ("bicycle", r"\bbicycles?\b|\bbikes?\b|\bpedals?\b|chain ?rings?"),
    ("solar", r"solar system|\bplanets?\b|\borbits?\b"),
    ("dna", r"\bdna\b|double helix|base pairs?"),
)
_TOPIC_RES = tuple((topic, re.compile(pattern, re.I)) for topic, pattern in _TOPICS)

# The question has to ask to see or understand it, so "what's this bike's
# price" stays a normal answer.
_ASKS_TO_SEE = re.compile(
    r"\b3d\b|\bthree[- ]?d\b|\bmodel\b|\bvisuali[sz]e|\bshow me\b|\bexplain\b"
    r"|\bhow (?:does|do|is|are|did)\b|\bworks?\b",
    re.I,
)


def match(question: str) -> str | None:
    """The topic a spoken question asks to see in 3D, or None."""
    if not question or not _ASKS_TO_SEE.search(question):
        return None
    for topic, pattern in _TOPIC_RES:
        if pattern.search(question):
            return topic
    return None


@lru_cache(maxsize=None)
def recipe(topic: str) -> dict:
    return json.loads((RECIPE_DIR / f"{topic}.json").read_text(encoding="utf-8"))


def topics() -> list[tuple[str, str]]:
    """(topic, its short name) for every topic, in menu order."""
    return [(t, recipe(t).get("short") or recipe(t)["title"]) for t in ORDER]


def intro(topic: str) -> str:
    """One spoken line as the model opens."""
    title = recipe(topic)["title"].rstrip(".")
    if not title.split()[0].isupper():  # "The solar system" -> "the", but DNA stays DNA
        title = title[0].lower() + title[1:]
    return f"Here's {title}."


def available() -> bool:
    """Whether this build can show 3D (it needs Qt WebEngine). Looks without
    importing it: WebEngine must not load on a worker thread."""
    try:
        found = importlib.util.find_spec("PyQt6.QtWebEngineWidgets") is not None
    except (ImportError, ValueError):
        found = False
    return found and PAGE.exists() and (WEB_DIR / "clixy3d.js").exists()


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


@lru_cache(maxsize=1)
def fonts() -> dict[str, str]:
    """Manrope for the page, as data URLs (a file:// page can't load font files)."""
    folder = Path(__file__).parent.parent / "assets" / "fonts"
    files = {"200": "Manrope-ExtraLight.ttf", "300": "Manrope-Light.ttf",
             "500": "Manrope-Medium.ttf", "600": "Manrope-SemiBold.ttf"}
    out = {}
    for weight, name in files.items():
        path = folder / name
        if path.exists():
            out[weight] = data_url(path.read_bytes(), "font/ttf")
    return out


def payload(open_topic: str, *, backdrop: str = "", accent: str = "#ff5a3d",
            on_accent: str = "#ffffff", buddy: list[str] | None = None) -> dict:
    """Everything the page needs to boot, as one JSON-able dict."""
    return {
        "recipes": {t: recipe(t) for t in ORDER},
        "order": list(ORDER),
        "open": open_topic,
        "backdrop": backdrop,
        "accent": accent,
        "onAccent": on_accent,
        "buddy": buddy or [],
        "fonts": fonts(),
    }
