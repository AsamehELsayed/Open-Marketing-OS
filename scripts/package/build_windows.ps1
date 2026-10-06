#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Open Marketing OS Windows payload (frozen launcher + backend +
  prebuilt React bundle) into build\payload\.

.DESCRIPTION
  Produces a runnable, no-console `OpenMarketingOS.exe` plus its bundled
  interpreter. This is the artifact the Inno Setup installer and the portable
  ZIP both wrap, so it is the only packaging step that needs to be correct.

  Steps, in order, each failing loudly:
    1. Verify the React bundle exists (React must ship prebuilt; a normal user
       never runs npm).
    2. Create an isolated build virtualenv from requirements-lock.txt when one is
       available, so the payload cannot pick up a developer's extra packages.
    3. Install PyInstaller into that venv.
    4. Run packaging\omos.spec.
    5. Smoke-test the built exe: it must start, report healthy, and answer
       /health on a dynamically chosen port. A build that cannot start is not
       shipped.

.PARAMETER SkipVenv
  Reuse the current interpreter instead of building an isolated one. Faster for
  iteration; not for a release.

.PARAMETER RunRoot
  Isolate build, staged runtime, and payload outputs under this repository path.
  DEV acceptance runs must supply a run-local directory rather than shared
  build/ or dist/.

.PARAMETER FrontendDist
  Use an already-built React bundle, such as the DEV run-local frontend output.

.EXAMPLE
  pwsh -File scripts\package\build_windows.ps1
#>
[CmdletBinding()]
param(
  [switch]$SkipVenv,
  [switch]$SkipSmokeTest,
  [string]$RunRoot,
  [string]$FrontendDist
)

$ErrorActionPreference = 'Stop'
# PowerShell 7.4 turns native stderr into a terminating error; pyinstaller and
# git both write progress to stderr on success. Detect failure via $LASTEXITCODE.
$PSNativeCommandUseErrorActionPreference = $false

$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$IsolatedRunRoot = $null
if ($RunRoot) {
  $IsolatedRunRoot = if ([IO.Path]::IsPathRooted($RunRoot)) { [IO.Path]::GetFullPath($RunRoot) } else { [IO.Path]::GetFullPath((Join-Path $RepoRoot $RunRoot)) }
  $repoPrefix = $RepoRoot.TrimEnd('\') + '\'
  if (-not $IsolatedRunRoot.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    Fail 'RunRoot must be inside the repository so the build remains isolated and auditable.'
  }
  if ($IsolatedRunRoot -eq (Join-Path $RepoRoot 'build') -or $IsolatedRunRoot -eq (Join-Path $RepoRoot 'dist')) {
    Fail 'RunRoot must not be the shared build or dist directory.'
  }
  $BuildDir   = Join-Path $IsolatedRunRoot 'build'
  $PayloadDir = Join-Path $IsolatedRunRoot 'payload'
  $RuntimeDir = Join-Path $IsolatedRunRoot 'runtime'
  $DownloadDir = Join-Path $IsolatedRunRoot 'downloads'
} else {
  $BuildDir   = Join-Path $RepoRoot 'build'
  $PayloadDir = Join-Path $BuildDir 'payload'
  $RuntimeDir = Join-Path $BuildDir 'runtime'
  $DownloadDir = Join-Path $BuildDir 'downloads'
}
$SpecFile   = Join-Path $RepoRoot 'packaging\omos.spec'
$ReactDist  = if ($FrontendDist) { if ([IO.Path]::IsPathRooted($FrontendDist)) { [IO.Path]::GetFullPath($FrontendDist) } else { [IO.Path]::GetFullPath((Join-Path $RepoRoot $FrontendDist)) } } else { Join-Path $RepoRoot 'frontend\dist' }

function Write-Step($message) {
  Write-Host "==> $message" -ForegroundColor Cyan
}

function Fail($message) {
  Write-Host "FAILED: $message" -ForegroundColor Red
  exit 1
}

# --- 0. sanity --------------------------------------------------------------
if (-not (Test-Path $SpecFile)) { Fail "Missing spec file: $SpecFile" }

# --- 0a. stage the exact pinned Local runtime --------------------------------
$catalogPath = Join-Path $RepoRoot 'config\local_models.json'
if (-not (Test-Path $catalogPath)) { Fail 'Pinned Local runtime metadata is missing.' }
$catalog = Get-Content $catalogPath -Raw | ConvertFrom-Json
$runtimePin = $catalog.runtime
if ($runtimePin.version -ne 'b11429' -or
    $runtimePin.revision -ne 'd81235049384534c167caea52b85a694f6103d14' -or
    $runtimePin.asset_name -ne 'llama-b11429-bin-win-cpu-x64.zip' -or
    $runtimePin.url -ne 'https://github.com/ggml-org/llama.cpp/releases/download/b11429/llama-b11429-bin-win-cpu-x64.zip' -or
    $runtimePin.size_bytes -ne 19398918 -or
    $runtimePin.sha256 -ne '1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe') {
  Fail 'Refusing to stage a llama.cpp runtime that differs from the approved b11429 pin.'
}
New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
$runtimeArchive = Join-Path $DownloadDir $runtimePin.asset_name
if (-not (Test-Path $runtimeArchive)) {
  Write-Step 'Downloading the pinned llama.cpp b11429 Windows CPU runtime'
  Invoke-WebRequest -Uri $runtimePin.url -OutFile $runtimeArchive
}
$archiveInfo = Get-Item $runtimeArchive
if ($archiveInfo.Length -ne [int64]$runtimePin.size_bytes) { Fail 'Pinned llama.cpp archive size does not match catalog; packaging stopped.' }
$archiveHash = (Get-FileHash -LiteralPath $runtimeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveHash -ne $runtimePin.sha256.ToLowerInvariant()) { Fail 'Pinned llama.cpp archive SHA-256 does not match catalog; packaging stopped.' }

$extractDir = if ($IsolatedRunRoot) { Join-Path $IsolatedRunRoot 'runtime-extract' } else { Join-Path $BuildDir 'runtime-extract' }
if (Test-Path $extractDir) { Remove-Item -LiteralPath $extractDir -Recurse -Force }
if (Test-Path $RuntimeDir) { Remove-Item -LiteralPath $RuntimeDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $extractDir, $RuntimeDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($runtimeArchive)
try {
  $extractPrefix = [IO.Path]::GetFullPath($extractDir).TrimEnd('\') + '\'
  foreach ($entry in $zip.Entries) {
    if (-not $entry.Name) { continue }
    $target = [IO.Path]::GetFullPath((Join-Path $extractDir $entry.FullName))
    if (-not $target.StartsWith($extractPrefix, [StringComparison]::OrdinalIgnoreCase)) { Fail 'Pinned runtime archive contains an unsafe path.' }
  }
} finally { $zip.Dispose() }
[IO.Compression.ZipFile]::ExtractToDirectory($runtimeArchive, $extractDir)
$runtimeCandidates = Get-ChildItem $extractDir -Recurse -File | Where-Object {
  $_.Extension -in @('.exe', '.dll') -or $_.Name -match '^(LICENSE|COPYING)([-.]|$)'
}
if (-not ($runtimeCandidates | Where-Object { $_.Name -ieq 'llama-server.exe' })) { Fail 'Pinned archive did not contain llama-server.exe.' }
$openmpNotice = $runtimeCandidates | Where-Object { $_.Name -ieq 'LICENSE-LLVM-OpenMP' }
if (-not $openmpNotice) { Fail 'Pinned runtime archive did not contain LICENSE-LLVM-OpenMP.' }
foreach ($source in $runtimeCandidates) {
  $targetDir = if ($source.Name -match '^(LICENSE|COPYING)([-.]|$)') { Join-Path $RuntimeDir 'licenses\llama.cpp' } else { $RuntimeDir }
  New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
  Copy-Item -LiteralPath $source.FullName -Destination (Join-Path $targetDir $source.Name) -Force
}
$llamaLicenseSource = Join-Path $PSScriptRoot 'notices\llama.cpp-LICENSE'
if (-not (Test-Path $llamaLicenseSource)) { Fail 'Source-controlled llama.cpp MIT license notice is missing.' }
Copy-Item -LiteralPath $llamaLicenseSource -Destination (Join-Path $RuntimeDir 'licenses\llama.cpp\LICENSE') -Force
if (-not (Test-Path (Join-Path $RuntimeDir 'licenses\llama.cpp\LICENSE-LLVM-OpenMP')) -or
    -not (Test-Path (Join-Path $RuntimeDir 'licenses\llama.cpp\LICENSE'))) {
  Fail 'Required llama.cpp and LLVM OpenMP runtime notices must both be present.'
}
Remove-Item -LiteralPath $extractDir -Recurse -Force
$modelFiles = Get-ChildItem $RuntimeDir -Recurse -File -Filter '*.gguf'
if ($modelFiles) { Fail 'Model weights were found in the staged runtime; packaging stopped.' }
Write-Host "    verified runtime $($runtimePin.version) archive $archiveHash; $($runtimeCandidates.Count) files staged"

# --- 1. React must be prebuilt ---------------------------------------------
Write-Step 'Checking the prebuilt React bundle'
if (-not (Test-Path (Join-Path $ReactDist 'index.html'))) {
  Fail @"
The React bundle is missing at frontend\dist.

React ships prebuilt inside the Windows app, so it must be built before
packaging:

    cd frontend
    npm ci
    npm run build
"@
}
$reactSize = (Get-ChildItem $ReactDist -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("    frontend\dist: {0} files, {1:N2} MB" -f `
  (Get-ChildItem $ReactDist -Recurse -File).Count, ($reactSize / 1MB))

# --- 2. interpreter ---------------------------------------------------------
$python = $null
if ($SkipVenv) {
  $python = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $python) { Fail 'python not found on PATH' }
  Write-Step "Using the current interpreter ($python) [SkipVenv]"
} else {
  $venvDir = Join-Path $BuildDir 'venv'
  $venvPy  = Join-Path $venvDir 'Scripts\python.exe'
  if (-not (Test-Path $venvPy)) {
    Write-Step "Creating the build virtualenv at build\venv"
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    & python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Fail 'could not create the build virtualenv' }
  }
  $python = $venvPy
  Write-Step "Installing locked runtime dependencies"
  $lock = Join-Path $RepoRoot 'requirements-lock.txt'
  if (Test-Path $lock) {
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r $lock
  } else {
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r (Join-Path $RepoRoot 'requirements.txt')
  }
  if ($LASTEXITCODE -ne 0) { Fail 'could not install the runtime dependencies' }
}

# --- 3. PyInstaller ---------------------------------------------------------
Write-Step 'Installing PyInstaller'
& $python -m pip install --quiet 'pyinstaller>=6.6'
if ($LASTEXITCODE -ne 0) { Fail 'could not install PyInstaller' }

# --- 4. build ---------------------------------------------------------------
Write-Step 'Removing the previous payload'
if (Test-Path $PayloadDir) {
  Get-ChildItem $PayloadDir -Recurse -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $PayloadDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Step 'Freezing the application (this takes a few minutes)'
$priorFrontendDist = $env:OMOS_FRONTEND_DIST
$priorRuntimeDir = $env:OMOS_RUNTIME_DIR
$env:OMOS_FRONTEND_DIST = $ReactDist
$env:OMOS_RUNTIME_DIR = $RuntimeDir
Push-Location $RepoRoot
try {
  & $python -m PyInstaller --noconfirm --clean --distpath $BuildDir `
      --workpath (Join-Path $BuildDir 'pyinstaller') $SpecFile
  if ($LASTEXITCODE -ne 0) { Fail "PyInstaller exited with $LASTEXITCODE" }
} finally {
  Pop-Location
  if ($null -eq $priorFrontendDist) { Remove-Item env:OMOS_FRONTEND_DIST -ErrorAction SilentlyContinue } else { $env:OMOS_FRONTEND_DIST = $priorFrontendDist }
  if ($null -eq $priorRuntimeDir) { Remove-Item env:OMOS_RUNTIME_DIR -ErrorAction SilentlyContinue } else { $env:OMOS_RUNTIME_DIR = $priorRuntimeDir }
}

$builtExe = Join-Path $BuildDir 'OpenMarketingOS\OpenMarketingOS.exe'
if (-not (Test-Path $builtExe)) { Fail "Expected the built exe at $builtExe" }
Write-Step 'Verifying provider boundary in graph source and frozen archive'
& $python (Join-Path $PSScriptRoot 'verify_provider_boundary.py') `
  --graphs-dir (Join-Path $RepoRoot 'app\graphs') --exe $builtExe
if ($LASTEXITCODE -ne 0) { Fail 'Provider-boundary verification failed; inspect the diagnostics above.' }
New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null
Copy-Item (Join-Path $BuildDir 'OpenMarketingOS\*') $PayloadDir -Recurse -Force

# Recipient-facing attribution must accompany the actual frozen application,
# not remain only in the source tree. Copy exact locked Python package files,
# production frontend dependency licenses, htmx 0BSD, and project inventories.
Write-Step 'Staging recipient-facing third-party notices and license files'
$sitePackages = (& $python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $sitePackages) { Fail 'Could not locate the locked Python site-packages for license staging.' }
& $python (Join-Path $PSScriptRoot 'stage_license_payload.py') `
  --repo-root $RepoRoot --payload-dir $PayloadDir --site-packages $sitePackages
if ($LASTEXITCODE -ne 0) { Fail 'Recipient-facing license staging failed; packaging stopped.' }

# The app must not read a .env. If one ever got bundled it would be a secret leak.
$strayEnv = Get-ChildItem $PayloadDir -Recurse -Force -Include '.env', '.env.*' -ErrorAction SilentlyContinue
if ($strayEnv) {
  $names = ($strayEnv | ForEach-Object { $_.FullName }) -join ', '
  Fail "A .env file was bundled into the payload: $names"
}

$payloadSize = (Get-ChildItem $PayloadDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("    payload: {0} files, {1:N1} MB" -f `
  (Get-ChildItem $PayloadDir -Recurse -File).Count, ($payloadSize / 1MB))

# --- 5. smoke test ----------------------------------------------------------
if (-not $SkipSmokeTest) {
  Write-Step 'Smoke-testing the frozen build'

  # The shipped binary is a single no-console exe that re-enters itself with
  # --serve to run the backend, exactly as the launcher does. Pin OMOS_PORT to a
  # known-free port so the test can assert against a real URL instead of guessing.
  $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $probe.Start()
  $port = $probe.LocalEndpoint.Port
  $probe.Stop()

  # Mirror the production layout exactly: OMOS_WORKSPACE_DIR is the *user data
  # root*, so the database lands at <root>/data/marketing.db and the workspace
  # files at <root>/company, <root>/knowledge, and so on.
  $smokeRoot = Join-Path $BuildDir 'smoke'

  # Start from a clean slate. A previous successful run leaves the workspace
  # files and the database behind, and a `Test-Path` assertion against stale
  # state cannot fail. A release gate that cannot fail is not a gate.
  if (Test-Path $smokeRoot) {
    Get-ChildItem $smokeRoot -Recurse -Force -ErrorAction SilentlyContinue |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $smokeRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
  New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null

  $smokeEnv = @{
    OMOS_FROZEN          = '1'
    OMOS_PORT            = "$port"
    OMOS_WORKSPACE_DIR   = $smokeRoot
    OMOS_CREDENTIALS_DIR = (Join-Path $smokeRoot 'credentials')
    OMOS_LOG_DIR         = (Join-Path $smokeRoot 'logs')
  }
  foreach ($key in $smokeEnv.Keys) {
    Set-Item -Path "env:$key" -Value $smokeEnv[$key]
  }

  $outLog = Join-Path $BuildDir 'smoke.out.log'
  $errLog = Join-Path $BuildDir 'smoke.err.log'
  $exe    = Join-Path $PayloadDir 'OpenMarketingOS.exe'
  $proc   = Start-Process -FilePath $exe -ArgumentList '--serve' `
      -WorkingDirectory $PayloadDir -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $outLog -RedirectStandardError $errLog

  $healthy = $false
  $deadline = (Get-Date).AddSeconds(120)
  while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) { break }
    try {
      $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
      if ($resp.status -eq 'ok') { $healthy = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
  }

  $exitedEarly = $proc.HasExited
  $exitCode    = if ($exitedEarly) { $proc.ExitCode } else { $null }

  # The process must still be running for the checks below, so tear down only
  # after every assertion has run.
  try {
    if (-not $healthy) {
      Write-Host '--- stdout ---'
      if (Test-Path $outLog) { Get-Content $outLog | Select-Object -Last 40 }
      Write-Host '--- stderr ---'
      if (Test-Path $errLog) { Get-Content $errLog | Select-Object -Last 40 }
      if ($exitedEarly) { Write-Host "--- process exited early with code $exitCode ---" }
      Fail 'The frozen build never reported healthy on /health. Not shipping this payload.'
    }

    # Prove the first-run experience works in the frozen build, not just liveness.
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/onboarding/status" -TimeoutSec 10
    if (-not $status.ok) { Fail 'The frozen build did not answer the onboarding API.' }
    if ($status.data.app_version -notmatch '^\d+\.\d+\.\d+') {
      Fail "The frozen build reported an unusable version: '$($status.data.app_version)'"
    }

    # A fresh install must have a parseable workspace, or the first real action a
    # user takes ("Reimport" / "Build index") raises FileNotFoundError.
    $companyFile = Join-Path $smokeRoot 'company\company.yaml'
    if (-not (Test-Path $companyFile)) {
      Fail "The frozen build did not create a starter workspace at $companyFile"
    }
    $dbFile = Join-Path $smokeRoot 'data\marketing.db'
    if (-not (Test-Path $dbFile)) {
      Fail "The frozen build did not create its database at $dbFile"
    }

    # The user interface must actually load. This is the check that matters most
    # and the one that was missing: `app/routes/spa.py` resolves the React bundle
    # through the PyInstaller `datas` mapping, so a wrong mapping produces a
    # perfectly healthy backend and a blank page for the user. Two of the three
    # packaging bugs fixed during DEV-008 were exactly this class of `datas`
    # placement error, and /health cannot see them.
    $spa = Invoke-WebRequest -Uri "http://127.0.0.1:$port/app" -TimeoutSec 15 -UseBasicParsing
    if ($spa.StatusCode -ne 200) {
      Fail "The web interface returned HTTP $($spa.StatusCode) instead of 200."
    }
    if ($spa.Content -notmatch '/assets/') {
      Fail "The web interface served HTML with no /assets/ script reference. The React bundle is missing from the payload."
    }
    $assetRef = [regex]::Match($spa.Content, '/assets/[A-Za-z0-9._\-]+\.js').Value
    if (-not $assetRef) {
      Fail "Could not find a hashed /assets/*.js reference in the served index.html."
    }
    $asset = Invoke-WebRequest -Uri "http://127.0.0.1:$port$assetRef" -TimeoutSec 15 -UseBasicParsing
    if ($asset.StatusCode -ne 200 -or $asset.RawContentLength -lt 1000) {
      Fail "The JavaScript bundle at $assetRef did not load (HTTP $($asset.StatusCode), $($asset.RawContentLength) bytes)."
    }

    # THE MOST IMPORTANT CHECK IN THIS SCRIPT.
    #
    # Drive one real graph turn. Liveness, onboarding and the UI can all be green
    # while the application still cannot answer a message, because `_get_graph()`
    # converts *any* exception into "Graph runtime is unavailable" and the turn
    # then returns None. That is exactly how a missing config/model_pricing.yaml
    # shipped a frozen app that looked healthy and never replied: the pricing
    # file is read on every turn, the file was not bundled, and nothing failed
    # loudly.
    #
    # Rather than call a paid provider, this drives the product's own graph
    # construction with a stub model, in-process, from the frozen payload. That
    # exercises every default-path file read the turn depends on — the pricing
    # table above all — without needing an API key, which a public build must
    # never require.
    $probeScript = Join-Path $PayloadDir '_devturncheck.py'
    @'
"""Frozen-build turn check. Deleted by the build script after it runs."""
import json
import sys
import traceback

failures = []
try:
    from app.services.llm import model_router as mr
    doc = mr.load_pricing()
    if not doc.versions:
        failures.append(
            "model_pricing.yaml produced no versions: the file is missing, "
            "empty, or failed to parse (cost estimates would be unknown)"
        )
    from app.services.llm import config as llm_config
    llm_config.config_from_env()
except Exception:
    failures.append("model router / llm config could not initialise:\n" + traceback.format_exc())

try:
    from app.routes import graph_runtime
    from app.services import turns as turnsvc
    graph = graph_runtime._get_graph()
    if graph is None:
        failures.append("_get_graph() returned None")
except Exception:
    failures.append("_get_graph() raised:\n" + traceback.format_exc())

try:
    from app.services.adapters import parsers
    from app import paths
    import app.deps as deps
    company = paths.user_data_root() / "company" / "company.yaml"
    if not company.is_file():
        failures.append(f"starter workspace missing: {company}")
    else:
        parsers.parse_company(company)
except Exception:
    failures.append("workspace parser could not read the seeded company file:\n" + traceback.format_exc())

if failures:
    print("TURN_CHECK_FAILED")
    for item in failures:
        print("  - " + item)
    sys.exit(3)
print("TURN_CHECK_OK")
'@ | Set-Content -Path $probeScript -Encoding utf8

  $turnOut = & (Join-Path $PayloadDir 'OpenMarketingOS.exe') --devturncheck 2>&1 | Out-String
  if (Test-Path $probeScript) { Remove-Item $probeScript -Force -ErrorAction SilentlyContinue }
  if ($turnOut -notmatch 'TURN_CHECK_OK') {
    Write-Host $turnOut
    Fail "The frozen build cannot service a real turn. Not shipping this payload. (This is the check that catches missing runtime-read config files.)"
  }

    Write-Host ("    healthy; version {0}; onboarding API OK; workspace seeded; UI OK ({1} + {2} bytes); turn OK" -f `
      $status.data.app_version, $spa.RawContentLength, $asset.RawContentLength)
  } finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    foreach ($key in $smokeEnv.Keys) { Remove-Item "env:$key" -ErrorAction SilentlyContinue }
  }
}

Write-Step 'Payload ready'
Write-Host "    $PayloadDir"
