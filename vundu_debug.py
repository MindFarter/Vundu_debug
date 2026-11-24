#!/usr/bin/env python3
"""Vundu_debug - a standalone JTAG debugger for ESP32-family boards.
MIT licence, Copyright (c) 2026 MindFarter (https://github.com/MindFarter). See LICENSE.

One local server (Python standard library only) plus one web page (index.html).
It contains no debugger of its own: it drives OpenOCD and GDB (Espressif builds)
and esptool, and shows what they report.  It is one half of a pair: Vundu_lab and
Vundu_debug are separate local servers that take turns with the board, linked by
a button in each (see README, section 5).
"""
import atexit, collections, glob, hashlib, http.client, http.server, json, os, platform, queue, re, secrets
import shlex, shutil, socket, subprocess, sys, tarfile, threading, time, traceback
import urllib.parse, urllib.request, venv, webbrowser, zipfile
from pathlib import Path

VERSION = "0.1.3"
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ENG = DATA / "engines" / "debug"
TOOLS = ENG / "tools"
VENV = ENG / "venv"
UPLOADS = DATA / "uploads"
HOST = "127.0.0.1"
PORT_FIRST, PORT_LAST = 8790, 8809          # Vundu_lab uses 8765-8784, so no clash
TOKEN = secrets.token_urlsafe(24)
PORT = 0
IS_WIN = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")

# ---------------------------------------------------------------- chip knowledge
# usb = has the built-in USB Serial/JTAG block.  pins = external JTAG GPIOs (only
# where I am sure of them; others are "see the chip's datasheet").
CHIPS = {
    "esp32":   dict(name="ESP32",    arch="xtensa", usb=False, ocd="esp32",   pins=dict(TCK=13, TMS=14, TDI=12, TDO=15), wp=2,
                    boards=["ESP32-2432S028R Cheap Yellow Display", "WT32-SC01", "M5Stack Core2", "Guition JC2432W328", "LCDWIKI E32R28T / E32R40T", "Waveshare ESP32-Touch-LCD-3.5"]),
    "esp32s2": dict(name="ESP32-S2", arch="xtensa", usb=False, ocd="esp32s2", pins=dict(TCK=39, TMS=42, TDI=41, TDO=40), wp=2, boards=["(no LCD Wizard presets)"]),
    "esp32s3": dict(name="ESP32-S3", arch="xtensa", usb=True,  ocd="esp32s3", pins=dict(TCK=39, TMS=42, TDI=41, TDO=40), usbpins=dict(**{"D-": 19, "D+": 20}), wp=2,
                    boards=["Sunton 8048S043 / 8048S070", "WT32-SC01 Plus", "LilyGO T-Display-S3", "Guition JC3248W535 / JC4827W543C / 4848S040", "LCDWIKI ES3C28P / ES3C35P / ES3C40P", "Waveshare ESP32-S3 Touch-LCD and AMOLED boards"]),
    "esp32c3": dict(name="ESP32-C3", arch="riscv",  usb=True,  ocd="esp32c3", pins=dict(TMS=4, TDI=5, TCK=6, TDO=7), usbpins={"D-": 18, "D+": 19}, wp=0,
                    boards=["Waveshare ESP32-C3-LCD-1.47"]),
    "esp32c6": dict(name="ESP32-C6", arch="riscv",  usb=True,  ocd="esp32c6", pins=dict(TMS=4, TDI=5, TCK=6, TDO=7), usbpins={"D-": 12, "D+": 13}, wp=0,
                    boards=["Waveshare ESP32-C6-Touch-LCD-1.47 (tested in Vundu_lab)", "Waveshare ESP32-C6 LCD / Touch-LCD / Touch-AMOLED boards"]),
    "esp32c5": dict(name="ESP32-C5", arch="riscv",  usb=True,  ocd="esp32c5", pins={}, wp=0,
                    boards=["RockBase NM-CYD-C5", "Waveshare ESP32-C5 LCD and Touch-LCD boards"]),
    "esp32p4": dict(name="ESP32-P4", arch="riscv",  usb=True,  ocd="esp32p4", pins={}, wp=0,
                    boards=["Waveshare ESP32-P4-WIFI6-Touch-LCD and P4-NANO", "Elecrow CrowPanel Advanced 7 / 9 / 10.1 inch", "VIEWE P4 SmartDisplay 7 inch", "Guition JC1060P470C / JC8012P4A1", "M5Stack Tab5"]),
    "esp32c61": dict(name="ESP32-C61", arch="riscv", usb=True, ocd="esp32c61", pins={}, wp=0, boards=["(no LCD Wizard presets)"]),
    "esp32h2": dict(name="ESP32-H2", arch="riscv",  usb=True,  ocd="esp32h2", pins={}, wp=0, boards=["(no LCD Wizard presets)"]),
    "esp32c2": dict(name="ESP32-C2", arch="riscv",  usb=False, ocd="esp32c2", pins={}, wp=0, boards=["(no LCD Wizard presets)"]),
}
# Reference memory map (check the chip's technical reference manual before relying on it).
MEMMAP = {
    "esp32":   [("Flash data (DROM)", 0x3F400000), ("DRAM", 0x3FFB0000), ("IRAM", 0x40080000), ("Flash code (IROM)", 0x400D0000), ("RTC fast", 0x3FF80000), ("RTC slow", 0x50000000), ("Peripherals", 0x3FF40000)],
    "esp32s3": [("Flash data (DROM)", 0x3C000000), ("DRAM", 0x3FC88000), ("IRAM", 0x40370000), ("Flash code (IROM)", 0x42000000), ("RTC", 0x50000000), ("Peripherals", 0x60000000)],
    "esp32c3": [("Flash data (DROM)", 0x3C000000), ("SRAM (data)", 0x3FC80000), ("Flash code (IROM)", 0x42000000), ("RTC", 0x50000000), ("Peripherals", 0x60000000)],
    "esp32c6": [("Flash (code and data)", 0x42000000), ("HP SRAM", 0x40800000), ("LP SRAM", 0x50000000), ("Peripherals", 0x60000000)],
}
# (vid, pid) -> (label, jtag path through this USB device)
KNOWN_USB = {
    ("303a", "1001"): ("Espressif USB Serial/JTAG", "builtin"),
    ("303a", "1000"): ("Espressif USB (firmware CDC / TinyUSB)", "none"),
    ("303a", "0002"): ("ESP32-S2 ROM USB CDC", "none"),
    ("0403", "6010"): ("FTDI FT2232H (ESP-Prog / ESP32-DevKitJ)", "external"),
    ("0403", "6014"): ("FTDI FT232H", "external"),
    ("0403", "6001"): ("FTDI FT232R (UART only)", "uart"),
    ("1a86", "7523"): ("CH340 USB-to-UART", "uart"),
    ("1a86", "55d3"): ("CH343 USB-to-UART", "uart"),
    ("1a86", "55d4"): ("CH9102 USB-to-UART", "uart"),
    ("10c4", "ea60"): ("CP210x USB-to-UART", "uart"),
    ("067b", "2303"): ("PL2303 USB-to-UART", "uart"),
}
FLASH_VENDORS = {"c8": "GigaDevice", "ef": "Winbond", "20": "XMC / Micron", "68": "Boya", "85": "Puya", "5e": "Zbit", "0b": "XTX", "9d": "ISSI", "c2": "Macronix"}
UDEV_FILE = "/etc/udev/rules.d/60-vundu-debug.rules"
UDEV_RULE = ('# Vundu_debug: let your user open Espressif USB devices, and keep ModemManager away from them\n'
             'SUBSYSTEM=="usb", ATTR{idVendor}=="303a", MODE="0666", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"\n'
             'SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="6010", MODE="0666", TAG+="uaccess"\n')
UDEV_CMD = ("sudo tee %s >/dev/null <<'EOF'\n%sEOF\nsudo udevadm control --reload-rules && sudo udevadm trigger" % (UDEV_FILE, UDEV_RULE))

class UsbAccess(RuntimeError):
    """Linux will not let this user open the board's USB JTAG interface (needs the udev rule)."""

# cores per chip (high-performance cores) and which have an extra low-power core
for _k, _c in CHIPS.items():
    _c["cores"] = {"esp32": 2, "esp32s3": 2, "esp32p4": 2}.get(_k, 1)
    _c["lp"] = _k in ("esp32c5", "esp32c6", "esp32p4")

# ---------------------------------------------------------------- plumbing
class Hub:
    def __init__(self):
        self.qs, self.l = set(), threading.Lock()
    def add(self):
        q = queue.Queue(maxsize=3000)
        with self.l: self.qs.add(q)
        return q
    def drop(self, q):
        with self.l: self.qs.discard(q)
    def pub(self, ev, data):
        with self.l:
            for q in list(self.qs):
                try: q.put_nowait((ev, data))
                except queue.Full: pass
hub = Hub()

STATE = dict(devices=[], scans={}, paused=True, autoscan=True)   # starts paused: touches no hardware until its page opens
SCAN_LOCK = threading.Lock()
_ver_cache = {}
_has_cache = {}

def read(p):
    try: return Path(p).read_text().strip()
    except Exception: return ""

def venv_python():
    p = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
    return str(p) if p.exists() else None

def venv_has(mod):
    py = venv_python()
    if not py: return False
    if _has_cache.get(mod): return True          # only "yes" is cached, so a fresh install is noticed
    try: ok = subprocess.run([py, "-c", "import " + mod], capture_output=True, timeout=20).returncode == 0
    except Exception: ok = False
    if ok: _has_cache[mod] = True
    return ok

def needs_usb_rule(vid, pid): return vid == "303a" or (vid, pid) == ("0403", "6010")   # Espressif USB JTAG and ESP-Prog

def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired: return 124, "timed out after %ss" % timeout
    except Exception as e: return 1, str(e)

# ---------------------------------------------------------------- tool discovery
def find_bin(names, extra_globs=()):
    """First name in the list that exists wins; inside one name, tools installed in this folder come first."""
    for n in names:
        exe = n + (".exe" if IS_WIN else "")
        cands = glob.glob(str(TOOLS / "**" / "bin" / exe), recursive=True)
        cands += glob.glob(os.path.expanduser("~/.espressif/tools/*/*/*/bin/" + exe))
        cands += glob.glob(os.path.expanduser("~/.espressif/tools/*/*/bin/" + exe))
        w = shutil.which(n)
        if w: cands.append(w)
        cands = [c for c in cands if os.path.isfile(c)]
        if cands: return sorted(set(cands), key=lambda c: (str(TOOLS) not in c, c))[0]
    return None

def find_openocd(): return find_bin(["openocd"])
def gdb_names(arch, key=""):
    if arch == "riscv": return ["riscv32-esp-elf-gdb"]
    pref = {"esp32": "xtensa-esp32-elf-gdb", "esp32s2": "xtensa-esp32s2-elf-gdb", "esp32s3": "xtensa-esp32s3-elf-gdb"}.get(key)
    return ([pref] if pref else []) + ["xtensa-esp32s3-elf-gdb", "xtensa-esp-elf-gdb", "xtensa-esp32s2-elf-gdb", "xtensa-esp32-elf-gdb"]
def find_gdb(arch, key=""): return find_bin(gdb_names(arch, key))

def ocd_scripts(binpath):
    env = os.environ.get("OPENOCD_SCRIPTS")
    for c in ([env] if env else []) + [os.path.join(os.path.dirname(binpath), "..", "share", "openocd", "scripts")]:
        if c and os.path.isdir(c): return os.path.normpath(c)
    return None

def tool_version(path, args):
    if not path: return ""
    if path in _ver_cache: return _ver_cache[path]
    rc, out = run([path] + args, 15)
    m = re.search(r"(Open On-Chip Debugger[^\n]*|GNU gdb[^\n]*)", out)
    _ver_cache[path] = (m.group(1) if m else out.strip().split("\n")[0])[:120]
    return _ver_cache[path]

def tools_state():
    ocd, gx, gr = find_openocd(), find_gdb("xtensa"), find_gdb("riscv")
    return dict(
        venv=bool(venv_python()), esptool=venv_has("esptool"), pyserial=venv_has("serial"),
        openocd=dict(path=ocd, version=tool_version(ocd, ["--version"]), scripts=bool(ocd and ocd_scripts(ocd))),
        gdb_xtensa=dict(path=gx, version=tool_version(gx, ["--version"])),
        gdb_riscv=dict(path=gr, version=tool_version(gr, ["--version"])))

# ---------------------------------------------------------------- USB scanning
def usb_label(vid, pid):
    return KNOWN_USB.get((vid, pid)) or (("SEGGER J-Link", "external") if vid == "1366" else ("USB device", "other"))

def scan_usb():
    devs = []
    if IS_LINUX:
        for d in glob.glob("/sys/bus/usb/devices/*"):
            name = os.path.basename(d)
            if ":" in name or name.startswith("usb"): continue
            vid, pid = read(d + "/idVendor"), read(d + "/idProduct")
            if not vid or read(d + "/bDeviceClass") == "09": continue
            ports = []
            for root, dirs, _ in os.walk(d):
                if root[len(d):].count(os.sep) > 3: dirs[:] = []; continue
                ports += ["/dev/" + x for x in dirs if x.startswith(("ttyACM", "ttyUSB"))]
            bus, dev = read(d + "/busnum"), read(d + "/devnum")
            node = "/dev/bus/usb/%03d/%03d" % (int(bus or 0), int(dev or 0))
            label, kind = usb_label(vid, pid)
            devs.append(dict(id="%s:%s:%s" % (vid, pid, name), vid=vid, pid=pid, name=name, label=label, kind=kind,
                             manufacturer=read(d + "/manufacturer"), product=read(d + "/product"), serial=read(d + "/serial"),
                             speed=read(d + "/speed"), bus=bus, dev=dev, ports=sorted(set(ports)),
                             usb_access=(os.access(node, os.R_OK | os.W_OK) if os.path.exists(node) else None) if needs_usb_rule(vid, pid) else None,
                             port_access=all(os.access(p, os.R_OK | os.W_OK) for p in ports) if ports else None))
    else:
        py = venv_python()
        if py:
            code = ("import json;from serial.tools import list_ports as l;print(json.dumps([dict(vid='%04x'%p.vid,pid='%04x'%p.pid,port=p.device,"
                    "product=p.description or '',manufacturer=p.manufacturer or '',serial=p.serial_number or '') for p in l.comports() if p.vid]))")
            rc, out = run([py, "-c", code], 10)
            try:
                for p in json.loads(out.strip().splitlines()[-1]):
                    label, kind = usb_label(p["vid"], p["pid"])
                    devs.append(dict(id="%s:%s:%s" % (p["vid"], p["pid"], p["port"]), vid=p["vid"], pid=p["pid"], name=p["port"], label=label, kind=kind,
                                     manufacturer=p["manufacturer"], product=p["product"], serial=p["serial"], speed="", bus="", dev="",
                                     ports=[p["port"]], usb_access=None, port_access=None))
            except Exception: pass
    for d in devs: d["espressif"] = d["vid"] == "303a" or d["kind"] in ("uart", "external")
    return sorted(devs, key=lambda d: d["id"])

def usb_loop():
    prev = {}
    gone = {}                                   # id -> (time it vanished, device) for boards that were being debugged
    while True:
        try:
            devs = scan_usb()
            cur = {d["id"]: d for d in devs}
            changed = set(cur) != set(prev) or any(cur[k] != prev[k] for k in cur if k in prev)
            if changed:
                added = [k for k in cur if k not in prev]
                removed = [k for k in prev if k not in cur]
                STATE["devices"] = devs
                for k in removed:
                    STATE["scans"].pop(k, None)
                    d = prev[k]
                    if needs_usb_rule(d["vid"], d["pid"]): gone[k] = (time.time(), d)
                for k in added: gone.pop(k, None)
                hub.pub("usb", dict(devices=devs, added=added, removed=removed))
                if STATE["autoscan"] and not STATE["paused"]:
                    for k in added:
                        if cur[k]["espressif"] and (cur[k]["ports"] or cur[k]["kind"] == "builtin"):
                            threading.Thread(target=run_scan, args=(k,), daemon=True).start()
                    for k in cur:      # a serial port that showed up after the first look: read the chip now
                        if k in prev and cur[k]["ports"] and not prev[k]["ports"] and cur[k]["espressif"] and "chip" not in STATE["scans"].get(k, {}):
                            threading.Thread(target=run_scan, args=(k,), daemon=True).start()
            prev = cur
            for k, (t0, d) in list(gone.items()):
                if k in cur: gone.pop(k, None); continue
                if time.time() - t0 > 3:
                    gone.pop(k, None)
                    if debugging_active():
                        GDB.stop(); OCD.stop()
                        hub.pub("notice", dict(msg="The board was unplugged, so the debug session was stopped. Plug it back in and press Start session."))
        except Exception: traceback.print_exc()
        time.sleep(1.0)

# ---------------------------------------------------------------- esptool: chip details
def chip_key(text):
    m = re.search(r"ESP32-(S2|S3|C2|C3|C5|C61|C6|H2|P4)", text or "")
    return ("esp32" + m.group(1).lower()) if m else ("esp32" if "ESP32" in (text or "") else None)

def parse_chip(t):
    r = {}
    m = re.search(r"Chip (?:is|type:)\s*(.+)", t)
    if m:
        r["full"] = m.group(1).strip()
        r["key"] = chip_key(r["full"])
        mm = re.search(r"revision v?([\d.]+)", r["full"]); r["rev"] = "v" + mm.group(1) if mm else ""
    m = re.search(r"Features:\s*(.+)", t)
    if m: r["features"] = [x.strip() for x in m.group(1).split(",")]
    m = re.search(r"Crystal is (\d+)\s*MHz", t) or re.search(r"Crystal frequency:\s*(\d+)\s*MHz", t)
    if m: r["crystal"] = m.group(1) + " MHz"
    m = re.search(r"MAC:\s*([0-9a-fA-F:]{17})", t)
    if m: r["mac"] = m.group(1)
    return r

def parse_flash(t):
    r = {}
    m = re.search(r"Detected flash size:\s*(\S+)", t)
    if m: r["size"] = m.group(1)
    m = re.search(r"Manufacturer:\s*([0-9a-fA-F]+)", t)
    if m: r["manufacturer"] = m.group(1).lower(); r["vendor"] = FLASH_VENDORS.get(m.group(1).lower(), "")
    m = re.search(r"Device:\s*([0-9a-fA-F]+)", t)
    if m: r["device"] = m.group(1).lower()
    m = re.search(r"Flash type set by eFuse.*?:\s*(.+)", t) or re.search(r"Flash voltage.*?:\s*(.+)", t)
    return r

EFUSE_RE = re.compile(r"^(\w*JTAG\w*|DIS_DOWNLOAD_MODE|\w*SECURE_BOOT_EN|SECURE_BOOT_EN|SPI_BOOT_CRYPT_CNT|\w*FORCE_DISABLE_SW_LD|USB_PHY_SEL|DIS_USB_OTG\w*)\b.*?=\s*(.+?)\s+(?:R/W|R/-|-/W|R/)", re.M)
def parse_efuses(t):
    out = {}
    for m in EFUSE_RE.finditer(t): out.setdefault(m.group(1), m.group(2).strip())
    return out

def jtag_verdict(dev, key, ef):
    """Say, in plain words, whether JTAG can work through what is plugged in."""
    c = CHIPS.get(key or "", None)
    notes, level, mode = [], "warn", dev["kind"]
    disabled = [k for k, v in ef.items() if "JTAG" in k and str(v).lower() in ("true", "1", "0b1") and not k.startswith(("JTAG_SEL", "STRAP", "SOFT"))]
    soft = [k for k, v in ef.items() if k.startswith("SOFT_DIS_JTAG") and str(v) not in ("0", "False", "false", "0b000")]
    usb_off = [k for k in disabled if "USB" in k]; pad_off = [k for k in disabled if k not in usb_off]   # two separate routes: USB JTAG and the JTAG pins
    blocked = (usb_off and pad_off) or (usb_off and dev["kind"] == "builtin") or (pad_off and dev["kind"] == "external")
    if usb_off: notes.append("eFuse %s switches off the built-in USB JTAG. An external adapter on the JTAG pins still works." % ", ".join(usb_off))
    if pad_off: notes.append("eFuse %s switches off the JTAG pins.%s" % (", ".join(pad_off), " The built-in USB JTAG still works." if c and c["usb"] else ""))
    if usb_off and pad_off: notes.append("Both routes are off: JTAG is permanently disabled on this chip.")
    if blocked:
        level = "bad"; mode = "none"
    elif c is None:
        notes.append("The chip has not been read yet, so JTAG support cannot be judged. Press Read again. If it keeps failing, check the cable, or hold BOOT and tap RESET.")
    elif dev["kind"] == "builtin":
        if c["usb"]: notes.append("This chip has the built-in USB Serial/JTAG block and it is what you plugged in. No extra adapter needed."); level = "ok"
        else: notes.append("This USB device looks like the built-in block, but %s does not have one. Check the chip reading." % c["name"]); level = "warn"
    elif dev["kind"] == "none":
        notes.append("The firmware (or ROM) is using this USB port as a plain serial/TinyUSB device, so the JTAG function is not present right now. Flash firmware that leaves USB Serial/JTAG alone, or hold BOOT and tap RESET.")
        level = "warn"
    elif dev["kind"] == "uart":
        if c["usb"]: notes.append("You are connected through a USB-to-UART chip, which cannot carry JTAG. %s has built-in USB JTAG: use the board's native USB port (often a second USB-C socket or the USB pins) instead." % c["name"])
        else: notes.append("USB-to-UART bridge: no JTAG here. %s has no built-in USB JTAG, so you need an external adapter (ESP-Prog, FT2232H or J-Link) on the JTAG GPIOs." % c["name"])
        level = "warn"
    elif dev["kind"] == "external": notes.append("External JTAG adapter found. Wire it to the chip's JTAG GPIOs and pick the FTDI / J-Link interface in the sidebar."); level = "ok"
    if soft: notes.append("SOFT_DIS_JTAG is set: JTAG is switched off until the firmware or a strap re-enables it.")
    if c and not c["usb"] and dev["kind"] != "external":
        notes.append("%s boards can only be debugged through an external adapter on the JTAG GPIOs (see the pin table)." % c["name"])
    return dict(mode=mode, level=level, notes=notes, disabled=bool(disabled))

def run_scan(dev_id):
    dev = next((d for d in STATE["devices"] if d["id"] == dev_id), None)
    if not dev: return
    if STATE["paused"]:
        hub.pub("scan", dict(id=dev_id, error="Vundu_debug is paused (Vundu_lab has the board).")); return
    if OCD.running():
        hub.pub("scan", dict(id=dev_id, error="A debug session is running. Stop OpenOCD before reading the board again (reading resets the chip).")); return
    if not SCAN_LOCK.acquire(blocking=False): return
    try:
        for _ in range(20):            # the serial port is created a moment after the USB device shows up
            dev = next((d for d in STATE["devices"] if d["id"] == dev_id), None)
            if not dev: return
            if dev["ports"] or dev["kind"] not in ("builtin", "uart", "none"): break
            hub.pub("scan", dict(id=dev_id, stage="Waiting for the serial port to appear..."))
            time.sleep(0.5)
        res = dict(id=dev_id, t=time.time(), device=dev)
        hub.pub("scan", dict(id=dev_id, stage="Reading chip details..."))
        port = dev["ports"][0] if dev["ports"] else None
        py = venv_python()
        t_all = {}
        if not port:
            res["error"] = "No serial port appeared for this device, so the chip could not be read. JTAG may still work; start OpenOCD to try."
        elif not py or not venv_has("esptool"):
            res["error"] = "esptool is not installed yet. Open the Setup tab and press Install (one-off)."
        elif dev.get("port_access") is False:
            res["error"] = "No permission to open %s. Run: sudo usermod -aG dialout $USER  then log out and back in." % port
        else:
            rc, t = run([py, "-m", "esptool", "--port", port, "chip_id"], 40); t_all["chip_id"] = t
            res["chip"] = parse_chip(t)
            if not res["chip"].get("full"):
                res["error"] = "No ESP chip answered on %s. Try another cable, or hold BOOT and tap RESET, then rescan." % port
            else:
                hub.pub("scan", dict(id=dev_id, stage="Reading flash..."))
                rc, t = run([py, "-m", "esptool", "--port", port, "flash_id"], 40); t_all["flash_id"] = t
                res["flash"] = parse_flash(t)
                m = re.search(r"(?:Embedded )?PSRAM\s*(\d+\s*MB)?", " ".join(res["chip"].get("features", [])))
                res["psram"] = (m.group(1) or "yes") if m else "none reported"
                hub.pub("scan", dict(id=dev_id, stage="Reading security and JTAG eFuses (read-only)..."))
                rc, t = run([py, "-m", "esptool", "--port", port, "get_security_info"], 30); t_all["security"] = t
                res["security"] = [l.strip() for l in t.splitlines() if re.search(r"(Secure Boot|Flash Encryption|Chip_ID|Flags|Crypt|JTAG|Download)", l)]
                rc, t = run([py, "-m", "espefuse", "--port", port, "--do-not-confirm", "summary"], 45); t_all["efuse"] = t
                res["efuses"] = parse_efuses(t)
        res["raw"] = t_all
        key = (res.get("chip") or {}).get("key")
        res["chip_key"] = key
        res["jtag"] = jtag_verdict(dev, key, res.get("efuses") or {})
        STATE["scans"][dev_id] = res
        hub.pub("scan", res)
    except Exception as e:
        traceback.print_exc(); hub.pub("scan", dict(id=dev_id, error=str(e)))
    finally: SCAN_LOCK.release()

# ---------------------------------------------------------------- OpenOCD
def strip_iac(b):
    out, i = bytearray(), 0
    while i < len(b):
        if b[i] == 255 and i + 1 < len(b):
            if b[i + 1] == 255: out.append(255); i += 2
            elif 251 <= b[i + 1] <= 254: i += 3
            else: i += 2
        else: out.append(b[i]); i += 1
    return bytes(out)

def port_free(p):
    s = socket.socket()
    try: s.bind((HOST, p)); return True
    except OSError: return False
    finally: s.close()

class Ocd:
    def __init__(self):
        self.proc = None; self.state = "stopped"; self.cfg = []; self.cmd = ""
        self.lines = collections.deque(maxlen=3000)
        self.sock = None; self.cl = threading.Lock(); self.exit_note = ""
    def running(self): return self.proc is not None and self.proc.poll() is None
    def info(self): return dict(state=self.state, cfg=self.cfg, cmd=self.cmd, exit=self.exit_note)
    def _set(self, st):
        self.state = st; hub.pub("ocd_state", self.info()); notify_parent_state()
    def start(self, o):
        if self.running(): raise RuntimeError("OpenOCD is already running.")
        binp = find_openocd()
        if not binp: raise RuntimeError("OpenOCD is not installed. Open the Setup tab and press Install.")
        scripts = ocd_scripts(binp)
        if not scripts: raise RuntimeError("Found OpenOCD at %s but not its scripts folder. Set OPENOCD_SCRIPTS or reinstall from the Setup tab." % binp)
        for p in (3333, 4444, 6666):
            if not port_free(p): raise RuntimeError("Port %d is already in use (another OpenOCD still running?). Close it, then try again. On Linux: pkill openocd" % p)
        key = o.get("chip") or ""
        c = CHIPS.get(key)
        mode = o.get("mode", "builtin")
        if IS_LINUX and mode in ("builtin", "ftdi"):          # catch the two commonest problems before OpenOCD fails on them
            match = (lambda d: d["kind"] == "builtin") if mode == "builtin" else (lambda d: (d["vid"], d["pid"]) == ("0403", "6010"))
            devs = [d for d in STATE["devices"] if match(d)]
            dev = next((d for d in devs if d["id"] == o.get("device")), devs[0] if devs else None)
            if dev is None:
                raise RuntimeError("No %s is plugged in. %s" % (
                    "built-in USB JTAG device (303a:1001)" if mode == "builtin" else "FTDI adapter (0403:6010)",
                    "If the board is plugged in, its firmware may be using the USB port itself (the Board tab says so), or this is the board's UART socket. Use its native USB socket, or hold BOOT and tap RESET." if mode == "builtin" else "Check the adapter's USB cable."))
            if dev.get("usb_access") is False:
                raise UsbAccess("Linux is blocking access to the board's USB JTAG interface. Install the one-off USB rule (shown in red on the Board tab), then press Start session again.")
        cfgs = []
        if mode == "custom":
            cfgs = [x for x in shlex.split(o.get("custom", "")) if x]
            if not cfgs: raise RuntimeError("Custom mode needs config files, e.g.  interface/jlink.cfg target/esp32s3.cfg")
        else:
            if not c: raise RuntimeError("Pick the chip first (read the board, or choose it in the sidebar).")
            if mode == "builtin":
                if not c["usb"]: raise RuntimeError("%s has no built-in USB JTAG. Choose the FTDI (ESP-Prog) or J-Link interface." % c["name"])
                b = "board/%s-builtin.cfg" % c["ocd"]
                cfgs = [b] if os.path.exists(os.path.join(scripts, b)) else ["interface/esp_usb_jtag.cfg", "target/%s.cfg" % c["ocd"]]
            elif mode == "ftdi": cfgs = ["interface/ftdi/esp32_devkitj_v1.cfg", "target/%s.cfg" % c["ocd"]]
            elif mode == "jlink": cfgs = ["interface/jlink.cfg", "target/%s.cfg" % c["ocd"]]
            else: raise RuntimeError("Unknown interface mode.")
            missing = [f for f in cfgs if not os.path.exists(os.path.join(scripts, f))]
            if missing: raise RuntimeError("This OpenOCD build has no %s. It is probably too old for %s: install the latest from the Setup tab." % (", ".join(missing), c["name"]))
        args = [binp, "-s", scripts, "-c", "bindto 127.0.0.1"]
        if not o.get("rtos", True): args += ["-c", "set ESP_RTOS none"]
        if not o.get("flash", True): args += ["-c", "set ESP_FLASH_SIZE 0"]
        for f in cfgs: args += ["-f", f]
        if o.get("pin_serial") and o.get("serial"): args += ["-c", "adapter serial %s" % o["serial"]]
        if o.get("speed"): args += ["-c", "adapter speed %d" % int(o["speed"])]
        if o.get("extra"): args += shlex.split(o["extra"])
        self.cfg, self.cmd, self.exit_note = cfgs, " ".join(shlex.quote(a) for a in args), ""
        self.lines.clear()
        self._line("$ " + self.cmd)
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
        self._set("starting")
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
    def _line(self, s):
        self.lines.append(s); hub.pub("ocd_log", s)
    def _reader(self, proc):
        for ln in proc.stdout:
            ln = ln.rstrip("\n"); self._line(ln)
            if "Listening on port 3333" in ln and self.state == "starting": self._set("running")     # printed only after the chip was examined
            if "Listening on port 4444" in ln: threading.Timer(10, self._late_ready, args=(proc,)).start()
        rc = proc.wait()
        self.exit_note = "OpenOCD exited (code %s)" % rc
        self._line(self.exit_note); self._drop()
        if proc is self.proc: self._set("stopped")
    def _late_ready(self, proc):
        if proc is self.proc and proc.poll() is None and self.state == "starting":
            self._line("Warn : the chip was not confirmed ready after 10 s. The command port is open so you can investigate; check the lines above.")
            self._set("running")
    def stop(self):
        if self.running():
            self.proc.terminate()
            try: self.proc.wait(4)
            except subprocess.TimeoutExpired: self.proc.kill()
        self._drop()
    def _drop(self):
        with self.cl:
            try:
                if self.sock: self.sock.close()
            except Exception: pass
            self.sock = None
    def _conn(self):
        if self.sock: return self.sock
        if not self.running(): raise RuntimeError("No session is running. Press Start session first.")
        err = None
        for _ in range(20):
            try:
                s = socket.create_connection((HOST, 4444), 3); self._read_prompt(s, 4); self.sock = s; return s
            except Exception as e: err = e; time.sleep(0.25)
        raise RuntimeError("Could not reach OpenOCD's command port: %s" % err)
    def _read_prompt(self, s, timeout):
        s.settimeout(timeout); buf = b""; end = time.time() + timeout
        while time.time() < end:
            try: ch = s.recv(65536)
            except socket.timeout: break
            if not ch: raise ConnectionError("closed")
            buf += ch
            if re.search(rb"\n> $", strip_iac(buf)): break
        return strip_iac(buf).decode(errors="replace").replace("\r", "")
    def cmd_run(self, cmd, timeout=8):
        with self.cl:
            s = self._conn()
            try:
                s.sendall((cmd + "\r\n").encode())
                text = self._read_prompt(s, timeout)
            except Exception:
                try: s.close()
                except Exception: pass
                self.sock = None; raise
        text = re.sub(r"\n> $", "", text)
        lines = text.split("\n")
        if lines and lines[0].strip() == cmd.strip(): lines = lines[1:]
        lines = [l for l in lines if not re.match(r"^(Info|Debug)\s*:", l)]
        return "\n".join(lines).strip("\n")
OCD = Ocd()

REG_RE = re.compile(r"\((\d+)\)\s+(\S+)\s+\(/(\d+)\)(?::\s*(0x[0-9a-fA-F]+))?")
def parse_targets(t):
    out = []
    for l in t.splitlines():
        m = re.match(r"^\s*(\d+)(\*?)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$", l)
        if m: out.append(dict(idx=int(m.group(1)), current=bool(m.group(2)), name=m.group(3), type=m.group(4), state=m.group(7)))
    return out

def ocd_status():
    if OCD.state != "running": return dict(state=OCD.state, cores=[])
    cores = parse_targets(OCD.cmd_run("targets"))
    return dict(state="running", cores=cores)

def ocd_regs():
    t = OCD.cmd_run("reg", 15)
    regs = [dict(n=int(m.group(1)), name=m.group(2), bits=int(m.group(3)), value=m.group(4)) for m in REG_RE.finditer(t)]
    return dict(regs=[r for r in regs if r["value"]], total=len(regs), text=t if not regs else "")

def ocd_mem(addr, length):
    length = max(1, min(int(length), 4096))
    t = OCD.cmd_run("mdb 0x%x %d" % (addr, length), 10)
    data = {}
    for l in t.splitlines():
        m = re.match(r"^(0x[0-9a-fA-F]+):\s+((?:[0-9a-fA-F]{2}\s*)+)$", l)
        if m:
            a = int(m.group(1), 16)
            for i, b in enumerate(m.group(2).split()): data[a + i] = int(b, 16)
    if not data: return dict(addr=addr, bytes=[], error=t.strip() or "no data")
    return dict(addr=addr, bytes=[data.get(addr + i) for i in range(length)])

def ocd_bps():
    bps = [dict(addr=m.group(1), len=m.group(2), hw=m.group(3) == "1") for m in re.finditer(r"Breakpoint\(\w+\):\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),\s*(\d+)", OCD.cmd_run("bp"))]
    wps = [dict(addr=m.group(1), len=m.group(2), kind={"0": "read", "1": "write", "2": "access"}.get(m.group(3), m.group(3)))
           for m in re.finditer(r"address:\s*(0x[0-9a-fA-F]+),\s*len:\s*(0x[0-9a-fA-F]+),\s*r/w/a:\s*(\d+)", OCD.cmd_run("wp"))]
    return dict(bps=bps, wps=wps)

TARGET_CMDS = dict(halt="halt", resume="resume", step="step", reset_halt="reset halt", reset_run="reset run", reset_init="reset init")

# ---------------------------------------------------------------- GDB (console mode)
class Gdb:
    def __init__(self): self.proc = None; self.arch = ""; self.path = ""
    def running(self): return self.proc is not None and self.proc.poll() is None
    def start(self, arch, elf, key=""):
        if self.running(): raise RuntimeError("GDB is already running.")
        p = find_gdb(arch, key)
        if not p: raise RuntimeError("No %s GDB found. Install it from the Setup tab (or install ESP-IDF tools)." % ("RISC-V" if arch == "riscv" else "Xtensa"))
        args = [p, "-q", "-nx", "-iex", "set pagination off", "-iex", "set confirm off", "-iex", "set width 0", "-iex", "set height 0"]
        if elf:
            if not os.path.isfile(elf): raise RuntimeError("ELF file not found: %s" % elf)
            args.append(elf)
        self.arch, self.path = arch, p
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        hub.pub("gdb_state", dict(running=True, path=p)); notify_parent_state()
    def _reader(self, proc):
        fd = proc.stdout.fileno()
        while True:
            try: b = os.read(fd, 4096)
            except OSError: break
            if not b: break
            hub.pub("gdb", b.decode(errors="replace"))
        proc.wait(); hub.pub("gdb", "\n[gdb exited]\n"); hub.pub("gdb_state", dict(running=False)); notify_parent_state()
    def send(self, line):
        if not self.running(): raise RuntimeError("GDB is not running.")
        self.proc.stdin.write((line + "\n").encode()); self.proc.stdin.flush()
    def stop(self):
        if self.running():
            try: self.send("quit")
            except Exception: pass
            try: self.proc.wait(2)
            except subprocess.TimeoutExpired: self.proc.kill()
GDB = Gdb()

def debugging_active(): return OCD.running() or GDB.running()
def notify_parent_state():
    hub.pub("session", dict(active=debugging_active(), ocd=OCD.state, gdb=GDB.running()))

# ---------------------------------------------------------------- installers
# Downloads come from ESP-IDF's own tool list (tools.json), which names the current
# Espressif OpenOCD and GDB builds for every platform, with checksums.  It is a plain
# file download, so GitHub's API rate limit does not apply.
TOOLS_JSON = "https://raw.githubusercontent.com/espressif/esp-idf/master/tools/tools.json"

def plat_key():
    m = platform.machine().lower()
    if IS_WIN: return "win-arm64" if "arm" in m else "win64"
    if sys.platform == "darwin": return "macos-arm64" if m in ("arm64", "aarch64") else "macos"
    if m in ("aarch64", "arm64"): return "linux-arm64"
    if m.startswith("armv7"): return "linux-armhf"
    return "linux-amd64"

def ilog(msg, kind="info"): hub.pub("install", dict(msg=msg, kind=kind))

def download(url, dest):
    mirror = os.environ.get("IDF_GITHUB_ASSETS")       # Espressif's optional mirror, same convention as ESP-IDF
    if mirror: url = url.replace("https://github.com", "https://" + mirror, 1)
    req = urllib.request.Request(url, headers={"User-Agent": "vundu-debug"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total, got, last = int(r.headers.get("Content-Length") or 0), 0, 0
        while True:
            ch = r.read(1 << 18)
            if not ch: break
            f.write(ch); got += len(ch)
            if total and time.time() - last > 0.7: last = time.time(); ilog("  %d%% of %.0f MB" % (got * 100 // total, total / 1e6), "progress")

def safe_extract(archive, dest):
    dest = Path(dest).resolve(); dest.mkdir(parents=True, exist_ok=True)
    if str(archive).endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if not (dest / n).resolve().is_relative_to(dest): raise RuntimeError("unsafe path in archive")
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if not (dest / m.name).resolve().is_relative_to(dest): raise RuntimeError("unsafe path in archive")
            t.extractall(dest)

def install_tool(data, name, key, sub, label):
    t = next((x for x in data["tools"] if x["name"] == name), None)
    vs = [v for v in (t or {}).get("versions", []) if key in v]
    if not vs: raise RuntimeError("ESP-IDF's tool list has no %s build for this computer (%s). Install ESP-IDF, or fetch it by hand (see README)." % (label, key))
    v = ([x for x in vs if x.get("status") == "recommended"] or vs)[-1]
    a = v[key]; fname = a["url"].split("/")[-1]
    ilog("Downloading %s %s (%.0f MB)" % (label, v["name"], a.get("size", 0) / 1e6))
    TOOLS.mkdir(parents=True, exist_ok=True)
    tmp = TOOLS / fname
    download(a["url"], tmp)
    if a.get("sha256"):
        h = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if h != a["sha256"]: tmp.unlink(missing_ok=True); raise RuntimeError("Checksum mismatch for %s. The download was damaged; try again." % fname)
        ilog("  checksum ok")
    ilog("Unpacking...")
    shutil.rmtree(TOOLS / sub, ignore_errors=True)
    safe_extract(tmp, TOOLS / sub); tmp.unlink(missing_ok=True)
    for b in glob.glob(str(TOOLS / sub / "**" / "bin" / "*"), recursive=True):
        try: os.chmod(b, 0o755)
        except Exception: pass
    ilog("%s %s installed." % (label, v["name"]), "ok")

def install_job(what):
    try:
        force = what != "all"            # "all" only fills gaps; a single button reinstalls
        t = tools_state()
        if what in ("serial", "all") and (force or not (t["esptool"] and t["pyserial"])):
            ilog("Creating the tools environment (needs python3-venv on Ubuntu)...")
            ENG.mkdir(parents=True, exist_ok=True)
            if not venv_python(): venv.EnvBuilder(with_pip=True, clear=False).create(str(VENV))
            p = subprocess.Popen([venv_python(), "-m", "pip", "install", "--upgrade", "pyserial", "esptool"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for l in p.stdout: ilog("  " + l.rstrip())
            if p.wait() != 0: raise RuntimeError("pip failed (see lines above)")
            ilog("pyserial and esptool installed.", "ok")
        want = lambda *k: what == "all" or what in k
        need = []
        if want("openocd") and (force or not (t["openocd"]["path"] and t["openocd"]["scripts"])): need.append(("openocd-esp32", "openocd", "OpenOCD"))
        if want("gdb") and (force or not t["gdb_xtensa"]["path"]): need.append(("xtensa-esp-elf-gdb", "gdb-xtensa", "GDB for Xtensa"))
        if want("gdb") and (force or not t["gdb_riscv"]["path"]): need.append(("riscv32-esp-elf-gdb", "gdb-riscv", "GDB for RISC-V"))
        if need:
            ilog("Reading Espressif's current tool list...")
            req = urllib.request.Request(TOOLS_JSON, headers={"User-Agent": "vundu-debug"})
            data = json.load(urllib.request.urlopen(req, timeout=30))
            for name, sub, lab in need: install_tool(data, name, plat_key(), sub, lab)
        ilog("Done.", "ok")
    except Exception as e:
        ilog("Install failed: %s" % e, "error")
    finally: _ver_cache.clear(); _has_cache.clear(); hub.pub("tools", tools_state())

# ---------------------------------------------------------------- Vundu_lab handoff
LAB_FIRST, LAB_LAST = 8765, 8784
def find_lab():
    """Address of a running Vundu_lab (its page title is just 'Vundu'), or None."""
    for p in range(LAB_FIRST, LAB_LAST + 1):
        try:
            c = http.client.HTTPConnection(HOST, p, timeout=0.5); c.request("GET", "/")
            body = c.getresponse().read(6000).decode("utf-8", "ignore"); c.close()
            if "<title>Vundu</title>" in body: return "http://%s:%d/" % (HOST, p)
        except Exception: continue
    return None

def lab_hint():
    for d in [ROOT.parent] + sorted(ROOT.parent.glob("Vundu_lab*")) + [ROOT / "Vundu_lab"]:
        if (d / "vundu_lab.py").exists(): return "cd %s && python3 vundu_lab.py" % shlex.quote(str(d))
    return "In your Vundu_lab folder run:  python3 vundu_lab.py"

def pause_debugger():
    """Hand the board over: stop GDB and OpenOCD and refuse hardware work until resumed."""
    STATE["paused"] = True
    GDB.stop(); OCD.stop()
    hub.pub("paused", dict(paused=True)); notify_parent_state()

def resume_debugger():
    STATE["paused"] = False
    hub.pub("paused", dict(paused=False))
    if STATE["autoscan"]:                                   # read boards that are already plugged in
        for d in STATE["devices"]:
            if d["espressif"] and "chip" not in STATE["scans"].get(d["id"], {}) and (d["ports"] or d["kind"] == "builtin"):
                threading.Thread(target=run_scan, args=(d["id"],), daemon=True).start()

def watchdog():
    """If the debugger page goes away (tab closed) while the debugger is active, let go of the board after 30 s."""
    idle = None
    while True:
        time.sleep(3)
        if hub.qs or STATE["paused"]: idle = None; continue
        idle = idle or time.time()
        if time.time() - idle > 30:
            print("The Vundu_debug page was closed: stopping any session and pausing, so the board is free.")
            pause_debugger(); idle = None

def open_browser(url):   # quiet launch, so the browser's own messages stay out of this terminal
    try:
        if IS_LINUX and shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else: webbrowser.open(url)
    except Exception: pass

# ---------------------------------------------------------------- start-up check
def startup_report():
    def row(n, ok, fix=None):
        print("  %-40s %s" % (n + " " + "." * max(2, 38 - len(n)), "ok" if ok is True else ("MISSING" if ok is False else ok)))
        if ok is False and fix:
            for f in fix: print("      -> " + f)
    print("Vundu_debug V%s - start-up check" % VERSION); bad = 0
    t = tools_state()
    row("Python %s" % platform.python_version(), sys.version_info >= (3, 9), ["install Python 3.9 or newer"])
    row("Tools environment (venv + pip)", t["venv"], ["open Setup and press Install (one-off)", "Ubuntu: sudo apt install python3-venv"])
    row("esptool (reads chip details)", t["esptool"], ["Setup tab > Install (one-off)"])
    row("OpenOCD (Espressif build)", bool(t["openocd"]["path"]) and t["openocd"]["scripts"], ["Setup tab > Install OpenOCD"])
    row("GDB for Xtensa (ESP32, S2, S3)", bool(t["gdb_xtensa"]["path"]), ["Setup tab > Install GDB"])
    row("GDB for RISC-V (C3, C5, C6, H2, P4)", bool(t["gdb_riscv"]["path"]), ["Setup tab > Install GDB"])
    if IS_LINUX:
        import grp
        try: ing = "dialout" in [grp.getgrgid(g).gr_name for g in os.getgroups()]
        except Exception: ing = True
        row("Serial port access (dialout group)", ing, ["sudo usermod -aG dialout $USER   (then log out and back in)"])
        rule = any(os.path.exists(p) for p in glob.glob("/etc/udev/rules.d/*openocd*") + glob.glob("/etc/udev/rules.d/*vundu*") + glob.glob("/usr/lib/udev/rules.d/*openocd*") + glob.glob("/lib/udev/rules.d/*openocd*"))
        row("USB JTAG access (udev rule)", rule, ["the Setup tab shows the exact rule to install"])
    print("  Missing items never stop the app from starting.\n")

# ---------------------------------------------------------------- HTTP
INDEX = None
ORIGIN_OK = re.compile(r"^http://(127\.0\.0\.1|localhost):\d+$")

class H(http.server.BaseHTTPRequestHandler):
    server_version = "VunduDebug/" + VERSION
    def log_message(self, *a): pass
    def _host_ok(self): return self.headers.get("Host", "") in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
    def _send(self, code, body, ctype="application/json", extra=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def _json(self, obj, code=200, extra=None): self._send(code, json.dumps(obj), extra=extra)
    def _authed(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return self.headers.get("X-Vundu-Token") == TOKEN or q.get("t", [""])[0] == TOKEN
    def _guard_paused(self):
        if STATE["paused"]:
            self._json(dict(error="Vundu_debug is paused because Vundu_lab has the board. Press Resume debugger."), 423); return True
        return False
    def do_GET(self):
        if not self._host_ok(): return self._send(403, "bad host", "text/plain")
        path = urllib.parse.urlparse(self.path).path
        if path == "/": return self._send(200, INDEX.replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if path == "/vundu_logo.png" and (ROOT / "vundu_logo.png").exists():
            return self._send(200, (ROOT / "vundu_logo.png").read_bytes(), "image/png")
        if path == "/api/public_state":       # read-only, for Vundu_lab to colour its tabs
            origin = self.headers.get("Origin", "")
            return self._json(dict(app="vundu_debug", version=VERSION, debugging=debugging_active(), paused=STATE["paused"]),
                              extra={"Access-Control-Allow-Origin": origin} if ORIGIN_OK.match(origin) else None)
        if not self._authed(): return self._json(dict(error="unauthorised"), 401)
        try:
            if path == "/api/events": return self._sse()
            if path == "/api/state":
                return self._json(dict(version=VERSION, tools=tools_state(), ocd=OCD.info(), gdb=dict(running=GDB.running(), path=GDB.path), paused=STATE["paused"],
                                       devices=STATE["devices"], scans=STATE["scans"], autoscan=STATE["autoscan"], chips=CHIPS,
                                       memmap={k: v for k, v in MEMMAP.items()}, udev=UDEV_RULE, udev_cmd=UDEV_CMD, ocd_log=list(OCD.lines)[-400:], platform=sys.platform))
            if path == "/api/lab": return self._json(dict(lab=find_lab(), hint=lab_hint()))
            if path == "/api/status": return self._json(ocd_status())
            if path == "/api/regs": return self._json(ocd_regs())
            if path == "/api/bps": return self._json(ocd_bps())
            self._json(dict(error="not found"), 404)
        except Exception as e: self._json(dict(error=str(e)), 500)
    def _sse(self):
        q = hub.add()
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store"); self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n"); self.wfile.flush()
            while True:
                try: ev, data = q.get(timeout=15); self.wfile.write(("event: %s\ndata: %s\n\n" % (ev, json.dumps(data))).encode())
                except queue.Empty: self.wfile.write(b": ka\n\n")
                self.wfile.flush()
        except Exception: pass
        finally: hub.drop(q)
    def do_POST(self):
        if not self._host_ok(): return self._send(403, "bad host", "text/plain")
        origin = self.headers.get("Origin", "")
        if origin and origin != "http://%s" % self.headers.get("Host"): return self._json(dict(error="cross-site request refused"), 403)
        if not self._authed(): return self._json(dict(error="unauthorised"), 401)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/elf":                       # raw upload, file name in the query
                name = os.path.basename(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("name", ["firmware.elf"])[0]) or "firmware.elf"
                UPLOADS.mkdir(parents=True, exist_ok=True); dest = UPLOADS / name; dest.write_bytes(raw)
                return self._json(dict(path=str(dest), size=len(raw)))
            b = json.loads(raw or b"{}")
            if path == "/api/resume": resume_debugger(); return self._json(dict(paused=False))
            if path == "/api/handoff":
                pause_debugger(); return self._json(dict(paused=True, lab=find_lab(), hint=lab_hint()))
            if path == "/api/autoscan": STATE["autoscan"] = bool(b.get("on")); return self._json(dict(on=STATE["autoscan"]))
            if path == "/api/install":
                threading.Thread(target=install_job, args=(b.get("what", "all"),), daemon=True).start(); return self._json(dict(ok=True))
            if self._guard_paused(): return
            if path == "/api/scan": threading.Thread(target=run_scan, args=(b["id"],), daemon=True).start(); return self._json(dict(ok=True))
            if path == "/api/ocd/start": OCD.start(b); return self._json(OCD.info())
            if path == "/api/ocd/stop": OCD.stop(); return self._json(OCD.info())
            if path == "/api/ocd": return self._json(dict(out=OCD.cmd_run(str(b["cmd"]), float(b.get("timeout", 8)))))
            if path == "/api/target":
                act = b["action"]
                if act == "select": return self._json(dict(out=OCD.cmd_run("targets " + re.sub(r"[^\w.\-]", "", b["name"]))))
                return self._json(dict(out=OCD.cmd_run(TARGET_CMDS[act], 15)))
            if path == "/api/mem":
                if b.get("write") is not None:
                    return self._json(dict(out=OCD.cmd_run("mww 0x%x 0x%x" % (int(b["addr"]), int(b["write"]) & 0xFFFFFFFF))))
                return self._json(ocd_mem(int(b["addr"]), int(b.get("len", 256))))
            if path == "/api/bp":
                op, a = b["op"], int(b["addr"])
                cmd = {"add": "bp 0x%x %d hw" % (a, int(b.get("len", 2))), "del": "rbp 0x%x" % a,
                       "wadd": "wp 0x%x %d %s" % (a, int(b.get("len", 4)), {"read": "r", "write": "w", "access": "a"}[b.get("kind", "write")]), "wdel": "rwp 0x%x" % a}[op]
                return self._json(dict(out=OCD.cmd_run(cmd)))
            if path == "/api/gdb/start": GDB.start(b.get("arch", "xtensa"), b.get("elf", ""), b.get("chip", "")); return self._json(dict(ok=True))
            if path == "/api/gdb/stop": GDB.stop(); return self._json(dict(ok=True))
            if path == "/api/gdb": GDB.send(str(b["cmd"])); return self._json(dict(ok=True))
            if path == "/api/gdb/attach":
                for l in (["target extended-remote :3333"] + (["set remote hardware-watchpoint-limit %d" % b["wp"]] if b.get("wp") else []) + ["monitor reset halt", "flushregs"]):
                    GDB.send(l); time.sleep(0.4)
                return self._json(dict(ok=True))
            self._json(dict(error="not found"), 404)
        except KeyError as e: self._json(dict(error="missing field %s" % e), 400)
        except UsbAccess as e: self._json(dict(error=str(e), fix="udev"), 409)
        except Exception as e: self._json(dict(error=str(e)), 500)

class Srv(http.server.ThreadingHTTPServer):
    daemon_threads = True

def main():
    global INDEX, PORT
    INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
    for d in (ENG, TOOLS, UPLOADS): d.mkdir(parents=True, exist_ok=True)
    startup_report()
    srv = None
    for p in range(PORT_FIRST, PORT_LAST + 1):
        try: srv = Srv((HOST, p), H); PORT = p; break
        except OSError: continue
    if not srv: sys.exit("No free port between %d and %d." % (PORT_FIRST, PORT_LAST))
    atexit.register(lambda: (GDB.stop(), OCD.stop()))
    threading.Thread(target=usb_loop, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    url = "http://%s:%d/" % (HOST, PORT)
    print("Vundu_debug is running at " + url + "   (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: open_browser(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nStopping.")

if __name__ == "__main__": main()
