"""The weapons' rules: what a shot or a swing does, played forward a tick at a time. The numbers are the items' own
(the mod's /state bag: damage, shoot_speed, use_time, knockback), with what the gym measured (Oct 18):

    an arrow   leaves the body's centre at the bow's shoot_speed plus the arrow's (a wooden bow and arrow: 9.1 px a
               tick) toward the aim, flies flat for 15 ticks, then drops 0.1 px a tick faster each tick; one every
               use_time ticks (30 for the wooden bow)
    a hit      does the weapon's damage (plus the ammo's) less half the enemy's defense, at least 1: the mean of the
               game's ±15% spread (an iron shortsword's 8 on a zombie: 5, seen as 6)
    a stab     an iron shortsword hits an enemy within about 30 px of the body's edge on the side it faces, 12 ticks
               after the use key goes down, then every use_time ticks
    knockback  a hit leaves the enemy moving away at 0.45 times the hit's knockback times the share it takes (1 less
               its resistance): a wooden arrow (2) on a zombie (takes half) 0.45 px a tick, an iron shortsword (4)
               0.9; it then turns back at its own 0.07 a tick (the gym, Oct 18)
"""
import json
import os

ARROW_FLAT_TICKS, ARROW_GRAVITY = 15, 0.1
STAB_REACH_PX = 30
KNOCK_SCALE = 0.45
# rule «swing» (Oct 20): a swung weapon's hitbox per animation tick is the engine's own (Player.ItemCheck_GetMeleeHitbox,
# handed to the mod's UseItemHitbox hook), recorded once per weapon by one swing each way and kept here: by item id,
# then facing (1 right, -1 left), then the game's itemAnimation tick (use_anim down to 1): [x, y, w, h] relative to
# the body's top-left. An enemy is hit once per swing when the rectangle overlaps it. The swing goes the way the
# body faces, and the left and right keys set the facing, not the cursor (the engine, through the mod's inputs:
# aimed left with no key the body kept facing right, 3 trials each way, Oct 20).
SWINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swings.json")
SWINGS = json.load(open(SWINGS_PATH)) if os.path.exists(SWINGS_PATH) else {}


def swing_of(item):
    """The recorded swing of this item, or None (then the stab's reach stands in)."""
    sw = SWINGS.get(str(item.get("id")))
    return sw if sw and sw.get("use_anim") else None


def swing_box(sw, k, dirn):
    """The hitbox at animation tick k facing dirn, relative to the body's top-left, or None for a tick with none."""
    return sw["1" if dirn > 0 else "-1"].get(str(k))


def way(item):
    """How an item hits: shoot (it takes ammo), stab (a shortsword), swing (a broadsword), or None."""
    if item.get("use_ammo"):
        return "shoot"
    if item.get("damage", 0) <= 0 or item.get("pick") or item.get("axe") or item.get("hammer"):
        return None
    if item.get("use_style") == 13:
        return "stab"
    if item.get("use_style") == 1:
        return "swing"
    return None


def ammo_for(bag, item):
    """Rule «ammo»: the first ammo of the right kind in the ammo slots (54 to 57), then by slot."""
    fits = [a for a in bag if a.get("ammo") == item.get("use_ammo") and a["stack"] > 0]
    fits.sort(key=lambda a: (0 if a["slot"] >= 54 else 1, a["slot"]))
    return fits[0] if fits else None


def hit_damage(damage, enemy_defense):
    return max(1, damage - enemy_defense // 2)


def shot(item, ammo, cx, cy, aim_x, aim_y):
    """A new arrow: where it starts and its speed."""
    speed = item["shoot_speed"] + ammo["shoot_speed"]
    dx, dy = aim_x - cx, aim_y - cy
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    return {"x": cx - 5, "y": cy - 5, "w": 10, "h": 10, "vx": speed * dx / n, "vy": speed * dy / n, "age": 0,
            "damage": item["damage"] + ammo["damage"], "knockback": item["knockback"] + ammo["knockback"]}


def shot_tick(s):
    s["age"] += 1
    if s["age"] > ARROW_FLAT_TICKS:
        s["vy"] += ARROW_GRAVITY
    s["x"] += s["vx"]
    s["y"] += s["vy"]


def knock(knockback, enemy):
    """The speed a hit leaves the enemy moving away, px a tick."""
    return KNOCK_SCALE * knockback * (1.0 - enemy.get("kb_taken", 0.5))


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
