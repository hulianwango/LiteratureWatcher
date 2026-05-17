@echo off
setlocal

cd /d "%~dp0"
set PYTHONUTF8=1
set "PYTHON_EXE=python"

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
)

if not exist "data" mkdir "data"
if not exist "reports" mkdir "reports"

"%PYTHON_EXE%" search_literature.py --config config.yaml
if errorlevel 1 (
  echo search_literature.py failed.
  exit /b %errorlevel%
)

"%PYTHON_EXE%" export_report.py --config config.yaml --input data\latest_results.json
if errorlevel 1 (
  echo export_report.py failed.
  exit /b %errorlevel%
)

echo Word, Markdown, and CSV reports saved in the reports folder.
endlocal
