# nut-ensure.ps1 - idempotent NUT stack + monitor ensure (no user attention).
# Checks each component and starts ONLY what is missing. Used by:
#   - start-nut.bat (boot / manual)
#   - scheduled task NUT-Watchdog (every 5 minutes)
# ALL processes are launched with hidden windows (no console flashes).
# Edit the CONFIG paths below for your machine.
$ErrorActionPreference = 'SilentlyContinue'

# --- CONFIG ---
$MW  = 'C:\nut\NUT-for-Windows-x86_64-SNAPSHOT-2.8.5.4499-master\mingw64'
$PY  = 'C:\Python313\pythonw.exe'
$MON = 'C:\nut\ups-monitor.py'
$LOG = 'C:\nut\logs\watchdog.log'
$FLAG = 'C:\nut\killpower'
# --------------

$ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

# HARDENING: if a UPS-triggered shutdown is in progress (killpower flag set),
# do NOT (re)start anything - the monitor is busy freeing COM3 to write the
# UPS power-off command directly; restarting the driver here would steal COM3
# back and the UPS would never power off. The flag is cleared at next boot
# (start-nut.bat) or by the monitor at startup.
if (Test-Path $FLAG) {
    "$ts [shutdown in progress] killpower flag present - skipping ensure" | Out-File $LOG -Append
    exit 0
}

# NOTE: the monitor check MUST match only python/pythonw processes whose
# command line contains ups-monitor.py - never the powershell running this
# script (its own command line contains the pattern).

if (-not (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'huawei-ups2000.exe' })) {
    "$ts [driver] missing -> upsdrvctl start" | Out-File $LOG -Append
    # upsdrvctl blocks while managing the driver; hidden window, async launch
    Start-Process -FilePath "$MW\sbin\upsdrvctl.exe" -ArgumentList 'start' -WorkingDirectory $MW -WindowStyle Hidden
    Start-Sleep -Seconds 6
}
if (-not (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'upsd.exe' })) {
    "$ts [upsd] missing -> starting" | Out-File $LOG -Append
    Start-Process -FilePath "$MW\sbin\upsd.exe" -WorkingDirectory $MW -WindowStyle Hidden
    Start-Sleep -Seconds 3
}
if (-not (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'upsmon.exe' })) {
    "$ts [upsmon] missing -> starting" | Out-File $LOG -Append
    Start-Process -FilePath "$MW\sbin\upsmon.exe" -WorkingDirectory $MW -WindowStyle Hidden
}
if (-not (Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -match 'ups-monitor\.py'
    })) {
    "$ts [monitor] missing -> starting" | Out-File $LOG -Append
    Start-Process -FilePath $PY -ArgumentList $MON -WorkingDirectory (Split-Path $MON) -WindowStyle Hidden
}
