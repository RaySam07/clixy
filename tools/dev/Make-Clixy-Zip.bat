@echo off
rem Clixy zip: builds Clixy.exe from this folder and packs it in Downloads as
rem Clixy-<date>-<letter>.zip (make_zip.py). Add --install to also replace the
rem installed app. Uses the environment Start-Clixy-Dev.bat sets up.
setlocal
cd /d "%~dp0..\.."
if not defined CLIXY_VENV set "CLIXY_VENV=%USERPROFILE%\.venvs\clixy"

if not exist "%CLIXY_VENV%\Scripts\python.exe" (
  echo Run tools\dev\Start-Clixy-Dev.bat once first: it sets up Python for Clixy.
  pause
  exit /b 1
)
"%CLIXY_VENV%\Scripts\python.exe" -m PyInstaller --version >nul 2>nul || "%CLIXY_VENV%\Scripts\python.exe" -m pip install "pyinstaller>=6.20"

"%CLIXY_VENV%\Scripts\python.exe" tools\dev\make_zip.py %*
if errorlevel 1 ( echo Making the zip failed; see the messages above. & pause & exit /b 1 )
timeout /t 5 >nul
