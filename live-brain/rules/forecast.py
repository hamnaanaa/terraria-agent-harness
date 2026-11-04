"""Short forecasts of movement, tool use and combat."""
from rules import body as B, enemies as E, hands as Hd, sight as Si, tiles as Tl, weapons as Wp

CELL, BW, BH = 16, B.W, B.H
HORIZON = 60


def enemies_of(state):
    out = []
    b = state["body"]
    for e in state["enemies"]:
        if abs(e["x"] - b["x"]) > 40 * CELL or abs(e["y"] - b["y"]) > 40 * CELL:
            continue
        out.append({"i": e["i"], "name": e["name"], "x": e["x"], "y": e["y"], "w": e["w"], "h": e["h"],
                    "vx": e["vx"], "vy": e["vy"], "kind": E.kind_of(e), "ai": e.get("ai"), "damage": e["damage"],
                    "defense": e["defense"], "life": e["life"], "life_max": e.get("life_max", e["life"]), "kb_taken": e["kb_taken"],
                    "through_blocks": e.get("through_blocks", False),
                    "timer": (e.get("timers") or [None])[0]})      # the game's own AI counter (a slime's hop timer)
    return out


def hold_for(dx, vx, phys):
    """How many ticks to hold the side key so a body on flat ground with speed vx comes to rest dx px away (rule
    «run»): the hold whose resting point is nearest, 1 to 60 ticks."""
    side = {"right"} if dx > 0 else {"left"}
    best, bd = 1, None
    for h in range(1, 61):
        b = {"vx": vx, "ground": True}
        x = 0.0
        for t in range(1, 200):
            B.run(b, side if t <= h else set(), phys)
            x += b["vx"]
            if t > h and abs(b["vx"]) < 0.01:
                break
        d = abs(x - dx)
        if bd is None or d < bd:
            best, bd = h, d
    return best


def foe_step(enemy, cx, cy, solid, floor, water, door):
    if enemy["through_blocks"]:
        solid = floor = lambda x, y: False
        water, door = None, None
    E.tick(enemy, cx, cy, solid, floor, water, door)


def in_air(b, sb):
    """A roll from the body as read: in the air its fall counts from the game's own fall_start (the row of its top
    when it last stood, swam or was pushed up by a jump: rule «fall_damage»), not from the row it is in now. Each roll
    began the count afresh where the body hung, so it could underestimate the damage at the landing."""
    if not sb["ground"] and sb.get("fall_start") is not None:
        b["fall_start"] = sb["fall_start"]


def motion_keys(keys, t, jump_at, hold, jump_hold):
    return [k for k in keys if t <= hold and (k != "jump" or jump_at < t <= jump_at + jump_hold)]


def play(state, world, foes0, keys, use, phys, strength, work, jump_at, hold, jump_hold, stop, *, prefix=None, horizon=HORIZON, land=False):
    sb = state["body"]
    b = B.start(sb["x"], sb["y"])
    b.update(vx=sb["vx"], vy=sb["vy"], jump=sb["jump"], ground=sb["ground"], wet=sb["wet"], released=sb.get("released", True),
             rope=sb.get("rope", False), wing=sb.get("wing_time", 0))
    in_air(b, sb)
    foes = [dict(e) for e in foes0]
    shots = [dict(s, age=20) for s in state["shots"] if s["hostile"]]
    own, put, dug, hits_on, puts_at, swap = [], {}, set(), {}, [], {}       # put: cell -> the tile set there
    ropes = set()                                          # ropes set in this play (rule «rope»)
    # rule «hook» (gym, Oct 20): the hook flies at 11 px a tick; once latched it pulls the character at 11 px a tick
    # to rest against the wall, its centre at the hook's height; a jump during the pull lets go with no lift, the
    # pull's speed carrying on; a jump from rest lets go with half a jump (8 ticks of push, 61 px)
    hook = {"latched": False, "rest": None, "line": None, "fly_until": None, "pull_v": (0.0, 0.0)}
    sh = sb.get("hook")
    if sh and sh.get("state") == "latched":
        hx, hy = sh["x"], sh["y"]
        face = int(hx // CELL) * CELL if sb["x"] + BW / 2 < hx else (int(hx // CELL) + 1) * CELL
        hook["rest"] = (face - BW if sb["x"] + BW / 2 < hx else face, hy - BH / 2)
        hook.update(line=(sb["x"], sb["y"]), fly_until=0, pull_v=(sb["vx"], sb["vy"]), point=(hx, hy))
        hook["latched"] = abs(sb["vx"]) + abs(sb["vy"]) < 0.5
    prev_jump = sb.get("jump_down", False)
    immune = sb["immune_time"] if sb["immune"] else 0
    tile_immune, on_fire, burn_t = 0, 0, 0                 # rules «lava», «spikes», «hot_blocks», «thorns»
    lava_safe, hot_safe = Tl.lava_safe(phys), Tl.hot_safe(phys)
    ready = max(0, sb["item_animation"])
    breath = sb["breath"]
    taken = healed = dealt = hits = shots_fired = 0
    path, aim = [(round(b["x"] + BW / 2), round(b["y"] + BH / 2))], None
    unknown_landing = False
    swing = None                                           # the swing under way (rule «swing»)
    facing = sb.get("dir", 1) or 1                         # the way the body faces: the last side key (rule «swing»)
    under = work["under"] if work else set()
    solid = lambda x, y: world.solid(x, y, put, dug)
    floor = lambda x, y: world.floor(x, y, put, dug)
    water = lambda x, y: world.water(x, y, put, dug)
    rope = lambda x, y: world.rope(x, y) or (x, y) in ropes
    sight = lambda x, y: (Si.SHAPED if (x, y) not in put and world.shape(x, y) else Si.BLOCK) if solid(x, y) else Si.OPEN
    duration = horizon if prefix is None else len(prefix)
    extra = HORIZON if land else 0
    for t in range(1, duration + extra + 1):
        if t > duration and (b["ground"] or b["wet"] or b["rope"] or hook["latched"]):
            t -= 1
            break
        ks = set(motion_keys(keys, t, jump_at, hold, jump_hold)) if prefix is None else (
            prefix[t - 1] if t <= duration else frozenset())
        if prefix is None and t == 1 and "jump" in ks and not prev_jump and not b["released"]:
            ks.discard("jump")                             # the mod lets a held jump key up for a tick at a new script
        target = next((e for e in foes if use and e["i"] == use[3] and e["life"] > 0), None) \
            if use and use[0] in ("shoot", "stab", "swing", "melee") else None
        pressed = "jump" in ks and not prev_jump
        prev_jump = "jump" in ks
        launch = hook.get("launch")
        if launch is not None and hook["line"] is not None and t <= hook["fly_until"]:
            ox, oy = hook["line"]
            tx, ty = hook["point"]
            part = (t - launch) / (hook["fly_until"] - launch)
            hx, hy = ox + (tx - ox) * part, oy + (ty - oy) * part
            if (hx - b["x"] - BW / 2) ** 2 + (hy - b["y"] - BH / 2) ** 2 > Hd.HOOK["reach"] ** 2:
                hook["line"] = None
        touching = next((e for e in foes if e["life"] > 0 and
                         E.overlaps(b["x"], b["y"], BW, BH, e["x"], e["y"], e["w"], e["h"])), None) if immune == 0 else None
        pulling = hook["line"] is not None and t >= hook["fly_until"] and not hook["latched"]
        if touching is not None:
            taken += E.touch_damage(touching, phys.get("defense", 0), phys.get("defense_eff", 0.5))
            immune = E.IMMUNE_TICKS
            if not (pulling or hook["latched"]):
                # Gym trace: a zombie hit replaces this tick's movement, including a jump in progress.
                direction = -1 if b["x"] + BW / 2 < touching["x"] + touching["w"] / 2 else 1
                b.update(vx=direction * 4.5, vy=-3.5, jump=0, ground=False, rope=False, hurt=0)
                B.move(b, solid, floor)
            else:
                b["hurt"] = 0
        elif pulling and pressed:
            # rule «hook»: a jump during the pull lets go with no lift; the pull's speed carries on
            hook["line"] = None
            b["vx"], b["vy"] = hook["pull_v"]
            b.update(ground=False, released=False, jump=0)
            B.tick(b, ks, phys, solid, floor, water, rope)
        elif pulling:
            (rx, ry) = hook["rest"]
            dx, dy = rx - b["x"], ry - b["y"]
            dd = (dx * dx + dy * dy) ** 0.5
            if dd <= Hd.HOOK["pull"]:
                b["x"], b["y"], hook["latched"] = rx, ry, True     # at rest against the wall
                b.update(vx=0.0, vy=0.0, ground=True)
            else:
                v = (dx / dd * Hd.HOOK["pull"], dy / dd * Hd.HOOK["pull"])
                b["x"], b["y"], hook["pull_v"] = b["x"] + v[0], b["y"] + v[1], v
                b.update(vx=v[0], vy=v[1], ground=False)
            b.update(jump=0, hurt=0, fall_start=int(b["y"] // CELL))
        elif hook["latched"] and not pressed:
            b.update(vx=0.0, vy=0.0, ground=True, jump=0, hurt=0, fall_start=int(b["y"] // CELL))   # still on the hook
        else:
            half = hook["latched"]
            if half:
                hook["latched"], hook["line"] = False, None      # a jump from rest lets go with half a jump
                b.update(ground=True, released=True)
            B.tick(b, ks, phys, solid, floor, water, rope)
            if half:
                b["jump"] = min(b["jump"], int(phys.get("jump_hold", 15)) // 2)
        if b["ground"] and not hook["latched"] and not b["rope"]:
            x0, x1 = B.span(b["x"], BW)
            row = round((b["y"] + BH) / CELL)
            support = [world.tile(x, row, put, dug) for x in range(x0, x1 + 1) if floor(x, row)]
            unknown_landing |= bool(support) and all(tile == -2 for tile in support)
        taken += b["hurt"]
        if "left" in ks:
            facing = -1
        elif "right" in ks:
            facing = 1
        cx, cy = b["x"] + BW / 2, b["y"] + BH / 2
        body = {"x": b["x"], "y": b["y"], "w": BW, "h": BH}
        # the tiles that hurt, as the gym measured them: lava and spikes hit with 40 ticks of their own immunity,
        # hot blocks burn while touched, thorns cost a touch and break
        td, tile_status = tile_damage(b, world, put, dug, phys, lava_safe, hot_safe, t, tile_immune, on_fire, burn_t)
        taken += td
        tile_immune, on_fire, burn_t = tile_status
        if use and t <= hold and (t == 1 if use[0] == "hook" else t >= ready):
            kind, item = use[0], use[1]
            if kind == "shoot" and target and shots_fired < use[2]["stack"]:
                # the target's track, stepped once and read at each flight time the aim tries (it was re-stepped
                # from the start for each one: 1,830 enemy steps for a far shot instead of 60, Oct 20)
                q, track = dict(target), []
                def future(k, ox=cx, oy=cy, q=q, track=track):
                    while len(track) <= k:
                        if track:
                            foe_step(q, ox, oy, solid, floor, water, world.door)
                        track.append((q["x"] + q["w"] / 2, q["y"] + q["h"] / 2))
                    return track[k]
                # the aim leads the target by its rules; aiming at its place now or half way was no better on bats
                # (68 percent either way) and worse on slimes (40 against 32), 5 runs each, Oct 20
                tx, ty = Wp.intercept(cx, cy, item["shoot_speed"] + use[2]["shoot_speed"], future)
                own.append(Wp.shot(item, use[2], cx, cy, *(aim or (tx, ty))))
                shots_fired += 1
                if aim is None:
                    aim = (tx, ty)
                ready = t + item["use_time"]
            elif kind in ("stab", "swing", "melee") and target:
                d = facing
                if kind == "stab" and aim is None:
                    # Keep the aim ahead of the player throughout this input script.
                    speed = max(abs(b["vx"]), phys["run_top"], phys.get("acc_run", 0))
                    aim = (cx + d * (BW + (hold - t) * speed), cy)
                sw = Wp.swing_of(item)
                if sw:
                    # rule «swing»: the engine's rectangles from this tick on, one hit per enemy per swing (below)
                    swing = {"sw": sw, "item": item, "start": t, "end": t + sw["use_anim"], "hit": set()}
                else:
                    rx = b["x"] + BW if d > 0 else b["x"] - Wp.STAB_REACH_PX
                    # rule «sight»: a stab or a swing lands only where the game's wall check lets it (rules/sight.py)
                    if E.overlaps(rx, b["y"], Wp.STAB_REACH_PX, BH, target["x"], target["y"], target["w"], target["h"]) and \
                            (target["through_blocks"] or Si.can_hit(b["x"], b["y"], BW, BH, d, target, sight)):
                        dmg = Wp.hit_damage(item["damage"], target["defense"])
                        target["life"] -= dmg
                        dealt += dmg
                        hits += 1
                        target["vx"] = d * Wp.knock(item["knockback"], target)
                ready = t + item["use_time"]
            elif kind == "rope":
                cells = use[3] if isinstance(use[3][0], tuple) else (use[3],)
                cell = next((c for c in cells if c not in ropes), None)
                if cell is not None and not world.lava(*cell) and world.tile(*cell, put, dug) == -1 and \
                        Hd.in_reach(body, cell, phys, item.get("tile_boost", 0)) and \
                        (any(world.tile(x, y, put, dug) >= 0 or rope(x, y) for x, y in ((cell[0] - 1, cell[1]), (cell[0] + 1, cell[1]), (cell[0], cell[1] - 1), (cell[0], cell[1] + 1)))):
                    ropes.add(cell)                        # rule «rope»: held by a tile or a rope beside it
                    puts_at.append((t, cell))
                    ready = t + Hd.use_ticks(item, phys.get("tile_speed", 1.0))
            elif kind == "open":
                # rule «door»: a right-click within reach opens it at once; the open door does not stop anything
                cell = use[3]
                if Hd.in_reach(body, cell, phys) and world.door(*cell):
                    for k in range(-2, 3):
                        if world.door(cell[0], cell[1] + k):
                            dug.add((cell[0], cell[1] + k))
                    puts_at.append((t, cell))
                ready = HORIZON + 1
            elif kind == "hook" and hook["line"] is None and not hook["latched"]:
                # rule «hook»: the hook flies from the centre to the wall's near face at 11 px a tick; the character
                # comes to rest against that face with its centre at the hook's height
                T = use[3]
                cx0, cy0 = b["x"] + BW / 2, b["y"] + BH / 2
                left = cx0 < T[0] * CELL
                face = T[0] * CELL if left else (T[0] + 1) * CELL
                tx, ty = face + (-1 if left else 1), T[1] * CELL + CELL / 2
                dd = ((tx - cx0) ** 2 + (ty - cy0) ** 2) ** 0.5
                if dd <= Hd.HOOK["reach"]:
                    hook.update(rest=(face - BW if left else face, ty - BH / 2), line=(cx0, cy0),
                                fly_until=t + int(dd / Hd.HOOK["speed"]) + 1, point=(tx, ty), launch=t)
                ready = t + 20
            elif kind == "put":
                cells = use[3] if isinstance(use[3][0], tuple) else (use[3],)
                cell = next((c for c in cells if c not in put and c not in swap), None)
                x0b, x1b = B.span(b["x"], BW)
                want = cell
                if cell is not None and cell in under:
                    # a block to stand on goes under the body's centre column when the way's column would hold
                    # less than half the body (pressed against a cliff the body kept 4 px on its pillar and the
                    # landing check sent it off, Oct 19 night), and only where a block can hold on
                    cc = int((b["x"] + BW / 2) // CELL)
                    alt = (cc, cell[1])
                    held = min(b["x"] + BW, (cell[0] + 1) * CELL) - max(b["x"], cell[0] * CELL)
                    if alt != cell and held < BW / 2 and x0b <= cc <= x1b and world.tile(*alt, put, dug) == -1 and \
                            Hd.can_place(alt, body, phys, item, lambda x, y: world.tile(x, y, put, dug),
                                         world.wall, lambda x, y: world.holds(x, y, put, dug)) is None:
                        cell = alt
                above = cell is not None and (want not in under or (x0b <= cell[0] <= x1b and b["y"] + BH <= cell[1] * CELL + 0.01))
                if cell is not None and above and not world.lava(*cell) and \
                        Hd.can_place(cell, body, phys, item, lambda x, y: world.tile(x, y, put, dug),
                                     world.wall, lambda x, y: world.holds(x, y, put, dug)) is None:
                    put[cell] = item["place_tile"]
                    dug.discard(cell)
                    puts_at.append((t, cell))
                    if cell != want:
                        swap[want] = cell                  # the way's cell, done as this one
                    ready = t + Hd.use_ticks(item, phys.get("tile_speed", 1.0))
            elif kind == "dig":
                cell = use[3]
                tt = world.tile(*cell, put, dug)
                if tt >= 0 and world.can_break.get(tt, True) and Hd.in_reach(body, cell, phys, item.get("tile_boost", 0)):
                    if item.get("axe", 0) > 0 and item.get("pick", 0) == 0:
                        dmg = Hd.axe_damage(item) if world.names.get(tt) == "Trees" else 0   # an axe cuts trees only
                    else:
                        dmg = world.pick.get(tt, 0) if cell not in put else world.pick.get(0, 70)
                    if dmg > 0:
                        hits_on[cell] = hits_on.get(cell, 0) + dmg
                        if hits_on[cell] >= 100:
                            dug.add(cell)
                            put.pop(cell, None)
                        ready = t + Hd.use_ticks(item, phys.get("pick_speed", 1.0))
            elif kind == "potion":
                healed = min(item["heal"], sb["life_max"] - sb["life"] + taken)
                ready = HORIZON + 1
        if swing is not None and swing["start"] <= t < swing["end"]:
            box = Wp.swing_box(swing["sw"], swing["end"] - t, facing)
            if box:
                rx, ry, rw, rh = b["x"] + box[0], b["y"] + box[1], box[2], box[3]
                for e in foes:
                    if e["life"] > 0 and e["i"] not in swing["hit"] and E.overlaps(rx, ry, rw, rh, e["x"], e["y"], e["w"], e["h"]) and \
                            (e["through_blocks"] or Si.can_hit(b["x"], b["y"], BW, BH, facing, e, sight)):
                        dmg = Wp.hit_damage(swing["item"]["damage"], e["defense"])
                        e["life"] -= dmg
                        dealt += dmg
                        hits += 1
                        e["vx"] = facing * Wp.knock(swing["item"]["knockback"], e)
                        swing["hit"].add(e["i"])
        # the enemies, toward the body
        for e in foes:
            if e["life"] <= 0:
                continue
            foe_step(e, cx, cy, solid, floor, water, world.door)
        for s in shots:
            s["x"] += s["vx"]
            s["y"] += s["vy"]
            over = E.overlaps(b["x"], b["y"], BW, BH, s["x"], s["y"], s["w"], s["h"])
            if immune == 0 and over:
                taken += E.shot_damage(s, strength, phys.get("defense", 0), phys.get("defense_eff", 0.5))
                immune = E.IMMUNE_TICKS
                s["y"] = -1e9
        for s in own:
            Wp.shot_tick(s)
            if s["y"] < -1e8:
                continue
            if solid(int(s["x"] // CELL), int(s["y"] // CELL)):
                s["y"] = -1e9
                continue
            for e in foes:
                if e["life"] > 0 and E.overlaps(s["x"], s["y"], s["w"], s["h"], e["x"], e["y"], e["w"], e["h"]):
                    dmg = Wp.hit_damage(s["damage"], e["defense"])
                    e["life"] -= dmg
                    dealt += dmg
                    hits += 1
                    e["vx"] = (1 if s["vx"] > 0 else -1) * Wp.knock(s["knockback"], e)
                    s["y"] = -1e9
                    break
        if immune > 0:
            immune -= 1
        # air (rules «breath», «drowning»)
        head_wet = water(int(cx // CELL), int((b["y"] + 4) // CELL))
        if head_wet:
            if t % 7 == 0:
                if breath > 0:
                    breath -= 1
                else:
                    taken += 2
        else:
            breath = min(200, breath + 3)
        done = stop(b, put, dug, ropes)
        if t % 3 == 0 or done:
            path.append((round(cx), round(cy)))
        if done or taken >= sb["life"] + healed:
            break
    engaged = hook["line"] is not None and t >= hook["fly_until"]
    hook_state = None
    if hook["line"] is not None:
        hx, hy = hook["point"]
        hook_state = {"x": hx, "y": hy, "state": "latched" if engaged else "flying"}
    elif sh and sh["state"] == "flying":
        hook_state = sh
    b.update(life=sb["life"] + healed - taken, breath=breath, hooked=engaged, hook=hook_state,
             jump_down=prev_jump, immune_time=immune, immune=immune > 0, dir=facing,
             wing_time=b.get("wing", 0), item_animation=max(0, ready - t))
    return {"body": b, "enemies": foes, "put": put, "dug": dug, "ropes": ropes, "hits_on": hits_on,
            "damage": taken, "healed": healed, "dealt": dealt, "hits": hits, "shots": shots_fired,
            "path": path, "aim": aim, "puts_at": puts_at, "swap": swap, "ticks": t,
            "hostile_shots": [s for s in shots if s["y"] > -1e8], "unknown_landing": unknown_landing}


def settling_damage(state, world, forecast, phys, ticks):
    """Forecast damage with the buttons released after a waiting move."""
    settled = dict(state, body=dict(state["body"], **forecast["body"]), shots=forecast["hostile_shots"])
    return play(settled, world, forecast["enemies"], (), None, phys, state["strength"],
                {}, 0, 0, 0, lambda *_: False, horizon=ticks)["damage"]


def tile_damage(b, world, put, dug, phys, lava_safe, hot_safe, t, tile_immune, on_fire, burn_t):
    """One tick of damage from the tiles the body is in or touches (rules/tiles.py), and the counters after it in
    _tile_state: the immunity ticks left after a lava or spike hit, the On Fire! ticks left, the burn's beat."""
    taken = 0
    defense = phys.get("defense", 0) // 2
    c0, c1 = B.span(b["x"] - 1, BW + 2)
    r0, r1 = B.span(b["y"] - 1, BH + 2)
    kinds = set()
    for c in range(c0, c1 + 1):
        for r in range(r0, r1 + 1):
            if (c, r) in put or (c, r) in dug:
                continue
            k = world.hurt(c, r)
            if k:
                kinds.add((k, c, r))
            if not lava_safe and world.lava(c, r) and b["x"] < (c + 1) * CELL and b["x"] + BW > c * CELL and \
                    b["y"] < (r + 1) * CELL and b["y"] + BH > r * CELL:
                kinds.add(("lava", c, r))
    if tile_immune > 0:
        tile_immune -= 1
    hit = None
    for k, c, r in kinds:
        if k == "lava" and tile_immune == 0:
            hit = max(hit or 0, Tl.LAVA_HIT)
            on_fire = Tl.FIRE_TICKS
        elif k == "spike" and tile_immune == 0:
            hit = max(hit or 0, Tl.SPIKES["Spikes"])
        elif k == "thorn" and (b["vx"] != 0 or b["vy"] != 0):
            taken += max(1, Tl.THORNS["JungleThorns"] - defense)
            dug.add((c, r))                                # the touched thorn breaks
        elif k == "hot" and not hot_safe:
            burn_t += 1
            if burn_t % Tl.HOT_EVERY == 0:
                taken += Tl.HOT_HIT
    if hit is not None:
        taken += max(1, hit - defense)
        tile_immune = Tl.HURT_EVERY
    if on_fire > 0:
        on_fire -= 1
        if on_fire % Tl.FIRE_EVERY == 0:
            taken += 1
    return taken, (tile_immune, on_fire, burn_t)


