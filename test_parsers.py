"""Checks the readers for esptool / OpenOCD output. Run: python3 test_parsers.py"""
import vundu_debug as v

def test_chip():
    c = v.parse_chip("Chip type:          ESP32-C61 (revision v1.0)\nFeatures: WiFi 6, BT 5\nCrystal frequency:  40MHz\nMAC: 60:55:f9:aa:bb:cc")
    assert c["key"] == "esp32c61" and c["rev"] == "v1.0" and c["crystal"] == "40 MHz" and c["mac"] == "60:55:f9:aa:bb:cc"
    assert v.parse_chip("Chip is ESP32-C6 (QFN40) (revision v0.1)")["key"] == "esp32c6"

def test_flash():
    f = v.parse_flash("Manufacturer: c8\nDevice: 4017\nDetected flash size: 8MB")
    assert f["size"] == "8MB" and f["vendor"] == "GigaDevice"

def test_targets():
    t = v.parse_targets("    TargetName         Type       Endian TapName            State\n"
                        "--  ------------------ ---------- ------ ------------------ ------------\n"
                        " 0* esp32s3.cpu0        esp32s3    little esp32s3.cpu0       halted\n"
                        " 1  esp32s3.cpu1        esp32s3    little esp32s3.cpu1       running")
    assert [(x["name"], x["current"], x["state"]) for x in t] == [("esp32s3.cpu0", True, "halted"), ("esp32s3.cpu1", False, "running")]

def verdict(kind, key, ef): return v.jtag_verdict(dict(kind=kind), key, ef)

def test_usb_fuse_only_blocks_usb_route():
    ef = {"DIS_USB_JTAG": "True"}
    assert verdict("builtin", "esp32s3", ef)["level"] == "bad"          # the USB route you plugged in is off
    assert verdict("external", "esp32s3", ef)["level"] == "ok"          # the pins still work

def test_pad_fuse_only_blocks_pin_route():
    ef = {"DIS_PAD_JTAG": "True"}
    assert verdict("external", "esp32s3", ef)["level"] == "bad"
    assert verdict("builtin", "esp32s3", ef)["level"] == "ok"

def test_both_fuses_block_everything():
    r = verdict("builtin", "esp32s3", {"DIS_USB_JTAG": "True", "DIS_PAD_JTAG": "True"})
    assert r["level"] == "bad" and r["disabled"]

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
