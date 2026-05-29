<#
  setup-embedded.ps1 — build a fully self-contained AIO bundle on Windows.

  Downloads the official Windows "embeddable" Python distribution into a
  local  python\  subfolder and wires it up so the project runs with NO
  system Python installed. Run this ONCE (it needs internet); afterwards
  run.bat / run.ps1 automatically use python\ and the bundle is portable
  (copy the whole folder to any Windows 11 machine, even offline).

  Usage:
    powershell -ExecutionPolicy Bypass -File .\setup-embedded.ps1
    powershell -ExecutionPolicy Bypass -File .\setup-embedded.ps1 -Version 3.12.7 -Arch amd64
#>
param(
  [string]$Version = "3.12.7",
  [ValidateSet("amd64", "arm64", "win32")]
  [string]$Arch = "amd64"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$dest = Join-Path $PSScriptRoot "python"
if (Test-Path (Join-Path $dest "python.exe")) {
  Write-Host "[setup] python\ already contains python.exe. Delete the folder to rebuild." -ForegroundColor Yellow
  exit 0
}

# Embeddable builds exist for 3.11+ only (this project needs 3.11+ for tomllib).
$minor = [int]($Version.Split('.')[1])
if ($minor -lt 11) { throw "Python 3.11+ is required (got $Version)." }

$zipName = "python-$Version-embed-$Arch.zip"
$url = "https://www.python.org/ftp/python/$Version/$zipName"
$tmp = Join-Path $env:TEMP $zipName

Write-Host "[setup] Downloading $url" -ForegroundColor Cyan
Invoke-WebRequest -Uri $url -OutFile $tmp

Write-Host "[setup] Extracting to $dest"
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Expand-Archive -Path $tmp -DestinationPath $dest -Force
Remove-Item $tmp -Force

# The embeddable distro uses a pythonXY._pth file to define sys.path and (by
# default) ignores PYTHONPATH. Add the project's src\ so `import aio` works.
$pth = Get-ChildItem -Path $dest -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { throw "Could not find the python*._pth file in $dest." }
$relSrc = "..\src"
$content = Get-Content $pth.FullName
if ($content -notcontains $relSrc) {
  Add-Content -Path $pth.FullName -Value $relSrc
  Write-Host "[setup] Added '$relSrc' to $($pth.Name)"
}

# Sanity check: the bundled interpreter must import the package.
& (Join-Path $dest "python.exe") -c "import aio; print('aio', aio.__version__, 'ready on', __import__('sys').version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "Bundled Python could not import aio." }

Write-Host ""
Write-Host "[setup] Done. The bundle is self-contained — run.bat will now use python\." -ForegroundColor Green
Write-Host "[setup] You can copy this whole folder to any Windows 11 machine (no install needed)."
