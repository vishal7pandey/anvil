<#
.SYNOPSIS
    (Re)starts the Anvil Control API on :8000 from main with environment loaded and Jira intake verified.

.DESCRIPTION
    Implements ANVIL-46:
    1. Validates that the worktree is on latest origin/main (overridable with -Force).
    2. Loads .env (root and backend) without leaking secret values.
    3. Requires ANVIL_DEMO_TOKEN. Atlassian credentials are optional (ANVIL-97): with none set the API runs
       offline on local JSON tickets; a half-set Atlassian configuration fails loudly.
    4. Sets ANVIL_EXECUTOR=deepagent and ANVIL_DATABASE_URL.
    5. Terminates any existing process listening on the target port.
    6. Applies the database migrations (alembic upgrade head), then starts uvicorn with the
       SelectorEventLoop factory in the background, logging to file.
    7. Waits for GET /api/sessions to return 200 with ANVIL_DEMO_TOKEN.
    8. With Atlassian configured, performs one POST /api/intake/jira/poll check to verify the credentials work.

.PARAMETER DatabaseUrl
    Postgres database connection string. Defaults to ANVIL_DATABASE_URL from .env, else the compose
    database "anvil". Per-agent run databases are chosen explicitly (e.g. anvil_claude).
.PARAMETER KbDatabaseUrl
    Database holding the shared, read-only knowledge base (kb_documents/kb_chunks). Defaults to
    anvil. Kept apart from DatabaseUrl so a per-agent run database still sees the KB (ANVIL-51).
.PARAMETER Port
    Port to listen on. Defaults to 8000.
.PARAMETER HostAddress
    Host address to bind to. Defaults to 127.0.0.1.
.PARAMETER LogFile
    Path to log file. Defaults to api-8000.log in repository root.
.PARAMETER Force
    Bypasses the origin/main fast-forward equality check.
#>
param(
    # Empty = ANVIL_DATABASE_URL from .env, else the compose database "anvil" (ANVIL-97). Per-agent run
    # databases are chosen explicitly with -DatabaseUrl.
    [string]$DatabaseUrl = "",
    [string]$KbDatabaseUrl = "",
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1",
    [string]$LogFile = "api-8000.log",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$backendDir = Join-Path $repoRoot "backend"
$logPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $LogFile))
$errLogPath = [System.IO.Path]::ChangeExtension($logPath, ".err.log")

Write-Host "=== Anvil Control API Startup (ANVIL-46) ===" -ForegroundColor Cyan
Write-Host "Repository root: $repoRoot"
Write-Host "Backend dir:     $backendDir"
Write-Host "Target:          http://${HostAddress}:${Port}"
Write-Host "Log file:        $logPath"

# 1. Check Git origin/main alignment
Write-Host "`n1. Checking Git alignment with origin/main..." -ForegroundColor Yellow
if (-not (Test-Path (Join-Path $repoRoot ".git"))) {
    # A release archive (git archive / zip) has no .git. Under $ErrorActionPreference = "Stop" the native
    # `fatal: not a git repository` below used to be rethrown and the script died before starting anything
    # (found by the junior rehearsal, ANVIL-101). `.git` may be a file in a worktree: Test-Path covers both.
    Write-Host "  [INFO] Not a git checkout (a release archive): skipping the origin/main check." -ForegroundColor Cyan
} else {
    try {
        git -C $repoRoot fetch -q origin main 2>$null
        $headSha = (git -C $repoRoot rev-parse HEAD).Trim()
        $mainSha = (git -C $repoRoot rev-parse origin/main).Trim()

        if ($headSha -ne $mainSha) {
            if ($Force) {
                Write-Host "  [WARN] Worktree is at $headSha, but origin/main is at $mainSha. Continuing due to -Force." -ForegroundColor DarkYellow
            } else {
                Write-Error "Worktree HEAD ($headSha) differs from origin/main ($mainSha). Please sync with main or pass -Force."
                exit 1
            }
        } else {
            Write-Host "  [PASS] Worktree matches origin/main ($headSha)" -ForegroundColor Green
        }
    } catch [System.Management.Automation.ActionPreferenceStopException] {
        throw $_
    } catch {
        Write-Host "  [WARN] Could not verify origin/main git status ($_.Exception.Message). Continuing." -ForegroundColor DarkYellow
    }
}

# 2. Load .env files (root and backend) without logging secrets
Write-Host "`n2. Loading environment configurations..." -ForegroundColor Yellow
$loadedVars = 0
$envFiles = @(
    (Join-Path $repoRoot ".env"),
    (Join-Path $backendDir ".env")
)

foreach ($ef in $envFiles) {
    if (Test-Path $ef) {
        Write-Host "  Reading $ef..."
        Get-Content $ef | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and ($line -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")) {
                $varName = $matches[1]
                $varValue = $matches[2].Trim().Trim('"').Trim("'")
                [System.Environment]::SetEnvironmentVariable($varName, $varValue, [System.EnvironmentVariableTarget]::Process)
                $loadedVars++
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

# 3. Validate credentials. Atlassian is optional (ANVIL-97): with none set the API runs offline, on local
#    JSON tickets only (docs/SETUP.md). A half-set Atlassian configuration is still an error. The demo token is
#    always required: the API answers 503 "Demo authentication is not configured" without it.
Write-Host "`n3. Validating credentials..." -ForegroundColor Yellow
$atlassianCredentials = @($env:ATLASSIAN_EMAIL, $env:ATLASSIAN_API_TOKEN | Where-Object { $_ })
$offlineMode = ($atlassianCredentials.Count -eq 0)
$missing = @()
if (-not $env:ANVIL_DEMO_TOKEN) { $missing += "ANVIL_DEMO_TOKEN (any string; the UI's frontend/.env.local VITE_DEMO_TOKEN must be the same)" }
if (-not $offlineMode) {
    if (-not $env:ATLASSIAN_SITE) { $missing += "ATLASSIAN_SITE (or JIRA_URL)" }
    if (-not $env:ATLASSIAN_EMAIL) { $missing += "ATLASSIAN_EMAIL (or JIRA_USER_EMAIL)" }
    if (-not $env:ATLASSIAN_API_TOKEN) { $missing += "ATLASSIAN_API_TOKEN (or JIRA_API_TOKEN)" }
}

if ($missing.Count -gt 0) {
    Write-Error "Missing required settings in .env: $($missing -join ', '). Cannot start API."
    exit 1
}
if ($offlineMode) {
    Write-Host "  [INFO] No Atlassian credentials: offline mode (local JSON tickets only, ANVIL_TICKETS_DIR=$env:ANVIL_TICKETS_DIR)." -ForegroundColor Cyan
    if (-not $env:ANVIL_TICKETS_DIR) {
        Write-Host "  [WARN] ANVIL_TICKETS_DIR is not set: there will be no ticket source, so no intake." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "  [PASS] Atlassian site: $env:ATLASSIAN_SITE" -ForegroundColor Green
    Write-Host "  [PASS] Atlassian user: $env:ATLASSIAN_EMAIL" -ForegroundColor Green
    Write-Host "  [PASS] Atlassian token: [configured]" -ForegroundColor Green
}
Write-Host "  [PASS] Demo token:      [configured]" -ForegroundColor Green

# 4. Configure runtime environment. Database: the parameter, else .env, else the compose database "anvil".
if (-not $DatabaseUrl) {
    $DatabaseUrl = if ($env:ANVIL_DATABASE_URL) { $env:ANVIL_DATABASE_URL } else { "postgresql+psycopg://anvil:anvil@127.0.0.1:5433/anvil" }
}
if (-not $KbDatabaseUrl) {
    $KbDatabaseUrl = if ($env:ANVIL_KB_DATABASE_URL) { $env:ANVIL_KB_DATABASE_URL } else { "postgresql+psycopg://anvil:anvil@127.0.0.1:5433/anvil" }
}
Write-Host "  Database URL:    $DatabaseUrl"
Write-Host "  KB database URL: $KbDatabaseUrl"
$env:ANVIL_EXECUTOR = "deepagent"
$env:ANVIL_DATABASE_URL = $DatabaseUrl
$env:ANVIL_KB_DATABASE_URL = $KbDatabaseUrl
$env:PYTHONUNBUFFERED = "1"

# 5. Stop any existing listener on target port
Write-Host "`n4. Checking for existing listener on port $Port..." -ForegroundColor Yellow
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        Write-Host "  Stopping existing listener on PID $procId..."
        try {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    Start-Sleep -Milliseconds 1000
}
$remaining = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Error "Port $Port is still in use by PID $($remaining.OwningProcess). Cannot bind."
    exit 1
}
Write-Host "  [PASS] Port $Port is available." -ForegroundColor Green

# 6. Locate Python runtime and start uvicorn
Write-Host "`n5. Starting Control API process..." -ForegroundColor Yellow
$pythonExe = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "Virtual environment not found at $pythonExe. Rebuild venv with: uv sync --directory backend --python 'C:\Users\yedna\AppData\Local\Python\pythoncore-3.13-64\python.exe'"
    exit 1
}
Write-Host "  Using Python runtime: $pythonExe"

# Create or upgrade the Control API tables (users, sessions, runs, events, ...). They exist only as Alembic
# migrations: nothing else creates them, so a fresh database made the API die with
# 'relation "users" does not exist' (ANVIL-97). A no-op on a database that is already at head.
Write-Host "  Applying database migrations (alembic upgrade head)..."
Push-Location $backendDir
$prevPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"   # alembic logs to stderr; PowerShell 5.1 must not treat that as a failure
try {
    $migrationOutput = & $pythonExe -m alembic upgrade head 2>&1 | ForEach-Object { "$_" }
    $migrationExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevPref
    Pop-Location
}
$migrationOutput | ForEach-Object { Write-Host "    $_" }
if ($migrationExit -ne 0) {
    Write-Error "Database migration failed (exit $migrationExit). Is Postgres up (.\scripts\up.ps1) and does the database in $DatabaseUrl exist?"
    exit 1
}
Write-Host "  [PASS] Database schema is at head." -ForegroundColor Green

$demoToken = $env:ANVIL_DEMO_TOKEN
$runCmd = Join-Path $PSScriptRoot "run-api.cmd"

# Start detached background process via WMI to break away from any terminating job/session
$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = "cmd.exe /c `"`"$runCmd`" `"$HostAddress`" $Port `"$DatabaseUrl`" `"$KbDatabaseUrl`"`""
    CurrentDirectory = $backendDir
}

if ($res.ReturnValue -ne 0) {
    Write-Error "Failed to start API process via Win32_Process (return code $($res.ReturnValue))."
    exit 1
}

$spawnedPid = $res.ProcessId
Write-Host "  Spawned Control API launcher on PID $spawnedPid"

# 7. Poll for API readiness (up to 60 seconds)
Write-Host "`n6. Waiting for API readiness on http://${HostAddress}:${Port}..." -ForegroundColor Yellow
$authHeader = @{ "Authorization" = "Bearer $demoToken" }
$ready = $false
$httpErrors = 0
$maxAttempts = 120
$actualApiPid = $null

for ($i = 1; $i -le $maxAttempts; $i++) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        $actualApiPid = $conns[0].OwningProcess
        try {
            $res = Invoke-WebRequest -Uri "http://${HostAddress}:${Port}/api/sessions" -Headers $authHeader -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($res.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # Not connectable yet: retry. But an API that answers with an HTTP error again and again is up and
            # misconfigured, so say why instead of waiting for the timeout (ANVIL-97). The usual cause is a demo
            # token that only this shell has: the API is started in a separate process and reads .env itself.
            if ($_.Exception.Response) {
                $httpErrors++
                if ($httpErrors -ge 10) {
                    $code = [int]$_.Exception.Response.StatusCode
                    Write-Error "The API is running but answers HTTP $code to GET /api/sessions. If it is 503 or 401, put ANVIL_DEMO_TOKEN in the .env file (the API process reads .env, not this shell's environment) and rerun. Logs: $logPath, $errLogPath."
                    exit 1
                }
            }
        }
    }
    Start-Sleep -Milliseconds 500
}

if (-not $ready) {
    Write-Error "Timed out waiting for API on http://${HostAddress}:${Port}/api/sessions. Check $logPath and $errLogPath."
    exit 1
}
Write-Host "  [PASS] API is healthy and accepting requests (PID $actualApiPid)" -ForegroundColor Green

# 8. Test live Jira intake poll (only when Atlassian is configured; offline mode has no Jira to poll)
Write-Host "`n7. Validating Jira intake connectivity..." -ForegroundColor Yellow
if ($offlineMode) {
    Write-Host "  [SKIP] Offline mode: no Atlassian credentials, tickets come from ANVIL_TICKETS_DIR." -ForegroundColor Cyan
} else {
    try {
        $pollRes = Invoke-RestMethod -Method Post -Uri "http://${HostAddress}:${Port}/api/intake/jira/poll" -Headers $authHeader -TimeoutSec 10 -ErrorAction Stop
        $acceptedCount = [int]$pollRes.accepted  # an int (IntakeResponse.accepted), not a list: .Count was always 1
        Write-Host "  [PASS] Jira intake poll succeeded (accepted candidates: $acceptedCount)" -ForegroundColor Green
    } catch {
        Write-Error "Live Jira intake poll failed: $($_.Exception.Message). Check Jira credentials in .env and API logs at $logPath."
        exit 1
    }
}

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host " API READY: PID $actualApiPid listening on http://${HostAddress}:${Port}" -ForegroundColor Green
Write-Host " Log file:  $logPath" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
