@echo off
REM Build a self-contained bundle: download embeddable Python into python\.
REM Run this ONCE (needs internet). Afterwards run.bat uses python\ and the
REM whole folder is portable to any Windows 11 machine, even offline.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-embedded.ps1" %*
echo.
pause
