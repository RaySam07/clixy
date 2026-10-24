"""X (Twitter) header for Clixy, 1500x500, from the real app code."""
import sys, time as _t, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
clock = [100.0]
_t.monotonic = lambda: clock[0]
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter, QColor, QFont, QFontMetricsF, QRadialGradient
from PyQt6.QtCore import QRectF, QPointF, Qt
app = QApplication([])
import keyring; keyring.get_password = lambda s, n: None; keyring.set_password = lambda s, n, v: None
import theme; theme.apply(app)
import overlay; overlay.apply_clickthrough_styles = lambda h: None
from island import SideIsland
import drawing

variant = sys.argv[1]
out = sys.argv[2]
W, H = 1500, 500
INK = QColor(10, 10, 18); CORAL = QColor(255, 90, 61); PAPER = QColor(245, 242, 234); BUTTER = QColor(255, 210, 63)

def font(px, weight=QFont.Weight.Light, spacing=97, family=None):
    f = theme.ui_font(10, weight) if family is None else QFont(family)
    if family is not None:
        f.setWeight(weight)
    f.setPixelSize(int(px)); f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f

# The real island, listening, with Ember in it.
isl = SideIsland(side="right"); isl._tick.stop(); isl._update_near = lambda: None
isl.model_name = "Haiku 5.5"
isl.listening()
for i in range(160):
    clock[0] += 1 / 60
    isl.set_level(0.55 + 0.35 * math.sin(i * 0.31) * math.sin(i * 0.07))
    isl._animate()
island = isl.grab()

img = QImage(W * 2, H * 2, QImage.Format.Format_ARGB32_Premultiplied)
img.setDevicePixelRatio(2)
p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

bg, fg, sub = {"coral": (CORAL, INK, QColor(10, 10, 18, 200)),
               "ink": (INK, QColor(255, 255, 255), QColor(255, 255, 255, 190)),
               "paper": (PAPER, INK, QColor(10, 10, 18, 190))}[variant]
p.fillRect(QRectF(0, 0, W, H), bg)
if variant == "ink":
    for cx, cy, r, col in ((1040, 330, 420, QColor(255, 110, 30, 60)), (300, -40, 380, QColor(255, 90, 61, 34))):
        g = QRadialGradient(QPointF(cx, cy), r); g.setColorAt(0, col); g.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
        p.setBrush(g); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QPointF(cx, cy), r, r)
elif variant == "coral":
    g = QRadialGradient(QPointF(1050, 300), 520); g.setColorAt(0, QColor(255, 150, 70, 120)); g.setColorAt(1, QColor(255, 150, 70, 0))
    p.setBrush(g); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QPointF(1050, 300), 520, 520)

# Wordmark + lines, kept in the top two thirds: X puts the profile photo
# over the bottom left.
x0 = 92
p.setPen(fg)
wf = font(170, QFont.Weight.ExtraLight, 95)
p.setFont(wf); p.drawText(QPointF(x0 - 8, 168), "clixy")
ww = QFontMetricsF(wf).horizontalAdvance("clixy")
p.setFont(font(27, QFont.Weight.Light)); p.setPen(sub)
p.drawText(QPointF(x0 + ww + 22, 168), "by")
p.setFont(font(27, QFont.Weight.DemiBold)); p.setPen(fg)
p.drawText(QPointF(x0 + ww + 22 + QFontMetricsF(font(27)).horizontalAdvance("by "), 168), "Rayan Sameer")

lf = font(34, QFont.Weight.Light)
fm = QFontMetricsF(lf)
base = 262
p.setFont(lf); p.setPen(fg)
head = "Hold Ctrl + Win. Ask out loud."
p.drawText(QPointF(x0, base), head)
tail_x = x0 + fm.horizontalAdvance(head) + 34
p.drawText(QPointF(tail_x, base), "It draws.")
tw = fm.horizontalAdvance("It draws.")
word = QRectF(tail_x, base - fm.ascent() + 4, tw, fm.ascent())

# A loop round "It draws." from the real drawing engine.
from annotations import Box
marks = drawing.build_marks([Box(word.left() - 2, word.top() - 2, word.right() + 2, word.bottom() + 8)], QRectF(0, 0, W, H))
for m in marks:
    m.start, m.duration = 0.0, 0.5
drawing.paint_marks(p, marks, 10.0, now=0.0)

p.setFont(font(19, QFont.Weight.Normal, 100, "JetBrains Mono")); p.setPen(sub)
p.drawText(QRectF(560, 410, 820, 30), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
           "free · open source · github.com/RaySam07/clixy")

# Buddies, on the right, on a little shelf above the notch's level.
buddies = ["ember", "splash", "sprout", "mochi", "twinkle"]
bx, by = 800, 232
for i, aid in enumerate(buddies):
    cx = bx + i * 118
    pal = theme.avatar(aid).palette
    c = pal.halo
    g = QRadialGradient(QPointF(cx, by), 80); g.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 110 if variant == "ink" else 70)); g.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
    p.setBrush(g); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QPointF(cx, by), 80, 80)
    theme.paint_avatar_in(p, QRectF(cx - 60, by - 64, 120, 120), t=0.3 + i * 0.37, mood="happy", avatar_id=aid, scale=2.5, device_pixel_ratio=2)
    p.setFont(font(15, QFont.Weight.Medium, 100)); p.setPen(sub)
    p.drawText(QRectF(cx - 60, by + 56, 120, 24), Qt.AlignmentFlag.AlignHCenter, theme.avatar(aid).name)

# The notch on the banner's right edge.
p.drawPixmap(QRectF(W - 400, 175 - 170, 400, 340), island, QRectF(0, 0, island.width(), island.height()))
p.end()
img.save(f"{out}/x-banner-{variant}@2x.png")
img.setDevicePixelRatio(1)
img.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).save(f"{out}/x-banner-{variant}.png")
print("ok", variant)
