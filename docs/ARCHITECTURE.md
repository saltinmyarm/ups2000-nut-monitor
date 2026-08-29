# Architecture

```
Huawei UPS2000-A (USB) ── Exar XR21V1410 bridge (internal RS485)
        │  Modbus RTU 9600 8N1, slave 1
        ▼
   COM3 ── huawei-ups2000 driver (NUT)
        │
        ▼
   upsd (port 3493, 127.0.0.1)
        │
        ├── upsc          → status queries (CLI, also used by the outage report)
        ├── upscmd        → instcmds (shutdown.return etc., admin user)
        └── ups-monitor.py (pythonw, headless)
              ├─ polls ups.status every 5 s
              ├─ self-heal: restart upsd (3 fails) / whole stack (8 fails)
              ├─ COM3 Modbus fallback when the chain is down (register 11024)
              └─ on OB >= SHUTDOWN_DELAY:
                   upscmd shutdown.return  → flag file → outage report
                   → finalize_shutdown() (direct 0x06 write 11044=0 + 11049)
                   → shutdown /s /f /t 0
                   → finalize_refresh() (best-effort timer refresh)
```

## Components

| File | Role |
|---|---|
| `scripts/ups-monitor.py` | The monitor: OB detection, trigger, self-healing, direct COM3 power-off |
| `scripts/ups-probe.py` | Direct Modbus probing tool (reads/writes registers on COM3) for testing & register research |
| `scripts/nut-ensure.ps1` | Idempotent "start only what is missing" — used by boot task & 5-min watchdog; refuses to start while a shutdown flag is present |
| `scripts/start-nut.bat` | Boot/manual start; clears stale shutdown flag first (prevents flag deadlock) |
| `scripts/stop-nut.bat` | Stops the whole stack (disables protection — use deliberately) |
| `scripts/ups-outage-report.ps1` | Logon task: shows a popup with the last outage report, archives it, writes the Windows event log |
| `config/*.example` | NUT config templates (ups.conf / upsd.users / upsmon.conf) |

## Power-cut flow (Windows)

1. Mains drops → UPS on battery (status `OB`).
2. Monitor counts `SHUTDOWN_DELAY` (30 s) of continuous OB.
3. Trigger: `upscmd shutdown.return` (standard action; harmless on this
   firmware), write `killpower` flag, write outage report.
4. **`finalize_shutdown()` runs BEFORE Windows shutdown starts** — it stops the
   NUT driver (frees COM3), then writes `11044=0` + `11049=delay` directly
   (0x06). This must happen while the process is alive: after
   `shutdown /f` the OS force-kills the monitor almost immediately (a past
   regression lost the UPS power-off exactly this way).
5. Windows shuts down; the UPS powers off after the delay — always **after** the PC.
6. Mains returns → UPS stays OFF (11044=0) → user presses ON/MUTE to start →
   boots the PC.
7. At logon, `ups-outage-report.ps1` pops the outage report (archived to
   `logs/outage-<timestamp>.txt` and logged to the Windows event log).

## Failure tolerance

- upsd dead → monitor restarts it (~15 s); whole chain dead → full stack
  restart (~40 s).
- Chain down during OB → monitor reads status directly from COM3 so shutdown
  still triggers (blind-window countdown continues on unknown status).
- Watchdog task every 5 min restarts any missing component; boot task restores
  everything after reboot.
- The watchdog will not restart the driver while a shutdown flag exists (the
  monitor is busy freeing COM3 for the direct power-off write).

## Windows integration (scheduled tasks, for the README install notes)

| Task | Trigger | Action |
|---|---|---|
| `NUT-UPS-Monitor` | at startup | `start-nut.bat` |
| `NUT-Watchdog` | every 5 min | `nut-ensure.ps1` |
| `UPS-Outage-Report` | at logon | `ups-outage-report.ps1` (interactive) |

## Notes / gotchas learned the hard way

- `upscmd -h` is **help**, not host — the host goes inside the UPS name
  (`ups2000@localhost`); instcmds need the admin user (`instcmds = ALL`).
- Use `upscmd -w -t 10` to wait for the driver's real result.
- Every subprocess needs `CREATE_NO_WINDOW` (pythonw has no console; children
  would flash windows and steal focus).
- Windows PowerShell 5.1 reads `.ps1` as ANSI/GBK unless the file is UTF-8
  **with BOM** — Chinese strings need the BOM.
- `.bat` files must be GBK + CRLF on zh-CN Windows.
- Windows toast notifications with an unregistered AppUserModelID are silently
  dropped — use a plain WinForms popup for guaranteed visibility.

---

*AI-assisted notice: this document was written with the assistance of an AI
coding agent; the described behavior was validated on real hardware and
reviewed by a human before publishing.*
