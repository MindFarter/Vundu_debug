# Vundu_debug V0.1.3

A JTAG debugger for ESP32-family boards, in your browser, on your own computer.
You plug a board in with a USB cable and it shows you the chip, flash, security
and whether debugging can work. Then you can halt the chip, step through code,
read registers and memory, set breakpoints and watchpoints, and run GDB.

It is a **separate program** from Vundu_lab, with its own folder, its own server
and its own tools. The two take turns with the board: a button in each one
hands the board over to the other. They never use it at the same time.

Vundu_debug contains no debugger of its own. It drives Espressif's **OpenOCD**
and **GDB**, and uses **esptool** to read the chip. All credit for the real work
belongs to those projects (see *Credits and licences*).

- - -
## What's new

See [CHANGELOG.md](CHANGELOG.md).

- - -
## 1. Before you start

You need:

- A computer running Linux, macOS or Windows with **Python 3.9 or newer**. On
  Ubuntu also run once: `sudo apt install python3-venv`
- A board that can be debugged (see *Which boards work* below)
- A USB **data** cable (a charge-only cable shows nothing)
- Internet, once, to download the tools

## 2. Install and start

```
git clone https://github.com/MindFarter/Vundu_debug.git
cd Vundu_debug
python3 vundu_debug.py
```
It opens in your browser, normally at `http://127.0.0.1:8790` (the next free
port up to 8809 if that one is busy; the address is printed in the terminal).
Press `Ctrl+C` in the terminal to stop it. On start-up it prints a short check
of what it needs; anything missing comes with the fix. A missing item never
stops it from starting.

## 3. One-off setup (about 2 minutes)

1.  Open the **Setup** tab.
2.  Press **Install everything missing**. It downloads OpenOCD, GDB for Xtensa
    chips and GDB for RISC-V chips (about 90 MB), checks each download's
    checksum, and puts them in `data/engines/debug/tools/`. It also installs `
    esptool` and `pyserial` in `data/engines/debug/venv`. Wait for "Done." in
    the log.
  - If you already have ESP-IDF installed, its tools in `~/.espressif` are found
    and used automatically.
3.  **Linux only, once:** allow your user to open the board's USB JTAG interface.
    Plug the board in; if the **Board** tab shows a red box *Linux is blocking
    the board's USB JTAG interface*, press **Copy commands**, paste them into a
    terminal and run them (they ask for your password). The same commands are
    in the *USB permission rule* box in Setup. Then **unplug and replug the board**
    . The red box and the **needs USB rule** badge disappear when it works.
    Without this, OpenOCD cannot open the board's JTAG interface.
4.  **Linux only:** if the start-up check says you are not in the `dialout`
    group, run `sudo usermod -aG dialout $USER`, then **log out and back in**.

## 4. How to debug a board, step by step

### Step 1. Put a debuggable program on the board (in Vundu_lab)

Debugging shows you your own code, so the board needs your program flashed,
built **with debug information**:

- PlatformIO: add `build_type = debug` to your `platformio.ini` (this also turns
  optimisation down, so variables are not optimised away).
- Compile and Flash it with Vundu_lab as usual.
- Note where the **.elf** file is. It is the file GDB needs. After a Vundu_lab
  compile it is in the project folder:
  - PlatformIO / pioarduino: `.pio/build/<environment>/firmware.elf`
  - Arduino engine: `.cw_build/<sketch>.ino.elf`
- The ELF must come from **exactly the build that is flashed**. A stale ELF is
  the number one reason breakpoints never hit.

### Step 2. Open Vundu_debug and plug in the board

Open Vundu_debug (from Vundu_lab's **Vundu_debug** button, or run `python3
vundu_debug.py`). Plug the board in with USB. Within a few seconds the **Board**
tab fills in:

- the chip, revision, features, crystal and MAC
- flash size and maker, and PSRAM
- security flags and JTAG-related eFuses (read-only)
- the USB details, and whether you have permission to use the port
- a coloured **verdict**: green means JTAG is available; amber means check the
  note; red means JTAG is blocked.

Read the verdict. It tells you in plain words if you are on the right USB port,
if the firmware has taken over the USB pins, or if you need an external adapter.

(Reading the board resets it briefly. You can switch that off with *Read board
details when plugged in*, and press **Read again** any time.)

### Step 3. Start a debug session

In the left sidebar, under **Debug session**:

1.  **Chip**: filled in for you from the board. Change it only if the chip was
    not read.
2.  **Debug interface**: leave it on *Built-in USB JTAG* for S3, C3, C5, C6, H2
    and P4 boards. Choose *ESP-Prog / FT2232H* or *J-Link* if you use an
    external adapter (required for the original ESP32, S2 and C2).
3.  Leave the tick boxes as they are to begin with. *FreeRTOS task awareness*
    lets GDB list your tasks. Untick *Flash access* only if OpenOCD reports a
    flash error.
4.  Press **Start session**. The OpenOCD tab opens and shows the log. The session
    is live when the **OpenOCD** lamp in the sidebar turns green and the log says
    it is listening for GDB.

If something goes wrong, the OpenOCD tab shows a yellow box in plain words with
the fix (for example the USB rule, a port already in use, or a chip that does
not answer).

### Step 4. Control the chip (Control tab)

- **Halt** stops the chip where it is. **Resume** lets it run. **Step** runs one
  instruction.
- **Reset and halt** restarts the chip and stops it at its very first
  instruction. Use this to debug start-up code.
- **Reset and run** restarts it normally.
- Dual-core chips show both cores. Press **Select** to choose which core the
  buttons and the other tabs act on.
- While halted, the program counter (where the chip stopped) is shown.

### Step 5. Look inside

- **Registers**: halt first, then **Read registers**. Values that changed since
  the last read are highlighted. Tick *Refresh while halted* to watch them as
  you step.
- **Memory**: type an address (or click a region name such as *DRAM* or *Flash
  code*), choose how many bytes, press **Read**. Tick *Live* to refresh every
  second; changed bytes are highlighted. Writing a word is available too;
  writing to the wrong place can crash the chip.
- **Breakpoints**: add a hardware breakpoint at an address (the chip stops when
  it reaches it) or a watchpoint on a variable's address (the chip stops when
  that memory is read or written). The chip has only a few of each; OpenOCD
  tells you when they run out.

### Step 6. Debug with your source code (GDB tab)

1.  Enter the full path to your **.elf** file, or press **Upload ELF...**.
2.  Press **Start GDB**. (The right GDB for your chip is chosen automatically.)
3.  Press **Attach to target**. This connects GDB to OpenOCD and halts the chip.
4.  Type commands in the box and press Enter, or use the buttons. Useful ones:

|You want to                            |Type                                                       |
|---------------------------------------|-----------------------------------------------------------|
|Stop when a function starts            |`break app_main` (or `break setup`, `break loop` in Arduino code)|
|Run until a breakpoint                 |`continue`                                                 |
|Run one line (over calls / into calls) |`next` / `step`                                            |
|See where you are and how you got there|`bt`                                                       |
|See all FreeRTOS tasks and their stacks|`info threads` and `thread apply all bt`                   |
|See a variable                         |`print myVariable`                                         |
|Restart from the top                   |`monitor reset halt`                                       |


5.  When finished, press **Stop GDB**, then **Stop** in the sidebar.

### Step 7. Go back to Vundu_lab

Press the **Vundu_lab** button (top right). It stops OpenOCD and GDB, lets go of
the board and opens Vundu_lab. Now you can Compile, Flash and Monitor as normal.

- - -
## 5. Vundu_lab and Vundu_debug take turns

The two programs are independent: each has its own folder, server and tools,
and either can be used without the other. They are connected only by a
hand-over button in each one, because both need the board's USB port and only
one program may hold it.

- **Vundu_debug starts paused.** It touches no hardware until its page is opened.
  Opening the page resumes it.
- **The Vundu_lab button** in Vundu_debug stops any session, puts the debugger
  back to *paused*, finds the running Vundu_lab (it looks on ports 8765 to 8784)
  and opens it. If Vundu_lab is not running, it shows the command to start it.
- **A paused page always has working buttons:** *Open Vundu_lab* and *Resume
  debugger*. Nothing is ever dead.
- **If you close the debugger's browser tab** while a session is running, the
  debugger stops the session and pauses itself after about 30 seconds, so the
  board is never left held.
- While paused, the server refuses every hardware action.

### The Vundu_debug button in Vundu_lab

Vundu_lab needs one small addition: a button that pauses itself and opens the
debugger. Add this to Vundu_lab's `index.html` (it uses the page's own `j()`
helper and original `_f` fetch):

In the header, next to the tabs:

```html
<button class="btn" id="goDebug">Vundu_debug</button>
```
In the script:

```js
$("#goDebug").onclick=async()=>{
  fetch("/api/stop");                              // stop a compile that is running
  await j("/api/flash/cancel");                    // stop a flash that is running
  await j("/api/serial/hold",{on:true});           // close the Monitor's port and stop auto-reconnect
  let port=null;
  for(let p=8790;p<=8809&&!port;p++){              // find the running Vundu_debug
    try{const s=await(await _f("http://127.0.0.1:"+p+"/api/public_state")).json();if(s.app==="vundu_debug")port=p}catch(e){}
  }
  if(port)location.href="http://127.0.0.1:"+port+"/";
  else{await j("/api/serial/hold",{on:false});alert("Vundu_debug is not running. Start it in a terminal:\n\npython3 vundu_debug.py")}
};
j("/api/serial/hold",{on:false});                  // on page load: give the port back after returning from the debugger
```
`GET http://127.0.0.1:<port>/api/public_state` returns `{app, version,
debugging, paused}`. It is read-only and only answers pages served from `
127.0.0.1` or `localhost`.

- - -
## 6. Which boards work


|Chip                        |How you debug                                                            |OpenOCD config used                                                   |
|----------------------------|-------------------------------------------------------------------------|----------------------------------------------------------------------|
|ESP32-S3, C3, C6, C5, H2, P4|**Built-in USB JTAG**: just the board's native USB port                  |`board/<chip>-builtin.cfg`                                           |
|ESP32 (original), S2, C2    |**External adapter only**: ESP-Prog, FT2232H or J-Link wired to the JTAG pins|`interface/ftdi/esp32_devkitj_v1.cfg` (or `jlink.cfg`) + `target/<chip>.cfg`|

This covers the ESP32-family boards on LCD Wizard's list, because the chip, not
the brand, decides the route:

- **S3, C3, C6, C5, P4 boards** (Sunton 8048S, WT32-SC01 Plus, LilyGO
  T-Display-S3, Guition JC3248 / JC4827 / 4848, LCDWIKI ES3C boards, Waveshare
  S3 / C6 / C5 / P4 boards, Elecrow CrowPanel Advanced, VIEWE, M5Stack Tab5,
  RockBase NM-CYD-C5): built-in USB JTAG, if you use the **native USB** socket.
- **Original ESP32 boards** (Cheap Yellow Displays, WT32-SC01, M5Stack Core2,
  Guition JC2432, LCDWIKI E32 boards, Waveshare ESP32-Touch-LCD-3.5): need an
  external adapter on GPIO 12 to 15. On many of these boards those pins are
  shared with the display, touch or SD card, so check your board's pinout
  first. GPIO12 is also a boot-strapping pin.
- **A USB port that goes through a CH340, CP210x or similar chip carries no JTAG.**
  Boards with two USB sockets usually have one UART socket and one native
  socket; use the native one. The Board tab says "UART only" when you are on
  the wrong one.

External adapter wiring: connect the adapter's TMS, TDI, TCK, TDO and GND to
the chip's JTAG pins (the **JTAG wiring** card on the Board tab lists them for
the chips where they are fixed), and power the board separately.

- - -
## 7. Safety

- The server listens only on `127.0.0.1`, checks the Host header, needs a random
  per-run token on every request, and refuses requests from other websites.
  OpenOCD is started bound to `127.0.0.1` as well.
- Reading a board with esptool resets it, so reading is refused while a session
  is running or while paused.
- eFuses are only ever **read**. Nothing here can burn a fuse. (A set `DIS_*JTAG`
  fuse permanently disables JTAG; the Board tab tells you if one is set.)
- Halting a chip stops its Wi-Fi, timers and watchdogs. Expect resets and
  disconnects when you resume after a long halt.

- - -
## 8. Troubleshooting


|Problem                                                                                                              |Fix                                                                                                                                                                                                                                                     |
|---------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|**Setup > Install failed**                                                                                           |Check your internet connection and press the install button again (it is safe to repeat). If your network blocks GitHub, set `IDF_GITHUB_ASSETS=dl.espressif.com/github_assets` before starting, or install ESP-IDF and its tools.                      |
|`LIBUSB_ERROR_ACCESS` (then `could not find or open device`, `Unsupported DTM version: -1`, `Could not identify target type`)|Linux is blocking USB access. Run the commands in the red box on the Board tab (or Setup), then **unplug and replug the board**. The other two errors are only knock-on effects. On Windows bind the WinUSB driver to the board's JTAG interface with Zadig.|
|`could not find or open device` / `LIBUSB_ERROR_NOT_FOUND` on its own                                                |Use the board's **native** USB socket and a short data cable, no hub. If the firmware is using the USB port itself, hold BOOT and tap RESET.                                                                                                            |
|The board you plugged in is not listed                                                                               |Only ESP32 boards and adapters are shown. Tick *Show other USB devices* to see everything.                                                                                                                                                              |
|After unplugging and replugging, the page shows another device or a red USB box                                      |Fixed in V0.1.3. The board is selected automatically when plugged in. If you have several boards, click the one you want in the list on the left.                                                                                                       |
|"The board was unplugged, so the debug session was stopped"                                                          |The board disappeared from USB for more than 3 seconds (cable, port or a firmware crash). Plug it back in and press **Start session**.                                                                                                                  |
|Board tab says **UART only**                                                                                         |You are on the USB-to-serial socket. Use the board's other USB socket, or an external adapter.                                                                                                                                                          |
|Board tab says **no JTAG now**                                                                                       |The running firmware is using the USB pins itself (for example USB CDC or TinyUSB). Hold BOOT, tap RESET, or flash firmware that leaves USB Serial/JTAG alone.                                                                                          |
|Board details say **no permission**                                                                                  |`sudo usermod -aG dialout $USER`, then log out and back in.                                                                                                                                                                                             |
|Port 3333, 4444 or 6666 already in use                                                                               |Another OpenOCD is running: `pkill openocd`, then **Start session** again.                                                                                                                                                                              |
|Session never turns green                                                                                            |Read the OpenOCD log. The chip may not be answering on JTAG (wiring, fuse, firmware), or OpenOCD is too old for the chip (press **OpenOCD** in Setup to reinstall the latest).                                                                          |
|GDB connects but **breakpoints never hit**                                                                           |Wrong or stale ELF. Rebuild, reflash with Vundu_lab, and point GDB at the new `.elf`.                                                                                                                                                                   |
|Variables show `<optimised out>`                                                                                    |Build with `build_type = debug` (PlatformIO).                                                                                                                                                                                                           |
|"Target not halted"                                                                                                  |Press **Halt** (or **Reset and halt**) first.                                                                                                                                                                                                           |
|Page says **Vundu_debug is paused**                                                                                  |Vundu_lab has the board. Press **Open Vundu_lab** to go there, or **Resume debugger** to take the board back.                                                                                                                                           |
|Vundu_lab button says Vundu_lab is not running                                                                       |Start it in a terminal: `python3 vundu_lab.py` (the page shows the exact command), then press **Open Vundu_lab**.                                                                                                                                       |
|Messages like `zygote_main_linux.cc ... Broken pipe` in the terminal                                                 |These come from the web browser itself (Chromium), not from Vundu_debug, and are harmless. This version starts the browser quietly so they no longer appear.                                                                                            |

- - -
## 9. What has and has not been tested


|What                                                                                      |How                                                              |Result                                                                                                                                                                                                                                 |
|------------------------------------------------------------------------------------------|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|Installing OpenOCD and both GDBs                                                          |Real downloads from Espressif's official list, checksums verified|Worked; all three start and report their versions                                                                                                                                                                                      |
|Real OpenOCD start with no board attached                                                 |Real OpenOCD with the S3 built-in config                         |Failed cleanly with its normal "could not find or open device" message, which Vundu_debug explains                                                                                                                                     |
|The server, session handling, pause and hand-over, page-closed watchdog                   |Against a stand-in that imitates OpenOCD's command port          |Worked                                                                                                                                                                                                                                 |
|Register, memory, breakpoint and core-state parsing                                       |Same stand-in                                                    |Worked                                                                                                                                                                                                                                 |
|Board detection on a real ESP32-C6 board (reported by the author)                         |Waveshare ESP32-C6 1.47 inch Touch-LCD                           |Board recognised as built-in USB JTAG. OpenOCD start was blocked by the missing Linux USB permission; V0.1.2 catches that and shows the fix, and a visual check of the new screens was done in a real browser against a simulated board|
|**A full debug session on a real board after the permission fix, GDB attached to a real chip**|Not yet confirmed                                                |**Untested.** Expect small fixes on the next real run.                                                                                                                                                                                 |

- - -
## 10. Where everything lives

```
Vundu_debug_V0.1.3/
  vundu_debug.py     the local server (Python standard library only)
  index.html         the whole interface (no outside libraries or network calls)
  vundu_logo.png     the Vundu logo
  LICENSE            MIT licence
  README.md          this file
  CHANGELOG.md       what changed in each version
  test_parsers.py    checks the readers for esptool and OpenOCD output (python3 test_parsers.py)
  data/engines/debug/
    venv/            esptool and pyserial (created by Setup)
    tools/           OpenOCD and GDB (created by Setup)
  data/uploads/      ELF files you upload on the GDB tab
```
Nothing is installed anywhere else. To remove everything, delete the folder.

- - -
## Credits and licences

**Vundu_debug is a convenience front-end. It is not a fork or rebranding of any
project below, and it does not include or redistribute any of them.** On first
use it downloads each one from Espressif's official sources onto your own
computer and then runs it.

- **OpenOCD** (Espressif's fork), the on-chip debugger: 
  <https://github.com/espressif/openocd-esp32> (GPL-2.0)
- **GDB** (Espressif builds), the source-level debugger: 
  <https://github.com/espressif/binutils-gdb> (GPL-3.0)
- **esptool**, which reads the chip: <https://github.com/espressif/esptool>
  (GPL-2.0 or later)
- **pyserial**, serial port access: <https://github.com/pyserial/pyserial>
  (BSD-3-Clause)

Each of these projects remains under its own licence, which applies to
everything Vundu_debug downloads and runs. Please star them and sponsor them
where you can. The debugging approach follows the public guide at
prototypeguru.com and Espressif's JTAG debugging documentation.

Vundu_debug is an independent open-source project by 
[MindFarter](https://github.com/MindFarter). It is not affiliated with,
endorsed by, or supported by Espressif, LCD Wizard or the maintainers of any
project named here.

## Licence (MIT)

Copyright (c) 2026 MindFarter (<https://github.com/MindFarter>). Released under
the MIT licence; the full text is in the `LICENSE` file. The licence covers
Vundu_debug's own code (`vundu_debug.py` and `index.html`) and documentation.
The tools it downloads keep their own licences.

