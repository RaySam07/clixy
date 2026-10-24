"""Regenerate assets/clicky_tray.ico (the EXE's icon) from an avatar.

The running app draws its tray and window icons live from theme.py, so they
follow the chosen avatar; the EXE file itself needs a static .ico. Run from
the repo root:

    python tools/make_icon.py            # Ember, the default avatar
    python tools/make_icon.py mochi
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


def main(avatar_id: str = theme.DEFAULT_AVATAR) -> None:
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    frames = []
    for size in SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        theme.avatar_image(avatar_id, size).save(buffer, "PNG")
        frames.append(Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA"))
    out = ROOT / "assets" / "clicky_tray.ico"
    frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    print(f"wrote {out}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
