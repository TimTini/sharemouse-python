@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" goto ready
py -3 -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
  goto ready
)
python -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>nul
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,11))"
if errorlevel 1 goto missing_python
".venv\Scripts\python.exe" run_sharemouse.py --check-dependencies
if not errorlevel 1 goto launch
echo This program does not download packages or connect to the internet.
goto failed
:launch
if "%~1"=="--configure" (
  ".venv\Scripts\python.exe" configure.py
) else (
  ".venv\Scripts\python.exe" run_sharemouse.py --interactive
)
set "result=%errorlevel%"
if not "%result%"=="0" echo ShareMouse stopped with error %result%. See the message above.
pause
exit /b %result%
:missing_python
echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/windows/
echo Enable the Python launcher or Add Python to PATH, then double-click this file again.
:failed
echo Setup failed. No ShareMouse process was started. See the error above.
pause
exit /b 1
