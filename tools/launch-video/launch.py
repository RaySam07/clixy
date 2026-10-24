"""Clixy launch video, 1920x1080 at 60 fps, drawn frame by frame from the
real app code: the side island (island.py), the overlay with its edge glow
and the drawing engine (overlay.py, drawing.py), and the pixel buddies
(theme.py). Writes raw frames to ffmpeg and a JSON list of sound events
(key presses, pops, the pen scribbling) for beat.py.

Usage: launch.py OUT.mp4 EVENTS.json [first_s last_s]
Run with QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2.
"""
import json
import math
import subprocess
import sys
import time as _time
import types

REPO = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
clock = [1000.0]
_time.monotonic = lambda: clock[0]

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient, QTransform)
from PyQt6.QtWidgets import QApplication

app = QApplication([])
import keyring
keyring.get_password = lambda s, n: None
keyring.set_password = lambda s, n, v: None
import theme
theme.apply(app)
import overlay
overlay.apply_clickthrough_styles = lambda h: None
import drawing
import aim
from island import SideIsland
from annotations import Angle, Arrow, Box, Dot, Line, Ring
from PIL import Image

# The overlay gets its own clock, so a demo can play its drawing a little faster.
ov_clock = [1000.0]
overlay.time = types.SimpleNamespace(monotonic=lambda: ov_clock[0])

FPS = 60
W, H = 1920, 1080
BEAT = 0.5
DUR = 40.0
SW, SH = 1280, 720          # the mock screen, logical px (rendered at 2x)

INK = QColor(10, 10, 18)
PAPER = QColor(245, 242, 234)
CORAL = QColor(255, 90, 61)
BUTTER = QColor(255, 210, 63)
WHITE = QColor(255, 255, 255)
GREEN = QColor(40, 190, 110)
PURPLE = QColor(112, 84, 255)
BLUE = QColor(30, 150, 255)

EVENTS = []
_once = set()


def ev(t, kind, key=None, **kw):
    """A sound for beat.py. ``key`` makes it fire once."""
    if key is not None:
        if key in _once:
            return
        _once.add(key)
    EVENTS.append(dict(t=round(t, 4), kind=kind, **kw))


# --- Easing ---------------------------------------------------------------------------

def c01(u):
    return max(0.0, min(1.0, u))


def expo_out(u):
    u = c01(u)
    return 1.0 if u >= 1 else 1 - 2 ** (-10 * u)


def cubic_io(u):
    u = c01(u)
    return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def back_out(u, s=1.9):
    u = c01(u) - 1
    return 1 + (s + 1) * u ** 3 + s * u ** 2


def spring(sec, w=16.0, z=0.32):
    """0 -> 1 with an overshoot, ``sec`` seconds after it starts."""
    if sec <= 0:
        return 0.0
    return 1 - math.exp(-z * w * sec) * math.cos(w * math.sqrt(1 - z * z) * sec)


def lerp(a, b, u):
    return a + (b - a) * u


def mix(c1, c2, u):
    return QColor(round(lerp(c1.red(), c2.red(), u)), round(lerp(c1.green(), c2.green(), u)),
                  round(lerp(c1.blue(), c2.blue(), u)), round(lerp(c1.alpha(), c2.alpha(), u)))


def alpha(c, a):
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


# --- Type -----------------------------------------------------------------------------

def mfont(px, weight=QFont.Weight.Light, spacing=97):
    f = theme.ui_font(10, weight)
    f.setPixelSize(max(1, round(px)))
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def ifont(px, weight=QFont.Weight.Normal):
    f = QFont("Inter")
    f.setWeight(weight)
    f.setPixelSize(max(1, round(px)))
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def jfont(px):
    f = QFont("JetBrains Mono")
    f.setPixelSize(max(1, round(px)))
    return f


def kinetic(p, words, x, y, f, t, t_in, *, align="center", stagger=0.05, dur=0.65, t_out=None,
            out_dur=0.32, color=None):
    """Words that rise into place from under their own baseline (a masked
    reveal), and leave the same way upward. ``words``: a string or a list of
    (word, color). Returns each word's rect."""
    if isinstance(words, str):
        words = [(w, color) for w in words.split(" ")]
    fm = QFontMetricsF(f)
    asc, desc = fm.ascent(), fm.descent()
    sp = fm.horizontalAdvance(" ")
    widths = [fm.horizontalAdvance(w) for w, _ in words]
    total = sum(widths) + sp * (len(words) - 1)
    x0 = x - total / 2 if align == "center" else (x - total if align == "right" else x)
    lh = asc + desc
    rects = []
    p.save()
    p.setFont(f)
    p.setClipRect(QRectF(x0 - 60, y - asc - lh * 0.12, total + 120, lh * 1.24))
    cx = x0
    for i, ((w, c), ww) in enumerate(zip(words, widths)):
        u = expo_out((t - t_in - i * stagger) / dur)
        dy = (1 - u) * lh * 1.1
        if t_out is not None:
            dy -= cubic_io((t - t_out - i * stagger * 0.4) / out_dur) * lh * 1.2
        if u > 0:
            p.setPen(c)
            p.drawText(QPointF(cx, y + dy), w)
        rects.append(QRectF(cx, y - asc, ww, lh))
        cx += ww + sp
    p.restore()
    return rects


def text_width(s, f):
    return QFontMetricsF(f).horizontalAdvance(s)


def chip(p, cx, cy, text, f, fg, bg=None, border=None, pad=(26, 14), scale=1.0, op=1.0, dot=None):
    fm = QFontMetricsF(f)
    tw = fm.horizontalAdvance(text) + (22 if dot is not None else 0)
    w, h = tw + 2 * pad[0], fm.height() + 2 * pad[1] - 8
    p.save()
    p.setOpacity(op)
    p.translate(cx, cy)
    p.scale(scale, scale)
    r = QRectF(-w / 2, -h / 2, w, h)
    p.setPen(QPen(border, 2.4) if border is not None else Qt.PenStyle.NoPen)
    p.setBrush(bg if bg is not None else Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, h / 2, h / 2)
    x = r.left() + pad[0]
    if dot is not None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        p.drawEllipse(QPointF(x + 6, 0), 6, 6)
        x += 22
    p.setPen(fg)
    p.setFont(f)
    p.drawText(QPointF(x, fm.ascent() - fm.height() / 2 + 1), text)
    p.restore()
    return r.translated(cx, cy)


def buddy(p, aid, cx, cy, size, t, mood="happy", glow=False, squash=0.0):
    """A buddy centred at (cx, cy); ``size`` is the cell size in px."""
    av = theme.avatar(aid)
    min_x, min_y, max_x, max_y = av.extent
    w, h = (max_x - min_x + 1) * size, (max_y - min_y + 1) * size
    scale = size / theme.CELL
    x = cx - w / 2 - min_x * size
    y = cy - h / 2 - min_y * size
    theme.paint_avatar(p, x, y, t=t, mood=mood, avatar_id=aid, scale=scale, glow=glow, squash=squash)


def burst(p, aid, x, y, prog, cell, reach=1.0):
    if not 0.0 <= prog < 1.0:
        return
    colors = theme.avatar(aid).palette.dots
    e = 1 - (1 - prog) ** 3
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for i in range(12):
        a = math.radians(i * 30 + 12)
        d = (6 + 20 * e * reach) * cell * (0.8 + 0.4 * ((i * 37) % 5) / 4)
        c = QColor(colors[i % 3])
        c.setAlpha(int(255 * (1 - prog)))
        s = cell * (2 if prog < 0.5 else 1)
        p.fillRect(QRectF(round(x + d * math.cos(a)), round(y + d * math.sin(a)), s, s), c)
    p.restore()


# --- Keycaps --------------------------------------------------------------------------

def win_logo(p, cx, cy, s, color):
    g = s * 0.08
    q = (s - g) / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    for dx in (0, 1):
        for dy in (0, 1):
            p.drawRoundedRect(QRectF(cx - s / 2 + dx * (q + g), cy - s / 2 + dy * (q + g), q, q), s * 0.04, s * 0.04)


def keycap(p, cx, cy, w, h, label, press, *, win=False, scale=1.0, hot=0.0):
    """``press`` 0..1 pushes the key down; ``hot`` 0..1 turns it coral."""
    p.save()
    p.translate(cx, cy)
    p.scale(scale, scale)
    depth = 16 * (1 - press) + 4
    body = QRectF(-w / 2, -h / 2, w, h)
    # Shadow and side.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawRoundedRect(body.translated(0, 22), 30, 30)
    side = mix(QColor(205, 199, 186), QColor(190, 60, 36), hot)
    p.setBrush(side)
    p.drawRoundedRect(body.translated(0, 18), 30, 30)
    top = body.translated(0, 18 - depth)
    p.setBrush(mix(WHITE, CORAL, hot))
    p.setPen(QPen(mix(QColor(230, 224, 212), QColor(255, 120, 90), hot), 2))
    p.drawRoundedRect(top, 28, 28)
    fg = mix(INK, WHITE, hot)
    f = mfont(h * 0.3, QFont.Weight.Medium, 100)
    tw = text_width(label, f)
    if win:
        logo = h * 0.26
        total = logo + 18 + tw
        win_logo(p, top.center().x() - total / 2 + logo / 2, top.center().y(), logo, fg)
        x = top.center().x() - total / 2 + logo + 18
    else:
        x = top.center().x() - tw / 2
    p.setPen(fg)
    p.setFont(f)
    fm = QFontMetricsF(f)
    p.drawText(QPointF(x, top.center().y() + fm.ascent() / 2 - fm.descent() / 2 + 2), label)
    p.restore()


# --- The mock screen ------------------------------------------------------------------

def cursor_path(at, s=1.0):
    path = QPainterPath(at)
    for dx, dy in ((0, 17), (4.5, 13), (7.5, 20), (10, 19), (7, 12), (12.5, 12)):
        path.lineTo(QPointF(at.x() + dx * s, at.y() + dy * s))
    path.closeSubpath()
    return path


def paint_cursor(p, at, s=1.15):
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(WHITE, 1.5))
    p.setBrush(QColor(0, 0, 0))
    p.drawPath(cursor_path(at, s))


def wallpaper(p, w=SW, h=SH):
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0.0, QColor(255, 236, 218))
    g.setColorAt(0.5, QColor(255, 205, 182))
    g.setColorAt(1.0, QColor(250, 166, 140))
    p.fillRect(QRectF(0, 0, w, h), g)
    for cx, cy, r, c in ((260, 520, 520, QColor(255, 255, 255, 90)), (1080, 150, 420, QColor(255, 140, 90, 70)),
                         (820, 760, 380, QColor(255, 120, 100, 60))):
        rg = QRadialGradient(QPointF(cx, cy), r)
        rg.setColorAt(0, c)
        rg.setColorAt(1, alpha(c, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rg)
        p.drawEllipse(QPointF(cx, cy), r, r)


def taskbar(p, w=SW, h=SH):
    bar = QRectF(0, h - 46, w, 46)
    p.fillRect(bar, QColor(250, 247, 242, 215))
    p.fillRect(QRectF(0, h - 46, w, 1), QColor(0, 0, 0, 22))
    cx = w / 2
    icons = [None, "search", QColor(255, 196, 64), QColor(52, 120, 246), QColor(40, 180, 110), QColor(240, 80, 60),
             QColor(120, 90, 240)]
    total = 40 * len(icons) + 120
    x = cx - total / 2
    for item in icons:
        if item is None:
            win_logo(p, x + 16, h - 23, 18, QColor(30, 120, 220))
            x += 40
        elif item == "search":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawRoundedRect(QRectF(x, h - 37, 150, 28), 14, 14)
            p.setPen(QColor(110, 110, 120))
            p.setFont(ifont(11))
            p.drawText(QPointF(x + 30, h - 18), "Search")
            p.setPen(QPen(QColor(110, 110, 120), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x + 16, h - 24), 5, 5)
            x += 160
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(item)
            p.drawRoundedRect(QRectF(x + 6, h - 35, 24, 24), 6, 6)
            x += 40
    p.setPen(QColor(40, 40, 50))
    p.setFont(ifont(11))
    p.drawText(QRectF(w - 110, h - 44, 96, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "9:41 PM")
    p.setPen(QColor(90, 90, 100))
    p.drawText(QRectF(w - 110, h - 22, 96, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "10/24/2026")


def window_chrome(p, rect, title):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 30))
    p.drawRoundedRect(rect.adjusted(-2, 2, 2, 8), 12, 12)
    p.setBrush(QColor(251, 251, 253))
    p.drawRoundedRect(rect, 10, 10)
    p.setBrush(QColor(243, 243, 247))
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect.left(), rect.top(), rect.width(), 46), 10, 10)
    p.drawPath(path)
    p.fillRect(QRectF(rect.left(), rect.top() + 36, rect.width(), 10), QColor(243, 243, 247))
    p.fillRect(QRectF(rect.left(), rect.top() + 46, rect.width(), 1), QColor(226, 226, 234))
    p.setPen(QColor(40, 40, 52))
    p.setFont(ifont(12.5, QFont.Weight.Medium))
    p.drawText(QPointF(rect.left() + 46, rect.top() + 28), title)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(40, 180, 110))
    p.drawRoundedRect(QRectF(rect.left() + 16, rect.top() + 14, 18, 18), 5, 5)
    p.setPen(QPen(QColor(90, 90, 100), 1.3))
    x = rect.right() - 30
    p.drawLine(QPointF(x - 5, rect.top() + 18), QPointF(x + 5, rect.top() + 28))
    p.drawLine(QPointF(x + 5, rect.top() + 18), QPointF(x - 5, rect.top() + 28))
    p.drawRect(QRectF(x - 50, rect.top() + 18, 10, 10))
    p.drawLine(QPointF(x - 96, rect.top() + 23), QPointF(x - 86, rect.top() + 23))


SHEET = {}


def sheet_scene(p):
    """A spreadsheet app: a table and a bar chart, Share at the top right."""
    wallpaper(p)
    taskbar(p)
    win = QRectF(28, 22, 1224, 640)
    window_chrome(p, win, "Q3 Revenue · Ledger")
    share = QRectF(win.right() - 196, win.top() + 8, 84, 30)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(22, 22, 30))
    p.drawRoundedRect(share, 15, 15)
    p.setPen(WHITE)
    p.setFont(ifont(12.5, QFont.Weight.DemiBold))
    p.drawText(share, Qt.AlignmentFlag.AlignCenter, "Share")
    for i, c in enumerate((QColor(255, 170, 60), QColor(120, 90, 240))):
        p.setPen(QPen(QColor(243, 243, 247), 2))
        p.setBrush(c)
        p.drawEllipse(QPointF(share.left() - 44 + i * 18, share.center().y()), 12, 12)
    SHEET["share"] = share
    # Menu
    p.setPen(QColor(60, 60, 72))
    p.setFont(ifont(12.5))
    for i, name in enumerate(("File", "Edit", "View", "Insert", "Format", "Data", "Help")):
        p.drawText(QPointF(win.left() + 20 + i * 64, win.top() + 72), name)
    p.fillRect(QRectF(win.left(), win.top() + 86, win.width(), 1), QColor(230, 230, 236))
    # Table
    tx, ty = win.left() + 30, win.top() + 120
    p.setFont(ifont(13, QFont.Weight.DemiBold))
    p.setPen(QColor(30, 30, 40))
    p.drawText(QPointF(tx, ty), "Month")
    p.drawText(QPointF(tx + 150, ty), "Revenue")
    p.drawText(QPointF(tx + 270, ty), "Growth")
    rows = (("June", "$31,200", "+4%"), ("July", "$36,050", "+16%"), ("August", "$48,400", "+34%"),
            ("September", "$41,900", "−13%"), ("Q3 total", "$157,550", "+18%"))
    p.setFont(ifont(13))
    for i, (m, v, g) in enumerate(rows):
        y = ty + 40 + i * 38
        p.fillRect(QRectF(tx - 10, y - 26, 360, 1), QColor(232, 232, 238))
        p.setPen(QColor(50, 50, 62))
        if i == 4:
            p.setFont(ifont(13, QFont.Weight.DemiBold))
        p.drawText(QPointF(tx, y), m)
        p.drawText(QPointF(tx + 150, y), v)
        p.setPen(QColor(30, 150, 90) if not g.startswith("−") else QColor(210, 70, 60))
        p.drawText(QPointF(tx + 270, y), g)
    # Notes
    p.setPen(QColor(150, 150, 160))
    p.setFont(ifont(12))
    p.drawText(QPointF(tx, ty + 270), "Notes")
    p.setPen(QColor(205, 205, 214))
    for i in range(5):
        p.drawLine(QPointF(tx, ty + 300 + i * 26), QPointF(tx + 330 - (i % 3) * 60, ty + 300 + i * 26))
    # Chart card
    card = QRectF(win.left() + 430, win.top() + 104, 520, 500)
    p.setPen(QPen(QColor(232, 232, 240), 1))
    p.setBrush(WHITE)
    p.drawRoundedRect(card, 12, 12)
    p.setPen(QColor(30, 30, 40))
    p.setFont(ifont(15, QFont.Weight.DemiBold))
    p.drawText(QPointF(card.left() + 26, card.top() + 40), "Revenue by month")
    p.setPen(QColor(140, 140, 150))
    p.setFont(ifont(12))
    p.drawText(QPointF(card.left() + 26, card.top() + 62), "Q3 2026, in thousands")
    base = card.bottom() - 60
    vals = (31.2, 36.05, 48.4, 41.9)
    p.setPen(QPen(QColor(236, 236, 242), 1))
    for k in range(5):
        y = base - k * 80
        p.drawLine(QPointF(card.left() + 26, y), QPointF(card.right() - 26, y))
    bars = []
    for i, v in enumerate(vals):
        bh = v / 50 * 320
        r = QRectF(card.left() + 70 + i * 110, base - bh, 64, bh)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(40, 180, 110) if i != 2 else QColor(30, 160, 98))
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0, 0, 0, 8), 8, 8)
        p.setClipRect(QRectF(r.left() - 2, r.top() - 2, r.width() + 4, r.height() + 2))
        p.drawPath(path)
        p.setClipping(False)
        p.setPen(QColor(60, 60, 72))
        p.setFont(ifont(12.5, QFont.Weight.Medium))
        p.drawText(QRectF(r.left() - 20, r.top() - 26, r.width() + 40, 20), Qt.AlignmentFlag.AlignCenter, f"{v:.1f}")
        p.setPen(QColor(120, 120, 130))
        p.setFont(ifont(12.5))
        p.drawText(QRectF(r.left() - 20, base + 12, r.width() + 40, 20), Qt.AlignmentFlag.AlignCenter,
                   ("Jun", "Jul", "Aug", "Sep")[i])
        bars.append(r)
    SHEET["aug"] = bars[2]
    # Right column: comments
    cx = win.left() + 980
    p.setPen(QColor(30, 30, 40))
    p.setFont(ifont(13, QFont.Weight.DemiBold))
    p.drawText(QPointF(cx, win.top() + 120), "Comments")
    for i in range(3):
        y = win.top() + 150 + i * 92
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(246, 246, 250))
        p.drawRoundedRect(QRectF(cx - 8, y, 220, 76), 10, 10)
        p.setBrush((QColor(255, 170, 60), QColor(120, 90, 240), QColor(40, 180, 110))[i])
        p.drawEllipse(QPointF(cx + 12, y + 20), 9, 9)
        p.setPen(QColor(200, 200, 210))
        p.drawLine(QPointF(cx + 30, y + 22), QPointF(cx + 180, y + 22))
        p.drawLine(QPointF(cx + 6, y + 44), QPointF(cx + 196, y + 44))
        p.drawLine(QPointF(cx + 6, y + 60), QPointF(cx + 140, y + 60))


GEO = {}


def worksheet_scene(p):
    """A maths worksheet on the desktop: a circle with its centre, and a triangle."""
    wallpaper(p)
    taskbar(p)
    win = QRectF(28, 22, 1224, 640)
    window_chrome(p, win, "Worksheet 7 · Circles and triangles.pdf")
    paper = QRectF(win.left() + 1, win.top() + 47, win.width() - 2, win.height() - 48)
    p.fillRect(paper, QColor(253, 253, 251))
    p.save()
    p.setClipRect(paper)
    p.setPen(QPen(QColor(226, 232, 242), 1))
    for x in range(int(paper.left()), int(paper.right()), 28):
        p.drawLine(QPointF(x, paper.top()), QPointF(x, paper.bottom()))
    for y in range(int(paper.top()), int(paper.bottom()), 28):
        p.drawLine(QPointF(paper.left(), y), QPointF(paper.right(), y))
    p.restore()
    p.setPen(QColor(25, 25, 35))
    p.setFont(ifont(17, QFont.Weight.Bold))
    p.drawText(QPointF(70, 112), "Q4.  Circles and triangles")
    p.setFont(ifont(13.5))
    p.drawText(QPointF(70, 140), "a)  The circle has a diameter of 10 cm. Show its radius and circumference.")
    p.drawText(QPointF(70, 164), "b)  Find the three angles of triangle ABC.")
    c = QPointF(300, 410)
    p.setPen(QPen(QColor(25, 25, 35), 2.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(c, 160, 160)
    p.setBrush(QColor(25, 25, 35))
    p.drawEllipse(c, 3, 3)
    p.setBrush(Qt.BrushStyle.NoBrush)
    A, B, C = QPointF(640, 575), QPointF(1000, 575), QPointF(760, 305)
    path = QPainterPath(A)
    path.lineTo(B)
    path.lineTo(C)
    path.closeSubpath()
    p.drawPath(path)
    p.setFont(ifont(16, QFont.Weight.DemiBold))
    p.drawText(QPointF(618, 600), "A")
    p.drawText(QPointF(1008, 600), "B")
    p.drawText(QPointF(754, 292), "C")
    GEO.update(c=c, r=160, A=A, B=B, C=C)


def desktop_scene(p):
    wallpaper(p)
    taskbar(p)
    # A couple of desktop icons, top left.
    for i, (name, col) in enumerate((("Projects", QColor(255, 196, 64)), ("Notes.txt", QColor(240, 240, 245)),
                                      ("Recycle Bin", QColor(200, 214, 230)))):
        y = 30 + i * 92
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(34, y, 44, 38), 6, 6)
        p.setPen(QColor(40, 30, 30))
        p.setFont(ifont(11))
        p.drawText(QRectF(6, y + 42, 100, 20), Qt.AlignmentFlag.AlignHCenter, name)


def make_bg(fn):
    img = QImage(SW * 2, SH * 2, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(2)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    fn(p)
    p.end()
    return img


def busy_for(bg):
    small = bg.scaled(SW, SH, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    small = small.convertToFormat(QImage.Format.Format_ARGB32)
    ptr = small.constBits()
    ptr.setsize(small.sizeInBytes())
    pil = Image.frombuffer("RGBA", (SW, SH), bytes(ptr), "raw", "BGRA", 0, 1)
    return aim.busy_map(pil)


ISLAND_AT = QPointF(SW - 400, round(SH * 0.34) - 170)
ISLAND_RECT = QRectF(SW - 400, round(SH * 0.34) - 170, 400, 340)


class Rig:
    """The real overlay + island over a mock screen, stepped frame by frame."""

    def __init__(self, bg_fn, *, speed=1.0, model="Haiku 5.5"):
        self.bg = make_bg(bg_fn)
        self.busy = busy_for(self.bg)
        self.ov = overlay.OverlayWindow(app.primaryScreen())
        self.ov._life_timer.stop()
        self.ov._annotation_clear_timer.stop()
        self.ov.setGeometry(0, 0, SW, SH)
        self.ov.show()
        self.ovs = [self.ov]
        self.isl = SideIsland(side="right")
        self.isl._tick.stop()
        self.isl._update_near = lambda: None
        self.isl.model_name = model
        self.speed = speed
        self.mouse = QPointF(SW * 0.55, SH * 0.6)
        self.show_mouse = True
        self.level = 0.0
        self.buddy_follows = True
        self.last = None
        self.drawing_log = []

    def second_overlay(self):
        ov = overlay.OverlayWindow(app.primaryScreen())
        ov._life_timer.stop()
        ov._annotation_clear_timer.stop()
        ov.setGeometry(0, 0, SW, SH)
        ov.show()
        self.ovs.append(ov)
        return ov

    def step(self, t):
        dt = 1 / FPS if self.last is None else t - self.last
        self.last = t
        ov_clock[0] += dt * self.speed
        mouse = self.mouse
        overlay.QCursor.pos = staticmethod(lambda: self.ov.mapToGlobal(QPoint(round(mouse.x()), round(mouse.y()))))
        for ov in self.ovs:
            if ov._tour is None and self.buddy_follows and ov is self.ovs[-1] and not ov._marks:
                ov._pointer_visible = True
                ov._pointer_pos = QPoint(round(mouse.x() + 16), round(mouse.y() + 26))
            ov.set_audio_level(self.level)
            ov._on_life_tick()
            if ov._tour is not None:
                tt = ov_clock[0] - ov._tour_t0
                pos, _s, mark = ov._tour.pen(tt)
                self.drawing_log.append((t, pos.x(), pos.y(), mark is not None and mark.kind != "dot"))
        self.isl.set_level(self.level)
        self.isl._animate()

    def screen(self):
        img = QImage(self.bg)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for ov in self.ovs:
            p.drawPixmap(QPointF(0, 0), ov.grab())
        p.drawPixmap(ISLAND_AT, self.isl.grab())
        if self.show_mouse:
            paint_cursor(p, self.mouse)
        p.end()
        return img


def present(p, img, rect, *, radius=22, bezel=12, shadow=True, op=1.0):
    """The screen image into ``rect`` (frame px), as a monitor: shadow,
    dark bezel, rounded screen."""
    p.save()
    p.setOpacity(op)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    outer = rect.adjusted(-bezel, -bezel, bezel, bezel)
    if shadow:
        for i in range(10):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 14))
            grow = 4 + i * 5
            p.drawRoundedRect(outer.adjusted(-grow * 0.5, -grow * 0.2 + 18, grow * 0.5, grow * 0.7 + 18),
                              radius + bezel + grow * 0.5, radius + bezel + grow * 0.5)
    p.setPen(QPen(QColor(255, 255, 255, 28), 1.5))
    p.setBrush(QColor(14, 14, 18))
    p.drawRoundedRect(outer, radius + bezel * 0.8, radius + bezel * 0.8)
    img.setDevicePixelRatio(1)
    brush = QBrush(img)
    sx = rect.width() / img.width()
    sy = rect.height() / img.height()
    brush.setTransform(QTransform(sx, 0, 0, sy, rect.left(), rect.top()))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(brush)
    p.drawRoundedRect(rect, radius, radius)
    p.restore()


def speech_level(u):
    """A voice-like level, 0..1, for u seconds into speaking."""
    syll = 0.5 + 0.5 * math.sin(u * 23.0) * math.sin(u * 7.3 + 1.0)
    words = 0.55 + 0.45 * math.sin(u * 4.1 + 0.6)
    return max(0.0, min(1.0, 0.25 + 0.7 * syll * words))


def caption(p, cx, cy, text, t, t0, t1, t_end, f=None):
    """What you said, typed in word by word between t0 and t1, in a pill."""
    if t < t0 or t > t_end + 0.3:
        return
    f = f or mfont(34, QFont.Weight.Light, 99)
    words = text.split(" ")
    n = max(1, min(len(words), int(len(words) * c01((t - t0) / (t1 - t0))) + 1))
    shown = "“" + " ".join(words[:n]) + ("”" if n == len(words) else "")
    full = "“" + text + "”"
    fm = QFontMetricsF(f)
    w = fm.horizontalAdvance(full) + 90
    h = 74
    op = c01((t - t0) / 0.18) * (1 - c01((t - t_end) / 0.3))
    p.save()
    p.setOpacity(op)
    r = QRectF(cx - w / 2, cy - h / 2, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 215))
    p.drawRoundedRect(r, h / 2, h / 2)
    pulse = (t % 1.0)
    p.setBrush(alpha(CORAL, 0.5 * (1 - pulse)))
    p.drawEllipse(QPointF(r.left() + 40, cy), 7 + 9 * pulse, 7 + 9 * pulse)
    p.setBrush(CORAL)
    p.drawEllipse(QPointF(r.left() + 40, cy), 7, 7)
    p.setPen(WHITE)
    p.setFont(f)
    p.drawText(QPointF(r.left() + 66, cy + fm.ascent() / 2 - fm.descent() / 2), shown)
    p.restore()


def flash(p, t, t0, dur=0.22, color=WHITE):
    u = (t - t0) / dur
    if 0 <= u < 1:
        p.fillRect(QRectF(0, 0, W, H), alpha(color, 0.85 * (1 - u) ** 2))


# --- Scenes ---------------------------------------------------------------------------

class SceneReveal:
    """0-4 s. A calm desktop, close on the right edge. psst. over here. The
    notch peeks at the mouse, Ctrl+Win, it springs open and the screen glows."""
    t0, t1 = 0.0, 4.0

    def __init__(self):
        self.rig = Rig(desktop_scene)
        self.rig.mouse = QPointF(700, 470)
        self.rig.buddy_follows = False
        self.arrow = None

    def frame(self, p, t):
        r = self.rig
        u = t - self.t0
        # Mouse heads for the edge, the notch peeks at it.
        m = cubic_io((u - 0.75) / 0.95)
        r.mouse = QPointF(lerp(700, 1236, m), lerp(470, 262, m) - 60 * math.sin(math.pi * m) * 0.4)
        away = cubic_io((u - 2.1) / 0.6)
        r.mouse = QPointF(r.mouse.x() - 190 * away, r.mouse.y() + 120 * away)
        r.isl._near = m > 0.82 and u < 2.0
        if u >= 2.0:
            r.isl.listening() if r.isl._state != "listening" else None
            r.ov.show_waveform()
            r.level = speech_level(u - 2.0) * 0.9
            ev(t, "keys", key="reveal-keys")
        r.step(t)
        screen = r.screen()
        p.fillRect(QRectF(0, 0, W, H), INK)
        # Camera: close on the notch, then pull back to the whole screen.
        k = cubic_io((u - 2.05) / 1.55)
        zoom = lerp(2.15, 1.0, k)
        fx, fy = lerp(1110, SW / 2, k), lerp(262, SH / 2, k)
        base = W * 0.84 / SW
        s = base * zoom
        rect = QRectF(W / 2 - fx * s, H / 2 - fy * s, SW * s, SH * s)
        present(p, screen, rect, radius=22 * zoom * base / 1.2, bezel=14 * zoom)
        # Words, in frame space.
        f = mfont(92, QFont.Weight.Light)
        kinetic(p, [("psst.", INK)], 640, 520, f, t, 0.15, t_out=0.95)
        rects = kinetic(p, [("over", INK), ("here.", INK)], 600, 520, f, t, 1.05, t_out=2.0)
        if 1.2 <= u < 2.25:
            if self.arrow is None:
                nx = W / 2 + (SW - 10 - fx) * s
                ny = H / 2 + (round(SH * 0.34) - fy) * s
                end = rects[-1].right() + 30
                marks = drawing.build_marks([Arrow(round(end / 2), 250, round((nx - 70) / 2), round(ny / 2 + 6))],
                                            QRectF(0, 0, W / 2, H / 2))
                self.arrow = (marks, drawing.plan_tour(marks, marks[0].points[0]))
            marks, tour = self.arrow
            p.save()
            p.scale(2, 2)
            p.setOpacity(1 - c01((u - 1.95) / 0.25))
            drawing.paint_marks(p, marks, (u - 1.25) * 1.2)
            p.restore()
        # Ctrl + Win, pressed.
        if 1.9 <= u < 3.7:
            pop = spring(u - 1.9, 18, 0.45)
            op = 1 - c01((u - 3.3) / 0.3)
            p.save()
            p.setOpacity(op)
            press = c01((u - 2.0) / 0.08)
            keycap(p, W / 2 - 120, 930, 190, 110, "Ctrl", press, scale=pop * 0.8, hot=press)
            keycap(p, W / 2 + 120, 930, 190, 110, "Win", press, win=True, scale=pop * 0.8, hot=press)
            p.restore()
        if u > 3.45:
            ev(t, "riser_hit", key="reveal-out")


class SceneTitle:
    """4-6 s. Coral. Ember lands, clixy rises, by Rayan Sameer."""
    t0, t1 = 4.0, 6.0

    def frame(self, p, t):
        u = t - self.t0
        p.fillRect(QRectF(0, 0, W, H), CORAL)
        g = QRadialGradient(QPointF(W / 2, H * 0.55), 900)
        g.setColorAt(0, QColor(255, 150, 80, 150))
        g.setColorAt(1, QColor(255, 150, 80, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(W / 2, H * 0.55), 900, 900)
        p.save()
        z = 1.0 + 0.035 * cubic_io(u / 2.0)
        p.translate(W / 2, H / 2)
        p.scale(z, z)
        p.translate(-W / 2, -H / 2)
        wf = mfont(330, QFont.Weight.ExtraLight, 94)
        ww = text_width("clixy", wf)
        cell = 21
        av = theme.avatar("ember")
        bw = (av.extent[2] - av.extent[0] + 1) * cell
        gap = 60
        x0 = W / 2 - (bw + gap + ww) / 2
        base = 610
        # Ember drops in and lands with a squash.
        land = 0.16
        if u < land:
            dy = -700 * (1 - (u / land) ** 2)
            sq = 0.0
        else:
            dy = 0.0
            sq = theme.landing_squash((u - land) * 0.9) * 1.6
            ev(t, "land", key="title-land")
        bh = (av.extent[3] - av.extent[1] + 1) * cell
        buddy(p, "ember", x0 + bw / 2, base - bh / 2 + 8 + dy, cell, u + 3, mood="happy", squash=sq)
        burst(p, "ember", x0 + bw / 2, base - bh * 0.4, (u - land) / 0.55, 9)
        # The wordmark, letter by letter.
        p.setFont(wf)
        x = x0 + bw + gap
        fm = QFontMetricsF(wf)
        p.save()
        p.setClipRect(QRectF(x - 20, base - fm.ascent() - 10, ww + 60, fm.ascent() + fm.descent() + 20))
        for i, ch in enumerate("clixy"):
            e = expo_out((u - 0.12 - i * 0.05) / 0.6)
            p.setPen(INK)
            p.drawText(QPointF(x, base + (1 - e) * 420), ch)
            x += fm.horizontalAdvance(ch)
        p.restore()
        sub = mfont(50, QFont.Weight.Light)
        kinetic(p, [("your", INK), ("pixel", INK), ("AI", INK), ("buddy", INK), ("for", INK), ("Windows.", INK)],
                W / 2, 780, sub, t, self.t0 + 0.55, stagger=0.04)
        by = mfont(34, QFont.Weight.Light, 100)
        byb = mfont(34, QFont.Weight.DemiBold, 100)
        kinetic(p, [("by", alpha(INK, 0.75))], W / 2 - text_width(" Rayan Sameer", byb) / 2, 860, by, t,
                self.t0 + 0.85)
        kinetic(p, [("Rayan", INK), ("Sameer", INK)], W / 2 + text_width("by ", by) / 2, 860, byb, t,
                self.t0 + 0.9)
        p.restore()
        flash(p, t, self.t0)


class SceneKeys:
    """6-8 s. Hold Ctrl + Win. Ask out loud. Let go."""
    t0, t1 = 6.0, 8.0

    def frame(self, p, t):
        u = t - self.t0
        p.fillRect(QRectF(0, 0, W, H), PAPER)
        pop1, pop2 = spring(u, 15, 0.4), spring(u - 0.08, 15, 0.4)
        press = c01((u - 0.22) / 0.07) * (1 - c01((u - 1.5) / 0.08))
        if u >= 0.22:
            ev(t, "key_down", key="keys-down")
        if u >= 1.5:
            ev(t, "key_up", key="keys-up")
        keycap(p, W / 2 - 230, 400, 380, 230, "Ctrl", press, scale=pop1, hot=press)
        p.setPen(alpha(INK, 0.5))
        p.setFont(mfont(90, QFont.Weight.ExtraLight))
        p.drawText(QRectF(W / 2 - 40, 330, 80, 140), Qt.AlignmentFlag.AlignCenter, "+")
        keycap(p, W / 2 + 230, 400, 380, 230, "Win", press, win=True, scale=pop2, hot=press)
        f = mfont(120, QFont.Weight.Light)
        kinetic(p, [("Hold.", INK)], W / 2, 790, f, t, self.t0 + 0.1, t_out=self.t0 + 0.92)
        kinetic(p, [("Ask", INK), ("out", INK), ("loud.", CORAL)], W / 2, 790, f, t, self.t0 + 1.0,
                t_out=self.t0 + 1.45)
        kinetic(p, [("Let", INK), ("go.", INK)], W / 2, 790, f, t, self.t0 + 1.5)
        # A little voice meter while you ask.
        if 0.95 < u < 1.5:
            op = c01((u - 0.95) / 0.1) * (1 - c01((u - 1.4) / 0.1))
            for i in range(15):
                lv = speech_level(u * 1.3 + i * 0.07)
                hgt = 10 + 70 * lv * (1 - abs(i - 7) / 9)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(alpha(CORAL, op))
                p.drawRoundedRect(QRectF(W / 2 - 15 * 14 / 2 + i * 14 - 4, 610 - hgt / 2, 8, hgt), 4, 4)


class SceneDemo:
    """8-15 s. It sees your screen. And draws right on it."""
    t0, t1 = 8.0, 15.0
    Q = "which month did best, and how do I share it?"

    def __init__(self):
        self.rig = None

    def frame(self, p, t):
        if self.rig is None:
            self.rig = Rig(sheet_scene)
            self.rig.mouse = QPointF(560, 470)
        r = self.rig
        u = t - self.t0
        isl, ov = r.isl, r.ov
        if 0.3 <= u < 2.0:
            if isl._state != "listening":
                isl.listening()
                ov.show_waveform()
            r.level = speech_level(u - 0.3)
        elif u >= 2.0 and not getattr(self, "asked", False):
            self.asked = True
            r.level = 0.0
            ov.hide_waveform()
            ov.show_spinner()
            isl.set_transcript(self.Q)
            isl.thinking()
        if u >= 2.45 and not getattr(self, "answered", False):
            self.answered = True
            ov.hide_spinner()
            isl.say("August did best, at $48.4k.")
            isl.highlight("August")
            aug, share = SHEET["aug"], SHEET["share"]
            ov.start_drawing([Box(round(aug.left() - 14), round(aug.top() - 34), round(aug.right() + 14),
                                  round(aug.bottom() + 4), "best month"),
                              Box(round(share.left() - 3), round(share.top() - 3), round(share.right() + 3),
                                  round(share.bottom() + 3), "share")],
                             avoid=[ISLAND_RECT], busy=r.busy)
            ev(t, "answer")
        if u >= 3.3 and not getattr(self, "said2", False):
            self.said2 = True
            isl.say("Hit Share at the top right to send it.")
            isl.highlight("Share")
            isl.set_meta("1.9s · $0.0004")
        # After the drawing, the mouse goes to Share: got it.
        if ov._tour is None and getattr(self, "answered", False):
            if not hasattr(self, "hover_t"):
                self.hover_t = t + 0.35
            share = SHEET["share"]
            m = cubic_io((t - self.hover_t) / 0.8)
            ex, ey = share.center().x() - 6, share.center().y() + 2
            r.mouse = QPointF((1 - m) ** 2 * 560 + 2 * (1 - m) * m * 560 + m * m * ex,
                              (1 - m) ** 2 * 470 + 2 * (1 - m) * m * 60 + m * m * ey)
            if m >= 1:
                ev(t, "got_it", key="demo-gotit")
        r.step(t)
        for mk in ov._marks:
            if mk.acked_at is not None:
                ev(t, "got_it", key="demo-ack")
        screen = r.screen()

        p.fillRect(QRectF(0, 0, W, H), INK)
        g = QRadialGradient(QPointF(W / 2, H * 0.9), 1100)
        g.setColorAt(0, QColor(255, 90, 61, 70))
        g.setColorAt(1, QColor(255, 90, 61, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(W / 2, H * 0.9), 1100, 1100)
        rise = expo_out(u / 0.7)
        z = 1.0 + 0.05 * cubic_io((u - 2.4) / 4.0)
        s = 1.2 * z
        fx, fy = lerp(SW / 2, 720, cubic_io((u - 2.4) / 4.0)), lerp(SH / 2, 330, cubic_io((u - 2.4) / 4.0))
        rect = QRectF(W / 2 - fx * s, 600 - fy * s + (1 - rise) * 120, SW * s, SH * s)
        p.save()
        p.setClipRect(QRectF(0, 168, W, H - 168))
        present(p, screen, rect, op=c01(u / 0.25))
        p.restore()
        p.fillRect(QRectF(0, 0, W, 168), INK)
        hf = mfont(66, QFont.Weight.Light)
        kinetic(p, [("It", WHITE), ("sees", WHITE), ("your", WHITE), ("screen.", WHITE)], W / 2, 112, hf, t,
                self.t0 + 0.05, t_out=self.t0 + 2.35)
        kinetic(p, [("And", WHITE), ("draws", CORAL), ("right", WHITE), ("on", WHITE), ("it.", WHITE)], W / 2, 112,
                hf, t, self.t0 + 2.45)
        caption(p, W / 2, 1000, self.Q, t, self.t0 + 0.35, self.t0 + 1.8, self.t0 + 2.3)


class SceneExactly:
    """15-17 s. Butter. "It draws exactly where you look." Ember loops "exactly"."""
    t0, t1 = 15.0, 17.0

    def __init__(self):
        self.marks = None

    def frame(self, p, t):
        u = t - self.t0
        p.fillRect(QRectF(0, 0, W, H), BUTTER)
        f = mfont(150, QFont.Weight.Light)
        l1 = kinetic(p, [("It", INK), ("draws", INK), ("exactly", INK)], W / 2, 470, f, t, self.t0 + 0.02)
        kinetic(p, [("where", INK), ("you", INK), ("look.", INK)], W / 2, 650, f, t, self.t0 + 0.12)
        word = l1[2]
        if self.marks is None:
            tb = QFontMetricsF(f).tightBoundingRect("exactly")
            tight = QRectF(word.left() + tb.left(), 470 + tb.top(), tb.width(), tb.height())
            hw = QRectF(tight.left() / 2 - 6, tight.top() / 2 - 2, tight.width() / 2 + 12, tight.height() / 2 + 4)
            self.marks = drawing.build_marks([Box(round(hw.left()), round(hw.top()), round(hw.right()),
                                                  round(hw.bottom()))], QRectF(0, 0, W / 2, H / 2))
            self.tour = drawing.plan_tour(self.marks, QPointF(880, 330))
        tt = (u - 0.3) * 1.55
        p.save()
        p.scale(2, 2)
        if tt > 0:
            drawing.paint_marks(p, self.marks, tt)
            pos, sc, mark = self.tour.pen(tt)
            if mark is not None:
                self.drawing = True
                EVENTS.append(dict(t=round(t, 4), kind="pen", x=pos.x(), y=pos.y()))
            landed = tt - self.tour.total
            sq = theme.landing_squash(landed) if landed > 0 else 0.0
            theme.paint_avatar(p, pos.x(), pos.y(), t=u, mood="happy", avatar_id="ember", scale=1.7 * sc, glow=True,
                               squash=sq)
            if landed > 0:
                ev(t, "pop", key="exactly-pop", pitch=2)
                theme.paint_burst(p, pos.x(), pos.y(), landed / 0.5, avatar_id="ember", cell=3)
        else:
            pos = QPointF(880, 330)
            fly = expo_out((u + 0.02) / 0.3)
            theme.paint_avatar(p, lerp(1000, pos.x(), fly), lerp(560, pos.y(), fly), t=u, mood="happy",
                               avatar_id="ember", scale=1.7, glow=True)
        p.restore()


class SceneGeometry:
    """17-23 s. Maths homework? It teaches on the figure itself."""
    t0, t1 = 17.0, 23.0
    Q1 = "what's the radius and circumference?"
    Q2 = "and the angles?"

    def __init__(self):
        self.rig = None
        self.state = 0

    def frame(self, p, t):
        if self.rig is None:
            self.rig = Rig(worksheet_scene, speed=1.35)
            self.rig.mouse = QPointF(560, 640)
        r = self.rig
        u = t - self.t0
        isl, ov = r.isl, r.ov
        if self.state == 0 and u >= 0.1:
            self.state = 1
            isl.listening()
            ov.show_waveform()
        if self.state == 1:
            r.level = speech_level(u)
            if u >= 0.95:
                self.state = 2
                r.level = 0
                ov.hide_waveform()
                ov.show_spinner()
                isl.set_transcript(self.Q1)
                isl.thinking()
        if self.state == 2 and u >= 1.25:
            self.state = 3
            ov.hide_spinner()
            c, rad = GEO["c"], GEO["r"]
            isl.say("The radius is half the diameter: 5 cm.")
            isl.say("Circumference is 2πr, about 31.4 cm.")
            isl.set_meta("2.0s · $0.0005")
            ov.start_drawing([Dot(round(c.x()), round(c.y()), "O"),
                              Line(round(c.x()), round(c.y()), round(c.x() + rad * 0.87), round(c.y() - rad * 0.5),
                                   "r = 5 cm"),
                              Ring(round(c.x()), round(c.y()), rad, "C ≈ 31.4 cm")], avoid=[ISLAND_RECT], busy=r.busy)
            ev(t, "answer")
        if self.state == 3 and ov._tour is None and u > 2.0:
            self.state = 4
            self.q2_t = u
            isl.listening()
            ov.show_waveform()
            self.ov2 = r.second_overlay()
            ov._pointer_visible = False
        if self.state == 4:
            r.level = speech_level(u - self.q2_t)
            if u - self.q2_t >= 0.55:
                self.state = 5
                r.level = 0
                ov.hide_waveform()
                isl.set_transcript(self.Q2)
                isl.thinking()
                A, B, C = GEO["A"], GEO["B"], GEO["C"]
                pt = lambda q: (round(q.x()), round(q.y()))
                isl.say("A and C are 66°, B is 48°.")
                isl.say("Together they make 180°.")
                isl.set_meta("1.7s · $0.0004")
                self.ov2._pointer_visible = True
                self.ov2._pointer_pos = ov._pointer_pos
                self.ov2.start_drawing([Angle(*pt(A), *pt(B), *pt(C), "66°"), Angle(*pt(B), *pt(C), *pt(A), "48°"),
                                        Angle(*pt(C), *pt(A), *pt(B), "66°")], avoid=[ISLAND_RECT], busy=r.busy)
                ev(t, "answer")
        r.step(t)
        screen = r.screen()
        p.fillRect(QRectF(0, 0, W, H), INK)
        g = QRadialGradient(QPointF(W / 2, H * 0.9), 1100)
        g.setColorAt(0, QColor(110, 210, 110, 60))
        g.setColorAt(1, QColor(110, 210, 110, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(W / 2, H * 0.9), 1100, 1100)
        punch = 1.0 + 0.03 * (1 - expo_out(u / 0.5))
        k = cubic_io((u - 1.2) / 4.5)
        s = 1.2 * punch * (1 + 0.05 * k)
        fx, fy = lerp(SW / 2, 620, k), lerp(SH / 2, 400, k)
        rect = QRectF(W / 2 - fx * s, 600 - fy * s, SW * s, SH * s)
        p.save()
        p.setClipRect(QRectF(0, 168, W, H - 168))
        present(p, screen, rect)
        p.restore()
        p.fillRect(QRectF(0, 0, W, 168), INK)
        hf = mfont(66, QFont.Weight.Light)
        kinetic(p, [("Maths", WHITE), ("homework?", WHITE)], W / 2, 112, hf, t, self.t0 + 0.02, t_out=self.t0 + 1.2)
        kinetic(p, [("It", WHITE), ("teaches", WHITE), ("on", WHITE), ("the", WHITE), ("figure.", CORAL)], W / 2, 112, hf,
                t, self.t0 + 1.3)
        caption(p, W / 2, 1000, self.Q1, t, self.t0 + 0.12, self.t0 + 0.85, self.t0 + 1.15)
        if hasattr(self, "q2_t"):
            q = self.t0 + self.q2_t
            caption(p, W / 2, 1000, self.Q2, t, q + 0.02, q + 0.4, q + 0.75)


BUDDIES = [("ember", "Ember", "a cozy campfire", QColor(255, 106, 31), INK),
           ("splash", "Splash", "a calm water drop", QColor(30, 150, 255), WHITE),
           ("sprout", "Sprout", "a fresh little leaf", QColor(46, 190, 106), INK),
           ("mochi", "Mochi", "a sleepy black cat", QColor(112, 84, 255), WHITE),
           ("twinkle", "Twinkle", "a dreamy star", QColor(255, 201, 51), INK)]


class SceneBuddies:
    """23-28 s. Say hi to your buddies. One per beat, then all five."""
    t0, t1 = 23.0, 28.0

    def frame(self, p, t):
        u = t - self.t0
        p.fillRect(QRectF(0, 0, W, H), INK)
        hf = mfont(120, QFont.Weight.Light)
        kinetic(p, [("Say", WHITE), ("hi", WHITE), ("to", WHITE), ("your", WHITE), ("buddies.", BUTTER)], W / 2, 590, hf,
                t, self.t0 + 0.0, t_out=self.t0 + 0.42, stagger=0.04)
        idx = int((u - 0.5) // 0.5) if u >= 0.5 else -1
        if 0 <= idx < 5:
            # Every earlier flood stays under the new one.
            for j in range(idx + 1):
                aid, name, tag, bg, fg = BUDDIES[j]
                start = 0.5 + j * 0.5
                rr = expo_out((u - start) / 0.32) * 1300
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawEllipse(QPointF(W / 2, 470), rr, rr)
            aid, name, tag, bg, fg = BUDDIES[idx]
            start = 0.5 + idx * 0.5
            ev(self.t0 + start, "pop", key=f"buddy-{idx}", pitch=idx)
            k = u - start
            sc = 0.25 + 0.75 * spring(k, 17, 0.38)
            cell = 20 * sc
            buddy(p, aid, W / 2, 440 - 30 * math.sin(math.pi * c01(k / 0.25)), cell, u + idx, mood="happy")
            burst(p, aid, W / 2, 440, k / 0.45, 10)
            nf = mfont(120, QFont.Weight.Light)
            kinetic(p, [(name, fg)], W / 2, 840, nf, t, self.t0 + start + 0.02, dur=0.4)
            kinetic(p, [(tag, alpha(fg, 0.7))], W / 2, 910, mfont(40, QFont.Weight.Light), t, self.t0 + start + 0.06,
                    dur=0.4)
        if u >= 3.0:
            k = u - 3.0
            rr = expo_out(k / 0.35) * 1300
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PAPER)
            p.drawEllipse(QPointF(W / 2, 470), rr, rr)
            kinetic(p, [("Five", INK), ("pixel", INK), ("buddies.", INK), ("Pick", INK), ("yours.", CORAL)], W / 2, 300,
                    mfont(84, QFont.Weight.Light), t, self.t0 + 3.05)
            for i, (aid, name, tag, bg, fg) in enumerate(BUDDIES):
                cx = W / 2 + (i - 2) * 330
                kk = k - 0.08 - i * 0.07
                sc = spring(kk, 17, 0.4)
                if sc <= 0:
                    continue
                ev(self.t0 + 3.08 + i * 0.07, "tick", key=f"line-{i}", pitch=i)
                hop = max(0.0, math.sin((u * 2 * math.pi) - i * 0.9)) * 18
                gl = QRadialGradient(QPointF(cx, 600), 170)
                hc = theme.avatar(aid).palette.halo
                gl.setColorAt(0, QColor(hc.red(), hc.green(), hc.blue(), 70))
                gl.setColorAt(1, QColor(hc.red(), hc.green(), hc.blue(), 0))
                p.setBrush(gl)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(cx, 600), 170, 170)
                buddy(p, aid, cx, 590 - hop, 12 * sc, u + i * 0.3, mood="happy")
                p.setPen(alpha(INK, c01(kk / 0.2)))
                p.setFont(mfont(36, QFont.Weight.Medium, 100))
                p.drawText(QRectF(cx - 150, 760, 300, 50), Qt.AlignmentFlag.AlignHCenter, name)


CARDS = [
    (CORAL, INK, [["Open", "source."]], "MIT licensed · fork it, mod it, ship it", "ember"),
    (BUTTER, INK, [["Fractions", "of", "a", "cent"], ["per", "question."]], "on Claude Haiku 5.5, with your own key",
     "twinkle"),
    (INK, WHITE, [["Your", "voice", "stays"], ["on", "your", "PC."]], "speech to text runs on-device", "mochi"),
    (PAPER, INK, [["No", "account."], ["No", "telemetry."]], "your key is encrypted by Windows", "splash"),
    (GREEN, INK, [["Free", "for", "Windows."]], "10 & 11 · one installer · no admin needed", "sprout"),
]


class SceneFlex:
    """28-33 s. Five cards, one a second."""
    t0, t1 = 28.0, 33.0

    def frame(self, p, t):
        u = t - self.t0
        i = min(4, int(u // 1.0))
        bg, fg, lines, sub, aid = CARDS[i]
        k = u - i
        ev(self.t0 + i, "hit", key=f"card-{i}", n=i)
        p.fillRect(QRectF(0, 0, W, H), bg)
        f = mfont(150, QFont.Weight.Light)
        n = len(lines)
        y0 = 560 - (n - 1) * 90
        z = 1.0 + 0.04 * (1 - expo_out(k / 0.5))
        p.save()
        p.translate(W / 2, H / 2)
        p.scale(z, z)
        p.translate(-W / 2, -H / 2)
        for li, words in enumerate(lines):
            kinetic(p, [(w, fg) for w in words], W / 2, y0 + li * 180, f, k, 0.0 + li * 0.08, stagger=0.045)
        p.restore()
        cf = mfont(34, QFont.Weight.Medium, 100)
        pop = spring(k - 0.25, 16, 0.45)
        if pop > 0:
            chip(p, W / 2, y0 + (n - 1) * 180 + 150, sub, cf, fg, border=alpha(fg, 0.6), scale=pop)
        # The card's buddy, peeking up from the bottom right.
        peek = expo_out((k - 0.1) / 0.45)
        buddy(p, aid, W - 210, H + 120 - peek * 250, 11, u * 1.2, mood="happy")


class SceneTease:
    """33-35 s. Coming soon: MCP access."""
    t0, t1 = 33.0, 35.0

    def frame(self, p, t):
        u = t - self.t0
        p.fillRect(QRectF(0, 0, W, H), INK)
        z = 1.0 + 0.05 * cubic_io(u / 2.0)
        p.save()
        p.translate(W / 2, H / 2)
        p.scale(z, z)
        p.translate(-W / 2, -H / 2)
        pop = spring(u - 0.05, 16, 0.45)
        if pop > 0:
            pulse = 0.55 + 0.45 * math.sin(u * 9)
            chip(p, W / 2, 380, "COMING SOON", mfont(28, QFont.Weight.DemiBold, 130), BUTTER,
                 border=alpha(BUTTER, 0.7), scale=pop, dot=alpha(BUTTER, pulse))
        kinetic(p, [("MCP", WHITE), ("access.", WHITE)], W / 2, 620, mfont(190, QFont.Weight.Light), t,
                self.t0 + 0.15, stagger=0.08, dur=0.8)
        kinetic(p, "Plug your buddy into your tools.", W / 2, 740, mfont(48, QFont.Weight.Light), t, self.t0 + 0.5,
                color=QColor(170, 170, 185))
        p.restore()
        # Twinkle drifts in, curious.
        k = expo_out((u - 0.6) / 0.9)
        buddy(p, "twinkle", lerp(W + 150, W - 260, k), 860 + 10 * math.sin(u * 3), 9, u, mood="thinking")


class SceneEnd:
    """35-40 s. The end card: logo, clixy by Rayan Sameer, where to get it.
    The real island on the right edge answers who made it."""
    t0, t1 = 35.0, 40.0

    def __init__(self):
        self.isl = None
        self.logo = theme.logo_image(440)

    def frame(self, p, t):
        u = t - self.t0
        if self.isl is None:
            self.isl = SideIsland(side="right")
            self.isl._tick.stop()
            self.isl._update_near = lambda: None
            self.isl.model_name = "Haiku 5.5"
            self.last = t
        isl = self.isl
        if u >= 0.55 and isl._state == "idle" and not getattr(self, "asked", False):
            isl.listening()
        if 0.55 <= u < 1.35:
            isl.set_level(speech_level(u))
        if u >= 1.35 and not getattr(self, "asked", False):
            self.asked = True
            isl.set_level(0)
            isl.set_transcript("who made you?")
            isl.thinking()
        if u >= 1.7 and not getattr(self, "answered", False):
            self.answered = True
            isl.say("Rayan Sameer made me.")
            isl.say("I'm free and open source.")
            isl.say("Say hi on GitHub!")
            isl.highlight("Rayan Sameer")
            isl.highlight("GitHub")
            isl.set_meta("1.2s · $0.0002")
        isl._animate()
        p.fillRect(QRectF(0, 0, W, H), PAPER)
        g = QRadialGradient(QPointF(560, 470), 700)
        g.setColorAt(0, QColor(255, 120, 60, 55))
        g.setColorAt(1, QColor(255, 120, 60, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(560, 470), 700, 700)
        # Logo tile.
        pop = spring(u - 0.02, 14, 0.4)
        p.save()
        p.translate(330, 430)
        p.scale(pop, pop)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(-150, -150, 300, 300), self.logo)
        p.restore()
        ev(t, "land", key="end-land")
        wf = mfont(250, QFont.Weight.ExtraLight, 94)
        kinetic(p, [("clixy", INK)], 530, 515, wf, t, self.t0 + 0.08, align="left", dur=0.8)
        by, byb = mfont(40, QFont.Weight.Light, 100), mfont(40, QFont.Weight.DemiBold, 100)
        kinetic(p, [("by", alpha(INK, 0.7))], 540, 600, by, t, self.t0 + 0.3, align="left")
        kinetic(p, [("Rayan", INK), ("Sameer", INK)], 540 + text_width("by ", by), 600, byb, t, self.t0 + 0.34,
                align="left")
        # Call to action.
        cta = spring(u - 0.6, 14, 0.5)
        if cta > 0:
            f1 = mfont(36, QFont.Weight.DemiBold, 100)
            f2 = jfont(30)
            label = "Get it free"
            url = "github.com/RaySam07/clixy"
            w = text_width(label, f1) + text_width(url, f2) + 120
            p.save()
            p.translate(180 + w / 2, 760)
            p.scale(cta, cta)
            rr = QRectF(-w / 2, -44, w, 88)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(INK)
            p.drawRoundedRect(rr, 44, 44)
            p.setPen(WHITE)
            p.setFont(f1)
            fm1 = QFontMetricsF(f1)
            p.drawText(QPointF(rr.left() + 44, fm1.ascent() / 2 - fm1.descent() / 2 + 2), label)
            p.setPen(BUTTER)
            p.setFont(f2)
            fm2 = QFontMetricsF(f2)
            p.drawText(QPointF(rr.left() + 76 + text_width(label, f1), fm2.ascent() / 2 - fm2.descent() / 2 + 2), url)
            p.restore()
        kinetic(p, "Windows 10 & 11  ·  open source (MIT)  ·  bring your own Claude key", 180, 900,
                mfont(30, QFont.Weight.Light, 100), t, self.t0 + 0.9, align="left", color=alpha(INK, 0.65))
        # The island, at twice its size, on the frame's right edge.
        pm = isl.grab()
        p.drawPixmap(QRectF(W - 800, H * 0.42 - 340, 800, 680), pm, QRectF(0, 0, pm.width(), pm.height()))
        flash(p, t, self.t0, 0.3)


SCENES = [SceneReveal(), SceneTitle(), SceneKeys(), SceneDemo(), SceneExactly(), SceneGeometry(), SceneBuddies(),
          SceneFlex(), SceneTease(), SceneEnd()]


def render(out, first=0.0, last=DUR, stills=None):
    n = int(round(DUR * FPS))
    ff = None
    if stills is None:
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}",
                               "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "15",
                               "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart", out],
                              stdin=subprocess.PIPE)
    frame = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    t_start = _time.perf_counter()
    for i in range(n):
        t = i / FPS
        clock[0] = 1000.0 + t
        scene = next(s for s in SCENES if s.t0 <= t < s.t1)
        want = stills is None or any(abs(t - s) < 0.5 / FPS for s in stills)
        if t < first - 1e-6 and scene.t1 <= first:
            continue
        if t > last:
            break
        p = QPainter(frame)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        scene.frame(p, t)
        p.end()
        if ff is not None:
            ptr = frame.constBits()
            ptr.setsize(frame.sizeInBytes())
            ff.stdin.write(bytes(ptr))
        elif want:
            frame.save(f"{out}/still_{t:06.2f}.png")
        if i % 300 == 0:
            print(f"  {t:5.1f}s  {(_time.perf_counter() - t_start):.0f}s elapsed", flush=True)
    if ff is not None:
        ff.stdin.close()
        ff.wait()


if __name__ == "__main__":
    out, events = sys.argv[1], sys.argv[2]
    stills = None
    if len(sys.argv) > 3:
        stills = [float(x) for x in sys.argv[3].split(",")]
    render(out, stills=stills)
    # Pen scribbles from the demos' drawing tours.
    for s in SCENES:
        rig = getattr(s, "rig", None)
        if rig is not None:
            for (tt, x, y, drawing_now) in rig.drawing_log:
                if drawing_now:
                    EVENTS.append(dict(t=round(tt, 4), kind="pen", x=x, y=y))
    json.dump(sorted(EVENTS, key=lambda e: e["t"]), open(events, "w"), indent=0)
    print("events", len(EVENTS))
