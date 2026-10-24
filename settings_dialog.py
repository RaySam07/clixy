"""Clicky's Settings window.

An ink rail on the left (the mark, the pages, and your buddy at the
bottom) and one page at a time on the right:

- General: your AI connection as a card (model, status, key ending, cost);
  Replace opens the paste zone. The push-to-talk shortcut as keycaps you
  re-record by pressing them, voice on this PC or in the cloud, and
  drawing on screen.
- Buddies: "Say hi to your buddies", the five pixel buddies, alive. Picking
  one previews it everywhere right away; Cancel puts the old one back.
- Look: the screen-edge glow and which side the island lives on.
- Privacy: where each thing goes, keep or forget history.
- Advanced: every provider with its own key, for the few who need them.

Save persists everything to Windows Credential Manager via keyring. The
shortcut, glow, island side, history and buddy apply at once; a new AI or
voice provider needs a restart, which ``needs_restart()`` reports.

The provider rows are the original dialog's, kept whole: one dropdown and
key field per category (AI model, Listening, Voice), shared keys shown
once, a pasted key moving to the provider it belongs to, and Save disabled
until every provider that needs a key has one.

Threading: Qt main thread only (modal). ``keyring.set_password`` is
synchronous and ~10 ms on Windows DPAPI.
"""
from __future__ import annotations

from dataclasses import dataclass

import keyring

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import theme
import mic
from version import NAME, VERSION
from widgets import (
    BuddyView,
    ConnectionCard,
    KeyCaps,
    LookPreview,
    MicMeter,
    PasteZone,
    Segmented,
    Toggle,
    buddy_grid,
    mark_label,
)

from config import KEYRING_SERVICE, OLLAMA_HOST


# The Ollama model the dropdown suggests: qwen3-vl:4b-instruct, the local model
# that points when asked in its own box format. The combobox is editable, so
# any other model can still be typed in.
_OLLAMA_MODEL_SUGGESTIONS: tuple[str, ...] = ("qwen3-vl:4b-instruct",)


# --- Provider category data model -------------------------------------------
#
# Drives 3-row progressive-disclosure UX in the dialog: pick provider per
# category (LLM/STT/TTS) from a dropdown, only that provider's API key field
# is visible. The dialog stays at one key field per category however many
# providers there are.


@dataclass(frozen=True)
class _Model:
    """One selectable model for a provider. ``model_id`` is the bare model
    string passed to the SDK / stored in the provider's model_setting slot
    (e.g. "claude-opus-4-8", "gpt-5.4", "llava:7b")."""

    display_name: str           # e.g. "Claude Sonnet 4.6 (default)"
    model_id: str               # e.g. "claude-sonnet-4-6"


@dataclass(frozen=True)
class _Provider:
    """Single provider in a category. ``provider_id`` is the lowercase
    string used as the value of LLM_PROVIDER / STT_PROVIDER / TTS_PROVIDER
    config + the dropdown's data slot. ``api_key_env_var`` is BOTH the
    env-var name AND the keyring slot name (they share namespace by
    convention; see config.resolve_api_key).

    Optional per-provider model picker: ``models`` (if non-empty)
    drives a contextual model dropdown shown only when this provider is
    selected; the chosen model_id persists to ``model_setting`` (a keyring
    slot like "ANTHROPIC_MODEL"). ``models_editable`` lets the user type a
    custom model (Ollama). ``hides_other_categories`` collapses the STT+TTS
    rows when selected (GPT-Realtime does speech end-to-end)."""

    provider_id: str            # e.g. "anthropic", "elevenlabs"
    display_name: str           # e.g. "Anthropic", "ElevenLabs"
    api_key_env_var: str        # e.g. "ANTHROPIC_API_KEY"
    signup_url: str
    models: tuple[_Model, ...] = ()
    model_setting: str = ""           # keyring slot for the chosen model
    models_editable: bool = False     # True → user can type a custom model
    hides_other_categories: bool = False  # True → collapse STT+TTS (realtime)
    requires_key: bool = True         # False → key not required (Save not gated)
    hide_key_field: bool = False      # True → no key field at all (pure-local)
    key_hint: str = ""                # custom empty-field placeholder text
    secret: bool = True               # False: the field holds an address, shown in plain text
    signup_label: str = "Get key →"   # text on the button that opens signup_url
    shares_key_with: str = ""         # category whose same-key provider makes this row keyless


@dataclass(frozen=True)
class _ProviderCategory:
    """A row group in the dialog. ``category_key`` is the prefix of
    the provider-selection config (e.g. "LLM" → LLM_PROVIDER setting)."""

    category_key: str           # "LLM", "STT", "TTS"
    label: str                  # "LLM (vision)", etc.
    providers: tuple[_Provider, ...]
    default_index: int


# A pasted key names its own AI provider; "sk-" alone is OpenAI, checked last.
# sk-or- (OpenRouter) works with any provider: it keeps a provider the user
# chose, and otherwise picks Anthropic, the fastest through OpenRouter.
_KEY_PREFIX_PROVIDERS = (("sk-ant-", "anthropic"), ("AIza", "gemini"), ("sk-or-", None), ("sk-", "openai"))


def _default_provider_id(category: "_ProviderCategory") -> str:
    """Provider shown before the user has chosen one. For the AI model it is
    the provider there is already a key for, else the first in the list."""
    if category.category_key == "LLM":
        from config import default_llm_provider
        return default_llm_provider()
    return category.providers[category.default_index].provider_id


_PROVIDER_CATEGORIES: tuple[_ProviderCategory, ...] = (
    _ProviderCategory(
        category_key="LLM",
        label="AI model",
        providers=(
            # First, so it is the default: GPT-6.1 Sol was the fastest to its
            # first word in testing and points as accurately as the others.
            # No model sub-pickers on the cloud providers: one default each,
            # with an env var (OPENAI_MODEL_VISION, ANTHROPIC_MODEL) to override.
            _Provider(
                provider_id="openai",
                display_name="OpenAI",
                api_key_env_var="OPENAI_API_KEY",
                signup_url="https://platform.openai.com/api-keys",
                key_hint="OpenAI key (sk-...), or paste an Anthropic, Google or OpenRouter key",
            ),
            _Provider(
                provider_id="anthropic",
                display_name="Anthropic",
                api_key_env_var="ANTHROPIC_API_KEY",
                signup_url="https://console.anthropic.com/settings/keys",
                key_hint="Anthropic key (sk-ant-) or an OpenRouter key (sk-or-)",
            ),
            # GPT-Realtime is intentionally NOT in this dropdown. It's
            # an experimental speech-to-speech path with known audio issues
            # (no transcription / no playback on some setups). Still reachable
            # for advanced use via LLM_PROVIDER=openai-realtime in .env, just not
            # surfaced as a working option.
            # Local Ollama. No API key: the "API key" field stores the
            # OLLAMA_HOST URL (default http://localhost:11434) instead.
            # Repurposing the field as a host URL keeps the dialog uniform
            # (single field per provider) without adding a separate "host"
            # input row. Pixel-pointing for local vision models is handled
            # by locator.py's two-stage grid pattern (see ai.OllamaClient).
            # requires_key=False and the field is prefilled with the localhost
            # default, so the one "free, no signup" path needs zero input and
            # Save is never gated on a box that looks like an API-key box.
            _Provider(
                provider_id="ollama",
                display_name="Ollama (local)",
                api_key_env_var="OLLAMA_HOST",
                signup_url="https://ollama.com/download",
                requires_key=False,
                secret=False,
                signup_label="Get Ollama →",
                key_hint="Ollama server address (leave the default unless you moved it)",
                models=tuple(_Model(m, m) for m in _OLLAMA_MODEL_SUGGESTIONS),
                model_setting="OLLAMA_MODEL_VISION",
                models_editable=True,
            ),
            # Google Gemini. Takes a Google AI Studio key or an OpenRouter key;
            # a saved OpenRouter key from another slot is filled in for you.
            _Provider(
                provider_id="gemini",
                display_name="Google Gemini",
                api_key_env_var="GEMINI_API_KEY",
                signup_url="https://aistudio.google.com/apikey",
                key_hint="Google AI Studio key, or an OpenRouter key (sk-or-)",
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="STT",
        label="Listening (speech to text)",
        providers=(
            # Same key as the Cartesia voice, so a new install needs two keys.
            _Provider(
                provider_id="cartesia",
                display_name="Cartesia",
                api_key_env_var="CARTESIA_API_KEY",
                signup_url="https://play.cartesia.ai/sign-in",
                shares_key_with="TTS",
            ),
            _Provider(
                provider_id="assemblyai",
                display_name="AssemblyAI",
                api_key_env_var="ASSEMBLYAI_API_KEY",
                signup_url="https://www.assemblyai.com/dashboard/signup",
            ),
            # Local offline STT (faster-whisper). No API key; model weights
            # download on first use. requires_key=False so Save isn't gated on
            # a credential and the startup modal never forces one.
            _Provider(
                provider_id="faster-whisper",
                display_name="Local (faster-whisper)",
                api_key_env_var="FASTER_WHISPER_LOCAL",
                signup_url="https://github.com/SYSTRAN/faster-whisper",
                requires_key=False,
                hide_key_field=True,  # truly local, no key field at all
            ),
        ),
        default_index=2,  # local: your voice stays on this PC
    ),
    _ProviderCategory(
        category_key="TTS",
        label="Voice (text to speech)",
        providers=(
            _Provider(
                provider_id="cartesia",
                display_name="Cartesia",
                api_key_env_var="CARTESIA_API_KEY",
                signup_url="https://play.cartesia.ai/sign-in",
            ),
            _Provider(
                provider_id="elevenlabs",
                display_name="ElevenLabs",
                api_key_env_var="ELEVENLABS_API_KEY",
                signup_url="https://elevenlabs.io/app/sign-up",
            ),
            # Local offline TTS (Kokoro-82M). No API key; model files download
            # on first use. requires_key=False (see faster-whisper note).
            _Provider(
                provider_id="kokoro",
                display_name="Local (Kokoro)",
                api_key_env_var="KOKORO_LOCAL",
                signup_url="https://github.com/thewh1teagle/kokoro-onnx",
                requires_key=False,
                hide_key_field=True,  # truly local, no key field at all
            ),
        ),
        default_index=2,  # local: speaks offline, no key
    ),
)


def _mask(value: str | None) -> str:
    """Return a privacy-preserving preview like 'sk-...****abc4' for an
    existing key. Empty input → empty string."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:5]}{'*' * 6}{value[-4:]}"


_PAGES: tuple[tuple[str, str], ...] = (
    ("general", "General"),
    ("buddies", "Buddies"),
    ("look", "Look"),
    ("privacy", "Privacy"),
    ("advanced", "Advanced"),
)

_LOCAL_VOICE = ("faster-whisper", "kokoro")
_CLOUD_VOICE = ("cartesia", "cartesia")


def _rows(*rows: QWidget) -> QFrame:
    """Rows in one white card, with hairlines between them."""
    frame = QFrame()
    frame.setObjectName("rows")
    v = QVBoxLayout(frame)
    v.setContentsMargins(0, 2, 0, 2)
    v.setSpacing(0)
    for i, row in enumerate(rows):
        if i:
            line = QFrame()
            line.setObjectName("rowSep")
            v.addWidget(line)
        v.addWidget(row)
    return frame


def _row(title: str, subtitle: str, *controls: QWidget) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(22, 15, 18, 15)
    h.setSpacing(14)
    text = QVBoxLayout()
    text.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("rowTitle")
    text.addWidget(title_label)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("rowSub")
        sub.setWordWrap(True)
        text.addWidget(sub)
        row.sub = sub  # so a row can update its own line
    h.addLayout(text, stretch=1)
    for control in controls:
        h.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)
    return row


def _heading(text: str, lead: str = "") -> QWidget:
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 6)
    v.setSpacing(6)
    h1 = QLabel(text)
    h1.setObjectName("h1")
    v.addWidget(h1)
    if lead:
        label = QLabel(lead)
        label.setObjectName("lead")
        label.setWordWrap(True)
        v.addWidget(label)
    return box


class SettingsDialog(QDialog):
    """Clicky's Settings. ``exec()`` returns Accepted on Save, Rejected on
    Cancel. Saved values land in Windows Credential Manager under
    ``KEYRING_SERVICE`` ("clicky-windows"), one entry per setting."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clicky Settings")
        self.setModal(True)
        self.setWindowIcon(theme.app_icon())
        self.resize(900, 660)
        self.setMinimumSize(820, 600)

        self._dropdowns: dict[str, QComboBox] = {}
        self._key_inputs: dict[str, QLineEdit] = {}
        self._signup_buttons: dict[str, QPushButton] = {}
        self._share_notes: dict[str, QLabel] = {}
        # Keys typed in this dialog, by keyring slot. Two rows can show the same
        # slot (Cartesia for listening and voice), so a row loading a slot shows
        # what was typed for it, not the older saved value.
        self._typed_keys: dict[str, str] = {}
        self._loading_key = False
        # True once an AI provider was chosen (saved earlier, or picked in the
        # dropdown now). Until then a pasted OpenRouter key picks the provider.
        self._llm_chosen = False
        # Per-provider model picker (in practice only Ollama has one). The row
        # shows only when the selected provider has models.
        self._model_combos: dict[str, QComboBox] = {}
        self._model_rows: dict[str, QWidget] = {}
        # Per-category containers, so the realtime provider can collapse the
        # STT + TTS rows (it does speech end-to-end).
        self._category_widgets: dict[str, QWidget] = {}
        self._realtime_note: QLabel | None = None
        self._draw_checkbox: QCheckBox | None = None
        # Buddy picked in this dialog: previewed live, put back on Cancel.
        self._avatar_at_open = theme.current_avatar_id()
        self._avatar_cards: dict = {}
        self._needs_restart = False
        self._nav: dict[str, QPushButton] = {}
        self._pages: dict[str, QWidget] = {}
        self._build_ui()

    # ---------- Layout ------------------------------------------------------

    def _build_ui(self) -> None:
        from config import resolve_setting

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_rail())

        pane = QWidget()
        pane.setObjectName("pane")
        pv = QVBoxLayout(pane)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        self._stack = QStackedWidget()
        pv.addWidget(self._stack, stretch=1)
        root.addWidget(pane, stretch=1)

        self._hotkey = resolve_setting("HOTKEY", default="ctrl+win")
        self._reveal = Toggle()
        self._reveal.toggled.connect(self._on_reveal_toggled)
        # Advanced first: the other pages read its dropdowns and key fields.
        pages = {"advanced": self._build_advanced()}
        pages["general"] = self._build_general()
        pages["buddies"] = self._build_buddies()
        pages["look"] = self._build_look()
        pages["privacy"] = self._build_privacy()
        for key, _label in _PAGES:
            scroll = QScrollArea()
            scroll.setObjectName("pageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page = pages[key]
            page.setObjectName("page")
            scroll.setWidget(page)
            self._stack.addWidget(scroll)
            self._pages[key] = scroll

        # Bottom bar: why Save is off (when it is), Cancel, Save.
        bar = QHBoxLayout()
        bar.setContentsMargins(48, 14, 36, 22)
        bar.setSpacing(12)
        self._save_hint = QLabel()
        self._save_hint.setObjectName("bad")
        bar.addWidget(self._save_hint, stretch=1)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        save = self._buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setObjectName("primary")
        for button in self._buttons.buttons():
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumWidth(104)
        bar.addWidget(self._buttons)
        pv.addLayout(bar)

        self._apply_realtime_collapse()
        self._update_save_enabled()
        self._refresh_connection()
        self.show_page("general")

    def _build_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(212)
        v = QVBoxLayout(rail)
        v.setContentsMargins(20, 28, 16, 20)
        v.setSpacing(4)
        logo = QHBoxLayout()
        logo.setContentsMargins(8, 0, 0, 0)
        logo.setSpacing(10)
        logo.addWidget(mark_label(26, theme.BUTTER))
        wordmark = QLabel("clicky")
        wordmark.setObjectName("railWordmark")
        logo.addWidget(wordmark)
        logo.addStretch(1)
        v.addLayout(logo)
        v.addSpacing(28)
        group = QButtonGroup(rail)
        group.setExclusive(True)
        for key, label in _PAGES:
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            group.addButton(button)
            v.addWidget(button)
            self._nav[key] = button
        v.addStretch(1)
        # Your buddy keeps you company at the bottom of the rail.
        self._rail_buddy = BuddyView(84, scale=2.0)
        v.addWidget(self._rail_buddy, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._rail_name = QLabel()
        self._rail_name.setObjectName("railBuddy")
        self._rail_name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self._rail_name)
        version = QLabel(f"{NAME} {VERSION}")
        version.setObjectName("railVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(version)
        self._refresh_rail_buddy()
        return rail

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(48, 40, 36, 12)
        v.setSpacing(16)
        return page, v

    def show_page(self, key: str) -> None:
        """Switch to a page: general, buddies, look, privacy or advanced."""
        self._stack.setCurrentWidget(self._pages[key])
        self._nav[key].setChecked(True)

    # ---------- General -----------------------------------------------------

    def _build_general(self) -> QWidget:
        from config import resolve_setting

        page, v = self._page()
        v.addWidget(_heading("General"))

        self._replacing = False
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(650)
        self._settle.timeout.connect(self._after_paste)
        self._conn_card = ConnectionCard()
        self._conn_card.replaceClicked.connect(lambda: self._show_paste_zone(True))
        v.addWidget(self._conn_card)
        self._paste_box = QWidget()
        pb = QVBoxLayout(self._paste_box)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(8)
        self._paste = PasteZone()
        self._paste.keyChanged.connect(self._on_paste_key)
        pb.addWidget(self._paste)
        links = QHBoxLayout()
        get_key = QPushButton("Get a key from Anthropic")
        get_key.setObjectName("quiet")
        get_key.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://console.anthropic.com/settings/keys"))
        )
        links.addWidget(get_key)
        links.addStretch(1)
        self._keep_key = QPushButton("Keep my current key")
        self._keep_key.setObjectName("quiet")
        self._keep_key.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_key.clicked.connect(lambda: self._show_paste_zone(False))
        links.addWidget(self._keep_key)
        pb.addLayout(links)
        v.addWidget(self._paste_box)

        # Shortcut: keycaps, re-recorded by pressing the new keys.
        self._caps = KeyCaps(self._hotkey)
        self._caps.recorded.connect(self._on_hotkey_recorded)
        change = QPushButton("Change")
        change.setObjectName("quiet")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(self._caps.record)

        self._voice = Segmented([("local", "On this PC"), ("cloud", "Cloud · faster")])
        self._voice.changed.connect(self._on_voice_changed)
        self._sync_voice_switch()

        self._draw_checkbox = Toggle()
        self._draw_checkbox.setChecked(resolve_setting("ANNOTATION_MODE", "on") == "on")

        # Microphone: which one, a live meter, and what's wrong if it's silent.
        self._mic_box = QComboBox()
        self._mic_box.setMinimumWidth(190)
        self._mic_box.setMaximumWidth(240)
        self._mic_box.addItem("Windows default", "")
        for name in mic.input_devices():
            self._mic_box.addItem(name, name)
        chosen = resolve_setting("MIC_DEVICE", default="")
        if chosen and self._mic_box.findData(chosen) < 0:
            self._mic_box.addItem(chosen, chosen)
        self._mic_box.setCurrentIndex(max(0, self._mic_box.findData(chosen)))
        self._mic_box.currentIndexChanged.connect(self._on_mic_changed)
        self._mic_meter = MicMeter()
        self._mic_row = _row("Microphone", "Say something to test it.", self._mic_box, self._mic_meter)
        self._mic_fix = QPushButton("Open Windows microphone settings")
        self._mic_fix.setObjectName("quiet")
        self._mic_fix.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_fix.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(mic.PRIVACY_SETTINGS_URI)))
        self._mic_fix.setVisible(False)
        self._test_mic = mic.Microphone(lambda *_a: None, device_name=chosen)
        self._test_mic.start()
        self._heard_at = 0.0
        self._mic_timer = QTimer(self)
        self._mic_timer.timeout.connect(self._update_mic)
        self._mic_timer.start(60)

        v.addWidget(_rows(
            _row("Shortcut", "Hold to talk, let go to ask.", self._caps, change),
            _row("Voice", "Hearing you and talking back.", self._voice),
            self._mic_row,
            _row("Draw on screen", "Your buddy draws to show you. Off, it still draws when you ask it to.",
                 self._draw_checkbox),
        ))
        hints = QHBoxLayout()
        self._voice_hint = QLabel("Cloud voice uses one Cartesia key for both. Add it in Advanced.")
        self._voice_hint.setObjectName("rowSub")
        self._voice_hint.setVisible(False)
        hints.addWidget(self._voice_hint)
        hints.addWidget(self._mic_fix)
        hints.addStretch(1)
        v.addLayout(hints)
        v.addStretch(1)
        return page

    def _on_mic_changed(self, _index: int) -> None:
        self._test_mic.set_device(self._mic_box.currentData() or "")
        self._heard_at = 0.0

    def _update_mic(self) -> None:
        """Move the meter and say plainly whether the microphone hears you."""
        import time as _time
        test = self._test_mic
        level = test.level
        test.level *= 0.85  # decays between chunks so the meter falls back
        self._mic_meter.set_level(level)
        now = _time.monotonic()
        if level > 0.05:
            self._heard_at = now
        test.check_silence()
        blocked = test.problem == mic.BLOCKED
        if test.problem == mic.MISSING:
            text = "No microphone found. Plug one in."
        elif test.problem == mic.FAILED:
            text = "This microphone won't open. Pick another one."
        elif blocked:
            text = "Silent: it's muted, or Windows is blocking it."
        elif now - self._heard_at < 2.0:
            text = f"Hearing you on {test.device_label or 'your microphone'}."
        else:
            text = "Say something to test it."
        if self._mic_row.sub.text() != text:
            self._mic_row.sub.setText(text)
        self._mic_fix.setVisible(blocked)

    def done(self, result: int) -> None:
        """Closing the window releases the test microphone."""
        self._mic_timer.stop()
        self._test_mic.close()
        super().done(result)

    def _connection(self) -> tuple[str, str, str, bool]:
        """(model as rich text, key, cost, local) for the connection card."""
        from config import resolve_model
        from island import model_label

        provider = self._selected_provider("LLM")
        key = self._key_inputs["LLM"].text().strip()
        semi = theme.family(QFont.Weight.DemiBold)

        def html(brand: str, model: str) -> str:
            return f'{brand} <span style="font-family:\'{semi}\'">{model}</span>'

        via = " · via OpenRouter" if key.startswith("sk-or-") else ""
        if provider.provider_id == "ollama":
            model = self._selected_model_id(self._category("LLM"), provider) or "a local model"
            return html("Ollama", model), "", "free · on this PC", True
        if provider.provider_id == "anthropic":
            model = resolve_model("ANTHROPIC_MODEL", "claude-haiku-5-5")
            cost = "≈ $0.0003 a question" if "haiku" in model else ""
            return html("Claude", model_label(model)), key, cost + via, False
        if provider.provider_id == "gemini":
            model = resolve_model("GEMINI_MODEL_VISION", "google/gemini-4-argon")
            return html("Gemini", model_label(model).removeprefix("gemini-")), key, via.lstrip(" ·"), False
        model = resolve_model("OPENAI_MODEL_VISION", "gpt-6.1-sol")
        return html("OpenAI", model_label(model)), key, via.lstrip(" ·"), False

    def _refresh_connection(self) -> None:
        if not hasattr(self, "_conn_card"):
            return
        model, key, cost, local = self._connection()
        self._conn_card.show_connection(model, key, cost, local=local)
        if not key and not local:
            self._show_paste_zone(True, can_keep=False)  # no key yet: paste is the whole story
        elif not self._replacing:
            self._show_paste_zone(False)

    def _show_paste_zone(self, show: bool, *, can_keep: bool = True) -> None:
        self._replacing = show and can_keep
        self._paste_box.setVisible(show)
        self._conn_card.setVisible(not show)
        self._keep_key.setVisible(can_keep)
        if show:
            self._paste.field.setFocus()

    def _on_paste_key(self, text: str) -> None:
        if self._paste.kind is None:
            return  # half a key, or not a Claude key: the zone says so
        self._replacing = True  # hold the zone while its check mark lands
        self._key_inputs["LLM"].setText(text)
        self._settle.start()

    def _after_paste(self) -> None:
        self._replacing = False
        self._refresh_connection()

    def _on_hotkey_recorded(self, combo: str) -> None:
        self._hotkey = combo

    def _sync_voice_switch(self) -> None:
        pair = (self._dropdowns["STT"].currentData(), self._dropdowns["TTS"].currentData())
        self._voice.set_value("local" if pair == _LOCAL_VOICE else "cloud" if pair == _CLOUD_VOICE else "")

    def _on_voice_changed(self, value: str) -> None:
        stt, tts = _LOCAL_VOICE if value == "local" else _CLOUD_VOICE
        for key, provider_id in (("STT", stt), ("TTS", tts)):
            dropdown = self._dropdowns[key]
            dropdown.setCurrentIndex(max(0, dropdown.findData(provider_id)))
        self._update_save_enabled()

    # ---------- Buddies -----------------------------------------------------

    def _build_buddies(self) -> QWidget:
        page, v = self._page()
        v.addWidget(_heading(
            "Say hi to your buddies",
            "Pick who points for you. Each one brings its own colors to the screen glow.",
        ))
        grid, self._avatar_cards = buddy_grid(self._on_avatar_picked, columns=3)
        v.addWidget(grid)
        v.addStretch(1)
        return page

    def _on_avatar_picked(self, avatar_id: str) -> None:
        """Preview the buddy everywhere right away; Save keeps it."""
        theme.set_avatar(avatar_id, persist=False)
        self._refresh_rail_buddy()

    def _refresh_rail_buddy(self) -> None:
        av = theme.current_avatar()
        self._rail_name.setText(f"{av.name} is with you")

    # ---------- Look ----------------------------------------------------------

    def _build_look(self) -> QWidget:
        from config import resolve_setting

        page, v = self._page()
        v.addWidget(_heading("Look"))
        self._look_preview = LookPreview()
        v.addWidget(self._look_preview)
        self._glow = Toggle()
        self._glow.toggled.connect(self._look_preview.set_glow)
        self._glow.setChecked(resolve_setting("EDGE_GLOW", default="on").lower() == "on")
        self._look_preview.set_glow(self._glow.isChecked())
        self._side = Segmented([("left", "Left"), ("right", "Right")])
        self._side.changed.connect(self._look_preview.set_side)
        side = "left" if resolve_setting("ISLAND_SIDE", default="right").lower() == "left" else "right"
        self._side.set_value(side)
        self._look_preview.set_side(side)
        self._look_preview._side = self._look_preview._side_target  # start there, no slide
        v.addWidget(_rows(
            _row("Edge glow", "Your buddy's colors flow around the screen while you talk.", self._glow),
            _row("Island side", "Which edge of the screen the notch lives on.", self._side),
        ))
        v.addStretch(1)
        return page

    # ---------- Privacy ---------------------------------------------------------

    def _build_privacy(self) -> QWidget:
        from config import resolve_setting

        page, v = self._page()
        v.addWidget(_heading("Privacy"))
        # Say where each thing goes. Not phrased as a denial ("nothing leaves
        # your PC"): that would be false, the screenshot goes to the AI.
        privacy = QLabel(
            "Keys stay on this PC, encrypted by Windows Credential Manager. "
            "Your screenshot, your question and Clicky's notes for that app go to the AI you pick. "
            "With voice on this PC, what you say stays here too."
        )
        privacy.setObjectName("lead")
        privacy.setWordWrap(True)
        v.addWidget(privacy)
        # Where each thing goes, at a glance: butter stays here, grey leaves.
        self._where: dict[str, QLabel] = {}
        where_rows = []
        for key, title in (("key", "Your keys"), ("voice", "What you say"), ("ai", "Screenshot + question")):
            chip = QLabel()
            chip.setFont(theme.mono_font(8.5))
            self._where[key] = chip
            where_rows.append(_row(title, "", chip))
        v.addWidget(_rows(*where_rows))
        self._keep_history = Toggle()
        self._keep_history.setChecked(resolve_setting("KEEP_HISTORY", default="on").lower() == "on")
        forget = QPushButton("Forget…")
        forget.setObjectName("danger")
        forget.setCursor(Qt.CursorShape.PointingHandCursor)
        forget.clicked.connect(self._on_forget)
        v.addWidget(_rows(
            _row("Keep history", "Short notes about what you asked in each app, so answers get better.",
                 self._keep_history),
            _row("Forget history", "Deletes those notes and any logs. Keeps your key and settings.", forget),
        ))
        v.addStretch(1)
        return page

    def _refresh_where(self) -> None:
        if not hasattr(self, "_where"):
            return
        stt = self._selected_provider("STT")
        llm = self._selected_provider("LLM")
        if self._collapsed_categories():
            stt = llm  # realtime: the AI hears you itself
        ai_names = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Google Gemini"}
        places = {
            "key": ("stays on this PC", True),
            "voice": ("stays on this PC", True) if not stt.requires_key else (f"goes to {ai_names.get(stt.provider_id, stt.display_name)}", False),
            "ai": ("stays on this PC", True) if llm.provider_id == "ollama" else (f"goes to {ai_names.get(llm.provider_id, llm.display_name)}", False),
        }
        for key, (text, here) in places.items():
            chip = self._where[key]
            chip.setText(text)
            name = "whereHere" if here else "whereAway"
            if chip.objectName() != name:
                chip.setObjectName(name)
                chip.style().unpolish(chip)
                chip.style().polish(chip)

    def _on_forget(self) -> None:
        from tray import forget_dialog, forget_history
        if forget_dialog().exec() != QMessageBox.StandardButton.Yes:
            return
        failed = forget_history()
        QMessageBox.information(
            self, "Clicky",
            "Some files were in use and stayed. Quit Clicky and try again." if failed else "Done. Your history is gone.",
        )

    # ---------- Advanced ----------------------------------------------------------

    def _build_advanced(self) -> QWidget:
        page, v = self._page()
        v.addWidget(_heading("Advanced", "Other AI and voice providers, each with its own key. Most people never need these."))
        for category in _PROVIDER_CATEGORIES:
            widget = self._build_category_row(category)
            self._category_widgets[category.category_key] = widget
            v.addWidget(widget)
        v.addWidget(_rows(_row("Show keys", "In plain text, to check a pasted key.", self._reveal)))
        v.addStretch(1)
        return page

    def _category(self, key: str) -> _ProviderCategory:
        return next(c for c in _PROVIDER_CATEGORIES if c.category_key == key)

    def _build_category_row(self, category: _ProviderCategory) -> QWidget:
        """One provider card: title, dropdown + Get key, key field, notes."""
        from config import resolve_setting

        container = QFrame()
        container.setObjectName("rows")
        v = QVBoxLayout(container)
        v.setContentsMargins(22, 16, 18, 18)
        v.setSpacing(10)

        label = QLabel(category.label)
        label.setObjectName("rowTitle")
        v.addWidget(label)

        selected_provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=_default_provider_id(category),
        )
        try:
            selected_index = next(
                i for i, p in enumerate(category.providers)
                if p.provider_id == selected_provider_id
            )
        except StopIteration:
            selected_index = category.default_index

        h = QHBoxLayout()
        h.setSpacing(10)
        dropdown = QComboBox()
        for provider in category.providers:
            dropdown.addItem(provider.display_name, provider.provider_id)
        dropdown.setCurrentIndex(selected_index)
        dropdown.currentIndexChanged.connect(
            lambda idx, c=category: self._on_provider_changed(c, idx)
        )
        self._dropdowns[category.category_key] = dropdown
        if category.category_key == "LLM":
            self._llm_chosen = bool(resolve_setting("LLM_PROVIDER", default=""))
            dropdown.activated.connect(lambda _index: setattr(self, "_llm_chosen", True))
        h.addWidget(dropdown, stretch=1)

        signup_button = QPushButton("Get key →")
        signup_button.setCursor(Qt.CursorShape.PointingHandCursor)
        signup_button.clicked.connect(
            lambda _checked=False, c=category: self._on_signup_clicked(c)
        )
        self._signup_buttons[category.category_key] = signup_button
        h.addWidget(signup_button)
        v.addLayout(h)

        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.textChanged.connect(self._update_save_enabled)
        key_input.textChanged.connect(
            lambda text, c=category: self._remember_typed_key(c, text)
        )
        self._key_inputs[category.category_key] = key_input
        v.addWidget(key_input)

        share_note = QLabel()
        share_note.setObjectName("rowSub")
        share_note.setVisible(False)
        self._share_notes[category.category_key] = share_note
        v.addWidget(share_note)

        self._refresh_key_field_for_category(category)

        if any(p.models for p in category.providers):
            model_row = self._build_model_row(category)
            v.addWidget(model_row)
            self._model_rows[category.category_key] = model_row
            current_provider = category.providers[selected_index]
            self._populate_model_combo(category, current_provider)
            model_row.setVisible(bool(current_provider.models))

        if category.category_key == "LLM":
            note = QLabel(
                "Realtime handles speech end-to-end (lowest latency), "
                "so listening and voice aren't used."
            )
            note.setWordWrap(True)
            note.setObjectName("rowSub")
            v.addWidget(note)
            self._realtime_note = note
            current_provider = category.providers[selected_index]
            note.setVisible(current_provider.hides_other_categories)

        return container

    # ---------- Provider rows: logic ------------------------------------------

    def _build_model_row(self, category: _ProviderCategory) -> QWidget:
        """Build the per-provider 'Model:' combobox row. The combo is
        (re)populated for the selected provider by _populate_model_combo.
        Stored in _model_combos."""
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 4, 0, 0)
        model_label = QLabel("Model")
        model_label.setObjectName("rowSub")
        h.addWidget(model_label)
        combo = QComboBox()
        h.addWidget(combo, stretch=1)
        self._model_combos[category.category_key] = combo
        return container

    def _populate_model_combo(
        self, category: _ProviderCategory, provider: _Provider
    ) -> None:
        """Fill the category's model combo with the provider's models and
        select the stored choice (resolve_setting on provider.model_setting,
        default = the provider's first model). Editable for Ollama (custom)."""
        from config import resolve_setting

        combo = self._model_combos.get(category.category_key)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.setEditable(provider.models_editable)
        for m in provider.models:
            combo.addItem(m.display_name, m.model_id)
        if provider.models and provider.model_setting:
            default_id = provider.models[0].model_id
            stored = resolve_setting(provider.model_setting, default_id)
            idx = combo.findData(stored)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif provider.models_editable:
                combo.addItem(stored, stored)
                combo.setCurrentText(stored)
            else:
                combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _selected_model_id(self, category: _ProviderCategory, provider: _Provider) -> str:
        """The chosen model id for a provider. Editable combos (Ollama) use the
        typed text; fixed combos use the selected item's data (the bare id)."""
        combo = self._model_combos.get(category.category_key)
        if combo is None:
            return ""
        if provider.models_editable:
            return combo.currentText().strip()
        data = combo.currentData()
        return data if data else combo.currentText().strip()

    def _selected_requires_key(self, category_key: str) -> bool:
        """True if the category's currently-selected provider needs an API key.
        Local providers (faster-whisper / kokoro) return False."""
        dropdown = self._dropdowns.get(category_key)
        category = next(
            (c for c in _PROVIDER_CATEGORIES if c.category_key == category_key), None
        )
        if dropdown is None or category is None:
            return True
        if self._shares_key(category_key):
            return False  # the row it shares with gates Save
        return category.providers[dropdown.currentIndex()].requires_key

    def _collapsed_categories(self) -> set[str]:
        """Categories collapsed because the selected LLM provider does speech
        end-to-end (realtime). Returns {"STT","TTS"} or an empty set."""
        llm_dropdown = self._dropdowns.get("LLM")
        if llm_dropdown is None:
            return set()
        llm_cat = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        provider = llm_cat.providers[llm_dropdown.currentIndex()]
        return {"STT", "TTS"} if provider.hides_other_categories else set()

    def _apply_realtime_collapse(self) -> None:
        """Hide/show the STT+TTS rows + the realtime note based on the current
        LLM provider. Called on construction + on LLM provider change."""
        collapsed = self._collapsed_categories()
        for key in ("STT", "TTS"):
            widget = self._category_widgets.get(key)
            if widget is not None:
                widget.setVisible(key not in collapsed)
        if self._realtime_note is not None:
            self._realtime_note.setVisible(bool(collapsed))

    def _cached_openrouter_key(self) -> str:
        """An sk-or- OpenRouter key already saved for any LLM provider slot.
        One OpenRouter key serves all LLM providers, so reuse it (cache +
        reuse) instead of making the user re-enter it per provider."""
        for slot in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            k = keyring.get_password(KEYRING_SERVICE, slot) or ""
            if k.startswith("sk-or-"):
                return k
        return ""

    def _refresh_key_field_for_category(self, category: _ProviderCategory) -> None:
        """Read the keyring slot for the dropdown's currently-selected
        provider, set the key field's text + placeholder accordingly.
        Called on dialog construction AND on dropdown change."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        existing = self._typed_keys.get(provider.api_key_env_var) or (
            keyring.get_password(KEYRING_SERVICE, provider.api_key_env_var) or ""
        )
        # Cache + reuse: one OpenRouter (sk-or-) key works for every LLM
        # provider. If this provider's own slot is empty but you pasted an
        # OpenRouter key for another LLM provider, reuse it here so you don't
        # re-enter it and Save isn't gated on an empty field. The _API_KEY
        # filter skips Ollama (its field holds OLLAMA_HOST, not a key).
        if (not existing and category.category_key == "LLM"
                and provider.api_key_env_var.endswith("_API_KEY")):
            existing = self._cached_openrouter_key()
        # Ollama's field holds a server address, not a key. Prefill the
        # documented default so the no-signup path needs zero typing.
        if not existing and provider.provider_id == "ollama":
            existing = OLLAMA_HOST
        key_input = self._key_inputs[category.category_key]
        self._loading_key = True
        try:
            key_input.setText(existing)
        finally:
            self._loading_key = False
        if existing:
            placeholder = _mask(existing)
        elif provider.key_hint:
            placeholder = provider.key_hint
        else:
            placeholder = f"Your {provider.display_name} key"
        key_input.setPlaceholderText(placeholder)
        # Pure-local providers (faster-whisper / kokoro) need no key, so hide the
        # key field + Get-key button entirely. Everything else shows the field
        # (Gemini shows an OPTIONAL field: blank reuses the OpenRouter key).
        key_input.setVisible(not provider.hide_key_field)
        self._signup_buttons[category.category_key].setVisible(not provider.hide_key_field)
        self._signup_buttons[category.category_key].setText(provider.signup_label)
        self._apply_key_sharing()
        revealed = self._reveal.isChecked() if hasattr(self, "_reveal") else False
        key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if revealed or not provider.secret
            else QLineEdit.EchoMode.Password
        )

    def _remember_typed_key(self, category: _ProviderCategory, text: str) -> None:
        if self._loading_key or category.category_key not in self._dropdowns:
            return
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        self._typed_keys[provider.api_key_env_var] = text
        if category.category_key != "LLM" or len(text.strip()) < 20:
            return  # only a whole pasted key, not one being typed
        match = next((pid for prefix, pid in _KEY_PREFIX_PROVIDERS if text.strip().startswith(prefix)), None)
        if text.strip().startswith("sk-or-") and not self._llm_chosen:
            match = "anthropic"  # through OpenRouter, Haiku answers first (2.8 s vs 4.6-6.6 s for Sol)
        target = next((p for p in category.providers if p.provider_id == match), None)
        if target is None or target is provider:
            return
        # The key belongs to another provider: move it there and select it.
        self._typed_keys.pop(provider.api_key_env_var, None)
        self._typed_keys[target.api_key_env_var] = text
        dropdown.setCurrentIndex(category.providers.index(target))

    # ---------- Slots ----------------------------------------------------

    def _on_provider_changed(self, category: _ProviderCategory, _index: int) -> None:
        """Dropdown changed: swap the key field to the newly-selected
        provider's stored key, repopulate + show/hide the model row, and (for
        the LLM category) collapse STT+TTS when realtime is selected."""
        self._refresh_key_field_for_category(category)
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]

        # Repopulate + show/hide the per-provider model row.
        model_row = self._model_rows.get(category.category_key)
        if model_row is not None:
            self._populate_model_combo(category, provider)
            model_row.setVisible(bool(provider.models))

        # LLM realtime collapse (hide STT+TTS rows + show the note).
        if category.category_key == "LLM":
            self._apply_realtime_collapse()

        self._update_save_enabled()

    def _on_signup_clicked(self, category: _ProviderCategory) -> None:
        """User clicked 'Get key →': open selected provider's signup URL
        in default browser via QDesktopServices."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        QDesktopServices.openUrl(QUrl(provider.signup_url))

    def _selected_provider(self, category_key: str) -> _Provider | None:
        dropdown = self._dropdowns.get(category_key)
        if dropdown is None:
            return None
        category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == category_key)
        return category.providers[dropdown.currentIndex()]

    def _shares_key(self, category_key: str) -> bool:
        """True when this row's provider uses the same key slot as the provider
        picked in the row it shares with, so that row's key covers both."""
        provider = self._selected_provider(category_key)
        if provider is None or not provider.shares_key_with:
            return False
        other = self._selected_provider(provider.shares_key_with)
        return other is not None and other.api_key_env_var == provider.api_key_env_var

    def _apply_key_sharing(self) -> None:
        """Hide a row's key box when another row's key covers it. A box that
        comes back into view shows the latest key typed for its slot, so an
        edit made while it was hidden is the one that gets saved."""
        for key, note in self._share_notes.items():
            provider = self._selected_provider(key)
            if provider is None or key not in self._key_inputs:
                continue
            shared = self._shares_key(key)
            typed = self._typed_keys.get(provider.api_key_env_var)
            if not shared and typed and self._key_inputs[key].text() != typed:
                self._loading_key = True
                try:
                    self._key_inputs[key].setText(typed)
                finally:
                    self._loading_key = False
            self._key_inputs[key].setVisible(not shared and not provider.hide_key_field)
            self._signup_buttons[key].setVisible(not shared and not provider.hide_key_field)
            note.setText(f"Uses your {provider.display_name} key below." if shared else "")
            note.setVisible(shared)
        self._update_save_enabled()

    def _on_reveal_toggled(self, checked: bool) -> None:
        for category in _PROVIDER_CATEGORIES:
            key_input = self._key_inputs.get(category.category_key)
            if key_input is None:
                continue
            provider = category.providers[self._dropdowns[category.category_key].currentIndex()]
            key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked or not provider.secret
                else QLineEdit.EchoMode.Password
            )

    def _update_save_enabled(self) -> None:
        """Save enabled when every category's key field has non-empty content.

        Defensively no-op if self._buttons isn't constructed yet. The
        textChanged signal can fire during initial _build_category_row
        (when the keyring already has a key, setText(existing) fires
        before the QDialogButtonBox is added to the dialog at the end of
        _build_ui).
        """
        if not hasattr(self, "_buttons"):
            return
        # Skip categories collapsed by realtime (STT+TTS aren't used /
        # editable in realtime mode, so don't gate Save on their key fields).
        collapsed = self._collapsed_categories()
        all_filled = all(
            key_input.text().strip()
            for key, key_input in self._key_inputs.items()
            if key not in collapsed and self._selected_requires_key(key)
        )
        self._buttons.button(
            QDialogButtonBox.StandardButton.Save
        ).setEnabled(all_filled)
        llm_missing = self._selected_requires_key("LLM") and not self._key_inputs["LLM"].text().strip()
        self._save_hint.setText(
            "" if all_filled else "Paste your Claude key to save." if llm_missing else "Add the missing key in Advanced to save."
        )
        self._refresh_connection()
        self._refresh_where()
        if hasattr(self, "_voice_hint"):
            self._sync_voice_switch()
            tts = self._key_inputs["TTS"]
            self._voice_hint.setVisible(
                self._voice.value() == "cloud" and self._selected_requires_key("TTS") and not tts.text().strip()
            )

    def _on_save(self) -> None:
        """Persist provider selection + currently-selected provider's key
        for each category to keyring.

        If the user picks Ollama + a model that needs a newer Ollama version
        than they have (Issue #1), show a non-blocking warning BEFORE
        persisting. User can override and save anyway, or cancel.
        Compatibility check runs against a live ``/api/version`` ping. If
        Ollama is unreachable we skip the check entirely (don't conflate
        "Ollama down" with "incompatible model").
        """
        # Pre-save compatibility check for Ollama LLM.
        llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        llm_dropdown = self._dropdowns["LLM"]
        llm_provider = llm_category.providers[llm_dropdown.currentIndex()]
        if llm_provider.provider_id == "ollama":
            model = self._selected_model_id(llm_category, llm_provider)
            if model and not self._confirm_ollama_compat(model):
                return  # user cancelled: abort save, no writes

        for category in _PROVIDER_CATEGORIES:
            dropdown = self._dropdowns[category.category_key]
            provider = category.providers[dropdown.currentIndex()]

            # 1. Persist provider selection (e.g. "TTS_PROVIDER" → "elevenlabs")
            self._store(f"{category.category_key}_PROVIDER", provider.provider_id, restart=True)

            # 2. Persist the API key for the selected provider. A row whose key
            # is covered by another row writes nothing; that row saves it.
            key_value = "" if self._shares_key(category.category_key) else (
                self._key_inputs[category.category_key].text().strip()
            )
            if key_value:
                self._store(provider.api_key_env_var, key_value, restart=True)

            # 3. Persist the chosen model for providers with a model picker
            # (Ollama→OLLAMA_MODEL_VISION). Only the selected provider's model
            # combo is live, so we only persist that one.
            if provider.models and provider.model_setting:
                model_id = self._selected_model_id(category, provider)
                if model_id:
                    self._store(provider.model_setting, model_id, restart=True)

        # These all apply at once (app.apply_live_settings).
        if self._draw_checkbox is not None:
            self._store("ANNOTATION_MODE", "on" if self._draw_checkbox.isChecked() else "off")
        self._store("HOTKEY", self._hotkey)
        self._store("MIC_DEVICE", self._mic_box.currentData() or "")
        self._store("EDGE_GLOW", "on" if self._glow.isChecked() else "off")
        self._store("ISLAND_SIDE", self._side.value() or "right")
        self._store("KEEP_HISTORY", "on" if self._keep_history.isChecked() else "off")
        theme.set_avatar(theme.current_avatar_id())  # persists the picked avatar
        self._avatar_at_open = theme.current_avatar_id()
        self.accept()

    def _store(self, name: str, value: str, *, restart: bool = False) -> None:
        """Save one setting; note when the running app can't pick it up live."""
        if restart and (keyring.get_password(KEYRING_SERVICE, name) or "") != value:
            self._needs_restart = True
        keyring.set_password(KEYRING_SERVICE, name, value)

    def needs_restart(self) -> bool:
        """True after Save changed a provider, key or model."""
        return self._needs_restart

    def reject(self) -> None:
        """Cancel also undoes an avatar previewed in this dialog."""
        if theme.current_avatar_id() != self._avatar_at_open:
            theme.set_avatar(self._avatar_at_open, persist=False)
        super().reject()

    def _confirm_ollama_compat(self, model: str) -> bool:
        """Pre-save Ollama compatibility check (Issue #1).

        Returns True if the save should proceed, False if the user
        cancelled. Pings the user's Ollama server for its version,
        checks against the known mllama-supports-from table. Shows a
        QMessageBox warning ONLY if there's a confirmed incompatibility;
        silent on success or when Ollama is unreachable.
        """
        from ollama_health import check_model_compatibility, detect_ollama_version

        host = self._key_inputs["LLM"].text().strip() or OLLAMA_HOST
        ollama_version = detect_ollama_version(host)
        warning = check_model_compatibility(model, ollama_version)
        if warning is None:
            return True  # compatible OR can't check: proceed silently

        reply = QMessageBox.warning(
            self,
            "Ollama compatibility warning",
            warning + "\n\nSave anyway?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Save


def required_keys_present() -> bool:
    """Probe: does every required-provider's API key resolve?

    "Required" = the currently-SELECTED provider per category
    (resolved via resolve_setting on LLM_PROVIDER / STT_PROVIDER /
    TTS_PROVIDER). The probe is what the launcher uses to decide whether
    to show the modal at start.

    A brain (LLM) provider counts as ready with its own key or with an
    OpenRouter key saved in any brain slot, the same rule the app uses.
    """
    from config import resolve_api_key, resolve_setting

    def _selected(category: _ProviderCategory) -> _Provider:
        provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=_default_provider_id(category),
        )
        return next(
            (p for p in category.providers if p.provider_id == provider_id),
            category.providers[category.default_index],
        )

    # If the selected LLM provider does speech end-to-end (realtime),
    # the STT + TTS keys aren't required: realtime never uses them.
    llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
    realtime = _selected(llm_category).hides_other_categories

    for category in _PROVIDER_CATEGORIES:
        if realtime and category.category_key in ("STT", "TTS"):
            continue
        provider = _selected(category)
        if not provider.requires_key:
            continue  # local provider (Ollama / faster-whisper / kokoro): no key needed
        key = resolve_api_key(provider.api_key_env_var)
        if not key and category.category_key == "LLM":
            key = next(
                (k for k in (resolve_api_key(p.api_key_env_var) for p in category.providers if p.requires_key)
                 if k and k.startswith("sk-or-")),
                None,
            )
        if not key:
            return False
    return True
