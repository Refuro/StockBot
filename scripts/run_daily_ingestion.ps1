# OPS job 1 — daily ingestion + drift-study refresh (shot 2).
# Fills the Alpha Vantage cache (~25 names/day) toward the 2b gate call, then
# mechanically re-runs the pre-registered drift study ONLY if ingestion succeeded.
# No parameters of the study are changed here — this is a refresh, not a tweak.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("ingestion-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== ingestion run started $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log

& $Python -m stockbot.data.ingest_universe --start 2022-06-01 --end 2026-06-09 *>> $Log
$IngestExit = $LASTEXITCODE
"ingest_universe exit code: $IngestExit" | Out-File -Append -Encoding utf8 $Log

if ($IngestExit -eq 0) {
    & $Python -m stockbot.research.drift_study *>> $Log
    "drift_study exit code: $LASTEXITCODE" | Out-File -Append -Encoding utf8 $Log
} else {
    "SKIPPED drift_study (ingestion failed)" | Out-File -Append -Encoding utf8 $Log
}

"=== ingestion run finished $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
exit $IngestExit
