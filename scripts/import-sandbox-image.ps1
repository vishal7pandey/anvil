# Import Anvil Docker Sandbox Image from File (ANVIL-57)
# Wraps python -m anvil.runtime.image_file import
#
# Usage:
#   .\scripts\import-sandbox-image.ps1 [-ImagePath <path>] [-ExpectedTag <tag>] [-SkipHashVerify] [-ExpectedHash <hash>]

param(
    [string]$ImagePath = "",
    [string]$ExpectedTag = "anvil-sandbox-rtems:7.1",
    [string]$ExpectedHash = "",
    [switch]$SkipHashVerify
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

Write-Host "=== Anvil Sandbox Image Importer (ANVIL-57) ===" -ForegroundColor Cyan

# Locate Archive File if omitted
if (-not $ImagePath) {
    $candidates = @(
        (Join-Path $root "data\sandbox\anvil-sandbox-rtems-7.1.tar"),
        (Join-Path $root "data\sandbox\anvil-sandbox-rtems-7.tar")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $ImagePath = $c
            break
        }
    }
    if (-not $ImagePath) {
        $found = Get-ChildItem (Join-Path $root "data\sandbox\*.tar") -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {
            $ImagePath = $found.FullName
        }
    }
}

if (-not $ImagePath -or -not (Test-Path $ImagePath)) {
    Write-Error "Sandbox image archive not found. Provide path via -ImagePath (e.g. data\sandbox\anvil-sandbox-rtems-7.1.tar)."
    exit 1
}

Write-Host "Archive File : $ImagePath" -ForegroundColor White
Write-Host "Expected Tag : $ExpectedTag" -ForegroundColor White

$pyArgs = @("run", "--directory", $backend, "python", "-m", "anvil.runtime.image_file", "import", "--archive", $ImagePath, "--tag", $ExpectedTag)
if ($SkipHashVerify) {
    $pyArgs += "--skip-verify"
}
if ($ExpectedHash) {
    $pyArgs += @("--expected-hash", $ExpectedHash)
}

& uv @pyArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Image import failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
