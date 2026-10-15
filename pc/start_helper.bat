@echo off
rem Starts the MuteHaram helper that the browser extension talks to.
rem Leave this window open while you browse. (Put a shortcut to this file in the
rem Startup folder -- press Win+R, type shell:startup -- to start it automatically.)
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m muteharam_pc.helper
) else (
  python -m muteharam_pc.helper
)
pause
