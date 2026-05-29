<#
  AIO - portable launcher for Windows 11 (PowerShell).
  No installation required: pure Python stdlib, run from this folder.
  Requirement: Python 3.11+ on PATH.

  If PowerShell blocks the script, run it once as:
    powershell -ExecutionPolicy Bypass -File .\run.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"

# Locate Python: 1) bundled embeddable runtime  2) 'py' launcher  3) 'python'.
$py = $null
$bundled = Join-Path $PSScriptRoot "python\python.exe"
if (Test-Path $bundled) {
  $py = @($bundled)
  Write-Host "[AIO] Using the bundled Python in python\ (no system install needed)." -ForegroundColor Cyan
}
elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = @("py", "-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = @("python") }

if (-not $py) {
  Write-Host "[AIO] No Python found." -ForegroundColor Yellow
  Write-Host "      Option A: run setup-embedded.ps1 once to download a self-contained Python."
  Write-Host "      Option B: install Python 3.11+ from https://www.python.org/downloads/windows/."
  Read-Host "Press Enter to exit"
  exit 1
}

# Verify version >= 3.11.
& $py[0] $py[1..($py.Count-1)] -c "import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,11) else 1)"
if ($LASTEXITCODE -ne 0) {
  Write-Host "[AIO] Python 3.11 or newer is required." -ForegroundColor Yellow
  & $py[0] $py[1..($py.Count-1)] --version
  Read-Host "Press Enter to exit"
  exit 1
}

Write-Host "[AIO] Starting the portable web dashboard at http://localhost:8765 ..." -ForegroundColor Cyan
& $py[0] $py[1..($py.Count-1)] -m aio --web --open @args
