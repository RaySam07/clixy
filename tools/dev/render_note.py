"""Renders a 3D note card to a PNG exactly as Clixy draws it, to check a model by eye.

    python tools/dev/render_note.py NOTE.json OUT.png [--turn DEG] [--tilt DEG] [--t SECONDS] [--scale 2]

NOTE.json is a whole sketch note, or a ready-made model from models3d/ (shown the way
Sonnet would ask for it: {"type": "model", "name": ...}, with "--cut" for its cutaway).
--turn and --tilt add to the model's own view, as dragging it would; --t is the time
for its moving parts. Runs offscreen; needs no keys and calls no AI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def load(path: Path, cut: bool) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "items" in data:
        return data
    item = {"type": "model", "name": path.stem}
    if cut:
        item["cutaway"] = True
    if data.get("captions"):
        item["captions"] = True
    return {"topic": data.get("topic", data.get("title", path.stem)), "title": data.get("title", path.stem),
            "subtitle": data.get("subtitle", ""), "view": {"3d": True}, "items": [item]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("note")
    ap.add_argument("out")
    ap.add_argument("--turn", type=float, default=0.0)
    ap.add_argument("--tilt", type=float, default=0.0)
    ap.add_argument("--t", type=float, default=0.0)
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--cut", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="rebuild instead of using the mesh cache")
    a = ap.parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    import theme
    theme.load_fonts() if hasattr(theme, "load_fonts") else None
    import model3d
    import render3d
    import sketch
    if a.fresh:
        model3d.CACHE = Path(os.environ.get("TEMP", ".")) / "clixy-3d-fresh"
    note = load(Path(a.note), a.cut)
    card = sketch.NoteCard()
    started = time.perf_counter()
    card.show_note(note)
    card._timer.stop()
    model = card._model
    if not isinstance(model, render3d.RealModel):
        print("not a GPU model:", type(model).__name__)
    else:
        model.wait(120)
        if model._error is not None:
            print("build failed:", repr(model._error))
            return 1
        print(f"built in {time.perf_counter() - started:.2f}s: {len(model.model.meshes)} parts, "
              f"{sum(len(m.tri) for m in model.model.meshes):,} triangles")
        model.drag_az, model.drag_el = a.turn, a.tilt
    card._clock = a.t
    card._resting = True
    size = card.size()
    img = QImage(int(size.width() * a.scale), int(size.height() * a.scale), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(a.scale)
    for back in (30.0, 60.0):          # the first paint starts the reveal; the second is after it
        card._t0 = time.monotonic() - back
        img.fill(0)
        p = QPainter(img)
        card.render(p)
        p.end()
    img.save(a.out)
    print("saved", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
