# Anvil Preflight Verification (T-30 min)
# Validates environment readiness before live presentations and recorded rehearsals.
# Exit code: 0 on all PASS (including skipped offline checks), 1 if any check fails.
#
# Usage:
#   .\scripts\preflight.ps1 [-Presenter <agent_or_human>] [-Offline]

param(
    [string]$Presenter = "antigravity",
    [string]$ExpectedSandboxImage = "anvil-sandbox-rtems:7.1",
    [string]$ExpectedSandboxIdPrefix = "bc17ec29",
    [switch]$Offline
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$passedCount = 0
$skippedCount = 0
$failedCount = 0

function Write-CheckPass {
    param([string]$Name, [string]$Details = "")
    if ($Details) {
        Write-Host "  [PASS] ${Name}: $Details" -ForegroundColor Green
    } else {
        Write-Host "  [PASS] ${Name}" -ForegroundColor Green
    }
    $script:passedCount++
}

function Write-CheckFail {
    param([string]$Name, [string]$Reason, [string]$FixHint)
    Write-Host "  [FAIL] ${Name}: $Reason" -ForegroundColor Red
    if ($FixHint) {
        Write-Host "         FIX: $FixHint" -ForegroundColor Yellow
    }
    $script:failedCount++
}

function Write-CheckSkip {
    param([string]$Name, [string]$Reason)
    Write-Host "  [SKIP] ${Name}: $Reason" -ForegroundColor Yellow
    $script:skippedCount++
}

Write-Host "=== Anvil Demo Preflight Verification ===" -ForegroundColor Cyan
Write-Host "Repository root: $root" -ForegroundColor Gray
Write-Host "Presenter target: $Presenter" -ForegroundColor Gray
Write-Host ""

# -----------------------------------------------------------------------------
# Discover Environment & Credentials (root, backend, frontend)
# -----------------------------------------------------------------------------
$envFiles = @(
    (Join-Path $root ".env"),
    (Join-Path $backend ".env"),
    (Join-Path $frontend ".env"),
    "C:\Dev\projects\anvil\.env"
)

foreach ($ef in $envFiles) {
    if (Test-Path $ef) {
        Get-Content $ef -ErrorAction SilentlyContinue | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and ($line -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")) {
                $varName = $matches[1]
                $varValue = $matches[2].Trim().Trim('"').Trim("'")
                if (-not [System.Environment]::GetEnvironmentVariable($varName, [System.EnvironmentVariableTarget]::Process)) {
                    [System.Environment]::SetEnvironmentVariable($varName, $varValue, [System.EnvironmentVariableTarget]::Process)
                }
            }
        }
    }
}

# Normalize Atlassian aliases
if (-not $env:ATLASSIAN_SITE -and $env:JIRA_URL) {
    $env:ATLASSIAN_SITE = $env:JIRA_URL
}
if (-not $env:ATLASSIAN_EMAIL -and $env:JIRA_USER_EMAIL) {
    $env:ATLASSIAN_EMAIL = $env:JIRA_USER_EMAIL
}
if (-not $env:ATLASSIAN_API_TOKEN -and $env:JIRA_API_TOKEN) {
    $env:ATLASSIAN_API_TOKEN = $env:JIRA_API_TOKEN
}

$hasAtlassian = [bool]($env:ATLASSIAN_EMAIL -and $env:ATLASSIAN_API_TOKEN)
$isOffline = $Offline -or (-not $hasAtlassian)

# -----------------------------------------------------------------------------
# Check 1: Docker Sandbox Image (RTEMS 7.1)
# -----------------------------------------------------------------------------
Write-Host "1. Checking Docker Sandbox Image ($ExpectedSandboxImage)..." -ForegroundColor White
$imageId = docker inspect --format='{{.Id}}' $ExpectedSandboxImage 2>$null
if (-not $imageId) {
    Write-CheckFail "Sandbox image" "$ExpectedSandboxImage is not present locally" "Offline: .\scripts\import-sandbox-image.ps1 -ImagePath <archive.tar>; Online: docker build -f sandbox/rtems/Dockerfile.leon3 -t $ExpectedSandboxImage sandbox/rtems"
} elseif (-not ($imageId -match $ExpectedSandboxIdPrefix)) {
    Write-CheckFail "Sandbox image ID" "$ExpectedSandboxImage ID ($imageId) does not match expected prefix $ExpectedSandboxIdPrefix" "Re-import: .\scripts\import-sandbox-image.ps1 -ImagePath <archive.tar>; Or rebuild: docker build -f sandbox/rtems/Dockerfile.leon3 -t $ExpectedSandboxImage sandbox/rtems"
} else {
    $shortId = if ($imageId -match 'sha256:([0-9a-f]{12})') { $matches[1] } else { $imageId }
    Write-CheckPass "Sandbox image" "$ExpectedSandboxImage present (ID: $shortId, matches $ExpectedSandboxIdPrefix)"
}

# -----------------------------------------------------------------------------
# Check 2: Python Environment (CPython 3.13 signed, Smart App Control bypass)
# -----------------------------------------------------------------------------
Write-Host "`n2. Checking Backend Python Environment..." -ForegroundColor White
$pyTestCmd = "import unicodedata, requests; print('OK')"
$pyCheck = uv run --no-sync --directory $backend python -c $pyTestCmd 2>&1
if ($LASTEXITCODE -eq 0 -and $pyCheck -match "OK") {
    $pyVersion = uv run --no-sync --directory $backend python -c "import sys; print(sys.version.split()[0])" 2>$null
    Write-CheckPass "Python runtime" "Imports unicodedata and requests successfully (Python $pyVersion, signed CPython)"
} else {
    Write-CheckFail "Python runtime" "unicodedata or requests import blocked by App Control or missing ($pyCheck)" "uv sync --directory backend --python 'C:/Users/yedna/AppData/Local/Python/pythoncore-3.13-64/python.exe'"
}

# -----------------------------------------------------------------------------
# Check 3: Control API on :8000
# -----------------------------------------------------------------------------
Write-Host "`n3. Checking Control API on :8000..." -ForegroundColor White
$conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-CheckFail "Control API" "No process listening on port :8000" "Start API from latest main: .\scripts\start-api.ps1"
} else {
    $pids = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($pids.Count -gt 1) {
        Write-CheckFail "Control API" "Multiple processes ($($pids -join ', ')) listening on :8000" "Kill duplicate processes and keep exactly one API instance"
    } else {
        $apiPid = $pids[0]
        $proc = Get-Process -Id $apiPid -ErrorAction SilentlyContinue
        $procStartTime = $null
        if ($proc -and $proc.StartTime) {
            try {
                $procStartTime = $proc.StartTime.ToUniversalTime()
            } catch {
                $procStartTime = $null
            }
        }

        # Best-effort fetch to ensure origin/main ref is fresh (safe if not in git repo or offline)
        $mainCommitDate = $null
        try {
            git -C $root fetch -q origin 2>$null
            $mainCommitDateStr = git -C $root log -1 --format=%cI origin/main 2>$null
            if ($mainCommitDateStr -and $mainCommitDateStr.Trim()) {
                $mainCommitDate = [DateTimeOffset]::Parse($mainCommitDateStr.Trim()).UtcDateTime
            }
        } catch {
            $mainCommitDate = $null
        }

        # Token discovery across standard locations (never agent worktrees)
        $tokenCandidates = @()
        if ($env:ANVIL_DEMO_TOKEN) { $tokenCandidates += $env:ANVIL_DEMO_TOKEN }
        if ($env:VITE_DEMO_TOKEN) { $tokenCandidates += $env:VITE_DEMO_TOKEN }

        if ($tokenCandidates.Count -eq 0) {
            Write-CheckFail "Control API Token" "ANVIL_DEMO_TOKEN is missing from environment and .env files" "Set ANVIL_DEMO_TOKEN in .env or environment"
        } else {
            # Health endpoint check
            $apiOk = $false
            foreach ($t in $tokenCandidates) {
                try {
                    $headers = @{ "Authorization" = "Bearer $t" }
                    $apiResp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/sessions" -Headers $headers -Method Get -TimeoutSec 5 -UseBasicParsing
                    if ($apiResp.StatusCode -eq 200) {
                        $apiOk = $true
                        break
                    }
                } catch {
                    # try next candidate
                }
            }

            if (-not $apiOk) {
                Write-CheckFail "Control API Health" "GET /api/sessions returned unauthorized or connection failed with discovered tokens" "Verify ANVIL_DEMO_TOKEN in .env and restart API process"
            } elseif ($mainCommitDate -and $procStartTime -and ($procStartTime -lt $mainCommitDate)) {
                Write-CheckFail "Control API Freshness" "Process PID $apiPid started at $procStartTime, older than latest main ($mainCommitDate)" "Restart API to ensure latest main code is loaded: .\scripts\start-api.ps1"
            } else {
                $freshnessNote = if ($procStartTime -and $mainCommitDate) { "; started after latest main commit ($procStartTime)" } elseif ($procStartTime) { "; started at $procStartTime" } else { "" }
                Write-CheckPass "Control API" "PID $apiPid listening on :8000$freshnessNote; GET /api/sessions returns 200"
            }
        }
    }
}

# -----------------------------------------------------------------------------
# Check 4: Local Stack (LiteLLM Gateway & Postgres)
# -----------------------------------------------------------------------------
Write-Host "`n4. Checking Local Stack Containers..." -ForegroundColor White
$gwStatus = docker inspect --format='{{.State.Health.Status}}' anvil-gateway 2>$null
$pgStatus = docker inspect --format='{{.State.Health.Status}}' anvil-postgres 2>$null

$gwHttp = $false
try {
    $gwLive = Invoke-WebRequest -Uri "http://127.0.0.1:4000/health/liveliness" -Method Get -TimeoutSec 5 -UseBasicParsing
    if ($gwLive.StatusCode -eq 200) {
        $gwHttp = $true
    }
} catch {
    try {
        $gwAuth = Invoke-WebRequest -Uri "http://127.0.0.1:4000/health" -Headers @{"Authorization"="Bearer sk-anvil-local"} -Method Get -TimeoutSec 5 -UseBasicParsing
        if ($gwAuth.StatusCode -eq 200) { $gwHttp = $true }
    } catch {
        $gwHttp = $false
    }
}

$gwOk = ($gwStatus -eq "healthy" -and $gwHttp)
$pgOk = ($pgStatus -eq "healthy")

if ($gwOk -and $pgOk) {
    Write-CheckPass "Local Stack" "anvil-gateway (healthy, :4000) and anvil-postgres (healthy, :5433) are operational"
} else {
    $stackFailures = @()
    if (-not $gwOk) { $stackFailures += "anvil-gateway (status: $gwStatus, HTTP: $gwHttp)" }
    if (-not $pgOk) { $stackFailures += "anvil-postgres (status: $pgStatus)" }
    Write-CheckFail "Local Stack" ($stackFailures -join "; ") "docker compose -f deploy/compose.yaml up -d --wait"
}

# -----------------------------------------------------------------------------
# Check 5: Build Lock State
# -----------------------------------------------------------------------------
Write-Host "`n5. Checking RTEMS Build Lock..." -ForegroundColor White
$lockFile = Join-Path $root "..\comms\locks\rtems-build.lock"
if (-not (Test-Path $lockFile)) {
    $lockFile = Join-Path $root "comms\locks\rtems-build.lock"
}
if (-not (Test-Path $lockFile)) {
    $lockFile = "C:\Dev\comms\locks\rtems-build.lock"
}

if (-not (Test-Path $lockFile)) {
    Write-CheckPass "Build Lock" "No lockfile present; build resources are completely free"
} else {
    $lockContent = Get-Content $lockFile -Raw
    if ($lockContent -match "(?m)^agent:\s*(.*)$") {
        $lockOwner = $matches[1].Trim()
        if ($lockOwner -eq $Presenter -or $lockOwner -eq "presenter") {
            Write-CheckPass "Build Lock" "Lock held by presenter ($lockOwner)"
        } elseif ($lockOwner -eq "none" -or $lockOwner -eq "") {
            Write-CheckPass "Build Lock" "No active agent lock; free placeholder present (agent: $lockOwner)"
        } else {
            $lockPurpose = ""
            if ($lockContent -match "(?m)^purpose:\s*(.*)$") { $lockPurpose = $matches[1].Trim() }
            Write-CheckFail "Build Lock" "Lock held by '$lockOwner' (purpose: $lockPurpose)" "Wait for current agent run to finish, or request lock handover in comms"
        }
    } else {
        Write-CheckPass "Build Lock" "Lockfile exists but has no active agent owner"
    }
}

# -----------------------------------------------------------------------------
# Check 6: RTEMS Jira Tickets Baseline (RTEMS-1..6 + cases)
# -----------------------------------------------------------------------------
Write-Host "`n6. Checking Jira Demo & Case Tickets Baseline..." -ForegroundColor White
if ($isOffline) {
    Write-CheckSkip "Jira Baseline" "No Atlassian credentials configured in environment or .env (offline mode)"
} else {
    $jiraResetOutput = uv run --no-sync --directory $backend python scripts/reset_demo.py --dry-run --jira 2>&1
    $needsReset = $jiraResetOutput | Where-Object { $_ -match "\[DRY RUN\] Would" }
    if ($LASTEXITCODE -ne 0) {
        Write-CheckFail "Jira Baseline" "reset_demo.py check failed ($jiraResetOutput)" "Check Atlassian credentials in backend/.env"
    } elseif ($needsReset) {
        $driftSummary = ($needsReset | Select-Object -First 2) -join "; "
        Write-CheckFail "Jira Baseline" "RTEMS tickets drifted from baseline ($driftSummary)" ".\scripts\reset-demo.ps1 -Jira"
    } else {
        Write-CheckPass "Jira Baseline" "RTEMS demo and case tickets are in baseline state (To Do, correct baseline labels, extra tickets purged)"
    }
}

# -----------------------------------------------------------------------------
# Check 7: Submodule Network Reachability
# -----------------------------------------------------------------------------
Write-Host "`n7. Checking Upstream Git Submodule Reachability..." -ForegroundColor White
if ($Offline) {
    Write-CheckSkip "Submodule Reachability" "-Offline specified (offline demo uses local checkouts without submodules)"
} else {
    $lwipOk = $false
    $rtemsOk = $false

    try {
        $lwipReq = Invoke-WebRequest -Uri "https://github.com/lwip-tcpip/lwip" -Method Head -TimeoutSec 5 -UseBasicParsing
        if ($lwipReq.StatusCode -lt 400) { $lwipOk = $true }
    } catch {
        $lwipOk = $false
    }

    try {
        $rtemsReq = Invoke-WebRequest -Uri "https://gitlab.rtems.org" -Method Head -TimeoutSec 5 -UseBasicParsing
        if ($rtemsReq.StatusCode -lt 400) { $rtemsOk = $true }
    } catch {
        $rtemsOk = $false
    }

    if ($lwipOk -and $rtemsOk) {
        Write-CheckPass "Submodule Reachability" "github.com/lwip-tcpip/lwip and gitlab.rtems.org are reachable"
    } elseif ($isOffline) {
        Write-CheckSkip "Submodule Reachability" "Upstream submodules unreachable; skipped in offline mode (local checkout operates self-contained without submodules)"
    } else {
        $unreachable = @()
        if (-not $lwipOk) { $unreachable += "github.com/lwip-tcpip/lwip" }
        if (-not $rtemsOk) { $unreachable += "gitlab.rtems.org" }
        Write-CheckFail "Submodule Reachability" "Cannot connect to $($unreachable -join ', ')" "Verify host Internet connection and DNS resolution"
    }
}

# -----------------------------------------------------------------------------
# Check 8: Leftover Ephemeral Sandbox Containers
# -----------------------------------------------------------------------------
Write-Host "`n8. Checking for Leftover Ephemeral Sandbox Containers..." -ForegroundColor White
$sbxContainers = docker ps -a --filter "name=^anvil-sbx-" --format "{{.Names}}" 2>$null
if (-not $sbxContainers) {
    Write-CheckPass "Ephemeral Containers" "No leftover anvil-sbx-* containers detected"
} else {
    $sbxList = ($sbxContainers -split "`n" | Where-Object { $_.Trim() }) -join ", "
    Write-CheckFail "Ephemeral Containers" "Leftover containers found: $sbxList" ".\scripts\reset-demo.ps1 -Containers (or: docker rm -f (docker ps -aq --filter 'name=^anvil-sbx-'))"
}

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
Write-Host "`n==========================================" -ForegroundColor Cyan
if ($failedCount -eq 0) {
    $skipNote = if ($skippedCount -gt 0) { "$passedCount passed, $skippedCount skipped (offline)" } else { "$passedCount passed, 0 skipped" }
    Write-Host "PREFLIGHT RESULT: ALL CHECKS PASSED ($skipNote)" -ForegroundColor Green
    Write-Host "Environment is fully verified for demo / recorded rehearsals." -ForegroundColor Green
    exit 0
} else {
    Write-Host "PREFLIGHT RESULT: $failedCount CHECK(S) FAILED ($passedCount passed, $failedCount failed, $skippedCount skipped)" -ForegroundColor Red
    Write-Host "Resolve the items flagged above before presenting." -ForegroundColor Yellow
    exit 1
}
