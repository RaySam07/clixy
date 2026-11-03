"""Design mockups: Clixy's new professional blue notch, every state, and the new cursor."""
import math, os, sys
repo, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import theme; theme.apply(app)
from widgets import _paint_windows_logo

S = 2.0
W, H = 1200, 200
CYAN, BLUE, NAVY = QColor(62, 224, 255), QColor(47, 111, 224), QColor(11, 22, 48)
WHITE, MUTED = QColor(255, 255, 255), QColor(150, 160, 182)

def backdrop(p, h=H, light=False):
    g = QLinearGradient(0, 0, W, h)
    if light:
        g.setColorAt(0, QColor(236, 239, 245)); g.setColorAt(1, QColor(222, 228, 238))
    else:
        g.setColorAt(0, QColor(40, 54, 84)); g.setColorAt(1, QColor(88, 110, 150))
    p.fillRect(QRectF(0, 0, W, h), g)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 235 if light else 30))
    p.drawRoundedRect(QRectF(60, 52, W - 120, h + 40), 12, 12)
    p.setBrush(QColor(0, 0, 0, 16 if light else 22))
    for i, y in enumerate(range(92, h, 22)):
        p.drawRoundedRect(QRectF(100, y, 300 + (i * 131) % 380, 9), 4, 4)

def notch(p, w, h, radius=10):
    rect = QRectF(W / 2 - w / 2, 0, w, h)
    path = QPainterPath(); path.addRoundedRect(QRectF(rect.x(), -radius, w, h + radius), radius, radius)
    for k in range(1, 8):  # soft blue glow
        p.setPen(QPen(QColor(47, 111, 224, 9), k * 2.4)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path.translated(0, 2))
    g = QLinearGradient(rect.left(), 0, rect.right(), h)
    g.setColorAt(0, QColor(4, 6, 12)); g.setColorAt(0.6, QColor(6, 10, 22)); g.setColorAt(1, NAVY)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawPath(path)
    edge = QLinearGradient(rect.left(), 0, rect.right(), 0)
    edge.setColorAt(0, QColor(47, 111, 224, 0)); edge.setColorAt(0.5, QColor(62, 160, 255, 120)); edge.setColorAt(1, QColor(47, 111, 224, 0))
    p.setPen(QPen(edge, 1.2)); p.drawLine(QPointF(rect.left() + radius, h - 0.6), QPointF(rect.right() - radius, h - 0.6))
    return rect

def glyph(p, c, r=8.0):
    """The Clixy mark: a soft four-point spark in blue-white, no fire."""
    g = QRadialGradient(c, r * 1.4); g.setColorAt(0, WHITE); g.setColorAt(0.55, CYAN); g.setColorAt(1, BLUE)
    path = QPainterPath(QPointF(c.x(), c.y() - r))
    for ang in range(4):
        a = math.radians(90 * ang)
        tip = QPointF(c.x() + r * math.sin(a + math.pi / 2), c.y() - r * math.cos(a + math.pi / 2))
        mid = QPointF(c.x() + r * 0.28 * math.sin(a + math.pi / 4), c.y() - r * 0.28 * math.cos(a + math.pi / 4))
        path.quadTo(mid, tip)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawPath(path)

def voice_pill(p, c, w, h, levels):
    pill = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
    for k in range(1, 6):
        halo = QColor(62, 190, 255, int(26 * (1 - k / 6)))
        p.setBrush(halo); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(pill.adjusted(-k * 2.2, -k * 2.2, k * 2.2, k * 2.2), h / 2 + k * 2.2, h / 2 + k * 2.2)
    g = QLinearGradient(pill.left(), 0, pill.right(), 0); g.setColorAt(0, CYAN); g.setColorAt(1, BLUE)
    p.setBrush(g); p.drawRoundedRect(pill, h / 2, h / 2)
    p.setBrush(WHITE)
    for k, lv in enumerate(levels):
        r = h * (0.1 + 0.16 * lv)
        p.drawEllipse(QPointF(pill.left() + w / (len(levels) + 1) * (k + 1), c.y()), r, r)

def label(p, title, sub, y=H - 30):
    p.setPen(QColor(255, 255, 255)); p.setFont(theme.ui_font(15, QFont.Weight.Light)); p.drawText(QPointF(70, y), title)
    p.setPen(QColor(220, 228, 240)); p.setFont(theme.ui_font(9.5)); p.drawText(QPointF(70, y + 20), sub)

def panel(draw, title, sub, h=H, light=False):
    img = QImage(int(W * S), int(h * S), QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.scale(S, S)
    backdrop(p, h, light); draw(p); 
    if title:
        label(p, title, sub, h - 34)
    p.end(); return img

def resting(p):
    r = notch(p, 156, 30)
    glyph(p, QPointF(r.left() + 20, 15), 7.5)
    voice_pill(p, QPointF(r.right() - 26, 15), 30, 14, [0.3, 0.3, 0.3])

def hello(p):
    r = notch(p, 260, 34)
    glyph(p, QPointF(r.left() + 22, 17), 8)
    p.setFont(theme.ui_font(12, QFont.Weight.DemiBold)); p.setPen(WHITE)
    p.drawText(QPointF(r.left() + 42, 22), "Hey Rayan")
    w = p.fontMetrics().horizontalAdvance("Hey Rayan"); p.setPen(CYAN); p.drawText(QPointF(r.left() + 42 + w, 22), "!")
    voice_pill(p, QPointF(r.right() - 30, 17), 38, 18, [0.3, 0.45, 0.3])

def listening(p):
    r = notch(p, 340, 36)
    glyph(p, QPointF(r.left() + 22, 18), 8)
    p.setFont(theme.ui_font(11, QFont.Weight.DemiBold)); p.setPen(WHITE)
    p.drawText(QRectF(r.left(), 0, r.width(), 36), Qt.AlignmentFlag.AlignCenter, "Listening")
    voice_pill(p, QPointF(r.right() - 34, 18), 48, 22, [0.5, 1.0, 0.75, 0.35])

def thinking(p):
    r = notch(p, 340, 36)
    glyph(p, QPointF(r.left() + 22, 18), 8)
    p.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
    w = p.fontMetrics().horizontalAdvance("Thinking"); x = r.center().x() - w / 2
    g = QLinearGradient(x, 0, x + w, 0); g.setColorAt(0, MUTED); g.setColorAt(0.55, WHITE); g.setColorAt(1, MUTED)
    p.setPen(QPen(g, 1)); p.drawText(QRectF(r.left(), 0, r.width(), 36), Qt.AlignmentFlag.AlignCenter, "Thinking")
    voice_pill(p, QPointF(r.right() - 34, 18), 48, 22, [0.2, 0.4, 0.2, 0.1])

def answer(p):
    r = notch(p, 460, 158, radius=14)
    x0, y = r.left() + 24, 22
    glyph(p, QPointF(x0 + 8, y + 2), 8)
    p.setPen(WHITE); p.setFont(theme.ui_font(10.5, QFont.Weight.DemiBold)); p.drawText(QPointF(x0 + 24, y + 7), "Clixy")
    p.setPen(MUTED); p.setFont(theme.mono_font(8.2)); p.drawText(QPointF(r.right() - 128, y + 6), "1.6s · $0.008")
    p.setFont(theme.ui_font(9.5, QFont.Weight.Light)); p.drawText(QPointF(x0 + 24, y + 26), "“how do I project my laptop to the projector”")
    p.setPen(WHITE); p.setFont(theme.display_font(13.5))
    p.drawText(QRectF(x0, y + 44, r.width() - 48, 80), Qt.TextFlag.TextWordWrap,
               "Press the Windows key and P together, then pick Duplicate so the projector shows your screen.")

def cursor(p):
    # The new pointer: a sleek blue arrow with a glow, pointing at a button, its name beside it.
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255)); p.drawRoundedRect(QRectF(120, 70, 420, 120), 12, 12)
    p.setBrush(QColor(232, 236, 244)); p.drawRoundedRect(QRectF(150, 110, 150, 40), 8, 8)
    p.setPen(QColor(40, 44, 56)); p.setFont(theme.ui_font(10.5, QFont.Weight.DemiBold)); p.drawText(QRectF(150, 110, 150, 40), Qt.AlignmentFlag.AlignCenter, "Display settings")
    tip = QPointF(312, 138)
    arrow = QPainterPath(tip)
    for dx, dy in ((0, 26), (7, 19), (13, 30), (18, 28), (12, 17), (22, 17)):
        arrow.lineTo(QPointF(tip.x() + dx, tip.y() + dy))
    arrow.closeSubpath()
    for k in range(1, 7):
        p.setPen(QPen(QColor(62, 170, 255, int(30 * (1 - k / 7))), k * 2.2)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(arrow)
    g = QLinearGradient(tip, QPointF(tip.x() + 18, tip.y() + 30)); g.setColorAt(0, CYAN); g.setColorAt(1, BLUE)
    p.setPen(QPen(WHITE, 1.6)); p.setBrush(g); p.drawPath(arrow)
    chip = QRectF(tip.x() + 26, tip.y() + 26, 132, 26)
    p.setPen(Qt.PenStyle.NoPen); g2 = QLinearGradient(chip.topLeft(), chip.topRight()); g2.setColorAt(0, QColor(8, 14, 30)); g2.setColorAt(1, NAVY)
    p.setBrush(g2); p.drawRoundedRect(chip, 8, 8)
    p.setPen(WHITE); p.setFont(theme.ui_font(9.5, QFont.Weight.DemiBold)); p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "Display settings")
    # A drawn box in the new blue, for comparison.
    box = QRectF(700, 96, 300, 56)
    p.setPen(QPen(QColor(62, 170, 255, 70), 7)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRoundedRect(box, 12, 12)
    p.setPen(QPen(BLUE, 2.6)); p.drawRoundedRect(box, 12, 12)
    p.setPen(QColor(40, 44, 56)); p.setFont(theme.ui_font(11)); p.drawText(box, Qt.AlignmentFlag.AlignCenter, "Extend")

panels = [
    panel(resting, "1 · Resting", "a longer, squarer black tab with a deep-blue gradient: the Clixy spark and a small cyan pill. No fire."),
    panel(hello, "2 · Hello", "\"Hey Rayan!\" when Clixy starts, then back to resting"),
    panel(listening, "3 · Listening", "the word, centred; the cyan-to-blue pill's dots jump with your voice"),
    panel(thinking, "4 · Thinking", "a soft shimmer across the word; the dots ripple"),
    panel(answer, "5 · Answer", "drops down from the notch on navy-black glass, then slides back up", h=250),
    panel(cursor, "6 · The new cursor and marks", "a sleek blue pointer with a glow and a name tag; marks in the same blue", h=250, light=True),
]
total = sum(i.height() for i in panels) + 10 * (len(panels) - 1)
sheet = QImage(int(W * S), total, QImage.Format.Format_ARGB32_Premultiplied); sheet.fill(QColor(255, 255, 255))
p = QPainter(sheet); y = 0
for im in panels:
    p.drawImage(0, y, im); y += im.height() + 10
p.end(); sheet.save(out); print("ok")
