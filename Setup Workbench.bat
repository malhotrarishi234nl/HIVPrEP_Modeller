@echo off
setlocal
cd /d "%~dp0"

py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 is required. Install it from python.org, then run this file again.
  pause
  exit /b 1
)

py -3.11 -m venv .venv-workbench
if errorlevel 1 goto :failed
.venv-workbench\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed
.venv-workbench\Scripts\python.exe -m pip install -r requirements-workbench.txt
if errorlevel 1 goto :failed

echo.
echo Setup complete. Double-click "Launch Workbench.bat".
pause
exit /b 0

:failed
echo.
echo Setup failed. Copy the error above when asking for help.
pause
exit /b 1
