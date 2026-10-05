# Sinden lightgun proof of concept

A button/recoil tester plus three games for Sinden lightguns: a 1-4 player Duck Hunt clone, a Point Blank style
set of quick shooting challenges, and a bonus stage where you destroy a dysfunctional printer with
machine guns and grenades.
Python + pygame, runs on Linux and Windows, two guns on one PC or guns on different PCs over the LAN.

## Run it

Linux (already set up):

    sudo ./setup-permissions.sh     # once - lets your user read the guns + recoil serial port
    ./run.sh                        # this PC only: uses every gun it finds (1 or 2)
    ./run.sh --host                 # host a LAN game (prints the address to join)

Windows 11 (the other PC):

    py -m pip install pygame-ce pyserial
    py lightgun.py --join <the Linux PC's address>

Copy `lightgun.py`, `gunio.py` and `net.py` to the Windows PC. The host picks the game;
clients just send their gun and show what the host shows. Port 5555/TCP.

## Keys (on the host)

| Key | |
|---|---|
| F1 / F2 / F3 / F4 | Tester / Duck Hunt / Printer bonus stage / Point Blank |
| - / = | thinner / thicker white tracking border |
| F5 | one recoil kick on every gun |
| F6 | ten rapid kicks |
| F7 | recoil mode: SOFTWARE (app kicks per shot), FIRMWARE (gun kicks on trigger), OFF |
| F8 / F9 | recoil strength 0-25 (same scale as the Sinden app; ~5 weak, 10+ full) |
| F10 | learn button mapping (also on clients) |
| F11 | fullscreen toggle, F12 reset counters, Esc quit |

## Games

Switch between them on the host with F2 / F3 / F4 (F1 is the tester). Clients on the LAN just see whatever the host is running.

### Printer stage (F3)

A cathartic bonus stage in the spirit of Street Fighter II's car-smashing round: destroy a
dysfunctional printer in 45 seconds. Based on real-life events.

* **Hold the trigger** for the machine gun: 10 rounds a second, with a recoil kick on every round.
* **Reload** throws a grenade at wherever you're aiming: up to 3 at a time, one regenerates every 4 seconds.
  Each blast does up to 45 damage and kicks the gun twice.
* The printer starts out reading `PC LOAD LETTER`, then `PAPER JAM`, `ERR 0x8F WHY`, `TONER LOW LOW` and
  finally `PLEASE NO`. It dents, cracks, smokes, sparks, pops its scanner lid and finally catches fire and
  explodes. Bullet holes build up as you shoot it.
* Each player adds 300 health to the printer. At the end it shows how much damage each player did.

### Point Blank (F4)

Five quick challenges in a random order, 1-4 players all shooting at once and competing for the same
targets. 8 shots a magazine, RELOAD refills. Highest total wins.

* **Number order** - shoot targets 1 to 6 in order (the next one glows).
* **Balloon pop** - 16 balloons float up; pop them before they escape.
* **Clay pigeons** - 12 plates fly across the screen in arcs.
* **Outlaws** - shoot the bandits (+150) as they pop out of the saloon windows, but shooting a townsperson costs 200.
* **Bullseye** - five targets appear one at a time: 300 for the middle ring down to 100 for the edge.

### Duck Hunt (F2)

60-second rounds, 1-4 players. The trigger shoots (6 shots per magazine), RELOAD refills it.
Recoil fires on every shot. Shooting off-screen also reloads if your Sinden config has
`OffscreenReload=1`.

## Testing the buttons

Every control lights up while held and turns green once seen, with a press counter.
The d-pad defaults to the arrow keys and trigger/reload to left/right click. The four unlabelled
buttons depend on your Sinden configuration, so press **F10** and the wizard asks for each control
in turn. The mapping is saved to `~/.sinden-poc-mapping.json`. Raw event names scroll by, so you can
see exactly what each button sends.

## Things to know

* **Aim needs the Sinden driver/software running.** The gun only reports a position after the
  Sinden software (camera tracking) has told it where it points. Without it, buttons and recoil still work,
  but the crosshair stays put ("NO AIM DATA").
* **Recoil needs the gun's serial port.** If the Sinden software has the port open (always on
  Windows) this app can't open it; the tester shows the error. Use FIRMWARE mode with the Sinden
  software's own recoil enabled in that case. When the app exits it puts the gun's recoil back to
  trigger-driven; the Sinden software re-applies its own settings next time it starts.
* **Two guns on one PC works on Linux only** (evdev tells the guns apart). On Windows the gun is just
  the system mouse, so use one gun per PC and the LAN mode.
* On Linux the guns are "grabbed" while the app runs, so they don't move the desktop mouse
  (`--no-grab` to disable). Aim is mapped to the whole screen, so run fullscreen.

## Sinden driver setup (not included - it's Sinden's software)

Download the official Linux software from <https://www.sindenlightgun.com/drivers/> and unpack it to
`~/sinden-software` (`run.sh` expects the Standard `Lightgun` folder there; install `mono` first).
`run.sh` starts the driver before the game and stops it afterwards, so the guns don't wander the
desktop pointer when you aren't playing.

The driver is what does the camera tracking (aim) and sets the guns up, including the handshake the
firmware needs before it will kick. Recoil from this app only works while the driver is running.

**Edit `LightgunMono.exe.config` so every button sends a different code.** Stock Sinden gives the
pump/reload button and the front-left button the same right-click, so no program can tell them apart:

| Setting (P1 and P2, on-screen and `Offscreen`) | Value |
|---|---|
| `ButtonFrontLeft` | `44` (keyboard `a`) |
| `ButtonRearLeft` | `9` (keyboard `1`) - P2 defaults to `10`, make it `9` too |
| `ButtonRearRight` | `13` (keyboard `5`) - P2 defaults to `14`, make it `13` too |

The tester's default mapping then matches: A = `a`, B = `1`, C = middle click, D = `5`.
Use F10 in the tester to learn anything different, or `rawdump.py` to see what each button sends.

## USB setup (read this if the second gun misbehaves)

Each Sinden gun is really a small USB hub with the gun (buttons, recoil serial port) and a
high-speed camera behind it. Two guns on one PC put two continuously streaming cameras on the bus,
and that is where most problems come from.

**Give each gun its own USB connection to the PC.**

* Each camera asks the USB 2 bus for a fixed, large slice of isochronous bandwidth (it always uses the
  same setting, whatever the resolution). One USB 2 port can only reserve about 80% of 480 Mbit/s, and
  two of these cameras together are just over that. Lowering `CameraRes` in the driver config does not help.
* The symptom when both guns share one hub: the kernel logs `Not enough bandwidth for altsetting 3`, the
  driver prints `VIDIOC_STREAMON error 28, No space left on device`, then crashes with a SIGSEGV.
* So: **never plug both guns into the same hub** (a monitor's USB hub, a 4-port hub, a front-panel header that
  uses an internal hub). Plug them into separate root ports, for example one into the back of the PC
  and one into a powered hub on another port. Any two ports that are not behind the same hub will do.
  USB 3 (blue) vs USB 2 doesn't matter: the guns are USB 2 devices either way.
* One gun on a hub is fine, as is a gun on a rear motherboard port. Front-panel ports go through
  longer internal cabling and are the least reliable.

**Power and cables.**

* A hub or port that is marginal on power makes a gun's whole internal hub drop off the bus and come back,
  every few seconds (kernel log: `USB disconnect` then `new high-speed USB device`, often with
  `clear tt ... error -110`). Each time it comes back as a new `/dev/ttyACM*`. Use a short, good data cable,
  and a **powered** hub if you need one.
* After repeated resets a gun's firmware can hang: its serial port then times out on every write
  (`write timeout`) and the driver can't start. Unplug the gun's USB cable, wait about ten seconds
  and plug it back in. A software re-enumerate (`authorized` toggle) does the same thing.
* Turn off Linux USB autosuspend while testing, as it can drop devices that look idle:
  `echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend`
  (make it permanent with `usbcore.autosuspend=-1` on the kernel command line).
* A wedged mouse or other device after unplugging and replugging guns is usually cured by replugging
  that device.

**Watching what the bus is doing.**

    lsusb -t                                        # who is behind which hub and root port
    journalctl -k -f | grep -E "usb [0-9]-|bandwidth"   # disconnects, resets, bandwidth errors
    tail ~/sinden-software/driver.log                # what the Sinden driver complains about

A quick check that both cameras can stream together, without the driver (substitute your
`/dev/video*` nodes from `v4l2-ctl --list-devices`; both should run at about 60 fps with no error):

    v4l2-ctl -d /dev/video0 --set-fmt-video=width=640,height=480,pixelformat=MJPG --set-parm=60 \
        --stream-mmap --stream-count=600 --stream-to=/dev/null &
    v4l2-ctl -d /dev/video4 --set-fmt-video=width=640,height=480,pixelformat=MJPG --set-parm=60 \
        --stream-mmap --stream-count=600 --stream-to=/dev/null

**Which gun is which colour.** Player slots (and so colours in the app) follow the gun's USB product id,
not the port it is plugged into: `0f02` is player 1 (red) and `0f01` is player 2 (blue). A lone gun keeps
its own colour. Both ids appear in `lsusb` as `16c0:0f01` / `16c0:0f02`.

## Files

* `lightgun.py` - the app (tester, Duck Hunt, printer stage, LAN host/client)
* `gunio.py` - gun input (evdev / pygame) and the recoil serial protocol
* `net.py` - LAN transport
* `rawdump.py` - print raw button events from every gun
* `setup-permissions.sh` - Linux udev rule so your user can read the guns and use their serial ports

Licensed under the MIT licence (see `LICENSE`). Not affiliated with Sinden Technology; the Sinden driver
and firmware protocol credits go to Sinden and to the open-source `sindenrs` project, whose protocol notes
were used for the recoil commands.
