"""Advance a projectile observed in the game by one projectile update."""
from rules.enemies import move_box, overlaps
from rules.sight import walk_hit, BLOCK, SHAPED, OPEN
from rules.body import f32, W, H
import json
from pathlib import Path
from math import atan2, cos, sin, pi, hypot

_CATALOG = json.loads(Path(__file__).with_name("projectile-types.json").read_text())
_WEAPONS = json.loads(Path(__file__).with_name("weapon-behaviors.json").read_text())
_HOMING = {207: 8, 837: 32}  # Projectile.AI_001, Terraria 1.4.4.9.
_FLIGHT = ("x", "y", "vx", "vy", "time_left", "_age", "ai0", "ai1", "scale")


def observe_shapes(state):
    """Index optional observed danger cells once, shared by all move forecasts."""
    data = state.get("attack_shapes")
    if not data:
        return state
    if data["step"] > min(data["target_size"]):
        raise ValueError("attack samples must cover the gaps between grid points")
    shapes = {}
    for shape in data["shapes"]:
        regions = [dict(origin=data["origin"], cols=data["cols"], rows=data["rows"], runs=shape["runs"]),
                   *shape.get("patches", ())]
        step = data["step"]
        origin = [min(r["origin"][axis] for r in regions) for axis in (0, 1)]
        cols = max((r["origin"][0] - origin[0]) // step + r["cols"] for r in regions)
        height = max((r["origin"][1] - origin[1]) // step + r["rows"] for r in regions)
        rows = [[] for _ in range(height)]
        masks, measured = [0] * height, [0] * height
        for region in regions:
            dx, dy = [(region["origin"][axis] - origin[axis]) // step for axis in (0, 1)]
            if any((region["origin"][axis] - origin[axis]) % step for axis in (0, 1)):
                raise ValueError("attack sample grids must be aligned")
            known = ((1 << region["cols"]) - 1) << dx
            for r in range(dy, dy + region["rows"]):
                measured[r] |= known
            for row, col, count in region["runs"]:
                rows[row+dy].append((col+dx, col+dx+count))
                masks[row+dy] |= ((1 << count) - 1) << (col+dx)
        coverage = []
        known = []
        for r, mask in enumerate(masks):
            adjacent = mask | (masks[r+1] if r+1 < len(masks) else 0)
            coverage.append(adjacent | adjacent >> 1)
            measured_square = measured[r] & (measured[r+1] if r+1 < len(measured) else 0)
            known.append(measured_square & measured_square >> 1)
        shapes[shape["i"], shape["id"]] = dict(
            rows=rows, cols=cols, step=step, origin=origin,
            coverage=coverage, known=known,
            bounds=[(r-1, r+1, lo-1, hi) for r, runs in enumerate(rows) for lo, hi in runs],
            anchor=(shape["x"], shape["y"]))
    return dict(state, shots=[dict(s, _attack=shapes[s["i"], s["id"]])
                             if (s["i"], s["id"]) in shapes else s for s in state["shots"]])


def hits_player(shot, player):
    """True/False for observed geometry; None when the samples do not cover it."""
    area = shot.get("_attack")
    if area is None:
        return overlaps(player["x"], player["y"], W, H,
                        shot["x"], shot["y"], shot["w"], shot["h"])
    # These cells already include the player's footprint. Translate with the
    # unchanged motion forecast; keep the measured orientation until next read.
    col = int((player["x"] - shot["x"] + area["anchor"][0] - area["origin"][0]) // area["step"])
    row = int((player["y"] - shot["y"] + area["anchor"][1] - area["origin"][1]) // area["step"])
    return _shape_hit(area, row, col)


def _shape_hit(area, row, col):
    if not (0 <= row < len(area["rows"]) and 0 <= col < area["cols"]):
        return None
    # The four surrounding player-sized samples, indexed once at observation.
    if area["coverage"][row] & (1 << col):
        return True
    return False if area["known"][row] & (1 << col) else None


def clearance(shot, player, cache=None):
    """Distance to the observed attack footprint, shared by matching forecasts."""
    area = shot.get("_attack")
    if area is None:
        dx = max(0, shot["x"] - player["x"] - W, player["x"] - shot["x"] - shot["w"])
        dy = max(0, shot["y"] - player["y"] - H, player["y"] - shot["y"] - shot["h"])
        return hypot(dx, dy)
    col = int((player["x"] - shot["x"] + area["anchor"][0] - area["origin"][0]) // area["step"])
    row = int((player["y"] - shot["y"] + area["anchor"][1] - area["origin"][1]) // area["step"])
    key = ("clearance", id(area), row, col)
    if cache is not None and key in cache:
        return cache[key]
    hit = _shape_hit(area, row, col)
    gap = None if hit is None else 0.0 if hit else float("inf")
    if gap == float("inf"):
        # Each run is the same player-sized coverage used by hits_player.
        square = float("inf")
        for top, bottom, left, right in area["bounds"]:
            dy = top-row if row < top else row-bottom if row > bottom else 0
            if dy*dy >= square:
                continue
            dx = left-col if col < left else col-right if col > right else 0
            value = dx*dx + dy*dy
            if value < square:
                square = value
        gap = square**.5 * area["step"]
    if cache is not None:
        cache[key] = gap
    return gap


def single_active(item, shots, owner):
    proto = item.get("shot", {})
    one = proto.get("ai") in (3, 9, 15, 19, 99, 165) or proto.get("whip")
    return one and any(s.get("id") == proto.get("id") and s.get("owner", owner) == owner
                       and s.get("time_left", 1) > 0 for s in shots)


def collides(shot, enemy):
    if shot.get("_no_hit") or shot.get("minion") or shot.get("sentry"): return False
    points = shot.get("points")
    if points is not None:
        return any(overlaps(x-shot["w"]/2, y-shot["h"]/2, shot["w"], shot["h"],
                            enemy["x"], enemy["y"], enemy["w"], enemy["h"]) for x,y in points)
    return overlaps(shot["x"], shot["y"], shot["w"], shot["h"],
                    enemy["x"], enemy["y"], enemy["w"], enemy["h"])


def whip_points(s, owner, age):
    """Projectile.FillWhipControlPoints, using the game's exported whip settings."""
    total = s.get("duration", 30) * (1+s.get("extra_updates", 0))
    t = age / total
    segments = s["whip_segments"]
    direction = 1 if s["vx"] >= 0 else -1
    bend = pi*10*(1-t*1.5)*-direction/segments
    extension = t*1.5
    returning = max(0, (extension-1)/.5)
    if extension > 1: extension = 1-returning
    length = hypot(s["vx"],s["vy"])*s.get("base_duration",s.get("duration",30))*2*t
    length *= s.get("whip_multiplier",1)*extension*s["whip_range"]/segments
    ox,oy = owner.get("arm_offset",(10,21))
    ax,ay = owner["x"]+ox,owner["y"]+oy
    a=b=c=(ax,ay)
    aa,ab,ac=-pi/2,pi/2+pi/2*direction,pi/2
    rotation = atan2(s["vy"],s["vx"])+pi/2+1.5*pi*returning**2*direction
    points=[(ax,ay)]
    mix=(1-(1-extension)**2)
    for i in range(segments):
        turn=bend*i/segments
        na=(a[0]+cos(aa)*length,a[1]+sin(aa)*length)
        nc=(c[0]+cos(ac)*length*2,c[1]+sin(ac)*length*2)
        nb=(b[0]+cos(ab)*length*2,b[1]+sin(ab)*length*2)
        u=mix*.9+.1;v=mix*.7+.3
        mid=(nc[0]+(na[0]-nc[0])*u,nc[1]+(na[1]-nc[1])*u)
        end=(nb[0]+(mid[0]-nb[0])*v,nb[1]+(mid[1]-nb[1])*v)
        x,y=end[0]-ax,(end[1]-ay)*1.5
        points.append((ax+x*cos(rotation)-y*sin(rotation),ay+x*sin(rotation)+y*cos(rotation)))
        aa+=turn;ab+=turn;ac+=turn;a,b,c=na,nb,nc
    return points


def spawn(type_id, cx, cy, vx, vy, damage, knockback=0, **changes):
    """NewProjectile properties from the installed engine's ContentSamples; no invented dimensions."""
    p = _CATALOG["types"][str(type_id)]
    shot = {"id": type_id, "ai": p["ai_style"], "w": p["width"], "h": p["height"],
            "x": f32(cx - p["width"] / 2), "y": f32(cy - p["height"] / 2),
            "vx": f32(vx), "vy": f32(vy), "damage": damage, "knockback": knockback,
            "ai0": 0, "ai1": 0, "max_penetrate": p["penetrate"],
            **{k: v for k, v in p.items() if k not in ("type", "ai_style", "width", "height", "max_penetrate")}}
    shot.update(changes)
    return shot

SAND_BALL = 31  # ProjectileID.SandBallFalling, also used for the Antlion's shot.
# Projectile.AI_001 in the installed 1.4.4.9 engine. Bullets and Jester's
# Arrows keep their velocity; these ordinary arrows acquire gravity.
ARROW_GRAVITY = {1: (15, .1), 2: (15, .1), 4: (15, .1), 41: (15, .1),
                 82: (15, .1), 172: (17, .085), 176: (15, .05), 81: (20, .07), 91: (20, .07),
                 174: (5, .15), 605: (5, .15), 920: (5, .15), 921: (5, .15), 926: (5, .15)}


def home(shot, targets, solid, shape):
    """Projectile.AI_001, Terraria 1.4.4.9: Chlorophyte/Nano Bullet steering.

    This is our weapon's flight rule. Targets keep their observed motion.
    Acquisition is 300 Manhattan pixels with Collision.CanHit; an existing
    lock steers within 1000 pixels. Inertia is 8 (207), or 32 (837).
    """
    vx, vy = shot["vx"], shot["vy"]
    local = list(shot.get("local_ai", (0, 0)))
    if not local[0]:
        local[0] = f32(f32(f32(vx * vx) + f32(vy * vy)) ** .5)
    shot["local_ai"] = local
    cx, cy = f32(shot["x"] + shot["w"] // 2), f32(shot["y"] + shot["h"] // 2)
    valid = lambda e: e["life"] > 0 and not e.get("dont_take_damage", False) and e.get("chaseable", e.get("life_max", e["life"]) > 5)
    if not shot.get("ai1", 0):
        nearest = 300
        cell = lambda x, y: (SHAPED if shape and shape(x, y) else BLOCK) if solid(x, y) else OPEN
        for e in targets:
            if not valid(e):
                continue
            ex, ey = f32(e["x"] + e["w"] // 2), f32(e["y"] + e["h"] // 2)
            distance = f32(abs(f32(cx - ex)) + abs(f32(cy - ey)))
            if distance < nearest and walk_hit(int(cx) // 16, int(cy) // 16, int(ex) // 16, int(ey) // 16, cell):
                nearest, shot["ai1"] = distance, e["i"] + 1
    target = next((e for e in targets if e["i"] + 1 == shot.get("ai1", 0)), None)
    if target is None or not valid(target):
        shot["ai1"] = 0
        return vx, vy
    dx, dy = f32(f32(target["x"] + target["w"] // 2) - cx), f32(f32(target["y"] + target["h"] // 2) - cy)
    if f32(abs(dx) + abs(dy)) >= 1000:
        return vx, vy
    length = f32(f32(f32(dx * dx) + f32(dy * dy)) ** .5)
    if not length:
        return vx, vy
    scale = f32(local[0] / length)
    inertia = _HOMING[shot["id"]]
    return (f32(f32(f32(vx * (inertia - 1)) + f32(dx * scale)) / inertia),
            f32(f32(f32(vy * (inertia - 1)) + f32(dy * scale)) / inertia))


def advance(shot, solid, shape, *, owner=None, using=False, cache=None, targets=()):
    """Return whether the shot can still hit during this update."""
    if shot.get("hostile"):
        from rules.motion import shot_tick
        return shot_tick(shot, solid, shape, cache)
    for key,value in list(shot.get("_hits", {}).items()):
        if value > 0: shot["_hits"][key] = value-1
    # AI_001 bullets/arrows fly independently of the owner's controls. The
    # caller shares this cache only while its terrain is unchanged. Homing
    # adds target positions and eligibility, so a kill or knockback branches it.
    if cache is not None and shot.get("friendly") and shot.get("ai") == 1 and not shot.get("whip"):
        foes = tuple((e["i"], e["x"], e["y"], e["w"], e["h"], e["life"] > 0,
                      e.get("chaseable"), e.get("dont_take_damage", False), e.get("life_max", e["life"]) > 5)
                     for e in targets) if shot["id"] in _HOMING else ()
        local = tuple(shot["local_ai"]) if "local_ai" in shot else None
        key = ("bullet", shot["id"], shot["w"], shot["h"], shot.get("tile_collide"),
               tuple(shot.get(k) for k in _FLIGHT), local, foes)
        cached = cache.get(key)
        if cached is None:
            alive = _advance_friendly(shot, solid, shape, owner, using, targets)
            cache[key] = (alive, tuple(shot.get(k) for k in _FLIGHT),
                          tuple(shot["local_ai"]) if "local_ai" in shot else None)
            return alive
        alive, values, local = cached
        for field, value in zip(_FLIGHT, values):
            if value is None: shot.pop(field, None)
            else: shot[field] = value
        if local is None: shot.pop("local_ai", None)
        else: shot["local_ai"] = list(local)
        return alive
    return _advance_friendly(shot, solid, shape, owner, using, targets)


def _advance_friendly(shot, solid, shape, owner, using, targets):
    if shot.get("time_left", 1) <= 0:
        return False
    if "time_left" in shot:
        shot["time_left"] -= 1

    vx, vy = shot["vx"], shot["vy"]
    x, y = shot["x"], shot["y"]
    ai = shot.get("ai")
    observed_age = (shot.get("ai1", 0) if ai == 3 else shot.get("local_ai", (0,0))[0] if ai == 99
                    else shot.get("local_ai", (0,0))[1] if ai == 15 else shot.get("ai0", 0))
    age = shot.get("_age",observed_age)+1
    shot["_age"] = age
    if owner and ai == 19 and str(shot["id"]) in _WEAPONS["spears"]:
        start,outward,inward = _WEAPONS["spears"][str(shot["id"])]
        duration = shot.get("duration",30)
        ticks = age / (1+shot.get("extra_updates",0))
        left = max(0,duration-ticks)
        back = duration//3
        extension = start+outward*(duration-back-max(0,left-back))-inward*(back-min(left,back))
        shot.update(x=owner["x"]+10-shot["w"]/2+vx*extension,
                    y=owner["y"]+21-shot["h"]/2+vy*extension)
        return ticks < duration
    if owner and shot.get("whip"):
        if age >= shot.get("duration",30)*(1+shot.get("extra_updates",0)): return False
        shot["points"] = whip_points(shot,owner,age)
        shot.update(x=owner["x"]+10-shot["w"]/2,y=owner["y"]+21-shot["h"]/2)
        return True
    if owner and ai == 3:
        state=shot.get("ai0",0)
        if not state and age >= (45 if shot["id"]==106 else 30): state=1
        if state:
            shot["tile_collide"]=False
            speed,acc={19:(20,1.5),33:(18,1.2),182:(16,1.2),106:(16,1.2),272:(20,1.5),333:(12,.6),
                       301:(15,3),320:(15,3),383:(16,4),866:(16,1.2),1000:(9.5,.4)}.get(shot["id"],(9,.4))
            dx,dy=owner["x"]+10-x-shot["w"]/2,owner["y"]+21-y-shot["h"]/2
            length=hypot(dx,dy)
            if length<max(shot["w"],shot["h"])/2 or length>3000:return False
            target=(dx/length*speed,dy/length*speed)
            def steer(v,w):
                if v<w:return v+acc+(acc*2 if v<0<w else 0)
                if v>w:return v-acc-(acc*2 if v>0>w else 0)
                return v
            vx,vy=steer(vx,target[0]),steer(vy,target[1])
        shot["ai0"]=state
    elif owner and ai == 9:
        # AI_009_MagicMissiles while channelled: steering comes from the actual cursor.
        if using:
            tx,ty=shot.get("aim",(x+vx,y+vy))
            dx,dy=tx-x-shot["w"]/2,ty-y-shot["h"]/2
            length=hypot(dx,dy)
            if length>=64:
                speed=min(32,length);vx,vy=dx/(length or 1)*speed,dy/(length or 1)*speed
            else:vx,vy=vx*.3+dx*.3,vy*.3+dy*.3
            shot["time_left"]=max(60,shot["time_left"])
        elif hypot(vx,vy):
            speed=hypot(vx,vy);vx,vy=vx/speed*32,vy/speed*32
    elif owner and ai == 99 and shot.get("yoyo_speed",0)>0:
        shot["time_left"]=6
        speed,reach=shot["yoyo_speed"],shot["yoyo_range"]
        cx,cy=x+shot["w"]/2,y+shot["h"]/2
        ox,oy=owner["x"]+10,owner["y"]+21
        returning = shot.get("_returning",False) or not using or (shot.get("yoyo_life",-1)>0 and age>shot["yoyo_life"]*60)
        tx,ty=(ox,oy) if returning else shot.get("aim",(cx,cy))
        dx,dy=tx-ox,ty-oy;length=hypot(dx,dy)
        if not returning and length>reach:tx,ty=ox+dx/length*(reach-1),oy+dy/length*(reach-1)
        dx,dy=tx-cx,ty-cy;length=hypot(dx,dy)
        inertia=max(1.01,14-speed/2)
        if returning:
            inertia=max(1,int(inertia*.8));speed*=1.5
            if length<speed+10:return False
        if length>5+speed/2 or returning:
            top=speed if returning else min(length/2,speed)
            vx=(vx*(inertia-1)+dx/(length or 1)*top)/inertia
            vy=(vy*(inertia-1)+dy/(length or 1)*top)/inertia
        else:vx*=.8;vy*=.8
        shot["_returning"]=returning
    elif owner and ai == 15:
        # Projectile.AI_015_Flails: held spin, launch on release, then retract.
        state=shot.get("ai0",0)
        if state==0 and using:
            direction=1 if shot.get("aim",(owner["x"]+100,0))[0]>=owner["x"]+10 else -1
            angle=pi*10*(age/60)*direction
            dx,dy=cos(angle)*direction,sin(angle)*direction*.8
            if dy>0:dy*=.5
            shot.update(x=owner["x"]+10+dx*30-shot["w"]/2,
                        y=owner["y"]+21+dy*30-shot["h"]/2,vx=0,vy=0,time_left=6,_no_hit=age<=12)
            return True
        if state==0:
            tx,ty=shot.get("aim",(x+vx,y+vy));dx,dy=tx-owner["x"]-10,ty-owner["y"]-21
            speed={25:14,154:15,26:16,35:17,63:21,757:22,247:23,947:12,948:12}.get(shot["id"],24)
            length=hypot(dx,dy) or 1;vx,vy=dx/length*speed,dy/length*speed
            shot.update(ai0=1,_released=age,_no_hit=False)
        elif age-shot.get("_released",age)>15:
            dx,dy=owner["x"]+10-x-shot["w"]/2,owner["y"]+21-y-shot["h"]/2
            length=hypot(dx,dy)
            if length<16:return False
            vx,vy=dx/length*16,dy/length*16
        shot["time_left"]=6
    elif ai == 2:
        # Projectile.AI style 2: thrown knives/shuriken acquire gravity after 20 updates.
        if age>=20:vx*=.97;vy=min(16,vy+.4)
    elif ai == 16:
        # Projectile.AI_016: standard grenades, bombs and dynamite.
        if age> (10 if shot["id"] in (30,397,517,588) else 5):
            if vy==0:vx*=.97
            vy=min(16,vy+.2)
        if shot["time_left"]<=3 and shot.get("blast_w") and not shot.get("_blast"):
            x-=(shot["blast_w"]-shot["w"])/2;y-=(shot["blast_h"]-shot["h"])/2
            shot.update(w=shot["blast_w"],h=shot["blast_h"],tile_collide=False,_blast=True)
    elif shot.get("id") == SAND_BALL:
        # Projectile.AI_010. The first AI value distinguishes falling sand
        # from a launched ball, which curves and loses sideways speed.
        if shot.get("ai0") == 2:
            vx = max(0, vx - 0.04) if vx > 0 else min(0, vx + 0.04)
            vy = min(10, vy + 0.2)
        elif shot.get("ai0") == 1:
            vy = min(10, vy + 0.41)
    elif shot.get("id") in _HOMING and shot.get("friendly"):
        vx, vy = home(shot, targets, solid, shape)
    elif shot.get("id") in ARROW_GRAVITY:
        delay, gravity = ARROW_GRAVITY[shot["id"]]
        age = shot.get("ai0", 0) + 1
        shot["ai0"] = min(delay, age)
        if age >= delay:
            vy = min(16, f32(vy + f32(gravity)))
    elif shot.get("id") == 90:  # Projectile.AI_024: Crystal Shard.
        vx, vy = f32(vx * .96), f32(vy * .96)
        shot["ai1"] = shot.get("ai1", 0) + 1
        if shot["ai1"] > 15:
            shot["scale"] = f32(shot.get("scale", 1.2) - .05)
            if shot["scale"] <= .2:
                shot["time_left"] = 0
                return False

    if shot.get("tile_collide"):
        _, _, free_vx, free_vy, _ = move_box(
            x, y, shot["w"], shot["h"], vx, vy, solid, solid, shape)
        if (vx, vy) != (free_vx, free_vy):
            if ai == 3:
                shot["ai0"]=1
                vx=-vx if vx!=free_vx else vx;vy=-vy if vy!=free_vy else vy
            elif ai in (8,16):
                bounce = 1 if ai==8 else .4
                vx=-vx*bounce if vx!=free_vx else vx
                vy=-vy*bounce if vy!=free_vy else vy
                if ai==16 and abs(vy)<1:vy=0
                if ai==8 and shot.get("penetrate",1)>0:
                    shot["penetrate"]-=1
                    if shot["penetrate"]==0:return False
            elif ai == 99:
                vx=-vx if vx!=free_vx else vx;vy=-vy if vy!=free_vy else vy
            else:
                shot["time_left"] = 0
                return False

    # Keep the observed velocity for types whose flight rule we haven't measured.
    shot.update(x=f32(x + vx), y=f32(y + vy), vx=vx, vy=vy)
    return True
