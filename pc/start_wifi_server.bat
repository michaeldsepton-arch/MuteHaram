@echo off
rem Lets your tablet/phone use this computer's graphics card over Wi-Fi.
rem Leave this window open while you watch on the tablet. If Windows asks about the
rem firewall, allow PRIVATE networks.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m muteharam_pc.stream_server %*
) else (
  python -m muteharam_pc.stream_server %*
)
pause
