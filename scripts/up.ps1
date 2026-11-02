# Start the local Anvil stack (Postgres + model gateway).
# The OpenAI key is read from Windows Credential Manager into this process's environment only;
# it is never written to a file. Store it once with:
#   uv run --with keyring python -m keyring set forge openai
param(
    [string]$CredentialTarget = "forge"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not $env:OPENAI_API_KEY) {
    $py = "import keyring, sys; c = keyring.get_credential('$CredentialTarget', None); sys.stdout.write(c.password if c else '')"
    $key = uv run --quiet --no-project --with keyring python -c $py
    if (-not $key) {
        throw "No OpenAI key in Credential Manager target '$CredentialTarget'. Store it with: uv run --with keyring python -m keyring set $CredentialTarget openai"
    }
    $env:OPENAI_API_KEY = $key
}

docker compose -f (Join-Path $root "deploy/compose.yaml") up -d --wait
if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }
Write-Host "Anvil stack up: Postgres on localhost:5433, gateway on http://localhost:4000"
