"""Pictures of Clixy's notch, panel and Settings, drawn exactly as on screen, to review
the look without clicking anything.

    python tools/dev/ui_shots.py OUT_DIR [--only notch,home,live,settings] [--back desk.png] [--skin dark]

home is Home with nothing running (your real done jobs and notes from today); live is Home
while a job runs (Clixy clicking through a flights search, two jobs waiting) and while a
step waits for your yes.

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

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter  # noqa: E402
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


def fake_screen() -> tuple[QImage, tuple[float, float, float, float]]:
    """A browser on a flights page, as a screenshot the live card can show, and the date
    box's place in it."""
    img = QImage(1366, 768, QImage.Format.Format_RGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.fillRect(0, 0, 1366, 74, QColor("#EEF0F6"))
    p.setPen(Qt.PenStyle.NoPen)
    for i, c in enumerate(("#FF6B6B", "#FFC24B", "#3DD68C")):
        p.setBrush(QColor(c))
        p.drawEllipse(20 + i * 22, 20, 12, 12)
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(QRectF(110, 40, 900, 26), 13, 13)
    f = QFont("Segoe UI", 11)
    p.setFont(f)
    p.setPen(QColor("#8A8FA0"))
    p.drawText(QRectF(126, 40, 600, 26), Qt.AlignmentFlag.AlignVCenter, "google.com/travel/flights")
    p.setFont(QFont("Segoe UI", 26, QFont.Weight.DemiBold))
    p.setPen(QColor("#3A3F4E"))
    p.drawText(QRectF(300, 130, 600, 60), Qt.AlignmentFlag.AlignVCenter, "Flights")
    p.setFont(QFont("Segoe UI", 13))
    boxes = [(300, 220, 360, 54, "Dubai DXB"), (680, 220, 360, 54, "Calicut CCJ"), (300, 294, 360, 54, "Mon, 22 Dec")]
    for x, y, w, h, text in boxes:
        p.setPen(QColor("#DADDE6"))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(x, y, w, h), 10, 10)
        p.setPen(QColor("#3A3F4E"))
        p.drawText(QRectF(x + 18, y, w - 20, h), Qt.AlignmentFlag.AlignVCenter, text)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#E8EEFF"))
    p.drawRoundedRect(QRectF(680, 294, 150, 54), 27, 27)
    p.setPen(QColor("#3B5BDB"))
    p.drawText(QRectF(680, 294, 150, 54), Qt.AlignmentFlag.AlignCenter, "Search")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ECEEF4"))
    for i in range(4):
        p.drawRoundedRect(QRectF(300, 400 + i * 70, 740 - i * 90, 40), 10, 10)
    p.end()
    return img, (300.0, 294.0, 660.0, 348.0)


def panel_shots(out: Path, back, dpr, only) -> None:
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
        over(pn, back, dpr).save(str(out / "panel-home.png"))
    if "live" in only:
        import jobs
        screen, box = fake_screen()
        info = {"goal": "find flights from Dubai to Calicut on 22 Dec", "done": ["Opened Google Flights"],
                "now": "Type 22 December into the date box", "keys": "", "n": 2, "total": 3, "shortcut": "",
                "plan": ["read you the cheapest three"], "driving": True, "asking": False, "can_drive": True,
                "started": time.time() - 95, "view": (screen, box)}
        jobs.clear_queue()
        jobs.enqueue("clone Mum's voice in VoiceStudio")
        jobs.enqueue("add a calendar panel in Hark")
        pn.guide_fn = lambda: info
        pn.refresh()
        settle(pn)
        pn.hide()
        over(pn, back, dpr).save(str(out / "panel-live.png"))
        info = dict(info, now="Click Book on the 9:40 flyDubai", driving=False, asking=True, n=3,
                    done=["Opened Google Flights", "Typed 22 December"], plan=[])
        pn.refresh()
        settle(pn)
        pn.hide()
        over(pn, back, dpr).save(str(out / "panel-live-your-call.png"))
        jobs.clear_queue()
        pn.guide_fn = None
        pn.refresh()
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
    pn._tick.stop()
    pn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--only", default="notch,home,live,settings")
    ap.add_argument("--back", default="")
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
    if {"home", "live", "settings"} & set(only):
        panel_shots(out, back, dpr, only)
    print("saved to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
