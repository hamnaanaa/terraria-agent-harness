"""The body's rules: one tick of the game's movement, from the keys held and the cells around.

The numbers are the player's own fields, read live from the game (AgentIO's /state "physics"): they change with gear,
buffs and liquids. The order of the steps is the game's (Player.Update: the run, then the jump, then gravity, then the
move with the tiles in the way). Each part names the rule and the gym test that checks it.

A body is a dict: x, y (its top left corner, px), vx, vy, jump (push ticks left), ground (it stood on something at
the end of the last tick), wet, released (the jump key was up since the last jump).
"""
import math
import struct

CELL = 16
W, H = 20, 42

# rule «water_move»: in water the game runs gravity 0.2, a fall's top speed 5.01, a jump push of 6.01 for 30 ticks,
# and moves the body half as far each tick (the game's own fields, read in the gym, Oct 18: water_numbers); honey
# slows it more (not tested yet)
WATER = {"gravity": 0.2, "fall_top": 5.01, "jump_speed": 6.01, "jump_hold": 30, "move": 0.5}
HONEY = {"gravity": 0.1, "fall_top": 3.0, "move": 0.25}
# rule «rope» (gym, Oct 20): with a rope at the point 8 px over the head in the centre's column and up or down
# pressed the character holds on to it, no
# gravity (it drifts 2 or 3 px at the grab); it climbs 0.2 a tick faster each tick up to 3, then 0.02 a tick
# more going up (3.9 after a second) and 0.1 more going down (4.5 after a second); with no key the climb dies
# away by 0.7 a tick; a side key moves it across the rope 0.6 px a tick, to 6 px past the rope's centre, never
# off it; a jump lets go and jumps
ROPE = {"acc": 0.2, "top": 3.0, "creep_up": 0.02, "creep_down": 0.1, "fade": 0.7, "slide": 0.6}
# rule «wings» (gym, Oct 20, Fledgling Wings, wing_time_max 25): the push's last tick is the wings' first, at the push
# speed and 0.1 more; each wing tick after takes 0.1 more off instead of gravity (25 of them); with the wings spent
# and the key still held, a falling body falls at a third of gravity up to a third of fall_top (0.13 a tick, 3.34);
# the wings fill up on the ground
WINGS = {"acc": 0.1, "glide": 3.0, "holds": (60, 120, 220)}   # the jump key held this long: the flights tried


def span(lo, size):
    """The cells a body from lo to lo + size (px) reaches into: the first, and the last one its far edge is past."""
    return int(lo // CELL), math.ceil((lo + size) / CELL) - 1


def f32(v):
    """The game computes in 32-bit floats; so does the rule, or a speed that the game holds a hair under its top is
    exactly at the top here (the gym's run test, Oct 18: one extra push in 45 ticks)."""
    return struct.unpack("f", struct.pack("f", v))[0]


def start(x, y):
    return {"x": float(x), "y": float(y), "vx": 0.0, "vy": 0.0, "jump": 0, "ground": True, "wet": False,
            "released": True, "fall_start": int(y // CELL), "hurt": 0, "rope": False, "wing": 0}


def run(b, keys, phys):
    """Rule «run»: holding a side while slower than run_top gains run_acc a tick, braking by run_slow first when the
    body still moves the other way. With boots (acc_run above run_top), on the ground, past run_top it gains a fifth
    of run_acc a tick up to acc_run (Hermes Boots: 0.016 a tick toward 6; the gym's boots test). Otherwise (no key,
    or at the top) the body loses run_slow a tick on the ground and half of it in the air. At run_top without boots
    the speed saws: one tick over, braked, two ticks back up (the gym's run test, Oct 18)."""
    acc, top, slow = f32(phys["run_acc"]), f32(phys["run_top"]), f32(phys["run_slow"])
    boots = f32(phys.get("acc_run", phys["run_top"]))
    vx = b["vx"]
    if "left" in keys and vx > -top:
        if vx > slow:
            vx = f32(vx - slow)
        vx = f32(vx - acc)
    elif "right" in keys and vx < top:
        if vx < -slow:
            vx = f32(vx + slow)
        vx = f32(vx + acc)
    elif "left" in keys and vx > -boots and b["ground"]:
        vx = f32(vx - f32(acc * 0.2))       # boots: past run_top, a fifth of the push up to acc_run (gym: boots)
    elif "right" in keys and vx < boots and b["ground"]:
        vx = f32(vx + f32(acc * 0.2))
    else:
        s = slow if b["ground"] else f32(slow * 0.5)
        vx = f32(vx - s) if vx > s else f32(vx + s) if vx < -s else 0.0
    b["vx"] = vx


def jump(b, keys, phys):
    """Rule «jump»: a press starts a jump only from the ground and only after the key was let go; it sets the push
    ticks to jump_hold and the speed up to jump_speed, and every later tick with the key held and push ticks left
    keeps the speed at jump_speed and spends one. In water the push is water's. A press in the air is spent too: a
    key held from the air through the landing never jumps (gym, Oct 19 night, dropped 2 and 4 rows)."""
    speed = WATER["jump_speed"] if b["wet"] else phys["jump_speed"]
    hold = WATER["jump_hold"] if b["wet"] else phys["jump_hold"]
    b["flying"] = False
    winged = phys.get("wing_time_max", 0) > 0 and not b["wet"]
    if "jump" in keys:
        if b["jump"] > 0 and not (winged and b["jump"] == 1 and b.get("wing", 0) > 0 and not b["ground"]):
            if b["vy"] == 0:
                b["jump"] = 0                   # the head hit something: the push ends
            else:
                b["vy"] = -f32(speed)
                b["jump"] -= 1
        elif b["jump"] == 1 and winged and b.get("wing", 0) > 0 and not b["ground"]:
            b["jump"] = 0                       # rule «wings»: the push's last tick is the wings' first: the push's
            b["vy"] = f32(-speed - WINGS["acc"])   # speed and the wings' first 0.1, no gravity (gym, Oct 20)
            b["wing"] -= 1
            b["flying"] = True
        elif b["ground"] and b["released"]:
            b["jump"] = hold
            b["vy"] = -f32(speed)
            b["released"] = False
            b["rope"] = False                   # a jump lets go of a rope
        elif not b["ground"] and b.get("wing", 0) > 0 and not b["released"]:
            b["vy"] = f32(b["vy"] - WINGS["acc"])   # rule «wings»: the wings push, no gravity this tick
            b["wing"] -= 1
            b["flying"] = True
        else:
            b["released"] = False               # the press is spent in the air
    else:
        b["jump"] = 0
        b["released"] = True


def gravity(b, phys, keys=()):
    """Rule «gravity»: every tick, pushes held or not, the fall speed gains gravity up to fall_top. A wing tick has
    its own push instead; with the wings spent and the jump key held the fall caps at a third of fall_top (rule
    «wings»)."""
    if b.get("flying"):
        return
    g = WATER["gravity"] if b["wet"] else phys["gravity"]
    top = WATER["fall_top"] if b["wet"] else phys["fall_top"]
    if "jump" in keys and phys.get("wing_time_max", 0) > 0 and not b["wet"] and not b["ground"] and b["vy"] > 0:
        g, top = g / WINGS["glide"], top / WINGS["glide"]      # the glide: a third of the fall
    b["vy"] = min(f32(b["vy"] + f32(g)), f32(top))
    b["g"] = f32(g)                     # the step down asks whether the fall speed is just this tick's gravity


def move(b, solid, floor, down=False):
    """The move: the body goes by its speed, stopped by solid cells; going down it lands on the first floor (a solid
    cell or a platform) its bottom edge reaches; with down held it falls through platforms (rule «platform»). In water
    it goes half as far, but the game looks for what is in the way at the full speed, and an axis that meets something
    moves all the way to it (the gym's water_sink test: the landing on a pool's floor). Before all that, the step
    (rule «step», below). solid(x, y) and floor(x, y) say what a cell is."""
    k = WATER["move"] if b["wet"] else 1.0
    x, y, vx, vy = b["x"], b["y"], b["vx"], b["vy"]
    # rule «step»: going sideways, standing or falling (fall speed at least gravity), down not held, into a solid cell
    # at the row of its lowest pixel with room above it: the body goes up onto it, by however much its feet are under
    # that cell's top, and stands. 16 px at most: the cliff plan's replay lifted a body 14.1 px under a ledge, the
    # chasm plan's did not lift one 16.9 px under (Oct 18); the step_up test lifts 16
    if vx != 0 and not down and vy >= b.get("g", 0.4):
        lead = int((x + vx + W // 2 + (W // 2 + 1) * (1 if vx > 0 else -1)) // CELL)
        row = int((y + H - 1) // CELL)
        top = row * CELL
        c0, c1 = span(x, W)
        if solid(lead, row) and 0 < y + H - top <= CELL and not any(solid(lead, r) for r in range(row - 4, row)) and \
                not any(solid(c, r) for c in range(c0, c1 + 1) for r in range(int((top - H) // CELL), row)):
            y, vy = float(top - H), 0.0
            b["vy"] = 0.0
    def holds(c, r):
        return solid(c, r) or (floor(c, r) and not down)

    # rule «step» down: a body that stood last tick (its fall speed is just this tick's gravity), walking off a ledge
    # one row high, snaps onto the floor below in the same tick, if that floor's top is 7 to 17 px under its feet
    # (the cliff plan's replay, Oct 18: the game came down 16 px in one tick where the rule fell)
    if not down and vy > 0 and vy == b.get("g", 0.4):
        sx = x + vx
        rr = int((math.floor((y + H) / CELL) * CELL + 4) // CELL)
        best = None
        for c in range(int(sx // CELL), int((sx + W) // CELL) + 1):
            for r in (rr, rr + 1):
                if holds(c, r) and c * CELL < x + W and c * CELL + CELL > x and r * CELL - 17 < y + H and \
                        r * CELL - 1 > y:
                    best = r * CELL if best is None else min(best, r * CELL)
        if best is not None and 7 < best - (y + H) < 17:
            y = float(best - H)
    fx, fy = f32(x + vx), f32(y + vy)                  # where the full speed would take it
    # what is in the way, as the game's TileCollision finds it: every cell the body would overlap at full speed,
    # sorted by where the body was before the move: a cell it was above is a floor (a platform too, down not held),
    # one it was beside is a wall, one it was under is a ceiling (the water shaft's replay, Oct 18: a wall's cells over
    # the head are not a ceiling)
    cvx, cvy, hit_x, hit_y = vx, vy, False, False
    c0, c1 = span(fx, W)
    r0, r1 = span(fy, H)
    for c in range(c0, c1 + 1):
        for r in range(r0, r1 + 1):
            tx, ty = c * CELL, r * CELL
            if not (fx + W > tx and fx < tx + CELL and fy + H > ty and fy < ty + CELL):
                continue
            if solid(c, r):
                if y + H <= ty:
                    cvy, hit_y = min(cvy, ty - (y + H)), True
                elif x + W <= tx:
                    cvx, hit_x = min(cvx, tx - (x + W)), True
                elif x >= tx + CELL:
                    cvx, hit_x = max(cvx, tx + CELL - x), True
                elif y >= ty + CELL:
                    cvy, hit_y = max(cvy, ty + CELL - y), True
            elif holds(c, r) and y + H <= ty and vy > 0:
                cvy, hit_y = min(cvy, ty - (y + H)), True
    # an axis that met something moves all the way to it; a free one moves by its speed (half of it in water)
    ny = f32(y + (cvy if hit_y else f32(vy * k)))
    nx = f32(x + (cvx if hit_x else f32(vx * k)))
    if hit_y:
        # the speed left is the distance moved: the body touches the floor this tick and stands (speed 0) from the
        # next one (the gym's jump and drop traces)
        b["vy"] = f32(cvy)
    if hit_x:
        b["vx"] = f32(cvx)                              # clipped to the wall, as the game does
    b["x"], b["y"] = nx, ny
    feet = round((ny + H) / CELL)
    b["ground"] = b["vy"] == 0 and abs(ny + H - feet * CELL) < 1e-6 and any(
        holds(c, feet) for c in range(span(nx, W)[0], span(nx, W)[1] + 1))


def wet_at(x, y, water):
    """Rule «water_move»: the body counts as in water when a box in its middle, 10 px wide and half its height (21
    px), lies in water; with the game's whole-number halves the box runs from 11 to 32 px under the body's top. A full
    cell's water starts 1/16 px under the cell's top (the gym's water_sink test, Oct 18)."""
    bx, by, bw, bh = x + W // 2 - 5, y + H // 2 - (H // 2) // 2, 10, H // 2
    for cx in range(int(bx // CELL), int((bx + bw) // CELL) + 1):
        for cy in range(int(by // CELL), int((by + bh) // CELL) + 1):
            if water(cx, cy):
                top = cy * CELL + 2 * (256 - 255) / 32
                if bx + bw > cx * CELL and bx < cx * CELL + CELL and by + bh > top and by < cy * CELL + CELL:
                    return True
    return False


def tick(b, keys, phys, solid, floor, water=None, rope=None):
    """One tick, in the game's order: where a fall counts from; the run, the jump and gravity on the numbers of the
    medium the body was in when the tick began; then whether it is in water where it stands now (before it moves);
    then the move, half of it in water (so the move halves a tick before gravity does: the gym's water_sink test);
    then the landing's damage, if it landed. b["hurt"] holds the damage this tick dealt."""
    # rule «fall_damage»: while the body stands, is in water or is still pushed up by the jump, a fall counts from its
    # top row where it is as the tick begins; after the push ends the count stays put, though the body rises a little
    # more (the game's own fallStart, read tick by tick in the gym, Oct 18)
    if b["ground"] or b["wet"] or b["jump"] > 0 or (phys.get("wing_time_max", 0) > 0 and "jump" in keys and not b["ground"]):
        b["fall_start"] = int(b["y"] // CELL)   # wings with the key held: no fall counts (the game's fallStart, Oct 20)
    was_ground = b["ground"]
    if b["ground"] and phys.get("wing_time_max", 0) > 0 and not b.get("rope"):
        b["wing"] = phys["wing_time_max"]        # rule «wings»: filled up on the ground
    if rope is not None and climb(b, keys, rope):
        return b                                 # rule «rope»: the tick was the rope's
    run(b, keys, phys)
    jump(b, keys, phys)
    gravity(b, phys, keys)
    if water:
        b["wet"] = wet_at(b["x"], b["y"], water)
    move(b, solid, floor, "down" in keys)
    b["hurt"] = 0
    if b["ground"] and not was_ground:
        rows = int(b["y"] // CELL) - b["fall_start"]
        b["hurt"] = fall_damage(rows, phys.get("defense", 0), extra=phys.get("extra_fall", 0))
    if b["ground"]:
        b["let_go"] = False                                   # on the ground: a rope can be grabbed again
    return b


def rope_grab(b):
    return int((b["x"] + W / 2) // CELL), int((b["y"] - 8) // CELL)


def climb(b, keys, rope):
    """Rule «rope»: a rope in one of the body's columns and up or down pressed (or the body already hanging) puts
    the body on the rope: no gravity, a climb that gains ROPE acc a tick up to top and creep beyond, a slide of
    ROPE slide a tick on a side key while a rope stays under it, and a jump lets go (handled in jump()). Returns
    whether the tick was the rope's."""
    cc, row = rope_grab(b)
    on = rope(cc, row)
    if not on:
        b["rope"] = False
        return False
    if b.get("let_go") and not b["ground"]:
        return False                                              # jumped off: no grab again on the way down (the gym,
                                                                  # Oct 20 night: down held, released or pressed fresh,
                                                                  # the body fell 36 rows past the rope every time)
    if not b.get("rope") and not ("up" in keys or "down" in keys):
        return False
    if "jump" in keys:
        if b.get("rope"):
            b["let_go"] = True                                    # until the feet are on the ground again
        b["rope"] = False                                         # a jump lets go, and no grab while the jump key is
        return False                                              # held (the game, Oct 20: jumped off and fell past)
    b["rope"] = True
    b["ground"] = True                                            # the game's own test: it can jump from here
    b["jump"] = 0
    b["fall_start"] = int(b["y"] // CELL)
    want = -1 if "up" in keys else 1 if "down" in keys else 0
    v = abs(b["vy"])
    if want:
        if want * b["vy"] < 0:
            v = 0.0                                               # the other way: from a stop
        v = v + ROPE["acc"] if v < ROPE["top"] else v + (ROPE["creep_up"] if want < 0 else ROPE["creep_down"])
        b["vy"] = f32(want * v)
    else:
        v = f32(v * ROPE["fade"])
        b["vy"] = f32(v * (1 if b["vy"] > 0 else -1)) if v >= 0.1 else 0.0
    side = 1 if "right" in keys else -1 if "left" in keys else 0
    b["vx"] = 0.0
    if side:
        # a side key moves the character across the rope, 0.6 px a tick, to 6 px past the rope's centre on that
        # side, never off it (gym, Oct 20: 12 px in 20 ticks, still on the rope after)
        centre, target = b["x"] + W / 2, cc * CELL + CELL / 2 + 6 * side
        step = max(-ROPE["slide"], min(ROPE["slide"], target - centre))
        b["x"] = f32(b["x"] + step)
    b["y"] = f32(b["y"] + b["vy"])
    if b["vy"] < 0 and not rope(cc, int((b["y"] - 8) // CELL)):
        # the top of the rope: the climb stops there, the grab point on the top rope cell (going down past the
        # end the character lets go and falls, measured; going up the game holds it at the top)
        b["y"] = f32((int((b["y"] - 8) // CELL) + 1) * CELL + 8)
        b["vy"] = 0.0
    b["hurt"] = 0
    return True


def fall_damage(rows, defense=0, extra=0):
    """Rule «fall_damage»: 10 for each row past 25 (and past the gear's extra_fall), less half the defense rounded up
    (terraria.wiki.gg/wiki/Fall_damage; the gym's drops of 20, 30, 35 and 40 rows, and 35 in iron armor). Where a
    fall counts from, and so what water does, is in tick()."""
    raw = 10 * (rows - 25 - extra)
    return max(1, raw - math.ceil(defense / 2)) if raw > 0 else 0


def cells_of(b):
    """The body's first and last column, its top row and its feet's row."""
    return span(b["x"], W) + span(b["y"], H)
