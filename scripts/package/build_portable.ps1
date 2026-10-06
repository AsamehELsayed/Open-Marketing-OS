#Requires -Version 5.1
<#
.SYNOPSIS
  Builds OpenMarketingOS-Portable.zip — the same payload, no installer.

.DESCRIPTION
  A zip of the frozen payload. Two properties matter and are asserted here:

    1. Running from the extracted folder must work. Because `app.paths`
       resolves the user data root from %LOCALAPPDATA% and never from the
       executable's folder, a portable run writes to the same user data as an
       installed run. Extracting to Desktop\Downloads does NOT scatter state
       next to the exe.
    2. No .env, no developer leftovers.

  Marked optional in the release workflow: a portable ZIP is a convenience, and
  its absence must never fail a release.

.EXAMPLE
  pwsh -File scripts\package\build_portable.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Payload  = Join-Path $RepoRoot 'build\payload'
$DistDir  = Join-Path $RepoRoot 'dist'
$ZipPath  = Join-Path $DistDir 'OpenMarketingOS-Portable.zip'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Fail($message) { Write-Host "FAILED: $message" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $Payload 'OpenMarketingOS.exe'))) {
  Fail @"
The frozen payload is missing.

Run the packaging build first:
    pwsh -File scripts\package\build_windows.ps1
"@
}

$stray = Get-ChildItem $Payload -Recurse -Force -Include '.env', '.env.*' -ErrorAction SilentlyContinue
if ($stray) { Fail 'A .env file is present in the payload and must not be shipped.' }

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue }

Write-Step 'Compressing the portable ZIP'
# Stage under a single top-level folder named after the product, matching the
# installed layout. Compressing the staging directory itself would produce an
# archive whose root is `_portable_stage\`, so the user's "double-click
# OpenMarketingOS.exe" instruction would send them one folder too deep.
$stageParent = Join-Path $DistDir '_portable_stage'
if (Test-Path $stageParent) {
  Get-ChildItem $stageParent -Recurse -Force -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $stageParent -Recurse -Force -ErrorAction SilentlyContinue
}
$stage = Join-Path $stageParent 'OpenMarketingOS'
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item (Join-Path $Payload '*') $stage -Recurse -Force

@'
Open Marketing OS - portable edition

1. Extract this ZIP. You will get a single folder called "OpenMarketingOS".
2. Open it and double-click OpenMarketingOS.exe.
   (Do not run the program from inside the ZIP itself.)
3. Your browser opens automatically.

Your projects, files and credentials are stored in
%LOCALAPPDATA%\OpenMarketingOS - the same place the installed edition uses -
so moving or deleting this folder never loses your work.

This is a beta and is not code-signed, so Windows SmartScreen may show an
"Unknown publisher" warning. See the README for how to verify the download.
'@ | Set-Content -Path (Join-Path $stage 'READ-ME-FIRST.txt') -Encoding utf8

# Compress $stage itself WITH its base directory, so the archive root is exactly
# "OpenMarketingOS\". .NET is used rather than Compress-Archive because
# Compress-Archive's handling of a directory argument differs between PowerShell
# versions and once produced an archive with BOTH "OpenMarketingOS\..." and bare
# root entries.
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
  $stage, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $true)

Get-ChildItem $stageParent -Recurse -Force -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $stageParent -Recurse -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $ZipPath)) { Fail "Expected the portable ZIP at $ZipPath" }

# Every entry must live under a single "OpenMarketingOS" folder at the archive
# root, or the user's extraction instructions are wrong. Entry names are
# normalised to forward slashes first: the ZIP spec mandates '/', but .NET on
# Windows has historically written '\', and the check must be correct either way.
$archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
  $entryNames = @($archive.Entries | ForEach-Object { $_.FullName -replace '\\', '/' })
} finally {
  $archive.Dispose()
}
$bad = @($entryNames | Where-Object { $_ -notlike 'OpenMarketingOS/*' })
if ($bad.Count -gt 0) {
  $sample = ($bad | Select-Object -First 5) -join ', '
  Fail "These $($bad.Count) archive entries are not under the OpenMarketingOS/ root: $sample"
}
if (-not ($entryNames -contains 'OpenMarketingOS/OpenMarketingOS.exe')) {
  Fail "The archive does not contain OpenMarketingOS/OpenMarketingOS.exe at the expected path."
}

$size = (Get-Item $ZipPath).Length
Write-Host ("    {0}  ({1:N1} MB)  {2} entries, root: OpenMarketingOS/" -f `
  (Split-Path $ZipPath -Leaf), ($size / 1MB), $entryNames.Count)
Write-Step 'Portable ZIP ready'
Write-Host "    $ZipPath"
