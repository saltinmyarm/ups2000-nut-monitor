# -*- coding: utf-8 -*-
"""Direct COM3 Modbus probe for Huawei UPS2000-A - safe-channel testing helper.

The NUT chain (driver/upsd/upsmon) must be STOPPED for COM3 to be free.

Usage:
  python ups-probe.py read              dump key registers
  python ups-probe.py status            human status of 11024
  python ups-probe.py write <addr> <v>  write one register (func 0x06) + readback
  python ups-probe.py write10 <addr> <v1> [v2...]  multi-write (func 0x10) + readback
  python ups-probe.py wait <addr> <v> <timeout>   poll until register == v
"""
import struct
import sys
import time

COM = "COM3"


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
    except Exception as e:
        print("pyserial not available: %r" % e)
        return None
    try:
        return serial.Serial(COM, 9600, timeout=2, bytesize=8,
                             parity=serial.PARITY_NONE, stopbits=1)
    except Exception as e:
        print("COM open FAILED: %r" % e)
        return None


def read_holding(slave, addr, count):
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
            print("short read @%d" % addr)
            return None
        if resp[0] != slave or resp[1] != 0x03:
            print("bad resp header @%d" % addr)
            return None
        if _crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
            print("CRC fail @%d" % addr)
            return None
        return [struct.unpack(">H", resp[3 + 2 * i:5 + 2 * i])[0] for i in range(count)]
    except Exception as e:
        print("read error: %r" % e)
        return None
    finally:
        try:
            ser.close()
        except Exception:
            pass


def write_holding(slave, addr, val):
    ser = _open_com()
    if ser is None:
        return False
    try:
        req = struct.pack(">BBHH", slave, 0x06, addr, val)
        req += struct.pack("<H", _crc16(req))
        ser.reset_input_buffer()
        ser.write(req)
        resp = ser.read(8)
        ok = len(resp) >= 8 and resp[0] == slave and resp[1] == 0x06 \
            and _crc16(resp[:-2]) == struct.unpack("<H", resp[-2:])[0]
        print("write 0x%04X=%d -> %s" % (addr, val, "ACK" if ok else "NACK"))
        return ok
    except Exception as e:
        print("write error: %r" % e)
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass


def write_holdings(slave, addr, vals):
    """Write multiple holding registers (func 0x10). Returns True/False."""
    ser = _open_com()
    if ser is None:
        return False
    try:
        n = len(vals)
        req = struct.pack(">BBHB", slave, 0x10, addr, n)
        req += b"".join(struct.pack(">H", v) for v in vals)
        req += struct.pack("<H", _crc16(req))
        ser.reset_input_buffer()
        ser.write(req)
        resp = ser.read(8)
        ok = len(resp) >= 8 and resp[0] == slave and resp[1] == 0x10 \
            and _crc16(resp[:-2]) == struct.unpack("<H", resp[-2:])[0]
        print("write10 0x%04X [%s] -> %s (resp %s)" % (
            addr, ",".join(str(v) for v in vals), "ACK" if ok else "NACK/TRUNC",
            resp.hex() if resp else "none"))
        return ok
    except Exception as e:
        print("write10 error: %r" % e)
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass


def do_write10(addr, vals):
    ok = write_holdings(1, addr, vals)
    time.sleep(0.5)
    rb = read_holding(1, addr, len(vals))
    print("readback 0x%05X = %s" % (addr, rb if rb else "NO RESPONSE"))
    sys.exit(0 if (ok and rb and rb == vals) else 1)


def status_name(v):
    return {0: "OFF", 1: "BYPASS", 2: "OL", 3: "OB", 5: "OL-ECO"}.get(v, "UNKNOWN(%d)" % v)


def do_read():
    for label, addr, n in [
        ("control 11024..11029", 11024, 6),
        ("telemetry 11000..11015", 11000, 16),
        ("battery 12000..12019", 12000, 20),
    ]:
        vals = read_holding(1, addr, n)
        if vals is None:
            print("%-22s: NO RESPONSE (COM busy/dead?)" % label)
        else:
            print("%-22s: %s" % (label, " ".join("%5d" % v for v in vals)))
    v = read_holding(1, 11024, 1)
    if v:
        print("status 11024=%d (%s)" % (v[0], status_name(v[0])))
    for a in (11044, 11046, 11049):
        v = read_holding(1, a, 1)
        if v is not None:
            print("reg 0x%05X=%d" % (a, v[0]))


def do_status():
    v = read_holding(1, 11024, 1)
    if v is None:
        print("NO RESPONSE")
        sys.exit(2)
    print(status_name(v[0]))


def do_write(addr, val):
    ok = write_holding(1, addr, val)
    time.sleep(0.5)
    rb = read_holding(1, addr, 1)
    print("readback 0x%05X=%s" % (addr, rb[0] if rb else "NO RESPONSE"))
    sys.exit(0 if (ok and rb and rb[0] == val) else 1)


def do_wait(addr, expect, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = read_holding(1, addr, 1)
        if v is not None:
            print("t=%.0fs reg 0x%05X=%d" % (time.time() - t0, addr, v[0]))
            if v[0] == expect:
                print("MATCHED %d" % expect)
                sys.exit(0)
        else:
            print("t=%.0fs reg 0x%05X NO RESPONSE (COM dead?)" % (time.time() - t0, addr))
        time.sleep(3)
    print("TIMEOUT waiting for %d" % expect)
    sys.exit(1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    if cmd == "read":
        do_read()
    elif cmd == "status":
        do_status()
    elif cmd == "write":
        do_write(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "write10":
        do_write10(int(sys.argv[2]), [int(v) for v in sys.argv[3:]])
    elif cmd == "wait":
        do_wait(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    else:
        print(__doc__)
        sys.exit(2)
