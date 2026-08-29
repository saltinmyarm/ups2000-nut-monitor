# ups2000-nut-monitor

**Full PC-integration for the Huawei UPS2000-A (1kVA–3kVA) on Windows: live
status monitoring, automatic PC shutdown on power loss, UPS power-off *after*
the PC is down, and a post-reboot outage report popup.**

> The UPS vendor told us this unit "cannot communicate with a PC". It can — the
> USB port is a standard Modbus RTU slave (Exar XR21V1410 bridge, 9600 8N1).
> This project is the proof, including the firmware quirks that made the
> "official" path fail. See [docs/REGISTER-FINDINGS.md](docs/REGISTER-FINDINGS.md).

## What it does

```
mains drops → UPS on battery → PC shuts down after 30 s (graceful)
→ UPS powers off AFTER the PC (60 s delay) → mains returns → UPS stays OFF
→ manual ON/MUTE button → UPS on → boot PC → outage report popup
```

Also: self-healing (restarts dead NUT components automatically), a 5-minute
watchdog, direct COM3 fallback when the NUT chain is down, and permanent outage
archives + Windows event-log records.

## Requirements

- Huawei UPS2000-A (1kVA–3kVA) with USB cable to the PC
- Windows 10/11 x64
- [NUT for Windows](https://networkupstools.org/download.html) 2.8.x (x64) with
  the `huawei-ups2000` driver
- Python 3.x with `pyserial` (only needed for the direct COM3 fallback/probe)
- MaxLinear XR21x USB-serial driver (the Windows built-in `usbser` driver does
  not work with this port)

## Install (Windows)

1. Install NUT, copy `config/*.example` into its `etc\` dir as `ups.conf`,
   `upsd.users`, `upsmon.conf` and edit:
   - `ups.conf`: `driver = huawei-ups2000`, `port = <your COM port>`
   - `upsd.users`: strong passwords (keep them secret; the monitor uses the
     `admin` account, `instcmds = ALL`)
   - `upsmon.conf`: `MONITOR ups2000@localhost 1 upsmon <pass> master` and a
     `POWERDOWNFLAG` path (forward slashes)
2. Edit the CONFIG block at the top of `scripts/ups-monitor.py`
   (`MW`, `COM`, `FLAG`, `OUTAGE_REPORT`, `LOG`, `NUT_ADMIN_PASS`) and the
   paths inside `scripts/nut-ensure.ps1`, `scripts/start-nut.bat`,
   `scripts/stop-nut.bat`, `scripts/ups-outage-report.ps1`.
3. Create the scheduled tasks (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):
   - `NUT-UPS-Monitor` (at startup) → `start-nut.bat`
   - `NUT-Watchdog` (every 5 min) → `nut-ensure.ps1`
   - `UPS-Outage-Report` (at logon, interactive) → `ups-outage-report.ps1`
4. Verify: `upsc ups2000@localhost` shows `OL CHRG`; `ups-probe.py status`
   prints `OL`.

## Why direct register writes? (short version)

The NUT `huawei-ups2000` driver sends all register writes as Modbus function
**0x10**, which firmware **V2R1C1SPC50 ignores** (no response, no effect) —
so `upscmd shutdown.return` reports success but the UPS never powers off.
Direct **0x06** single-register writes work: `11044=0` (stay off on mains
return) + `11049=<delay>` (power-off timer) is the reliable recipe this
monitor uses. Details and the full evidence table:
[docs/REGISTER-FINDINGS.md](docs/REGISTER-FINDINGS.md).

This was reported upstream to [networkupstools/nut](https://github.com/networkupstools/nut) as [issue #3593](https://github.com/networkupstools/nut/issues/3593) with a suggested driver fix.

## Project layout

```
config/    NUT config templates (no real passwords — keep yours private)
scripts/   monitor, probe, watchdog, startup/shutdown bats, outage report
docs/      architecture + register findings
```

## Caveats

- Verified on **one unit / one firmware build** (V2R1C1SPC50). Register
  behavior may differ on other builds — always verify with `ups-probe.py`.
- Only the UPS <-> PC control link is covered here; BIOS "restore on AC loss"
  stays off by design (the PC is started manually, and the UPS does not
  auto-start — that's the intended policy).

## License

MIT — see [LICENSE](LICENSE).

## AI-assisted notice / AI 创作声明

This project — the monitor code, scripts, documentation, debugging, and the
register-level reverse engineering — was developed with the assistance of an
AI coding agent. Every technical claim was validated on real hardware
(power-cut drills, direct Modbus probing) and reviewed by a human before
publishing.

本项目（监控代码、脚本、文档、调试与寄存器逆向分析）由 AI 辅助开发完成；
所有技术结论均经过真机实测验证（断电演练、直接 Modbus 探测），发布前经人工审核。
