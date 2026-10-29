@echo off
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0preview_motion.py" %*
exit /b %errorlevel%
