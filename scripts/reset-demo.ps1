# Reset the Anvil demo environment:
# 1. Reset Jira tickets (RTEMS-1..6) to initial status and clean baseline labels; delete RTEMS-7+.
# 2. Close open agent PRs and delete agent branches (anvil/*) on mirror repos.
# 3. Purge agent-written Confluence pages in space RTKB and clear orphaned pgvector chunks.
# 4. Clear LangGraph /memories/ lessons in database and disk.
# 5. Stop and delete ephemeral sandbox containers (anvil-sbx-*, anvil-sandbox-*).
#
# Usage:
#   .\scripts\reset-demo.ps1 [-DryRun] [-KeepTc4Run1]
#   .\scripts\reset-demo.ps1 -Jira
#   .\scripts\reset-demo.ps1 -Confluence -KeepTc4Run1
#   .\scripts\reset-demo.ps1 -Memories

param(
    [switch]$Jira,
    [switch]$Mirrors,
    [switch]$Confluence,
    [switch]$Memories,
    [switch]$Containers,
    [switch]$KeepTc4Run1,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

$cmdArgs = @("run", "python", "scripts/reset_demo.py")

if ($Jira) { $cmdArgs += "--jira" }
if ($Mirrors) { $cmdArgs += "--mirrors" }
if ($Confluence) { $cmdArgs += "--confluence" }
if ($Memories) { $cmdArgs += "--memories" }
if ($Containers) { $cmdArgs += "--containers" }
if ($KeepTc4Run1) { $cmdArgs += "--keep-tc4-run1" }
if ($DryRun) { $cmdArgs += "--dry-run" }

Write-Host "Executing Anvil demo reset from $backend..." -ForegroundColor Cyan
Set-Location $backend
uv @cmdArgs
if ($LASTEXITCODE -ne 0) {
    throw "reset_demo.py failed with exit code $LASTEXITCODE"
}
Write-Host "Demo environment successfully reset." -ForegroundColor Green
