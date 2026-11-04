"""Tests for sfx.py, the interface sounds: every sound renders clean and at
the right level and length, play() is cheap and safe, and the mixer mixes.
No test opens a real audio device: the stream is a fake that the test pulls
blocks from, as PortAudio would."""
from __future__ import annotations

import time

import numpy as np
import pytest

import sfx

NAMES = sorted(sfx.NAMES)
QUIET = ("hover", "type", "think")


# ── Helpers ─────────────────────────────────────────────────────────────────

class FakeStream:
    """Stands in for a sounddevice.OutputStream: holds the callback."""

    def __init__(self, callback, finished):
        self.callback = callback
        self.finished = finished
        self.closed = False

    def pull(self, frames: int = sfx.BLOCKSIZE) -> np.ndarray:
        out = np.zeros((frames, 2), np.float32)
        self.callback(out, frames, None, None)
        return out

    def close(self):
        self.closed = True
        self.finished()


class FakeDevices:
    """Opens fake streams and remembers them."""

    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.delay = delay
        self.fail = fail
        self.opened: list[FakeStream] = []

    def open(self, callback, finished):
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise OSError("no device")
        stream = FakeStream(callback, finished)
        self.opened.append(stream)
        return stream


def _wait(cond, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        time.sleep(0.005)
    return cond()


def _settle() -> None:
    """Let any background open finish, so it cannot leak into the next test."""
    _wait(lambda: not sfx._opening)


@pytest.fixture(scope="module")
def rendered():
    """Every sound rendered once for this file (no disk cache), so no test
    starts a background render."""
    for name in sfx._ORDER:
        if name not in sfx._SOUNDS:
            sfx._SOUNDS[name] = [np.vstack([sfx.render(name, k), np.zeros((1, 2), np.float32)])
                                 for k in range(sfx.variants(name))]
    sfx._render_started = True
    return sfx._SOUNDS


@pytest.fixture
def engine(monkeypatch, rendered):
    """sfx with a clean slate, sounds on at the default volume, and a fake device."""
    _settle()
    sfx.shutdown()
    devices = FakeDevices()
    monkeypatch.setattr(sfx, "_open_device", devices.open)
    monkeypatch.setattr(sfx, "_enabled", True)
    monkeypatch.setattr(sfx, "_volume", 0.7)
    monkeypatch.setattr(sfx, "_master_gain", 1.0)
    monkeypatch.setattr(sfx, "_applied_gain", 1.0)
    monkeypatch.setattr(sfx, "_retry_at", 0.0)
    monkeypatch.setattr(sfx, "_stream_failed", False)
    monkeypatch.setattr(sfx, "_last_played", {})
    monkeypatch.setattr(sfx, "_last_variant", {})
    yield devices
    _settle()
    sfx.shutdown()


@pytest.fixture
def clock(monkeypatch):
    """sfx's clock, moved by hand."""
    now = [1000.0]
    monkeypatch.setattr(sfx, "_now", lambda: now[0])
    return now


def _queued() -> list[str]:
    return [v.name for v in sfx._pending]


# ── The sounds themselves ───────────────────────────────────────────────────

def test_every_sound_has_a_description_and_a_few_variants():
    assert set(sfx.NAMES) == set(sfx._SPECS)
    for name, text in sfx.NAMES.items():
        assert text.strip(), name
        assert 2 <= sfx.variants(name) <= 4, name
    assert sfx.variants("nope") == 0


@pytest.mark.parametrize("name", NAMES)
def test_every_variant_renders_as_stereo_float32(name, rendered):
    for k in range(sfx.variants(name)):
        x = sfx.render(name, k)
        assert x.dtype == np.float32 and x.ndim == 2 and x.shape[1] == 2
        assert x.shape[0] > sfx.SAMPLE_RATE * 0.01
        assert np.isfinite(x).all()
    assert np.array_equal(sfx.render(name, 0), rendered[name][0][:-1]), "renders are repeatable"


@pytest.mark.parametrize("name", NAMES)
def test_variants_differ(name, rendered):
    takes = rendered[name]
    for a, b in zip(takes, takes[1:]):
        n = min(len(a), len(b))
        assert not np.allclose(a[:n], b[:n], atol=1e-4)


@pytest.mark.parametrize("name", NAMES)
def test_no_clicks_at_the_edges(name, rendered):
    for x in rendered[name]:
        x = x[:-1].astype(np.float64)
        assert np.abs(x[0]).max() < 1e-6 and np.abs(x[-1]).max() < 1e-6
        jumps = np.abs(np.diff(x, axis=0))
        assert jumps[:48].max() < 0.002, "the first millisecond rises gently"
        assert jumps[-96:].max() < 0.002, "the last two milliseconds fade out"


@pytest.mark.parametrize("name", NAMES)
def test_nothing_piercing_and_no_rumble(name, rendered):
    for x in rendered[name]:
        m = sfx.measure(x[:-1])
        assert m["low"] < 0.005, "almost nothing below 120 Hz"
        assert m["high"] < 0.01, "almost nothing above 9 kHz"
        mono = x[:-1].mean(axis=1).astype(np.float64)
        power = np.abs(np.fft.rfft(mono)) ** 2
        f = np.fft.rfftfreq(mono.size, 1.0 / sfx.SAMPLE_RATE)
        assert power[f > 12_000].sum() / power.sum() < 1e-3, "no broadband clicks anywhere"


@pytest.mark.parametrize("name", NAMES)
def test_levels(name, rendered):
    target = sfx._SPECS[name].peak
    for x in rendered[name]:
        peak = float(np.abs(x).max())
        assert peak <= 0.3
        assert abs(20 * np.log10(peak / target)) < 0.5
    if name in QUIET:
        assert target <= 0.035, "small sounds sit around -30 dBFS"
    else:
        assert 0.09 <= target <= 0.21, "the rest peak between -21 and -14 dBFS"


@pytest.mark.parametrize("name", NAMES)
def test_durations_match_the_intent(name, rendered):
    target = sfx.target_ms(name)
    for k in range(sfx.variants(name)):
        body = sfx.measure(sfx.render(name, k, room=False))["dur_ms"]
        assert 0.7 * target <= body <= 1.3 * target, f"{name}-{k}: {body:.0f} ms vs {target:.0f}"
        ring = sfx.measure(rendered[name][k][:-1])["dur_ms"]
        assert ring <= 1.3 * target + 70, "the room only adds a short tail"


def test_the_numbers_match_the_intent(rendered):
    stats = {name: sfx.measure(rendered[name][0][:-1]) for name in NAMES}
    assert stats["close"]["centroid_hz"] < stats["open"]["centroid_hz"]
    quietest = sorted(NAMES, key=lambda n: stats[n]["rms_db"])[:3]
    assert set(quietest) == set(QUIET)
    assert stats["hover"]["peak"] < stats["press"]["peak"] < stats["success"]["peak"]
    assert stats["note"]["centroid_hz"] < stats["page"]["centroid_hz"], "a thud, not a swish"


def _pitch(x: np.ndarray, start: float, end: float) -> float:
    seg = x[int(start * sfx.SAMPLE_RATE):int(end * sfx.SAMPLE_RATE)].mean(axis=1)
    power = np.abs(np.fft.rfft(seg * np.hanning(seg.size), 16384)) ** 2
    return float(np.fft.rfftfreq(16384, 1.0 / sfx.SAMPLE_RATE)[power.argmax()])


def test_pitches_move_the_right_way(rendered):
    start = rendered["listen_start"][0]
    assert abs(_pitch(start, 0.008, 0.04) - 880) < 40 and abs(_pitch(start, 0.07, 0.11) - 1318.5) < 40
    end = rendered["listen_end"][0]
    assert _pitch(end, 0.003, 0.02) > _pitch(end, 0.06, 0.1)
    error = rendered["error"][0]
    assert _pitch(error, 0.01, 0.08) > _pitch(error, 0.12, 0.2) > 200, "low and falling, never rumbling"
    done = rendered["done"][0]
    assert abs(_pitch(done, 0.15, 0.3) - 2093) < 60, "ends on C7"


def test_whooshes_travel(rendered):
    def centroid(x, a, b):
        seg = x[int(a * sfx.SAMPLE_RATE):int(b * sfx.SAMPLE_RATE)].mean(axis=1)
        power = np.abs(np.fft.rfft(seg)) ** 2
        f = np.fft.rfftfreq(seg.size, 1.0 / sfx.SAMPLE_RATE)
        return float((f * power).sum() / power.sum())

    send = rendered["send"][0]
    assert centroid(send, 0.0, 0.04) < centroid(send, 0.1, 0.15), "send swoops up"
    close = rendered["close"][0]
    assert centroid(close, 0.0, 0.04) > centroid(close, 0.1, 0.14), "close falls"


def test_the_chimes_stay_whole_in_mono(rendered):
    for name in ("hello", "done", "success", "point", "open"):
        x = rendered[name][0].astype(np.float64)
        assert np.corrcoef(x[:, 0], x[:, 1])[0, 1] > 0.6, name


def test_rendering_everything_is_quick_enough():
    t0 = time.perf_counter()
    for name in NAMES:
        for k in range(sfx.variants(name)):
            sfx.render(name, k)
    assert time.perf_counter() - t0 < 5.0  # ~0.7 s here; the cache covers later starts


# ── play() ──────────────────────────────────────────────────────────────────

def test_play_is_a_no_op_when_off(engine):
    sfx.set_enabled(False)
    sfx.play("press")
    assert _queued() == [] and not engine.opened
    assert sfx.enabled() is False


def test_play_ignores_unknown_names_and_bad_gains(engine):
    sfx.play("no-such-sound")
    sfx.play(None)  # type: ignore[arg-type]
    assert _queued() == []
    sfx.play("press", gain="loud")  # type: ignore[arg-type]
    assert _queued() == []


def test_play_returns_at_once_even_when_the_device_is_slow(engine, monkeypatch):
    slow = FakeDevices(delay=0.5)
    monkeypatch.setattr(sfx, "_open_device", slow.open)
    t0 = time.perf_counter()
    sfx.play("press")
    sfx.play("hover")
    assert time.perf_counter() - t0 < 0.05
    assert _wait(lambda: len(slow.opened) == 1)


def test_the_same_sound_is_rate_limited(engine, clock):
    sfx.play("hover")
    sfx.play("hover")
    assert _queued() == ["hover"]
    clock[0] += 0.03
    sfx.play("hover")
    assert _queued() == ["hover"], "hover at most every 50 ms"
    clock[0] += 0.025
    sfx.play("hover")
    assert _queued() == ["hover", "hover"]
    sfx.play("press")
    clock[0] += 0.02
    sfx.play("press")
    clock[0] += 0.015
    sfx.play("press")
    assert _queued().count("press") == 2, "others at most every 30 ms"


def test_variants_never_repeat_back_to_back_and_pitch_wobbles(engine, clock):
    picks = []
    for _ in range(40):
        clock[0] += 0.1
        sfx.play("select")
    picks = [v.variant for v in sfx._pending]
    rates = [v.rate for v in sfx._pending]
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert set(picks) == set(range(sfx.variants("select")))
    assert all(0.97 <= r <= 1.03 for r in rates) and max(rates) - min(rates) > 0.01


def test_a_flood_of_sounds_is_bounded(engine, clock, monkeypatch):
    monkeypatch.setattr(sfx, "_open_device", FakeDevices(fail=True).open)
    for _ in range(500):
        clock[0] += 0.1
        sfx.play("press")
    assert len(sfx._pending) <= sfx._MAX_QUEUED


# ── The mixer ───────────────────────────────────────────────────────────────

def _const(value: float, frames: int) -> list[np.ndarray]:
    data = np.full((frames + 1, 2), value, np.float32)
    data[-1] = 0.0
    return [data]


def _voice(name, rate=1.0, gain=1.0, deadline=float("inf")):
    return sfx._Voice(name, 0, rate, gain, deadline)


def test_the_mixer_mixes_two_voices(engine, monkeypatch):
    monkeypatch.setitem(sfx._SOUNDS, "t_a", _const(0.1, 2000))
    monkeypatch.setitem(sfx._SOUNDS, "t_b", _const(0.05, 2000))
    sfx._pending.extend([_voice("t_a"), _voice("t_b")])
    out = np.zeros((480, 2), np.float32)
    sfx._callback(out, 480, None, None)
    assert np.allclose(out, np.tanh(0.15), atol=1e-6)
    assert len(sfx._voices) == 2


def test_the_mixer_soft_clips(engine, monkeypatch):
    monkeypatch.setitem(sfx._SOUNDS, "t_a", _const(0.9, 2000))
    sfx._pending.extend([_voice("t_a"), _voice("t_a"), _voice("t_a")])
    out = np.zeros((480, 2), np.float32)
    sfx._callback(out, 480, None, None)
    assert np.all(out < 1.0) and np.allclose(out, np.tanh(2.7), atol=1e-6)


def test_a_voice_ends_and_frees_its_slot(engine, monkeypatch):
    monkeypatch.setitem(sfx._SOUNDS, "t_a", _const(0.1, 700))
    sfx._pending.append(_voice("t_a"))
    first, second = np.zeros((480, 2), np.float32), np.zeros((480, 2), np.float32)
    sfx._callback(first, 480, None, None)
    sfx._callback(second, 480, None, None)
    assert np.allclose(second[:220], np.tanh(0.1), atol=1e-6)
    assert np.all(second[221:] == 0.0)
    assert sfx._voices == []


def test_rate_and_gain_shape_a_voice(engine, monkeypatch):
    ramp = np.zeros((2001, 2), np.float32)
    ramp[:-1] = (np.arange(2000) / 2000.0)[:, None]
    monkeypatch.setitem(sfx._SOUNDS, "t_ramp", [ramp])
    sfx._pending.append(_voice("t_ramp", rate=2.0, gain=0.5))
    out = np.zeros((480, 2), np.float32)
    sfx._callback(out, 480, None, None)
    assert np.allclose(out[100, 0], np.tanh(0.5 * 200 / 2000), atol=1e-5), "twice as fast, half as loud"


def test_late_and_unrendered_voices(engine, monkeypatch, clock):
    monkeypatch.setitem(sfx._SOUNDS, "t_a", _const(0.1, 2000))
    sfx._pending.append(_voice("t_a", deadline=clock[0] - 0.01))   # too late: dropped
    sfx._pending.append(_voice("t_later", deadline=clock[0] + 1))  # not rendered yet: waits
    out = np.zeros((480, 2), np.float32)
    sfx._callback(out, 480, None, None)
    assert np.all(out == 0.0)
    assert _queued() == ["t_later"]
    monkeypatch.setitem(sfx._SOUNDS, "t_later", _const(0.1, 2000))
    sfx._callback(out, 480, None, None)
    assert np.allclose(out, np.tanh(0.1), atol=1e-6)


def test_the_mixer_caps_its_voices(engine, monkeypatch):
    monkeypatch.setitem(sfx._SOUNDS, "t_a", _const(0.001, 5000))
    sfx._pending.extend(_voice("t_a") for _ in range(40))
    sfx._callback(np.zeros((480, 2), np.float32), 480, None, None)
    assert len(sfx._voices) == sfx._MAX_VOICES


def test_the_callback_never_raises(engine, monkeypatch):
    monkeypatch.setitem(sfx._SOUNDS, "t_bad", [np.zeros((10, 3), np.float32)])
    sfx._pending.append(_voice("t_bad"))
    out = np.full((480, 2), 0.5, np.float32)
    sfx._callback(out, 480, None, None)
    assert np.all(out == 0.0)


def test_volume_is_clamped_tapered_and_ramped(engine, monkeypatch):
    sfx.set_volume(5)
    assert sfx.volume() == 1.0
    sfx.set_volume(-1)
    assert sfx.volume() == 0.0
    sfx.set_volume("loud")  # type: ignore[arg-type]
    sfx.set_volume(float("nan"))
    assert sfx.volume() == 0.0
    sfx.set_volume(0.7)
    assert sfx._master_gain == pytest.approx(1.0), "the default plays sounds as designed"
    monkeypatch.setitem(sfx._SOUNDS, "t_a", _const(0.1, 5000))
    sfx._pending.append(_voice("t_a"))
    out = np.zeros((480, 2), np.float32)
    sfx._callback(out, 480, None, None)
    sfx.set_volume(0.35)
    sfx._callback(out, 480, None, None)
    assert out[0, 0] == pytest.approx(np.tanh(0.1), rel=1e-3), "no step: the change ramps in"
    assert out[-1, 0] == pytest.approx(np.tanh(0.1 * 0.25), rel=2e-2)


# ── The device ──────────────────────────────────────────────────────────────

def test_one_stream_serves_every_sound_and_they_sound(engine):
    sfx.play("press")
    assert _wait(lambda: sfx._stream is not None)
    for name in ("select", "toggle_on", "page"):
        sfx.play(name)
    assert len(engine.opened) == 1
    audio = np.vstack([engine.opened[0].pull() for _ in range(20)])
    assert np.abs(audio).max() > 0.05
    assert np.abs(audio).max() < 0.3


def test_a_dead_device_is_dropped_and_reopened(engine):
    sfx.play("press")
    assert _wait(lambda: sfx._stream is not None)
    first = engine.opened[0]
    first.finished()  # PortAudio: the stream stopped (headphones unplugged)
    sfx.play("select")
    assert _wait(lambda: len(engine.opened) == 2 and sfx._stream is engine.opened[1])
    assert first.closed
    assert np.abs(np.vstack([engine.opened[1].pull() for _ in range(5)])).max() > 0.01


def test_a_stream_that_stops_calling_back_is_reopened(engine, monkeypatch):
    sfx.play("press")
    assert _wait(lambda: sfx._stream is not None)
    now = time.monotonic() + 5.0  # five seconds of silence from the device
    monkeypatch.setattr(sfx, "_now", lambda: now)
    sfx.play("select")
    assert _wait(lambda: len(engine.opened) == 2)


def test_a_failing_device_is_retried_later_not_on_every_sound(engine, monkeypatch, clock):
    broken = FakeDevices(fail=True)
    attempts = []

    def opener(callback, finished):
        attempts.append(1)
        return broken.open(callback, finished)

    monkeypatch.setattr(sfx, "_open_device", opener)
    sfx.play("press")
    _settle()
    for _ in range(10):
        clock[0] += 0.1
        sfx.play("select")
        _settle()
    assert len(attempts) == 1
    clock[0] += sfx._RETRY_S
    sfx.play("press")
    _settle()
    assert len(attempts) == 2


def test_turning_sounds_off_lets_go_of_the_device(engine):
    sfx.play("press")
    assert _wait(lambda: sfx._stream is not None)
    sfx.set_enabled(False)
    assert _wait(lambda: engine.opened[0].closed)
    assert sfx._stream is None
    sfx.set_enabled(True)
    assert _wait(lambda: len(engine.opened) == 2), "on again: the device opens ahead of the next sound"


def test_preview_plays_even_when_off(engine):
    sfx.set_enabled(False)
    sfx.preview("success")
    assert _queued() == ["success"]
    assert _wait(lambda: sfx._stream is not None)
    audio = np.vstack([engine.opened[0].pull() for _ in range(10)])
    assert np.abs(audio).max() > 0.05


def test_shutdown_closes_the_device(engine):
    sfx.play("press")
    assert _wait(lambda: sfx._stream is not None)
    sfx.shutdown()
    assert engine.opened[0].closed and sfx._stream is None and _queued() == []


# ── Warming up and the cache ────────────────────────────────────────────────

def test_warm_renders_everything_and_caches_it(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(sfx, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(sfx, "_SOUNDS", {})
    monkeypatch.setattr(sfx, "_render_started", False)
    sfx.warm(wait=True)
    assert set(sfx._SOUNDS) == set(sfx.NAMES)
    assert _wait(lambda: len(engine.opened) == 1), "and the device opens"
    files = list(tmp_path.glob("sounds-*.npz"))
    assert [f.name for f in files] == [sfx._cache_file().name]
    first = sfx._SOUNDS

    monkeypatch.setattr(sfx, "_SOUNDS", {})
    t0 = time.perf_counter()
    sfx._render_all()
    assert time.perf_counter() - t0 < 1.0
    for name in sfx.NAMES:
        for a, b in zip(first[name], sfx._SOUNDS[name]):
            assert np.array_equal(a, b)


def test_a_bad_cache_is_ignored(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(sfx, "_CACHE_DIR", tmp_path)
    sfx._cache_file().write_bytes(b"not a cache")
    assert sfx._load_cache() == {}


def test_the_cache_name_follows_the_sounds(monkeypatch):
    before = sfx._cache_file().name
    assert before == sfx._cache_file().name
    monkeypatch.setitem(sfx._SPECS, "press", sfx._SPECS["press"]._replace(peak=0.12))
    assert sfx._cache_file().name != before


def test_warm_does_nothing_when_off(engine):
    sfx.set_enabled(False)
    sfx.warm(wait=True)
    assert not engine.opened


# ── Settings ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, expected", [
    ("70", 70), ("100", 100), ("150", 100), ("-5", 0), ("55.6", 56), ("loud", 70), ("", 70),
    ("nan", 70), ("inf", 70),
])
def test_sound_volume_setting(monkeypatch, stored, expected):
    import config
    monkeypatch.setattr(config, "resolve_setting", lambda name, default: stored or default)
    assert config._sound_volume() == expected


def test_settings_come_from_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "SOUND_EFFECTS", False)
    monkeypatch.setattr(config, "SOUND_VOLUME", 35)
    assert sfx._settings_from_config() == (False, 0.35)
