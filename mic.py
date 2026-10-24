"""The microphone, kept working.

Clicky opens the microphone once and keeps it open, so a question starts
the instant you press the keys. Laptops change under an open stream, so
this class:

- opens the microphone you picked in Settings, else Windows' default,
  else the first one that opens;
- asks for 16 kHz, and when a device can't do that, records at its own
  rate and converts to 16 kHz;
- notices a stream that has gone quiet (a headset unplugged, earbuds
  connected, the PC woke from sleep) and reopens it on your next press;
- notices pure digital silence, which is what Windows sends when
  microphone access is off in Privacy settings or the mic is muted, so
  the buddy can say so instead of "I didn't catch that".

A missing microphone never stops Clicky from starting: it records the
problem and tries again on the next press.

Threading: callbacks arrive on PortAudio's thread; everything else is
called from the Qt main thread or the pipeline worker.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

STALE_S = 1.2
"""A running stream with no audio for this long is dead; reopen it."""

SILENT_S = 0.8
"""This much exact digital silence means Windows is blocking the mic."""

PRIVACY_SETTINGS_URI = "ms-settings:privacy-microphone"

# Problems, as (code, what the buddy says).
BLOCKED = "blocked"
MISSING = "missing"
FAILED = "failed"


def problem_message(problem: str | None, keys: str = "the keys") -> str:
    """What the buddy says about a microphone problem, in plain words."""
    if problem == BLOCKED:
        return ("I can't hear you: your microphone is muted or Windows is blocking it. "
                "Turn on Settings > Privacy & security > Microphone > Let desktop apps access your microphone.")
    if problem == MISSING:
        return "I can't find a microphone. Plug one in, or pick one in Clixy's Settings > General."
    if problem == FAILED:
        return "Your microphone wouldn't open. Pick another one in Clixy's Settings > General."
    return f"I didn't catch that. Hold {keys} and try again?"


def input_devices(sd=None) -> list[str]:
    """Names of the microphones Windows offers (default host API), for the
    Settings picker. Empty if audio isn't available."""
    try:
        sd = sd or _sounddevice()
        host = _default_hostapi(sd)
        names = []
        for dev in sd.query_devices():
            if dev["max_input_channels"] > 0 and (host is None or dev["hostapi"] == host):
                if dev["name"] not in names:
                    names.append(dev["name"])
        return names
    except Exception:
        return []


def _sounddevice():
    import sounddevice
    return sounddevice


def _default_hostapi(sd) -> int | None:
    try:
        return int(sd.default.hostapi)
    except Exception:
        return None


def resample(samples: np.ndarray, rate: int, target: int) -> np.ndarray:
    """int16 samples at ``rate`` to ``target`` Hz. Whole-number ratios are
    averaged (a cheap low-pass); others are interpolated."""
    if rate == target or samples.size == 0:
        return samples
    ratio = rate / target
    if abs(ratio - round(ratio)) < 1e-6 and ratio >= 2:
        n = int(round(ratio))
        usable = samples.size - samples.size % n
        return samples[:usable].reshape(-1, n).astype(np.int32).mean(axis=1).astype(np.int16)
    out_n = int(round(samples.size * target / rate))
    x_old = np.arange(samples.size)
    x_new = np.linspace(0, samples.size - 1, out_n)
    return np.interp(x_new, x_old, samples.astype(np.float32)).astype(np.int16)


class Microphone:
    """A self-healing input stream. Drop-in for a sounddevice stream: has
    start(), stop(), close() and ``active``; calls ``callback(bytes, frames,
    time_info, status)`` with 16-bit mono PCM at ``samplerate``."""

    def __init__(self, callback: Callable, samplerate: int = 16_000, blocksize: int = 1024,
                 device_name: str | None = None, sd=None) -> None:
        self._callback = callback
        self._rate = samplerate
        self._blocksize = blocksize
        self.device_name = (device_name or "").strip()
        self._sd = sd
        self._stream = None
        self._native_rate = samplerate
        self._lock = threading.Lock()
        self._opened_at = 0.0
        self._last_audio = 0.0
        self._zero_frames = 0
        self.level = 0.0
        """Recent loudness, 0-1, for meters."""
        self.problem: str | None = None
        """None, BLOCKED, MISSING or FAILED."""
        self.device_label = ""
        """The microphone actually in use."""
        self.error = ""

    # --- sounddevice-stream surface ------------------------------------------------

    @property
    def active(self) -> bool:
        return self._stream is not None and bool(getattr(self._stream, "active", True))

    def start(self) -> None:
        """Open the microphone. Never raises: a problem is kept in
        ``problem`` and the next ensure_alive() tries again."""
        with self._lock:
            self._open()

    def stop(self) -> None:
        with self._lock:
            self._close()

    def close(self) -> None:
        self.stop()

    # --- Health ------------------------------------------------------------------------

    def ensure_alive(self) -> bool:
        """Called on each press: reopen a stream that is missing, stopped or
        has gone quiet. True when the microphone is delivering audio (or
        has only just opened)."""
        with self._lock:
            now = time.monotonic()
            stale = (self._stream is not None and now - self._opened_at > STALE_S
                     and now - self._last_audio > STALE_S)
            if self._stream is None or not self.active or stale:
                self._close()
                self._open()
            return self._stream is not None

    @property
    def silent(self) -> bool:
        """Exact digital silence for a while: muted or blocked by Windows."""
        return self._zero_frames >= SILENT_S * self._rate

    def check_silence(self) -> None:
        """Turn a long run of exact silence into the BLOCKED problem (or
        clear it once real sound arrives)."""
        if self.silent:
            self.problem = BLOCKED
        elif self.problem == BLOCKED:
            self.problem = None

    def set_device(self, name: str | None) -> None:
        """Switch microphones ('' for Windows' default)."""
        with self._lock:
            self.device_name = (name or "").strip()
            self._close()
            self._open()

    # --- Internals ------------------------------------------------------------------

    def _candidates(self, sd) -> list[int | None]:
        """Devices to try, in order: the one picked by name, the default
        (None), then every other microphone on the default host API."""
        order: list[int | None] = []
        host = _default_hostapi(sd)
        devices = list(enumerate(sd.query_devices()))
        inputs = [(i, d) for i, d in devices if d["max_input_channels"] > 0]
        if self.device_name:
            for i, d in inputs:
                if d["name"] == self.device_name and (host is None or d["hostapi"] == host):
                    order.append(i)
            for i, d in inputs:
                if self.device_name.lower() in d["name"].lower() and i not in order:
                    order.append(i)
        order.append(None)
        for i, d in inputs:
            if i not in order and (host is None or d["hostapi"] == host):
                order.append(i)
        return order

    def _open(self) -> None:
        try:
            sd = self._sd or _sounddevice()
        except Exception as exc:
            self.problem, self.error = FAILED, f"audio unavailable: {exc}"
            return
        try:
            candidates = self._candidates(sd)
            has_inputs = any(c is not None for c in candidates) or _default_input_exists(sd)
        except Exception as exc:
            self.problem, self.error = FAILED, str(exc)
            return
        if not has_inputs:
            self.problem, self.error = MISSING, "no microphone"
            return
        last_error = ""
        for device in candidates:
            for rate in self._rates(sd, device):
                try:
                    blocksize = max(64, round(self._blocksize * rate / self._rate))
                    stream = sd.RawInputStream(samplerate=rate, blocksize=blocksize, dtype="int16",
                                               channels=1, device=device, callback=self._on_audio)
                    self._native_rate = rate
                    stream.start()
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue
                self._stream = stream
                self._opened_at = self._last_audio = time.monotonic()
                self._zero_frames = 0
                self.problem, self.error = None, ""
                self.device_label = _device_name(sd, device)
                return
        self.problem, self.error = FAILED, last_error or "could not open"

    def _rates(self, sd, device) -> list[int]:
        rates = [self._rate]
        try:
            native = int(sd.query_devices(device, "input")["default_samplerate"])
            if native and native != self._rate:
                rates.append(native)
        except Exception:
            pass
        for common in (48_000, 44_100):
            if common not in rates:
                rates.append(common)
        return rates

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """PortAudio's thread: convert to 16 kHz if needed, watch for
        silence, update the level, pass the audio on. Must not raise."""
        try:
            samples = np.frombuffer(bytes(indata), dtype=np.int16)
            if self._native_rate != self._rate:
                samples = resample(samples, self._native_rate, self._rate)
            self._last_audio = time.monotonic()
            if samples.size:
                if np.any(samples):
                    self._zero_frames = 0
                else:
                    self._zero_frames += samples.size
                rms = float(np.sqrt(np.mean((samples.astype(np.float32) / 32768.0) ** 2)))
                self.level = max(min(1.0, rms * 10.2), self.level * 0.8)
            self._callback(samples.tobytes(), samples.size, time_info, status)
        except Exception:
            pass


def _default_input_exists(sd) -> bool:
    try:
        default = sd.default.device[0]
        return default is not None and int(default) >= 0
    except Exception:
        return False


def _device_name(sd, device) -> str:
    try:
        return str(sd.query_devices(device, "input")["name"])
    except Exception:
        return "microphone"
