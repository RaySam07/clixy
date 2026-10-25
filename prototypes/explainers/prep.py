import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QRadialGradient
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import theme
theme.load_fonts()
out = sys.argv[1]
# The page loads Manrope from next to itself.
import shutil
from pathlib import Path
_fonts = Path(__file__).resolve().parents[2] / "assets" / "fonts"
for ttf in _fonts.glob("Manrope-*.ttf"):
    shutil.copy(ttf, Path(out) / ttf.name)
# Ember, big and crisp, on transparent.
for t, name in ((0.2, "ember.png"), (0.55, "ember2.png")):
    img = QImage(220, 260, QImage.Format.Format_ARGB32_Premultiplied); img.fill(0)
    p = QPainter(img)
    theme.paint_avatar_in(p, QRectF(0, 0, 220, 260), t=t, mood="happy", avatar_id="ember", scale=7, glow=True)
    p.end(); img.save(f"{out}/{name}")
# Dimmed screens: an article in a browser.
def backdrop(title, name):
    W, H = 1920, 1080
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.fillRect(QRectF(0, 0, W, H), QColor(236, 238, 244))
    p.fillRect(QRectF(0, 0, W, 64), QColor(250, 250, 252))
    f = theme.ui_font(10); f.setPixelSize(20); p.setFont(f); p.setPen(QColor(120, 120, 130))
    p.drawText(QPointF(40, 41), f"{title} - Encyclopedia")
    p.fillRect(QRectF(240, 110, 1440, 940), QColor(255, 255, 255))
    p.setPen(QColor(205, 205, 214))
    for i in range(22):
        y = 260 + i * 34
        p.drawLine(QPointF(300, y), QPointF(1620 - (i % 4) * 110, y))
    p.fillRect(QRectF(0, 0, W, H), QColor(10, 10, 18, 222))
    g = QRadialGradient(QPointF(W / 2, H / 2 + 40), 820)
    g.setColorAt(0, QColor(255, 140, 70, 42)); g.setColorAt(1, QColor(255, 140, 70, 0))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawEllipse(QPointF(W / 2, H / 2 + 40), 820, 820)
    p.end(); img.save(f"{out}/{name}")
backdrop("Bicycle", "backdrop_bicycle.png")
backdrop("Water cycle", "backdrop_water.png")
backdrop("Notes", "backdrop_plain.png")
print("ok")
