@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" goto run_app
echo [ERROR] Project virtual environment was not found.
echo Run: python -m venv venv
echo Then install project requirements.
pause
exit /b 1

:run_app
"venv\Scripts\python.exe" "commentary_studio\main.py"
if errorlevel 1 echo [ERROR] The application exited with an error.
pause
