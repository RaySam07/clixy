"""Tests for the self-healing microphone, with a fake sound system."""
from __future__ import annotations

import numpy as np
import pytest


class _Stream:
    def __init__(self, sd, samplerate, blocksize, device, callback, **_kw):
        if sd.refuse_rates and samplerate in sd.refuse_rates:
            raise OSError("Invalid sample rate")
        if device in sd.broken_devices:
            raise OSError("device unavailable")
        self.samplerate, self.blocksize, self.device, self.callback = samplerate, blocksize, device, callback
        self.active = False
        self.closed = False
        sd.streams.append(self)

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True

    def feed(self, samples):
        self.callback(np.asarray(samples, dtype=np.int16).tobytes(), len(samples), None, None)


class _Default:
    def __init__(self):
        self.device = [0, 1]
        self.hostapi = 0


class FakeSD:
    def __init__(self, devices=None, native=48_000):
        self.devices = devices if devices is not None else [
            {"name": "Laptop Mic", "max_input_channels": 2, "hostapi": 0, "default_samplerate": native},
            {"name": "Speakers", "max_input_channels": 0, "hostapi": 0, "default_samplerate": 48_000},
            {"name": "USB Headset Mic", "max_input_channels": 1, "hostapi": 0, "default_samplerate": native},
            {"name": "USB Headset Mic", "max_input_channels": 1, "hostapi": 1, "default_samplerate": native},
        ]
        self.default = _Default()
        if not any(d["max_input_channels"] for d in self.devices):
            self.default.device = [-1, 1]
        self.streams: list[_Stream] = []
        self.refuse_rates: set[int] = set()
        self.broken_devices: set = set()

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return self.devices
        if device is None:
            index = self.default.device[0]
            if index < 0:
                raise OSError("no default input")
            return self.devices[index]
        return self.devices[device]

    def RawInputStream(self, **kw):  # noqa: N802 (sounddevice's name)
        return _Stream(self, **kw)


def _mic(sd, got=None, **kw):
    import mic
    got = got if got is not None else []
    m = mic.Microphone(lambda data, frames, t, s: got.append(data), sd=sd, **kw)
    return m, got


def test_opens_the_default_microphone_at_16khz():
    sd = FakeSD()
    m, got = _mic(sd)
    m.start()
    assert m.problem is None and sd.streams[-1].samplerate == 16_000 and sd.streams[-1].device is None
    sd.streams[-1].feed([100] * 1024)
    assert len(got) == 1 and len(got[0]) == 2048


def test_falls_back_to_the_devices_own_rate_and_converts_to_16khz():
    sd = FakeSD()
    sd.refuse_rates = {16_000}
    m, got = _mic(sd)
    m.start()
    stream = sd.streams[-1]
    assert stream.samplerate == 48_000 and stream.blocksize == 3072
    stream.feed([1000] * 3072)
    assert len(got[0]) == 1024 * 2  # 1024 samples of 16-bit audio


def test_resample_handles_odd_rates():
    import mic
    out = mic.resample(np.full(4410, 500, dtype=np.int16), 44_100, 16_000)
    assert out.size == 1600 and int(out.mean()) == 500


def test_the_microphone_picked_in_settings_comes_first():
    sd = FakeSD()
    m, _ = _mic(sd, device_name="USB Headset Mic")
    m.start()
    assert sd.streams[-1].device == 2  # the one on the default host API


def test_a_broken_choice_tries_the_same_mic_another_way_then_any_that_works():
    sd = FakeSD()
    sd.broken_devices = {2, None}
    m, _ = _mic(sd, device_name="USB Headset Mic")
    m.start()
    assert m.problem is None and sd.streams[-1].device == 3   # the headset through another driver
    sd = FakeSD()
    sd.broken_devices = {2, 3, None}
    m, _ = _mic(sd, device_name="USB Headset Mic")
    m.start()
    assert m.problem is None and sd.streams[-1].device == 0   # the laptop mic


def test_no_microphone_is_reported_and_never_raises():
    import mic
    sd = FakeSD(devices=[{"name": "Speakers", "max_input_channels": 0, "hostapi": 0, "default_samplerate": 48_000}])
    m, _ = _mic(sd)
    m.start()
    assert m.problem == mic.MISSING
    assert "find a microphone" in mic.problem_message(m.problem)


def test_a_microphone_that_wont_open_is_reported():
    import mic
    sd = FakeSD()
    sd.broken_devices = {0, 2, 3, None}
    m, _ = _mic(sd)
    m.start()
    assert m.problem == mic.FAILED


def test_windows_privacy_silence_is_recognised():
    import mic
    sd = FakeSD()
    m, _ = _mic(sd)
    m.start()
    for _ in range(14):           # ~0.9 s of exact zeros
        sd.streams[-1].feed([0] * 1024)
    m.check_silence()
    assert m.problem == mic.BLOCKED
    assert "Privacy" in mic.problem_message(m.problem)
    sd.streams[-1].feed([3, -2, 5] * 300)  # real sound clears it
    m.check_silence()
    assert m.problem is None


def test_quiet_room_noise_is_not_mistaken_for_blocked():
    sd = FakeSD()
    m, _ = _mic(sd)
    m.start()
    rng = np.random.default_rng(1)
    for _ in range(30):
        sd.streams[-1].feed(rng.integers(-3, 4, 1024))
    m.check_silence()
    assert m.problem is None


def test_a_dead_stream_is_reopened_on_the_next_press(monkeypatch):
    import mic
    clock = [100.0]
    monkeypatch.setattr(mic.time, "monotonic", lambda: clock[0])
    sd = FakeSD()
    m, _ = _mic(sd)
    m.start()
    first = sd.streams[-1]
    clock[0] += 0.5
    assert m.ensure_alive() and sd.streams[-1] is first      # still fresh
    clock[0] += 5.0                                          # no audio since: unplugged, slept
    assert m.ensure_alive()
    assert sd.streams[-1] is not first and first.closed


def test_a_stream_that_was_stopped_is_reopened():
    sd = FakeSD()
    m, _ = _mic(sd)
    m.start()
    sd.streams[-1].active = False
    m.ensure_alive()
    assert len(sd.streams) == 2 and sd.streams[-1].active


def test_switching_microphones():
    sd = FakeSD()
    m, _ = _mic(sd)
    m.start()
    m.set_device("USB Headset Mic")
    assert sd.streams[0].closed and sd.streams[-1].device == 2


def test_input_devices_lists_each_microphone_once():
    import mic
    assert mic.input_devices(FakeSD()) == ["Laptop Mic", "USB Headset Mic"]


# --- Listening uses it --------------------------------------------------------------------

def test_listening_reports_a_blocked_microphone(mocker):
    import mic
    from stt import FasterWhisperSTT
    sd = FakeSD()
    holder = {}

    def factory(cb):
        holder["mic"] = mic.Microphone(cb, sd=sd)
        return holder["mic"]

    stt = FasterWhisperSTT(model_factory=lambda: mocker.MagicMock(), audio_stream_factory=factory)
    stt.connect()
    stt.start_recording()
    for _ in range(14):
        sd.streams[-1].feed([0] * 1024)
    assert stt.mic_problem() == mic.BLOCKED


def test_listening_reopens_a_dead_microphone_on_press(mocker, monkeypatch):
    import mic
    from stt import FasterWhisperSTT
    clock = [100.0]
    monkeypatch.setattr(mic.time, "monotonic", lambda: clock[0])
    sd = FakeSD()
    stt = FasterWhisperSTT(model_factory=lambda: mocker.MagicMock(),
                           audio_stream_factory=lambda cb: mic.Microphone(cb, sd=sd))
    stt.connect()
    clock[0] += 10
    stt.start_recording()
    assert len(sd.streams) == 2


def test_a_failed_model_download_says_what_to_do(mocker):
    from stt import FasterWhisperSTT, ListeningUnavailable

    def broken():
        raise ConnectionError("no internet")

    stt = FasterWhisperSTT(model_factory=broken, audio_stream_factory=lambda cb: mocker.MagicMock())
    stt.connect()
    stt.start_recording()
    stt._on_audio(b"\x01\x00" * 1024, 1024, None, None)
    with pytest.raises(ListeningUnavailable, match="Cloud"):
        stt.stop_recording()
    from app import _friendly_error
    try:
        stt.start_recording()
        stt._on_audio(b"\x01\x00" * 1024, 1024, None, None)
        stt.stop_recording()
    except ListeningUnavailable as exc:
        assert "internet" in _friendly_error("speech-to-text", exc)


def test_the_app_starts_before_the_model_finishes_loading(mocker):
    import threading
    from stt import FasterWhisperSTT
    gate = threading.Event()

    def slow_model():
        gate.wait(5)
        return mocker.MagicMock()

    stt = FasterWhisperSTT(model_factory=slow_model, audio_stream_factory=lambda cb: mocker.MagicMock())
    stt.connect()                 # returns at once
    assert stt.loading is True
    gate.set()
    stt._model_ready.wait(2)
    assert stt.loading is False


def test_the_buddy_explains_a_microphone_problem_instead_of_didnt_catch_that(qapp, mocker):
    import queue as _queue
    import threading
    import mic
    from PIL import Image
    from app import ClickyApp
    app = ClickyApp(
        ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(), tts_client=mocker.MagicMock(),
        memory_store=mocker.MagicMock(), overlay_controller=mocker.MagicMock(), hotkey_instance=mocker.MagicMock(),
    )
    app._stt.stop_recording.return_value = ""
    app._stt.loading = False
    app._stt.mic_problem.return_value = mic.BLOCKED
    notes = []
    app.sig_island_note.connect(notes.append)
    cap = mocker.MagicMock()
    cap.image = Image.new("RGB", (64, 64))
    q = _queue.Queue(maxsize=1)
    q.put(([cap], "", "test"))
    app._press_captures = [cap]
    app._pipeline_worker("X.EXE", "x", threading.Event(), q)
    assert notes and "Privacy" in notes[-1]


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
