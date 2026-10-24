"""An original 120 BPM track for the Clixy launch video, plus sound effects
synced to the render's event log. Everything is synthesized here.

Usage: beat.py EVENTS.json OUT.wav
"""
import json
import sys

import numpy as np
from scipy import signal

SR = 48_000
BPM = 120
B = 60 / BPM                 # one beat, 0.5 s
DUR = 40.0
N = int(SR * DUR)
rng = np.random.default_rng(7)

events = json.load(open(sys.argv[1]))
out_path = sys.argv[2]


def buf():
    return np.zeros((N, 2))


def at(t):
    return int(round(t * SR))


def add(dst, x, t, gain=1.0, pan=0.0):
    """Mix mono or stereo ``x`` into ``dst`` at time ``t``."""
    i = at(t)
    if i >= N:
        return
    if x.ndim == 1:
        l, r = np.sqrt(0.5 * (1 - pan)), np.sqrt(0.5 * (1 + pan))
        x = np.stack([x * l * 1.414, x * r * 1.414], axis=1)
    n = min(len(x), N - i)
    if i < 0:
        x, n, i = x[-i:], min(len(x) + i, N), 0
    dst[i:i + n] += gain * x[:n]


def bp(x, lo, hi, order=2):
    sos = signal.butter(order, [lo, hi], btype="band", fs=SR, output="sos")
    return signal.sosfilt(sos, x, axis=0)


def hp(x, f, order=2):
    return signal.sosfilt(signal.butter(order, f, btype="high", fs=SR, output="sos"), x, axis=0)


def lp(x, f, order=2):
    return signal.sosfilt(signal.butter(order, f, btype="low", fs=SR, output="sos"), x, axis=0)


def tt(sec):
    return np.arange(int(sec * SR)) / SR


def hz(note):
    """MIDI note to Hz."""
    return 440.0 * 2 ** ((note - 69) / 12)


# --- Drums ------------------------------------------------------------------------------

def kick():
    t = tt(0.42)
    f = 46 + 120 * np.exp(-t * 32)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 8.5)
    click = hp(rng.standard_normal(len(t)), 2500) * np.exp(-t * 400) * 0.35
    return np.tanh((body + click) * 1.6) * 0.9


def clap():
    t = tt(0.32)
    n = rng.standard_normal(len(t))
    env = np.zeros_like(t)
    for d in (0.0, 0.011, 0.022):
        env += np.where(t >= d, np.exp(-(t - d) * 90), 0)
    env += np.where(t >= 0.03, 0.45 * np.exp(-(t - 0.03) * 16), 0)
    return bp(n, 900, 5200) * env * 0.9


def hat(open_=False):
    t = tt(0.25 if open_ else 0.06)
    n = rng.standard_normal(len(t))
    return hp(n, 7500, 4) * np.exp(-t * (14 if open_ else 70)) * (0.5 if open_ else 0.4)


def crash():
    t = tt(2.6)
    n = rng.standard_normal((len(t), 2))
    return hp(n, 4200, 2) * np.exp(-t * 1.6)[:, None] * 0.4


def snare_hit():
    t = tt(0.18)
    tone = np.sin(2 * np.pi * 210 * t) * np.exp(-t * 40) * 0.22
    n = bp(rng.standard_normal(len(t)), 1200, 8000) * np.exp(-t * 22)
    return tone + n * 0.8


# --- Synths ------------------------------------------------------------------------------

def supersaw(freq, sec, voices=5, spread=0.012):
    t = tt(sec)
    out = np.zeros((len(t), 2))
    for v in range(voices):
        d = (v - (voices - 1) / 2) / ((voices - 1) / 2) * spread
        f = freq * (1 + d)
        ph = rng.random()
        saw = 2 * ((f * t + ph) % 1.0) - 1
        pan = (v - (voices - 1) / 2) / ((voices - 1) / 2) * 0.8
        out[:, 0] += saw * np.sqrt(0.5 * (1 - pan))
        out[:, 1] += saw * np.sqrt(0.5 * (1 + pan))
    return out / voices


def pad_chord(notes, sec, cutoff=2200):
    x = sum(supersaw(hz(n), sec) for n in notes) / len(notes)
    x = lp(x, cutoff, 2)
    t = tt(sec)
    env = np.minimum(1, t / 0.08) * np.minimum(1, (sec - t) / 0.12)
    return x * env[:, None]


def pluck(freq, sec=0.6, bright=1.0):
    t = tt(sec)
    x = np.zeros_like(t)
    for k in range(1, 14):
        if freq * k > 15_000:
            break
        x += np.sin(2 * np.pi * freq * k * t) / k * np.exp(-t * (5 + 4.2 * k / bright))
    return x * np.minimum(1, t / 0.002) * 0.5


def bass_note(freq, sec):
    t = tt(sec)
    x = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
         + 0.12 * (2 * ((freq * t) % 1) - 1) + 0.45 * np.sin(np.pi * freq * t))
    env = np.minimum(1, t / 0.006) * np.exp(-t * 4.5) * np.minimum(1, (sec - t) / 0.02)
    return np.tanh(x * env * 1.2) * 0.7


def bell(freq, sec=1.2):
    t = tt(sec)
    x = (np.sin(2 * np.pi * freq * t) * np.exp(-t * 5)
         + 0.5 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 11)
         + 0.25 * np.sin(2 * np.pi * freq * 5.4 * t) * np.exp(-t * 20))
    return x * np.minimum(1, t / 0.001) * 0.5


def pop_sfx(pitch=0):
    t = tt(0.16)
    f = (420 + 60 * pitch) * (1 + 1.6 * (1 - np.exp(-t * 60)))
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * 28) * 0.7


def riser(sec, lo=300, hi=9000):
    """Noise through a band that sweeps up, swelling."""
    n = rng.standard_normal(int(sec * SR))
    out = np.zeros_like(n)
    hop = 1024
    edges = np.geomspace(lo, hi, len(n) // hop + 2)
    zi = None
    for k, i in enumerate(range(0, len(n), hop)):
        c = edges[k]
        sos = signal.butter(2, [c * 0.7, min(c * 1.5, SR / 2 - 100)], btype="band", fs=SR, output="sos")
        if zi is None:
            zi = signal.sosfilt_zi(sos) * 0
        seg, zi = signal.sosfilt(sos, n[i:i + hop], zi=zi)
        out[i:i + hop] = seg
    env = np.linspace(0, 1, len(n)) ** 2.2
    return out * env * 0.8


def whoosh(sec=0.45):
    n = rng.standard_normal(int(sec * SR))
    t = tt(sec)
    env = np.sin(np.pi * np.minimum(1, t / sec)) ** 2
    return bp(n, 400, 3000) * env * 0.35


def key_click(down=True):
    t = tt(0.09)
    thump = np.sin(2 * np.pi * (140 if down else 190) * t) * np.exp(-t * 60)
    clk = hp(rng.standard_normal(len(t)), 3000) * np.exp(-t * 250)
    return (0.8 * thump + 0.6 * clk) * 0.8


def reverb(x, sec=1.6, wet=0.25):
    n = int(sec * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal((n, 2)) * np.exp(-t * 3.6)[:, None]
    ir = lp(ir, 6000)
    ir[: int(0.012 * SR)] = 0
    ir /= np.sqrt((ir ** 2).sum(axis=0))
    y = np.stack([signal.fftconvolve(x[:, c], ir[:, c])[:N] for c in range(2)], axis=1)
    return x + wet * y


# --- Arrangement -----------------------------------------------------------------------

# vi - IV - I - V in C, one chord per bar (2 s), from the drop at 4 s.
CHORDS = [(57, [57, 60, 64, 69]), (53, [53, 57, 60, 65]), (48, [55, 60, 64, 67]), (55, [55, 59, 62, 67])]
DROP = 4.0
BREAK = 33.0
HIT = 35.0


def chord_at(t):
    k = int(np.floor((t - DROP) / (4 * B))) % 4 if t >= DROP else 3
    return CHORDS[k]


drums, bass, pads, arps, sfx = buf(), buf(), buf(), buf(), buf()
K, CL, HC, HO = kick(), clap(), hat(), hat(True)

# Intro (0-4): a pad that opens up, a quiet arp, a riser and a snare roll into the drop.
for bar_t, (root, notes) in ((0.0, CHORDS[2]), (2.0, CHORDS[3])):
    add(pads, pad_chord([n + 12 for n in notes[:3]], 2.0, cutoff=900 + bar_t * 500), bar_t, 0.5)
for i in range(32):
    t = i * B / 4
    root, notes = chord_at(t)
    n = notes[[0, 1, 2, 3, 2, 1][i % 6]] + 12
    add(arps, pluck(hz(n), 0.5, bright=0.6), t, 0.18 * (0.4 + 0.6 * t / 4), pan=0.3 * (-1) ** i)
for i in range(8):
    add(drums, HC, 2.0 + i * B / 2 + B / 2 * 0, 0.4 if i % 2 else 0.25)
add(sfx, riser(2.0), 2.0, 0.55)
for i in range(16):
    t = 3.0 + i * B / 8 if i < 8 else 3.5 + (i - 8) * B / 16
    add(drums, snare_hit(), t, 0.15 + 0.5 * i / 16)
add(drums, crash(), DROP, 0.9)

# The groove, 4-33 s.
t = DROP
beat = 0
while t < BREAK - 1e-6:
    add(drums, K, t, 1.0)
    if beat % 2 == 1:
        add(drums, CL, t, 0.75)
    add(drums, HO, t + B / 2, 0.55)
    for s in (0.25, 0.75):
        add(drums, HC, t + B * s, 0.35, pan=0.25)
    root, notes = chord_at(t + 0.01)
    add(bass, bass_note(hz(root - 12 + (12 if beat % 4 == 3 else 0)), B / 2), t + B / 2, 0.8)
    t += B
    beat += 1
for bar in range(int((BREAK - DROP) / (4 * B))):
    bt = DROP + bar * 4 * B
    root, notes = chord_at(bt + 0.01)
    add(pads, pad_chord([n + 12 for n in notes], 4 * B), bt, 0.42)
for i in range(int((BREAK - DROP) / (B / 4))):
    t = DROP + i * B / 4
    root, notes = chord_at(t + 0.01)
    seq = [0, 2, 1, 3, 2, 1, 3, 2]
    n = notes[seq[i % 8]] + 24
    add(arps, pluck(hz(n), 0.45), t, 0.17 if i % 2 == 0 else 0.11, pan=0.35 * (-1) ** i)

# Sidechain: pads, bass and arps duck under each kick.
duck = np.ones(N)
tk = DROP
while tk < BREAK:
    i = at(tk)
    seg = 1 - 0.65 * np.exp(-np.arange(int(B * SR)) / SR * 11)
    duck[i:i + len(seg)] = np.minimum(duck[i:i + len(seg)], seg[: N - i])
    tk += B
for x in (pads, arps):
    x *= duck[:, None]
bass *= (0.35 + 0.65 * duck)[:, None]

# Break (33-35): drums out, the pad filters down, a riser and a roll; a breath; the hit.
add(pads, pad_chord([69, 72, 76], 2.0, cutoff=700), BREAK, 0.5)
add(sfx, riser(1.9, 200, 11000), BREAK, 0.6)
for i in range(16):
    t = 34.0 + i * B / 8
    if t < HIT - B / 4:
        add(drums, snare_hit(), t, 0.15 + 0.55 * i / 16)
# The end: a big C, kick and crash, then a soft groove that fades.
add(drums, K, HIT, 1.2)
add(drums, crash(), HIT, 1.0)
add(bass, bass_note(hz(48), 2.5), HIT, 0.9)
add(pads, pad_chord([60, 64, 67, 72, 76], 4.8, cutoff=3000), HIT, 0.55)
for i, n in enumerate([72, 76, 79, 84, 88]):
    add(arps, pluck(hz(n), 1.6, bright=1.4), HIT + i * 0.035, 0.22, pan=-0.4 + 0.2 * i)
t = HIT + B
beat = 1
while t < 39.0:
    fade = 1 - (t - HIT) / 4.5
    add(drums, K, t, 0.6 * fade)
    add(drums, HO, t + B / 2, 0.3 * fade)
    if beat % 2 == 1:
        add(drums, CL, t, 0.35 * fade)
    t += B
    beat += 1

# --- Sound effects from the render ---------------------------------------------------------

PENTA = [72, 74, 76, 79, 81, 84]
pen_t = []
for e in events:
    k, t = e["kind"], e["t"]
    if k in ("keys", "key_down"):
        add(sfx, key_click(True), t, 0.9, pan=-0.15)
        add(sfx, key_click(True), t + 0.03, 0.8, pan=0.15)
    elif k == "key_up":
        add(sfx, key_click(False), t, 0.7)
    elif k == "land":
        add(sfx, pop_sfx(0), t, 0.6)
        add(sfx, whoosh(0.3), t - 0.2, 0.6)
    elif k == "pop":
        p = int(e.get("pitch", 0))
        add(sfx, pop_sfx(p), t, 0.55)
        add(sfx, bell(hz(PENTA[p % len(PENTA)])), t, 0.32)
        add(sfx, whoosh(0.3), t - 0.15, 0.5)
    elif k == "tick":
        add(sfx, bell(hz(PENTA[int(e.get("pitch", 0)) % len(PENTA)] + 12), 0.5), t, 0.18)
    elif k == "got_it":
        add(sfx, bell(hz(88), 0.8), t, 0.22)
        add(sfx, bell(hz(93), 0.8), t + 0.07, 0.2)
    elif k == "answer":
        add(sfx, bell(hz(84), 0.6), t, 0.12)
    elif k == "hit":
        add(sfx, whoosh(0.25), t - 0.18, 0.7)
    elif k == "riser_hit":
        pass
    elif k == "pen":
        pen_t.append((t, e["x"], e["y"]))

# The pen: a soft marker scribble, louder when it moves faster.
if pen_t:
    pen_t.sort()
    env = np.zeros(N)
    for (t0, x0, y0), (t1, x1, y1) in zip(pen_t, pen_t[1:]):
        if 0 < t1 - t0 < 0.05:
            speed = np.hypot(x1 - x0, y1 - y0) / (t1 - t0)
            i0, i1 = at(t0), at(t1)
            env[i0:i1] = np.clip(speed / 900, 0, 1.0)
    env = np.convolve(env, np.ones(480) / 480, mode="same")
    grain = np.abs(rng.standard_normal(N)) ** 2
    grain = np.convolve(grain, np.ones(60) / 60, mode="same")
    scratch = bp(rng.standard_normal(N), 1800, 6500) * (0.5 + 0.5 * grain / grain.mean())
    add(sfx, scratch * env * 0.22, 0.0)

# Whooshes where the camera moves or a panel arrives.
for t in (2.05, 7.95, 16.95, 22.95, 26.95):
    add(sfx, whoosh(0.5), t - 0.3, 0.55)

# --- Mix ------------------------------------------------------------------------------------

music = drums * 0.95 + bass * 0.6 + reverb(pads * 0.6 + arps * 1.0, 1.8, 0.35)
mix = music + reverb(sfx, 0.9, 0.18) * 0.9
mix = hp(mix, 28, 2)
# Glue: soft saturation, then normalize to -1 dBFS.
mix /= np.max(np.abs(mix))
mix = np.tanh(mix * 1.3) / np.tanh(1.3)
# Fade out the tail.
tail = at(38.6)
mix[tail:] *= np.linspace(1, 0, N - tail)[:, None] ** 1.5
mix *= 10 ** (-1 / 20) / np.max(np.abs(mix))
rms = np.sqrt(np.mean(mix ** 2))
print(f"peak -1.0 dBFS, rms {20 * np.log10(rms):.1f} dBFS")

pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
import wave
with wave.open(out_path, "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print("wrote", out_path)
