@echo off
REM ============================================================
REM  AIO - portable launcher for Windows 11 (local web)
REM  No installation required: the project is pure Python stdlib,
REM  so it runs straight from this folder.
REM  Python resolution order:
REM    1) bundled python\  (fully self-contained, see setup-embedded.bat)
REM    2) the 'py' launcher
REM    3) 'python' on PATH                       (requires Python 3.11+)
REM ============================================================
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

REM PYEXE = program, PYARGS = extra args (kept separate so paths with spaces
REM can be quoted while the 'py -3' launcher keeps its argument).
set "PYEXE="
set "PYARGS="

if exist "%~dp0python\python.exe" set "PYEXE=%~dp0python\python.exe"
if defined PYEXE echo [AIO] Using the bundled Python in python\ ^(no system install needed^).

if not defined PYEXE where py >nul 2>&1 && set "PYEXE=py"
if "%PYEXE%"=="py" set "PYARGS=-3"

if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"

if not defined PYEXE (
  echo [AIO] No Python found.
  echo       Option A: double-click setup-embedded.bat once to download a
  echo                 self-contained Python into python\ ^(needs internet once^).
  echo       Option B: install Python 3.11+ from
  echo                 https://www.python.org/downloads/windows/ ^(tick "Add to PATH"^).
  pause
  exit /b 1
)

REM --- verify version is 3.11+ (tomllib is needed) ---
"%PYEXE%" %PYARGS% -c "import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,11) else 1)"
if errorlevel 1 (
  echo [AIO] Python 3.11 or newer is required. Detected:
  "%PYEXE%" %PYARGS% --version
  pause
  exit /b 1
)

echo [AIO] Starting the portable web dashboard...
echo [AIO] A browser tab will open at http://localhost:8765
echo.
REM Pass any extra args through (e.g. --port 9000, -p ollama, -C C:\path\to\project)
"%PYEXE%" %PYARGS% -m aio --web --open %*

echo.
echo [AIO] Server stopped.
pause
endlocal
