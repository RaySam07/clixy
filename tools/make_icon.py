"""Regenerate assets/clicky_tray.ico (the EXE's icon) and
assets/clicky-logo.png.

The running app draws its icons live from theme.py: the mark for windows,
your buddy in the tray. The EXE file itself needs a static .ico. Run from
the repo root:

    python tools/make_icon.py            # the mark (butter C on ink)
    python tools/make_icon.py mochi      # a buddy instead
"""
import io
import sys
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QGuiApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import theme  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
_APP = None  # Qt needs an application object alive while painting


def main(avatar_id: str | None = None) -> None:
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    frames = []
    for size in SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image = theme.avatar_image(avatar_id, size) if avatar_id else theme.app_icon_image(size)
        image.save(buffer, "PNG")
        frames.append(Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA"))
    out = ROOT / "assets" / "clicky_tray.ico"
    frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    print(f"wrote {out}")
    if avatar_id is None:
        logo = ROOT / "assets" / "clicky-logo.png"
        theme.app_icon_image(360).save(str(logo))
        print(f"wrote {logo}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
