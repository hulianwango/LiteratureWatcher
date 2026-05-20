@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" config_editor.py
) else (
    python config_editor.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Cannot start the configuration editor.
    echo Please make sure Python and the required packages are installed.
    pause
    exit /b 1
)
