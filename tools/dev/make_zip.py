"""Build Clixy.exe from this folder and pack it as a ready-to-run zip in Downloads.

Run tools\\dev\\Make-Clixy-Zip.bat (or this file with the dev environment's
Python). The zip is named by day and letter, like Clixy-2026.11.04-a.zip, and
holds the Clixy folder: unzip it anywhere and run Clixy\\Clixy.exe.
``--install`` also puts the build where the installed app lives
(%LOCALAPPDATA%\\Programs\\Clixy), so the Start menu and desktop shortcuts
open the new one.

Builds in %USERPROFILE%\\.venvs\\clixy-build, a short path, because Windows'
260-character limit breaks PyQt6 under a long project path.
"""
from __future__ import annotations

import datetime
import os
import string
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path.home() / ".venvs" / "clixy-build"
DOWNLOADS = Path.home() / "Downloads"
INSTALLED = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "Clixy"


def next_zip_name(folder: Path, day: datetime.date) -> Path:
    """The first free Clixy-<day>-<letter>.zip in ``folder``."""
    stamp = day.strftime("%Y.%m.%d")
    for letter in string.ascii_lowercase:
        path = folder / f"Clixy-{stamp}-{letter}.zip"
        if not path.exists():
            return path
    raise SystemExit(f"26 zips already made on {stamp}; move some out of {folder}.")


def build() -> Path:
    print("Building Clixy.exe (2-5 minutes, quiet while it works)...", flush=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "clixy.spec", "--noconfirm", "--log-level", "WARN",
         "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work")],
        cwd=ROOT, check=True,
    )
    return BUILD / "dist" / "Clixy"


def pack(app: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                zf.write(path, Path("Clixy") / path.relative_to(app))


def install(app: Path) -> None:
    """Close the running Clixy.exe and mirror the build into the installed folder."""
    subprocess.run(["taskkill", "/im", "Clixy.exe", "/f"], capture_output=True)
    code = subprocess.run(["robocopy", str(app), str(INSTALLED), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]).returncode
    if code >= 8:  # robocopy: below 8 means copied fine
        raise SystemExit(f"Couldn't copy into {INSTALLED} (robocopy {code}).")
    print(f"Installed to {INSTALLED}.")


def main() -> int:
    app = build()
    target = next_zip_name(DOWNLOADS, datetime.date.today())
    pack(app, target)
    print(f"Made {target} ({target.stat().st_size / 1e6:.0f} MB). Unzip it and run Clixy\\Clixy.exe.")
    if "--install" in sys.argv[1:]:
        install(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
