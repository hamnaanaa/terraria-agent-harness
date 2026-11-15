"""Contact damage, hurt effects and observed-motion enemy physics."""
import math

from rules import sight, motion

CELL = 16


def touch_damage(e, defense, eff=0.5, reduction=0):
    """Rule «touch»: the mean of the spread, less the defense times its share (the mod's defense_eff: 0.5, Expert
    0.75, Master 1), at least 1."""
    return max(1, int(max(1, e["damage"] - defense * eff) * max(0, 1 - reduction)))


def shot_damage(s, strength, defense, eff=0.5, reduction=0):
    """Rule «shot»: an enemy's shot, twice its damage times the world's multiplier (the mod's strength), less the
    defense times its share, at least 1."""
    return max(1, int(max(1, s["damage"] * 2 * strength - defense * eff) * max(0, 1 - reduction)))


def hurt_immunity(damage, body):
    """Player.Hurt: one-damage hits get 20 ticks, other hits 40; longInvince doubles it."""
    return (20 if damage == 1 else 40) * (2 if body.get("long_invince", False) else 1)


def hurt_slot(source):
    slot = source.get("hurt_slot", -1)
    return -1 if slot == 2 else slot  # Player.Hurt maps slot 2 to general immunity.


def protection(body, source):
    slot = hurt_slot(source)
    if slot < 0:
        return body.get("immune_time", 0) if body.get("immune") else 0
    counters = body.get("hurt_cooldowns", ())
    return counters[slot] if slot < len(counters) else 0


def proximity(body, source, gap, phys, reaction_ticks, ranged=False):
    """Fraction of a reaction-and-escape buffer used up, capped at zero outside."""
    if gap is None:
        return 0.0
    dx = body["x"] + 10 - source["x"] - source["w"] / 2
    dy = body["y"] + 21 - source["y"] - source["h"] / 2
    distance = math.hypot(dx, dy)
    updates = 1 + source.get("extra_updates", 0)
    vx, vy = source["vx"] * updates, source["vy"] * updates
    closing = max(0, (vx * dx + vy * dy) / distance,
                  ((vx - body["vx"]) * dx + (vy - body["vy"]) * dy) / distance) if distance else 0
    acc, speed = phys["run_acc"], phys["run_top"]
    if ranged:
        closing = max(closing, speed, math.hypot(vx, vy))
    clear = min(20 + source["w"], 42 + source["h"]) / 2
    if acc > 0 and speed > 0:
        ramp = speed * speed / (2 * acc)
        escape = math.sqrt(2 * clear / acc) if clear <= ramp else speed / acc + (clear - ramp) / speed
    else:
        escape = float("inf")
    needed = max(clear, closing * (reaction_ticks + escape)) if closing else clear
    return max(0, 1 - gap / needed)


def hurt_recoil(body, direction, observed):
    """Player.Hurt uses 4.5 knockback, unless equipment makes the player immune."""
    if observed.get("no_knockback", False) or observed.get("cart", False) or not direction:
        return False
    body.update(vx=4.5 * direction, vy=-3.5, jump=0, ground=False, rope=False, hurt=0,
                fall_start=int(body["y"] // CELL))
    return True


def hurt_factor(observed):
    """Player.ApplyVanillaHurtEffectModifiers, using current equipment charges."""
    factor = max(0, 1 - observed.get("damage_reduction", 0))
    if observed.get("solar_set") and observed.get("solar_shields", 0) > 0:
        factor *= .8
    if observed.get("beetle_defense") and observed.get("beetle_orbs", 0) > 0:
        factor *= 1 - .15 * observed["beetle_orbs"]
    return factor


def hurt_consumes(observed):
    """Player.OnHurt_Part2 consumes a charged armor shield on a real hit."""
    if observed.get("solar_set") and observed.get("solar_shields", 0) > 0:
        observed["solar_shields"] -= 1
    if observed.get("beetle_defense") and observed.get("beetle_orbs", 0) > 0:
        observed["beetle_orbs"] -= 1


def overlaps(ax, ay, aw, ah, bx, by, bw, bh):
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def move_box(x, y, w, h, vx, vy, solid, floor, shape=None):
    """A box moved by its speed and stopped by cells, the way the game's TileCollision does it: a cell the box was
    above is a floor (platforms too), beside a wall, under a ceiling. Returns x, y, vx, vy, on_ground."""
    fx, fy = sight.f32(x + vx), sight.f32(y + vy)
    cvx, cvy, hit_x, hit_y = vx, vy, False, False
    for c in range(int(fx // CELL), int(math.ceil((fx + w) / CELL))):
        for r in range(int(fy // CELL), int(math.ceil((fy + h) / CELL))):
            tx, ty = c * CELL, r * CELL
            bottom = ty + CELL
            tile_shape = shape(c, r) if shape is not None else 0
            if tile_shape == 5:
                ty += CELL / 2
            if not (fx + w > tx and fx < tx + CELL and fy + h > ty and fy < bottom):
                continue
            if solid(c, r):
                # TileCollision leaves these slope approaches open. Ground movers have
                # a separate slope step, which this flight collision does not model.
                if 1 <= tile_shape <= 4:
                    inside = x >= tx if tile_shape in (1, 3) else x + w <= tx + CELL
                    if inside and (y + h - abs(vx) <= bottom if tile_shape <= 2 else y + abs(vx) >= ty):
                        continue
                if y + h <= ty:
                    if tile_shape not in (1, 2):
                        cvy, hit_y = min(cvy, ty - (y + h)), True
                elif x + w <= tx:
                    if shape is None or shape(c - 1, r) not in (2, 4):
                        cvx, hit_x = min(cvx, tx - (x + w)), True
                elif x >= tx + CELL:
                    if shape is None or shape(c + 1, r) not in (1, 3):
                        cvx, hit_x = max(cvx, tx + CELL - x), True
                elif y >= bottom:
                    cvy, hit_y = max(cvy, bottom - y), True
            elif floor(c, r) and y + h <= ty and vy > 0:
                cvy, hit_y = min(cvy, ty - (y + h)), True
    nx, ny = x + cvx, y + cvy
    ground = hit_y and vy > 0
    return nx, ny, (0.0 if hit_x else vx), (0.0 if hit_y else vy), ground



def tick(e, body_cx, body_cy, solid, floor, water=None, door=None, shape=None, *, cache=None):
    """Continue measured motion using the entity's reported physical properties."""
    motion.advance(e, solid, floor, shape,
                   gravity=0 if e.get("flies") else e.get("gravity", 0),
                   fall_top=e.get("fall_top", float("inf")),
                   tile_collide=not e.get("through_blocks", False), cache=cache)
