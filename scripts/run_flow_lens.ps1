# OPS job 3 — daily Flow Lens accumulation run (F1).
# Live run: samples ONLY providers whose keys exist (OpenAI + Anthropic today);
# Grok/Gemini are skipped cleanly and listed in the output. Spends real API
# money (small); the per-run estimated spend is printed into the log.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("flow-lens-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== flow lens run started $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
& $Python -m stockbot.tools.flow_lens.run run *>> $Log
$ExitCode = $LASTEXITCODE
"flow lens exit code: $ExitCode" | Out-File -Append -Encoding utf8 $Log
"=== flow lens run finished $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
exit $ExitCode
