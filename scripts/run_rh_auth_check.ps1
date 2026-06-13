# OPS job 5 - weekly Robinhood OAuth token health check (shot 5 rider, from the
# shot-4 review). The pilot must never discover dead auth at order time: this
# runs `rh_auth status` (no network) every Monday and warns LOUDLY in the log
# when the token store is missing or the token is expired.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("rh-auth-check-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== rh_auth check started $(Get-Date -Format o) ===" | Out-File -Append -Encoding utf8 $Log

$Output = & $Python -m stockbot.tools.rh_auth status 2>&1 | Out-String
$StatusExit = $LASTEXITCODE
$Output | Out-File -Append -Encoding utf8 $Log
"rh_auth status exit code: $StatusExit" | Out-File -Append -Encoding utf8 $Log

$Problem = $null
if ($StatusExit -ne 0) {
    $Problem = "token store MISSING (rh_auth status exit $StatusExit)"
} elseif ($Output -match '"expired":\s*true') {
    $Problem = "token EXPIRED"
}

if ($Problem) {
    $Warning = @(
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
        "!!! ROBINHOOD AUTH PROBLEM: $Problem",
        "!!! Run:  python -m stockbot.tools.rh_auth login",
        "!!! The live pilot CANNOT place orders until this is fixed.",
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    ) -join "`r`n"
    $Warning | Out-File -Append -Encoding utf8 $Log
    Write-Warning $Problem
    "=== rh_auth check finished $(Get-Date -Format o) (PROBLEM) ===" | Out-File -Append -Encoding utf8 $Log
    exit 1
}

"rh_auth token healthy." | Out-File -Append -Encoding utf8 $Log
"=== rh_auth check finished $(Get-Date -Format o) (ok) ===" | Out-File -Append -Encoding utf8 $Log
exit 0
