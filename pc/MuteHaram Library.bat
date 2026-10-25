@echo off
rem MuteHaram Library: films cleaned in advance. Opens as its own window (starts the helper if needed).
cd /d "%~dp0"
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient('127.0.0.1',47800)).Close();exit 0}catch{exit 1}"
if errorlevel 1 start "MuteHaram helper" /min cmd /c start_helper.bat
set "CH=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CH%" set "CH=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CH%" set "CH=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if exist "%CH%" (start "" "%CH%" --app=http://127.0.0.1:47800/library --window-size=1100,800) else (start "" http://127.0.0.1:47800/library)
