#Requires -Version 5.1
<#
.SYNOPSIS
  FOR DEVELOPERS. Sets up an Open Marketing OS development environment.

.DESCRIPTION
  Creates .venv, installs runtime and dev dependencies, and installs the
  frontend packages.

  This is NOT how you install OMOS as a user. If you just want to use the app,
  download OpenMarketingOS-Quick-Setup.exe from the GitHub releases page — you
  do not need Python, Node.js, Git or this script.

  Usage:
      .\scripts\setup-dev.ps1
      .\scripts\setup-dev.ps1 -WithOptionalRetrieval

.EXAMPLE
  .\scripts\setup-dev.ps1
#>
[CmdletBinding()]
param(
  [switch]$WithOptionalRetrieval,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvDir  = Join-Path $RepoRoot '.venv'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "FAILED: $m" -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host 'Open Marketing OS - developer setup' -ForegroundColor Green
Write-Host 'For using the app as a normal user, download the installer instead.' -ForegroundColor DarkGray
Write-Host ''

# --- prerequisites ----------------------------------------------------------
Write-Step 'Checking prerequisites'
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { Fail 'Python was not found on PATH. Install Python 3.11 or newer.' }
$version = & $pythonCmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
$parts = $version -split '\.'
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
  Fail "Python $version found. OMOS needs 3.11 or newer."
}
Write-Host "    python $version"
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
  Write-Host '    node not found - the frontend cannot be built or typechecked.' -ForegroundColor Yellow
  Write-Host '    Backend work is still possible. Install Node 20+ from https://nodejs.org' -ForegroundColor Yellow
} else {
  Write-Host "    node $(& $nodeCmd.Source --version)"
}

# --- python env -------------------------------------------------------------
if ($Force -and (Test-Path $VenvDir)) {
  Write-Step 'Removing the existing .venv (-Force)'
  Get-ChildItem $VenvDir -Recurse -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $VenvPy)) {
  Write-Step 'Creating .venv'
  & $pythonCmd.Source -m venv $VenvDir
  if ($LASTEXITCODE -ne 0) { Fail 'could not create the virtual environment' }
} else {
  Write-Step 'Reusing the existing .venv'
}

Write-Step 'Installing Python dependencies'
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $RepoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'could not install requirements.txt' }
& $VenvPy -m pip install --quiet -r (Join-Path $RepoRoot 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { Fail 'could not install requirements-dev.txt' }

if ($WithOptionalRetrieval) {
  Write-Step 'Installing optional hybrid-retrieval extras (larger download)'
  & $VenvPy -m pip install --quiet -r (Join-Path $RepoRoot 'requirements-optional.txt')
  if ($LASTEXITCODE -ne 0) { Fail 'could not install requirements-optional.txt' }
}

# --- frontend ---------------------------------------------------------------
$FrontendDir = Join-Path $RepoRoot 'frontend'
if (Test-Path (Join-Path $FrontendDir 'package.json')) {
  if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Step 'Installing frontend packages'
    Push-Location $FrontendDir
    try {
      npm ci --no-audit --no-fund
      if ($LASTEXITCODE -ne 0) { Fail 'npm ci failed' }
      Write-Step 'Building the frontend once, so the backend can serve it'
      npm run build
      if ($LASTEXITCODE -ne 0) { Fail 'npm run build failed' }
    } finally {
      Pop-Location
    }
  } else {
    Write-Host '    skipping the frontend: npm is not installed' -ForegroundColor Yellow
  }
}

# --- .env -------------------------------------------------------------------
if (-not (Test-Path (Join-Path $RepoRoot '.env'))) {
  Write-Step 'Creating .env from .env.example'
  Copy-Item (Join-Path $RepoRoot '.env.example') (Join-Path $RepoRoot '.env')
  Write-Host '    .env is gitignored. Never commit it, and never paste its values into an issue.' -ForegroundColor DarkGray
}

Write-Step 'Verifying the backend imports'
Push-Location $RepoRoot
try {
  & $VenvPy -c "from app.main import create_app; a = create_app(); print('    backend OK,', len(a.routes), 'routes')"
  if ($LASTEXITCODE -ne 0) { Fail 'the backend failed to import - see the error above' }
} finally {
  Pop-Location
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host '  Start the app        : .\scripts\run-dev.ps1'
Write-Host '  Run the tests        : .\.venv\Scripts\python.exe -m pytest -q'
Write-Host '  Typecheck the frontend: cd frontend; npm run typecheck'
Write-Host ''
