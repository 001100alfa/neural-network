<#
  AIO - portable launcher for Windows 11 (PowerShell).
  No installation required: pure Python stdlib, run from this folder.
  Python resolution: bundled python\  ->  'py' launcher  ->  'python' on PATH.
  Requires Python 3.11+.

  If PowerShell blocks the script, run it once as:
    powershell -ExecutionPolicy Bypass -File .\run.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"

# $exe = program, $pre = prefix args (e.g. -3 for the 'py' launcher).
$exe = $null
$pre = @()
$bundled = Join-Path $PSScriptRoot "python\python.exe"
if (Test-Path $bundled) {
  $exe = $bundled
  Write-Host "[AIO] Using the bundled Python in python\ (no system install needed)." -ForegroundColor Cyan
}
elseif (Get-Command py -ErrorAction SilentlyContinue) { $exe = "py"; $pre = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $exe = "python" }

if (-not $exe) {
  Write-Host "[AIO] No Python found." -ForegroundColor Yellow
  Write-Host "      Option A: run setup-embedded.ps1 once to download a self-contained Python."
  Write-Host "      Option B: install Python 3.11+ from https://www.python.org/downloads/windows/."
  Read-Host "Press Enter to exit"
  exit 1
}

# Verify version >= 3.11 (tomllib).
& $exe @pre -c "import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,11) else 1)"
if ($LASTEXITCODE -ne 0) {
  Write-Host "[AIO] Python 3.11 or newer is required." -ForegroundColor Yellow
  & $exe @pre --version
  Read-Host "Press Enter to exit"
  exit 1
}

Write-Host "[AIO] Starting the portable web dashboard at http://localhost:8765 ..." -ForegroundColor Cyan
& $exe @pre -m aio --web --open @args
