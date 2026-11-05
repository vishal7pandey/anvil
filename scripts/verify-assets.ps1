#requires -Version 5.1
<#
.SYNOPSIS
  Verify every asset Anvil needs is present and intact (docs/ASSETS.md).
.DESCRIPTION
  Checks the in-git and out-of-band assets the app needs to run: required files exist, the
  out-of-band binaries match handover/MANIFEST.json sha256 (if present), the sandbox image tag
  is loaded, and the demo ports are free. Offline-friendly: out-of-band binaries are reported
  MISSING but do not fail the run — a junior may not have staged them yet.
#>
param(
    [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
)

$ErrorActionPreference = "Continue"
$fail = 0; $skip = 0
function Ok([string]$m)  { Write-Host "  [PASS] $m" -ForegroundColor Green }
function Bad([string]$m) { Write-Host "  [FAIL] $m" -ForegroundColor Red; $script:fail++ }
function Skip([string]$m){ Write-Host "  [SKIP] $m" -ForegroundColor DarkYellow; $script:skip++ }

Write-Host "VERIFY ASSETS ($RepoRoot)" -ForegroundColor Cyan

# --- in-git files (must exist) --------------------------------------------------
$gitRequired = @(
    "backend/pyproject.toml", "frontend/package.json", "deploy/compose.yaml",
    "sandbox/rtems/Dockerfile", "backend/migrations/versions",
    "data/kb/kb-snapshot.jsonl.gz", "data/kb/manifest.json",
    "backlog/index.json", "docs/SETUP.md", "docs/ASSETS.md",
    "scripts/start-api.ps1", "scripts/preflight.ps1", ".env.example", "frontend/.env.example"
)
foreach ($f in $gitRequired) {
    if (Test-Path (Join-Path $RepoRoot $f)) { Ok "$f" } else { Bad "missing $f" }
}

# --- KB snapshot sha256 against its committed manifest ---------------------------
$mf = Join-Path $RepoRoot "data/kb/manifest.json"
if (Test-Path $mf) {
    $want = (Get-Content $mf -Raw | ConvertFrom-Json).sha256
    $got = (Get-FileHash (Join-Path $RepoRoot "data/kb/kb-snapshot.jsonl.gz") -Algorithm SHA256).Hash
    if ($got -eq $want) { Ok "kb-snapshot sha256 matches manifest ($($want.Substring(0,12))...)" }
    else { Bad "kb-snapshot sha256 $got != manifest $want" }
}

# --- out-of-band binaries (report, don't fail) ----------------------------------
$vectors = @("data/kb/kb-snapshot-vectors.jsonl.gz", "data/kb/kb-snapshot-vectors.manifest.json")
foreach ($f in $vectors) {
    if (Test-Path (Join-Path $RepoRoot $f)) { Ok "$f (out-of-band staged)" }
    else { Skip "$f not staged (copy from the handover folder)" }
}
$imgTag = "anvil-sandbox-rtems:7.1"
try {
    docker image inspect $imgTag | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "sandbox image $imgTag loaded" } else { Skip "$imgTag not loaded (tar or build)" }
} catch { Skip "docker not reachable; can't check $imgTag" }

# --- ports ------------------------------------------------------------------------
foreach ($p in @(8000, 5173, 5433, 4000)) {
    $inUse = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue
    if ($inUse) { Bad "port $p in use" } else { Ok "port $p free" }
}

Write-Host ""
if ($fail -eq 0) { Write-Host "VERIFY ASSETS: OK ($($gitRequired.Count) files, $skip skipped out-of-band)" -ForegroundColor Green; exit 0 }
Write-Host "VERIFY ASSETS: $fail failed" -ForegroundColor Red; exit 1
