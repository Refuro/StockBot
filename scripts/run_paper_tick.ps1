# OPS job 4 - daily forward-paper tick (2d): one drive_strategies fan-out on
# the live feed, racing the LLM-gated proportionality lane against its
# mechanical control twin. LLM spend hard-capped at $5/day in code.
# Shot 4 (5b): config shadow.enabled adds the proportionality_shadow lane to
# the same process — shadow_order decision rows, broker never reachable.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("paper-tick-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== paper tick started $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
& $Python -m stockbot.harness.paper_run *>> $Log
$ExitCode = $LASTEXITCODE
"paper tick exit code: $ExitCode" | Out-File -Append -Encoding utf8 $Log
"=== paper tick finished $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log
exit $ExitCode
