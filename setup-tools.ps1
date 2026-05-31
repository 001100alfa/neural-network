<#
  setup-tools.ps1 — give AIO a portable Linux-style toolchain on Windows,
  with NO Docker and NO install.

  Docker needs a Linux kernel (Docker Desktop = a heavyweight install with
  WSL2). But AIO only shells out to git / bash / grep, all of which ship inside
  **PortableGit** — a self-contained, no-install Git-for-Windows distribution
  that also bundles MSYS2 bash, grep, sed, awk, find, curl, ssh, etc.

  This script downloads PortableGit into a local  tools\  folder and wires
  run.bat to put it first on PATH. Run it ONCE (needs internet); afterwards the
  whole folder is portable — copy it to any Windows 11 machine (or a USB stick)
  and the agent has its Unix toolchain offline, no admin rights, no Docker.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\setup-tools.ps1
    powershell -ExecutionPolicy Bypass -File .\setup-tools.ps1 -Version 2.47.1 -Arch 64
#>
param(
  [string]$Version = "2.47.1",
  [ValidateSet("64", "32")]
  [string]$Arch = "64"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$dest = Join-Path $PSScriptRoot "tools"
$gitExe = Join-Path $dest "cmd\git.exe"
if (Test-Path $gitExe) {
  Write-Host "[tools] tools\ already contains git. Delete the folder to rebuild." -ForegroundColor Yellow
  & $gitExe --version
  exit 0
}

# PortableGit self-extracting archive from the official git-for-windows release.
$tag  = "v$Version.windows.1"
$file = "PortableGit-$Version-$Arch-bit.7z.exe"
$url  = "https://github.com/git-for-windows/git/releases/download/$tag/$file"
$tmp  = Join-Path $env:TEMP $file

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

Write-Host "[tools] Downloading $url" -ForegroundColor Cyan
Invoke-WebRequest -Uri $url -OutFile $tmp

Write-Host "[tools] Extracting PortableGit to $dest (no install) ..."
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Path $dest | Out-Null
# The .7z.exe is a self-extractor: -y silent, -o output dir.
& $tmp -y "-o$dest" | Out-Null
Remove-Item $tmp -Force

if (-not (Test-Path $gitExe)) { throw "Extraction failed: $gitExe not found." }

# Sanity check: git, bash and grep must all run from the portable toolchain.
Write-Host "[tools] Verifying the portable toolchain:" -ForegroundColor Cyan
& $gitExe --version
& (Join-Path $dest "usr\bin\bash.exe") -c "grep --version | head -1"
if ($LASTEXITCODE -ne 0) { throw "Portable bash/grep did not run." }

Write-Host ""
Write-Host "[tools] Done. run.bat will now put tools\ first on PATH (git/bash/grep)." -ForegroundColor Green
Write-Host "[tools] Copy this whole folder anywhere — the toolchain travels with it, no Docker, no install."
