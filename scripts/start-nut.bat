@echo off
rem ============================================================
rem  start-nut.bat - start / repair the NUT UPS stack (idempotent).
rem  Delegates to nut-ensure.ps1 which starts ONLY missing parts.
rem  Runs at boot (scheduled task NUT-UPS-Monitor) and on demand.
rem  Edit the paths below for your machine.
rem ============================================================
set MW=C:\nut\NUT-for-Windows-x86_64-SNAPSHOT-2.8.5.4499-master\mingw64
set FLAG=C:\nut\killpower
set LOG=C:\nut\logs\start-stop.log
echo [%date% %time%] === start-nut === >> "%LOG%"
rem Clear any stale powerdown flag: this is a FRESH start (boot/manual), so
rem any leftover killpower flag from a previous UPS-triggered shutdown must
rem NOT block the stack from starting (the flag is only removed by the
rem monitor at startup, and the watchdog refuses to start anything while
rem the flag exists). Without this, a shutdown-test flag would deadlock the
rem whole stack on next boot.
if exist %FLAG% del %FLAG%
powershell -NoProfile -ExecutionPolicy Bypass -File C:\nut\nut-ensure.ps1
echo [%date% %time%] NUT ensured. Test with: "%MW%\bin\upsc.exe" ups2000@localhost >> "%LOG%"
