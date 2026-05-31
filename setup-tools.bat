@echo off
REM Give AIO a portable Linux-style toolchain (git/bash/grep) with no Docker and
REM no install: downloads PortableGit into tools\. Run ONCE (needs internet);
REM afterwards run.bat puts tools\ first on PATH and the whole folder is portable.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-tools.ps1" %*
echo.
pause
