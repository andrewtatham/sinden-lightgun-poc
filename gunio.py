"""Sinden lightgun I/O: button/aim input and recoil, cross-platform.

Input backends
  EvdevInput   Linux. Reads every Sinden gun separately from /dev/input (so two guns on one PC
               work), and "grabs" them so the guns do not also drive the desktop mouse.
  PygameInput  Windows/macOS (or Linux without permissions). The gun is just the system mouse
               + keyboard, so only ONE gun per PC can be told apart.

Recoil
  Recoil       Talks to the gun's USB serial port using the 7-byte frame protocol
               AA <cmd> p1 p2 p3 p4 BB (protocol documented by the sindenrs project).
"""
import glob
import json
import re
import time
from pathlib import Path

SINDEN_VID = 0x16C0
SINDEN_PIDS = (0x0F01, 0x0F02, 0x0F03, 0x0F04)   # guns for player 1-4

# The ten physical controls on the gun, in the order the learn wizard asks for them.
CONTROLS = ["TRIGGER", "RELOAD", "UP", "DOWN", "LEFT", "RIGHT", "A", "B", "C", "D"]
CONTROL_HELP = {
    "TRIGGER": "the TRIGGER",
    "RELOAD": "RELOAD (pull / off-screen shot / pump button)",
    "UP": "D-PAD UP", "DOWN": "D-PAD DOWN", "LEFT": "D-PAD LEFT", "RIGHT": "D-PAD RIGHT",
    "A": "button A (1st of the 4 unlabelled buttons)", "B": "button B (2nd)",
    "C": "button C (3rd)", "D": "button D (4th)",
}
# Sinden's out-of-the-box mapping: trigger = left click, reload/off-screen = right click,
# d-pad = arrow keys. The four buttons vary with your config, so use the learn wizard (F10).
DEFAULT_MAP = {"mouseleft": "TRIGGER", "mouseright": "RELOAD",
               "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
               # front-left, rear-left, front-right, rear-right. These match the driver config in
               # ~/sinden-software (every button needs its own code - stock Sinden sends right-click
               # for BOTH the pump/reload and front-left buttons, which can't be told apart).
               "a": "A", "1": "B", "mousemiddle": "C", "5": "D"}
MAP_FILE = Path.home() / ".sinden-poc-mapping.json"

_ALIASES = {"return": "enter", "escape": "esc", "pgup": "pageup", "pgdn": "pagedown"}
_EVDEV_BTN = {"BTN_LEFT": "mouseleft", "BTN_RIGHT": "mouseright", "BTN_MIDDLE": "mousemiddle",
              "BTN_SIDE": "mouse4", "BTN_EXTRA": "mouse5"}
_PG_MOUSE = {1: "mouseleft", 2: "mousemiddle", 3: "mouseright", 6: "mouse4", 7: "mouse5"}


def canon(name):
    """Normalise a key name so evdev and pygame agree ('KEY_LEFTCTRL' == 'left ctrl')."""
    name = name.lower().replace(" ", "").replace("_", "").replace("keypad", "kp")
    return _ALIASES.get(name, name)


def load_mapping():
    try:
        return json.loads(MAP_FILE.read_text())
    except (OSError, ValueError):
        return dict(DEFAULT_MAP)


def save_mapping(mapping):
    try:
        MAP_FILE.write_text(json.dumps(mapping, indent=1))
    except OSError:
        pass


class Gun:
    """What we know about one physical gun on this PC."""

    def __init__(self, name):
        self.name = name
        self.connected = True
        self.aim = (0.5, 0.5)          # fraction of the screen, 0..1
        self.has_aim = False           # have we ever received a position from it?
        self.port_key = None           # USB port path, used to pair with its serial port


# --------------------------------------------------------------------------- input backends
class EvdevInput:
    kind = "evdev"

    def __init__(self, grab=True):
        import evdev
        from evdev import ecodes
        self.ec = ecodes
        self.guns = []
        self.denied = 0
        self._devs = []                # per gun: list of InputDevice
        self._range = []               # per gun: (xmin, xmax, ymin, ymax)
        groups = {}
        for sysdir in sorted(glob.glob("/sys/class/input/event*")):
            try:
                name = Path(sysdir, "device/name").read_text().strip()
                phys = Path(sysdir, "device/phys").read_text().strip()
            except OSError:
                continue
            if "sinden" not in name.lower():
                continue
            node = "/dev/input/" + Path(sysdir).name
            try:
                dev = evdev.InputDevice(node)
            except PermissionError:
                self.denied += 1
                continue
            except OSError:
                continue
            groups.setdefault(phys.split("/input")[0], []).append((dev, phys))
        for key in sorted(groups):
            gun = Gun("Sinden gun (%s)" % key.replace("usb-", ""))
            m = re.search(r"-(\d+(?:\.\d+)*)$", key)
            gun.port_key = m.group(1) if m else None
            rng = [0, 32767, 0, 32767]
            import os
            for dev, _ in groups[key]:
                os.set_blocking(dev.fd, False)
                caps = dev.capabilities()
                if ecodes.EV_ABS in caps:
                    info = dict(caps[ecodes.EV_ABS])
                    if ecodes.ABS_X in info and ecodes.ABS_Y in info:
                        ax, ay = info[ecodes.ABS_X], info[ecodes.ABS_Y]
                        rng = [ax.min, max(ax.max, ax.min + 1), ay.min, max(ay.max, ay.min + 1)]
                if grab:
                    try:
                        dev.grab()
                    except OSError:
                        pass
            self.guns.append(gun)
            self._devs.append([d for d, _ in groups[key]])
            self._range.append(rng)

    def describe(self):
        if self.guns:
            return "%d gun(s) via evdev" % len(self.guns)
        if self.denied:
            return "no permission to read gun devices - run ./setup-permissions.sh"
        return "no Sinden guns found"

    def _key_name(self, code):
        """Portable name for an evdev key/button code (the library may give several aliases)."""
        names = self.ec.bytype[self.ec.EV_KEY].get(code, "code%d" % code)
        names = [names] if isinstance(names, str) else list(names)
        for n in names:
            if n in _EVDEV_BTN:
                return _EVDEV_BTN[n]
        n = next((n for n in names if n.startswith("KEY_")), names[-1])
        return canon(n[4:] if n.startswith("KEY_") else n)

    def poll(self, _pg_events=None):
        """Return [(gun_index, raw_name, pressed)] for everything that happened."""
        ec, out = self.ec, []
        for gi, devs in enumerate(self._devs):
            gun, (x0, x1, y0, y1) = self.guns[gi], self._range[gi]
            for dev in devs:
                try:
                    for ev in dev.read():
                        if ev.type == ec.EV_ABS and ev.code in (ec.ABS_X, ec.ABS_Y):
                            x, y = gun.aim
                            if ev.code == ec.ABS_X:
                                x = min(1.0, max(0.0, (ev.value - x0) / (x1 - x0)))
                            else:
                                y = min(1.0, max(0.0, (ev.value - y0) / (y1 - y0)))
                            gun.aim, gun.has_aim = (x, y), True
                        elif ev.type == ec.EV_KEY and ev.value in (0, 1):
                            out.append((gi, self._key_name(ev.code), bool(ev.value)))
                except BlockingIOError:
                    pass
                except OSError:
                    gun.connected = False
        return out

    def close(self):
        for devs in self._devs:
            for d in devs:
                try:
                    d.ungrab()
                except OSError:
                    pass
                d.close()


class PygameInput:
    """One gun == the system mouse and keyboard."""
    kind = "pygame"

    def __init__(self):
        self.guns = [Gun("Gun as system mouse")]
        self.denied = 0

    def describe(self):
        return "1 gun via system mouse/keyboard"

    def poll(self, pg_events):
        import pygame
        out, gun = [], self.guns[0]
        w, h = pygame.display.get_surface().get_size()
        for e in pg_events:
            if e.type == pygame.MOUSEMOTION:
                gun.aim, gun.has_aim = (e.pos[0] / w, e.pos[1] / h), True
            elif e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and e.button in _PG_MOUSE:
                gun.aim = (e.pos[0] / w, e.pos[1] / h)
                out.append((0, _PG_MOUSE[e.button], e.type == pygame.MOUSEBUTTONDOWN))
            elif e.type in (pygame.KEYDOWN, pygame.KEYUP):
                name = canon(pygame.key.name(e.key))
                if re.fullmatch(r"f\d+", name) or name == "esc":
                    continue            # reserved for the app itself
                out.append((0, name, e.type == pygame.KEYDOWN))
        return out

    def close(self):
        pass


def make_input(kind, grab=True):
    if kind in ("auto", "evdev"):
        try:
            inp = EvdevInput(grab=grab)
            if inp.guns or kind == "evdev":
                return inp
            note = inp.describe()
            if inp.denied:
                print("NOTE:", note, "- falling back to mouse/keyboard input (1 gun).")
        except ImportError:
            pass
    return PygameInput()


# --------------------------------------------------------------------------------- recoil
def find_serial_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = [p for p in list_ports.comports() if p.vid == SINDEN_VID and p.pid in SINDEN_PIDS]
    return sorted(ports, key=lambda p: p.device)


def port_key(port):
    """USB port path of a serial port, e.g. '7.2' from location '1-7.2:1.0' (Linux only)."""
    m = re.match(r"\d+-(\d+(?:\.\d+)*)", port.location or "")
    return m.group(1) if m else None


class Recoil:
    """Fires one gun's solenoid by writing frames to its serial port."""

    def __init__(self, device):
        self.device = device
        self.ser = None
        self.error = None
        self.pulses = 0
        self.mode = None
        try:
            import serial
            self.ser = serial.Serial(device, 115200, timeout=0, write_timeout=0.5)
        except Exception as e:                       # busy, permissions, unplugged ...
            self.error = str(e).split(":")[-1].strip() or type(e).__name__

    @property
    def ok(self):
        return self.ser is not None and self.error is None

    @property
    def status(self):
        if self.ok:
            return "serial %s ok" % self.device
        return "serial %s: %s" % (self.device, self.error)

    def _send(self, cmd, *params):
        if not self.ok:
            return
        p = (list(params) + [0, 0, 0, 0])[:4]
        try:
            self.ser.write(bytes([0xAA, cmd, *[v & 0xFF for v in p], 0xBB]))
            time.sleep(0.005)            # some commands answer; keep replies out of the way
            self.ser.reset_input_buffer()
        except Exception as e:
            self.error = str(e)

    def configure(self, mode, slider=10):
        """mode: 'off' | 'software' (we fire each pulse) | 'firmware' (gun kicks on trigger).

        slider is the Sinden app's 0-25 recoil strength; the wire value is slider*10
        (about 50 = weak, 100+ = full kick on firmware 2.1).
        """
        if not self.ok:
            return
        level = max(0, min(250, int(slider) * 10))
        on_trigger = 1 if mode == "firmware" else 0
        enabled = 0 if mode == "off" else 1
        for cmd, params in [
            (162, (40, 0, 40, 13)),      # auto-recoil parameters
            (161, (1,)),                 # enable
            (167, (level,)),             # strength
            (163, (0,)),                 # single pulse per trigger pull
            (164, (on_trigger, 0, 0, 0)),  # which events kick: trigger on-screen, off, pump on/off
            (165, (0, 0, 0, 0)),         # which buttons kick
            (171, (4, 4, 4, 0)),         # timing
            (161, (enabled,)),
            (172, (level,)),             # extended strength: this is the one that really counts
            (181, (1,)),                 # allow the gun's own recoil on/off toggle
        ]:
            self._send(cmd, *params)
        time.sleep(0.05)
        self.mode = mode

    def fire(self):
        self._send(168)                  # one pulse now
        self.pulses += 1

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass


def pair_recoils(guns, serial_overrides=None, enabled=True):
    """Return one Recoil (or None) per gun, pairing by USB port path where possible."""
    if not enabled:
        return [None] * len(guns)
    if serial_overrides:
        devices = list(serial_overrides)
        by_key = {}
    else:
        ports = find_serial_ports()
        devices = [p.device for p in ports]
        by_key = {port_key(p): p.device for p in ports if port_key(p)}
    used, result = set(), []
    for g in guns:                                   # first pass: exact USB-port match
        dev = by_key.get(g.port_key) if g.port_key else None
        if dev:
            used.add(dev)
        result.append(dev)
    spare = [d for d in devices if d not in used]
    for i, dev in enumerate(result):                 # second pass: leftovers in order
        if dev is None and spare:
            result[i] = spare.pop(0)
    return [Recoil(d) if d else None for d in result]
