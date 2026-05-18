@echo off
setlocal

cd /d "%~dp0"

echo ========================================
echo Daily Literature Search
echo Project folder: %cd%
echo ========================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Cannot find .venv\Scripts\python.exe
    echo Please run:
    echo python -m venv .venv
    echo .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo [1/2] Running search_literature.py...
".venv\Scripts\python.exe" search_literature.py

if errorlevel 1 (
    echo.
    echo [WARN] search_literature.py returned an error.
    echo This may be caused by arXiv / Semantic Scholar 429 limits.
    echo Continue to export report using latest_results.json...
)

echo.
echo [2/2] Running export_report.py...
".venv\Scripts\python.exe" export_report.py

if errorlevel 1 (
    echo.
    echo [ERROR] export_report.py failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Done.
echo Please check the reports folder:
echo %cd%\reports
echo ========================================

pause