@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-workbench\Scripts\python.exe" (
  echo The workbench environment is missing. Run "Setup Workbench.bat" first.
  pause
  exit /b 1
)

set "MPLCONFIGDIR=%CD%\outputs\workbench\.matplotlib"
if not exist "%MPLCONFIGDIR%" mkdir "%MPLCONFIGDIR%"
.venv-workbench\Scripts\python.exe -m streamlit run workbench_ui/app.py --server.address 127.0.0.1
if errorlevel 1 pause
