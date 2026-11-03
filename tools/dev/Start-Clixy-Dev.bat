@echo off
rem Clixy dev: runs Clixy from this folder and restarts it by itself whenever the
rem code changes (devmode.py). Double-click to start. The first run sets up a
rem private Python environment (a few minutes, about 1 GB) in a short folder,
rem %USERPROFILE%\.venvs\clixy: Windows' 260-character path limit breaks PyQt6
rem when it sits under a long project path. Set CLIXY_VENV to put it elsewhere.
setlocal
cd /d "%~dp0..\.."
if not defined CLIXY_VENV set "CLIXY_VENV=%USERPROFILE%\.venvs\clixy"

where py >nul 2>nul
if errorlevel 1 (
  echo Python isn't installed. Get Python 3.13 from https://www.python.org/downloads/ ,
  echo tick "Add python.exe to PATH" while installing, then run this again.
  pause
  exit /b 1
)

if not exist "%CLIXY_VENV%\Scripts\python.exe" (
  echo Setting up Clixy dev for the first time...
  py -3.13 -m venv "%CLIXY_VENV%" 2>nul || py -3 -m venv "%CLIXY_VENV%"
  if errorlevel 1 ( echo Couldn't create %CLIXY_VENV%. & pause & exit /b 1 )
)

rem Install packages on the first run and whenever requirements.txt changes.
fc /b requirements.txt "%CLIXY_VENV%\requirements.installed" >nul 2>nul
if errorlevel 1 (
  echo Installing what Clixy needs...
  "%CLIXY_VENV%\Scripts\python.exe" -m pip install --upgrade pip
  "%CLIXY_VENV%\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo Installing failed; see the messages above. & pause & exit /b 1 )
  copy /y requirements.txt "%CLIXY_VENV%\requirements.installed" >nul
)

rem Only one Clixy runs at a time: close the installed one first.
taskkill /im Clixy.exe /f >nul 2>nul

set CLIXY_DEV=1
start "Clixy dev" "%CLIXY_VENV%\Scripts\pythonw.exe" app.py
rem Brings Clixy back if a restart lands mid-edit and fails (keep_alive.py).
start "Clixy dev keep-alive" "%CLIXY_VENV%\Scripts\pythonw.exe" tools\dev\keep_alive.py
echo Clixy dev is running. Look for your buddy in the tray.
timeout /t 3 >nul
