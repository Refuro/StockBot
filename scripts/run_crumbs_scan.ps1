# OPS job 2 — daily Crumbs scan (odd-lot tender offers on EDGAR).
# Needs only SEC_USER_AGENT from .env; notifier only, never trades.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("crumbs-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== crumbs scan started $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
# Structured alert feed for the dashboard (4b-2 panel 6) — written by the
# scanner itself, read-only for the dashboard.
$env:CRUMBS_OUT_JSON = Join-Path $RepoRoot "data\crumbs_alerts.json"
& $Python -m stockbot.tools.crumbs.scanner *>> $Log
$ExitCode = $LASTEXITCODE
"crumbs scanner exit code: $ExitCode" | Out-File -Append -Encoding utf8 $Log
"=== crumbs scan finished $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
exit $ExitCode
