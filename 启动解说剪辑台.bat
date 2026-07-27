@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" goto run_app
echo [ERROR] Project virtual environment was not found.
echo Run: python -m venv venv
echo Then install project requirements.
pause
exit /b 1

:run_app
"venv\Scripts\python.exe" -c "import PySide6" >nul 2>&1
if errorlevel 1 goto missing_dependencies
"venv\Scripts\python.exe" "commentary_studio\main.py"
if errorlevel 1 echo [ERROR] The application exited with an error.
pause
exit /b

:missing_dependencies
echo [ERROR] StoryCut dependencies are not installed in the project virtual environment.
echo Run:
echo venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
