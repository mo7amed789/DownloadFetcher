@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" "%~dp0.venv\Scripts\python.exe" "%~dp0updater.py"
py -3 -u app.py
if errorlevel 1 python -u app.py
pause
