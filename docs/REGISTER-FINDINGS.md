# Register findings — Huawei UPS2000-A (1kVA-3kVA), firmware V2R1C1SPC50

Reverse-engineered register map validated by direct Modbus RTU probing over the
USB port (Exar XR21V1410 bridge, internal RS485, 9600 8N1, slave 1) on a real
unit. Values may differ on other firmware builds — verify with `ups-probe.py`
before relying on them.

## Key registers

| Address | Name | Semantics | Writable? |
|---|---|---|---|
| 11024 | ups.status | 0=OFF, 1=BYPASS, 2=OL, 3=OB, 5=OL-ECO | — |
| 11043 | alarm | bit2 = CAL, bit6 = LB | — |
| 11044 | start.auto (来电自启) | 0 = stay off on mains return, 1 = auto-restart | **yes (0x06), persists across power-off** |
| 11046 | beeper | 0 = enabled, 1 = disabled | yes (0x06) |
| 11047 / 11048 | shutdown delay pair (NUT driver's `shutdown.return` path) | ignored by this firmware (readback stays 0) | **no effect** |
| 11049 | shutdown delay | 0.1-minute units; value = seconds × 10 / 60 (e.g. 5 → ~30 s, 10 → ~60 s, 20 → ~120 s) | **yes (0x06) — the real power-off timer** |
| 11000–11028 | telemetry | input/output voltage & frequency, load, temperature | — |
| 12000–12033 | battery data | voltage, charge, runtime, capacity | — |

## The two findings that matter

### 1. The firmware ignores Modbus function 0x10 writes (value-changing)

The NUT driver sends **every** register write through libmodbus
`modbus_write_registers()` = function **0x10** — including "single-register"
writes (`ups2000_write_register()` is `ups2000_write_registers(addr, 1, …)`).

Direct tests on firmware V2R1C1SPC50:

| Write method | Register(s) | Value change | Response | Effect |
|---|---|---|---|---|
| 0x06 | 11044 | 1 → 0 | ACK | persisted; UPS stays OFF on mains return |
| 0x06 | 11044 | 0 → 1 | ACK | UPS auto-restarts on mains return |
| 0x06 | 11049 | 0 → 5 | ACK | UPS fully powered off after ~30 s (11024: 3→0) |
| 0x10 (1 reg) | 11046 | 0 → 1 | **no response** | no effect (readback stays 0) |
| 0x10 (1 reg) | 11044 | 0 → 1 | **no response** | no effect (readback stays 0) |
| 0x10 (2 regs) | 11047 + 11048 | 0,0 | **no response** | no effect |
| 0x10 (1 reg) | 11046 | 0 → 0 (no-op) | ACK echo | n/a |

Consequence: `upscmd shutdown.return` (which writes 11047/11048 via 0x10 after
setting `start.auto=yes`) reports success but the UPS never powers off — the
write path is dead. Only direct **0x06** writes take effect.

### 2. The reliable shutdown recipe (0x06 only)

To power the UPS off after the PC is down, write directly over the wire:

1. `11044 = 0` — start.auto = no → the UPS stays OFF when mains returns
   (matches the "UPS mission ends once the PC is safely down" policy;
   manual start is the front-panel ON/MUTE button, hold ~5 s).
2. `11049 = delay` (0.1-minute units) — the UPS powers off after the delay,
   guaranteeing it cuts power **after** the PC has shut down.

Verified end-to-end many times: battery mode → write → UPS off after the delay
→ mains return → **stays off** → manual ON/MUTE → back to OL.

## Extra observations

- `ups.start.auto` (11044) persists across a full power-off + mains cycle.
- While the UPS is OFF (standby) with AC present, the USB/Modbus control board
  stays powered: COM3 remains readable and the battery keeps charging
  (observed 26.6 → 27.8 V recovery).
- `shutdown.stayoff` in the driver uses the same registers we write (11044=0 +
  11049) but sends them via 0x10, so it fails on this firmware for the same
  reason — another reason the driver should switch single-register writes to
  function 0x06.

## Upstream

These findings were reported to the NUT project — see the linked issue in
README.md.
