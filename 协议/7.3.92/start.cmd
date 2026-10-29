@echo off
setlocal
if not defined CHROMIUM_PATH if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROMIUM_PATH=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROMIUM_PATH if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "CHROMIUM_PATH=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0cli.py" %*
exit /b %errorlevel%
