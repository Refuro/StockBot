# OPS — race dashboard (4b-2): keeps http://127.0.0.1:8765 up from logon.
# Read-only against the paper DB; restarts automatically if the process dies.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "dashboard.log"

while ($true) {
    "=== dashboard started $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
    & $Python -m stockbot.dashboard.app --port 8765 *>> $Log
    "dashboard exited (code $LASTEXITCODE) $(Get-Date -Format o) — restarting in 30s" |
        Out-File -Append -Encoding utf8 $Log
    Start-Sleep -Seconds 30
}
