<#
.SYNOPSIS
    (Re)starts the Anvil Control API on :8000 from main with environment loaded and Jira intake verified.

.DESCRIPTION
    Implements ANVIL-46:
    1. Validates that the worktree is on latest origin/main (overridable with -Force).
    2. Loads .env (root and backend) without leaking secret values.
    3. Fails loudly upfront if required Atlassian/Jira credentials are missing.
    4. Sets ANVIL_EXECUTOR=deepagent and ANVIL_DATABASE_URL.
    5. Terminates any existing process listening on the target port.
    6. Starts uvicorn with the SelectorEventLoop factory in the background, logging to file.
    7. Waits for GET /api/sessions to return 200 with ANVIL_DEMO_TOKEN.
    8. Performs one POST /api/intake/jira/poll check to verify Jira credentials work live.

.PARAMETER DatabaseUrl
    Postgres database connection string. Defaults to anvil_claude (or anvil).
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
    [string]$DatabaseUrl = "postgresql+psycopg://anvil:anvil@localhost:5433/anvil_claude",
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
Write-Host "Database URL:    $DatabaseUrl"
Write-Host "Log file:        $logPath"

# 1. Check Git origin/main alignment
Write-Host "`n1. Checking Git alignment with origin/main..." -ForegroundColor Yellow
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

# 3. Fail loudly upfront if Atlassian credentials missing
Write-Host "`n3. Validating required Atlassian / Jira credentials..." -ForegroundColor Yellow
$missing = @()
if (-not $env:ATLASSIAN_SITE) { $missing += "ATLASSIAN_SITE (or JIRA_URL)" }
if (-not $env:ATLASSIAN_EMAIL) { $missing += "ATLASSIAN_EMAIL (or JIRA_USER_EMAIL)" }
if (-not $env:ATLASSIAN_API_TOKEN) { $missing += "ATLASSIAN_API_TOKEN (or JIRA_API_TOKEN)" }

if ($missing.Count -gt 0) {
    Write-Error "Missing required Atlassian credentials in .env: $($missing -join ', '). Jira intake will fail."
    exit 1
}
Write-Host "  [PASS] Atlassian site: $env:ATLASSIAN_SITE" -ForegroundColor Green
Write-Host "  [PASS] Atlassian user: $env:ATLASSIAN_EMAIL" -ForegroundColor Green
Write-Host "  [PASS] Atlassian token: [configured]" -ForegroundColor Green

# 4. Configure runtime environment
$env:ANVIL_EXECUTOR = "deepagent"
$env:ANVIL_DATABASE_URL = $DatabaseUrl
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
    # Fallback to signed CPython 3.13 if available
    $signedPy = "C:\Users\yedna\AppData\Local\Python\pythoncore-3.13-64\python.exe"
    if (Test-Path $signedPy) {
        $pythonExe = $signedPy
    } else {
        Write-Error "Python executable not found in $pythonExe or $signedPy."
        exit 1
    }
}
Write-Host "  Using Python runtime: $pythonExe"

$demoToken = if ($env:ANVIL_DEMO_TOKEN) { $env:ANVIL_DEMO_TOKEN } else { "sk-anvil-local" }
$launcherCmd = Join-Path ([System.IO.Path]::GetTempPath()) "anvil-api-launcher.cmd"

$launcherContent = @"
@echo off
set "ANVIL_EXECUTOR=deepagent"
set "ANVIL_DATABASE_URL=$DatabaseUrl"
set "ATLASSIAN_SITE=$env:ATLASSIAN_SITE"
set "ATLASSIAN_EMAIL=$env:ATLASSIAN_EMAIL"
set "ATLASSIAN_API_TOKEN=$env:ATLASSIAN_API_TOKEN"
set "JIRA_URL=$env:ATLASSIAN_SITE"
set "JIRA_USER_EMAIL=$env:ATLASSIAN_EMAIL"
set "JIRA_API_TOKEN=$env:ATLASSIAN_API_TOKEN"
set "ANVIL_DEMO_TOKEN=$demoToken"
set "PYTHONUNBUFFERED=1"
cd /d "$backendDir"
"$pythonExe" -u -c "import uvicorn; uvicorn.run('anvil.api.app:app', host='$HostAddress', port=$Port, loop='anvil.api.serve:api_loop_factory')" > "$logPath" 2> "$errLogPath"
"@

[System.IO.File]::WriteAllText($launcherCmd, $launcherContent)

# Start detached background process via WMI to break away from any terminating job/session
$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = "cmd.exe /c `"$launcherCmd`""
    CurrentDirectory = $backendDir
}

if ($res.ReturnValue -ne 0) {
    Write-Error "Failed to start API process via Win32_Process (return code $($res.ReturnValue))."
    exit 1
}

$spawnedPid = $res.ProcessId
Write-Host "  Spawned Control API launcher on PID $spawnedPid"

# 7. Poll for API readiness
Write-Host "`n6. Waiting for API readiness on http://${HostAddress}:${Port}..." -ForegroundColor Yellow
$authHeader = @{ "Authorization" = "Bearer $demoToken" }
$ready = $false
$maxAttempts = 30
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
            # Retry until ready
        }
    }
    Start-Sleep -Milliseconds 500
}

if (-not $ready) {
    Write-Error "Timed out waiting for API on http://${HostAddress}:${Port}/api/sessions. Check $logPath and $errLogPath."
    exit 1
}
Write-Host "  [PASS] API is healthy and accepting requests (PID $actualApiPid)" -ForegroundColor Green

# 8. Test live Jira intake poll
Write-Host "`n7. Validating Jira intake connectivity..." -ForegroundColor Yellow
try {
    $pollRes = Invoke-RestMethod -Method Post -Uri "http://${HostAddress}:${Port}/api/intake/jira/poll" -Headers $authHeader -TimeoutSec 10 -ErrorAction Stop
    $acceptedCount = if ($null -ne $pollRes.accepted) { $pollRes.accepted.Count } else { 0 }
    Write-Host "  [PASS] Jira intake poll succeeded (accepted candidates: $acceptedCount)" -ForegroundColor Green
} catch {
    Write-Error "Live Jira intake poll failed: $($_.Exception.Message). Check Jira credentials in .env and API logs at $logPath."
    exit 1
}

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host " API READY: PID $actualApiPid listening on http://${HostAddress}:${Port}" -ForegroundColor Green
Write-Host " Log file:  $logPath" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
