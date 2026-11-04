# Export Anvil Docker Sandbox Image to File (ANVIL-57)
# Wraps python -m anvil.runtime.image_file export
#
# Usage:
#   .\scripts\export-sandbox-image.ps1 [-Image <tag>] [-OutputPath <path>] [-Force]

param(
    [string]$Image = "anvil-sandbox-rtems:7.1",
    [string]$OutputPath = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

if (-not $OutputPath) {
    $safeName = ($Image -replace '[:/]', '-')
    $OutputPath = Join-Path $root "data\sandbox\${safeName}.tar"
}

$outDir = Split-Path -Parent $OutputPath
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

Write-Host "=== Anvil Sandbox Image Exporter (ANVIL-57) ===" -ForegroundColor Cyan
Write-Host "Target Image  : $Image" -ForegroundColor White
Write-Host "Output Archive: $OutputPath" -ForegroundColor White

$pyArgs = @("run", "--directory", $backend, "python", "-m", "anvil.runtime.image_file", "export", "--image", $Image, "--output", $OutputPath)
if ($Force) {
    $pyArgs += "--force"
}

& uv @pyArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Image export failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
