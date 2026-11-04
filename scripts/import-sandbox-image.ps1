# Import Anvil Docker Sandbox Image from File (ANVIL-57)
# Validates SHA-256 digest integrity, loads into Docker daemon, and verifies image ID prefix.
#
# Usage:
#   .\scripts\import-sandbox-image.ps1 [-ImagePath <path>] [-ExpectedTag <tag>] [-ExpectedIdPrefix <prefix>] [-SkipHashVerify]

param(
    [string]$ImagePath = "",
    [string]$ExpectedTag = "anvil-sandbox-rtems:7.1",
    [string]$ExpectedIdPrefix = "bc17ec29",
    [string]$ExpectedHash = "",
    [switch]$SkipHashVerify
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "=== Anvil Sandbox Image Importer (ANVIL-57) ===" -ForegroundColor Cyan

# 1. Locate Archive File
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
Write-Host "Expected ID  : Prefix '$ExpectedIdPrefix'" -ForegroundColor White

# 2. SHA-256 Integrity Verification
if (-not $SkipHashVerify) {
    Write-Host "`n1. Verifying SHA-256 integrity..." -ForegroundColor White
    $expected = $ExpectedHash.Trim().ToLower()
    
    # Check sidecar files if ExpectedHash not explicitly passed
    if (-not $expected) {
        $shaSidecar = "${ImagePath}.sha256"
        $manifestSidecar = "${ImagePath}.manifest.json"

        if (Test-Path $shaSidecar) {
            $content = (Get-Content $shaSidecar -Raw).Trim()
            $parts = $content -split '\s+'
            if ($parts.Count -gt 0 -and $parts[0].Length -eq 64) {
                $expected = $parts[0].ToLower()
                Write-Host "  Found checksum sidecar: $shaSidecar" -ForegroundColor Gray
            }
        } elseif (Test-Path $manifestSidecar) {
            try {
                $m = Get-Content $manifestSidecar -Raw | ConvertFrom-Json
                if ($m.sha256) {
                    $expected = ($m.sha256).ToString().Trim().ToLower()
                    Write-Host "  Found manifest sidecar: $manifestSidecar" -ForegroundColor Gray
                }
            } catch {
                # ignore json parse errors
            }
        }
    }

    if (-not $expected) {
        Write-Warning "No expected SHA-256 checksum found (.sha256 or .manifest.json sidecar). Use -ExpectedHash or -SkipHashVerify."
    } else {
        $hashInfo = Get-FileHash -Path $ImagePath -Algorithm SHA256
        $actual = $hashInfo.Hash.ToLower()

        if ($actual -ne $expected) {
            Write-Error "SHA-256 checksum mismatch!`nExpected: $expected`nActual  : $actual`nThe file may be incomplete or corrupted."
            exit 1
        }
        Write-Host "  [PASS] SHA-256 matched ($actual)" -ForegroundColor Green
    }
} else {
    Write-Warning "Skipping SHA-256 verification as requested (-SkipHashVerify)."
}

# 3. Load into Docker Daemon
Write-Host "`n2. Loading image into Docker daemon (docker load)..." -ForegroundColor White
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$loadOut = docker load -i $ImagePath 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker load failed: $loadOut"
    exit 1
}
$sw.Stop()
Write-Host "  docker load finished in $([math]::Round($sw.Elapsed.TotalSeconds, 1))s." -ForegroundColor Green
Write-Host "  Output: $loadOut" -ForegroundColor Gray

# 4. Verify Loaded Image ID & Tag
Write-Host "`n3. Verifying loaded image ID..." -ForegroundColor White
$existingShortId = (docker images -q $ExpectedTag 2>$null)
$inspectId = $null
if ($existingShortId) {
    $inspectId = docker inspect --format='{{.Id}}' $ExpectedTag 2>$null
}
if (-not $inspectId) {
    # If loaded under a different tag or bare hash, search for prefix
    $allImages = docker images --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}}' 2>$null | Out-String
    $matching = $allImages -split "`n" | Where-Object { $_ -match $ExpectedIdPrefix } | Select-Object -First 1
    if ($matching) {
        $rawId = ($matching -split '\s+')[0]
        Write-Host "  Tagging matching image ID $rawId as $ExpectedTag..." -ForegroundColor Yellow
        docker tag $rawId $ExpectedTag
        $inspectId = docker inspect --format='{{.Id}}' $ExpectedTag 2>$null
    }
}

if (-not $inspectId) {
    Write-Error "Could not find image with tag '$ExpectedTag' or ID prefix '$ExpectedIdPrefix' after import."
    exit 1
}

if ($inspectId -notmatch $ExpectedIdPrefix) {
    Write-Error "Image loaded with ID '$inspectId', which does NOT match expected prefix '$ExpectedIdPrefix'."
    exit 1
}

$shortId = if ($inspectId -match 'sha256:([0-9a-f]{12})') { $matches[1] } else { $inspectId }
Write-Host "  [PASS] Sandbox image ready: $ExpectedTag (ID: $shortId, matches prefix $ExpectedIdPrefix)" -ForegroundColor Green

Write-Host "`n[SUCCESS] Sandbox image imported successfully." -ForegroundColor Cyan

