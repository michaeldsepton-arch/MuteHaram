@echo off
rem MuteHaram Library: films cleaned in advance. Opens as its own window and starts the helper in the
rem background if it is not running (no terminal window; its messages go to pc\logs\helper.log).
cd /d "%~dp0"
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient('127.0.0.1',47800)).Close();exit 0}catch{exit 1}"
if errorlevel 1 (
  if not exist logs mkdir logs
  set "PY=python"
  if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
  call :starthelper
)
set "CH=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CH%" set "CH=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CH%" set "CH=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if exist "%CH%" (start "" "%CH%" --app=http://127.0.0.1:47800/library --window-size=1100,800) else (start "" http://127.0.0.1:47800/library)
exit /b

:starthelper
powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '-u','-m','muteharam_pc.helper' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0logs\helper.log' -RedirectStandardError '%~dp0logs\helper-errors.log'"
exit /b
