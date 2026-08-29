# ups-outage-report.ps1 - shows a visible popup with the last power-outage report.
# Run at user logon (scheduled task UPS-Outage-Report). If the monitor wrote
# logs\last-outage.txt during a UPS-triggered shutdown, this shows a popup,
# archives the report, and clears the pending file.
# NOTE: Windows toast (Windows.UI.Notifications) with an unregistered
# AppUserModelID is silently dropped by Win10/11, so we use a guaranteed-visible
# auto-close popup instead.
# Edit the CONFIG paths below for your machine.
$ErrorActionPreference = 'SilentlyContinue'

# --- CONFIG ---
$MW     = 'C:\nut\NUT-for-Windows-x86_64-SNAPSHOT-2.8.5.4499-master\mingw64'
$report = 'C:\nut\logs\last-outage.txt'
$archiveDir = 'C:\nut\logs'
# --------------

if (-not (Test-Path $report)) { exit 0 }

$content = Get-Content $report -Raw -Encoding UTF8

# current UPS state (query through NUT)
$upsc = Join-Path $MW 'bin\upsc.exe'
$status = (& $upsc ups2000@localhost ups.status 2>$null | Out-String).Trim()
$charge = (& $upsc ups2000@localhost battery.charge 2>$null | Out-String).Trim()
if ($status) {
    $current = "当前: $status  电量 $charge%"
} else {
    $current = '当前: UPS 状态无法读取（监控恢复后自动更新）'
}

# archive the report (keep a permanent copy per outage)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item $report (Join-Path $archiveDir "outage-$stamp.txt") -Force
Remove-Item $report -Force

# give the logon desktop a few seconds to settle
Start-Sleep -Seconds 5

# --- visible popup (topmost, bottom-right, auto-closes) ---
$shown = $false
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'UPS 停电报告'
    $form.Width = 520
    $form.Height = 340
    $form.FormBorderStyle = 'FixedToolWindow'
    $form.ShowInTaskbar = $false
    $form.TopMost = $true
    $form.StartPosition = 'Manual'
    $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $form.Left = $wa.Right - $form.Width - 20
    $form.Top = $wa.Bottom - $form.Height - 20
    $form.BackColor = [System.Drawing.Color]::FromArgb(32, 32, 32)

    $box = New-Object System.Windows.Forms.RichTextBox
    $box.Location = New-Object System.Drawing.Point(12, 12)
    $box.Size = New-Object System.Drawing.Size($form.ClientSize.Width - 24, $form.ClientSize.Height - 64)
    $box.ReadOnly = $true
    $box.BorderStyle = 'None'
    $box.BackColor = [System.Drawing.Color]::FromArgb(43, 43, 43)
    $box.ForeColor = [System.Drawing.Color]::FromArgb(232, 232, 232)
    $box.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 10)
    $box.Text = "UPS 停电报告`r`n" + ('=' * 24) + "`r`n" + $content.Trim() + "`r`n`r`n" + $current
    $form.Controls.Add($box)

    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = '知道了'
    $btn.Location = New-Object System.Drawing.Point($form.ClientSize.Width - 112, $form.ClientSize.Height - 44)
    $btn.Size = New-Object System.Drawing.Size(100, 32)
    $btn.BackColor = [System.Drawing.Color]::FromArgb(60, 60, 60)
    $btn.ForeColor = [System.Drawing.Color]::White
    $btn.FlatStyle = 'Flat'
    $btn.Add_Click({ $form.Close() })
    $form.Controls.Add($btn)

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 60000
    $timer.Add_Tick({ $form.Close() })
    $timer.Start()

    $form.ShowDialog() | Out-Null
    $shown = $true
} catch {
    $shown = $false
}

# last-resort fallback: classic message box (only works in an interactive session)
if (-not $shown) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show("$content`n$current", 'UPS 停电报告', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    } catch {}
}

# record to Windows Application event log for the record
try {
    $source = 'UPS监控'
    if (-not [System.Diagnostics.EventLog]::SourceExists($source)) {
        New-EventLog -LogName Application -Source $source
    }
    Write-EventLog -LogName Application -Source $source -EventId 1001 -EntryType Information -Message ("停电报告`n$content`n$current")
} catch {}

exit 0
