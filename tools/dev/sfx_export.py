"""Writes every Clixy interface sound (sfx.py) to WAV files, to listen to them.

    python tools/dev/sfx_export.py OUT_DIR

OUT_DIR gets ``<name>-<k>.wav`` for every sound and variant (16-bit, 48 kHz,
stereo) and ``all-sounds.wav``: each sound's first variant in turn, 0.4 s
apart. Prints each sound's peak, loudness, audible length (to -30 dB) and
brightness (spectral centroid), and how long rendering took. Plays nothing.
"""
from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import sfx  # noqa: E402

GAP_S = 0.4


def write_wav(path: Path, samples: np.ndarray) -> None:
    """float samples (frames x 2) to a 16-bit stereo WAV."""
    pcm = np.round(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(sfx.SAMPLE_RATE)
        out.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    rendered = {name: [sfx.render(name, k) for k in range(sfx.variants(name))] for name in sfx.NAMES}
    render_s = time.perf_counter() - t0

    gap = np.zeros((int(GAP_S * sfx.SAMPLE_RATE), 2), np.float32)
    reel = [gap]
    print(f"{'sound':<13}{'var':>4}{'peak dBFS':>11}{'RMS dBFS':>10}{'body ms':>9}{'target':>8}"
          f"{'ring ms':>9}{'len ms':>8}{'centroid Hz':>13}")
    for name, takes in rendered.items():
        for k, samples in enumerate(takes):
            write_wav(args.out_dir / f"{name}-{k}.wav", samples)
        stats = [sfx.measure(s) for s in takes]
        mean = {key: float(np.mean([s[key] for s in stats])) for key in stats[0]}
        body = np.mean([sfx.measure(sfx.render(name, k, room=False))["dur_ms"] for k in range(len(takes))])
        print(f"{name:<13}{len(takes):>4}{mean['peak_db']:>11.1f}{mean['rms_db']:>10.1f}"
              f"{body:>9.0f}{sfx.target_ms(name):>8.0f}{mean['dur_ms']:>9.0f}{mean['len_ms']:>8.0f}"
              f"{mean['centroid_hz']:>13.0f}")
        reel += [takes[0], gap]
    print("body: the sound itself, to -30 dB; ring: with the shared room, to -30 dB;"
          " len: the samples kept (to -54 dB). Averages over the variants.")
    write_wav(args.out_dir / "all-sounds.wav", np.vstack(reel))
    count = sum(len(t) for t in rendered.values())
    print(f"\n{count} takes of {len(rendered)} sounds rendered in {render_s * 1000:.0f} ms; "
          f"written to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
