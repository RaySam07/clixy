"""Pictures of Clixy's notch, panel and Settings, drawn exactly as on screen, to review
the look without clicking anything.

    python tools/dev/ui_shots.py OUT_DIR [--only notch,home,settings,working] [--back desk.png] [--skin dark]

Each picture is the real widget painted over a backdrop (a screenshot of your desktop
with --back, else a plain wallpaper gradient). Needs no keys and calls no AI.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def backdrop(w: int, h: int, back: QImage | None, dpr: float) -> QImage:
    img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    p = QPainter(img)
    if back is not None and not back.isNull():
        # The top centre of the desktop, where the notch and panel live.
        sw = back.width() / back.devicePixelRatio() if back.devicePixelRatio() else back.width()
        x = int((back.width() - w * dpr) / 2)
        p.drawImage(0, 0, back.copy(QRect(max(0, x), 0, int(w * dpr), int(h * dpr))))
    else:
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#3a4a6b"))
        g.setColorAt(1, QColor("#c98b6b"))
        p.fillRect(0, 0, w, h, g)
    p.end()
    return img


def over(widget, back: QImage | None, dpr: float, size=None) -> QImage:
    w, h = size or (widget.width(), widget.height())
    img = backdrop(w, h, back, dpr)
    p = QPainter(img)
    widget.render(p, QPoint(0, 0))
    p.end()
    return img


def settle(widget, steps: int = 200) -> None:
    import ui_kit
    ticker = ui_kit._the_ticker()
    for _ in range(steps):
        widget._animate()
        ticker._tick()          # the springs (buttons, cards) come to rest too


def notch_shots(out: Path, back, dpr) -> None:
    import island
    isl = island.SideIsland("top")
    isl._tick.stop()
    isl.hide()
    isl.name = "Rayan"
    states = [("notch-rest", None), ("notch-hello", "hello"), ("notch-listening", "listening"),
              ("notch-thinking", "thinking"), ("notch-speaking", "answer"), ("notch-hover", "hover")]
    for name, state in states:
        isl._state = "idle"
        isl._hello_until = 0.0
        isl._hover_since = None
        isl._near = False
        if state == "hello":
            isl._hello_until = time.monotonic() + 30
        elif state == "listening":
            isl.listening()
            isl._tick.stop()
            isl.set_level(0.6)
        elif state == "thinking":
            isl.thinking()
            isl._tick.stop()
        elif state == "answer":
            isl.thinking()
            isl._tick.stop()
            isl.say("Here we go.")
            isl._tick.stop()
            isl.set_level(0.5)
        elif state == "hover":
            isl._near, isl._near_until = True, time.monotonic() + 30
            isl._hover_since = time.monotonic() - 5
        for _ in range(160):
            if state == "hover":
                isl._near, isl._near_until = True, time.monotonic() + 30
            isl._animate()
        isl.hide()
        over(isl, back, dpr).save(str(out / f"{name}.png"))
    isl._tick.stop()
    isl.close()


def panel_shots(out: Path, back, dpr, only, note_index: int = 0) -> None:
    import panel as panel_mod
    panel_mod._key_down = lambda vk: False
    pn = panel_mod.ClixyPanel()
    pn.name = "Rayan"
    pn.hotkey = "Ctrl + Win"
    pn._activate = lambda: None
    pn.open_on()
    pn._tick.stop()
    pn.hide()
    if "home" in only:
        settle(pn)
        stack = pn.home.stack
        if note_index:
            stack.show_index(note_index)
            settle(pn)
        if stack._model is not None:            # a 3D note: wait for its model, then show it settled
            stack._model.wait(120)
            stack._shown_at -= 30
            stack.grab()                        # the first paint starts the model on the GPU
            stack._shown_at -= 30
        over(pn, back, dpr).save(str(out / "panel-home.png"))
    if "settings" in only:
        for key, _title in __import__("panel_settings").SECTIONS:
            pn.show_settings(key)
            QApplication.processEvents()
            pn._tick.stop()
            pn.settings.show_section(key)
            settle(pn)
            pn.hide()
            over(pn, back, dpr).save(str(out / f"settings-{key}.png"))
        pn.settings.discard()
    if "working" in only:
        info = {"question": "How do I turn on dark mode?", "steps": ["Open Settings", "Go to Personalisation"],
                "now": "Click Colours", "n": 3, "total": 4}
        pn.guide_fn = lambda: info
        pn.refresh()
        settle(pn)
        pn.hide()
        over(pn, back, dpr).save(str(out / "panel-working.png"))
    pn._tick.stop()
    pn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--only", default="notch,home,settings,working")
    ap.add_argument("--back", default="")
    ap.add_argument("--note", type=int, default=0, help="which kept note is in front on Home (0: the newest)")
    ap.add_argument("--skin", default="", help="light, dark or system (default: the stored one)")
    a = ap.parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    import theme
    theme.load_fonts()
    if a.skin:
        import ui_kit
        ui_kit._appearance = a.skin
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dpr = app.primaryScreen().devicePixelRatio() if app.primaryScreen() else 2.0
    back = QImage(a.back) if a.back else None
    if back is not None:
        back.setDevicePixelRatio(dpr)
    only = a.only.split(",")
    if "notch" in only:
        notch_shots(out, back, dpr)
    if {"home", "settings", "working"} & set(only):
        panel_shots(out, back, dpr, only, a.note)
    print("saved to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
