"""The weapons' rules: what a shot or a swing does, played forward a tick at a time. The numbers are the items' own
(the mod's /state bag, with player-adjusted values in combat), with what the gym measured:

    a shot     uses the engine's chosen ammo, combined damage, speed and projectile properties. Its flight is
               stepped in projectiles.py, including extra updates and collisions. Future random spread, damage
               rolls and Crystal Bullet fragments are unknown; existing fragments come from the observation.
    a hit      does the weapon's damage (plus the ammo's) less half the enemy's defense, at least 1: the mean of the
               game's ±15% spread (an iron shortsword's 8 on a zombie: 5, seen as 6)
    a stab     the shortsword projectile extends from the player along the cursor angle for eight game ticks
               (Projectile.AI_161_RapierStabs and Colliding, checked with copper and iron shortswords in Gym)
    knockback  NPC.GetIncomingStrikeModifiers and StrikeNPC: resistance, diminishing returns, and a push that
               depends on damage relative to maximum life, existing momentum, gravity, and critical hits.
               Checked against the installed Terraria 1.4.4.9 / tModLoader engine, Nov 4.
"""
import json
from math import hypot
import os

STAB_TICKS = 8
# rule «swing» (Oct 20): a swung weapon's hitbox per animation tick is the engine's own (Player.ItemCheck_GetMeleeHitbox,
# handed to the mod's UseItemHitbox hook), recorded once per weapon by one swing each way and kept here: by item id,
# then facing (1 right, -1 left), then the game's itemAnimation tick (use_anim down to 1): [x, y, w, h] relative to
# the body's top-left. An enemy is hit once per swing when the rectangle overlaps it. The swing goes the way the
# body faces, and the left and right keys set the facing, not the cursor (the engine, through the mod's inputs:
# aimed left with no key the body kept facing right, 3 trials each way, Oct 20).
# Nov 4 adds Gold and Adamantite sword recordings. The current animation duration and scale select the box;
# Player.attackCD and meleeNPCHitCooldown prevent predicting a repeat hit during an ongoing swing.
SWINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swings.json")
SWINGS = json.load(open(SWINGS_PATH)) if os.path.exists(SWINGS_PATH) else {}


def swing_of(item):
    """The recorded swing of this item, if available."""
    item = effective(item)
    if item.get("no_melee"):
        return None
    if item.get("draw_frame") and item.get("use_style") == 1:
        return {"engine": item, "use_anim": item["use_anim"], "scale": item["scale"]}
    sw = SWINGS.get(str(item.get("id")))
    return sw if sw and sw.get("use_anim") else None


def effective(item):
    """Weapon values adjusted by the game for the current player; old recordings retain their base values."""
    return dict(item, **item["combat"]) if item.get("combat") else item


def swing_box(sw, k, dirn, duration=None, scale=1, *, origin=(0, 0), gravity_dir=1, height_offset=0):
    """The hitbox at animation tick k facing dirn, relative to the body's top-left, or None for a tick with none."""
    if "engine" in sw:
        return engine_swing_box(sw["engine"], k, dirn, duration or sw["use_anim"], scale,
                                origin, gravity_dir, height_offset)
    boxes = sw["1" if dirn > 0 else "-1"]
    phase = lambda left, total: 0 if left < total * .333 else 1 if left < total * .666 else 2
    p = phase(k, duration or sw["use_anim"])
    box = boxes.get(str(k)) if duration is None or duration == sw["use_anim"] else next(
        (box for left, box in boxes.items() if phase(int(left), sw["use_anim"]) == p), None)
    if box is None or scale == sw.get("scale", 1):
        return box
    # ItemCheck_GetMeleeHitbox scales the blade around the same hand location.
    # Recover that location from the recorded box before applying the current scale.
    middle = next(box for left, box in boxes.items() if phase(int(left), sw["use_anim"]) == 1)
    w, h = middle[2:]
    x, y = box[:2]
    if dirn < 0:
        x += w + (int(w * 1.4 - w) if p == 0 else 0)
    elif p == 2:
        x += int(w * 1.2)
    y += h - int(h * .5) if p == 0 else h + int(h * 1.4 - h) if p == 2 else h
    w, h = int(w * scale / sw.get("scale", 1)), int(h * scale / sw.get("scale", 1))
    x -= w if dirn < 0 else 0
    y -= h
    if p == 0:
        if dirn < 0:
            x -= int(w * 1.4 - w)
        return [x, y + int(h * .5), int(w * 1.4), int(h * 1.1)]
    if p == 2:
        if dirn > 0:
            x -= int(w * 1.2)
        return [x, y - int(h * 1.4 - h), w * 2, int(h * 1.4)]
    return [x, y, w, h]


def engine_swing_box(item, left, direction, duration, scale, origin, gravity_dir, height_offset):
    """Player.ItemCheck_ApplyUseStyle and ItemCheck_GetMeleeHitbox,1.4.4.9.

    Sprite frame and adjusted scale come from the game. The 3 phases, hand
    offsets, claw handling and special blade offsets are the engine's rules.
    """
    from rules.body import f32
    fw, fh = item["draw_frame"]
    phase = 0 if left < duration * .333 else 1 if left < duration * .666 else 2
    claw = item.get("claw", False)
    if claw:
        offset, hand_y = ((10, 26), (8, 24), (6, 20))[phase]
    else:
        offset = (10, 10, 6)[phase]
        for width, value in (((32, 14), (52, 24), (64, 28), (92, 38)) if phase == 0 else
                             ((32, 18), (52, 24), (64, 28), (92, 38)) if phase == 1 else
                             ((32, 14), (48, 18), (52, 24), (64, 28), (92, 38))):
            if fw > width if width == 32 else fw >= width:
                offset = value
        hand_y = 24 if phase == 0 else 10
        if phase:
            for height, value in ((32, 8 if phase == 1 else 10), (52, 12), (64, 14)):
                if fh > height:
                    hand_y = value
        if item["id"] in (2330, 2320, 2341):
            offset += 8 if phase == 0 else 4
            if phase:
                hand_y += 4
        if item["id"] == 671:
            offset += (12, 6, 8)[phase]
            if phase:
                hand_y += 10 if phase == 1 else 8
    bx, by = origin
    hand_x = f32(f32(bx + 10) + f32((fw * .5 - offset) * direction * (-1 if phase == 2 else 1)))
    hand_y = f32(f32(by + hand_y) + height_offset)
    if not claw and item.get("better_melee_location", False):
        sx, sy = ((-4, 1), (-6, -4), (4, -2))[phase]
        hand_x, hand_y = f32(hand_x + sx * direction), f32(hand_y + sy * gravity_dir)
    if gravity_dir == -1:
        hand_y = f32(f32(by + 42) + f32(by - hand_y))
    trim = {5094: 10, 5095: 10, 5096: 12, 5097: 8}.get(item["id"], 0)
    width, height = int(f32((fw - trim) * scale)), int(f32((fh - trim) * scale))
    x, y = int(hand_x), int(hand_y)
    if direction == -1:
        x -= width
    if gravity_dir == 1:
        y -= height
    if phase == 0:
        if direction == -1:
            x -= int(width * 1.4 - width)
        width = int(width * 1.4)
        y += int(height * .5 * gravity_dir)
        height = int(height * 1.1)
    elif phase == 2:
        if direction == 1:
            x -= int(width * 1.2)
        width *= 2
        y -= int((height * 1.4 - height) * gravity_dir)
        height = int(height * 1.4)
    return [x - bx, y - by, width, height]


def way(item):
    """How an item hits, from its game properties rather than weapon names."""
    item = effective(item)
    if item.get("use_ammo"):
        return "shoot"
    if item.get("damage", 0) <= 0 or item.get("pick") or item.get("axe") or item.get("hammer"):
        return None
    if item.get("use_style") == 13:
        return "stab"
    if item.get("use_style") == 1 and not item.get("no_melee", False):
        return "swing"
    if item.get("shoot", 0) > 0 and not item.get("shot", {}).get("minion") and not item.get("shot", {}).get("sentry"):
        return "shoot"
    return None


def affordable(state, item, ammo=None, used=None, mana=None):
    """Whether the observed resources cover one use of this item."""
    item = effective(item)
    used = used or {}
    if item.get("use_ammo") and (ammo is None or used.get(ammo["slot"], 0) >= ammo["stack"]):
        return False
    if item.get("consumable") and used.get(item["slot"], 0) >= item["stack"]:
        return False
    if (state["body"].get("mana", 0) if mana is None else mana) < item.get("mana_cost", 0):
        return False
    return True


def ammo_for(bag, item):
    """Use the engine's ammo selection; older recordings use slot priority."""
    fits = [a for a in bag if a.get("ammo") == item.get("use_ammo") and a["stack"] > 0]
    chosen = effective(item).get("shot", {}).get("ammo_slot")
    if chosen is not None:
        return next((a for a in fits if a["slot"] == chosen), None)
    fits.sort(key=lambda a: (0 if a["slot"] >= 54 else 1, a["slot"]))
    return fits[0] if fits else None


def hit_damage(damage, enemy_defense):
    return max(1, int(damage - enemy_defense * .5))


def stab_overlaps(cx, cy, ux, uy, speed, age, enemy):
    """Check the swept thrust against the enemy box. The projectile updates twice per game tick."""
    x = enemy["x"] + enemy["w"] / 2 - cx
    y = enemy["y"] + enemy["h"] / 2 - cy
    half_w, half_h = enemy["w"] / 2, enemy["h"] / 2
    start, end = age * 2 * speed, (age * 2 + 7) * speed
    # Project both boxes onto the thrust's axes and the world axes.
    along = x * ux + y * uy
    extent = half_w * abs(ux) + half_h * abs(uy)
    if along + extent < start or along - extent > end:
        return False
    across = y * ux - x * uy
    if abs(across) > 5 + half_w * abs(uy) + half_h * abs(ux):
        return False
    middle, radius = (start + end) / 2, (end - start) / 2
    return (abs(x - ux * middle) <= half_w + radius * abs(ux) + 5 * abs(uy)
            and abs(y - uy * middle) <= half_h + radius * abs(uy) + 5 * abs(ux))


def stab_direction(cx, cy, vx, vy, enemy, speed):
    """Lead a nearby enemy to the first tick at which this thrust can reach it."""
    ex, ey = enemy["x"] + enemy["w"] / 2, enemy["y"] + enemy["h"] / 2
    reach = (2 * (STAB_TICKS - 1) + 7) * speed + 5
    dvx, dvy = enemy["vx"] - vx, enemy["vy"] - vy
    if (abs(ex - cx) > reach + enemy["w"] / 2 + abs(dvx) * STAB_TICKS
            or abs(ey - cy) > reach + enemy["h"] / 2 + abs(dvy) * STAB_TICKS):
        return None
    for age in range(STAB_TICKS):
        dx, dy = ex - cx + dvx * age + enemy["vx"], ey - cy + dvy * age + enemy["vy"]
        length = hypot(dx, dy)
        if length <= (age * 2 + 7) * speed + min(enemy["w"], enemy["h"]) / 2:
            return (dx / length, dy / length) if length else (1, 0)
    return None


def shot(item, ammo, cx, cy, aim_x, aim_y):
    """A shot using the engine's ammo-adjusted projectile properties."""
    prototype = effective(item)["shot"]
    s = dict(prototype)
    speed = s.pop("speed")
    dx, dy = aim_x - cx, aim_y - cy
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    s.update(x=cx - s["w"] / 2, y=cy - s["h"] / 2, vx=speed * dx / n, vy=speed * dy / n,
             friendly=True, hostile=False)
    return s


def shot_speed(item, ammo):
    """Travel per game tick, including the projectile's extra updates."""
    prototype = effective(item)["shot"]
    return prototype["speed"] * (1 + prototype["extra_updates"])


def apply_knockback(enemy, knockback, direction, damage, *, crit=False):
    """The engine's NPC.StrikeNPC push, using mean damage and a noncritical hit in forecasts."""
    resist = enemy.get("kb_taken", 0.5)
    enemy["just_hit"] = True
    push = max(0, knockback * resist)
    if not push:
        return
    for threshold, share in ((8, .9), (10, .8), (12, .7), (14, .6)):
        if push > threshold:
            push = threshold + (push - threshold) * share
    push = min(16, push) * (1.4 if crit else 1)
    if int(damage) * (15 if enemy.get("expert") else 10) > enemy.get("life_max", enemy["life"]):
        speed = enemy["vx"] * direction
        if speed < push:
            speed += push * (2 if speed < 0 else 1)
            enemy["vx"] = direction * min(speed, push)
        lift = -push * (.5 if enemy.get("flies") else .75)
        if enemy["vy"] > lift:
            enemy["vy"] = max(lift, enemy["vy"] + lift)
    else:
        enemy["vx"] = push * direction * resist
        enemy["vy"] = -push * (.5 if enemy.get("flies") else .75) * resist


def intercept(cx, cy, speed, future):
    """Where to aim a shot of this speed from (cx, cy) at a moving target: future(k) gives the target's centre k ticks
    on. The aim is the first predicted place the shot can reach in time (within 60 ticks); failing that, the
    target's place now."""
    for k in range(1, 61):
        tx, ty = future(k)
        d = ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5
        if d <= speed * k:
            return tx, ty
    return future(0)
