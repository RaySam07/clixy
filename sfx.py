"""Clixy's little interface sounds: soft taps, pops, whooshes and chimes.

Every sound is made here, in numpy, from nothing: tones with a few
(sometimes inharmonic) partials, quick pitch envelopes for pops and blips,
light FM for bells, and filtered noise for air, paper and whooshes. They all
share one small synthetic room, so they sound like they belong together. Each
has a few variants, and play() nudges the pitch a little, so a sound heard
twice in a row never sounds like a machine.

Playback is one output stream that stays open, because opening an audio
device takes 300-700 ms on Windows. Its callback mixes every sound playing at
once (they overlap instead of cutting each other off), applies the master
volume and rounds off the sum with a gentle tanh limiter. When the device goes
away (headphones unplugged, the PC woke from sleep) the stream is dropped and
opened again on the next sound.

    import sfx
    sfx.warm()           # at startup: renders the sounds and opens the device
    sfx.play("press")    # from anywhere, as often as you like; returns at once

Threads: play() and the setters may be called from any thread (normally Qt's
main thread). Rendering and opening the device happen on background threads.
The mixer runs on PortAudio's thread; it takes no locks and only reads what
the other threads hand it through a queue.
"""
from __future__ import annotations

import itertools
import os
import random
import threading
import time
import zlib
from collections import deque
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np

VERSION = 1
"""Bump when any sound changes."""

SAMPLE_RATE = 48_000
BLOCKSIZE = 480
"""10 ms at 48 kHz, the period Windows' audio engine runs at."""

NAMES: dict[str, str] = {
    "hover": "The pointer enters a button or card: a tiny, airy glass tick (very quiet).",
    "press": "A button is pressed: a round gel pop with a quick pitch drop.",
    "toggle_on": "A switch is turned on: two tiny clicks, the second higher and brighter.",
    "toggle_off": "A switch is turned off: two tiny clicks, the second lower and duller.",
    "select": "A segment, option or card is picked: a soft wooden tock.",
    "open": "The notch unfolds into the panel: a rising airy whoosh and two rising notes.",
    "close": "The panel tucks back into the notch: a falling whoosh and a low soft tock.",
    "page": "The panel switches views or Settings sections: a quick paper-slide swish.",
    "listen_start": "The hotkey goes down and the mic is hot: a bright rising two-note bloop.",
    "listen_end": "The hotkey comes up: one soft falling note.",
    "think": "Thinking starts: a very soft, quiet shimmer.",
    "note": "A sketch-note card lands on screen: a soft paper thud and a light rustle.",
    "clear": "A note is cleared away: an eraser-like swipe that whooshes off.",
    "build": "A 3D model has finished building: three rising blips and a shimmer.",
    "point": "The buddy flies to point at something: a short swoosh, then a gentle bell ping.",
    "done": "A task or guide step is complete: a quick sparkling arpeggio (C6 E6 G6 C7).",
    "success": "Settings are saved: a small warm ta-da of two bell chords.",
    "error": "Something failed: a gentle 'nope' of two soft falling tones.",
    "send": "A typed question is sent: an upward swoop.",
    "type": "A key is typed in a text field: a very quiet soft key tick.",
    "slide": "Flipping through a stack of note cards: a card sliding past.",
    "buddy": "A buddy character is picked: a cute springy boing.",
    "hello": "Clixy says hello at start: a warm three-note chime.",
}
"""Every sound, with what it is for."""

_PITCH_NUDGE = 0.03          # each play is up to 3% higher or lower
_MIN_GAP_S = {"hover": 0.05, "type": 0.05}
_DEFAULT_GAP_S = 0.03        # the same sound at most this often (no machine-gunning)
_LATE_S = 0.35               # a sound that cannot start by then is skipped
_LATE_FOR = {"hello": 2.0}   # ...except the greeting, which may wait for the device
_PREVIEW_LATE_S = 2.0        # a Settings "test sound" may wait for the device to open
_MAX_VOICES = 16
_MAX_QUEUED = 64
_STALE_S = 0.6               # a running stream with no callback for this long is dead
_RETRY_S = 2.0               # after a failed open, wait this long before trying again
_IDLE_CLOSE_S = 4.0          # with effects off, a preview's device closes after this

_CEILING_HZ = 8_500.0        # partials above this are left out: nothing piercing
_HP_HZ = 120.0               # no rumble below this
_LP_HZ = 9_000.0


# ── Settings ────────────────────────────────────────────────────────────────

def _settings_from_config() -> tuple[bool, float]:
    """SOUND_EFFECTS and SOUND_VOLUME from config, or on and 0.7."""
    try:
        import config
        return bool(config.SOUND_EFFECTS), max(0, min(100, int(config.SOUND_VOLUME))) / 100.0
    except Exception:
        return True, 0.7


def _taper(volume: float) -> float:
    """Master gain for a 0-1 volume: squared (an audio taper, so the slider
    feels even), with 0.7, the default, playing the sounds as designed."""
    return (volume * volume) / 0.49


_enabled, _volume = _settings_from_config()
_master_gain = _taper(_volume)


def enabled() -> bool:
    """Whether play() makes a sound."""
    return _enabled


def set_enabled(on: bool) -> None:
    """Turn the sounds on or off. Off also lets go of the audio device."""
    global _enabled
    on = bool(on)
    if on == _enabled:
        return
    _enabled = on
    if on:
        _ensure_stream(_now())
    else:
        _pending.clear()
        _release_device()


def volume() -> float:
    """The master volume, 0-1."""
    return _volume


def set_volume(v: float) -> None:
    """Set the master volume, 0-1 (0.7 plays the sounds as designed)."""
    global _volume, _master_gain
    try:
        v = float(v)
    except (TypeError, ValueError):
        return
    if v != v:  # NaN
        return
    _volume = max(0.0, min(1.0, v))
    _master_gain = _taper(_volume)


# ── Playing ─────────────────────────────────────────────────────────────────

class _Voice:
    """One sound playing (or waiting to): which variant, how fast, how loud."""

    __slots__ = ("name", "variant", "rate", "gain", "deadline", "data", "pos")

    def __init__(self, name: str, variant: int, rate: float, gain: float, deadline: float) -> None:
        self.name = name
        self.variant = variant
        self.rate = rate
        self.gain = gain
        self.deadline = deadline
        self.data: np.ndarray | None = None
        self.pos = 0.0


_now = time.monotonic
_lock = threading.Lock()            # rate limits and stream bookkeeping; never held by the mixer
_pending: deque[_Voice] = deque()   # play() -> mixer
_voices: list[_Voice] = []          # the mixer's own list
_last_played: dict[str, float] = {}
_last_variant: dict[str, int] = {}


def play(name: str, gain: float = 1.0) -> None:
    """Play a sound by name (see NAMES). Returns at once, from any thread.

    Does nothing when sounds are off, the name is unknown, the same sound
    played a moment ago, or audio is not available. Picks a variant (never
    the same one twice in a row) and nudges its pitch up to 3% either way.
    ``gain`` scales this one play (0-2)."""
    if _enabled:
        _queue(name, gain, _LATE_FOR.get(name, _LATE_S))


def preview(name: str) -> None:
    """Play a sound even when sounds are off: Settings' "test sound" button."""
    _queue(name, 1.0, _PREVIEW_LATE_S)
    if not _enabled:
        timer = threading.Timer(_IDLE_CLOSE_S, _close_if_disabled)
        timer.daemon = True
        timer.start()


def _queue(name: str, gain: float, late: float) -> None:
    try:
        spec = _SPECS.get(name)
        if spec is None:
            return
        gain = max(0.0, min(2.0, float(gain)))
        now = _now()
        with _lock:
            if now - _last_played.get(name, -1e9) < _MIN_GAP_S.get(name, _DEFAULT_GAP_S):
                return
            _last_played[name] = now
            count = spec.variants
            previous = _last_variant.get(name)
            if count > 1 and previous is not None:
                variant = random.randrange(count - 1)
                variant += variant >= previous
            else:
                variant = random.randrange(count)
            _last_variant[name] = variant
        rate = 1.0 + random.uniform(-_PITCH_NUDGE, _PITCH_NUDGE)
        _pending.append(_Voice(name, variant, rate, gain, now + late))
        while len(_pending) > _MAX_QUEUED:
            _pending.popleft()
        _ensure_stream(now)
    except Exception:
        pass


# ── The mixer (runs on PortAudio's thread) ──────────────────────────────────

_RAMP = np.arange(8192, dtype=np.float64)
_applied_gain = _master_gain
_last_callback = 0.0


def _callback(outdata, frames, time_info, status) -> None:
    """PortAudio asks for the next block: mix every voice into it."""
    global _last_callback
    now = _now()
    _last_callback = now
    try:
        _mix(outdata, frames, now)
    except Exception:
        try:
            outdata.fill(0.0)
        except Exception:
            pass


def _mix(out: np.ndarray, frames: int, now: float) -> None:
    """Sum the voices into ``out`` (frames x 2, float32), apply the master
    volume (ramped across the block when it changed) and soft-clip."""
    global _applied_gain, _RAMP
    out.fill(0.0)
    if frames > _RAMP.size:
        _RAMP = np.arange(frames, dtype=np.float64)
    waiting = None
    for _ in range(len(_pending)):
        try:
            voice = _pending.popleft()
        except IndexError:
            break
        if now > voice.deadline:
            continue  # too late to sound right: skip it
        variants = _SOUNDS.get(voice.name)
        if not variants:
            waiting = waiting or []
            waiting.append(voice)  # not rendered yet
            continue
        voice.data = variants[voice.variant % len(variants)]
        voice.pos = 0.0
        _voices.append(voice)
    if waiting:
        _pending.extend(waiting)
    if len(_voices) > _MAX_VOICES:
        del _voices[: len(_voices) - _MAX_VOICES]
    target = _master_gain
    if not _voices:
        _applied_gain = target
        return
    ramp = _RAMP[:frames]
    keep = []
    for voice in _voices:
        data = voice.data
        last = data.shape[0] - 1  # data ends with one extra silent frame
        idx = voice.pos + voice.rate * ramp
        count = frames if idx[-1] < last else int(np.searchsorted(idx, last))
        if count > 0:
            i0 = idx[:count].astype(np.intp)
            frac = (idx[:count] - i0).astype(np.float32)[:, None]
            a = data[i0]
            out[:count] += (a + (data[i0 + 1] - a) * frac) * voice.gain
        voice.pos += voice.rate * frames
        if count == frames and voice.pos < last:
            keep.append(voice)
    _voices[:] = keep
    start = _applied_gain
    if start == target:
        out *= target
    else:
        out *= (start + (target - start) * (ramp / frames)).astype(np.float32)[:, None]
    np.tanh(out, out=out)
    _applied_gain = target


# ── The output device ───────────────────────────────────────────────────────

_stream = None
_stream_token = 0
_stream_started = 0.0
_stream_failed = False
_opening = False
_retry_at = 0.0
_tokens = itertools.count(1)


def _open_device(callback: Callable, finished: Callable):
    """Open and start the output stream: Windows' shared WASAPI first (about
    30 ms from play() to the speaker), else the default device (MME, ~90 ms)."""
    import sounddevice as sd

    tries: list[tuple] = []
    try:
        for api in sd.query_hostapis():
            if "WASAPI" in api["name"] and api["default_output_device"] >= 0:
                tries.append((api["default_output_device"], 0.03,
                              sd.WasapiSettings(auto_convert=True)))
    except Exception:
        pass
    tries.append((None, "low", None))
    error: Exception | None = None
    for device, latency, extra in tries:
        try:
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=2, dtype="float32", blocksize=BLOCKSIZE,
                latency=latency, device=device, extra_settings=extra,
                callback=callback, finished_callback=finished,
            )
        except Exception as exc:
            error = exc
            continue
        try:
            stream.start()
        except Exception as exc:
            error = exc
            _close_quietly(stream)
            continue
        return stream
    raise OSError(f"no audio output: {error}")


def _ensure_stream(now: float, wait: bool = False) -> None:
    """Open the stream in the background if it is missing or dead."""
    global _opening
    with _lock:
        if _opening:
            return
        old = _stream
        if old is not None and not _stream_failed and not (
            now - _stream_started > _STALE_S and now - _last_callback > _STALE_S
        ):
            return
        if now < _retry_at:
            return
        _opening = True
        opener = _open_device  # taken now, so a late thread uses the opener of its time
    if wait:
        _reopen(old, opener)
    else:
        threading.Thread(target=_reopen, args=(old, opener), daemon=True, name="sfx-open").start()


def _reopen(old, opener: Callable) -> None:
    global _stream, _stream_token, _stream_started, _stream_failed, _opening, _retry_at, _last_callback
    try:
        if old is not None:
            with _lock:
                if _stream is old:
                    _stream = None
            _close_quietly(old)
        _voices.clear()  # no stream is running now, so the mixer is idle
        token = next(_tokens)
        stream = opener(_callback, lambda: _finished(token))
        with _lock:
            _stream, _stream_token = stream, token
            _stream_failed = False
            _stream_started = _last_callback = _now()
        _start_render()
    except Exception:
        with _lock:
            _retry_at = _now() + _RETRY_S
    finally:
        with _lock:
            _opening = False


def _finished(token: int) -> None:
    """PortAudio says a stream stopped (a device error, or our own close)."""
    global _stream_failed
    if token == _stream_token:
        _stream_failed = True


def _close_quietly(stream) -> None:
    try:
        stream.close()
    except Exception:
        pass


def _release_device() -> None:
    """Let go of the device, off the caller's thread (closing can be slow)."""
    global _stream
    with _lock:
        stream, _stream = _stream, None
    if stream is not None:
        threading.Thread(target=_close_quietly, args=(stream,), daemon=True, name="sfx-close").start()


def _close_if_disabled() -> None:
    """After a preview with sounds off: let the device go, unless another
    preview came since."""
    with _lock:
        last = max(_last_played.values(), default=0.0)
    if not _enabled and _now() - last >= _IDLE_CLOSE_S - 0.1:
        _release_device()


def shutdown() -> None:
    """Close the output device now (at exit). The next sound opens it again."""
    global _stream
    with _lock:
        stream, _stream = _stream, None
    _pending.clear()
    if stream is not None:
        _close_quietly(stream)
    _voices.clear()


# ── Warming up ──────────────────────────────────────────────────────────────

_SOUNDS: dict[str, list[np.ndarray]] = {}   # name -> variants, each frames x 2 + one silent frame
_render_lock = threading.Lock()
_render_started = False


def warm(wait: bool = False) -> None:
    """Render every sound and open the output device, on a background thread
    (or on this one with ``wait=True``), so the first sound is instant.
    Does nothing while sounds are off: preview() then warms on demand."""
    if not _enabled:
        return
    if wait:
        _warm_now()
    else:
        threading.Thread(target=_warm_now, daemon=True, name="sfx-warm").start()


def _warm_now() -> None:
    global _render_started
    try:
        with _lock:
            _render_started = True
        _ensure_stream(_now())
        _render_all()
    except Exception:
        pass


def _start_render() -> None:
    global _render_started
    with _lock:
        if _render_started:
            return
        _render_started = True
    threading.Thread(target=_render_all, daemon=True, name="sfx-render").start()


def _render_all() -> None:
    """Load the sounds from the cache, or render the ones missing (the
    first-heard first) and cache them for the next start."""
    with _render_lock:
        cached = _load_cache()
        fresh = False
        for name in _ORDER:
            if name in _SOUNDS:
                continue
            takes = cached.get(name)
            if takes is None:
                try:
                    takes = [np.vstack([render(name, k), np.zeros((1, 2), np.float32)])
                             for k in range(_SPECS[name].variants)]
                except Exception:
                    continue
                fresh = True
            _SOUNDS[name] = takes
        _ROOM_SPECTRA.clear()
        if fresh and set(_SOUNDS) == set(_SPECS):
            _save_cache(dict(_SOUNDS))


_CACHE_DIR: Path | None = None
"""Where rendered sounds are kept; None means ~/.clixy/cache/sfx."""


def _cache_file() -> Path:
    """The cache file for this exact set of sounds: its name carries VERSION
    and a checksum of the synthesis code and settings, so any change to a
    sound renders it afresh."""
    root = _CACHE_DIR
    if root is None:
        try:
            import config
            root = config.APP_HOME / "cache" / "sfx"
        except Exception:
            root = Path.home() / ".clixy" / "cache" / "sfx"
    code = [_fingerprint(fn.__code__) for _, fn in sorted(globals().items())
            if getattr(fn, "__module__", None) == __name__ and hasattr(fn, "__code__")]
    settings = repr((SAMPLE_RATE, _HP_HZ, _LP_HZ, _CEILING_HZ, _PURE, _BLOOP, _BELLS,
                     [(name, s.peak, s.ms, s.wet, s.variants) for name, s in sorted(_SPECS.items())]))
    stamp = zlib.crc32(b"".join(code) + settings.encode())
    return Path(root) / f"sounds-v{VERSION}-{stamp:08x}.npz"


def _fingerprint(code) -> bytes:
    """A function's bytecode and constants (nested functions too), the same in
    every run (unlike marshal, whose output depends on reference counts)."""
    parts = [code.co_code, repr(code.co_names).encode()]
    for const in code.co_consts:
        parts.append(_fingerprint(const) if hasattr(const, "co_code") else repr(const).encode())
    return b"".join(parts)


def _load_cache() -> dict[str, list[np.ndarray]]:
    """The cached sounds, or {} when there are none (or they look wrong)."""
    try:
        path = _cache_file()
        if not path.is_file():
            return {}
        out = {}
        with np.load(path, allow_pickle=False) as data:
            for name, spec in _SPECS.items():
                takes = [data[f"{name}.{k}"] for k in range(spec.variants)]
                if any(x.dtype != np.float32 or x.ndim != 2 or x.shape[1] != 2
                       or not np.isfinite(x).all() or np.abs(x).max() > 0.5 for x in takes):
                    return {}
                out[name] = takes
        return out
    except Exception:
        return {}


def _save_cache(sounds: dict[str, list[np.ndarray]]) -> None:
    """Write the sounds for the next start and remove older caches. Best effort."""
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.stem + ".part.npz")
        np.savez(part, **{f"{name}.{k}": x for name, takes in sounds.items()
                          for k, x in enumerate(takes)})
        os.replace(part, path)
        for old in path.parent.glob("sounds-*.npz"):
            if old != path:
                old.unlink(missing_ok=True)
    except Exception:
        pass


# ── Synthesis toolkit ───────────────────────────────────────────────────────

_TWO_PI = 2.0 * np.pi


def _n(seconds: float) -> int:
    return max(1, int(round(seconds * SAMPLE_RATE)))


def _t(seconds: float) -> np.ndarray:
    return np.arange(_n(seconds)) / SAMPLE_RATE


def _fft_size(n: int) -> int:
    """The smallest quick FFT length (2^a times 1, 3, 5, 9 or 15) of at least n."""
    best = None
    for odd in (1, 3, 5, 9, 15):
        size = odd
        while size < n:
            size *= 2
        best = size if best is None else min(best, size)
    return best


def _lp(f: np.ndarray, fc: float, order: int = 2) -> np.ndarray:
    return 1.0 / np.sqrt(1.0 + (f / fc) ** (2 * order))


def _hp(f: np.ndarray, fc: float, order: int = 2) -> np.ndarray:
    return 1.0 / np.sqrt(1.0 + (fc / np.maximum(f, 1e-3)) ** (2 * order))


def _bp(f: np.ndarray, fc: float, octaves: float) -> np.ndarray:
    """A bell-shaped band around fc, ``octaves`` wide (one standard deviation)."""
    return np.exp(-0.5 * (np.log2(np.maximum(f, 1.0) / fc) / octaves) ** 2)


def _response(f: np.ndarray, hp: float | None, lp: float | None, band: tuple | None,
              order: int) -> np.ndarray:
    h = np.ones_like(f)
    if hp:
        h *= _hp(f, hp, order)
    if lp:
        h *= _lp(f, lp, order)
    if band:
        h *= _bp(f, *band)
    return h


def _filter(x: np.ndarray, hp: float | None = None, lp: float | None = None,
            band: tuple | None = None, order: int = 2) -> np.ndarray:
    """Zero-phase filtering in the frequency domain (mono or stereo)."""
    n = x.shape[0]
    size = _fft_size(n + _n(0.05))
    f = np.fft.rfftfreq(size, 1.0 / SAMPLE_RATE)
    h = _response(f, hp, lp, band, order)
    spectrum = np.fft.rfft(x, size, axis=0)
    return np.fft.irfft(spectrum * (h[:, None] if x.ndim == 2 else h), size, axis=0)[:n]


def _noise(dur: float, rng: np.random.Generator, hp: float | None = None,
           lp: float | None = None, band: tuple | None = None, order: int = 2) -> np.ndarray:
    """Filtered noise, made straight in the frequency domain, peak 1."""
    n = _n(dur)
    size = _fft_size(n)
    f = np.fft.rfftfreq(size, 1.0 / SAMPLE_RATE)
    spectrum = (rng.standard_normal(f.size) + 1j * rng.standard_normal(f.size))
    y = np.fft.irfft(spectrum * _response(f, hp, lp, band, order), size)[:n]
    return y / (np.max(np.abs(y)) + 1e-12)


def _sweep(dur: float, rng: np.random.Generator, center, width, frame: int = 512,
           hop: int = 128) -> np.ndarray:
    """Noise whose band glides: ``center`` (Hz) and ``width`` (octaves) are
    numbers or functions of time. Built frame by frame and overlap-added;
    every frame carries the same energy, so loudness is left to the
    envelope the caller multiplies in. Peak 1."""
    n = _n(dur)
    frames = n // hop + frame // hop + 1
    times = np.arange(frames) * hop / SAMPLE_RATE
    freqs = np.fft.rfftfreq(frame, 1.0 / SAMPLE_RATE)
    c = np.asarray(center(times) if callable(center) else np.full(frames, float(center)))
    w = np.asarray(width(times) if callable(width) else np.full(frames, float(width)))
    log_f = np.log2(np.maximum(freqs, 1.0))[None, :]
    mag = np.exp(-0.5 * ((log_f - np.log2(c)[:, None]) / w[:, None]) ** 2)
    mag *= (_hp(freqs, 150.0) * _lp(freqs, 8000.0, 3))[None, :]
    mag /= np.sqrt(np.sum(mag ** 2, axis=1, keepdims=True)) + 1e-12
    spectrum = (rng.standard_normal(mag.shape) + 1j * rng.standard_normal(mag.shape)) * mag
    window = 0.5 - 0.5 * np.cos(_TWO_PI * np.arange(frame) / frame)
    grains = np.fft.irfft(spectrum, frame, axis=1) * window
    buf = np.zeros((frames - 1) * hop + frame)
    for k in range(frames):
        buf[k * hop:k * hop + frame] += grains[k]
    y = buf[frame // 2:frame // 2 + n]
    return y / (np.max(np.abs(y)) + 1e-12)


def _crackle(dur: float, rng: np.random.Generator, rate: float, fc: float,
             octaves: float = 0.7) -> np.ndarray:
    """Sparse little crackles (paper fibres, eraser grit) in a band. Peak 1."""
    n = _n(dur)
    x = np.zeros(n)
    count = int(rng.poisson(rate * dur))
    if count:
        np.add.at(x, rng.integers(0, n, count), rng.standard_normal(count) * rng.exponential(1.0, count))
    y = _filter(x, band=(fc, octaves), hp=300.0)
    return y / (np.max(np.abs(y)) + 1e-12)


def _flutter(dur: float, rng: np.random.Generator, rate: float, depth: float) -> np.ndarray:
    """A grainy, uneven amplitude (paper texture), between 1 - depth and 1."""
    x = np.abs(_noise(dur, rng, lp=rate))
    return 1.0 - depth + depth * x / (np.max(x) + 1e-12)


def _env(t: np.ndarray, attack: float, tau: float) -> np.ndarray:
    """A smooth (raised-cosine) rise over ``attack`` seconds, then an
    exponential decay with time constant ``tau``."""
    e = np.exp(-np.maximum(t - attack, 0.0) / tau)
    if attack > 0:
        rise = t < attack
        e[rise] = 0.5 - 0.5 * np.cos(np.pi * t[rise] / attack)
    return e


def _bump(t: np.ndarray, peak: float, fall: float) -> np.ndarray:
    """A swell: a smooth rise to 1 at ``peak`` seconds, then an exponential fall."""
    e = np.exp(-np.maximum(t - peak, 0.0) / fall)
    rise = t < peak
    e[rise] = np.sin(0.5 * np.pi * t[rise] / peak) ** 2
    return e


_PURE = ((1.0, 1.0, 1.0),)
_BLOOP = ((1.0, 1.0, 1.0), (2.0, 0.12, 0.6), (3.0, 0.03, 0.4))
_BELLS = {
    # (ratio to the fundamental, level, decay relative to the fundamental's)
    "soft": ((1.0, 1.0, 1.0), (2.0, 0.10, 0.55), (3.0, 0.03, 0.4), (4.16, 0.02, 0.22)),
    "glass": ((1.0, 1.0, 1.0), (2.76, 0.18, 0.42), (5.40, 0.05, 0.24), (8.93, 0.012, 0.14)),
    "warm": ((1.0, 1.0, 1.0), (2.0, 0.20, 0.65), (3.0, 0.06, 0.42), (4.0, 0.02, 0.3),
             (5.02, 0.012, 0.2)),
}


def _tone(dur: float, freq, partials=_PURE, attack: float = 0.002, tau: float = 0.05,
          fm: float = 0.0, fm_ratio: float = 1.0, fm_tau: float = 0.015) -> np.ndarray:
    """A pitched voice. ``freq`` is Hz or a function of time (pitch
    envelopes); ``fm`` adds a brief bell-like brightness at the start."""
    t = _t(dur)
    f = np.asarray(freq(t) if callable(freq) else np.full(t.shape, float(freq)), dtype=float)
    phase = _TWO_PI * np.cumsum(f) / SAMPLE_RATE
    mod = np.zeros_like(t)
    if fm:
        m = min(t.size, _n(9.2 * fm_tau))  # the FM is 80 dB down by then
        mod[:m] = fm * np.exp(-t[:m] / fm_tau) * np.sin(fm_ratio * phase[:m])
    after = np.maximum(t - attack, 0.0)
    a = min(t.size, _n(attack)) if attack > 0 else 0
    rise = 0.5 - 0.5 * np.cos(np.pi * t[:a] / attack) if a else None
    top = float(f.max())
    y = np.zeros_like(t)
    for ratio, level, decay in partials:
        if top * ratio > _CEILING_HZ:
            continue
        d = tau * decay
        m = max(a, min(t.size, _n(attack + 9.2 * d)))  # each partial only until it is 80 dB down
        env = np.exp(-after[:m] / d)
        if a:
            env[:a] = rise
        y[:m] += level * np.sin(ratio * phase[:m] + mod[:m]) * env
    return y


def _bell(dur: float, freq: float, tau: float, kind: str = "soft", attack: float = 0.003,
          fm: float = 0.3, width: float = 0.0004) -> np.ndarray:
    """A bell note in stereo: each side tuned a hair apart, for width (little
    enough that the two sides only drift out of step once the note has died)."""
    ratio = 3.5 if kind == "glass" else 1.0
    left = _tone(dur, freq * (1 - width), _BELLS[kind], attack, tau, fm=fm, fm_ratio=ratio)
    right = _tone(dur, freq * (1 + width), _BELLS[kind], attack, tau, fm=fm, fm_ratio=ratio)
    return np.stack([left, right], axis=1)


def _pan(x: np.ndarray, pos) -> np.ndarray:
    """Mono to stereo, equal power; ``pos`` -1 (left) to 1 (right), or an array."""
    angle = (np.clip(pos, -1.0, 1.0) + 1.0) * (np.pi / 4)
    return np.stack([x * np.cos(angle), x * np.sin(angle)], axis=1) * np.sqrt(2.0)


def _glide(t: np.ndarray, start: float, end: float, over: float, curve: float = 1.0) -> np.ndarray:
    """From ``start`` to ``end`` over ``over`` seconds, exponentially (as pitch moves)."""
    return start * (end / start) ** (np.clip(t / over, 0.0, 1.0) ** curve)


def _place(out: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0) -> None:
    """Mix ``x`` into ``out`` starting ``at`` seconds in."""
    i = int(round(at * SAMPLE_RATE))
    if i >= out.shape[0]:
        return
    m = min(x.shape[0], out.shape[0] - i)
    seg = x[:m]
    if out.ndim == 2 and seg.ndim == 1:
        seg = seg[:, None]
    out[i:i + m] += gain * seg


def _fade(x: np.ndarray, fade_in: float, fade_out: float) -> None:
    """Raised-cosine fades so the first and last samples are exactly zero."""
    a = min(x.shape[0] // 2, _n(fade_in))
    b = min(x.shape[0] // 2, _n(fade_out))
    x[:a] *= (0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a))[:, None]
    x[-b:] *= (0.5 + 0.5 * np.cos(np.pi * np.arange(1, b + 1) / b))[:, None]


# ── The room ────────────────────────────────────────────────────────────────

_ROOM: np.ndarray | None = None
_ROOM_SPECTRA: dict[int, np.ndarray] = {}


def _room() -> np.ndarray:
    """A small, soft room: 0.36 s of decaying stereo noise (highs die first)
    after a few early reflections, a different pattern on each side. The
    noise starts white (its bands split it without overlap), so the room
    colours no note more than another."""
    global _ROOM
    if _ROOM is None:
        rng = np.random.default_rng(20261104)
        n = _n(0.36)
        t = np.arange(n) / SAMPLE_RATE
        ir = np.zeros((n, 2))
        taps = (((7.3, 2.2), (12.9, -1.6), (19.7, 1.2), (26.1, -0.9)),
                ((8.6, 2.1), (14.4, -1.5), (21.8, 1.1), (29.5, 0.8)))
        for ch in range(2):
            white = rng.standard_normal(n)
            low, rest = _filter(white, lp=1400.0), _filter(white, hp=1400.0)
            mid, high = _filter(rest, lp=3800.0), _filter(rest, hp=3800.0)
            tail = low * np.exp(-t / 0.055) + mid * np.exp(-t / 0.038) + high * np.exp(-t / 0.024)
            tail *= 0.5 - 0.5 * np.cos(np.pi * np.clip(t / 0.012, 0.0, 1.0))
            delay = _n(0.011 + 0.0013 * ch)
            ir[delay:, ch] = tail[:n - delay]
            for ms, level in taps[ch]:
                ir[_n(ms / 1000.0), ch] += level
        ir = _filter(ir, hp=220.0, lp=6500.0)
        ir /= np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True))
        end = _n(0.03)
        ir[-end:] *= (0.5 + 0.5 * np.cos(np.pi * np.arange(1, end + 1) / end))[:, None]
        _ROOM = ir
    return _ROOM


def _room_spectrum(size: int) -> np.ndarray:
    spectrum = _ROOM_SPECTRA.get(size)
    if spectrum is None:
        spectrum = _ROOM_SPECTRA[size] = np.fft.rfft(_room(), size, axis=0)
    return spectrum


def _master(dry: np.ndarray, peak: float, wet: float) -> np.ndarray:
    """Finish a sound: add the room, keep it between 120 Hz and 9 kHz, set its
    peak, trim the silent tail and fade both ends. float32, frames x 2."""
    dry = np.asarray(dry, dtype=np.float64)
    lead = _n(0.002)
    ir = _room()
    n = lead + dry.shape[0] + ir.shape[0]
    size = _fft_size(n + _n(0.05))
    buf = np.zeros((size,) + dry.shape[1:])
    buf[lead:lead + dry.shape[0]] = dry
    spectrum = np.fft.rfft(buf, axis=0)
    if dry.ndim == 1:
        spectrum = spectrum[:, None]
        mono = spectrum
    else:
        mono = spectrum.mean(axis=1, keepdims=True)
    f = np.fft.rfftfreq(size, 1.0 / SAMPLE_RATE)
    h = (_hp(f, _HP_HZ, 4) * _lp(f, _LP_HZ, 4))[:, None]
    out = np.fft.irfft((spectrum + wet * mono * _room_spectrum(size)) * h, size, axis=0)[:n]
    out *= peak / (np.max(np.abs(out)) + 1e-12)
    loud = np.flatnonzero(np.max(np.abs(out), axis=1) > peak * 0.002)  # -54 dB
    out = out[:min(n, int(loud[-1]) + _n(0.004))]
    _fade(out, 0.001, 0.004)
    return out.astype(np.float32)


# ── The sounds ──────────────────────────────────────────────────────────────

class _Spec(NamedTuple):
    make: Callable[[np.random.Generator, int], np.ndarray]
    peak: float       # target peak, 0-1
    ms: float         # intended length (to -30 dB), for the tests and the table
    wet: float        # how much room
    variants: int


_SPECS: dict[str, _Spec] = {}


def _sound(name: str, *, peak: float, ms: float, wet: float = 0.12, variants: int = 3):
    def register(make):
        _SPECS[name] = _Spec(make, peak, ms, wet, variants)
        return make
    return register


def render(name: str, variant: int = 0, room: bool = True) -> np.ndarray:
    """One variant of a sound as float32 samples (frames x 2) at 48 kHz.
    The same name and variant always give the same samples. ``room=False``
    leaves out the shared room (to measure the sound itself)."""
    spec = _SPECS[name]
    rng = np.random.default_rng(zlib.crc32(f"{name}:{variant}:{VERSION}".encode()))
    return _master(spec.make(rng, variant % spec.variants), spec.peak, spec.wet if room else 0.0)


def variants(name: str) -> int:
    """How many variants a sound has (0 if the name is unknown)."""
    spec = _SPECS.get(name)
    return spec.variants if spec else 0


def _jit(rng: np.random.Generator, amount: float) -> float:
    return 1.0 + rng.uniform(-amount, amount)


def _click(dur: float, freq: float, bright: float, rng: np.random.Generator) -> np.ndarray:
    """A tiny mechanical click: a damped ping plus a breath of contact noise."""
    t = _t(dur)
    ping = _tone(dur, freq, ((1.0, 1.0, 1.0), (2.3, 0.3 * bright, 0.5)), attack=0.0006, tau=0.004)
    contact = _noise(dur, rng, band=(freq * 1.7, 0.6)) * _env(t, 0.0003, 0.001)
    return ping + 0.35 * bright * contact


@_sound("hover", peak=0.025, ms=15, wet=0.04, variants=4)
def _hover(rng, v):
    dur = 0.04
    t = _t(dur)
    f = (3150.0, 3350.0, 3000.0, 3500.0)[v] * _jit(rng, 0.01)
    tick = _tone(dur, f, ((1.0, 1.0, 1.0), (2.41, 0.32, 0.55)), attack=0.0008, tau=0.0042)
    air = _noise(dur, rng, band=(6000.0, 0.45)) * _env(t, 0.0005, 0.0025)
    return tick + 0.25 * air


@_sound("type", peak=0.02, ms=25, wet=0.04, variants=4)
def _type(rng, v):
    dur = 0.05
    t = _t(dur)
    f = (1850.0, 2050.0, 1700.0, 2250.0)[v] * _jit(rng, 0.01)
    tick = _tone(dur, f, ((1.0, 1.0, 1.0), (2.7, 0.25, 0.5)), attack=0.0006, tau=0.0035)
    body = _tone(dur, f * 0.29, ((1.0, 1.0, 1.0), (2.0, 0.2, 0.6)), attack=0.001, tau=0.007)
    contact = _noise(dur, rng, band=(3500.0, 0.7)) * _env(t, 0.0003, 0.0012)
    return tick + 0.55 * body + 0.4 * contact


@_sound("press", peak=0.13, ms=60, wet=0.06)
def _press(rng, v):
    dur = 0.11
    t = _t(dur)
    base = (520.0, 560.0, 495.0)[v] * _jit(rng, 0.01)
    pop = _tone(dur, lambda t: base * (1 + 0.75 * np.exp(-t / 0.010)),
                ((1.0, 1.0, 1.0), (2.0, 0.16, 0.5), (3.0, 0.04, 0.3)), attack=0.0012, tau=0.017)
    body = _tone(dur, lambda t: base * 0.5 * (1 + 0.4 * np.exp(-t / 0.012)), attack=0.0015, tau=0.02)
    snap = _noise(dur, rng, band=(2600.0, 0.6)) * _env(t, 0.0003, 0.0015)
    return pop + 0.35 * body + 0.12 * snap


def _toggle(rng, v, first: float, second: float, levels: tuple, brights: tuple,
            knob: float, dull: float | None) -> np.ndarray:
    gap = (0.020, 0.023, 0.018)[v]
    j = _jit(rng, 0.015)
    out = np.zeros(_n(0.1))
    _place(out, _click(0.04, first * j, brights[0], rng), 0.0, levels[0])
    _place(out, _click(0.05, second * j, brights[1], rng), gap, levels[1])
    _place(out, _tone(0.06, knob * j, attack=0.001, tau=0.010), gap, 0.3)
    return _filter(out, lp=dull) if dull else out


@_sound("toggle_on", peak=0.10, ms=50, wet=0.06)
def _toggle_on(rng, v):
    return _toggle(rng, v, 1450.0, 2100.0, (0.65, 1.0), (0.6, 1.0), 520.0, None)


@_sound("toggle_off", peak=0.10, ms=50, wet=0.06)
def _toggle_off(rng, v):
    return _toggle(rng, v, 1900.0, 1250.0, (1.0, 0.6), (0.9, 0.4), 380.0, 5000.0)


@_sound("select", peak=0.12, ms=80, wet=0.07)
def _select(rng, v):
    dur = 0.14
    t = _t(dur)
    base = (740.0, 790.0, 700.0)[v] * _jit(rng, 0.01)
    tock = _tone(dur, lambda t: base * (1 + 0.06 * np.exp(-t / 0.004)),
                 ((1.0, 1.0, 1.0), (2.67, 0.2, 0.3), (3.93, 0.12, 0.25)), attack=0.0008, tau=0.022)
    knock = _noise(dur, rng, band=(2400.0, 0.8)) * _env(t, 0.0003, 0.0018)
    return tock + 0.18 * knock


@_sound("open", peak=0.18, ms=350, wet=0.12)
def _open(rng, v):
    out = np.zeros((_n(0.5), 2))
    t = _t(0.34)
    j = _jit(rng, 0.006)
    air = _sweep(0.34, rng, lambda t: _glide(t, 450.0, 3200.0, 0.28, 0.9), 0.9) * _bump(t, 0.16, 0.05)
    _place(out, _pan(air, np.linspace(-0.15, 0.15, t.size)), 0.0, 0.6)
    _place(out, _bell(0.4, 784.0 * j, 0.06), 0.10 + 0.005 * v, 0.75)       # G5
    _place(out, _bell(0.45, 1174.7 * j, 0.06), 0.17 + 0.005 * v, 1.0)      # D6
    return out


@_sound("close", peak=0.16, ms=250, wet=0.1)
def _close(rng, v):
    out = np.zeros((_n(0.4), 2))
    t = _t(0.2)
    j = _jit(rng, 0.01)
    air = _sweep(0.2, rng, lambda t: _glide(t, 2600.0, 380.0, 0.17), 0.85) * _bump(t, 0.07, 0.045)
    _place(out, _pan(air, np.linspace(0.12, -0.12, t.size)), 0.0, 0.6)
    tock = _tone(0.2, lambda t: 330.0 * j * (1 + 0.08 * np.exp(-t / 0.005)),
                 ((1.0, 1.0, 1.0), (2.6, 0.18, 0.35), (4.1, 0.05, 0.2)), attack=0.001, tau=0.028)
    _place(out, tock, 0.15, 1.0)
    return out


@_sound("page", peak=0.12, ms=120, wet=0.08, variants=4)
def _page(rng, v):
    dur = 0.16
    t = _t(dur)
    env = _bump(t, 0.035, 0.025)
    swish = _sweep(dur, rng, lambda t: _glide(t, 3800.0, 2400.0, 0.12), 1.0)
    swish *= _flutter(dur, rng, 120.0, 0.5) * env
    grit = _crackle(dur, rng, 400.0, 4200.0, 0.6) * env
    direction = 1.0 if v % 2 == 0 else -1.0
    return _pan(swish + 0.3 * grit, direction * np.linspace(-0.12, 0.12, t.size))


@_sound("listen_start", peak=0.18, ms=120, wet=0.08)
def _listen_start(rng, v):
    gap = (0.050, 0.046, 0.054)[v]
    j = _jit(rng, 0.004)
    out = np.zeros(_n(0.24))
    low = _tone(0.2, lambda t: 880.0 * j * (1 - 0.12 * np.exp(-t / 0.008)), _BLOOP, 0.002, 0.024)
    high = _tone(0.2, lambda t: 1318.5 * j * (1 - 0.08 * np.exp(-t / 0.008)), _BLOOP, 0.002, 0.02)
    _place(out, low, 0.0, 0.8)
    _place(out, high, gap, 1.0)
    return out


@_sound("listen_end", peak=0.14, ms=90, wet=0.08)
def _listen_end(rng, v):
    j = _jit(rng, 0.006)
    drop = (0.30, 0.27, 0.33)[v]
    return _tone(0.16, lambda t: 880.0 * j * (1 + drop * np.exp(-t / 0.022)), _BLOOP, 0.003, 0.025)


@_sound("think", peak=0.03, ms=300, wet=0.14)
def _think(rng, v):
    out = np.zeros((_n(0.45), 2))
    notes = (2093.0, 2349.3, 2637.0, 3136.0, 3520.0)  # C7 pentatonic
    for at in np.sort(rng.uniform(0.0, 0.17, 6)):
        grain = _tone(0.3, notes[rng.integers(len(notes))] * _jit(rng, 0.003),
                      ((1.0, 1.0, 1.0), (2.0, 0.1, 0.4)), attack=0.004, tau=0.05)
        _place(out, _pan(grain, rng.uniform(-0.4, 0.4)), float(at), rng.uniform(0.5, 1.0))
    t = _t(0.32)
    air = _sweep(0.32, rng, 5000.0, 0.6) * _bump(t, 0.12, 0.07)
    _place(out, _pan(air, 0.0), 0.0, 0.35)
    return out


@_sound("note", peak=0.16, ms=200, wet=0.08)
def _note(rng, v):
    out = np.zeros(_n(0.32))
    t = _t(0.06)
    slide_in = _sweep(0.06, rng, 3200.0, 1.0) * _flutter(0.06, rng, 150.0, 0.5) * _bump(t, 0.025, 0.012)
    _place(out, slide_in, 0.0, 0.25)
    t = _t(0.12)
    thud = 0.6 * _noise(0.12, rng, hp=150.0, lp=900.0) * _env(t, 0.0015, 0.012)
    thud += _tone(0.12, lambda t: 210.0 * (1 + 0.35 * np.exp(-t / 0.006)),
                  ((1.0, 1.0, 1.0), (2.0, 0.2, 0.5)), attack=0.0015, tau=0.022)
    at = (0.035, 0.032, 0.038)[v]
    _place(out, thud, at, 1.0)
    t = _t(0.26)
    settle = 0.35 * _crackle(0.26, rng, 250.0, 4000.0, 0.8) + 0.2 * _sweep(0.26, rng, 2500.0, 1.1)
    _place(out, settle * _env(t, 0.006, 0.08), at + 0.01, 1.0)
    return out


@_sound("clear", peak=0.14, ms=250, wet=0.09)
def _clear(rng, v):
    dur = 0.36
    t = _t(dur)
    env = _bump(t, 0.09, 0.05)
    swipe = _sweep(dur, rng, lambda t: _glide(t, 900.0, 4200.0, 0.22),
                   lambda t: 0.9 - 0.3 * np.clip(t / 0.22, 0.0, 1.0))
    swipe *= _flutter(dur, rng, 60.0, 0.55) * env
    grit = _crackle(dur, rng, 300.0, 5000.0, 0.6) * env
    return _pan(swipe + 0.2 * grit, np.linspace(0.0, 0.35, t.size))


@_sound("build", peak=0.18, ms=300, wet=0.12)
def _build(rng, v):
    out = np.zeros((_n(0.42), 2))
    step = (0.055, 0.05, 0.06)[v]
    for k, (f, level) in enumerate(((784.0, 0.7), (1046.5, 0.85), (1568.0, 1.0))):
        blip = _tone(0.2, lambda t, f=f: f * (1 - 0.1 * np.exp(-t / 0.006)),
                     ((1.0, 1.0, 1.0), (2.0, 0.15, 0.5), (3.0, 0.04, 0.3)),
                     attack=0.0015, tau=0.022, fm=0.5, fm_ratio=1.5)
        _place(out, _pan(blip, (k - 1) * 0.12), k * step, level)
    shimmer = (2637.0, 3136.0, 3520.0, 4186.0, 4699.0)
    for at in np.sort(rng.uniform(2 * step + 0.01, 2 * step + 0.11, 5)):
        grain = _tone(0.2, shimmer[rng.integers(len(shimmer))], _PURE, attack=0.003, tau=0.04)
        _place(out, _pan(grain, rng.uniform(-0.5, 0.5)), float(at), 0.3 * rng.uniform(0.6, 1.0))
    t = _t(0.3)
    air = _sweep(0.3, rng, 6000.0, 0.5) * _bump(t, 0.18, 0.05)
    _place(out, _pan(air, 0.0), 0.0, 0.12)
    return out


@_sound("point", peak=0.17, ms=500, wet=0.13)
def _point(rng, v):
    out = np.zeros((_n(0.7), 2))
    t = _t(0.16)
    swoosh = _sweep(0.16, rng, lambda t: _glide(t, 700.0, 2600.0, 0.12), 0.8) * _bump(t, 0.07, 0.03)
    _place(out, _pan(swoosh, np.linspace(-0.2, 0.1, t.size)), 0.0, 0.55)
    _place(out, _bell(0.6, 1318.5 * _jit(rng, 0.005), 0.115, "glass", fm=0.25), 0.10 + 0.006 * v, 1.0)
    return out


@_sound("done", peak=0.19, ms=450, wet=0.13)
def _done(rng, v):
    out = np.zeros((_n(0.65), 2))
    step = (0.045, 0.042, 0.048)[v]
    j = _jit(rng, 0.004)
    notes = ((1046.5, 0.06, 0.65), (1318.5, 0.06, 0.72), (1568.0, 0.07, 0.8), (2093.0, 0.10, 1.0))
    for k, (f, tau, level) in enumerate(notes):
        _place(out, _bell(0.5, f * j, tau, "glass", attack=0.002, fm=0.2), k * step, level)
    t = _t(0.25)
    sparkle = _crackle(0.25, rng, 120.0, 6500.0, 0.3) * _env(t, 0.01, 0.06)
    _place(out, _pan(sparkle, 0.0), 3 * step, 0.12)
    return out


@_sound("success", peak=0.2, ms=500, wet=0.13)
def _success(rng, v):
    out = np.zeros((_n(0.7), 2))
    second = (0.115, 0.11, 0.12)[v]
    j = _jit(rng, 0.004)
    for k, f in enumerate((523.25, 659.25, 784.0)):                    # ta: C5 E5 G5
        _place(out, _bell(0.3, f * j, 0.045, "warm", attack=0.003), 0.007 * k, 0.6)
    for k, f in enumerate((659.25, 784.0, 1046.5, 1318.5)):            # da: E5 G5 C6 E6
        level = 0.45 if k == 3 else 0.6
        _place(out, _bell(0.55, f * j, 0.105, "warm", attack=0.003), second + 0.007 * k, level)
    return out


@_sound("error", peak=0.16, ms=220, wet=0.08)
def _error(rng, v):
    nope = ((1.0, 1.0, 1.0), (2.0, 0.25, 0.7), (3.0, 0.12, 0.5), (4.0, 0.04, 0.35))
    j = _jit(rng, 0.008)
    out = np.zeros(_n(0.34))
    first = _tone(0.2, lambda t: 392.0 * j * (1 + 0.02 * np.exp(-t / 0.01)), nope, 0.006, 0.032)
    second = _tone(0.25, lambda t: 311.1 * j * (1 + 0.015 * np.exp(-t / 0.01)), nope, 0.006, 0.036)
    gap = (0.095, 0.09, 0.1)[v]
    _place(out, first, 0.0, 1.0)
    _place(out, second, gap, 0.95)
    return _filter(out, lp=3000.0)


@_sound("send", peak=0.15, ms=180, wet=0.09)
def _send(rng, v):
    dur = 0.26
    t = _t(dur)
    env = _bump(t, 0.085, 0.035)
    swish = _sweep(dur, rng, lambda t: _glide(t, 600.0, 5200.0, 0.15, 1.2), 0.75) * env
    phase = _TWO_PI * np.cumsum(_glide(t, 320.0, 1280.0, 0.14) * _jit(rng, 0.01)) / SAMPLE_RATE
    swoop = (np.sin(phase) + 0.15 * np.sin(2 * phase)) * _bump(t, 0.07, 0.03)
    return _pan(swish + 0.35 * swoop, np.linspace(-0.1, 0.25, t.size))


@_sound("slide", peak=0.12, ms=150, wet=0.08, variants=4)
def _slide(rng, v):
    dur = 0.22
    t = _t(dur)
    out = np.zeros(t.size)
    swish = _sweep(dur, rng, lambda t: _glide(t, 1600.0, 2600.0, 0.1), 0.9)
    out += swish * _flutter(dur, rng, 90.0, 0.3) * _bump(t, 0.05, 0.025)
    stop = (0.095, 0.09, 0.1, 0.098)[v]
    _place(out, _click(0.03, 2600.0 * _jit(rng, 0.03), 0.6, rng), stop, 0.35)
    flap = _noise(0.03, rng, lp=1500.0, hp=200.0) * _env(_t(0.03), 0.001, 0.008)
    _place(out, flap, stop, 0.3)
    return _pan(out, np.linspace(-0.25, 0.2, t.size))


@_sound("buddy", peak=0.17, ms=250, wet=0.09)
def _buddy(rng, v):
    base = (440.0, 466.2, 415.3)[v]

    def pitch(t):
        rise = base * (1 + 0.55 * (1 - np.exp(-t / 0.025)))
        return rise * (1 + 0.07 * np.exp(-t / 0.07) * np.sin(_TWO_PI * 17.0 * t))

    boing = _tone(0.32, pitch, ((1.0, 1.0, 1.0), (2.0, 0.3, 0.7), (3.0, 0.1, 0.5)),
                  attack=0.003, tau=0.07)
    t = _t(0.32)
    pluck = _noise(0.32, rng, band=(3000.0, 0.6)) * _env(t, 0.0003, 0.0015)
    return boing + 0.1 * pluck


@_sound("hello", peak=0.2, ms=600, wet=0.13)
def _hello(rng, v):
    out = np.zeros((_n(0.8), 2))
    j = _jit(rng, 0.004)
    step = (0.11, 0.105, 0.115)[v]
    for k, (f, tau, level) in enumerate(((659.25, 0.08, 0.75), (784.0, 0.08, 0.82), (1046.5, 0.11, 1.0))):
        _place(out, _bell(0.6, f * j, tau, "warm", attack=0.005, fm=0.35), k * step, level)
    return out


_FIRST = ("listen_start", "hello", "hover", "press", "listen_end", "open", "close", "think")
_ORDER = _FIRST + tuple(name for name in NAMES if name not in _FIRST)
assert set(_SPECS) == set(NAMES), "every sound needs a description in NAMES"


# ── Measuring (for the tests and tools/dev/sfx_export.py) ───────────────────

def measure(samples: np.ndarray) -> dict:
    """Peak, loudness, audible length and brightness of rendered samples.

    ``dur_ms`` runs from the first to the last moment within 30 dB of the
    peak (2 ms resolution); ``rms_db`` is the RMS over that stretch;
    ``centroid_hz`` is the power-weighted mean frequency; ``low``/``high``
    are the shares of energy below 120 Hz and above 9 kHz."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    level = np.max(np.abs(x), axis=1)
    peak = float(level.max())
    w = _n(0.002)
    blocks = level[: level.size // w * w].reshape(-1, w).max(axis=1)
    loud = np.flatnonzero(blocks >= peak * 10 ** (-30 / 20))
    first, last = int(loud[0]) * w, (int(loud[-1]) + 1) * w
    mono = x.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono[first:last] ** 2)))
    power = np.abs(np.fft.rfft(mono)) ** 2
    f = np.fft.rfftfreq(mono.size, 1.0 / SAMPLE_RATE)
    total = float(power.sum()) + 1e-30
    return {
        "peak": peak,
        "peak_db": 20 * np.log10(peak + 1e-12),
        "rms_db": 20 * np.log10(rms + 1e-12),
        "dur_ms": (last - first) * 1000.0 / SAMPLE_RATE,
        "len_ms": x.shape[0] * 1000.0 / SAMPLE_RATE,
        "centroid_hz": float((f * power).sum() / total),
        "low": float(power[f < 120.0].sum() / total),
        "high": float(power[f > 9000.0].sum() / total),
    }


def target_ms(name: str) -> float:
    """How long a sound is meant to last, in milliseconds."""
    return _SPECS[name].ms
