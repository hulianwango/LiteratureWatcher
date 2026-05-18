@echo off
setlocal

cd /d "%~dp0"

if not exist "logs" mkdir logs

echo ======================================== >> logs\daily_literature.log
echo Run started: %date% %time% >> logs\daily_literature.log
echo Project folder: %cd% >> logs\daily_literature.log

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Cannot find .venv\Scripts\python.exe >> logs\daily_literature.log
    exit /b 1
)

echo [1/2] Running search_literature.py... >> logs\daily_literature.log
".venv\Scripts\python.exe" search_literature.py >> logs\daily_literature.log 2>&1

if errorlevel 1 (
    echo [WARN] search_literature.py returned an error. Continue to export report. >> logs\daily_literature.log
)

echo [2/2] Running export_report.py... >> logs\daily_literature.log
".venv\Scripts\python.exe" export_report.py >> logs\daily_literature.log 2>&1

if errorlevel 1 (
    echo [ERROR] export_report.py failed. >> logs\daily_literature.log
    echo Run ended: %date% %time% >> logs\daily_literature.log
    exit /b 1
)

echo [OK] Report generated. >> logs\daily_literature.log
echo Run ended: %date% %time% >> logs\daily_literature.log
exit /b 0