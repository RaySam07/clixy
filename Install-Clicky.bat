@echo off
setlocal EnableExtensions
title Clicky installer
cd /d "%~dp0"

echo.
echo   Clicky - your private build
echo   ===========================
echo   Builds Clicky.exe from this folder and adds it to your Start menu.
echo   About 5 minutes the first time. The only downloads are Python and
echo   Clicky's packages; your keys are never part of this.
echo.
echo   Tip: if Abhishek's "Clicky Windows" is installed, uninstall it first
echo   (Settings ^> Apps) so two Clickys don't run at once.
echo.

rem --- 1. Python 3.13 ------------------------------------------------------------
call :findpy
if not defined PYEXE (
  where winget >nul 2>&1
  if not errorlevel 1 (
    echo   Python 3.13 isn't installed. Installing it now with winget...
    winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
    call :findpy
  )
)
if not defined PYEXE (
  echo.
  echo   Couldn't find Python 3.13. Install it from https://www.python.org/downloads/
  echo   then double-click this file again.
  goto :fail
)
echo   Using Python: %PYEXE%

rem --- 2. A private environment with Clicky's packages ------------------------
if not exist ".venv\Scripts\python.exe" (
  echo   [1/4] Making a private Python environment ^(up to a minute^)...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 goto :fail
)
set "VPY=%~dp0.venv\Scripts\python.exe"
echo   [2/4] Installing Clicky's packages: the long step, 5-10 minutes and about 1 GB
echo         the first time. Lines scroll by as each package arrives; that's normal.
echo         Don't click inside this window: that pauses it ^(press Esc if the title says Select^).
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt "pyinstaller>=6.20" --disable-pip-version-check --progress-bar on
if errorlevel 1 goto :fail

rem --- 3. Build ----------------------------------------------------------------------
tasklist /fi "imagename eq Clicky.exe" 2>nul | find /i "Clicky.exe" >nul
if not errorlevel 1 (
  echo   Closing the Clicky that's running...
  taskkill /im Clicky.exe /f >nul 2>&1
  timeout /t 2 /nobreak >nul
)
echo   [3/4] Building Clicky.exe ^(2-5 minutes, quiet while it works^)...
"%VPY%" -m PyInstaller clicky.spec --noconfirm --log-level WARN
if errorlevel 1 goto :fail

rem --- 4. Install for this Windows user, with shortcuts -------------------------
set "DEST=%LOCALAPPDATA%\Programs\Clicky"
echo   [4/4] Installing to %DEST% ...
robocopy "dist\Clicky" "%DEST%" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail
call :shortcut Programs
call :shortcut Desktop

echo.
choice /c YN /n /m "  Start Clicky when you sign in to Windows? [Y/N] "
if errorlevel 2 (
  del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Clicky.lnk" >nul 2>&1
) else (
  call :shortcut Startup
)

echo.
echo   Done. Starting Clicky...
start "" "%DEST%\Clicky.exe"
echo   Hold Ctrl + Win, ask out loud, let go.
echo.
pause
exit /b 0

rem --- Helpers -----------------------------------------------------------------------
:findpy
set "PYEXE="
for /f "delims=" %%i in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python313\python.exe" set "PYEXE=%ProgramFiles%\Python313\python.exe"
exit /b 0

:shortcut
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('%1') + '\Clicky.lnk');" ^
  "$s.TargetPath = '%DEST%\Clicky.exe'; $s.WorkingDirectory = '%DEST%'; $s.IconLocation = '%DEST%\Clicky.exe,0'; $s.Save()"
exit /b 0

:fail
echo.
echo   Something went wrong (see above). Copy the last few lines and send them to Claude.
echo.
pause
exit /b 1
