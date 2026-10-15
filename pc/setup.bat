@echo off
rem Double-click to set up MuteHaram PC on this computer (downloads what's missing).
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" in the installer^), then run this again.
  pause
  exit /b 1
)
cd /d "%~dp0"
python setup_pc.py %*
pause
