#Requires -Version 5.1
<#
.SYNOPSIS
  FOR DEVELOPERS. Runs Open Marketing OS from source.

.DESCRIPTION
  Starts the FastAPI backend on 127.0.0.1 and opens your browser.

  This is NOT how you run OMOS as a user. If you just want to use the app,
  launch "Open Marketing OS" from the Start menu after installing
  OpenMarketingOS-Quick-Setup.exe.

  Usage:
      .\scripts\run-dev.ps1                  # production-like runtime
      .\scripts\run-dev.ps1 -LegacyRuntime   # rollback-only legacy path
      .\scripts\run-dev.ps1 -FrontendOnly    # Vite dev server with HMR

.EXAMPLE
  .\scripts\run-dev.ps1
#>
[CmdletBinding()]
param(
  [int]$Port = 0,
  [switch]$LegacyRuntime,
  [switch]$FrontendOnly,
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvPy   = Join-Path $RepoRoot '.venv\Scripts\python.exe'

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "FAILED: $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $VenvPy)) {
  Fail "No virtual environment found. Run .\scripts\setup-dev.ps1 first."
}

if ($FrontendOnly) {
  $FrontendDir = Join-Path $RepoRoot 'frontend'
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail 'npm is not installed' }
  Push-Location $FrontendDir
  try { npm run dev } finally { Pop-Location }
  exit 0
}

# Ask the OS for a free port rather than assuming 8000 is available. The
# shipped launcher does exactly the same thing.
if ($Port -eq 0) {
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $listener.Start()
  $Port = $listener.LocalEndpoint.Port
  $listener.Stop()
}

$env:OMOS_PORT = "$Port"
if ($LegacyRuntime) {
  Write-Step 'Using the LEGACY runtime (rollback only - not the shipped path)'
  $env:AI_RUNTIME = 'legacy'
}

Write-Step "Starting the backend on http://127.0.0.1:$Port/app"
Write-Host '    Loopback only. Press Ctrl+C to stop.' -ForegroundColor DarkGray

$DistIndex = Join-Path $RepoRoot 'frontend\dist\index.html'
if (-not (Test-Path $DistIndex)) {
  Write-Host ''
  Write-Host 'NOTE: frontend\dist is missing, so /app will return a build-hint page.' -ForegroundColor Yellow
  Write-Host '      Run: cd frontend; npm ci; npm run build' -ForegroundColor Yellow
  Write-Host '      Or use -FrontendOnly for the Vite dev server with hot reload.' -ForegroundColor Yellow
  Write-Host ''
}

if (-not $NoBrowser) {
  Start-Job -Name OmosOpenBrowser -ScriptBlock {
    param($u)
    Start-Sleep -Seconds 3
    Start-Process $u
  } -ArgumentList "http://127.0.0.1:$Port/app" | Out-Null
}

Push-Location $RepoRoot
try {
  & $VenvPy (Join-Path $RepoRoot 'launcher\serve.py')
} finally {
  Pop-Location
  Get-Job -Name OmosOpenBrowser -ErrorAction SilentlyContinue |
    Stop-Job -ErrorAction SilentlyContinue
  Get-Job -Name OmosOpenBrowser -ErrorAction SilentlyContinue |
    Remove-Job -Force -ErrorAction SilentlyContinue
}
