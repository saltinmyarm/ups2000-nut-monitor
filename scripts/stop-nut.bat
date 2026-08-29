@echo off
rem ============================================================
rem  stop-nut.bat - stop the NUT UPS stack + monitor.
rem  NOTE: this disables UPS-monitoring and auto-shutdown protection!
rem  Edit the paths below for your machine.
rem ============================================================
set LOG=C:\nut\logs\start-stop.log
echo [%date% %time%] === stop-nut === >> "%LOG%"
taskkill /IM upsmon.exe /F >nul 2>&1
taskkill /IM upsd.exe /F >nul 2>&1
taskkill /IM huawei-ups2000.exe /F >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -match 'ups-monitor\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo [%date% %time%] NUT stopped >> "%LOG%"
