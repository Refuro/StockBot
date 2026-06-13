# Registers the Stockbot scheduled jobs with Windows Task Scheduler (shot 2/3 OPS,
# weekly auth check added in shot 5).
# Idempotent: re-running replaces existing definitions.
#
# Pause a task:   Disable-ScheduledTask -TaskName "Stockbot Daily Ingestion"
# Resume a task:  Enable-ScheduledTask  -TaskName "Stockbot Daily Ingestion"
# Remove a task:  Unregister-ScheduledTask -TaskName "Stockbot Daily Ingestion" -Confirm:$false

$RepoRoot = Split-Path -Parent $PSScriptRoot

$Jobs = @(
    @{ Name = "Stockbot Daily Ingestion"; Script = "run_daily_ingestion.ps1"; Time = "08:00" },
    @{ Name = "Stockbot Crumbs Scan";     Script = "run_crumbs_scan.ps1";     Time = "08:30" },
    @{ Name = "Stockbot Flow Lens";       Script = "run_flow_lens.ps1";       Time = "09:00" },
    @{ Name = "Stockbot Paper Tick";      Script = "run_paper_tick.ps1";      Time = "17:00" }
)

foreach ($Job in $Jobs) {
    $ScriptPath = Join-Path $RepoRoot ("scripts\" + $Job.Script)
    $Action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`"" `
        -WorkingDirectory $RepoRoot
    $Trigger = New-ScheduledTaskTrigger -Daily -At $Job.Time
    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName $Job.Name `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Stockbot daily job ($($Job.Script)); logs append to $RepoRoot\logs" `
        -Force | Out-Null

    Write-Host "Registered: $($Job.Name) daily at $($Job.Time) -> $ScriptPath"
}

# Weekly Robinhood auth health check (shot 5 rider): Mondays 08:45.
$AuthScript = Join-Path $RepoRoot "scripts\run_rh_auth_check.ps1"
$AuthAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$AuthScript`"" `
    -WorkingDirectory $RepoRoot
$AuthTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "08:45"
$AuthSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "Stockbot RH Auth Check" `
    -Action $AuthAction `
    -Trigger $AuthTrigger `
    -Settings $AuthSettings `
    -Description "Weekly Robinhood OAuth token health check (run_rh_auth_check.ps1); warns in $RepoRoot\logs when auth is dead" `
    -Force | Out-Null

Write-Host "Registered: Stockbot RH Auth Check weekly Monday 08:45 -> $AuthScript"

# Dashboard (4b-2): runs from logon, no time limit — keeps 127.0.0.1:8765 up.
$DashScript = Join-Path $RepoRoot "scripts\run_dashboard.ps1"
$DashAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$DashScript`"" `
    -WorkingDirectory $RepoRoot
# Scoped to the current user so registration works without elevation.
$DashTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$DashSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "Stockbot Dashboard" `
    -Action $DashAction `
    -Trigger $DashTrigger `
    -Settings $DashSettings `
    -Description "Stockbot race dashboard at http://127.0.0.1:8765 (read-only; run_dashboard.ps1)" `
    -Force | Out-Null

Write-Host "Registered: Stockbot Dashboard at logon -> $DashScript"

Get-ScheduledTask -TaskName "Stockbot*" | Format-Table TaskName, State
