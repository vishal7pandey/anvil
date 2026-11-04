# Export Anvil Docker Sandbox Image to File (ANVIL-57)
# Saves a local Docker image to a tar archive with SHA-256 integrity checksums and manifest.
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

if (-not $OutputPath) {
    $safeName = ($Image -replace '[:/]', '-')
    $OutputPath = Join-Path $root "data\sandbox\${safeName}.tar"
}

# Resolve to absolute path
$outDir = Split-Path -Parent $OutputPath
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

Write-Host "=== Anvil Sandbox Image Exporter (ANVIL-57) ===" -ForegroundColor Cyan
Write-Host "Target Image : $Image" -ForegroundColor White
Write-Host "Output Archive: $OutputPath" -ForegroundColor White

# 1. Verify Docker Daemon & Image Presence
$existingShortId = (docker images -q $Image 2>$null)
if (-not $existingShortId) {
    Write-Error "Docker image '$Image' not found locally. Build or pull it first."
    exit 1
}
$imageId = docker inspect --format='{{.Id}}' $Image

Write-Host "Found image ID: $imageId" -ForegroundColor Gray

# 2. Check if destination exists
if ((Test-Path $OutputPath) -and -not $Force) {
    Write-Host "Output file already exists. Use -Force to overwrite." -ForegroundColor Yellow
    exit 0
}

# 3. Export Image via docker save
Write-Host "Exporting image (docker save)... This may take 1-2 minutes." -ForegroundColor Yellow
$sw = [System.Diagnostics.Stopwatch]::StartNew()

docker save $Image -o $OutputPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $OutputPath)) {
    Write-Error "docker save failed for image $Image"
    exit 1
}
$sw.Stop()
Write-Host "Image exported in $([math]::Round($sw.Elapsed.TotalSeconds, 1))s." -ForegroundColor Green

# 4. Calculate SHA-256 Digest
Write-Host "Computing SHA-256 checksum..." -ForegroundColor Yellow
$hashInfo = Get-FileHash -Path $OutputPath -Algorithm SHA256
$digest = $hashInfo.Hash.ToLower()
$fileName = Split-Path -Leaf $OutputPath
$fileSize = (Get-Item $OutputPath).Length

Write-Host "SHA-256: $digest" -ForegroundColor White
Write-Host "Size   : $([math]::Round($fileSize / 1MB, 2)) MB" -ForegroundColor White

# 5. Write Checksum (.sha256) and Manifest (.manifest.json)
$shaPath = "${OutputPath}.sha256"
"${digest} *${fileName}`n" | Set-Content -Path $shaPath -NoNewline -Encoding utf8
Write-Host "Wrote checksum file: $shaPath" -ForegroundColor Green

$manifestPath = "${OutputPath}.manifest.json"
$manifest = [PSCustomObject]@{
    image_tag       = $Image
    image_id        = $imageId.Trim()
    file_name       = $fileName
    file_size_bytes = $fileSize
    sha256          = $digest
    exported_at     = (Get-Date).ToUniversalTime().ToString("o")
    format_version  = 1
}
$manifestJson = $manifest | ConvertTo-Json -Depth 4
$manifestJson | Set-Content -Path $manifestPath -Encoding utf8
Write-Host "Wrote manifest file: $manifestPath" -ForegroundColor Green

Write-Host "`n[SUCCESS] Sandbox image export complete." -ForegroundColor Cyan

