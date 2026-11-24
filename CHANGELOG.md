# Changelog

Newest first. Vundu_debug is a solo-developer tool and is still pre-1.0.

## V0.1.3

Fix for a bug found when unplugging and replugging the board:

- **The page no longer switches to the wrong device after a replug.** When the
  board was unplugged, the page fell back to "the first USB device in the
  list", which could be your keyboard receiver or webcam. It then showed that
  device's details, and a red "Linux is blocking the board's USB JTAG
  interface" box that was really about the receiver. The board itself was
  detected and ready the whole time; the page just was not showing it. Now it
  only ever selects an ESP32 board or adapter, and selects a board
  automatically the moment it is plugged in.
- **The USB-permission warning only applies to devices that need it** (Espressif
  USB JTAG and ESP-Prog). Unrelated USB devices can no longer trigger it.
- **Unplugging during a session stops the session cleanly.** About 3 seconds
  after the board disappears, OpenOCD and GDB are stopped and a message says
  so, so nothing is left half-alive. Plug the board back in and press **Start
  session**.
- If you click an unrelated USB device (with *Show other USB devices* ticked),
  the Board tab says it is not an ESP32 board instead of showing empty cards.
- ESP32-C61 is recognised (it was being read as a C6).

The USB rule you installed in V0.1.2 is permanent: it survives unplugging,
replugging and rebooting. You do not need to run the commands again.

Also in this version (before the first GitHub release):

- **The JTAG verdict names the route that is off.** A set `DIS_USB_JTAG` fuse now
  says "USB JTAG is off; an external adapter on the JTAG pins still works"
  instead of "JTAG is permanently disabled". Only the pad-JTAG fuses (`DIS_PAD_JTAG`,
  `HARD_DIS_JTAG`) say the pin route is off. `SOFT_DIS_JTAG` is unchanged.
- **Memory tab:** chips without a region shortcut list (S2, C5, P4, C61, H2, C2) now
  say so, instead of "Pick a chip".
- Reading the tool status no longer starts two Python processes on every refresh.
- Added `test_parsers.py`: run `python3 test_parsers.py` to check the readers for
  esptool and OpenOCD output.
- README formatting fixed for GitHub; this changelog added.

## V0.1.2

Fixes from the first real test on an ESP32-C6 board:

- **USB permission is now caught before OpenOCD starts.** On Linux the commonest
  failure is `LIBUSB_ERROR_ACCESS`: the system will not let your user open the
  board's JTAG interface. The Board tab now shows a red box with the exact
  commands and a **Copy commands** button, the device card shows a **needs USB
  rule** badge, and **Start session** takes you to the fix instead of launching
  OpenOCD to fail. The box and badge clear by themselves once it works.
- **A stronger USB rule.** It now covers every Espressif USB device, and also
  keeps ModemManager away from the board.
- **"Chip details unavailable: no serial port appeared" fixed.** Linux creates
  the serial port a moment after the USB device appears, and the first read
  could miss it and never retry. It now waits for the port, and reads the chip
  as soon as the port shows up.
- **OpenOCD problems are explained by cause, not by symptom.** The follow-on
  errors (`Unsupported DTM version: -1`, `Could not identify target type`) were
  unexplained and buried the real cause. Now one box names the root cause and
  the knock-on errors are hidden. When OpenOCD stops, a message says why.
- **Unrelated USB devices are hidden** (webcam, Bluetooth and so on). Tick *Show
  other USB devices* to see them.
- **Empty cards:** the Chip card shows cores (including the low-power core on the
  C6, C5 and P4) as soon as you pick the chip, and the Memory card says "not
  read yet" instead of being blank.
- When no session is running, console commands now say "No session is running.
  Press Start session first."
