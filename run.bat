@echo off
REM ============================================================
REM  AIO - portable launcher for Windows 11 (local web)
REM  No installation required: the project is pure Python stdlib,
REM  so it runs straight from this folder via PYTHONPATH.
REM  Requirement: Python 3.11+ on PATH (the 'py' launcher is fine).
REM ============================================================
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

REM --- locate Python: prefer the 'py' launcher, then 'python' ---
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
  echo [AIO] Python 3.11+ was not found on PATH.
  echo       Get it from https://www.python.org/downloads/windows/
  echo       and tick "Add python.exe to PATH" during setup, then run this file again.
  pause
  exit /b 1
)

REM --- verify version is 3.11+ (tomllib is needed) ---
%PYEXE% -c "import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,11) else 1)"
if errorlevel 1 (
  echo [AIO] Python 3.11 or newer is required. Detected:
  %PYEXE% --version
  pause
  exit /b 1
)

echo [AIO] Starting the portable web dashboard...
echo [AIO] %PYEXE%  ^|  PYTHONPATH=%PYTHONPATH%
echo [AIO] A browser tab will open at http://localhost:8765
echo.
REM Pass any extra args through (e.g. --port 9000, -p ollama, -C C:\path\to\project)
%PYEXE% -m aio --web --open %*

echo.
echo [AIO] Server stopped.
pause
endlocal
