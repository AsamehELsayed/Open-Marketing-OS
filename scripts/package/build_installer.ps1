#Requires -Version 5.1
<#
.SYNOPSIS
  Builds a profile-specific Open Marketing OS beta installer with Inno Setup.

.DESCRIPTION
  Wraps the frozen payload produced by scripts\package\build_windows.ps1 into a
  normal Windows installer. Requires Inno Setup 6.

  Fails loudly rather than producing a broken installer, because a public
  release asset that a user cannot install is worse than no release.

.EXAMPLE
  pwsh -File scripts\package\build_installer.ps1 -EditionProfile Local -RunRoot build/local

.EXAMPLE
  pwsh -File scripts\package\build_installer.ps1 -EditionProfile OpenRouter -RunRoot build/openrouter
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Local', 'OpenRouter')]
  [string]$EditionProfile,
  [string]$IsccPath,
  [switch]$SkipPayloadCheck,
  [Parameter(Mandatory = $true)]
  [string]$RunRoot
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$AppPathsFile = Join-Path $RepoRoot 'app\paths.py'
$AppPathsSource = Get-Content -LiteralPath $AppPathsFile -Raw
$VersionMatch = [regex]::Match($AppPathsSource, 'APP_VERSION\s*=\s*"([^"]+)"')
if (-not $VersionMatch.Success) {
  throw 'Could not read canonical APP_VERSION from app\paths.py.'
}
$ReleaseVersion = $VersionMatch.Groups[1].Value
$ProfileSlug = $EditionProfile.ToLowerInvariant()
$ProfileId = "omos-$ProfileSlug-v$ReleaseVersion"
$InstallerName = "OMOS-$EditionProfile-v$ReleaseVersion.exe"
$ProfileDescriptor = Join-Path $RepoRoot "packaging\profiles\$ProfileSlug.json"
if (-not (Test-Path -LiteralPath $ProfileDescriptor -PathType Leaf)) {
  throw "Profile descriptor is missing: $ProfileDescriptor"
}
$profile = $null
try {
  $profile = Get-Content -LiteralPath $ProfileDescriptor -Raw | ConvertFrom-Json -ErrorAction Stop
} catch {
  throw "Profile descriptor is invalid JSON: $ProfileDescriptor"
}
if ($profile.schema_version -ne 1 -or $profile.profile_id -ne $ProfileId -or
    $profile.edition -ne $EditionProfile -or $profile.release_version -ne $ReleaseVersion) {
  throw "Profile descriptor identity does not match requested build: $ProfileId"
}
if ($profile.local_model_catalog.model_bundled -ne $false -or
    $profile.credential_policy.credential_bundled -ne $false) {
  throw 'Profile descriptors must never bundle model weights or credential material.'
}
$IsolatedRunRoot = $null
if ($RunRoot) {
  $IsolatedRunRoot = if ([IO.Path]::IsPathRooted($RunRoot)) { [IO.Path]::GetFullPath($RunRoot) } else { [IO.Path]::GetFullPath((Join-Path $RepoRoot $RunRoot)) }
  $repoPrefix = $RepoRoot.TrimEnd('\') + '\'
  if (-not $IsolatedRunRoot.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    Fail 'RunRoot must be inside the repository so the installer remains isolated and auditable.'
  }
  if ($IsolatedRunRoot -eq (Join-Path $RepoRoot 'build') -or $IsolatedRunRoot -eq (Join-Path $RepoRoot 'dist')) {
    Fail 'RunRoot must not be the shared build or dist directory.'
  }
  $Payload = Join-Path $IsolatedRunRoot 'payload'
  $DistDir = Join-Path $IsolatedRunRoot 'output'
} else {
  $Payload = Join-Path $RepoRoot 'build\payload'
  $DistDir = Join-Path $RepoRoot 'dist'
}
$IssFile   = Join-Path $RepoRoot 'packaging\omos.iss'
$Installer = Join-Path $DistDir $InstallerName

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Fail($message) { Write-Host "FAILED: $message" -ForegroundColor Red; exit 1 }

# --- locate Inno Setup ------------------------------------------------------
function Find-Iscc {
  param([string]$Explicit)

  if ($Explicit) {
    if (Test-Path $Explicit) { return $Explicit }
    Fail "ISCC not found at the supplied path: $Explicit"
  }
  $onPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
  if ($onPath) { return $onPath.Source }
  # Order matters: the 64-bit Program Files first, then the per-user install
  # location. `winget install JRSoftware.InnoSetup --scope user` and some
  # chocolatey configurations install under LOCALAPPDATA, and a build script
  # that only looks in Program Files fails on a perfectly normal machine.
  $candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 5\ISCC.exe"
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { return $candidate }
  }
  return $null
}

$iscc = Find-Iscc -Explicit $IsccPath
if (-not $iscc) {
  Fail @"
Inno Setup 6 was not found.

Install it with one of:
    choco install innosetup
    winget install JRSoftware.InnoSetup
or download https://jrsoftware.org/isdl.php

Then re-run this script, or pass -IsccPath <path to ISCC.exe>.
"@
}
Write-Step "Using ISCC at $iscc"

# --- payload ----------------------------------------------------------------
if (-not $SkipPayloadCheck) {
  Write-Step 'Checking the frozen payload'
  if (-not (Test-Path (Join-Path $Payload 'OpenMarketingOS.exe'))) {
    Fail @"
The frozen payload is missing.

Run the packaging build first:
    pwsh -File scripts\package\build_windows.ps1
"@
  }
  $stray = Get-ChildItem $Payload -Recurse -Force -Include '.env', '.env.*' -ErrorAction SilentlyContinue
  if ($stray) { Fail "A .env file is present in the payload and must not be shipped." }
  $modelWeights = Get-ChildItem $Payload -Recurse -File -Filter '*.gguf' -ErrorAction SilentlyContinue
  if ($modelWeights) { Fail 'GGUF model weights must never be shipped in either installer.' }
}

# --- compile ----------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
if (Test-Path $Installer) {
  Remove-Item $Installer -Force -ErrorAction SilentlyContinue
}

Write-Step 'Compiling the installer (LZMA2, this takes a few minutes)'
Push-Location (Join-Path $RepoRoot 'packaging')
try {
  if ($IsolatedRunRoot) {
    $issPayload = $Payload.Replace('/', '\')
    $issOutput = $DistDir.Replace('/', '\')
    $issProfile = $ProfileDescriptor.Replace('/', '\')
    $versionDefine = '/DReleaseVersion="' + $ReleaseVersion + '"'
    & $iscc "/DPayloadDir=$issPayload" "/DOutputDir=$issOutput" "/DEditionProfile=$ProfileSlug" "/DProfileId=$ProfileId" "/DProfileDescriptor=$issProfile" "/DOutputBaseFilename=$([IO.Path]::GetFileNameWithoutExtension($InstallerName))" $versionDefine $IssFile
  } else {
    $issProfile = $ProfileDescriptor.Replace('/', '\')
    $versionDefine = '/DReleaseVersion="' + $ReleaseVersion + '"'
    & $iscc "/DEditionProfile=$ProfileSlug" "/DProfileId=$ProfileId" "/DProfileDescriptor=$issProfile" "/DOutputBaseFilename=$([IO.Path]::GetFileNameWithoutExtension($InstallerName))" $versionDefine $IssFile
  }
  if ($LASTEXITCODE -ne 0) { Fail "ISCC exited with $LASTEXITCODE" }
} finally {
  Pop-Location
}

if (-not (Test-Path $Installer)) { Fail "Expected the installer at $Installer" }

$size = (Get-Item $Installer).Length
Write-Host ("    {0}  ({1:N1} MB)" -f (Split-Path $Installer -Leaf), ($size / 1MB))

Write-Host "    version from app\paths.py: $ReleaseVersion"

Write-Step 'Installer ready'
Write-Host "    $Installer"
