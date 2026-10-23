@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if errorlevel 1 goto python_missing
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto python_missing
".venv\Scripts\python.exe" -c "import importlib.metadata; assert importlib.metadata.version('PyMuPDF') == '1.28.2'" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" launcher.py
if errorlevel 1 goto fail
exit /b 0
:python_missing
echo Python 3.10 or later is required. Install Python, then run this file again.
echo If this folder was moved, remove only its .venv folder and try again.
pause
exit /b 1
:fail
echo CourseTrace could not start. See the error above.
pause
exit /b 1
