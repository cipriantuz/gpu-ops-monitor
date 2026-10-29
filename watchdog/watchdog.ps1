# Watchdog for the second GPU machine: the scheduled task gpu-ops-watchdog (S4U, every 5 min, runs with nobody
# logged on). Install: install_watchdog.ps1.
#  1. Writes the state of this machine to C:\ProgramData\gpu-ops-watchdog\state.json. The main monitor reads it over
#     SSH (check type host_state) and sees from `ts` whether the watchdog still runs.
#  2. Checks the main monitor (its /api/status). If it does not answer or has not run a round for 10 min, it pages
#     the owner directly: the main machine is down, and with it the monitor and the LLM agent tier. It repeats after
#     1, 2, 4... hours and says when the monitor is back.
# Settings: watchdog.json next to this script (see watchdog.example.json); the ntfy topic in ntfy_topic.txt.
# Test the path to the phone:  powershell -NoProfile -ExecutionPolicy Bypass -File watchdog.ps1 -Test
# All texts are ASCII: Windows PowerShell 5.1 reads a BOM-less UTF-8 script as cp1252.
param([switch]$Test, [string]$Monitor = '')
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$dir = 'C:\ProgramData\gpu-ops-watchdog'
$cfg = $null
try { $cfg = Get-Content (Join-Path $PSScriptRoot 'watchdog.json') -Raw | ConvertFrom-Json } catch {}
if (-not $Monitor) { $Monitor = if ($cfg.monitor_url) { $cfg.monitor_url } else { 'http://monitor.example.lan/api/status' } }
$mainHost = [string]$cfg.main_host
$ntfyUrl = if ($cfg.ntfy_url) { $cfg.ntfy_url.TrimEnd('/') } else { 'https://ntfy.example.com' }
$limitS = 600
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

function Write-Log($text) {
    $log = Join-Path $dir 'watchdog.log'
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.1" -Force }
    Add-Content -Path $log -Encoding UTF8 -Value ('{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $text)
}

function Write-Json($name, $object) {
    $path = Join-Path $dir $name
    [IO.File]::WriteAllText("$path.tmp", ($object | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
    Move-Item "$path.tmp" $path -Force
}

function Send-Alert($title, $message, $priority, $tag) {
    try {
        $topic = (Get-Content (Join-Path $dir 'ntfy_topic.txt') -Raw).Trim()
        $body = @{ topic = $topic; title = $title; message = $message; priority = $priority; tags = @($tag) } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri "$ntfyUrl/" -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 20 | Out-Null
        Write-Log "sent: $title"
        return $true
    } catch {
        Write-Log "notification failed ($title): $($_.Exception.Message)"
        return $false
    }
}

if ($Test) {
    if (Send-Alert 'TEST watchdog' 'TEST - not a real problem: checking the path from the watchdog to the phone.' 2 'test_tube') { 'sent' } else { 'not sent (see watchdog.log)' }
    exit
}

# 1. the state of this machine
$battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
$power = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue | Select-Object -First 1
$ev = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'nvlddmkm'; StartTime = (Get-Date).AddDays(-1) } -MaxEvents 50 -ErrorAction SilentlyContinue)
# driver resets (nvlddmkm 153) since the current driver was installed: the date its package entered the DriverStore
$drvPath = [string](Get-CimInstance Win32_SystemDriver -Filter "Name='nvlddmkm'" -ErrorAction SilentlyContinue).PathName
if ($drvPath.StartsWith('\SystemRoot\')) { $drvPath = Join-Path $env:SystemRoot $drvPath.Substring(12) }
$drvInstalled = if ($drvPath -and (Test-Path $drvPath)) { (Get-Item (Split-Path $drvPath)).CreationTime } else { $null }
$drvVersion = [string](Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object Name -like '*NVIDIA*' | Select-Object -First 1).DriverVersion
$drvDigits = $drvVersion -replace '\.', ''
$state = [ordered]@{
    ts = $now
    on_ac = if ($power) { [bool]$power.PowerOnline } else { $true }
    battery = if ($battery) { [int]$battery.EstimatedChargeRemaining } else { $null }
    driver_errors_24h = $ev.Count
    driver_last = if ($ev.Count) { '{0:dd.MM HH:mm} (id {1})' -f $ev[0].TimeCreated, $ev[0].Id } else { '' }
    driver_version = if ($drvDigits.Length -ge 5) { '{0}.{1}' -f $drvDigits.Substring($drvDigits.Length - 5, 3), $drvDigits.Substring($drvDigits.Length - 2) } else { $drvVersion }
    driver_installed = if ($drvInstalled) { ([DateTimeOffset]$drvInstalled).ToUnixTimeSeconds() } else { $null }
    driver_153 = if ($drvInstalled) { @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'nvlddmkm'; Id = 153; StartTime = $drvInstalled } -ErrorAction SilentlyContinue).Count } else { $null }
    wu_auoptions = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -ErrorAction SilentlyContinue).AUOptions
    reboot_pending = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
                     (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
    watchdog_problem = $null
}

# 2. the main monitor
$problem = $null
try {
    $r = Invoke-RestMethod -Uri $Monitor -TimeoutSec 20
    $age = $now - [double]$r.last_round
    if ($age -gt $limitS) { $problem = "main monitor has not run a check for $([int]($age / 60)) min (stuck)" }
} catch {
    $err = $_.Exception.Message
    $up = $null
    if ($mainHost) { $up = Test-Connection -ComputerName $mainHost -Count 1 -Quiet -ErrorAction SilentlyContinue }
    $problem = if ($up) { 'main host is up (answers ping), but the monitor does not answer: it restarted with nobody logged on, or the monitor stopped' }
               elseif ($up -eq $false) { 'main host is offline: powered off, restarting, no power or no network' }
               else { "main monitor does not answer ($err)" }
}
$state.watchdog_problem = $problem
Write-Json 'state.json' $state

$p = $null
try { $p = Get-Content (Join-Path $dir 'watchdog_state.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
$since = if ($p) { [double]$p.since } else { 0 }
$notified = if ($p) { [double]$p.notified } else { 0 }
$alerts = if ($p) { [int]$p.alerts } else { 0 }
if ($problem) {
    if ($since -eq 0) { $since = $now; Write-Log "problem: $problem" }
    $pause = if ($alerts -eq 0) { 0 } else { 3600 * [math]::Pow(2, $alerts - 1) }
    if ($now - $since -ge $limitS -and $now - $notified -ge $pause) {
        $text = "$problem (for $([int](($now - $since) / 60)) min).`nWhile the main machine or its monitor is down, nothing else will page you: not the monitor, not the agent. " +
                'This machine keeps working. If you stopped it on purpose, ignore this: I will tell you when it is back.'
        if (Send-Alert 'Main monitor down' $text 5 'rotating_light') { $notified = $now; $alerts++ }
    }
} elseif ($alerts -gt 0) {
    if (Send-Alert 'Main monitor: working again' "The main monitor answers again (it was down ~$([int](($now - $since) / 60)) min)." 2 'white_check_mark') {
        $since = 0; $notified = 0; $alerts = 0
    }
} else {
    if ($since -ne 0) { Write-Log 'back before an alert' }
    $since = 0
}
Write-Json 'watchdog_state.json' @{ since = $since; notified = $notified; alerts = $alerts }
