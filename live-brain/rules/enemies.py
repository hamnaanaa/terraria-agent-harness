"""The enemies' rules: how each kind moves, played forward a tick at a time toward the body, and what its touch
costs. The Gym measurements cover walking, jumping at walls, flying, and contact damage.

Measured in the gym, Oct 18, at the world's enemy strength 1.5:
    walker (a zombie)   gains 0.07 px a tick toward the body up to 1.0; gravity 0.3, falls at most 10; blocked by a
                        wall on the ground it jumps off at 7.7 px a tick (5.7 at a wall of 2, 6.7 at 3) and clears
                        walls up to 6 high
    flier (a demon eye) gains 0.1 sideways and 0.04 up or down toward the body, at most 4 sideways and 1.5 up or down
    touch               the enemy's damage as the mod reads it, the mean of the game's ±15% spread, less the defense
                        times its share (half in Classic and Journey); then the body cannot be touched for 40 ticks (a
                        zombie's 21 came as 24, an eye's 27 as 29: the spread)
The world's multiplier is already in an enemy's damage, put there when it spawns (NPC.ScaleStats: the mode's own,
Journey's slider). A shot's damage does not have it: a hostile shot hits for twice its damage times the multiplier
(Projectile.Damage, then CombinedHooks.ModifyHitByProjectile). Checked Nov 1 against the hits logged: in the Gym, 289
touches of five kinds, each kind's mean within 0.32 of the rule; in Classic, falling sand of damage 10 hit for 18, 22
and 18 at defense 3, the rule's 18.
An enemy is a dict: x, y (top left), w, h, vx, vy, kind ("walker" or "flier"), damage, defense, life, kb_taken.
"""
import math

from rules.body import W as BODY_W, H as BODY_H

CELL = 16
WALKER = {"acc": 0.07, "top": 1.0, "gravity": 0.3, "fall_top": 10.0, "jump": 7.7, "jump_by_wall": {2: 5.7, 3: 6.7}}
# fliers gain less when turning back than when closing in (the gym, Oct 19: the one-step velocity errors were 0.05 on
# every tick a demon eye moved away from the body). By AI style: 2 the demon eye, 14 the bats and harpies at twice
# the eye's; the eye's numbers for the fliers not measured yet
FLIER = {"ax": 0.1, "ax_away": 0.05, "ay": 0.04, "ay_away": 0.01, "top_x": 4.0, "top_y": 1.5}
FLIERS = {2: FLIER, 14: {"ax": 0.2, "ax_away": 0.1, "ay": 0.08, "ay_away": 0.02, "top_x": 4.0, "top_y": 1.5}}
IMMUNE_TICKS = 40


# the game's AI style is its movement family (the gym's probe of 23 early enemies, Oct 18): 1 slimes, 2 demon eyes,
# 3 every walker (zombies, skeletons, goblins, archers, crawlers), 4 the Eye of Cthulhu, 5 crimeras, eaters, hornets,
# 6 worms, 8 casters, 14 bats and harpies, 16 piranhas, 17 vultures, 19 antlions, 22 wraiths. Each family gets one
# model; the ones not measured yet fall back on the nearest measured one (flier for anything that flies, walker for
# the rest) with the enemy's own numbers where they are read live
FAMILIES = {1: "hopper", 2: "flier", 3: "walker", 4: "flier", 5: "flier", 6: "burrower", 8: "walker", 14: "flier",
            16: "swimmer", 17: "vulture", 19: "walker", 22: "flier"}
MEASURED = {"walker", "flier", "hopper", "swimmer", "burrower", "vulture"}
# AI style 17, the vultures, from the game's own code (tModLoader.dll, NPC.VanillaAI_Inner, read Nov 1): it sits on
# the ground (its first AI counter 0) until the body's box meets its own grown by 100 px every way, or it is hurt, or
# it moves; then it jumps 6 px a tick up and flies with no gravity. Sideways it gains 0.1 a tick toward the body (0.05
# more while still moving away, 0.1 more past 3 the other way), at most 3. Up or down it gains 0.05 a tick toward a
# height (0.01 more while still moving away), at most 3: its top half its height over the body's top, and 100 px
# higher while its centre is more than 50 px from the body's sideways. A block it runs into turns it back at half
# speed: 2 at least sideways, 1 up or down. In water it rises (0.5 a tick, up to 4). Before Nov 1 it moved as a
# walker: over 42 shots at a flying vulture in Classic the forecast was a median 140 px off, the bird 96 px higher
VULTURE = {"wake_px": 100, "jump": 6.0, "ax": 0.1, "ax_away": 0.05, "ay": 0.05, "ay_away": 0.01, "top": 3.0,
           "over_px": 100, "near_px": 50, "bounce": 0.5, "bounce_x": 2.0, "bounce_y": 1.0, "rise": 0.5, "rise_top": 4.0}
# measured in the gym, Oct 18 night: a blue slime rests about 99 ticks between hops, takes off at 2 px a tick toward
# the body and 5.7 up (every third hop 3 and 7.7), falls at gravity 0.3 and skids to a stop on landing (×0.8 a tick);
# a piranha swims at up to 3 px a tick, gaining 0.1, in water only; a giant worm runs through blocks at 5 to 27 px a
# tick (10.9 on average) and only surfaces near the body: taken as a burrower heading for the body at 8 px a tick
HOPPER = {"rest": 99, "vx": 2.0, "vy": 5.7, "gravity": 0.3, "skid": 0.8, "first_rest": 50,
          # the game's own hop timer, read from the slime (gym, Oct 20, 838 samples): on the ground it climbs 2 a
          # tick and the hop fires when it reaches its band's line, 0, then -1000, then -2000; after each hop it
          # is set to -1120, -2120, -200 in turn, so the waits are 59, 59 and 100 ticks and the third hop is the
          # big one (3 px a tick and 7.7 up against 2 and 5.7)
          "step": 2, "after": (-1120, -2120, -200), "big_vx": 3.0, "big_vy": 7.7,
          # in the air it gains 0.2 a tick toward the body up to 3 (gym, Oct 20: 2, 2.6, 3.0 by the sixth tick)
          "air_acc": 0.2, "air_top": 3.0}
SWIMMER = {"acc": 0.1, "top": 3.0}
BURROWER = {"speed": 8.0, "turn": 0.15}
# Enemy names with measurements, using the game's TypeName.
# Oct 19 and 20, zombie_walk, zombie_wall and touch on a Zombie, eye_fly on a Demon Eye, bat_fly on a Cave Bat,
# slime_hop on a Blue Slime, all passed (the rule and the game 36.4 px apart at worst, at the zombie's wall). Left out:
# the piranha, whose test failed (122.7 px apart at worst, 81.1 at 60 ticks), and the giant worm, whose speeds were
# read but never tested. The Vulture's model is the game's own code, checked on Classic's logged flights (Nov 1: VULTURE).
# Every other kind, the other slimes and zombies too, moves on its family's model as a stand-in
MEASURED_NAMES = {"Zombie", "Demon Eye", "Cave Bat", "Blue Slime", "Vulture"}


def kind_of(e):
    """The movement model for the mod's enemy fields: by the game's AI style, else by whether it flies."""
    fam = FAMILIES.get(e.get("ai"), "flier" if e.get("flies") else "walker")
    return fam if fam in MEASURED else ("flier" if e.get("flies") else "walker")


def measured(name):
    """Whether the gym measured this kind itself, by the game's English name; else its rules are a stand-in."""
    return name in MEASURED_NAMES


def touch_damage(e, defense, eff=0.5):
    """Rule «touch»: the mean of the spread, less the defense times its share (the mod's defense_eff: 0.5, Expert
    0.75, Master 1), at least 1."""
    return max(1, int(e["damage"] - defense * eff))


def shot_damage(s, strength, defense, eff=0.5):
    """Rule «shot»: an enemy's shot, twice its damage times the world's multiplier (the mod's strength), less the
    defense times its share, at least 1."""
    return max(1, int(s["damage"] * 2 * strength - defense * eff))


def overlaps(ax, ay, aw, ah, bx, by, bw, bh):
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def move_box(x, y, w, h, vx, vy, solid, floor):
    """A box moved by its speed and stopped by cells, the way the game's TileCollision does it: a cell the box was
    above is a floor (platforms too), beside a wall, under a ceiling. Returns x, y, vx, vy, on_ground."""
    fx, fy = x + vx, y + vy
    cvx, cvy, hit_x, hit_y = vx, vy, False, False
    for c in range(int(fx // CELL), int(math.ceil((fx + w) / CELL))):
        for r in range(int(fy // CELL), int(math.ceil((fy + h) / CELL))):
            tx, ty = c * CELL, r * CELL
            if not (fx + w > tx and fx < tx + CELL and fy + h > ty and fy < ty + CELL):
                continue
            if solid(c, r):
                if y + h <= ty:
                    cvy, hit_y = min(cvy, ty - (y + h)), True
                elif x + w <= tx:
                    cvx, hit_x = min(cvx, tx - (x + w)), True
                elif x >= tx + CELL:
                    cvx, hit_x = max(cvx, tx + CELL - x), True
                elif y >= ty + CELL:
                    cvy, hit_y = max(cvy, ty + CELL - y), True
            elif floor(c, r) and y + h <= ty and vy > 0:
                cvy, hit_y = min(cvy, ty - (y + h)), True
    nx, ny = x + cvx, y + cvy
    ground = hit_y and vy > 0
    return nx, ny, (0.0 if hit_x else vx), (0.0 if hit_y else vy), ground


def tick(e, body_cx, body_cy, solid, floor, water=None, door=None):
    """One tick of the enemy toward the body's centre (px). Updates e in place. water(x, y): for swimmers."""
    cx = e["x"] + e["w"] / 2
    d = 1 if body_cx > cx else -1
    if e["kind"] == "burrower":
        dx, dy = body_cx - cx, body_cy - (e["y"] + e["h"] / 2)
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        sp = BURROWER["speed"]
        e["vx"] += (sp * dx / n - e["vx"]) * BURROWER["turn"]
        e["vy"] += (sp * dy / n - e["vy"]) * BURROWER["turn"]
        e["x"] += e["vx"]
        e["y"] += e["vy"]
        return
    if e["kind"] == "swimmer":
        # a flier in water: toward the body while the way stays in water, else it waits at the water's edge
        dy = 1 if body_cy > e["y"] + e["h"] / 2 else -1
        vx = max(-SWIMMER["top"], min(SWIMMER["top"], e["vx"] + SWIMMER["acc"] * d))
        vy = max(-SWIMMER["top"], min(SWIMMER["top"], e["vy"] + SWIMMER["acc"] * dy))
        nx, ny = e["x"] + vx, e["y"] + vy
        cc, cr = int((nx + e["w"] / 2) // CELL), int((ny + e["h"] / 2) // CELL)
        if water is None or water(cc, cr):
            e.update(x=nx, y=ny, vx=vx, vy=vy)
        else:
            e.update(vx=0.0, vy=0.0)
        return
    if e["kind"] == "hopper":
        in_air = e["vy"] != 0                                 # before gravity: on the ground vy is 0 at the tick's start
        e["vy"] = min(e["vy"] + HOPPER["gravity"], WALKER["fall_top"])
        if in_air and e["vx"] * d >= 0:
            e["vx"] = d * min(HOPPER["air_top"], abs(e["vx"]) + HOPPER["air_acc"])   # in the air: toward the body
        x, y, vx, vy, ground = move_box(e["x"], e["y"], e["w"], e["h"], e["vx"], e["vy"], solid, floor)
        if ground:
            vx *= HOPPER["skid"]
            if abs(vx) < 0.1:
                vx = 0.0
            if e.get("timer") is not None:
                t = e["timer"] + HOPPER["step"]
                band = int(-t // 1000) if t < 0 else 0
                if t >= -1000 * band:
                    big = band == 2
                    vx, vy = d * (HOPPER["big_vx"] if big else HOPPER["vx"]), -(HOPPER["big_vy"] if big else HOPPER["vy"])
                    t = HOPPER["after"][min(band, 2)]
                e["timer"] = t
            else:
                e["rest"] = e.get("rest", HOPPER["first_rest"]) - 1
                if e["rest"] <= 0:
                    vx, vy = d * HOPPER["vx"], -HOPPER["vy"]
                    e["rest"] = HOPPER["rest"]
        e.update(x=x, y=y, vx=vx, vy=vy)
        return
    if e["kind"] == "vulture":
        vulture(e, body_cx, body_cy, solid, floor, water)
        return
    if e["kind"] == "flier":
        f = FLIERS.get(e.get("ai"), FLIER)
        dy = 1 if body_cy > e["y"] + e["h"] / 2 else -1
        ax = f["ax"] if e["vx"] * d >= 0 else f["ax_away"]
        ay = f["ay"] if e["vy"] * dy >= 0 else f["ay_away"]
        e["vx"] = max(-f["top_x"], min(f["top_x"], e["vx"] + ax * d))
        e["vy"] = max(-f["top_y"], min(f["top_y"], e["vy"] + ay * dy))
        x, y, vx, vy, _ = move_box(e["x"], e["y"], e["w"], e["h"], e["vx"], e["vy"], solid, floor)
        e.update(x=x, y=y, vx=vx, vy=vy)
        return
    e["vx"] = max(-WALKER["top"], min(WALKER["top"], e["vx"] + WALKER["acc"] * d))
    e["vy"] = min(e["vy"] + WALKER["gravity"], WALKER["fall_top"])
    x, y, vx, vy, ground = move_box(e["x"], e["y"], e["w"], e["h"], e["vx"], e["vy"], solid, floor)
    if ground and vx == 0.0 and e["vx"] != 0.0:
        # blocked by a wall while standing: it jumps, higher for a taller wall (rule «walker», the gym's zombie_wall),
        # never higher than the highest measured jump; a closed door stops it without a jump (Gym observations: a zombie
        # stood at the house's door for three minutes while the rule had it jump onto the roof)
        col = int((x + e["w"] + 1) // CELL) if d > 0 else int((x - 1) // CELL)
        feet = int((y + e["h"] - 1) // CELL)
        if door is not None and any(door(col, feet - k) for k in range(3)):
            e.update(x=x, y=y, vx=0.0, vy=vy)
            return
        height = 0
        while solid(col, feet - height) and height < 8:
            height += 1
        jumps = WALKER["jump_by_wall"]
        vy = -jumps.get(height, max(jumps.values()) if height > max(jumps) else WALKER["jump"])
        vx = e["vx"]
    e.update(x=x, y=y, vx=vx, vy=vy)


def vulture_wakes(e, body_cx, body_cy):
    """A sitting vulture takes off: the body's box meets its own grown by 100 px every way, or it has been hurt."""
    v = VULTURE["wake_px"]
    near = overlaps(body_cx - BODY_W / 2, body_cy - BODY_H / 2, BODY_W, BODY_H,
                    e["x"] - v, e["y"] - v, e["w"] + 2 * v, e["h"] + 2 * v)
    return near or e["life"] < e.get("life_max", e["life"])


def vulture_height(e, body_cx, body_cy):
    """The height a flying vulture makes for, as its top: half its height over the body's top, and 100 px higher while
    its centre is more than 50 px from the body's sideways."""
    y = body_cy - BODY_H / 2 - e["h"] // 2
    if abs(e["x"] + e["w"] // 2 - body_cx) > VULTURE["near_px"]:
        y -= VULTURE["over_px"]
    return y


def vulture(e, body_cx, body_cy, solid, floor, water=None):
    """One tick of a vulture (VULTURE: the game's AI style 17)."""
    V = VULTURE
    vx, vy = e["vx"], e["vy"]
    if not e.get("timer"):
        # sitting: gravity on (the game's default for an enemy, 0.3 a tick up to 10: the zombie's in the gym)
        if vx != 0 or vy < 0 or vy > 0.3:
            e["timer"] = 1.0
        elif vulture_wakes(e, body_cx, body_cy):
            e["timer"], vy = 1.0, vy - V["jump"]
        vy = min(vy + WALKER["gravity"], WALKER["fall_top"])
        x, y, vx, vy, _ = move_box(e["x"], e["y"], e["w"], e["h"], vx, vy, solid, floor)
        e.update(x=x, y=y, vx=vx, vy=vy)
        return
    d = 1 if body_cx > e["x"] + e["w"] / 2 else -1
    if d == -1 and vx > -V["top"]:
        vx -= V["ax"]
        if vx > V["top"]:
            vx -= V["ax"]
        elif vx > 0:
            vx -= V["ax_away"]
        vx = max(vx, -V["top"])
    elif d == 1 and vx < V["top"]:
        vx += V["ax"]
        if vx < -V["top"]:
            vx += V["ax"]
        elif vx < 0:
            vx += V["ax_away"]
        vx = min(vx, V["top"])
    if e["y"] < vulture_height(e, body_cx, body_cy):
        vy += V["ay"]
        if vy < 0:
            vy += V["ay_away"]
    else:
        vy -= V["ay"]
        if vy > 0:
            vy -= V["ay_away"]
    vy = max(-V["top"], min(V["top"], vy))
    if water is not None and water(int((e["x"] + e["w"] / 2) // CELL), int((e["y"] + e["h"] / 2) // CELL)):
        vy = max(-V["rise_top"], (vy * 0.95 if vy > 0 else vy) - V["rise"])
    x, y, nvx, nvy, _ = move_box(e["x"], e["y"], e["w"], e["h"], vx, vy, solid, floor)
    if nvx == 0.0 and vx != 0.0:
        nvx = -vx * V["bounce"]
        if d == -1 and 0 < nvx < V["bounce_x"]:
            nvx = V["bounce_x"]
        elif d == 1 and -V["bounce_x"] < nvx < 0:
            nvx = -V["bounce_x"]
    if nvy == 0.0 and vy != 0.0:
        nvy = -vy * V["bounce"]
        if 0 < nvy < V["bounce_y"]:
            nvy = V["bounce_y"]
        elif -V["bounce_y"] < nvy < 0:
            nvy = -V["bounce_y"]
    e.update(x=x, y=y, vx=nvx, vy=nvy)


def reads(e, body_cx, body_cy):
    """What one tick of this enemy reads of the body beyond its side and whether it is above or below (the fast brain
    keeps steps by these): a vulture's height to make for, and whether it wakes."""
    if e["kind"] != "vulture":
        return ()
    return (e["y"] < vulture_height(e, body_cx, body_cy), bool(not e.get("timer") and vulture_wakes(e, body_cx, body_cy)))
