@echo off
REM Run AIO in a Linux container against the CURRENT directory (Windows 11).
REM
REM The agent gets a full Linux toolchain (git, ripgrep, build tools) and the
REM POSIX sandbox that Windows itself can't provide; your project is mounted
REM read-write at /workspace. Needs Docker Desktop with the WSL2 backend.
REM
REM   run-docker.bat                 :: build if needed, then serve on :8765
REM   run-docker.bat -p ollama       :: extra flags are forwarded to `aio`
setlocal enabledelayedexpansion

set "IMAGE=aio:local"
if not "%AIO_IMAGE%"=="" set "IMAGE=%AIO_IMAGE%"
set "PORT=8765"
if not "%AIO_PORT%"=="" set "PORT=%AIO_PORT%"

REM AIO source = this script's folder; workspace = where you launched it.
set "SRC_DIR=%~dp0"
set "WORKSPACE=%CD%"

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker is required. Install Docker Desktop ^(WSL2 backend^) and retry.
  exit /b 1
)

echo Building %IMAGE% from "%SRC_DIR%" (first run only) ...
docker build -t %IMAGE% "%SRC_DIR%"
if errorlevel 1 exit /b 1

REM Forward provider keys that are set in the environment.
set "ENVS="
for %%K in (ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY GROQ_API_KEY MISTRAL_API_KEY DEEPSEEK_API_KEY AIO_WEB_TOKEN) do (
  if defined %%K set "ENVS=!ENVS! -e %%K=!%%K!"
)

echo Serving AIO at http://localhost:%PORT%  (workspace: %WORKSPACE%)
docker run --rm -it -p %PORT%:8765 -v "%WORKSPACE%:/workspace" !ENVS! %IMAGE% %*
endlocal
