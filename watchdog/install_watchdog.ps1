# Installs the watchdog on the second GPU machine: copies watchdog.ps1 and watchdog.json to
# C:\ProgramData\gpu-ops-watchdog (readable only by Administrators and SYSTEM: the ntfy topic lives there too) and
# registers the gpu-ops-watchdog task from Watchdog.xml. Run as Administrator, from the folder with these files:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install_watchdog.ps1
# The ntfy topic is not in git: put it in C:\ProgramData\gpu-ops-watchdog\ntfy_topic.txt yourself.
$ErrorActionPreference = 'Stop'
$source = $PSScriptRoot
$dir = 'C:\ProgramData\gpu-ops-watchdog'
New-Item -ItemType Directory -Force $dir | Out-Null
icacls $dir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' | Out-Null
Copy-Item (Join-Path $source 'watchdog.ps1') $dir -Force
if (Test-Path (Join-Path $source 'watchdog.json')) { Copy-Item (Join-Path $source 'watchdog.json') $dir -Force }
# schtasks wants the file in UTF-16, as its declaration says; /change hangs over ssh, hence /create /xml /f
$xml = Join-Path $env:TEMP 'Watchdog-utf16.xml'
Get-Content -Encoding UTF8 (Join-Path $source 'Watchdog.xml') | Set-Content -Encoding Unicode $xml
schtasks /create /tn gpu-ops-watchdog /xml $xml /f
if ($LASTEXITCODE -ne 0) { throw "schtasks /create exited with $LASTEXITCODE" }
Remove-Item $xml
schtasks /run /tn gpu-ops-watchdog | Out-Null
schtasks /query /tn gpu-ops-watchdog /v /fo list | Select-String 'Logon Mode|Status|Run As User|Next Run'
