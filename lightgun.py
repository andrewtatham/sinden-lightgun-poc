#!/usr/bin/env python3
"""Sinden lightgun proof of concept: button/recoil tester + 1-4 player Duck Hunt, over a LAN.

  python lightgun.py                 this PC only (every gun it can see)
  python lightgun.py --host          this PC is the host; other PCs join with --join <ip>
  python lightgun.py --join <ip>     send this PC's gun to a host, show what the host shows

The host runs the game. Clients send their gun's aim + buttons to the host and fire their
own recoil when the host says so.
"""
import argparse
import math
import random
import socket
import sys
import time

import pygame

import gunio
from gunio import CONTROLS, CONTROL_HELP

W, H = 1280, 720
MAXP = 4
COLORS = [(255, 90, 80), (80, 170, 255), (120, 220, 90), (255, 210, 70)]
RMODES = ["software", "firmware", "off"]
RMODE_HELP = {
    "software": "SOFTWARE - the app kicks the gun on every shot (works over the LAN)",
    "firmware": "FIRMWARE - the gun kicks by itself on trigger pull",
    "off": "OFF - no recoil",
}


# ------------------------------------------------------------------------------ host state
class Hub:
    """Everything the host knows about the players, local and remote."""

    def __init__(self, n_local):
        self.n_local = n_local
        self.slots = [{"c": False, "aim": [0.5, 0.5], "has": False, "name": "", "rc": "",
                       "held": set(), "ev": []} for _ in range(MAXP)]
        for i in range(min(n_local, MAXP)):
            self.slots[i]["c"] = True
        self.remote = {}                    # connection id -> slot
        self.rseq = [0] * MAXP              # bumped each time a gun should kick
        self.rmode = "software"
        self.rstr = 10                      # 0-25, same scale as the Sinden app

    def recoil(self, slot):
        self.rseq[slot] += 1

    def add_remote(self, cid, name):
        for i in range(self.n_local, MAXP):
            if not self.slots[i]["c"]:
                self.remote[cid] = i
                self.slots[i].update(c=True, name=name, held=set(), ev=[])
                return i
        return None

    def remove_remote(self, cid):
        i = self.remote.pop(cid, None)
        if i is not None:
            self.slots[i].update(c=False, held=set(), ev=[])

    def push(self, slot, events):
        sl = self.slots[slot]
        for raw, logical, pressed in events:
            sl["ev"].append((raw, logical, pressed))
            if logical:
                (sl["held"].add if pressed else sl["held"].discard)(logical)

    def end_frame(self):
        for sl in self.slots:
            sl["ev"] = []

    def players(self):
        return [{"c": s["c"], "aim": s["aim"], "has": s["has"], "name": s["name"], "rc": s["rc"]}
                for s in self.slots]


# ---------------------------------------------------------------------------------- scenes
class Tester:
    """Shows every control lighting up, counts presses, and exercises the recoil."""
    name = "test"

    def __init__(self):
        self.log = []
        self.burst, self.burst_t = 0, 0.0
        self.reset()

    def reset(self):
        self.s = [{"cnt": {c: 0 for c in CONTROLS}, "held": [], "raw": "-", "shots": 0}
                  for _ in range(MAXP)]
        self.log = []

    def update(self, dt, hub):
        for i, sl in enumerate(hub.slots):
            st = self.s[i]
            for raw, logical, pressed in sl["ev"]:
                if pressed:
                    st["raw"] = raw
                    if logical:
                        st["cnt"][logical] += 1
                self.log.append("P%d  %-10s %-5s %s" % (i + 1, raw, "DOWN" if pressed else "up",
                                                       ("= " + logical) if logical else "(not mapped - press F10)"))
                if logical == "TRIGGER" and pressed and hub.rmode == "software":
                    hub.recoil(i)
                    st["shots"] += 1
            st["held"] = sorted(sl["held"])
        self.log = self.log[-9:]
        if self.burst > 0:
            self.burst_t -= dt
            if self.burst_t <= 0:
                self.burst -= 1
                self.burst_t = 0.12
                for i, sl in enumerate(hub.slots):
                    if sl["c"]:
                        hub.recoil(i)

    def key(self, k, hub):
        if k == pygame.K_F5:                             # one kick on every gun
            for i, sl in enumerate(hub.slots):
                if sl["c"]:
                    hub.recoil(i)
        elif k == pygame.K_F6:                           # ten kicks in a row
            self.burst, self.burst_t = 10, 0.0
        elif k == pygame.K_F12:
            self.reset()

    def snapshot(self):
        return {"name": "test", "s": self.s, "log": self.log}


class Duck:
    """Shoot the ducks. Trigger = fire, RELOAD (or an off-screen shot) = reload."""
    name = "duck"
    MAG, ROUND, RADIUS = 6, 60.0, 52

    def __init__(self):
        self.ducks, self.fx, self.next_id = [], [], 1
        self.t = 0.0
        self.players = [self._fresh() for _ in range(MAXP)]
        self.left, self.phase, self.over_t = self.ROUND, "play", 0.0

    def _fresh(self):
        return {"score": 0, "ammo": self.MAG, "hits": 0, "shots": 0, "msg": ""}

    def _spawn(self, speed):
        d = random.choice((-1, 1))
        self.ducks.append({"id": self.next_id, "x": -50.0 if d > 0 else W + 50.0,
                           "y": random.uniform(110, 400), "d": d, "st": "fly",
                           "vx": d * random.uniform(150, 230) * speed,
                           "vy": random.uniform(-40, 40), "ph": random.random() * 6})
        self.next_id += 1

    def _shoot(self, i, aim, hub):
        p = self.players[i]
        if p["ammo"] <= 0:
            p["msg"] = "RELOAD!"
            return
        p["ammo"] -= 1
        p["shots"] += 1
        hub.recoil(i)
        x, y = aim[0] * W, aim[1] * H
        best, bd = None, self.RADIUS
        for d in self.ducks:
            dist = math.hypot(d["x"] - x, d["y"] - y)
            if d["st"] == "fly" and dist < bd:
                best, bd = d, dist
        if best:
            best["st"], best["vy"] = "fall", -120.0
            p["score"] += 100
            p["hits"] += 1
            p["msg"] = "+100"
        else:
            p["msg"] = ""
        self.fx.append([x, y, best is not None, i, 0.0])

    def update(self, dt, hub):
        self.t += dt
        for i, sl in enumerate(hub.slots):
            if not sl["c"]:
                self.players[i] = self._fresh() if self.players[i]["shots"] else self.players[i]
                continue
            for raw, logical, pressed in sl["ev"]:
                if not pressed or self.phase != "play":
                    continue
                if logical == "TRIGGER":
                    self._shoot(i, sl["aim"], hub)
                elif logical == "RELOAD":
                    self.players[i]["ammo"] = self.MAG
                    self.players[i]["msg"] = "RELOADED"
        if self.phase == "play":
            self.left -= dt
            active = sum(1 for s in hub.slots if s["c"])
            speed = 1.0 + 0.4 * (1 - self.left / self.ROUND)
            if sum(1 for d in self.ducks if d["st"] == "fly") < 2 + active:
                self._spawn(speed)
            if self.left <= 0:
                self.phase, self.over_t = "over", 8.0
        else:
            self.over_t -= dt
            if self.over_t <= 0:
                self.__init__()
                return
        for d in self.ducks:
            d["ph"] += dt * (10 if d["st"] == "fly" else 0)
            if d["st"] == "fly":
                d["x"] += d["vx"] * dt
                d["y"] += d["vy"] * dt
                if d["y"] < 70 or d["y"] > 430:
                    d["vy"] = -d["vy"]
                d["vy"] += math.sin(self.t * 2 + d["id"]) * 60 * dt
            else:
                d["vy"] += 700 * dt
                d["y"] += d["vy"] * dt
        self.ducks = [d for d in self.ducks if -120 < d["x"] < W + 120 and d["y"] < H - 90]
        for f in self.fx:
            f[4] += dt
        self.fx = [f for f in self.fx if f[4] < 0.6]

    def key(self, k, hub):
        pass

    def snapshot(self):
        return {"name": "duck", "phase": self.phase, "left": round(self.left, 1), "p": self.players,
                "ducks": [[d["id"], round(d["x"]), round(d["y"]), d["d"], d["st"], round(d["ph"], 2)]
                          for d in self.ducks],
                "fx": [[round(f[0]), round(f[1]), f[2], f[3], round(f[4], 2)] for f in self.fx]}


class Printer:
    """Bonus stage: destroy the printer. Hold TRIGGER = machine gun, RELOAD = grenade."""
    name = "printer"
    CX, CY, PW, PH = W // 2, 395, 440, 270
    ROUND, RATE, HP_EACH, GREN_DMG, GREN_MAX = 45.0, 0.1, 300.0, 45.0, 3

    def __init__(self):
        self.t, self.phase, self.left, self.over_t = 0.0, "play", self.ROUND, 0.0
        self.players = [self._fresh() for _ in range(MAXP)]
        self.hp = self.maxhp = None
        self.marks, self.parts, self.booms = [], [], []
        self.shake, self.cool, self.kick = 0.0, [0.0] * MAXP, [0.0] * MAXP
        self.paper_t = self.smoke_t = self.spark_t = 0.0
        self.last_stage = 0

    def _fresh(self):
        return {"dmg": 0.0, "gren": self.GREN_MAX, "gt": 0.0, "msg": ""}

    # particles: [kind, x, y, vx, vy, life, max_life, rot, spin, size]  kinds: 0 spark 1 debris 2 paper 3 smoke 4 fire 5 dust
    def _add(self, kind, x, y, vx, vy, life, size, spin=0.0):
        self.parts.append([kind, x, y, vx, vy, life, life, random.uniform(0, 6.28), spin, size])
        if len(self.parts) > 170:
            del self.parts[:len(self.parts) - 170]

    def _burst(self, x, y, sparks=0, debris=0, fire=0, speed=380):
        for _ in range(sparks):
            a = random.uniform(0, 6.28)
            v = random.uniform(0.3, 1) * speed
            self._add(0, x, y, math.cos(a) * v, math.sin(a) * v - 80, random.uniform(0.25, 0.6), random.uniform(2, 4))
        for _ in range(debris):
            a = random.uniform(-3.4, 0.25)
            v = random.uniform(0.3, 1) * speed
            self._add(1, x, y, math.cos(a) * v, math.sin(a) * v, random.uniform(1.0, 2.0),
                      random.uniform(8, 24), random.uniform(-9, 9))
        for _ in range(fire):
            self._add(4, x + random.uniform(-30, 30), y + random.uniform(-20, 20), random.uniform(-40, 40),
                      random.uniform(-160, -60), random.uniform(0.4, 0.9), random.uniform(18, 40))

    def _inside(self, x, y, margin=0):
        return abs(x - self.CX) <= self.PW / 2 + margin and abs(y - self.CY) <= self.PH / 2 + margin

    def _damage(self, i, amount):
        amount = min(amount, self.hp)
        self.hp -= amount
        self.players[i]["dmg"] += amount
        stage = int((1 - self.hp / self.maxhp) * 10)
        if stage > self.last_stage:                    # a lump falls off every 10% of damage
            self.last_stage = stage
            self._burst(self.CX + random.uniform(-150, 150), self.CY + random.uniform(-90, 60), debris=4, sparks=8)

    def _fire(self, i, aim, hub):
        hub.recoil(i)
        x, y = aim[0] * W + random.uniform(-9, 9), aim[1] * H + random.uniform(-9, 9)
        if self._inside(x, y, 6):
            self._damage(i, 1.0)
            self.shake = min(7.0, self.shake + 1.6)
            self.marks.append([round(x - self.CX), round(y - self.CY)])
            del self.marks[:-90]
            self._burst(x, y, sparks=3)
            if random.random() < 0.04:
                self._add(2, x, y, random.uniform(-200, 200), random.uniform(-300, -80), 2.0, 22, random.uniform(-6, 6))
            self.players[i]["msg"] = ""
        else:
            self._add(5, x, y, 0, -20, 0.3, 8)

    def _grenade(self, i, aim, hub):
        p = self.players[i]
        if p["gren"] <= 0:
            p["msg"] = "NO GRENADES"
            return
        p["gren"] -= 1
        hub.recoil(i)
        self.kick[i] = 0.12                             # a second, heavier-feeling kick
        x, y = aim[0] * W, aim[1] * H
        dx = max(abs(x - self.CX) - self.PW / 2, 0)
        dy = max(abs(y - self.CY) - self.PH / 2, 0)
        d = math.hypot(dx, dy)
        self.booms.append([round(x), round(y), 0.0])
        self._burst(x, y, sparks=22, debris=6, fire=6, speed=520)
        self.shake = 15.0
        if d < 140:
            self._damage(i, max(5.0, self.GREN_DMG * (1 - d / 140)))
            p["msg"] = "BOOM!"
        else:
            p["msg"] = "MISSED"

    def update(self, dt, hub):
        self.t += dt
        active = [i for i, s in enumerate(hub.slots) if s["c"]]
        if self.hp is None:
            self.maxhp = self.HP_EACH * max(1, len(active))
            self.hp = self.maxhp
        playing = self.phase == "play"
        for i in range(MAXP):
            if not hub.slots[i]["c"]:
                continue
            sl, p = hub.slots[i], self.players[i]
            if p["gren"] < self.GREN_MAX:
                p["gt"] += dt
                if p["gt"] >= 4.0:
                    p["gt"], p["gren"] = 0.0, p["gren"] + 1
            if self.kick[i] > 0:
                self.kick[i] -= dt
                if self.kick[i] <= 0:
                    hub.recoil(i)
            self.cool[i] -= dt
            if not playing:
                continue
            for raw, logical, pressed in sl["ev"]:
                if pressed and logical == "TRIGGER" and self.cool[i] <= 0:
                    self._fire(i, sl["aim"], hub)
                    self.cool[i] = self.RATE
                elif pressed and logical == "RELOAD":
                    self._grenade(i, sl["aim"], hub)
            if "TRIGGER" in sl["held"] and self.cool[i] <= 0:
                self._fire(i, sl["aim"], hub)
                self.cool[i] = self.RATE + max(self.cool[i], -0.05)
        frac = self.hp / self.maxhp
        if playing:
            self.left -= dt
            if self.hp <= 0:
                self.phase, self.over_t = "win", 9.0
                self.booms.append([self.CX, self.CY, 0.0])
                self._burst(self.CX, self.CY, sparks=60, debris=40, fire=30, speed=700)
                self.shake = 24.0
            elif self.left <= 0:
                self.phase, self.over_t = "lose", 8.0
        else:
            self.over_t -= dt
            if self.over_t <= 0:
                self.__init__()
                return
        if self.phase != "win":                          # ambient damage effects
            self.paper_t -= dt
            if self.paper_t <= 0:
                self.paper_t = random.uniform(0.7, 1.6)
                self._add(2, self.CX + random.uniform(-100, 100), self.CY - 10, random.uniform(-120, 120),
                          random.uniform(-260, -120), 2.2, 22, random.uniform(-6, 6))
            self.smoke_t -= dt
            if frac < 0.6 and self.smoke_t <= 0:
                self.smoke_t = 0.07 if frac < 0.3 else 0.15
                self._add(3, self.CX + random.uniform(-120, 120), self.CY - 110, random.uniform(-20, 20),
                          random.uniform(-90, -50), random.uniform(1.2, 2.2), random.uniform(18, 34))
            self.spark_t -= dt
            if frac < 0.45 and self.spark_t <= 0:
                self.spark_t = random.uniform(0.15, 0.5)
                self._burst(self.CX + random.uniform(-160, 160), self.CY + random.uniform(-100, 60), sparks=5, speed=260)
            if frac < 0.25 and random.random() < dt * 14:
                self._burst(self.CX + random.uniform(-150, 150), self.CY - 90, fire=1)
        for q in self.parts:
            k = q[0]
            q[5] -= dt
            q[1] += q[3] * dt
            q[2] += q[4] * dt
            q[7] += q[8] * dt
            if k in (0, 1):
                q[4] += 900 * dt
            elif k == 2:
                q[4] += 260 * dt
                q[3] *= 1 - 1.5 * dt
                q[8] = math.sin(self.t * 5 + q[1] * 0.02) * 5
            elif k == 3:
                q[3] *= 1 - dt
        self.parts = [q for q in self.parts if q[5] > 0 and q[2] < H + 40]
        for b in self.booms:
            b[2] += dt
        self.booms = [b for b in self.booms if b[2] < 0.6]
        self.shake = max(0.0, self.shake - 22 * dt)

    def key(self, k, hub):
        pass

    def lcd(self):
        if self.hp is None:
            return "PC LOAD LETTER"
        f = self.hp / self.maxhp
        if f <= 0:
            return ""
        for limit, text in ((0.85, "PC LOAD LETTER"), (0.65, "PAPER JAM"), (0.45, "ERR 0x8F WHY"),
                            (0.25, "TONER LOW LOW"), (0.0, "PLEASE NO")):
            if f > limit:
                return text
        return "PLEASE NO"

    def snapshot(self):
        return {"name": "printer", "phase": self.phase, "left": round(self.left, 1),
                "hp": round(self.hp if self.hp is not None else 1, 1), "max": self.maxhp or 1,
                "p": [{"dmg": round(p["dmg"]), "gren": p["gren"], "msg": p["msg"]} for p in self.players],
                "marks": self.marks, "shake": round(self.shake, 1), "lcd": self.lcd(),
                "booms": [[b[0], b[1], round(b[2], 2)] for b in self.booms],
                "parts": [[q[0], round(q[1]), round(q[2]), round(q[7], 1), round(q[5] / q[6], 2), round(q[9])]
                          for q in self.parts]}


# ------------------------------------------------------------------------------- rendering
class Renderer:
    def __init__(self):
        mk = lambda n: pygame.font.Font(None, n)
        self.f = {"s": mk(22), "m": mk(30), "l": mk(48), "xl": mk(96)}
        self.sky = pygame.Surface((W, H))
        for y in range(H):
            k = y / H
            self.sky.fill((int(110 + 90 * k), int(180 + 50 * k), int(255 - 30 * k)), (0, y, W, 1))
        random.seed(3)
        self.clouds = [(random.randint(0, W), random.randint(20, 250), random.randint(40, 90))
                       for _ in range(6)]
        random.seed()
        self.border = 24                   # white tracking border thickness in pixels (- and = keys)

    def frame(self, surf):
        """White border round the screen edge: the Sinden camera tracks this to find the screen."""
        b = self.border
        if b <= 0:
            return
        pygame.draw.rect(surf, (255, 255, 255), (0, 0, W, H), b)
        pygame.draw.rect(surf, (0, 0, 0), (b, b, W - 2 * b, H - 2 * b), 3)   # thin black inner edge

    def text(self, surf, s, key, pos, color=(235, 235, 240), center=False, right=False):
        img = self.f[key].render(s, True, color)
        r = img.get_rect()
        if center:
            r.center = pos
        elif right:
            r.topright = pos
        else:
            r.topleft = pos
        surf.blit(img, r)
        return r

    # ---- tester
    def control(self, surf, rect, label, held, count, color, circle=False):
        if held:
            fill, border = color, (255, 255, 255)
        elif count:
            fill, border = (30, 90, 50), (90, 220, 120)
        else:
            fill, border = (45, 48, 60), (90, 94, 110)
        if circle:
            pygame.draw.ellipse(surf, fill, rect)
            pygame.draw.ellipse(surf, border, rect, 3)
        else:
            pygame.draw.rect(surf, fill, rect, border_radius=10)
            pygame.draw.rect(surf, border, rect, 3, border_radius=10)
        tc = (10, 10, 10) if held else (235, 235, 240)
        self.text(surf, label, "m", (rect.centerx, rect.centery - 9), tc, center=True)
        self.text(surf, "x%d" % count if count else "untested", "s", (rect.centerx, rect.centery + 13),
                  tc if held else (150, 155, 170), center=True)

    def panel(self, surf, r, i, pl, st, me):
        col = COLORS[i]
        pygame.draw.rect(surf, (28, 31, 42), r, border_radius=14)
        pygame.draw.rect(surf, col, r, 3, border_radius=14)
        self.text(surf, "PLAYER %d%s" % (i + 1, "  (this PC)" if i in me else "  (LAN)"), "m",
                  (r.x + 16, r.y + 10), col)
        if pl["name"]:
            self.text(surf, pl["name"][:34], "s", (r.right - 14, r.y + 14), (150, 155, 170), right=True)
        ox, oy, cw, ch = r.x + 16, r.y + 46, r.w - 32, r.h - 46 - 78
        held = set(st["held"])
        s = min(cw * 0.11, ch * 0.24)
        cx, cy = ox + cw * 0.17, oy + ch * 0.5
        for name, (dx, dy) in (("UP", (0, -1)), ("DOWN", (0, 1)), ("LEFT", (-1, 0)), ("RIGHT", (1, 0))):
            rect = pygame.Rect(0, 0, s, s)
            rect.center = (cx + dx * (s + 6), cy + dy * (s + 6))
            self.control(surf, rect, {"UP": "UP", "DOWN": "DN", "LEFT": "LT", "RIGHT": "RT"}[name],
                         name in held, st["cnt"][name], col)
        rx, rw = ox + cw * 0.38, cw * 0.62
        self.control(surf, pygame.Rect(rx, oy, rw, ch * 0.30), "TRIGGER", "TRIGGER" in held,
                     st["cnt"]["TRIGGER"], col)
        self.control(surf, pygame.Rect(rx, oy + ch * 0.36, rw, ch * 0.22), "RELOAD", "RELOAD" in held,
                     st["cnt"]["RELOAD"], col)
        d = min(rw / 4 - 8, ch * 0.34)
        for k, c in enumerate("ABCD"):
            rect = pygame.Rect(rx + k * (rw / 4), oy + ch * 0.66, d, d)
            self.control(surf, rect, c, c in held, st["cnt"][c], col, circle=True)
        y = r.bottom - 70
        self.text(surf, "last raw input: %s" % st["raw"], "s", (r.x + 16, y), (200, 205, 220))
        aim = "aim %.2f, %.2f" % tuple(pl["aim"]) if pl["has"] else "NO AIM DATA yet (is the Sinden driver running?)"
        self.text(surf, aim, "s", (r.x + 16, y + 20), (200, 205, 220) if pl["has"] else (255, 170, 90))
        self.text(surf, "recoil requests: %d   %s" % (st["shots"], pl["rc"]), "s", (r.x + 16, y + 40),
                  (200, 205, 220))

    def tester(self, surf, sc, state, me):
        surf.fill((18, 20, 28))
        self.text(surf, "LIGHTGUN TESTER", "l", (36, 32))
        self.text(surf, "Press every control on each gun - it turns green once it has been seen.",
                  "s", (W - 36, 44), (150, 155, 170), right=True)
        players = state["pl"]
        conn = [i for i, p in enumerate(players) if p["c"]]
        if not conn:
            self.text(surf, "No guns connected", "l", (W // 2, H // 2 - 60), center=True)
        n = max(1, len(conn))
        cols, rows = (1 if n == 1 else 2), ((n + 1) // 2 if n > 1 else 1)
        area = pygame.Rect(32, 84, W - 64, H - 84 - 142)
        pw, ph = area.w // cols, area.h // rows
        for k, i in enumerate(conn):
            r = pygame.Rect(area.x + (k % cols) * pw + 4, area.y + (k // cols) * ph + 4, pw - 8, ph - 8)
            self.panel(surf, r, i, players[i], sc["s"][i], me)
        if len(conn) <= 1:                      # room beside the single panel for the event log
            for k, line in enumerate(sc["log"][-9:]):
                self.text(surf, line, "s", (W // 2 + 24, 92 + k * 22), (130, 135, 150))
        elif sc["log"]:
            self.text(surf, sc["log"][-1], "s", (36, 68), (130, 135, 150))

    # ---- duck hunt
    def duck(self, surf, d, wing_up):
        x, y, dr, st, ph = d[1], d[2], d[3], d[4], d[5]
        hit = st == "fall"
        body = (190, 70, 60) if hit else (150, 100, 55)
        pygame.draw.ellipse(surf, body, (x - 36, y - 18, 72, 38))
        pygame.draw.ellipse(surf, (225, 205, 170), (x - 20, y - 4, 44, 22))
        hx, hy = x + dr * 34, y - 20
        pygame.draw.circle(surf, (30, 120, 65), (hx, hy), 15)
        pygame.draw.rect(surf, (245, 245, 245), (hx - dr * 16 - 6 if dr < 0 else hx - 16, hy + 10, 12, 4))
        pygame.draw.polygon(surf, (250, 170, 40), [(hx + dr * 13, hy - 2), (hx + dr * 31, hy + 4), (hx + dr * 13, hy + 8)])
        if hit:
            for sx in (-1, 1):
                pygame.draw.line(surf, (0, 0, 0), (hx + 2 * dr - 4, hy - 8 + 4 * (1 + sx)),
                                 (hx + 2 * dr + 4, hy - 4 + 4 * (1 + sx)), 2)
        else:
            pygame.draw.circle(surf, (0, 0, 0), (hx + dr * 4, hy - 4), 3)
        up = math.sin(ph) > 0 and not hit
        tip = y - 44 if up else y + 22
        pygame.draw.polygon(surf, (110, 70, 40), [(x - 18 * dr, y - 4), (x + 8 * dr, y - 4), (x - 12 * dr, tip)])
        pygame.draw.polygon(surf, (70, 45, 25), [(x - 18 * dr, y - 4), (x + 8 * dr, y - 4), (x - 12 * dr, tip)], 2)

    def duck_scene(self, surf, sc, state, me):
        surf.blit(self.sky, (0, 0))
        for cx, cy, r in self.clouds:
            for k in range(3):
                pygame.draw.circle(surf, (250, 250, 255), (cx + k * r * 0.8, cy + (k % 2) * 8), r * 0.6)
        pygame.draw.rect(surf, (70, 160, 60), (0, H - 150, W, 150))
        pygame.draw.rect(surf, (50, 125, 45), (0, H - 150, W, 14))
        for d in sc["ducks"]:
            self.duck(surf, d, True)
        for x in range(0, W, 26):                       # grass blades in front of the ducks
            pygame.draw.polygon(surf, (45, 130, 45), [(x, H - 140), (x + 13, H - 175 - (x * 7) % 18), (x + 26, H - 140)])
        pygame.draw.rect(surf, (90, 60, 35), (0, H - 118, W, 118))
        for f in sc["fx"]:
            r = int(10 + f[4] * 40)
            pygame.draw.circle(surf, (255, 230, 80) if f[2] else (230, 230, 230), (f[0], f[1]), r, 3)
        self.text(surf, "TIME %02d" % max(0, int(sc["left"])), "l", (W // 2 - 60, 34), (20, 30, 60), center=False)
        players = state["pl"]
        conn = [i for i, p in enumerate(players) if p["c"]]
        for k, i in enumerate(conn):
            x, p = 44 + k * 310, sc["p"][i]
            self.text(surf, "P%d  %05d" % (i + 1, p["score"]), "l", (x, H - 112), COLORS[i])
            for b in range(Duck.MAG):
                r = pygame.Rect(x + b * 22, H - 66, 14, 28)
                pygame.draw.rect(surf, (255, 215, 90) if b < p["ammo"] else (70, 55, 40), r, border_radius=5)
            self.text(surf, p["msg"], "m", (x + 150, H - 62), (255, 255, 255))
        if sc["phase"] == "over":
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 150))
            surf.blit(ov, (0, 0))
            self.text(surf, "TIME UP", "xl", (W // 2, 200), center=True)
            for k, i in enumerate(conn):
                p = sc["p"][i]
                acc = 100 * p["hits"] // p["shots"] if p["shots"] else 0
                self.text(surf, "PLAYER %d   %d points   %d%% accuracy" % (i + 1, p["score"], acc), "l",
                          (W // 2, 320 + k * 60), COLORS[i], center=True)

    # ---- printer bonus stage
    @staticmethod
    def quad(surf, color, x, y, w, h, ang, width=0):
        c, sn = math.cos(ang), math.sin(ang)
        pts = [(x + px * c - py * sn, y + px * sn + py * c) for px, py in
               ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
        pygame.draw.polygon(surf, color, pts, width)

    def printer(self, surf, sc, ox, oy):
        cx, cy = Printer.CX + ox, Printer.CY + oy
        frac = max(0.0, sc["hp"] / sc["max"])
        dmg = 1 - frac
        body = tuple(int(a + (b - a) * dmg) for a, b in zip((226, 221, 206), (60, 56, 52)))
        dark = tuple(int(c * 0.78) for c in body)
        pygame.draw.rect(surf, (40, 40, 40), (cx - 200, cy + 128, 50, 14))           # feet
        pygame.draw.rect(surf, (40, 40, 40), (cx + 150, cy + 128, 50, 14))
        pygame.draw.rect(surf, body, (cx - 220, cy - 80, 440, 210), border_radius=10)
        pygame.draw.rect(surf, (30, 30, 30), (cx - 220, cy - 80, 440, 210), 4, border_radius=10)
        if frac > 0.4:                                                                  # scanner lid
            pygame.draw.rect(surf, dark, (cx - 224, cy - 130, 448, 54), border_radius=8)
            pygame.draw.rect(surf, (30, 30, 30), (cx - 224, cy - 130, 448, 54), 4, border_radius=8)
            pygame.draw.rect(surf, (190, 200, 210), (cx - 200, cy - 120, 300, 34), border_radius=4)
        else:                                                                           # lid has popped open
            self.quad(surf, dark, cx - 20, cy - 175, 440, 50, -0.35)
            self.quad(surf, (30, 30, 30), cx - 20, cy - 175, 440, 50, -0.35, 4)
        pygame.draw.rect(surf, (45, 48, 52), (cx + 40, cy - 70, 165, 62), border_radius=6)   # control panel
        pygame.draw.rect(surf, (140, 200, 120) if sc["lcd"] else (30, 40, 30), (cx + 48, cy - 64, 150, 26))
        if sc["lcd"]:
            self.text(surf, sc["lcd"], "s", (cx + 123, cy - 51), (20, 60, 20), center=True)
        for k, col in enumerate(((220, 60, 50), (240, 200, 60), (80, 190, 90), (90, 150, 230))):
            pygame.draw.circle(surf, col, (cx + 62 + k * 38, cy - 22), 9)
        pygame.draw.rect(surf, (25, 25, 28), (cx - 200, cy - 10, 380, 34), border_radius=6)  # output slot
        pygame.draw.rect(surf, (250, 250, 250), (cx - 180, cy - 2, 340, 18))
        for k in range(3):
            pygame.draw.line(surf, (170, 170, 190), (cx - 170, cy + 3 + k * 5), (cx + 150, cy + 3 + k * 5), 1)
        pygame.draw.rect(surf, dark, (cx - 200, cy + 44, 400, 66), border_radius=6)           # paper drawer
        pygame.draw.rect(surf, (30, 30, 30), (cx - 200, cy + 44, 400, 66), 3, border_radius=6)
        pygame.draw.rect(surf, (30, 30, 30), (cx - 50, cy + 70, 100, 12), border_radius=5)
        if dmg > 0.25:                                                                  # dents and scorching
            pygame.draw.polygon(surf, dark, [(cx - 190, cy - 60), (cx - 120, cy - 40), (cx - 160, cy - 15)])
            pygame.draw.polygon(surf, dark, [(cx + 120, cy + 60), (cx + 200, cy + 90), (cx + 130, cy + 118)])
        if dmg > 0.5:
            for pts in (((cx - 90, cy - 70), (cx - 60, cy - 30), (cx - 80, cy + 10), (cx - 45, cy + 40)),
                        ((cx + 150, cy - 10), (cx + 110, cy + 30), (cx + 140, cy + 60))):
                pygame.draw.lines(surf, (20, 20, 20), False, pts, 3)
        for mx, my in sc["marks"]:                                                      # bullet holes
            pygame.draw.circle(surf, (15, 15, 15), (cx + mx, cy + my), 4)
            pygame.draw.circle(surf, (120, 120, 120), (cx + mx, cy + my), 6, 1)

    def particle(self, q, surf):
        k, x, y, rot, life, size = q
        if k == 0:
            pygame.draw.circle(surf, (255, 235, 140) if life > 0.4 else (255, 140, 40), (x, y), max(1, size // 2 + 1))
        elif k == 1:
            self.quad(surf, (95, 92, 88), x, y, size, size * 0.6, rot)
            self.quad(surf, (30, 30, 30), x, y, size, size * 0.6, rot, 2)
        elif k == 2:
            self.quad(surf, (252, 252, 252), x, y, size, size * 1.3, rot)
            self.quad(surf, (150, 150, 170), x, y, size, size * 1.3, rot, 1)
        elif k == 3:
            r = int(size * (1.8 - life))
            sm = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(sm, (70, 70, 70, int(150 * life)), (r, r), r)
            surf.blit(sm, (x - r, y - r))
        elif k == 4:
            r = max(2, int(size * (0.5 + life)))
            pygame.draw.circle(surf, (255, 90, 20), (x, y), r)
            pygame.draw.circle(surf, (255, 200, 60), (x, y - r // 4), max(1, r // 2))
        else:
            pygame.draw.circle(surf, (225, 225, 225), (x, y), int(3 + (1 - life) * 6), 1)

    def printer_scene(self, surf, sc, state, me):
        surf.blit(self.sky, (0, 0))
        for cx, cy, r in self.clouds:
            for k in range(3):
                pygame.draw.circle(surf, (250, 250, 255), (cx + k * r * 0.8, cy + (k % 2) * 8), r * 0.6)
        pygame.draw.rect(surf, (96, 150, 70), (0, 520, W, H - 520))
        pygame.draw.rect(surf, (74, 122, 52), (0, 520, W, 12))
        pygame.draw.ellipse(surf, (60, 100, 45), (Printer.CX - 270, 640, 540, 40))     # shadow
        s = sc["shake"]
        ox, oy = random.uniform(-s, s), random.uniform(-s, s)
        if sc["hp"] > 0:
            self.printer(surf, sc, ox, oy)
        for q in sc["parts"]:
            if q[0] != 4:
                self.particle(q, surf)
        for q in sc["parts"]:
            if q[0] == 4:
                self.particle(q, surf)
        for bx, by, age in sc["booms"]:
            r = int(30 + age * 260)
            pygame.draw.circle(surf, (255, 120, 30), (bx, by), r, max(2, int(18 * (0.6 - age))))
            pygame.draw.circle(surf, (255, 220, 90), (bx, by), int(r * 0.6))
        frac = max(0.0, sc["hp"] / sc["max"])
        bar = pygame.Rect(W // 2 - 240, 34, 480, 26)
        pygame.draw.rect(surf, (30, 30, 30), bar.inflate(8, 8), border_radius=8)
        pygame.draw.rect(surf, (70, 20, 20), bar, border_radius=6)
        if frac > 0:
            pygame.draw.rect(surf, (230, 60, 50) if frac < 0.4 else (240, 190, 50), (bar.x, bar.y, int(bar.w * frac), bar.h), border_radius=6)
        self.text(surf, "PRINTER  %d%%" % round(frac * 100), "m", bar.center, (255, 255, 255), center=True)
        self.text(surf, "%02d" % max(0, int(sc["left"] + 0.99)), "l", (W - 40, 32), (20, 30, 60), right=True)
        conn = [i for i, p in enumerate(state["pl"]) if p["c"]]
        for k, i in enumerate(conn):
            x, p = 44 + k * 310, sc["p"][i]
            self.text(surf, "P%d  %d dmg" % (i + 1, p["dmg"]), "l", (x, H - 112), COLORS[i])
            for g in range(Printer.GREN_MAX):
                c = (110, 130, 60) if g < p["gren"] else (60, 60, 50)
                pygame.draw.circle(surf, c, (x + 14 + g * 34, H - 52), 13)
                pygame.draw.circle(surf, (20, 20, 20), (x + 14 + g * 34, H - 52), 13, 2)
            self.text(surf, p["msg"], "m", (x + 120, H - 62), (255, 255, 255))
        if sc["phase"] != "play":
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 120))
            surf.blit(ov, (0, 0))
            win = sc["phase"] == "win"
            self.text(surf, "PRINTER DESTROYED" if win else "THE PRINTER SURVIVED", "xl", (W // 2, 170), (255, 220, 90) if win else (255, 120, 120), center=True)
            self.text(surf, ("Time: %.1fs   PC LOAD LETTER? NO MORE." % (Printer.ROUND - sc["left"])) if win else "PC LOAD LETTER", "l", (W // 2, 250), center=True)
            for k, i in enumerate(conn):
                self.text(surf, "PLAYER %d   %d damage" % (i + 1, sc["p"][i]["dmg"]), "l", (W // 2, 340 + k * 56), COLORS[i], center=True)

    def crosshairs(self, surf, state, me):
        for i, p in enumerate(state["pl"]):
            if not p["c"]:
                continue
            x, y = p["aim"][0] * W, p["aim"][1] * H
            c = COLORS[i] if p["has"] else (130, 130, 130)
            pygame.draw.circle(surf, (0, 0, 0), (x, y), 26, 5)
            pygame.draw.circle(surf, c, (x, y), 26, 3)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                pygame.draw.line(surf, c, (x + dx * 12, y + dy * 12), (x + dx * 36, y + dy * 36), 3)
            self.text(surf, "P%d" % (i + 1), "s", (x + 30, y + 28), c)

    def draw(self, surf, state, me, status, banner, help_line):
        sc = state["sc"]
        {"test": self.tester, "duck": self.duck_scene, "printer": self.printer_scene}[sc["name"]](surf, sc, state, me)
        self.crosshairs(surf, state, me)
        bar = pygame.Rect(0, H - 138, W, 138)
        if sc["name"] == "test":
            pygame.draw.rect(surf, (12, 13, 18), bar)
            self.text(surf, "Recoil mode: " + RMODE_HELP[state["rmode"]], "m", (36, bar.y + 8), (255, 220, 120))
            self.text(surf, "strength %d/25" % state["rstr"], "m", (W - 36, bar.y + 8), (255, 220, 120), right=True)
            for k, s in enumerate(status[:2]):
                self.text(surf, s, "s", (36, bar.y + 40 + k * 18), (150, 155, 170))
            self.text(surf, help_line, "s", (36, bar.y + 84), (110, 200, 255))
        else:
            self.text(surf, "F1 test  F2 ducks  F3 printer  -/= border  Esc", "s", (36, 36), (30, 40, 70))
        if banner:
            r = self.text(surf, banner, "l", (W // 2, 100), (255, 255, 255), center=True)
            pygame.draw.rect(surf, (200, 60, 60), r.inflate(40, 16), 3, border_radius=10)


# ---------------------------------------------------------------------------------- the app
HELP_HOST = ("F1 tester   F2 ducks   F3 printer   F5 kick   F6 rapid kicks   F7 recoil mode   F8/F9 strength   "
             "-/= border   F10 learn   F11 fullscreen   F12 reset   Esc quit")
HELP_CLIENT = "-/= border   F10 learn buttons   F11 fullscreen   Esc quit   (the host picks the game)"


class App:
    def __init__(self, args, backend=None):
        self.args = args
        self.role = "client" if args.join else ("host" if args.host else "local")
        pygame.init()
        flags = pygame.SCALED | (0 if args.windowed else pygame.FULLSCREEN)
        self.screen = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("Sinden lightgun POC")
        pygame.mouse.set_visible(False)
        self.clock = pygame.time.Clock()
        self.r = Renderer()
        self.backend = backend or gunio.make_input(args.input, grab=not args.no_grab)
        self.guns = self.backend.guns
        self.mapping = gunio.load_mapping()
        serials = args.serial.split(",") if args.serial else None
        self.recoils = gunio.pair_recoils(self.guns, serials, not args.no_recoil)
        self.rcfg, self.seen = None, [0] * MAXP
        self.learn, self.learn_msg, self.banner, self.banner_t = None, "", "", 0.0
        self.running, self.state, self.net, self.my_slot = True, None, None, 0
        self.send_t = 0.0
        if self.role == "client":
            import net
            try:
                self.net = net.ClientNet(args.join, args.port)
            except OSError as e:
                sys.exit("Could not connect to %s:%d - %s" % (args.join, args.port, e))
            self.net.send({"t": "hello", "name": socket.gethostname()})
            self.me = [0]
        else:
            n = min(len(self.guns), MAXP)
            self.hub, self.scene = Hub(n), Tester()
            self.me = list(range(n))
            if self.role == "host":
                import net
                self.net = net.HostNet(args.port)
                print("Hosting on port %d. On the other PC run:  python lightgun.py --join %s"
                      % (args.port, net.lan_ip()))
        if not self.guns:
            self.say("No guns found - " + self.backend.describe(), 6)

    # ---- helpers
    def say(self, msg, secs=3.0):
        self.banner, self.banner_t = msg, secs

    def status(self):
        lines = []
        for i, g in enumerate(self.guns[:2]):
            rc = self.recoils[i].status if self.recoils[i] else ("recoil disabled" if self.args.no_recoil else "no serial port found for this gun")
            lines.append("local gun %d: %s | aim %s | %s" % (i + 1, g.name, "ok" if g.has_aim else "no data", rc))
        return lines or [self.backend.describe()]

    def read_local(self, pg_events):
        """Poll the guns; returns one list of (raw, logical, pressed) per local gun."""
        per = [[] for _ in self.guns]
        for gi, raw, pressed in self.backend.poll(pg_events):
            if self.learn is not None:
                if pressed and raw != "tab" and raw in self.learn["map"]:
                    self.say("'%s' was already used for %s - this button sends the same code. "
                             "Change it in the Sinden driver config, or press Tab to skip." % (
                                 raw, self.learn["map"][raw]), 4)
                elif pressed and raw != "tab":
                    ctl = CONTROLS[self.learn["i"]]
                    self.learn["map"][raw] = ctl
                    self.learn["i"] += 1
                    self.learn_msg = "%s = %s" % (ctl, raw)
                    self.finish_learn_if_done()
                continue
            per[gi].append((raw, self.mapping.get(raw), pressed))
        return per

    def finish_learn_if_done(self):
        if self.learn and self.learn["i"] >= len(CONTROLS):
            self.mapping = self.learn["map"]
            gunio.save_mapping(self.mapping)
            self.learn = None
            self.say("Button mapping saved", 3)

    # ---- main loop
    def handle_events(self, events):
        for e in events:
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                k = e.key
                if k == pygame.K_ESCAPE:
                    if self.learn is not None:
                        self.learn = None
                        self.say("Learning cancelled")
                    else:
                        self.running = False
                elif k == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                elif k in (pygame.K_MINUS, pygame.K_EQUALS, pygame.K_KP_MINUS, pygame.K_KP_PLUS):
                    self.r.border = max(0, min(90, self.r.border + (4 if k in (pygame.K_EQUALS, pygame.K_KP_PLUS) else -4)))
                    self.say("Tracking border %d px" % self.r.border, 1.5)
                elif k == pygame.K_F10:
                    self.learn = {"i": 0, "map": {}}
                elif k == pygame.K_TAB and self.learn is not None:
                    self.learn["i"] += 1
                    self.finish_learn_if_done()
                elif self.role != "client" and self.learn is None:
                    self.host_key(k)

    def host_key(self, k):
        hub = self.hub
        if k == pygame.K_F1:
            self.scene = Tester()
        elif k == pygame.K_F2:
            self.scene = Duck()
        elif k == pygame.K_F3:
            self.scene = Printer()
        elif k == pygame.K_F7:
            hub.rmode = RMODES[(RMODES.index(hub.rmode) + 1) % len(RMODES)]
        elif k == pygame.K_F8:
            hub.rstr = max(0, hub.rstr - 1)
        elif k == pygame.K_F9:
            hub.rstr = min(25, hub.rstr + 1)
        else:
            self.scene.key(k, hub)

    def step_host(self, dt, pg_events):
        hub = self.hub
        per = self.read_local(pg_events)
        for i, g in enumerate(self.guns[:MAXP]):
            sl = hub.slots[i]
            sl.update(aim=list(g.aim), has=g.has_aim, name=g.name, c=g.connected,
                      rc=self.recoils[i].status if self.recoils[i] else "no recoil serial port")
            hub.push(i, per[i])
        if self.net:
            for cid, msg in self.net.messages():
                if msg is None:
                    hub.remove_remote(cid)
                    self.net.drop(cid)
                    self.say("A remote gun left", 2)
                elif msg.get("t") == "hello":
                    slot = hub.add_remote(cid, msg.get("name", "remote"))
                    self.net.send(cid, {"t": "welcome", "slot": slot})
                    if slot is not None:
                        self.say("Player %d joined over the LAN (%s)" % (slot + 1, msg.get("name")), 3)
                elif msg.get("t") == "in" and cid in hub.remote:
                    slot = hub.remote[cid]
                    hub.slots[slot].update(aim=msg["aim"], has=msg["has"], rc=msg.get("rc", ""))
                    hub.push(slot, [tuple(e) for e in msg["ev"]])
        self.scene.update(dt, hub)
        rmode = hub.rmode
        self.state = {"t": "state", "sc": self.scene.snapshot(), "pl": hub.players(),
                      "rseq": hub.rseq, "rmode": rmode, "rstr": hub.rstr}
        hub.end_frame()
        if self.net:
            self.send_t += dt
            if self.send_t >= 1 / 30:
                self.send_t = 0
                self.net.broadcast(self.state)

    def step_client(self, dt, pg_events):
        per = self.read_local(pg_events)
        g = self.guns[0] if self.guns else None
        self.send_t += dt
        if g and (per[0] or self.send_t >= 1 / 30):
            self.send_t = 0
            self.net.send({"t": "in", "aim": list(g.aim), "has": g.has_aim,
                           "rc": self.recoils[0].status if self.recoils[0] else "no recoil serial port",
                           "ev": per[0]})
        for msg in self.net.messages():
            if msg.get("t") == "welcome":
                if msg["slot"] is None:
                    sys.exit("Host is full")
                self.my_slot = msg["slot"]
                self.me = [self.my_slot]
            elif msg.get("t") == "state":
                self.state = msg

    def run(self):
        try:
            while self.running:
                dt = self.clock.tick(60) / 1000
                events = pygame.event.get()
                self.handle_events(events)
                (self.step_client if self.role == "client" else self.step_host)(dt, events)
                self.banner_t -= dt
                banner = self.banner if self.banner_t > 0 else ""
                if self.learn is not None and not banner:
                    i = min(self.learn["i"], len(CONTROLS) - 1)
                    banner = "Press %s on any gun   (Tab = skip, Esc = cancel)   [%d/%d]" % (
                        CONTROL_HELP[CONTROLS[i]], i + 1, len(CONTROLS))
                if self.state:
                    self.apply_recoil_local()
                    self.r.draw(self.screen, self.state, self.me, self.status(), banner,
                                HELP_CLIENT if self.role == "client" else HELP_HOST)
                else:
                    self.screen.fill((18, 20, 28))
                    self.r.text(self.screen, "Waiting for the host ...", "l", (W // 2, H // 2), center=True)
                if self.role == "client" and not self.net.alive:
                    self.r.text(self.screen, "Host disconnected - press Esc", "l", (W // 2, H - 80), (255, 120, 120), center=True)
                self.r.frame(self.screen)
                pygame.display.flip()
        finally:
            self.shutdown()

    def apply_recoil_local(self):
        """Configure local guns and fire their recoil when the shared counters tick up."""
        st = self.state
        cfg = (st["rmode"], st["rstr"])
        if cfg != self.rcfg:
            self.rcfg = cfg
            for rc in self.recoils:
                if rc:
                    rc.configure(*cfg)
        pairs = [(i, i) for i in range(len(self.guns))] if self.role != "client" else (
            [(self.my_slot, 0)] if self.guns else [])
        for slot, gi in pairs:
            if slot < MAXP and st["rseq"][slot] != self.seen[slot]:
                self.seen[slot] = st["rseq"][slot]
                if self.recoils[gi] and st["rmode"] != "off":
                    self.recoils[gi].fire()

    def shutdown(self):
        for rc in self.recoils:
            if rc:
                rc.configure("firmware", 10)         # leave the gun the way Sinden's defaults do
                rc.close()
        self.backend.close()
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", action="store_true", help="accept guns from other PCs")
    ap.add_argument("--join", metavar="IP", help="connect to a host")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--input", choices=["auto", "evdev", "pygame"], default="auto",
                    help="evdev = per-gun input on Linux (needed for 2 guns on 1 PC)")
    ap.add_argument("--serial", help="comma separated serial ports for recoil, one per gun (e.g. COM5 or /dev/ttyACM0)")
    ap.add_argument("--no-recoil", action="store_true", help="never touch the serial ports")
    ap.add_argument("--no-grab", action="store_true", help="evdev: don't hide the guns from the desktop")
    ap.add_argument("--windowed", action="store_true")
    App(ap.parse_args()).run()


if __name__ == "__main__":
    main()
