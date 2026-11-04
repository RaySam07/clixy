"""Clixy orchestrator: wires all 7 building blocks into the PTT loop.

One sequential pipeline worker thread per PTT press, cancel-on-re-press.
Matches Clicky's Swift Task pattern (CompanionManager.swift lines 490-720).

Threading rule: only pyqtSignal crosses thread boundaries. Worker thread
NEVER calls overlay methods directly.

Top-to-bottom order (so `python -m app` works):
    1. Module docstring
    2. Imports
    3. Constants + sentence splitter
    4. get_foreground_app() ctypes helper
    5. ClickyApp(QObject) orchestrator class
    6. __main__ block
"""
from __future__ import annotations

import ctypes
import math
import os
import queue
import re
import signal
import sys
import threading
import time
from ctypes import wintypes

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

import apps
import guide
import kb
import model3d
import sketch
import sysinfo
from ai import (
    _CLICKY_ANNOTATION_SYSTEM_PROMPT,
    _CLICKY_SYSTEM_PROMPT,
    OllamaClient,
    create_ai_client,
)
from annotations import parse_annotations, wants_drawing
from debug_log import DebugSession
from locator import locate_via_grid
from capture import (
    capture_all_screens,
    get_cursor_position,
    list_monitors,
    monitor_containing,
    set_dpi_awareness,
    unscale_claude_coords,
)
from config import (
    ANNOTATION_MODE as _ANNOTATION_MODE_AT_START,
    ASSEMBLYAI_API_KEY,
    CARTESIA_API_KEY,
    DEFAULT_STT_PROVIDER,
    DEFAULT_TTS_PROVIDER,
    GEMINI_MODEL_VISION,
    MODEL_ID,
    OLLAMA_HOST,
    OLLAMA_MODEL_VISION,
    OPENAI_MODEL_VISION,
    default_llm_provider,
    resolve_api_key,
    resolve_model,
    resolve_setting,
)
# LLM_PROVIDER and the LLM keys are read fresh (resolve_setting /
# resolve_api_key) instead of imported, so a key saved in the first-launch
# dialog is used in the same session. Module constants freeze at import.
from hotkey import DEFAULT_COMBO, PushToTalkHotkey, describe as describe_hotkey, parse_combo
import mic
from memory import MemoryStore
from overlay import OverlayController
from stt import AssemblyAIStreamingSTT, create_stt_client
from tts import CartesiaSonicTTS, first_phrase, with_pause

ANNOTATION_MODE: str = _ANNOTATION_MODE_AT_START
"""Draw on screen, "on" or "off". Read once here and refreshed when
Settings are saved (ClickyApp.apply_live_settings), never per question."""


# --- Constants + sentence splitter --------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]\s")
_CLAUSE_END_RE = re.compile(r"[,;:]\s")
_DO_TAG_RE = re.compile(r"^\s*\[\s*DO\s*\]", re.IGNORECASE)   # the answer model: "a job for the hands"
_STRAY_POINT_RE = re.compile(r"\[\s*POINT\b[^\]]*\]?", re.IGNORECASE)

_MAX_HISTORY_EXCHANGES = 10

_REUSE_THRESHOLD_PX = 150
"""Max cursor movement between press and release for reusing press-time
captures. Cursor hovers of 100-150px while talking are normal and should
not trigger a re-capture. 150px = ~3cm on a 200% DPI laptop display,
still 'target hover' intent, not 'user repositioned intentionally'."""


def flush_sentences(buffer: str, clause: bool = False) -> tuple[list[str], str]:
    """Split buffer into complete sentences and leftover.

    Returns (list_of_complete_sentences, remaining_buffer).
    Splits on .!? followed by whitespace. The system prompt tells the model
    to avoid abbreviations like 'e.g.' so false splits are rare.
    With ``clause`` (nothing spoken yet), a first sentence still being written
    is cut at its first comma, semicolon or colon after four words, so the
    voice starts on a short phrase instead of waiting for the whole sentence;
    a long first sentence is cut at a natural break too (``tts.first_phrase``),
    finished or not, since a short phrase is ready to hear much sooner.
    """
    sentences: list[str] = []
    if clause:
        end = _SENTENCE_END_RE.search(buffer)
        cut = first_phrase(buffer[:end.end()] if end else buffer)
        if cut:
            sentences.append(buffer[:cut].strip())
            buffer = buffer[cut:]
    while (m := _SENTENCE_END_RE.search(buffer)):
        end = m.end()
        sentences.append(buffer[:end].strip())
        buffer = buffer[end:]
    if clause and not sentences:
        for m in _CLAUSE_END_RE.finditer(buffer):
            if len(buffer[:m.start()].split()) >= 4:
                sentences.append(buffer[:m.end()].strip())
                buffer = buffer[m.end():]
                break
    return sentences, buffer


def unspoken_tail(spoken_text: str, spoken: list[str]) -> str:
    """The part of spoken_text the voice hasn't said yet. The sentences already
    spoken are found in order, any run of whitespace matching any other, rather
    than counted: extra spaces between sentences, or draw mode tidying the
    whitespace, can't move the cut into a sentence that was already said."""
    pos = 0
    for sentence in spoken:
        found = re.compile(r"\s+".join(map(re.escape, sentence.split()))).search(spoken_text, pos)
        if found:
            pos = found.end()
    return spoken_text[pos:].strip()


# --- Grid-locator fallback (Ollama pixel-pointing) ---------------------------

_DIRECTIONAL_QUERY_WORDS = (
    "where", "click", "show me", "find", "point", "open", "select", "press",
    "navigate", "locate", "tap", "look at", "go to",
)
"""Words/phrases that suggest the user wants Clicky to point at a UI element.
Grid-locator only fires for queries containing one of these, so it skips
conceptual asks like 'what is HTML' that don't have a UI target."""


def _looks_directional(query: str) -> bool:
    """True if the query contains a directional word like 'where', 'click', 'show me'.

    Used as a cheap pre-filter before firing the grid-locator, which costs 2
    extra LLM calls (expensive on local Ollama). For conceptual questions Clicky
    should just answer with TTS and not try to point anywhere.
    """
    if not query:
        return False
    q_lower = query.lower()
    return any(word in q_lower for word in _DIRECTIONAL_QUERY_WORDS)


def _drawing_anchor(annotations: list) -> tuple[int, int] | None:
    """Where the cursor lands when a draw-mode answer has shapes but no
    [POINT]: the first shape, in screenshot coords, placed so the cursor does
    not cover what the shape marks. A text label alone is not a target."""
    from annotations import Angle, Arrow, Box, Circle, Dot, Highlight, Line, Ring, Underline

    for ann in annotations:
        if isinstance(ann, Ring):
            edge = math.ceil(ann.r * 0.7071)
            return (ann.x + edge, ann.y + edge)
        if isinstance(ann, Line):
            return (ann.x2, ann.y2)
        if isinstance(ann, Angle):
            return (ann.vx, ann.vy)
        if isinstance(ann, Dot):
            return (ann.x, ann.y)
        if isinstance(ann, Circle):
            edge = math.ceil(ann.r * 0.7071)  # 45 degrees: on the circle's lower-right edge
            return (ann.x + edge, ann.y + edge)
        if isinstance(ann, (Box, Highlight)):
            return (ann.x2, ann.y2)  # the lower-right corner, off the element
        if isinstance(ann, Arrow):
            return (ann.x2, ann.y2)
        if isinstance(ann, Underline):
            return (ann.x + ann.w // 2, ann.y)
    return None


def _annotations_to_physical(annotations: list, cursor_capture) -> list:
    """Map screenshot-pixel annotation coords -> physical virtual-desktop
    coords using the SAME transform the [POINT] cursor uses
    (capture.unscale_claude_coords: clamp -> *scale -> +monitor-origin).
    No grid-locator: Claude is natively accurate at coords, and GPT-4o/Ollama
    are forgiving enough for big worksheet regions.

    Lengths (circle radius, underline width) only scale by scale_x (they're
    sizes, not positions: no origin, no clamp). Returns NEW annotation objects.
    """
    from annotations import Angle, Arrow, Box, Circle, Dot, Highlight, Keys, Label, Line, Ring, Underline

    if not annotations:
        return []

    cap = cursor_capture

    def pt(x: int, y: int) -> tuple[int, int]:
        return unscale_claude_coords(
            claude_x=x,
            claude_y=y,
            scale_x=cap.scale_x,
            scale_y=cap.scale_y,
            monitor_left=cap.monitor["left"],
            monitor_top=cap.monitor["top"],
            target_w=cap.target_width,
            target_h=cap.target_height,
        )

    out: list = []
    for a in annotations:
        if isinstance(a, Circle):
            x, y = pt(a.x, a.y)
            out.append(Circle(x, y, int(a.r * cap.scale_x), a.label))
        elif isinstance(a, Arrow):
            x1, y1 = pt(a.x1, a.y1)
            x2, y2 = pt(a.x2, a.y2)
            out.append(Arrow(x1, y1, x2, y2))
        elif isinstance(a, Underline):
            x, y = pt(a.x, a.y)
            out.append(Underline(x, y, int(a.w * cap.scale_x), a.label))
        elif isinstance(a, Label):
            x, y = pt(a.x, a.y)
            out.append(Label(x, y, a.text))
        elif isinstance(a, (Box, Highlight)):
            x1, y1 = pt(a.x1, a.y1)
            x2, y2 = pt(a.x2, a.y2)
            out.append((Box if isinstance(a, Box) else Highlight)(x1, y1, x2, y2, a.label))
        elif isinstance(a, Ring):
            x, y = pt(a.x, a.y)
            out.append(Ring(x, y, int(a.r * cap.scale_x), a.label))
        elif isinstance(a, Line):
            x1, y1 = pt(a.x1, a.y1)
            x2, y2 = pt(a.x2, a.y2)
            out.append(Line(x1, y1, x2, y2, a.label))
        elif isinstance(a, Angle):
            vx, vy = pt(a.vx, a.vy)
            ax, ay = pt(a.ax, a.ay)
            bx, by = pt(a.bx, a.by)
            out.append(Angle(vx, vy, ax, ay, bx, by, a.label))
        elif isinstance(a, Dot):
            x, y = pt(a.x, a.y)
            out.append(Dot(x, y, a.label))
        elif isinstance(a, Keys):
            out.append(a)  # keys have no position
    return out


def _maybe_locate_via_grid(
    *,
    ai_client,
    result,
    cursor_capture,
    query: str,
    dbg=None,
):
    """Grid-locator fallback for weak-vision responses lacking a [POINT:x,y] tag.

    Triggers ONLY if:
        1. ai_client is a local Ollama model without its own box format. Cloud
           models and local Qwen-VL models point on their own, so a missing
           point from them means there is nothing to point at, not a miss
           worth extra calls.
        2. result.coordinate is None (model didn't emit a usable [POINT:x,y])
        3. query is directional (contains 'where' / 'click' / 'show me' / etc.)

    Returns (phys_x, phys_y) in PHYSICAL virtual-desktop coords (matching the
    output of unscale_claude_coords) or None if any condition fails / locator
    can't find a target.

    The output is in physical coords (not logical) so the caller can pass it
    straight to overlay.sig_point_at.emit(), the same convention the
    model-coordinate path uses.

    Args:
        ai_client: the active AIClient (OllamaClient / AnthropicClient / etc.)
        result: PointParseResult from stream.final_result(), used to check
            if coordinate is already set
        cursor_capture: capture.LabeledCapture for the primary screen
        query: user's transcript (the question they asked)
        dbg: optional DebugSession for logging the grid-locator outcome
    """
    if not isinstance(ai_client, OllamaClient):
        return None
    if result.coordinate is not None:
        return None
    if ai_client.box_style is not None:
        return None  # it answers in its own box format; no box means nothing to point at
    if not _looks_directional(query):
        if dbg is not None:
            dbg.log(
                f"GRID-LOCATOR: skipped (query not directional): {query!r}"
            )
        return None

    # Convert the PIL screenshot to base64 JPEG for the locator
    import io
    import base64
    buf = io.BytesIO()
    cursor_capture.image.save(buf, format="JPEG", quality=85)
    jpeg_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    monitor = cursor_capture.monitor
    # locate_via_grid returns coords pre-divided by dpi_scale. We want
    # PHYSICAL virtual-desktop coords (matching the rest of the pipeline),
    # so pass dpi_scale=1.0 and the locator returns physical (vx, vy).
    # dbg.log is threaded into the locator so transport failures (Ollama
    # timeout, image-decode error) are distinguishable from model uncertainty
    # (cell 0 / unparseable reply) in the debug log. Otherwise a broken Ollama
    # looks identical to "model said no UI element".
    phys_xy = locate_via_grid(
        llm_client=ai_client,
        screenshot_jpeg_b64=jpeg_b64,
        original_size=(cursor_capture.target_width, cursor_capture.target_height),
        physical_size=(monitor["width"], monitor["height"]),
        physical_origin=(monitor["left"], monitor["top"]),
        dpi_scale=1.0,   # We want PHYSICAL coords; overlay handles logical conversion
        query=query,
        debug_log=(dbg.log if dbg is not None else None),
    )

    if dbg is not None:
        if phys_xy is None:
            dbg.log("GRID-LOCATOR: ran but returned None (LLM unsure or conceptual)")
        else:
            dbg.log(f"GRID-LOCATOR: hit physical=({phys_xy[0]},{phys_xy[1]})")

    return phys_xy


# --- Foreground app detection -------------------------------------------------

def get_foreground_app() -> tuple[str, str]:
    """Return (app_name, window_title) of the foreground window via ctypes.

    app_name is the .exe basename (e.g. 'EXCEL.EXE').
    window_title is the full title bar text.
    Returns ('unknown', '') if detection fails.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ("unknown", "")

    length = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buf, length + 1)
    window_title = title_buf.value

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    app_name = "unknown"
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if handle:
        try:
            exe_buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            kernel32.QueryFullProcessImageNameW(
                handle, 0, exe_buf, ctypes.byref(size)
            )
            app_name = os.path.basename(exe_buf.value) or "unknown"
        finally:
            kernel32.CloseHandle(handle)

    return (app_name, window_title)


# --- ClickyApp orchestrator ---------------------------------------------------

def _request_host(exc: BaseException) -> str | None:
    """Host of the HTTP request behind an httpx error, if it has one."""
    try:
        return exc.request.url.host  # httpx raises if the request was never set
    except Exception:
        return None


def _friendly_error(stage: str, exc: BaseException) -> str:
    """One plain sentence for the tray. ``stage`` is "AI", "voice" or
    "speech-to-text". Looks through wrapped exceptions for the HTTP status."""
    chain: list[BaseException] = []
    e: BaseException | None = exc
    while e is not None and e not in chain:
        chain.append(e)
        e = e.__cause__ or e.__context__
    from stt import ListeningUnavailable
    if isinstance(exc, ListeningUnavailable):
        return str(exc)  # already in plain words
    # SDK errors carry status_code; a rejected WebSocket handshake carries it on .response.
    codes = (getattr(x, "status_code", None) or getattr(getattr(x, "response", None), "status_code", None) for x in chain)
    status = next((c for c in codes if isinstance(c, int)), None)
    text = " ".join(str(x) for x in chain).lower()
    names = " ".join(type(x).__name__ for x in chain)
    if status in (401, 403) or any(t in text for t in ("invalid x-api-key", "invalid api key", "incorrect api key", "unauthorized")):
        return f"Your {stage} key was rejected. Right-click the Clixy tray icon, open Settings, and paste it again."
    # An empty balance can arrive as 402, 400 or 429 (OpenAI's insufficient_quota),
    # so billing words decide it; a bare "quota" is a temporary per-minute limit.
    if status == 402 or any(t in text for t in (
        "insufficient_quota", "insufficient credit", "insufficient funds", "insufficient balance",
        "out of credit", "credit balance", "billing", "payment required",
    )):
        return f"Your {stage} account is out of credit. Top it up, then try again."
    if status == 429 or any(t in text for t in ("rate limit", "quota", "resource_exhausted", "too many requests")):
        return f"The {stage} service is rate-limiting you. Try again in a minute."
    if status == 404 or "model not found" in text:
        return f"The {stage} model isn't available on your key. Pick another provider in Settings."
    timed_out = any(isinstance(x, TimeoutError) or "Timeout" in type(x).__name__ for x in chain)
    if timed_out and any(_request_host(x) in ("localhost", "127.0.0.1", "::1") for x in chain):
        return f"The local {stage} model took too long to answer. It needs a strong GPU; a cloud model is much faster."
    if any(isinstance(x, (ConnectionError, TimeoutError)) for x in chain) or any(
        n in names for n in ("ConnectError", "ConnectionError", "Timeout")
    ):
        return f"Couldn't reach the {stage} service. Check your internet connection."
    return f"The {stage} step failed. Details are in the debug log."


# USD per million tokens (input, output) for the island's cost chip. Cached
# input is billed at a tenth of the input price, cache writes at 1.25x.
_PRICES_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def _hotkey_combo() -> str:
    """The saved push-to-talk combo, or the default if it's missing or broken."""
    combo = resolve_setting("HOTKEY", default=DEFAULT_COMBO)
    try:
        parse_combo(combo)
    except ValueError:
        return DEFAULT_COMBO
    return combo


def _answer_meta(first_sentence_s: float | None, model_id: str, usage: dict) -> str:
    """Chip text for the island: time to the first spoken sentence and, for
    models with a known price, roughly what the answer cost."""
    parts = []
    if first_sentence_s is not None:
        parts.append(f"{first_sentence_s:.1f}s")
    price = _PRICES_PER_MTOK.get(model_id.split("/", 1)[-1])
    if price and usage:
        dollars = (
            usage.get("input", 0) * price[0]
            + usage.get("cache_read", 0) * price[0] * 0.1
            + usage.get("cache_write", 0) * price[0] * 1.25
            + usage.get("output", 0) * price[1]
        ) / 1_000_000
        parts.append(f"${dollars:.4f}")
    return " · ".join(parts)


class ClickyApp(QObject):
    """Main orchestrator. Owns all services + signals + worker lifecycle."""

    sig_pressed = pyqtSignal()
    sig_released = pyqtSignal()
    sig_hide_overlay = pyqtSignal()
    sig_show_overlay = pyqtSignal()
    sig_point_at = pyqtSignal(int, int, dict)
    sig_record_memory = pyqtSignal(str, str, str, str, list)
    # LISTENING-state signals + audio-level forwarding.
    sig_show_waveform = pyqtSignal(int, int, dict)
    sig_hide_waveform = pyqtSignal()
    sig_audio_level = pyqtSignal(float)
    # THINKING-state spinner: shown between release and the model returning
    # a coordinate, so the user sees feedback during the ~4-7s LLM wait.
    sig_show_spinner = pyqtSignal(int, int, dict)
    sig_hide_spinner = pyqtSignal()
    # Draw-on-screen teaching annotations. Carries a list of annotation
    # dataclasses (PHYSICAL coords) + the target monitor dict.
    sig_show_annotations = pyqtSignal(list, dict, object)
    # Clear all teaching shapes (fired at the start of each press so stale
    # annotations never survive a no-speech / cancelled / errored turn).
    sig_clear_annotations = pyqtSignal()
    # One plain-language sentence for the tray when a provider call fails.
    sig_notify = pyqtSignal(str)
    # Side island: what was asked, each sentence as it goes to the voice, the
    # time/cost chip, a short note instead of an answer, and the end of a turn.
    sig_island_transcript = pyqtSignal(str)
    sig_island_say = pyqtSignal(str)
    sig_island_meta = pyqtSignal(str)
    sig_island_note = pyqtSignal(str)
    sig_island_done = pyqtSignal()
    # Name of what the next point_at points at (the [POINT] tag's label).
    sig_point_label = pyqtSignal(str)
    # Guide mode: the step being shown (its sentence, number and the total).
    sig_guide_step = pyqtSignal(str, int, int, object)
    # A sketch note to draw on the note card (sketch.py), as a dict.
    sig_show_sketch = pyqtSignal(object)
    # The next job in Up next (jobs.py), to start now that the one before it is done.
    sig_next_job = pyqtSignal(str)

    def __init__(
        self,
        ai_client=None,
        stt_client=None,
        tts_client=None,
        memory_store=None,
        overlay_controller=None,
        hotkey_instance=None,
    ) -> None:
        super().__init__()

        # Respect the LLM_PROVIDER setting (Settings dialog dropdown).
        # _resolve_llm_credentials returns the effective model_id + api_key
        # for the provider the user picked in Settings.
        if ai_client is None:
            _model_id, _api_key = _resolve_llm_credentials()
            ai_client = create_ai_client(
                model_id=_model_id,
                api_key=_api_key,
                ollama_host=OLLAMA_HOST,
            )
        self._ai = ai_client
        # Planning work (guide steps, sketches) goes to a client that thinks a
        # little first: Claude Sonnet 5.5 at low effort when Claude is the provider.
        from ai import thinking_variant
        try:
            self._smart_ai = thinking_variant(ai_client)
        except Exception:
            self._smart_ai = ai_client

        # GPT-Realtime speech-to-speech mode, selected via
        # LLM_PROVIDER='openai-realtime'. This is a PARALLEL pipeline: when
        # active, _handle_press/_handle_release branch to the realtime session
        # (mic streams to the WS, model speaks back + points) instead of the
        # STT->AI->TTS chain. The realtime session's rough point_at coordinate
        # is replaced by a vision pass (_realtime_on_coordinate). Everything is
        # fail-safe: a realtime setup failure logs + leaves _realtime None so
        # the app still runs (just without realtime).
        self._realtime = None
        self._realtime_vision = None  # OpenAIVisionClient for the vision pass
        self._realtime_capture = None  # cursor-screen capture for the current turn
        if resolve_setting("LLM_PROVIDER", default=default_llm_provider()) == "openai-realtime":
            self._setup_realtime()
        self._stt = stt_client or AssemblyAIStreamingSTT(
            api_key=ASSEMBLYAI_API_KEY
        )
        self._mic_device = resolve_setting("MIC_DEVICE", default="")  # what the mic opened with
        self._tts = tts_client or CartesiaSonicTTS(api_key=CARTESIA_API_KEY)
        self._tts.on_error(lambda exc: self._notify("voice", exc))
        self._notice_times: dict[str, float] = {}  # message -> when it was last shown
        self._notice_lock = threading.Lock()
        self._memory = memory_store or MemoryStore()
        self._overlay = overlay_controller
        self._hotkey = hotkey_instance

        self._history: list[dict] = []
        self._cancel_event = threading.Event()
        self._guide_cancel: threading.Event | None = None
        """The running guide's own stop: a press doesn't end a guide, only a new question does."""
        self._worker_thread: threading.Thread | None = None
        self._holding = False  # the shortcut is held down right now
        self._current_app: str = "unknown"
        self._current_title: str = ""

        # Press-time capture state. Shifts capture + memory recall off the
        # release-time critical path, saving ~250ms wall-clock. The release-time
        # pipeline re-captures only if the cursor moved more than
        # _REUSE_THRESHOLD_PX (user intentionally repositioned mid-utterance).
        self._press_captures: list | None = None
        self._press_memory: str = ""
        self._press_cursor_pos: tuple[int, int] | None = None
        self._capture_thread: threading.Thread | None = None

        self.sig_pressed.connect(self._handle_press)
        self.sig_released.connect(self._handle_release)
        self.sig_hide_overlay.connect(self._on_hide_overlay)
        self.sig_show_overlay.connect(self._on_show_overlay)
        self.sig_point_at.connect(self._on_point_at)
        self.sig_record_memory.connect(self._on_record_memory)
        self.sig_show_waveform.connect(self._on_show_waveform)
        self.sig_hide_waveform.connect(self._on_hide_waveform)
        self.sig_audio_level.connect(self._on_audio_level)
        self.sig_show_spinner.connect(self._on_show_spinner)
        self.sig_hide_spinner.connect(self._on_hide_spinner)
        self.sig_show_annotations.connect(self._on_show_annotations)
        self.sig_clear_annotations.connect(self._on_clear_annotations)
        self.sig_island_transcript.connect(lambda text: self._island("island_transcript", text))
        self.sig_island_say.connect(lambda text: self._island("island_say", text))
        self.sig_island_meta.connect(lambda text: self._island("island_meta", text))
        self.sig_island_note.connect(lambda text: self._island("island_note", text))
        self.sig_island_done.connect(lambda: self._island("island_done"))
        self.sig_point_label.connect(lambda label: self._island("point_label", label))
        self.sig_guide_step.connect(lambda text, n, total, where: self._island("island_step", text, n, total, where))
        self.sig_show_sketch.connect(self._on_show_sketch)
        self.sig_next_job.connect(self.ask_text)
        self._note_card = None

    def start(self) -> None:
        """Initialize overlay + hotkey and begin listening.

        Hotkey callbacks fire on the pynput listener thread, so they emit
        sig_pressed/sig_released which Qt marshals to _handle_press/_handle_release
        on the main thread. This is the pyqtSignal-only thread crossing rule.
        """
        if self._overlay is None:
            self._overlay = OverlayController()
        if getattr(self._overlay, "island", None) is not None:
            from island import model_label
            self._overlay.island.model_name = model_label(getattr(self._ai, "model_id", "") or "")
        apps.load_in_background()  # which apps this PC has, for "open spotify"
        if self._hotkey is None:
            self._hotkey = PushToTalkHotkey(
                on_press=lambda: self.sig_pressed.emit(),
                on_release=lambda: self.sig_released.emit(),
                combo=_hotkey_combo(),
            )
        # Wire RMS audio-level → Qt-thread-safe signal → overlay waveform.
        # stt's callback runs on the portaudio thread; pyqtSignal marshals
        # to the Qt main thread where _on_audio_level calls overlay.set_audio_level.
        self._stt.on_audio_level(lambda lvl: self.sig_audio_level.emit(lvl))

        self._hotkey.start()
        _log(f"Listening for {describe_hotkey(self._hotkey.combo)}...")

    def _mic_problem(self) -> str | None:
        check = getattr(self._stt, "mic_problem", None)
        try:
            problem = check() if callable(check) else None
        except Exception:
            return None
        return problem if isinstance(problem, str) else None

    def apply_live_settings(self) -> None:
        """Apply what Settings can change without a restart: the shortcut,
        drawing on screen, the edge glow and the island's side. (The buddy
        switches by itself.)"""
        global ANNOTATION_MODE
        ANNOTATION_MODE = resolve_setting("ANNOTATION_MODE", default="on").lower()
        device = resolve_setting("MIC_DEVICE", default="")
        if device != self._mic_device:
            self._stt.set_mic_device(device)
        self._mic_device = device
        combo = _hotkey_combo()
        if self._hotkey is not None and parse_combo(combo) != self._hotkey.tokens:
            self._hotkey.stop()
            self._hotkey = PushToTalkHotkey(
                on_press=lambda: self.sig_pressed.emit(),
                on_release=lambda: self.sig_released.emit(),
                combo=combo,
            )
            self._hotkey.start()
            _log(f"Listening for {describe_hotkey(combo)}...")
        if getattr(self._overlay, "island", None) is not None:
            self._overlay.island.hotkey = describe_hotkey(combo)
        try:
            import dashboard
            dashboard.refresh(force=True)  # a new timetable or city
        except Exception:
            pass
        if self._overlay is not None and hasattr(self._overlay, "apply_look"):
            self._overlay.apply_look(
                glow_enabled=resolve_setting("EDGE_GLOW", default="on").lower() == "on",
                island_side=resolve_setting("ISLAND_SIDE", default="top").lower(),
            )

    def stop(self) -> None:
        """Clean shutdown of all services."""
        if self._hotkey:
            self._hotkey.stop()
        self._cancel_event.set()
        self._tts.stop()
        self._stt.disconnect()
        if self._realtime is not None:
            try:
                self._realtime.close()
            except Exception:
                pass
        _log("Shutdown complete.")

    # --- Hotkey handlers (called on Qt main thread via pyqtSignal) ---

    # --- GPT-Realtime (parallel pipeline) ----------------------------------

    def _setup_realtime(self) -> None:
        """Build + connect the GPT-Realtime session. Fail-safe: any error
        leaves self._realtime None and logs, so the app still runs."""
        try:
            from realtime import RealtimeSession
            from ai import OpenAIVisionClient
            key = resolve_api_key("OPENAI_API_KEY") or ""
            # Accurate vision client for the realtime PIXEL pass: gpt-5.4 by
            # default, precise enough to need no grid-locator. Same
            # OPENAI_MODEL_VISION the standard OpenAI path uses.
            self._realtime_vision = OpenAIVisionClient(
                api_key=key, model_id=f"openai/{OPENAI_MODEL_VISION}",
            )
            self._realtime = RealtimeSession(
                api_key=key,
                on_coordinate=self._realtime_on_coordinate,
                on_audio_start=lambda: self.sig_hide_spinner.emit(),
            )
            self._realtime.connect()
            _log("REALTIME: session connected (gpt-realtime speech-to-speech mode)")
        except Exception as exc:
            self._realtime = None
            _log(f"REALTIME: setup failed, falling back to normal pipeline: {exc}")

    def _realtime_on_coordinate(self, x: int, y: int, label: str) -> None:
        """Realtime called point_at(x,y,label): the user wants visual help.
        Runs on the realtime recv thread. We DISCARD the model's rough (x,y)
        (realtime is weak at pixels) and do an ACCURATE vision pass (gpt-5.4)
        on the screenshot instead: when draw mode is on, render shapes;
        otherwise point the cursor."""
        cap = self._realtime_capture
        if cap is None:
            return
        try:
            if ANNOTATION_MODE == "on":
                shapes = self._realtime_render_annotations(
                    label, cap, self._realtime_vision
                )
                if shapes:
                    self.sig_show_annotations.emit(shapes, cap.monitor, None)
                    _log(f"REALTIME: drew {len(shapes)} shapes (label={label!r})")
                    return
            phys = self._realtime_locate_point(label, cap, self._realtime_vision)
            if phys is not None:
                self.sig_point_at.emit(phys[0], phys[1], cap.monitor)
                _log(f"REALTIME: pointed at {phys} (label={label!r})")
        except Exception as exc:
            _log(f"REALTIME: vision pass failed: {exc}")

    def _realtime_render_annotations(self, label, cap, vision_client) -> list:
        """Accurate annotation vision pass for realtime draw mode: send the
        screenshot + the annotation system prompt to gpt-5.4, parse the shape
        tags, map to physical coords. Returns physical shapes (possibly empty).

        Uses ask_stream (not ask) because only ask_stream accepts a
        system_prompt override. The spoken text is discarded: GPT-Realtime
        already speaks, so this pass is pixels-only."""
        img_label = (
            f"primary focus (image dimensions: "
            f"{cap.target_width}x{cap.target_height} pixels)"
        )
        query = (
            f"circle or point at: {label}" if label
            else "point at what the user just asked about"
        )
        with vision_client.ask_stream(
            images=[(cap.image, img_label)],
            transcript=query,
            history=[],
            system_prompt=_CLICKY_ANNOTATION_SYSTEM_PROMPT,
        ) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()
        _, anns = parse_annotations(result.spoken_text)
        return _annotations_to_physical(anns, cap)

    def _realtime_locate_point(self, label, cap, vision_client):
        """Accurate single-point vision pass for realtime cursor mode: gpt-5.4
        returns a precise [POINT] directly. Returns physical (x,y) or None."""
        query = (
            f"where is: {label}" if label
            else "where is what the user just asked about"
        )
        result = vision_client.ask(
            cap.image, query, [], cap.target_width, cap.target_height,
        )
        points = result.get("points") or []
        if not points:
            return None
        p = points[0]
        return unscale_claude_coords(
            claude_x=p["x"],
            claude_y=p["y"],
            scale_x=cap.scale_x,
            scale_y=cap.scale_y,
            monitor_left=cap.monitor["left"],
            monitor_top=cap.monitor["top"],
            target_w=cap.target_width,
            target_h=cap.target_height,
        )

    def _notify(self, stage: str, exc: BaseException) -> None:
        """Tell the user in plain words, at most once a minute per message."""
        import time
        message = _friendly_error(stage, exc)
        now = time.time()
        with self._notice_lock:  # the AI worker and the voice thread both report
            if now - self._notice_times.get(message, 0.0) < 60:
                return
            self._notice_times[message] = now
        self.sig_notify.emit(message)
        self.sig_island_note.emit(message)

    def _warm_connections(self) -> None:
        self._ai.warm()
        self._tts.warm()

    def busy(self) -> bool:
        """Whether a question is being heard or answered right now."""
        worker = self._worker_thread
        return self._holding or (worker is not None and worker.is_alive())

    def _handle_press(self) -> None:
        """Hotkey pressed: kill TTS + start recording + capture foreground app."""
        import time
        self._holding = True
        # GPT-Realtime mode takes a separate path: stream the mic to the
        # realtime WS instead of STT. The normal STT/AI/TTS chain is skipped.
        if self._realtime is not None:
            _log("PRESS handler START (realtime mode)")
            try:
                self._realtime.stop()  # cancel any in-flight response
                self._realtime.start_turn()
                cursor_x, cursor_y = get_cursor_position()
                self._press_cursor_pos = (cursor_x, cursor_y)
                mon = monitor_containing(cursor_x, cursor_y, list_monitors())
                if mon is not None:
                    self.sig_show_waveform.emit(cursor_x, cursor_y, mon)
            except Exception as exc:
                _log(f"REALTIME: press failed: {exc}")
            return
        _log("PRESS handler START")
        t0 = time.time()
        # Cancel any in-flight worker from the PREVIOUS turn BEFORE clearing, so
        # it can't race past its cancel guards and repaint stale annotations /
        # pointer after this clear. (Without this, the old worker is only
        # cancelled at release, leaving a window during the new press-hold where
        # it could emit sig_show_annotations again.) Mirrors _handle_release's
        # cancel; the new worker gets a fresh cancel_event at release.
        guiding = self._guiding()
        if self._worker_thread and self._worker_thread.is_alive() and not guiding:
            self._cancel_event.set()
        # Clear any stale spinner from a prior interaction (defensive: if the
        # previous pipeline errored before hide_spinner fired, we don't want
        # to leave a spinner spinning when a new PTT starts).
        self.sig_hide_spinner.emit()
        # Same for stale teaching annotations: clearing on every press means
        # old shapes never survive a no-speech / cancelled / errored prior turn
        # (they'd otherwise linger until the 30s auto-clear timer). Cheap no-op
        # when there were no annotations. A running guide keeps its step's mark up:
        # what you say may be "next" or "I can't find it".
        if not guiding:
            self.sig_clear_annotations.emit()
        self._tts.stop()
        # Prevent TTS speaker decay from leaking into this PTT's transcript
        # (acoustic feedback loop). 200ms window tuned to real laptop-mic decay.
        # MUST be set before the chime: on cold start the chime triggers a
        # 400-500ms numpy/sounddevice cold-path init we don't want to count
        # against the grace window start time.
        self._stt.set_tts_grace_until(time.time() + 0.200)
        # Play listening chime (async / non-blocking) so user hears instant
        # "mic is hot" feedback. First call triggers sample generation
        # (~5ms CPU) + sounddevice cold-path init (~400ms on fresh portaudio).
        # Both are one-time costs amortized across the session.
        _play_chime_async()
        # Check if TTS thread actually died
        tts_thread = self._tts._current_thread
        tts_alive = tts_thread.is_alive() if tts_thread else False
        _log(f"  tts.stop() called, old thread alive={tts_alive}")
        self._current_app, self._current_title = get_foreground_app()
        _log(f"  app: {self._current_app}")
        try:
            self._stt.start_recording()
            _log(f"  start_recording() in {(time.time()-t0)*1000:.0f}ms")
        except RuntimeError as exc:
            _log(f"ERROR: STT start failed: {exc}")
            self._notify("speech-to-text", exc)
            return
        # No microphone at all: say so now, not after you've talked to nothing.
        problem = self._mic_problem()
        if problem in (mic.MISSING, mic.FAILED):
            _log(f"MIC: {problem}")
            self.sig_island_note.emit(mic.problem_message(problem))

        # Open the LLM and TTS connections while the user is still talking, so
        # the requests on release skip the TCP and TLS handshake.
        threading.Thread(target=self._warm_connections, daemon=True, name="clixy-warm").start()

        # Kick off capture + memory recall in the background so they overlap
        # with the user speaking. Release-time pipeline uses cached result.
        self._press_captures = None
        self._press_memory = ""
        self._press_cursor_pos = get_cursor_position()
        self._capture_thread = threading.Thread(
            target=self._press_time_capture,
            args=(self._current_app,),
            daemon=True,
            name="clixy-press-capture",
        )
        self._capture_thread.start()

        # Show the LISTENING-state waveform at the cursor position. The
        # cursor polygon hides; bars replace it for the duration of the utterance.
        cursor_x, cursor_y = self._press_cursor_pos
        try:
            mon = monitor_containing(cursor_x, cursor_y, list_monitors())
            if mon is not None:
                self.sig_show_waveform.emit(cursor_x, cursor_y, mon)
        except Exception as exc:
            _log(f"WARN: show_waveform dispatch failed: {exc}")

    def _press_time_capture(self, app_name: str) -> None:
        """Background thread launched at press time. Captures screens + recalls
        memory while the user is still speaking. Result stored on self for
        the release-time pipeline worker to consume.

        overlay.hide_for_capture() fires BEFORE the mss.grab() call via
        sig_hide_overlay (Qt signal to main thread), so the model never sees
        our own cursor.
        """
        try:
            self.sig_hide_overlay.emit()
            threading.Event().wait(0.05)
            captures = capture_all_screens()
            self.sig_show_overlay.emit()
            self._press_captures = captures
            self._press_memory = self._memory.recall(app_name)
        except Exception as exc:
            _log(f"ERROR: press-time capture failed: {type(exc).__name__}: {exc}")
            self._press_captures = None  # Release-time path falls back to re-capture

    def _release_capture_worker(
        self,
        release_cursor: tuple[int, int],
        app_name: str,
        result_queue: "queue.Queue",
    ) -> None:
        """Background thread launched at hotkey release. Runs in parallel with
        stt.stop_recording() so re-capture wall-clock is hidden under STT
        finalize latency. Makes the reuse-vs-recapture decision itself so a
        still cursor causes no overlay flicker.

        Pushes a tuple (captures, memory_context, reason_log_str) to
        result_queue. On exception pushes (None, None, error_str) so the
        main thread's queue.get() never hangs.

        overlay.hide_for_capture() fires BEFORE every mss.grab() via
        sig_hide_overlay (Qt signal to main thread), so the model never sees
        our own cursor.
        """
        try:
            # If press-time capture is still running, wait briefly for it to
            # finish before making the reuse decision. Avoids overlay-hide
            # collision between press-time + release-time captures.
            press_thread = self._capture_thread
            if press_thread is not None and press_thread.is_alive():
                press_thread.join(timeout=0.5)

            # Compute cursor delta at release.
            cursor_moved_px = 9999
            if self._press_cursor_pos is not None:
                dx = release_cursor[0] - self._press_cursor_pos[0]
                dy = release_cursor[1] - self._press_cursor_pos[1]
                cursor_moved_px = int((dx * dx + dy * dy) ** 0.5)

            if (
                self._press_captures is not None
                and cursor_moved_px <= _REUSE_THRESHOLD_PX
            ):
                reason = (
                    f"reusing press-time captures "
                    f"(cursor moved {cursor_moved_px}px, "
                    f"threshold {_REUSE_THRESHOLD_PX}px)"
                )
                result_queue.put(
                    (self._press_captures, self._press_memory, reason)
                )
                return

            # Re-capture path: hide overlay → grab → show.
            if self._press_captures is None:
                reason_suffix = "no press-time capture available"
            else:
                reason_suffix = (
                    f"cursor moved {cursor_moved_px}px > "
                    f"{_REUSE_THRESHOLD_PX}px threshold"
                )
            reason = f"re-capturing on release ({reason_suffix})"

            self.sig_hide_overlay.emit()
            threading.Event().wait(0.05)
            captures = capture_all_screens()
            self.sig_show_overlay.emit()
            memory_context = self._memory.recall(app_name)
            result_queue.put((captures, memory_context, reason))
        except Exception as exc:
            _log(
                f"ERROR: release capture worker failed: "
                f"{type(exc).__name__}: {exc}"
            )
            result_queue.put(
                (None, None, f"error: {type(exc).__name__}: {exc}")
            )

    def _handle_release(self) -> None:
        """Hotkey released: cancel previous worker, spawn new pipeline."""
        self._holding = False
        try:
            from ui_kit import sound
            sound("listen_end")       # the mic closes with a soft falling note
        except Exception:
            pass
        import time
        # GPT-Realtime mode: capture the screen and hand it to the
        # realtime session, which commits the spoken audio + requests a
        # response (model speaks back + emits point_at). Coordinate refinement
        # happens in _realtime_on_coordinate. Normal pipeline is skipped.
        if self._realtime is not None:
            _log("RELEASE handler START (realtime mode)")
            self.sig_hide_waveform.emit()
            try:
                import io as _io
                import base64 as _b64
                # Hide overlay so the model never sees our own avatar.
                self.sig_hide_overlay.emit()
                threading.Event().wait(0.05)
                captures = capture_all_screens()
                self.sig_show_overlay.emit()
                # Cursor-screen capture is first (capture_all_screens sorts it).
                self._realtime_capture = captures[0] if captures else None
                if self._realtime_capture is not None:
                    buf = _io.BytesIO()
                    self._realtime_capture.image.save(buf, format="JPEG", quality=85)
                    b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
                    self._realtime.respond(screenshot_jpeg_b64=b64)
                    # Show THINKING spinner until audio starts (on_audio_start hides it).
                    cx, cy = self._press_cursor_pos or get_cursor_position()
                    mon = monitor_containing(cx, cy, list_monitors())
                    if mon is not None:
                        self.sig_show_spinner.emit(cx, cy, mon)
            except Exception as exc:
                _log(f"REALTIME: release failed: {exc}")
            return
        _log("RELEASE handler START (Qt main thread)")
        self._released_at = time.monotonic()
        # LISTENING → THINKING transition: hide waveform, show spinner at the
        # current cursor position. Cursor polygon stays hidden while spinner
        # runs; it reappears when pipeline hides spinner + fires bezier.
        self.sig_hide_waveform.emit()

        # Snapshot release-time cursor synchronously. Taken here (not at
        # worker-start) so mouse motion during STT can't flip the
        # reuse-vs-recapture decision mid-flight. Reused for the spinner
        # dispatch below to avoid a redundant Win32 GetCursorPos call.
        release_cursor: tuple[int, int] | None = None
        try:
            cursor_x, cursor_y = get_cursor_position()
            release_cursor = (cursor_x, cursor_y)
            mon = monitor_containing(cursor_x, cursor_y, list_monitors())
            if mon is not None:
                self.sig_show_spinner.emit(cursor_x, cursor_y, mon)
        except Exception as exc:
            _log(f"WARN: show_spinner dispatch failed: {exc}")
        if release_cursor is None:
            release_cursor = self._press_cursor_pos or (0, 0)

        if self._worker_thread and self._worker_thread.is_alive():
            if self._guiding():
                _log("  a guide is running: it carries on unless this is a new question")
            else:
                _log("  cancelling previous worker + stopping TTS")
                self._cancel_event.set()
            self._tts.stop()
            # Same 200ms grace as press: keeps the aborted TTS tail from
            # contaminating the new PTT's transcript.
            self._stt.set_tts_grace_until(time.time() + 0.200)

        self._cancel_event = threading.Event()

        # Size-1 queue: capture worker pushes once, pipeline worker gets once.
        release_capture_queue: queue.Queue = queue.Queue(maxsize=1)

        # Launch capture worker BEFORE pipeline worker so it starts doing its
        # reuse-decision + potential mss.grab in parallel with stt.stop_recording.
        capture_worker_thread = threading.Thread(
            target=self._release_capture_worker,
            args=(release_cursor, self._current_app, release_capture_queue),
            daemon=True,
            name="clixy-release-capture",
        )
        capture_worker_thread.start()

        self._worker_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(
                self._current_app,
                self._current_title,
                self._cancel_event,
                release_capture_queue,
            ),
            daemon=True,
            name="clixy-pipeline",
        )
        self._worker_thread.start()

    # --- Pipeline worker (runs on worker thread) ---

    def _pipeline_worker(
        self,
        app_name: str,
        window_title: str,
        cancel: threading.Event,
        capture_queue: "queue.Queue",
        question: str | None = None,
    ) -> None:
        """Sequential pipeline: STT → capture → recall → stream → TTS → overlay.
        ``question`` skips listening (a follow-up button was clicked).

        ``capture_queue`` is populated in parallel by
        :meth:`_release_capture_worker` (launched in ``_handle_release``
        BEFORE this thread). This thread blocks on ``stt.stop_recording()``,
        then reads the capture result from the queue. Wall-clock becomes
        ``max(STT, capture)`` instead of ``STT + capture``.
        """
        dbg = DebugSession.start(app_name, window_title)
        # Log the ACTUAL providers this interaction used, so any
        # interaction.log shows exactly what ran (STT, LLM, TTS).
        dbg.log(
            f"PROVIDERS: STT={type(self._stt).__name__} | "
            f"LLM={getattr(self._ai, 'model_id', None) or type(self._ai).__name__} | "
            f"TTS={type(self._tts).__name__}"
        )
        stage = "speech-to-text"
        try:
            if cancel.is_set():
                return

            if question is not None:
                transcript = question
            else:
                if getattr(self._stt, "loading", False) is True:
                    self.sig_island_note.emit("Getting my ears ready: a one-time download, about 150 MB.")
                dbg.log("STT: calling stop_recording()...")
                transcript = self._stt.stop_recording()
                dbg.log(f"STT: {self._stt._chunk_count} audio chunks captured")
                dbg.log(f"STT: latest partial: {self._stt._latest_partial!r}")
                if getattr(self._stt, "reused_early", False):
                    dbg.log("STT: used the pass made while you talked (nothing new after it)")
            dbg.log(f"STT: final transcript ({len(transcript)} chars): {transcript!r}")
            _log(f"Transcript: {transcript!r}")
            running = guide.current() if self._guiding() else None
            if running is not None:
                what = guide.during_guide(transcript)
                if what == "keep":
                    dbg.log("GUIDE: keys held with nothing said: still guiding")
                    progress = running.progress()      # the notch shows this step again
                    self.sig_guide_step.emit(progress["now"], progress["n"], progress["total"], None)
                    return
                if what == "next":
                    if running.awaiting_yes:
                        dbg.log(f"GUIDE: {transcript!r}: go ahead with the step it asked about")
                        running.approve()
                    else:
                        dbg.log(f"GUIDE: {transcript!r}: on to the next step")
                        running.confirm()
                    return
                if what in ("drive", "hands off"):
                    on = what == "drive" and self._may_drive()
                    dbg.log(f"GUIDE: {transcript!r}: {'doing the steps' if on else 'pointing the way'}")
                    running.set_drive(on)
                    if on:
                        running.do_this_one()     # the step on show, unless it's one that's always theirs
                    return
                if what == "remark":
                    dbg.log(f"GUIDE: heard {transcript!r} about this step")
                    running.hear(transcript)
                    return
                dbg.log(f"GUIDE: a new question ends the guide: {transcript!r}")
                running.stop()
                if self._guide_cancel is not None:
                    self._guide_cancel.set()
            if not transcript.strip():
                dbg.log("NO SPEECH DETECTED, skipping interaction")
                _log("No speech detected, skipping.")
                keys = describe_hotkey(self._hotkey.combo) if self._hotkey is not None else "the keys"
                problem = self._mic_problem()
                dbg.log(f"MIC: problem={problem}")
                self.sig_island_note.emit(mic.problem_message(problem, keys))
                return
            self.sig_island_transcript.emit(transcript)

            if cancel.is_set():
                return

            # Read capture result from the worker that's been running in
            # parallel with stt.stop_recording above. Timeout is 5s (far
            # above any realistic capture time, ~300ms worst case), so if
            # the worker errored silently we fail loudly instead of hanging.
            # Fallback on timeout or error: use press-time captures if
            # available, else abort pipeline.
            try:
                captures, memory_context, capture_reason = capture_queue.get(
                    timeout=5.0
                )
            except queue.Empty:
                dbg.log("CAPTURE: worker timeout after 5s, falling back to press-time")
                captures = self._press_captures
                memory_context = self._press_memory
                capture_reason = "worker timeout, press-time fallback"

            if captures is None:
                if self._press_captures is not None:
                    dbg.log(
                        f"CAPTURE: worker failed ({capture_reason}), "
                        f"using press-time fallback"
                    )
                    captures = self._press_captures
                    memory_context = self._press_memory
                else:
                    dbg.log(
                        f"CAPTURE: worker failed and no press-time fallback "
                        f"({capture_reason}), aborting pipeline"
                    )
                    _log("ERROR: No screenshots available for the model, aborting.")
                    return

            dbg.log(f"CAPTURE: {capture_reason}")

            dbg.log(f"CAPTURE: {len(captures)} screen(s)")
            for i, c in enumerate(captures):
                dbg.log(f"  screen[{i}]: {c.target_width}x{c.target_height}, "
                        f"scale=({c.scale_x:.2f}, {c.scale_y:.2f}), "
                        f"monitor={c.monitor}, cursor={c.is_cursor_screen}")
                dbg.save_screenshot(c.image, f"screenshot_{i}.jpg")
            dbg.log(f"MEMORY: recalled {len(memory_context)} chars for {app_name}")

            if cancel.is_set():
                return

            user_text = transcript
            about = sysinfo.profile()
            if about:
                user_text = f"[this computer, use silently: {about}]\n{user_text}"
            if memory_context:
                user_text = (
                    f"[context from past sessions — use silently, don't summarize or reference it:]\n"
                    f"{memory_context}\n\n"
                    f"{user_text}"
                )

            # Curated KB recall (user-uploaded per-app docs). Empty tuple
            # if no .md file exists for this app, and the model answers from
            # vision + memory only. When present, ask_stream injects as a 2nd
            # cache_control system block (Anthropic) or concats into
            # system string (Gemini).
            #
            # Wrapped in try/except because KB files are user-controlled
            # and could be malformed (bad encoding, permission errors,
            # symlink loops, etc.). Failure here must NOT crash the
            # pipeline: the model can still answer with vision + memory.
            try:
                kb_content, kb_app_name = kb.recall(app_name)
            except Exception as exc:
                dbg.log(
                    f"KB: read failed ({type(exc).__name__}: {exc}), "
                    f"falling back to no-KB path"
                )
                kb_content, kb_app_name = "", ""
            if kb_content:
                dbg.log(
                    f"KB: injected {len(kb_content)} chars from "
                    f"{kb_app_name}.md"
                )
            else:
                dbg.log(f"KB: no file for {app_name}, skipping")

            images = [(c.image, c.label) for c in captures]
            cursor_capture = captures[0]

            if cancel.is_set():
                return

            # "How do I project my screen": one step at a time, each shown
            # exactly, the next one as soon as this one is done.
            if guide.wants_guide(transcript):
                stage = "AI"
                self._run_guide(transcript, cursor_capture, cancel, dbg, drive=guide.does_it(transcript))
                return

            # Learning something: a sketch note draws itself while the voice explains. A
            # hand-built 3D model that fits shows at once; anything else is sketched by the
            # thinking model. Either way the voice knows the card is coming, so it teaches
            # the thing instead of talking about the screen or what it can't show.
            if sketch.wants_sketch(transcript):
                instant = sketch.library_note(transcript)
                if instant is not None:
                    dbg.log(f"SKETCH: at once, the hand-built {instant['items'][0]['name']!r}")
                    self.sig_show_sketch.emit(instant)
                else:
                    threading.Thread(target=self._make_sketch, args=(transcript, cursor_capture, cancel, dbg),
                                     daemon=True, name="clixy-sketch").start()
                user_text = f"{user_text}\n\n{sketch.VOICE_HINT}"

            dbg.log("LLM: streaming started...")
            _log("Asking the model...")

            # Arm one-shot first-audible-word log. Fires on the first
            # successful sounddevice.play(samples) in the TTS playback
            # worker, measuring the gap between "LLM: streaming started"
            # (when we open the HTTP connection) and the actual moment
            # the user hears something. Per-interaction (slot clears
            # after firing once); next interaction re-arms.
            self._tts.arm_first_chunk_callback(
                lambda: dbg.log("TTS: first audible chunk played")
            )

            # Sentence-level TTS streaming. Flush complete sentences from
            # the buffer as each .!? boundary arrives, so TTS starts on
            # sentence 1 (~1200ms into the stream) instead of
            # after the full response (~3700ms). Saves ~2s perceived latency.
            #
            # Tag-safety: stop flushing the moment '[', '{' or '`' appears in the
            # buffer (a [POINT] tag, or a local model's JSON box, which may put
            # its label first). On stream close, use result.spoken_text
            # (tag-stripped) to compute + flush the tail.
            sentence_buffer = ""
            tag_started = False
            flushed: list[str] = []

            # Draw-on-screen teaching mode. Uses the module-level cached
            # config.ANNOTATION_MODE (resolved ONCE at import), NOT a fresh
            # resolve_setting(), so there is ZERO per-interaction keyring
            # read/write on the hot path (resolve_setting writes to keyring on
            # every call when the value is in env). Trade-off: toggling needs an
            # app restart. When on, swap in the annotation system prompt so the
            # model emits shape tags. Off (the default) keeps the [POINT] cursor
            # prompt.
            # Drawing is on in Settings, or this question asks to be shown.
            annotation_mode = ANNOTATION_MODE == "on" or wants_drawing(transcript)
            _ask_kwargs = dict(
                images=images,
                transcript=user_text,
                history=self._history,
                kb_content=kb_content,
                kb_app_name=kb_app_name,
            )
            if annotation_mode:
                _ask_kwargs["system_prompt"] = _CLICKY_ANNOTATION_SYSTEM_PROMPT
            if type(self._ai).__name__ != "OllamaClient":  # its box prompt is matched by identity
                # It can open apps, websites and Settings pages, and knows this PC's apps.
                _ask_kwargs["system_prompt"] = (
                    _ask_kwargs.get("system_prompt", _CLICKY_SYSTEM_PROMPT) + apps.prompt_block())

            stage = "AI"
            handed = False
            with self._ai.ask_stream(**_ask_kwargs) as stream:
                first_token = True
                first_sentence_s = None
                for delta in stream.text_deltas():
                    if cancel.is_set():
                        return
                    if first_token:
                        dbg.log("LLM: first token")
                        first_token = False
                    sentence_buffer += delta
                    if not flushed and _DO_TAG_RE.match(sentence_buffer):
                        handed = True          # it's a job, not a question: Clixy does it
                        break
                    if any(mark in sentence_buffer for mark in "[{`"):
                        tag_started = True
                    if not tag_started:
                        sentences, sentence_buffer = flush_sentences(sentence_buffer, clause=not flushed)
                        for s in sentences:
                            if cancel.is_set():
                                return
                            self._tts.speak_sentence(with_pause(s) if not flushed else s)
                            self.sig_island_say.emit(s)
                            if first_sentence_s is None:
                                first_sentence_s = time.monotonic() - getattr(self, "_released_at", time.monotonic())
                            flushed.append(s)

                if not handed:
                    result = stream.final_result()
                    usage = stream.usage()
                    dbg.log(f"LLM: usage {usage}")
                    self.sig_island_meta.emit(
                        _answer_meta(first_sentence_s, getattr(self._ai, "model_id", ""), usage)
                    )

            if handed and not cancel.is_set():
                dbg.log("LLM: [DO], so Clixy does it itself")
                self._run_guide(transcript, cursor_capture, cancel, dbg, drive=True)
                return

            if cancel.is_set():
                return

            dbg.log(f"LLM: done ({len(result.spoken_text)} chars)")
            dbg.log(f"LLM: spoken_text: {result.spoken_text!r}")
            dbg.log(f"LLM: coordinate={result.coordinate}, label={result.element_label!r}, screen={result.screen_number}")

            # Annotation mode: the shape tags ([ARROW]/[CIRCLE]/...) are still
            # in result.spoken_text because ai.py's parser only strips [POINT].
            # Parse + strip them here so TTS never reads coordinates aloud, and
            # map their screenshot coords to physical for the overlay.
            spoken_text = result.spoken_text
            # "Open Spotify", "go to youtube", "take me to display settings": open it.
            spoken_text, to_open = apps.parse_open(spoken_text)
            spoken_text = _STRAY_POINT_RE.sub("", spoken_text).strip()
            for target in to_open[:1]:
                try:
                    opened = apps.open_target(target)
                except Exception as exc:
                    opened = ""
                    dbg.log(f"OPEN: {target!r} failed: {type(exc).__name__}: {exc}")
                dbg.log(f"OPEN: {target!r} -> {opened!r}")
                if not opened:
                    self.sig_island_note.emit(f"I couldn't find {target} on this PC.")
            phys_annotations: list = []
            _anns: list = []
            if annotation_mode and spoken_text:
                # Strip FIRST, outside the try: parse_annotations is pure regex
                # and cannot raise, so spoken_text is ALWAYS tag-stripped before
                # the tail flush (coords never reach TTS even if the coordinate
                # transform below fails). Only the physical transform is guarded.
                spoken_text, _anns = parse_annotations(spoken_text)

            # Flush the tail (everything in spoken_text that hasn't yet been
            # sent to TTS). Uses the tag-stripped spoken_text so the
            # [POINT:x,y:label] and shape tags are never spoken aloud.
            if spoken_text:
                tail = unspoken_tail(spoken_text, flushed)
                if tail:
                    dbg.log(f"TTS: flushing tail ({len(tail)} chars)")
                    self._tts.speak_sentence(tail)
                    self.sig_island_say.emit(tail)

            if cancel.is_set():
                return

            # Aim the marks while the answer is being spoken: a close-up look
            # per mark for its exact box (aim.py), then on Windows a snap to
            # the real control's edges (snap.py). Never breaks the pipeline.
            marks_busy = None
            if _anns:
                try:
                    import aim
                    _anns = aim.aim(self._ai, cursor_capture, _anns, question=transcript, dbg=dbg)
                    if cancel.is_set():
                        return
                    phys_annotations = _annotations_to_physical(_anns, cursor_capture)
                    import snap
                    phys_annotations = snap.refine(phys_annotations, cursor_capture.monitor, dbg=dbg)
                    marks_busy = aim.busy_map(cursor_capture.image)
                    dbg.log(f"ANNOTATIONS: {len(phys_annotations)} marks to draw")
                except Exception as exc:  # never break the pipeline
                    dbg.log(f"ANNOTATIONS: skipped: {type(exc).__name__}: {exc}")
                    phys_annotations = []
            if cancel.is_set():
                return

            _log(f"Response: {result.spoken_text[:80]}...")

            # Grid-locator fallback for Ollama / weak vision models.
            # If the model returned no [POINT:x,y] tag AND the client is a
            # weak-vision one AND the query was directional, run grid-locator
            # on the cursor screenshot to derive coordinates. Returns physical
            # virtual-desktop coords (same convention as unscale_claude_coords
            # output), or None if the locator can't find a target.
            #
            # CANCEL GUARD: skip the locator entirely if cancel fired between
            # stream.final_result() and here (e.g. a re-press during sentence
            # streaming). Otherwise the locator's 2 LLM calls would run for
            # 5-10s on a cancelled worker and emit pointer + memory side
            # effects for an interaction the user already aborted.
            if cancel.is_set():
                return
            if annotation_mode:
                # Annotation mode draws SHAPES, not a grid-located cursor point.
                # Skip the grid-locator entirely: it would otherwise fire for
                # weak-vision providers (OpenAI/Ollama) on directional queries
                # since annotation responses carry no [POINT] tag, adding 2 extra
                # LLM calls + emitting an unrelated cursor point that competes
                # with the shapes. Keeps annotation behavior identical across
                # providers.
                locator_phys_xy = None
            else:
                locator_phys_xy = _maybe_locate_via_grid(
                    ai_client=self._ai,
                    result=result,
                    cursor_capture=cursor_capture,
                    query=transcript,
                    dbg=dbg,
                )
            # POST-LOCATOR cancel guard: if locator just ran (took seconds on
            # Ollama), the user may have pressed the hotkey again. Stop
            # before emitting any pointer / memory side effects, which would
            # race the new pipeline and write history for an interaction that
            # no longer matters.
            if cancel.is_set():
                return

            # Draw mode often answers with shapes only. When there are marks,
            # the buddy flies over and draws them itself (show_annotations);
            # the anchor is still what memory records as where it pointed.
            draw_anchor = (
                _drawing_anchor(_anns) if annotation_mode and not result.coordinate else None
            )
            buddy_draws = annotation_mode and bool(phys_annotations)

            if buddy_draws:
                dbg.log(f"COORDS: the buddy draws {len(phys_annotations)} marks")
                self.sig_hide_spinner.emit()
            elif result.coordinate:
                x_claude, y_claude = result.coordinate
                screen_num = result.screen_number

                # Save screenshot with red marker at the model's coordinate
                dbg.save_screenshot(
                    cursor_capture.image,
                    "screenshot_with_marker.jpg",
                    coordinate=(x_claude, y_claude),
                )

                target_capture = cursor_capture
                if screen_num is not None:
                    for c in captures:
                        if f"screen{screen_num}" in c.label.replace(" ", ""):
                            target_capture = c
                            break

                phys_x, phys_y = unscale_claude_coords(
                    claude_x=x_claude,
                    claude_y=y_claude,
                    scale_x=target_capture.scale_x,
                    scale_y=target_capture.scale_y,
                    monitor_left=target_capture.monitor["left"],
                    monitor_top=target_capture.monitor["top"],
                    target_w=target_capture.target_width,
                    target_h=target_capture.target_height,
                )
                dbg.log(f"COORDS: claude=({x_claude},{y_claude}) -> physical=({phys_x},{phys_y})")
                dbg.log(f"COORDS: scale=({target_capture.scale_x:.2f},{target_capture.scale_y:.2f}), "
                        f"monitor_offset=({target_capture.monitor['left']},{target_capture.monitor['top']})")
                # THINKING → FLYING: hide spinner BEFORE the point_at signal
                # so the overlay paints cleanly (no flicker of spinner +
                # cursor at the same time during the transition).
                self.sig_hide_spinner.emit()
                self.sig_point_label.emit(result.element_label or "")
                self.sig_point_at.emit(phys_x, phys_y, target_capture.monitor)
            elif draw_anchor is not None:
                # Shapes are always on the cursor screen.
                phys_x, phys_y = unscale_claude_coords(
                    claude_x=draw_anchor[0],
                    claude_y=draw_anchor[1],
                    scale_x=cursor_capture.scale_x,
                    scale_y=cursor_capture.scale_y,
                    monitor_left=cursor_capture.monitor["left"],
                    monitor_top=cursor_capture.monitor["top"],
                    target_w=cursor_capture.target_width,
                    target_h=cursor_capture.target_height,
                )
                dbg.log(f"COORDS: drawing anchor {draw_anchor} -> physical=({phys_x},{phys_y})")
                self.sig_hide_spinner.emit()
                self.sig_point_at.emit(phys_x, phys_y, cursor_capture.monitor)
            elif locator_phys_xy is not None:
                # Grid-locator fallback: coords are already in PHYSICAL
                # virtual-desktop space, so skip unscale_claude_coords and
                # emit directly.
                phys_x, phys_y = locator_phys_xy
                dbg.log(f"COORDS: grid-locator -> physical=({phys_x},{phys_y})")
                self.sig_hide_spinner.emit()
                self.sig_point_label.emit(result.element_label or "")
                self.sig_point_at.emit(phys_x, phys_y, cursor_capture.monitor)
            else:
                dbg.log("COORDS: no coordinate returned (text-only response)")
                # Text-only path: spinner still needs to go away so the cursor
                # goes back to following the mouse during TTS playback.
                self.sig_hide_spinner.emit()

            # Draw the teaching annotations (additive to the cursor).
            # Independent of the [POINT]/locator branches above: shapes show
            # during SPEAKING and auto-clear after 30s. Emit on EVERY annotation-
            # mode turn (even with zero shapes) so a no-shape answer clears any
            # stale circles/arrows from the previous turn immediately, instead of
            # leaving them on screen (misleading) until the 30s timer fires.
            # Final cancel guard: if the user re-pressed (which cancels this
            # worker), do NOT repaint; the press already cleared the overlay.
            if cancel.is_set():
                return
            if annotation_mode:
                self.sig_show_annotations.emit(
                    phys_annotations, cursor_capture.monitor, marks_busy
                )

            pointer_targets = []
            if result.coordinate:
                pointer_targets.append(result.coordinate)
            elif draw_anchor is not None:
                pointer_targets.append(draw_anchor)
            elif locator_phys_xy is not None:
                # Memory recording: store the grid-locator coords (physical
                # virtual-desktop space) so future recall can reference them
                # the same way model coords are referenced.
                pointer_targets.append(locator_phys_xy)

            # Use the tag-stripped `spoken_text` (== result.spoken_text in
            # normal mode; shape-tags removed in annotation mode) so memory +
            # history never store [CIRCLE]/[ARROW] control tags; otherwise
            # recall() would re-inject coordinates into future prompts and
            # stale tags could resurface on non-annotation turns.
            self.sig_record_memory.emit(
                app_name,
                window_title,
                transcript,
                spoken_text,
                pointer_targets,
            )

            self._history.append({
                "role": "user",
                "content": [{"type": "text", "text": transcript}],
            })
            self._history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": spoken_text}],
            })
            if len(self._history) > _MAX_HISTORY_EXCHANGES * 2:
                self._history = self._history[-(
                    _MAX_HISTORY_EXCHANGES * 2
                ):]

            dbg.log("DONE: interaction complete")

        except Exception as exc:
            if not cancel.is_set():
                dbg.log(f"ERROR: {type(exc).__name__}: {exc}")
                _log(f"ERROR: Pipeline failed: {type(exc).__name__}: {exc}")
                self._notify(stage, exc)
        finally:
            # Always hide spinner on pipeline exit (success, error, cancel).
            # Prevents a stuck-spinning arc if anything above raises before
            # the normal hide_spinner emit fires.
            self.sig_hide_spinner.emit()
            if not cancel.is_set():
                self.sig_island_done.emit()
            # Kept open a little longer: the first audio often plays after the
            # model has finished, and its "first audible chunk" line belongs here.
            closer = threading.Timer(20.0, dbg.close)
            closer.daemon = True
            closer.start()

    # --- Signal slot handlers (run on Qt main thread) ---

    def _island(self, method: str, *args) -> None:
        if self._overlay is not None and hasattr(self._overlay, method):
            getattr(self._overlay, method)(*args)

    def _make_sketch(self, question: str, capture, cancel: threading.Event, dbg) -> None:
        """Ask the thinking model for a sketch note and show it (worker thread)."""
        try:
            with self._smart_ai.ask_stream(images=[(capture.image, capture.label)], transcript=question,
                                           history=[], system_prompt=sketch.PROMPT,
                                           max_tokens=9000) as stream:   # room for a sculpted 3D model's parts
                reply = "".join(stream.text_deltas())
        except Exception as exc:
            dbg.log(f"SKETCH: failed: {type(exc).__name__}: {exc}")
            return
        note = sketch.parse_note(reply)
        dbg.log(f"SKETCH: {'drawing ' + repr(note.get('title')) if note else 'none'} ({len(reply)} chars)")
        if note is not None and not cancel.is_set():
            self.sig_show_sketch.emit(note)

    def _on_show_sketch(self, note: dict, again: bool = False) -> None:
        if self._note_card is None:
            self._note_card = sketch.NoteCard()
            self._note_card.followup.connect(self.ask_text)
            self._note_card.cleared.connect(lambda: self.sig_clear_annotations.emit())
        self._note_card.show_note(note, again=again)

    def show_saved_note(self, note: dict) -> None:
        """A note opened from the panel: the note card again, drawing itself in,
        its 3D model live (Qt main thread)."""
        self._on_show_sketch(note, again=True)

    def ask_text(self, question: str) -> None:
        """Ask ``question`` as if it had been spoken (a sketch note's follow-up
        buttons): the screen is looked at afresh and the answer is spoken."""
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._tts.stop()
        self._cancel_event = threading.Event()
        captured: queue.Queue = queue.Queue(maxsize=1)
        threading.Thread(target=self._release_capture_worker, args=((0, 0), self._current_app, captured),
                         daemon=True, name="clixy-ask-capture").start()
        self._worker_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(self._current_app, self._current_title, self._cancel_event, captured),
            kwargs={"question": question}, daemon=True, name="clixy-pipeline")
        self._worker_thread.start()

    def _guiding(self) -> bool:
        """A guide is running and hasn't been told to stop."""
        stop = self._guide_cancel
        return stop is not None and not stop.is_set() and guide.current() is not None

    @staticmethod
    def _may_drive() -> bool:
        """"Do it for me" is allowed (Settings > General)."""
        return resolve_setting("DO_IT_FOR_ME", default="on").lower() == "on"

    def _run_guide(self, goal: str, capture, cancel: threading.Event, dbg, drive: bool = False) -> None:
        """Guide toward ``goal`` one step at a time (guide.py), on this worker thread.
        ``drive``: asked to do it ("... for me"), so Clixy does the steps itself."""
        import hands
        import snap

        dbg.log(f"GUIDE: {goal!r}")
        self._guide_cancel = cancel

        quick_ai = getattr(self, "_quick_ai", None)
        if quick_ai is None:             # Sonnet without the thinking pass, made once and kept warm
            from ai import thinking_variant
            quick_ai = self._quick_ai = (thinking_variant(self._ai, effort=None)
                                         if self._smart_ai is not self._ai else self._ai)

        def ask(cap, text: str):
            # Streams: the guide speaks each sentence the moment it's in, before the tags arrive.
            # The first step plans the route (thinking); the rest answer straight away, so
            # each step keeps up with them.
            client = quick_ai if session.progress()["n"] >= 1 else self._smart_ai
            with client.ask_stream(images=[(cap.image, cap.label)], transcript=text, history=[],
                                   system_prompt=guide.system_prompt()) as stream:
                yield from stream.text_deltas()

        def fresh_capture():
            self.sig_hide_overlay.emit()
            threading.Event().wait(0.05)
            try:
                captures = capture_all_screens()
            finally:
                self.sig_show_overlay.emit()
            return next((c for c in captures if c.is_cursor_screen), captures[0] if captures else None)

        def say(text: str, n: int, total: int, where=None) -> None:
            self.sig_guide_step.emit(text, n, total, where)   # the voice already went out through speak=

        from memory import RouteBook
        keep = resolve_setting("KEEP_HISTORY", default="on").lower() == "on"
        session = guide.Guide(
            ask=ask,
            capture=fresh_capture,
            list_elements=lambda monitor: snap.elements(monitor),
            show=lambda marks, monitor: self.sig_show_annotations.emit(marks, monitor, None),
            clear=lambda: self.sig_clear_annotations.emit(),
            say=say,
            note=self.sig_island_note.emit,
            cancelled=cancel.is_set,
            foreground=snap.foreground,
            about=sysinfo.profile(),
            refine=snap.refine,
            open=apps.open_target,
            speak=self._tts.speak_sentence,
            windows=snap.windows,
            focus=snap.focus,
            arrange=snap.side_by_side,
            knowhow=kb.knowhow,
            routes=RouteBook() if keep else None,   # history off in Settings: no routes kept
            hands=hands if self._may_drive() else None,
            dbg=dbg,
        )
        if drive and self._may_drive():
            dbg.log("GUIDE: doing it for them")
            session.set_drive(True)
        try:
            last = session.run(goal, capture)
        finally:
            if self._guide_cancel is cancel:
                self._guide_cancel = None
        if not cancel.is_set():
            self.sig_clear_annotations.emit()
            self._history.append({"role": "user", "content": [{"type": "text", "text": goal}]})
            self._history.append({"role": "assistant", "content": [{"type": "text", "text": last or "(guided step by step)"}]})
        # Home's Done today and Up next (jobs.py): a finished job is kept (history on) and
        # the next in line starts; a job stopped short (Esc, Stop, a new question) clears the line.
        import jobs
        if getattr(session, "finished", False) and not cancel.is_set():
            if keep:
                jobs.record(goal, did=getattr(session, "drove", False), steps=max(1, len(session.progress()["done"])))
            following = jobs.next_job()
            if following:
                dbg.log(f"GUIDE: next in line: {following!r}")
                self.sig_next_job.emit(following)
        else:
            jobs.clear_queue()
        dbg.log("GUIDE: finished")

    def _on_hide_overlay(self) -> None:
        if self._overlay:
            self._overlay.hide_for_capture()

    def _on_show_overlay(self) -> None:
        if self._overlay:
            self._overlay.show_after_capture()

    def _on_point_at(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.point_at(physical_x, physical_y, monitor)

    def _on_record_memory(
        self,
        app_name: str,
        window_title: str,
        question: str,
        response: str,
        pointer_targets: list,
    ) -> None:
        if not resolve_setting("KEEP_HISTORY", default="on").lower() == "on":
            return  # history is off in Settings: keep nothing
        try:
            self._memory.record(
                app_name=app_name,
                window_title=window_title,
                user_question=question,
                claude_response=response,
                pointer_targets=pointer_targets,
            )
        except Exception as exc:
            _log(f"ERROR: Memory record failed: {exc}")

    # LISTENING-state slot handlers (run on Qt main thread)

    def _on_show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_waveform(physical_x, physical_y, monitor)

    def _on_hide_waveform(self) -> None:
        if self._overlay:
            self._overlay.hide_waveform()

    def _on_audio_level(self, level: float) -> None:
        if self._overlay:
            self._overlay.set_audio_level(level)

    # THINKING-state slot handlers (Qt main thread)

    def _on_show_spinner(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_spinner(physical_x, physical_y, monitor)

    def _on_hide_spinner(self) -> None:
        if self._overlay:
            self._overlay.hide_spinner()

    def _on_show_annotations(self, annotations: list, monitor: dict, busy=None) -> None:
        if self._overlay:
            self._overlay.show_annotations(annotations, monitor, busy=busy)

    def _on_clear_annotations(self) -> None:
        if self._overlay:
            self._overlay.clear_all_annotations()


_T0 = __import__("time").time()


# --- Single-instance mutex --------------------------------------------------
#
# Without this, double-clicking the installed shortcut spawns multiple
# Clicky.exe processes. Each installs its own pynput.Listener (suppress=False
# is observe-only, so multiple listeners coexist), so one hotkey press
# fires N parallel STT->LLM->TTS pipelines. User hears N overlapping
# voices answering one question.
#
# Pattern: Win32 named mutex acquired before QApplication construction.
# Whoever wins the kernel-level CreateMutexW race holds the mutex for their
# process lifetime; second instance sees ERROR_ALREADY_EXISTS and exits.
# Same pattern Spotify, Slack, Discord, Raycast all use.

_MUTEX_NAME = "Local\\Clixy-SingleInstance-v1"
"""Per-logon-session namespace (Local\\): admin and non-admin in the same
session see the same mutex (correct), but different Windows users on the
same machine each get their own Clicky (also correct). Global\\ would
block the second user on a shared RDP host."""

_ERROR_ALREADY_EXISTS = 183  # winerror.h ERROR_ALREADY_EXISTS


def _acquire_single_instance_mutex(kernel32=None):
    """Try to acquire the named mutex. Returns the HANDLE (truthy int) if
    we are the first instance, ``None`` if another Clicky already owns it,
    or the string ``"fail-open"`` on rare CreateMutexW genuine failure (in
    which case caller should proceed with startup: better to risk a
    duplicate than block the user with a broken installer).

    The ``kernel32`` parameter is a DI hook for tests (pass a MagicMock).
    Production passes ``None`` and the function looks up the real
    ``ctypes.windll.kernel32`` itself, applying the explicit ``restype`` /
    ``argtypes`` signatures that prevent x64 HANDLE truncation (without
    them, ctypes defaults to ``c_int`` = 32-bit, which silently corrupts
    64-bit handles on x64 Windows).

    The returned handle MUST be retained for the process lifetime (a
    module-global reference is sufficient). The Windows kernel auto-
    releases the mutex when the process terminates (including on crash
    or Task Manager kill), so no explicit cleanup is needed at shutdown.
    """
    if kernel32 is None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    # bInitialOwner=False: for single-instance detection we need the
    # kernel object's *existence* as a flag, not ownership/synchronization
    # semantics. Setting True would make first instance pointlessly own a
    # mutex it never releases.
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    # Note: ctypes maps c_void_p NULL to Python None (NOT integer 0). Test
    # mocks use return_value=0 for convenience; both are falsy so `not handle`
    # handles both representations safely.
    if not handle:
        # Genuine CreateMutexW failure (rare). Fail open: don't block startup.
        return "fail-open"
    # GetLastError MUST be the next Win32 call after CreateMutexW; any
    # intervening kernel32 call could clobber the thread-local last-error.
    # The `if not handle` branch above is pure Python, so it is safe.
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        # Another Clicky owns the mutex. Close OUR handle to the same
        # kernel object (the original mutex is still held by the first
        # instance) so we don't leak.
        kernel32.CloseHandle(handle)
        return None
    return handle


# --- Listening chime (sfx.py's "listen_start") ------------------------------

def _play_chime_async() -> threading.Thread:
    """Play the 'mic is hot' sound on hotkey PRESS without blocking it.

    It is sfx.py's "listen_start", a bright rising two-note bloop, mixed by
    sfx's one open output stream (opening a device takes 300-700 ms, so it
    stays open). sfx.play only queues the sound, and it runs on a thread so
    the press handler can never wait on audio. Errors are swallowed: the
    chime is UX-only.
    """
    def _play() -> None:
        try:
            import sfx
            sfx.play("listen_start")
        except Exception:
            pass
    thread = threading.Thread(target=_play, daemon=True, name="chime")
    thread.start()
    return thread


def _warm_chime() -> threading.Thread:
    """Render the interface sounds and open their output stream at startup,
    off the main thread, so the first sound is instant."""
    def _open() -> None:
        try:
            import sfx
            sfx.warm(wait=True)
        except Exception:
            pass
    thread = threading.Thread(target=_open, daemon=True, name="chime-warm")
    thread.start()
    return thread


def _log(msg: str) -> None:
    """Print a log line with millisecond-precision elapsed time."""
    import time
    elapsed = (time.time() - _T0) * 1000
    ts = time.strftime("%H:%M:%S")
    print(f"[clicky {ts} +{elapsed:.0f}ms] {msg}", flush=True)


# --- Provider resolution ----------------------------------------------------


def _resolve_tts_credentials() -> tuple[str, str | None]:
    """Resolve (TTS_PROVIDER, api_key_for_that_provider) at startup.

    Reads TTS_PROVIDER via config.resolve_setting (env→keyring→default)
    then resolves the right API key via config.resolve_api_key based on
    the selected provider. Returned to __main__ which dispatches via
    tts.create_tts_client(provider, api_key).
    """
    provider = resolve_setting("TTS_PROVIDER", default=DEFAULT_TTS_PROVIDER)
    if provider == "kokoro":
        return provider, ""  # local offline TTS, no API key
    if provider == "elevenlabs":
        api_key = resolve_api_key("ELEVENLABS_API_KEY")
    else:
        api_key = resolve_api_key("CARTESIA_API_KEY")
    return provider, api_key


def _resolve_stt_credentials() -> tuple[str, str]:
    """Resolve (STT_PROVIDER, api_key) at startup. Mirrors
    _resolve_tts_credentials. faster-whisper is local offline (no key).
    Resolved fresh (env→keyring) so a Settings change is honored on restart.
    """
    provider = resolve_setting("STT_PROVIDER", default=DEFAULT_STT_PROVIDER)
    if provider in ("faster-whisper", "local"):
        return "faster-whisper", ""
    if provider == "assemblyai":
        return "assemblyai", resolve_api_key("ASSEMBLYAI_API_KEY") or ""
    return "cartesia", resolve_api_key("CARTESIA_API_KEY") or ""


def _openrouter_key() -> str:
    """Any OpenRouter (sk-or-) key the user pasted in ANY LLM slot.

    One OpenRouter key works for every model namespace (anthropic/, openai/,
    google/), so a power user pastes it once and it is reused across all LLM
    providers (cache + reuse). Direct provider keys (sk-ant-, sk-..., Google
    AIza) are NOT reused here: they only authenticate their own provider, so
    each is matched by its own slot. create_ai_client then routes an sk-or-
    key to OpenRouter and a direct key to that provider's native endpoint.
    """
    for slot in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        k = resolve_api_key(slot) or ""
        if k.startswith("sk-or-"):
            return k
    return ""


def _resolve_llm_credentials() -> tuple[str, str]:
    """Resolve (effective_model_id, api_key) at startup based on LLM_PROVIDER.

    Reads LLM_PROVIDER via config.resolve_setting (env→keyring→default).
    - LLM_PROVIDER='ollama'    → returns ("ollama/<OLLAMA_MODEL_VISION>", ""),
                                  api_key empty because local Ollama is
                                  unauthenticated. create_ai_client factory
                                  routes `ollama/*` prefix to OllamaClient.
    - LLM_PROVIDER='anthropic' → returns (MODEL_ID, ANTHROPIC_API_KEY).
                                  Factory routes MODEL_ID prefix
                                  ('anthropic/...' or 'claude...') to
                                  AnthropicClient.
    - any other value          → falls back to anthropic path (forward-compat).
    - nothing chosen yet       → config.default_llm_provider(): the provider
                                  there is a key for, else OpenAI.

    Note: MODEL_ID env var takes precedence over LLM_PROVIDER ONLY when
    MODEL_ID already routes to a non-Anthropic prefix (the factory dispatches
    on MODEL_ID prefix first). For the GUI-flow (user clicks Ollama in the
    dropdown), LLM_PROVIDER='ollama' is sufficient; they never need to know
    about MODEL_ID.
    """
    provider = resolve_setting("LLM_PROVIDER", default=default_llm_provider())
    if provider in ("openai", "openai-realtime"):
        # OpenAI native vision. 'openai/' prefix routes create_ai_client →
        # OpenAIVisionClient. For 'openai-realtime', the GPT-Realtime
        # speech-to-speech session runs as a parallel pipeline (see
        # _setup_realtime); the main ai_client built here is a valid OpenAI
        # vision client, harmless if unused.
        # Direct OpenAI key (sk-...) → api.openai.com; an OpenRouter key →
        # OpenRouter (create_ai_client routes by prefix). If the OpenAI slot is
        # empty, reuse a cached OpenRouter key so one sk-or- key serves OpenAI too.
        return f"openai/{OPENAI_MODEL_VISION}", resolve_api_key("OPENAI_API_KEY") or _openrouter_key()
    if provider == "gemini":
        # Own slot first (a direct Google AI Studio key, or a per-Gemini
        # OpenRouter key), else reuse any cached OpenRouter key so one sk-or-
        # key serves Gemini too and there's no extra key to enter.
        # create_ai_client routes by key prefix.
        return GEMINI_MODEL_VISION, resolve_api_key("GEMINI_API_KEY") or _openrouter_key()
    if provider == "ollama":
        # Log detected Ollama version + warn about model/version mismatches
        # at startup. Stderr only: the Settings dialog catches this case
        # interactively. This covers users who set OLLAMA_MODEL_VISION via
        # env var and never touch the Settings UI.
        try:
            from ollama_health import (
                check_model_compatibility,
                detect_ollama_version,
            )
            version = detect_ollama_version(OLLAMA_HOST)
            if version is None:
                print(
                    f"[ollama] could not reach {OLLAMA_HOST}/api/version "
                    "(is `ollama serve` running?)",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[ollama] detected version {version}, "
                    f"using model {OLLAMA_MODEL_VISION}",
                    file=sys.stderr,
                )
                warning = check_model_compatibility(OLLAMA_MODEL_VISION, version)
                if warning:
                    print(f"[ollama] WARNING: {warning}", file=sys.stderr)
        except Exception as exc:
            # Don't fail startup over a logging helper.
            print(f"[ollama] version-check skipped: {exc}", file=sys.stderr)

        # Construct an ollama/ prefixed model id so create_ai_client routes
        # correctly. api_key is empty (Ollama is unauthenticated local).
        return f"ollama/{OLLAMA_MODEL_VISION}", ""
    # anthropic (default). A MODEL_ID env var overrides everything; otherwise
    # ANTHROPIC_MODEL (env or keyring) picks the model.
    ant_key = resolve_api_key("ANTHROPIC_API_KEY") or _openrouter_key()
    if os.getenv("MODEL_ID"):
        return MODEL_ID, ant_key
    ant_model = resolve_model("ANTHROPIC_MODEL", "claude-haiku-5-5")
    return f"anthropic/{ant_model}", ant_key


def _should_connect_stt(realtime) -> bool:
    """Whether to open the AssemblyAI STT mic at startup.

    FALSE in realtime mode: GPT-Realtime owns the 24 kHz mic (realtime.py
    start_turn opens its own RawInputStream). If the 16 kHz AssemblyAI STT
    mic opens too, both grab the input device and the realtime path produces
    no audio. So in realtime mode we skip STT entirely.
    """
    return realtime is None


# --- Manual entry point -------------------------------------------------------

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    import devmode
    if devmode.enabled():
        devmode.log_to_file()

    # Single-instance check. MUST run before QApplication construction
    # so a duplicate-launch exits fast without spinning up Qt / SDKs.
    # The handle is assigned to a __main__-module binding to keep it
    # alive for the process lifetime; Windows auto-releases on exit.
    _mutex_handle = _acquire_single_instance_mutex()
    if _mutex_handle is None:
        # Another Clicky is already running. Show a Win32 messagebox
        # (no Qt dependency) telling the user where to look, then exit
        # cleanly. MB_ICONINFORMATION = 0x40.
        ctypes.windll.user32.MessageBoxW(
            None,
            "Clixy is already running.\n\n"
            "Look for your buddy's icon in your system tray "
            "(bottom-right corner of your screen). Right-click it "
            "for the Settings and Quit menu.",
            "Clixy already running",
            0x40,
        )
        sys.exit(0)
    # _mutex_handle == "fail-open" or a real handle: proceed with startup.

    print("=" * 70)
    print(f"Clixy: hold {describe_hotkey(_hotkey_combo())} and ask")
    print("=" * 70)

    set_dpi_awareness()
    qt_app = QApplication(sys.argv)
    # Fonts, the app-wide stylesheet and the window icon all follow the
    # chosen avatar's theme, and switch with it. clixy.spec's `icon=` embeds
    # assets/clixy.ico as the EXE's own icon.
    import theme
    theme.apply(qt_app)
    qt_app.setWindowIcon(theme.app_icon())
    # Tray-only mode: closing the overlay (or any internal window)
    # must NOT exit the app; only the Quit menu item should.
    qt_app.setQuitOnLastWindowClosed(False)

    # First-launch / missing-keys flow: show modal until all 3 keys
    # are saved. Modal blocks the QApplication.exec() loop so this
    # is synchronous from main()'s perspective.
    from settings_dialog import SettingsDialog, required_keys_present
    if not required_keys_present():
        # A new install gets the three-step welcome; a setup missing some
        # other key (a cloud voice, say) goes straight to Settings.
        from onboarding import Onboarding, needs_onboarding
        if needs_onboarding():
            print("First launch: showing the welcome...")
            dlg = Onboarding()
        else:
            print("A key is missing: showing Settings...")
            dlg = SettingsDialog()
            dlg.show_page("advanced")
        if dlg.exec() != dlg.DialogCode.Accepted:
            print("Setup cancelled by user. Exiting.")
            sys.exit(1)
        # Sanity check: Save was clicked AND all 3 keys are now resolvable.
        if not required_keys_present():
            print(
                "ERROR: Setup completed but at least one API key still "
                "missing. Aborting."
            )
            sys.exit(1)

    # Resolve effective LLM model + api key based on LLM_PROVIDER setting
    # (Settings dialog dropdown). Reads keyring fresh so any change the user
    # just made in the modal is honored. See _resolve_llm_credentials for the
    # per-provider dispatch.
    _llm_model_id, _llm_api_key = _resolve_llm_credentials()

    # Listening provider from STT_PROVIDER: Cartesia by default (the voice's key).
    # Resolved fresh so a Settings change is honored without a code edit.
    _stt_provider, _stt_key = _resolve_stt_credentials()

    # Dispatch TTS subclass based on TTS_PROVIDER setting.
    # Cartesia (default) and ElevenLabs (opt-in) are both supported;
    # user picks via Settings dialog dropdown which writes to keyring
    # under "TTS_PROVIDER" + the provider's key under e.g. "ELEVENLABS_API_KEY".
    tts_provider, tts_api_key = _resolve_tts_credentials()
    # Local TTS (Kokoro) is keyless, so skip the credential guard for it.
    if tts_provider != "kokoro" and not tts_api_key:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Clixy needs an API key for {tts_provider.title()} TTS.\n\n"
            "Right-click the tray icon → Settings... to set it.",
            f"{tts_provider.title()} key missing",
            0x40,
        )
        sys.exit(1)
    from tts import create_tts_client
    try:
        tts_instance = create_tts_client(provider=tts_provider, api_key=tts_api_key)
    except ValueError as exc:
        # Stale provider_id in keyring (e.g. user downgraded after a future
        # version added a new provider that no longer exists). Show a
        # friendly MessageBox instead of dumping a traceback into the
        # bundled-EXE void.
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Clixy's TTS configuration is invalid: {exc}\n\n"
            "Right-click the tray icon → Settings... to choose a "
            "supported provider.",
            "TTS provider not supported",
            0x40,
        )
        sys.exit(1)

    # Open the voice's audio stream in the background, and for Kokoro load the
    # ~330MB model, so the first spoken reply waits for neither.
    import threading as _warmup_threading
    _warmup_threading.Thread(
        target=tts_instance.warmup, daemon=True, name="tts-warmup"
    ).start()

    clicky = ClickyApp(
        # Route LLM_PROVIDER to the right model/client. When the user
        # selected "Ollama (local)" in Settings, _llm_model_id is
        # 'ollama/<vision-model>' and _llm_api_key is empty, so create_ai_client
        # dispatches to OllamaClient and the Anthropic key is ignored.
        ai_client=create_ai_client(
            model_id=_llm_model_id,
            api_key=_llm_api_key,
            ollama_host=OLLAMA_HOST,
        ),
        stt_client=create_stt_client(_stt_provider, _stt_key),
        tts_client=tts_instance,
    )

    # In realtime mode GPT-Realtime owns the 24kHz mic, so we must NOT also
    # open the 16kHz AssemblyAI STT mic (both grabbing the input device = no
    # audio). Skip STT entirely when the realtime session is active.
    if _should_connect_stt(clicky._realtime):
        _log("Pre-opening mic + WebSocket (one-time startup cost)...")
        _warm_chime()
        try:
            clicky._stt.connect()
            clicky._stt.on_partial_transcript(
                lambda text: print(f"[stt partial] {text}", flush=True)
            )
        except Exception as exc:
            # A provider that fails to load at startup (a local model
            # with a missing bundled dep, or a stale keyring setting) must NOT
            # brick the app with a traceback. If we exit here the user can't
            # reach Settings to fix it. Show a friendly message and keep going
            # so the tray + Settings open and they can switch providers.
            import traceback
            print(f"\nERROR: STT failed to start: {exc}")
            traceback.print_exc()
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Clixy's speech-to-text provider failed to start:\n\n"
                    f"{exc}\n\n"
                    "Right-click the Clixy tray icon and open Settings to switch "
                    "to a different provider (for example AssemblyAI cloud), then "
                    "restart Clixy.",
                    "Speech-to-text failed to load",
                    0x10,  # MB_ICONERROR
                )
            except Exception:
                pass
            # Do NOT sys.exit: let the app open so the user can recover via Settings.
    else:
        _log("REALTIME: skipping AssemblyAI STT mic (GPT-Realtime owns the mic)")

    clicky.start()

    # System tray icon: the ONLY clean exit path, since the overlay
    # has WS_EX_TOOLWINDOW (no taskbar entry) and there's no console
    # for Ctrl+C. Right-click tray → Quit triggers a clean shutdown.
    from tray import ClickyTray

    def _quit_via_tray() -> None:
        _log("Quit requested via tray menu, shutting down...")
        clicky.stop()
        qt_app.quit()

    def _show_settings() -> None:
        dlg = SettingsDialog()
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        _settings_saved(dlg.needs_restart())

    def _settings_saved(restart: bool) -> None:
        """After Save, in the Settings window or the panel's own Settings."""
        _log("Settings saved.")
        clicky.apply_live_settings()
        if not restart:
            return
        # Providers and models are built once at startup, so a new AI or voice
        # provider needs a fresh start. No auto-relaunch: it pops a stray
        # terminal and sometimes fails.
        ctypes.windll.user32.MessageBoxW(
            None,
            "Saved. Your new AI or voice provider starts next time.\n\n"
            "Clixy will close now. Reopen it from the Start menu to continue.",
            "Clixy",
            0x40,  # MB_ICONINFORMATION (OK only)
        )
        clicky.stop()
        qt_app.quit()

    # Tray construction can raise RuntimeError if the user's Windows
    # has no system tray available (rare: kiosk mode, custom shells,
    # certain VMs). Show a QMessageBox + exit cleanly rather than
    # leaving an invisible app running with no quit path.
    try:
        tray = ClickyTray(
            on_quit=_quit_via_tray,
            on_settings=_show_settings,
        )
    except RuntimeError as exc:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Clixy -- Tray Error", str(exc)
        )
        clicky.stop()
        sys.exit(1)
    clicky.sig_notify.connect(tray.notify)

    # The interface sounds: the audio device opens and the sounds load off the main thread.
    try:
        import sfx
        sfx.warm()
    except Exception:
        pass

    # The ready-made 3D models are built once, quietly in the background, so they show at once.
    _prewarm = threading.Timer(25.0, model3d.prewarm)
    _prewarm.daemon = True
    _prewarm.start()

    # Click the notch: the Clixy panel (panel.py) for typed questions and Settings.
    _island = getattr(clicky._overlay, "island", None)
    if _island is not None:
        _island.on_ask = clicky.ask_text
        _island.on_settings = _show_settings          # if the panel's own Settings can't open
        _island.on_settings_saved = _settings_saved
        _island.on_open_note = clicky.show_saved_note    # Open note: the real note card, not a picture

    # Dev mode (tools/dev/Start-Clixy-Dev.bat): restart by itself when the code changes.
    import devmode
    if devmode.enabled():
        from pathlib import Path

        def _reload() -> None:
            _log("Dev mode: the code changed, restarting...")
            clicky.stop()
            devmode.restart(_mutex_handle)
            qt_app.quit()

        _dev_watcher = devmode.Watcher(Path(__file__).resolve().parent, on_change=_reload, busy=clicky.busy,
                                       on_error=clicky.sig_island_note.emit)
        clicky.sig_island_note.emit("Dev mode is on: I restart by myself whenever the code changes.")

    def _shutdown(*_args):
        _log("Shutting down...")
        clicky.stop()
        qt_app.quit()

    signal.signal(signal.SIGINT, _shutdown)

    _log(f"Model: {_llm_model_id}")
    _log(f"Listening for {describe_hotkey(_hotkey_combo())}... (Ctrl+C to quit)")

    sys.exit(qt_app.exec())
