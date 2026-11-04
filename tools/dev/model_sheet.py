"""Renders every ready-made 3D model (and its cutaway) into one contact sheet, to review
the library at a glance.

    python tools/dev/model_sheet.py OUT.png [--cols 4] [--only heart,dna]

Each tile is the note card exactly as Clixy draws it (tools/dev/render_note.py does one).
Runs offscreen; needs no keys and calls no AI.
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

from PyQt6.QtCore import QRectF  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def card_image(note: dict, scale: float) -> QImage:
    import render3d
    import sketch
    card = sketch.NoteCard()
    card.show_note(note)
    card._timer.stop()
    if isinstance(card._model, render3d.RealModel):
        card._model.wait(180)
    card._resting = True
    img = QImage(int(card.width() * scale), int(card.height() * scale), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(scale)
    for back in (30.0, 60.0):
        card._t0 = time.monotonic() - back
        img.fill(0)
        p = QPainter(img)
        card.render(p)
        p.end()
    card.clear()
    card.close()
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    import theme
    theme.load_fonts()
    import model3d
    import sketch
    sketch.save_note = lambda n, img: None
    names = [n for n in model3d.library_names() if not a.only or n in a.only.split(",")]
    index = model3d.library_index()
    tiles = []
    for name in names:
        data = json.loads((model3d.LIBRARY / f"{name}.json").read_text(encoding="utf-8"))
        for cut in ((False, True) if index[name]["cutaway"] else (False,)):
            item = {"type": "model", "name": name, **({"cutaway": True} if cut else {})}
            note = {"topic": name.replace("_", " ") + (" (cut open)" if cut else ""), "title": data.get("title", name),
                    "subtitle": data.get("subtitle", ""), "view": {"3d": True}, "items": [item]}
            started = time.perf_counter()
            tiles.append(card_image(note, a.scale))
            print(f"{name}{' cut' if cut else ''}: {time.perf_counter() - started:.1f}s", flush=True)
    if not tiles:
        return 1
    w, h = tiles[0].width(), tiles[0].height()
    cols = max(1, min(a.cols, len(tiles)))
    rows = (len(tiles) + cols - 1) // cols
    sheet = QImage(w * cols, h * rows, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor(236, 232, 224))
    p = QPainter(sheet)
    for k, img in enumerate(tiles):
        p.drawImage(QRectF((k % cols) * w, (k // cols) * h, w, h), img, QRectF(0, 0, img.width(), img.height()))
    p.end()
    sheet.save(a.out)
    print("saved", a.out, f"({len(tiles)} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
