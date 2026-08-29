# -*- coding: utf-8 -*-
"""NUT power-failure monitor for Huawei UPS2000-A - self-healing, headless.

Runs in the background (started by start-nut.bat / scheduled tasks / watchdog)
and needs NO user attention. On a power cut it waits SHUTDOWN_DELAY seconds of
continuous on-battery, then:
  1. tells the UPS to power off after offdelay via "shutdown.return"
     (upscmd -> upsd -> driver -> Modbus);
  2. writes the POWERDOWNFLAG file (so a shutdown script / Group-Policy hook can
     tell this shutdown was UPS-triggered);
  3. arms the UPS power-off DIRECTLY over COM3 (see finalize_shutdown) and
     shuts Windows down (/s /f /t 0).

Self-healing layers (so the PC ALWAYS shuts down on power loss even if some
part of the NUT chain died or was closed):
  Layer 1: if "upsc" fails 3x in a row, restart upsd automatically.
  Layer 2: if it keeps failing (8x), restart the whole stack
           (driver + upsd + upsmon).
  Layer 3: while the chain is down, read the UPS status DIRECTLY from COM3
           (Modbus RTU, register 11024: 3=OB) so OB is still detected.
  Layer 4: if upscmd cannot reach the UPS at trigger time, write the
           shutdown registers straight to COM3 (11044=0 + delay 11049).
  An external watchdog task (NUT-Watchdog, every 5 min) plus a boot task
  (NUT-UPS-Monitor) restart the monitor itself if it ever dies.

IMPORTANT firmware findings (verified on UPS2000-A firmware V2R1C1SPC50):
  - The NUT driver sends ALL register writes as Modbus function 0x10, which
    this firmware ignores (no response, register unchanged). Only function 0x06
    single-register writes take effect. See docs/REGISTER-FINDINGS.md.
  - Therefore the only reliable way to power the UPS off is a direct 0x06
    write: register 11044 = 0 (start.auto = no, stays off on mains return) +
    register 11049 = delay (0.1-minute units). finalize_shutdown() does this.
"""
import os
import struct
import subprocess
import time

# ---------------------------------------------------------------------------
# CONFIG - edit for your machine
# ---------------------------------------------------------------------------
MW = r"C:\nut\NUT-for-Windows-x86_64-SNAPSHOT-2.8.5.4499-master\mingw64"  # NUT Windows install dir
UPSC = MW + r"\bin\upsc.exe"
UPSCMD = MW + r"\bin\upscmd.exe"
UPSD = MW + r"\sbin\upsd.exe"
UPSMON_EXE = MW + r"\sbin\upsmon.exe"
UPSDRVCTL = MW + r"\sbin\upsdrvctl.exe"
COM = "COM3"                     # UPS USB serial port
FLAG = r"C:\nut\killpower"       # POWERDOWNFLAG (same as upsmon.conf)
OUTAGE_REPORT = r"C:\nut\logs\last-outage.txt"
SHUTDOWN = r"C:\Windows\System32\shutdown.exe"
LOG = r"C:\nut\logs\ups-monitor.log"
POLL = 5                          # seconds between checks
SHUTDOWN_DELAY = 30               # seconds of continuous on-battery before shutdown
UPS_POWEROFF_DELAY = 60           # seconds the UPS waits (after PC is off) before cutting power
HEAL_UPSD_AFTER = 3               # consecutive upsc failures -> restart upsd
HEAL_STACK_AFTER = 8              # still failing -> restart whole stack
FALLBACK_AFTER = 3                # start direct COM3 reads after this many failures
UPS_NAME = "ups2000@localhost"
NUT_ADMIN_PASS = "CHANGE_ME_ADMIN_PASSWORD"   # must match upsd.users [admin]

# Windows: never show a console window for child processes (this monitor runs
# as pythonw with NO console, so without this flag every child gets a NEW
# visible console window - upsc polls every 5s would flash windows constantly
# and steal focus).
CREATE_NO_WINDOW = 0x08000000


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass  # pythonw (no console)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_upsc(args):
    try:
        # -W 5: fast failure detection when upsd is down (localhost anyway)
        return subprocess.run([UPSC, "-W", "5"] + args, capture_output=True, text=True, timeout=8,
                               creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log("upsc error (%s): %r" % (" ".join(args), e))
        return None


_KNOWN_STATUS = ("OL", "OB", "LB", "HB", "RB", "CHRG", "DISCHRG",
                 "BYPASS", "CAL", "OFF", "FSD", "BOOST", "TRIM")


def _valid_status(s):
    """A real NUT status contains at least one known token. Strings like
    'Data stale' are NOT valid - treat them as failures so the auto-heal
    kicks in instead of pretending everything is fine."""
    return any(t in s for t in _KNOWN_STATUS)


def get_status():
    """Return the NUT ups.status string, or '' on failure/invalid."""
    r = run_upsc([UPS_NAME, "ups.status"])
    if r is None or r.returncode != 0:
        if r is not None:
            log("upsc rc=%d stderr=%s" % (r.returncode, (r.stderr or "").strip()[:150]))
        return ""
    s = r.stdout.strip()
    return s if _valid_status(s) else ""


# ---------------------------------------------------------------------------
# Direct COM3 Modbus RTU (fallback when the NUT chain is down)
# ---------------------------------------------------------------------------
def _crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _open_com():
    try:
        import serial
    except Exception:
        return None
    try:
        return serial.Serial(COM, 9600, timeout=2, bytesize=8,
                             parity=serial.PARITY_NONE, stopbits=1)
    except Exception:
        return None  # COM busy (driver alive) or missing


def modbus_read_holding(slave, addr, count):
    """Read holding registers directly from COM3 (function 0x03). list|None."""
    ser = _open_com()
    if ser is None:
        return None
    try:
        req = struct.pack(">BBHH", slave, 0x03, addr, count)
        req += struct.pack("<H", _crc16(req))
        ser.reset_input_buffer()
        ser.write(req)
        resp = ser.read(5 + 2 * count)
        if len(resp) < 5 + 2 * count:
            return None
        if resp[0] != slave or resp[1] != 0x03:
            return None
        if _crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
            return None
        return [struct.unpack(">H", resp[3 + 2 * i:5 + 2 * i])[0] for i in range(count)]
    except Exception:
        return None
    finally:
        try:
            ser.close()
        except Exception:
            pass


def modbus_write_holding(slave, addr, val):
    """Write ONE holding register (function 0x06). True/False.

    NOTE: this firmware IGNORES function 0x10 writes (even single-register
    ones) - the NUT driver uses 0x10 everywhere, which is why driver-initiated
    shutdown never works on UPS2000-A. 0x06 works reliably.
    """
    ser = _open_com()
    if ser is None:
        return False
    try:
        req = struct.pack(">BBHH", slave, 0x06, addr, val)
        req += struct.pack("<H", _crc16(req))
        ser.reset_input_buffer()
        ser.write(req)
        resp = ser.read(8)
        if len(resp) < 8 or resp[0] != slave or resp[1] != 0x06:
            return False
        return _crc16(resp[:-2]) == struct.unpack("<H", resp[-2:])[0]
    except Exception:
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass


def direct_status():
    """Read UPS status straight from COM3 (driver must be DOWN, else None).
    Register 11024: 0=OFF 1=BYPASS 2=OL 3=OB 5=OL ECO."""
    vals = modbus_read_holding(1, 11024, 1)
    if vals is None:
        return None
    v = vals[0]
    if v == 3:
        return "OB"
    if v in (2, 5):
        return "OL"
    if v == 0:
        return "OFF"
    return "UNKNOWN(%d)" % v


def direct_shutdown_return():
    """Write the shutdown command straight to COM3 (driver must be DOWN).
    Register 11044 = 0 (start.auto = no -> stays off on mains return) +
    register 11049 = delay (0.1-minute units). Returns True on full success."""
    v = (UPS_POWEROFF_DELAY * 10) // 60   # 0.1-minute units
    if not modbus_write_holding(1, 11044, 0):
        log("direct shutdown: write ups.start.auto FAILED")
        return False
    if not modbus_write_holding(1, 11049, v):
        log("direct shutdown: write 11049 delay FAILED")
        return False
    log("direct shutdown: 11044=0 11049=%d (UPS powers off in %ds)" % (v, UPS_POWEROFF_DELAY))
    return True


# ---------------------------------------------------------------------------
# Auto-heal
# ---------------------------------------------------------------------------
def run_hidden(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log("run_hidden %r error: %r" % (cmd, e))
        return None


def heal_upsd():
    log("AUTO-HEAL: restarting upsd (%d consecutive upsc failures)" % heal_upsd.fail_count)
    run_hidden(["taskkill", "/IM", "upsd.exe", "/F"])
    time.sleep(1)
    try:
        subprocess.Popen([UPSD], cwd=MW, creationflags=CREATE_NO_WINDOW)
        log("AUTO-HEAL: upsd relaunched")
    except Exception as e:
        log("AUTO-HEAL: launch upsd FAILED: %r" % e)
    time.sleep(5)


def heal_stack():
    log("AUTO-HEAL: full stack restart (driver + upsd + upsmon)")
    for img in ("upsmon.exe", "upsd.exe", "huawei-ups2000.exe"):
        run_hidden(["taskkill", "/IM", img, "/F"])
    time.sleep(2)
    try:
        # upsdrvctl blocks while managing the driver; CREATE_NO_WINDOW keeps it hidden
        subprocess.Popen([UPSDRVCTL, "start"], cwd=MW, creationflags=CREATE_NO_WINDOW)
        time.sleep(7)
        subprocess.Popen([UPSD], cwd=MW, creationflags=CREATE_NO_WINDOW)
        time.sleep(3)
        subprocess.Popen([UPSMON_EXE], cwd=MW, creationflags=CREATE_NO_WINDOW)
        log("AUTO-HEAL: stack relaunched")
    except Exception as e:
        log("AUTO-HEAL: stack launch FAILED: %r" % e)
    time.sleep(5)


def snapshot():
    r = run_upsc([UPS_NAME])
    if r is not None and r.returncode == 0:
        log("--- status snapshot ---")
        for line in (r.stdout or "").splitlines():
            log("    " + line.strip())
        log("--- end snapshot ---")
    else:
        log("snapshot unavailable (upsc rc=%s)" % (None if r is None else r.returncode))


# ---------------------------------------------------------------------------
def main():
    log("=== ups-monitor started (shutdown after %ds on battery, poll %ds) ===" % (SHUTDOWN_DELAY, POLL))
    log("flag path: %s" % FLAG)
    log("log file: %s" % LOG)
    log("heal: upsd after %d fails, full stack after %d, COM3 fallback after %d" %
        (HEAL_UPSD_AFTER, HEAL_STACK_AFTER, FALLBACK_AFTER))
    # clean up any stale powerdown flag left from a previous shutdown, so a
    # later MANUAL shutdown is never mistaken for an UPS-triggered one
    try:
        if os.path.exists(FLAG):
            os.remove(FLAG)
            log("stale flag removed")
    except Exception:
        pass
    snapshot()
    ob_since = None
    last_status = None
    poll_no = 0
    consec_fail = 0
    while True:
        poll_no += 1
        st = get_status()
        if st == "":
            consec_fail += 1
            if poll_no % 3 == 0:
                log("upsc FAILING (%d consecutive) - status unknown" % consec_fail)
        else:
            consec_fail = 0

        # fallback: direct COM3 read while the chain is down
        direct = None
        if consec_fail >= FALLBACK_AFTER:
            direct = direct_status()
            if direct is not None and poll_no % 3 == 0:
                log("direct COM3 status: %s" % direct)

        # auto-heal
        heal_upsd.fail_count = consec_fail
        if consec_fail == HEAL_UPSD_AFTER:
            heal_upsd()
        elif consec_fail >= HEAL_STACK_AFTER and (consec_fail == HEAL_STACK_AFTER or consec_fail % 10 == 0):
            heal_stack()

        status_str = st if st else (direct or "")
        if status_str != last_status:
            log("status change: '%s' -> '%s'" % (last_status, status_str))
            last_status = status_str

        on_ob = ("OB" in status_str)
        now = time.time()
        if on_ob:
            if ob_since is None:
                ob_since = now
                log("POWER FAILURE detected (status=%r) -> will shut down in %ds" % (status_str, SHUTDOWN_DELAY))
            else:
                elapsed = now - ob_since
                if poll_no % 3 == 0:
                    log("still on battery: %.0fs/%ds (status=%r)" % (elapsed, SHUTDOWN_DELAY, status_str))
                if elapsed >= SHUTDOWN_DELAY:
                    trigger_shutdown(status_str)
                    break
        elif status_str == "" and ob_since is not None:
            # blind window: keep the OB countdown running (don't reset on unknown)
            elapsed = now - ob_since
            if poll_no % 3 == 0:
                log("status unknown, continuing OB countdown: %.0fs/%ds" % (elapsed, SHUTDOWN_DELAY))
            if elapsed >= SHUTDOWN_DELAY:
                log("On battery >= %ds (counted through blind window) - chain is down, "
                    "writing flag and shutting down Windows" % SHUTDOWN_DELAY)
                try:
                    with open(FLAG, "w") as f:
                        f.write("upsmon-shutdown-file\n")
                    log("flag written: %s" % FLAG)
                except Exception as e:
                    log("FLAG WRITE FAILED: %r" % e)
                try:
                    with open(OUTAGE_REPORT, "w", encoding="utf-8") as f:
                        f.write("UPS 停电报告\n")
                        f.write("============\n")
                        f.write("停电时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                        f.write("状态: 通讯链路故障中（盲窗路径触发）\n")
                        f.write("电池供电: 持续 %d 秒后触发\n" % SHUTDOWN_DELAY)
                        f.write("电脑关机命令: 已发送\n")
                except Exception as e:
                    log("outage report write FAILED: %r" % e)
                finalize_shutdown()   # arm the UPS power-off BEFORE the OS kills this process
                r = subprocess.run([SHUTDOWN, "/s", "/f", "/t", "0"], capture_output=True, text=True, timeout=30,
                                   creationflags=CREATE_NO_WINDOW)
                log("shutdown.exe rc=%d (blind-window trigger)" % r.returncode)
                log("Windows shutdown initiated (blind-window path).")
                finalize_refresh()    # best-effort timer refresh while Windows shuts down
                log("Monitor exiting.")
                break
        else:
            if ob_since is not None:
                ob_since = None
            if poll_no % 12 == 0:
                log("heartbeat: status=%r" % status_str)
        time.sleep(POLL)


def trigger_shutdown(status_str):
    log("On battery for >= %ds -> primary: send shutdown.return, write flag, shut down Windows" % SHUTDOWN_DELAY)
    sent = False
    upscmd_rc = -1
    try:
        # upscmd has NO "-h host" option ("-h" = help, phantom rc=0). Host is in
        # the UPS name. instcmds need the "admin" user (instcmds=ALL).
        # -w -t 10: wait for the driver's REAL result.
        r = subprocess.run(
            [UPSCMD, "-W", "5", "-w", "-t", "10", "-u", "admin", "-p", NUT_ADMIN_PASS,
             UPS_NAME, "shutdown.return"],
            capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or "").strip()[:100]
        err = (r.stderr or "").strip()[:100]
        log("upscmd shutdown.return rc=%d out=%s err=%s" % (r.returncode, out, err))
        sent = (r.returncode == 0)
        upscmd_rc = r.returncode
    except Exception as e:
        log("upscmd shutdown.return ERROR: %r" % e)
    if not sent:
        log("upscmd failed - trying DIRECT Modbus shutdown.return on COM3")
        if direct_shutdown_return():
            log("direct Modbus shutdown.return OK")
        else:
            log("direct Modbus shutdown.return FAILED (COM3 busy or driver alive)")
    try:
        with open(FLAG, "w") as f:
            f.write("upsmon-shutdown-file\n")
        log("flag written: %s" % FLAG)
    except Exception as e:
        log("FLAG WRITE FAILED: %r" % e)
    # write the outage report for the post-reboot Windows notification
    try:
        with open(OUTAGE_REPORT, "w", encoding="utf-8") as f:
            f.write("UPS 停电报告\n")
            f.write("============\n")
            f.write("停电时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("触发状态: %s\n" % status_str)
            f.write("电池供电: 持续 %d 秒后触发\n" % SHUTDOWN_DELAY)
            f.write("upscmd shutdown.return: rc=%d\n" % upscmd_rc)
            f.write("电脑关机命令: 已发送\n")
        log("outage report written: %s" % OUTAGE_REPORT)
    except Exception as e:
        log("outage report write FAILED: %r" % e)
    finalize_shutdown()   # arm the UPS power-off BEFORE the OS kills this process
    r = subprocess.run([SHUTDOWN, "/s", "/f", "/t", "0"], capture_output=True, text=True, timeout=30,
                       creationflags=CREATE_NO_WINDOW)
    log("shutdown.exe rc=%d out=%s err=%s" % (r.returncode, (r.stdout or "").strip()[:120], (r.stderr or "").strip()[:120]))
    log("Windows shutdown initiated.")
    finalize_refresh()    # best-effort timer refresh while Windows shuts down
    log("Monitor exiting.")


def finalize_shutdown():
    """Free COM3 and arm the UPS power-off. MUST run BEFORE the Windows
    shutdown is initiated - see note below.

    USER POLICY: once the PC is safely down, the UPS mission is complete:
      - 11044 = 0  (start.auto = no)  -> the UPS stays OFF when mains returns
                                         (manual ON/MUTE button to start);
      - 11049 = delay (0.1 min)       -> the UPS powers off after the delay.

    KEY FINDING: the NUT driver's shutdown.return writes delay registers
    11047/11048 (via Modbus 0x10), which THIS UPS2000A firmware IGNORES - so
    upscmd shutdown.return alone can never power this UPS off. The register
    that IS writable and effective is 11049 (0x06 write). So we stop the NUT
    driver+upsd to free COM3 and write DIRECTLY to the UPS.

    ORDERING MATTERS: this must run BEFORE shutdown.exe. If it runs after,
    the OS force-kills this process during shutdown (/f) before the write
    happens and the UPS never receives the power-off command.
    """
    run_hidden(["taskkill", "/IM", "huawei-ups2000.exe", "/F"])   # free COM3 for direct writes
    run_hidden(["taskkill", "/IM", "upsd.exe", "/F"])
    time.sleep(2)
    v = (UPS_POWEROFF_DELAY * 10) // 60   # 0.1-minute units (driver convention)
    ok1 = ok2 = False
    for i in range(1, 4):                 # retry while we are still alive
        ok1 = modbus_write_holding(1, 11044, 0)          # start.auto = no (stay off on mains)
        ok2 = modbus_write_holding(1, 11049, v)          # shutdown delay
        log("finalize #%d: 11044=0(%s) 11049=%d(%s)" % (i, ok1, v, ok2))
        try:
            with open(OUTAGE_REPORT, "a", encoding="utf-8") as f:
                f.write("UPS 断电命令 (11044=0 + 11049=%d): %s\n" %
                        (v, "成功" if (ok1 and ok2) else "失败(重试中)"))
        except Exception:
            pass
        if ok1 and ok2:
            break
        time.sleep(3)


def finalize_refresh():
    """Best-effort: keep re-arming the UPS delay while Windows shuts down.
    The OS may kill this process at any moment - that is fine, because
    finalize_shutdown() already armed the power-off while we were alive."""
    v = (UPS_POWEROFF_DELAY * 10) // 60
    t0 = time.time()
    while time.time() - t0 < 60:
        if not os.path.exists(FLAG):
            log("finalize: flag gone - no more writes")
            break
        ok1 = modbus_write_holding(1, 11044, 0)
        ok2 = modbus_write_holding(1, 11049, v)
        log("refresh: 11044=0(%s) 11049=%d(%s)" % (ok1, v, ok2))
        time.sleep(15)
    log("finalize: refresh window ended")


if __name__ == "__main__":
    main()
