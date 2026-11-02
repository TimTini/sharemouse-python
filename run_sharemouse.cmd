@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PIP_NO_INDEX=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_CACHE_DIR=%~dp0.cache\setup"
if not exist "%~dp0.cache\setup" mkdir "%~dp0.cache\setup"
set "TEMP=%~dp0.cache\setup"
set "TMP=%TEMP%"
if exist ".venv\Scripts\python.exe" goto ready
py -3 -c "import sys,platform,sysconfig; sys.exit(not ((3,11) <= sys.version_info[:2] <= (3,14)) or platform.machine().upper() not in ('AMD64','X86_64') or bool(sysconfig.get_config_var('Py_GIL_DISABLED')))" >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
  goto ready
)
python -c "import sys,platform,sysconfig; sys.exit(not ((3,11) <= sys.version_info[:2] <= (3,14)) or platform.machine().upper() not in ('AMD64','X86_64') or bool(sysconfig.get_config_var('Py_GIL_DISABLED')))" >nul 2>nul
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" -c "import sys,platform,sysconfig; sys.exit(not ((3,11) <= sys.version_info[:2] <= (3,14)) or platform.machine().upper() not in ('AMD64','X86_64') or bool(sysconfig.get_config_var('Py_GIL_DISABLED')))"
if errorlevel 1 goto missing_python
".venv\Scripts\python.exe" run_sharemouse.py --check-dependencies
if not errorlevel 1 goto launch
echo This program does not download packages or connect to the internet.
echo Installing PAKE dependencies from bundled, SHA256-pinned local wheels.
".venv\Scripts\python.exe" -m ensurepip >nul 2>nul
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --no-index --require-hashes --only-binary=:all: --find-links "%~dp0vendor\packages" -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" run_sharemouse.py --check-dependencies
if errorlevel 1 goto failed
:launch
if "%~1"=="--configure" (
  ".venv\Scripts\python.exe" configure.py
) else if "%~1"=="--test" (
  ".venv\Scripts\python.exe" -m tests
) else (
  ".venv\Scripts\python.exe" run_sharemouse.py --interactive
)
set "result=%errorlevel%"
if not "%result%"=="0" echo ShareMouse stopped with error %result%. See the message above.
pause
exit /b %result%
:missing_python
echo Standard x64 Python 3.11-3.14 is required (not ARM64 or free-threaded). Install it from https://www.python.org/downloads/windows/
echo Enable the Python launcher or Add Python to PATH, then double-click this file again.
:failed
echo Setup failed. No ShareMouse process was started. See the error above.
pause
exit /b 1
