@echo off
rem Clixy dev: runs Clixy from this folder and restarts it by itself whenever the
rem code changes (devmode.py). Double-click to start. The first run sets up a
rem private Python environment in .venv (a few minutes, about 1 GB).
setlocal
cd /d "%~dp0..\.."

where py >nul 2>nul
if errorlevel 1 (
  echo Python isn't installed. Get Python 3.13 from https://www.python.org/downloads/ ,
  echo tick "Add python.exe to PATH" while installing, then run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Setting up Clixy dev for the first time...
  py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv
  if errorlevel 1 ( echo Couldn't create .venv. & pause & exit /b 1 )
)

rem Install packages on the first run and whenever requirements.txt changes.
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
  echo Installing what Clixy needs...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo Installing failed; see the messages above. & pause & exit /b 1 )
  copy /y requirements.txt .venv\requirements.installed >nul
)

rem Only one Clixy runs at a time: close the installed one first.
taskkill /im Clixy.exe /f >nul 2>nul

set CLIXY_DEV=1
start "Clixy dev" ".venv\Scripts\pythonw.exe" app.py
echo Clixy dev is running. Look for your buddy in the tray.
timeout /t 3 >nul
