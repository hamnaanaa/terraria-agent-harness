"""Short forecasts of movement, tool use and combat."""
from rules import body as B, enemies as E, hands as Hd, projectiles as P, sight as Si, tiles as Tl, weapons as Wp, motion as M

CELL, BW, BH = 16, B.W, B.H
HORIZON = 60


def enemies_of(state):
    out = []
    for e in state["enemies"]:
        velocity = e.get("motion", e)
        out.append({**{k: e[k] for k in ("i", "id", "name", "x", "y", "w", "h", "damage", "defense", "life", "kb_taken") if k in e},
                    "vx": velocity["vx"], "vy": velocity["vy"],
                    "life_max": e.get("life_max", e["life"]),
                    "hurt_slot": E.hurt_slot(e),
                    "dont_take_damage": e.get("dont_take_damage", False),
                    **({"chaseable": e["chaseable"]} if "chaseable" in e else {}),
                    "through_blocks": e.get("through_blocks", False), "flies": e.get("flies", False),
                    "gravity": e.get("gravity", 0), "fall_top": e.get("fall_top", float("inf")),
                    "dir": e.get("dir", 0), "dir_y": e.get("dir_y", 0),
                    "melee_immune": e.get("melee_immune", 0), "owner_immune": e.get("owner_immune", 0)})
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


def foe_step(enemy, cx, cy, solid, floor, water, door, shape=None, cache=None):
    return E.tick(enemy, cx, cy, solid, floor, water, door, shape, cache=cache)


def in_air(b, sb):
    """A roll from the body as read: in the air its fall counts from the game's own fall_start (the row of its top
    when it last stood, swam or was pushed up by a jump: rule «fall_damage»), not from the row it is in now. Each roll
    began the count afresh where the body hung, so it could underestimate the damage at the landing."""
    if not sb["ground"] and sb.get("fall_start") is not None:
        b["fall_start"] = sb["fall_start"]


def motion_keys(keys, t, jump_at, hold, jump_hold):
    return [k for k in keys if t <= hold and (k != "jump" or jump_at < t <= jump_at + jump_hold)]


def play(state, world, foes0, keys, use, phys, strength, work, jump_at, hold, jump_hold, stop, *, prefix=None, horizon=HORIZON, land=False, movement=None, follow=None, projecting=False, spacing_ticks=0):
    if use and use[0] in ("shoot", "swing", "stab", "melee"):
        use = (use[0], Wp.effective(use[1]), *use[2:])
    sb = state["body"]
    hit_state = dict(sb)
    mana = sb.get("mana", 0)
    b = B.start(sb["x"], sb["y"])
    b.update(vx=sb["vx"], vy=sb["vy"], jump=sb["jump"], ground=sb["ground"], wet=sb["wet"], released=sb.get("released", True),
             rope=sb.get("rope", False), wing=sb.get("wing_time", 0))
    in_air(b, sb)
    if "arm_offset" in sb: b["arm_offset"] = tuple(sb["arm_offset"])
    foes = [dict(e) for e in foes0]
    shots = [dict(M.prepare_shot(s)) for s in state["shots"] if s.get("hostile") or s.get("friendly") and s.get("damage", 0) > 0]
    for s in shots:
        if s.get("hostile"):
            s["_hurt_slot"] = E.hurt_slot(s)
            s["_protected_until"] = E.protection(sb, s)
            s["_harm"] = E.shot_damage(s, strength, phys.get("defense", 0), phys.get("defense_eff", .5),
                                       1-E.hurt_factor(sb))
        if "_hits" in s or "local_immune" in s: s["_hits"] = dict(s.get("_hits", s.get("local_immune", {})))
        if s.get("friendly") and s.get("owner", 0) == sb.get("player_id", 0) and (s.get("ai") == 19 or s.get("whip")):
            duration = sb.get("item_animation_max", 0)
            held_item = next((i for i in state["bag"] if i["id"] == sb.get("held_item")), {})
            if duration:
                s["duration"] = duration
                if s.get("ai") == 19:
                    s["_age"] = (duration - sb["item_animation"]) * (1+s.get("extra_updates", 0))
            s["base_duration"] = held_item.get("base_use_anim", s.get("duration", 30))
            s["whip_multiplier"] = sb.get("whip_range", 1)
    static_hits = {}
    put, dug, hits_on, puts_at, swap = {}, set(), {}, [], {}       # put: cell -> the tile set there
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
    hurt_cooldowns = list(sb.get("hurt_cooldowns", [0] * 5))
    tile_immune, on_fire, burn_t = 0, 0, 0                 # rules «lava», «spikes», «hot_blocks», «thorns»
    lava_safe, hot_safe = Tl.lava_safe(phys), Tl.hot_safe(phys)
    ready = max(0, sb["item_animation"])
    if use and use[0] == "stab" and ready:
        ready += 1  # A single press may also need to switch slots, which waits for a free hand.
    breath = sb["breath"]
    regen, regen_count = min(0, sb.get("life_regen", 0)), sb.get("life_regen_count", 0)
    taken = healed = dealt = hits = shots_fired = contacts = 0
    pressure = 0.0
    area_risk, area_ticks, area_first, area_ids = 0.0, 0, None, set()
    # Potential hits are costs of this candidate, never facts for the next one.
    area_cooldowns, area_hit_damage = {}, {}
    unknown_ticks, unknown_first, unknown_ids = 0, None, set()
    path, aim = [(round(b["x"] + BW / 2), round(b["y"] + BH / 2))], None
    unknown_landing = False
    swing = None                                           # the swing under way (rule «swing»)
    held = next((Wp.effective(i) for i in state["bag"] if i.get("id") == sb.get("held_item")), None)
    animation, item_time = sb["item_animation"], sb.get("item_time", sb["item_animation"])
    selected = sb.get("selected", held.get("slot") if held else None)
    ammo_used = {}
    potion_at = resume_at = None
    resume_use = follow
    potion_slot = None
    if follow and follow[0] in ("shoot", "swing", "stab", "melee"):
        resume_use = (follow[0], Wp.effective(follow[1]), *follow[2:])
    if sb["item_animation"] and held and Wp.swing_of(held):
        swing = {"sw": Wp.swing_of(held), "item": held, "start": 0, "end": sb["item_animation"],
                 "duration": sb.get("item_animation_max") or held["use_anim"],
                 "hit": {e["i"] for e in foes if e.get("melee_immune", 0) > 0}}
    hit_events = []
    melee_cooldown = sb.get("attack_cooldown", 0)
    stab = None
    stabs_at = []
    facing = sb.get("dir", 1) or 1                         # the way the body faces: the last side key (rule «swing»)
    under = work["under"] if work else set()
    solid = lambda x, y: world.solid(x, y, put, dug)
    # The pathfinder treats unexplored cells as solid. They cannot prove cover.
    shot_cover = lambda x, y: world.tile(x, y, put, dug) >= 0 and solid(x, y)
    floor = lambda x, y: world.floor(x, y, put, dug)
    water = lambda x, y: world.water(x, y, put, dug)
    rope = lambda x, y: world.rope(x, y) or (x, y) in ropes
    shape = lambda x, y: 0 if (x, y) in put or (x, y) in dug else world.shape(x, y)
    sight = lambda x, y: (Si.SHAPED if shape(x, y) else Si.BLOCK) if solid(x, y) else Si.OPEN
    def move_body(ks):
        # The cache belongs to this decision's fixed map and physics. Once an
        # option changes terrain, its collision results must be computed anew.
        if movement is None or put or dug or ropes:
            B.tick(b, ks, phys, solid, floor, water, rope)
            return
        key = (tuple(b.items()), tuple(sorted(ks)))
        cached = movement.get(key)
        if cached is None:
            B.tick(b, ks, phys, solid, floor, water, rope)
            movement[key] = dict(b)
        else:
            b.clear()
            b.update(cached)
    duration = horizon if prefix is None else len(prefix)
    extra = HORIZON if land else 0
    hand_until = duration if follow or use and use[0] in ("shoot", "swing", "stab", "melee") else hold
    for t in range(1, duration + extra + 1):
        if t > duration and (b["ground"] or b["wet"] or b["rope"] or hook["latched"]):
            t -= 1
            break
        melee_cooldown = max(0, melee_cooldown - 1)
        area_damage, area_unknown = {}, False
        launched = []
        step = prefix[t - 1] if prefix is not None and t <= duration else None
        ks = set(motion_keys(keys, t, jump_at, hold, jump_hold)) if prefix is None else set(
            step.get("keys", ()) if isinstance(step, dict) else step or ())
        # A running command contains a slot and a fixed cursor position as well
        # as buttons. Keep firing while we calculate its replacement.
        scripted_aim = None
        if resume_at is not None and t == resume_at:
            use = resume_use
        if isinstance(step, dict):
            if not animation and "slot" in step:
                selected = step["slot"]
                held = next((Wp.effective(i) for i in state["bag"] if i["slot"] == selected), None)
            use = None
            if held and Wp.way(held) == "shoot" and "use" in ks:
                ammo = Wp.ammo_for(state["bag"], held)
                scripted_aim = step.get("aim")
                if (ammo is not None or not held.get("use_ammo")) and scripted_aim is not None:
                    use = ("shoot", held, ammo, None)
            elif held and held.get("heal", 0) > 0 and "use" in ks and potion_at is None and not sb.get("potion_used") \
                    and max(0, sb.get("potion_delay", 0) - t) == 0:
                use = ("potion", held, None, None)
        elif use and use[0] == "shoot" and not animation:
            held, selected = use[1], use[1]["slot"]
        animation, item_time = max(0, animation - 1), max(0, item_time - 1)
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
        contact = next((e for e in foes if e["life"] > 0 and
                        E.overlaps(b["x"], b["y"], BW, BH, e["x"], e["y"], e["w"], e["h"])), None)
        contacts += contact is not None
        protection = hurt_cooldowns[E.hurt_slot(contact)] if contact and E.hurt_slot(contact) >= 0 else immune
        touching = contact if protection == 0 and not projecting else None
        pulling = hook["line"] is not None and t >= hook["fly_until"] and not hook["latched"]
        if touching is not None:
            dmg = E.touch_damage(touching, phys.get("defense", 0), phys.get("defense_eff", 0.5), 1 - E.hurt_factor(hit_state))
            E.hurt_consumes(hit_state)
            taken += dmg
            hit_events.append({"tick": t, "kind": "touch", "enemy": touching["i"], "damage": dmg})
            if E.hurt_slot(touching) >= 0:
                hurt_cooldowns[E.hurt_slot(touching)] = E.hurt_immunity(dmg, sb)
            else:
                immune = E.hurt_immunity(dmg, sb)
            if not (pulling or hook["latched"]):
                # Gym trace: a zombie hit replaces this tick's movement, including a jump in progress.
                direction = -1 if b["x"] + BW / 2 < touching["x"] + touching["w"] / 2 else 1
                if E.hurt_recoil(b, direction, sb):
                    B.move(b, solid, floor)
                else:
                    move_body(ks)
            else:
                b["hurt"] = 0
        elif pulling and pressed:
            # rule «hook»: a jump during the pull lets go with no lift; the pull's speed carries on
            hook["line"] = None
            b["vx"], b["vy"] = hook["pull_v"]
            b.update(ground=False, released=False, jump=0)
            move_body(ks)
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
            move_body(ks)
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
        hand_pressed = t <= hand_until if prefix is None else "use" in ks
        if use and use[0] == "shoot" and target and hand_pressed:
            if use[1].get("channel") and aim is None:
                aim = (target["x"]+target["w"]/2, target["y"]+target["h"]/2)
            for shot in shots:
                if shot.get("owner", 0) == sb.get("player_id", 0) and shot.get("id") == use[1].get("shoot") and aim:
                    shot["aim"] = aim
        can_use = (item_time == 0 and held and held["id"] == use[1]["id"]) if use and use[0] == "shoot" else t >= ready
        if use and hand_pressed and (t == 1 if use[0] == "hook" else can_use):
            kind, item = use[0], use[1]
            if kind == "shoot" and (target or scripted_aim) and Wp.affordable(state, item, use[2], ammo_used, mana) and not P.single_active(item, shots, sb.get("player_id", 0)):
                # the target's track, stepped once and read at each flight time the aim tries (it was re-stepped
                # from the start for each one: 1,830 enemy steps for a far shot instead of 60, Oct 20)
                q, track = dict(target) if target else None, []
                def future(k, ox=cx, oy=cy, q=q, track=track):
                    while len(track) <= k:
                        if track:
                            foe_step(q, ox, oy, solid, floor, water, world.door, shape,
                                     movement if not (put or dug or ropes) else None)
                        track.append((q["x"] + q["w"] / 2, q["y"] + q["h"] / 2))
                    return track[k]
                # The aim follows the observed-motion forecast; aiming at its place now or half way was no better on bats
                # (68 percent either way) and worse on slimes (40 against 32), 5 runs each, Oct 20
                tx, ty = scripted_aim or aim or Wp.intercept(cx, cy, Wp.shot_speed(item, use[2]), future)
                facing = 1 if tx > cx else -1
                shot = Wp.shot(item, use[2], cx, cy, tx, ty)
                shot["owner"] = sb.get("player_id", 0)
                launched.append(shot)
                shots_fired += 1
                resource = use[2] if item.get("use_ammo") else item if item.get("consumable") else None
                if resource:
                    ammo_used[resource["slot"]] = ammo_used.get(resource["slot"], 0) + 1
                mana -= item.get("mana_cost", 0)
                shot.update(duration=item["use_anim"], base_duration=item.get("base_use_anim", item["use_anim"]),
                            aim=(tx, ty), whip_multiplier=sb.get("whip_range", 1))
                if aim is None:
                    aim = (tx, ty)
                ready = t + item["use_time"]
                item_time = item["use_time"]
                if not animation:
                    animation = item["use_anim"]
            elif kind == "stab" and target:
                direction = Wp.stab_direction(cx, cy, b["vx"], b["vy"], target, item["shoot_speed"])
                if direction:
                    ux, uy = direction
                    facing = 1 if ux > 0 else -1
                    # A distant point keeps rounding the mouse coordinates from changing the angle.
                    stabs_at.append((t, (cx + 160 * ux, cy + 160 * uy)))
                    stab = {"start": t, "direction": direction, "item": item, "hit": set()}
                    ready = t + item["use_time"]
            elif kind in ("swing", "melee") and target:
                d = facing
                sw = Wp.swing_of(item)
                if sw:
                    # rule «swing»: the engine's rectangles from this tick on, one hit per enemy per swing (below)
                    animation = item["use_anim"]
                    swing = {"sw": sw, "item": item, "start": t, "end": t + animation,
                             "duration": animation, "hit": set()}
                    melee_cooldown = 0
                ready = t + item["use_anim"] + item.get("reuse_delay", 0)
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
                    overlap_width = min(b["x"] + BW, (cell[0] + 1) * CELL) - max(b["x"], cell[0] * CELL)
                    if alt != cell and overlap_width < BW / 2 and x0b <= cc <= x1b and world.tile(*alt, put, dug) == -1 and \
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
                if tt >= 0 and world.can_break(*cell) and Hd.in_reach(body, cell, phys, item.get("tile_boost", 0)):
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
            elif kind == "potion" and potion_at is None and not sb.get("potion_used") \
                    and max(0, sb.get("potion_delay", 0) - t) == 0:
                healed = min(item["heal"], sb["life_max"] - sb["life"] + taken)
                potion_at, potion_slot = t, item["slot"]
                ready = t + item["use_anim"]
                animation, item_time = item["use_anim"], item["use_time"]
                held, selected = item, item["slot"]
                if resume_use:
                    resume_at = ready + 1  # The next update can switch back after the animation ends.
        if swing is not None and swing["start"] <= t < swing["end"]:
            box = Wp.swing_box(swing["sw"], swing["end"] - t, facing, swing["duration"], swing["item"].get("scale", 1),
                               origin=(b["x"], b["y"]), gravity_dir=sb.get("gravity_dir", 1),
                               height_offset=sb.get("height_offset", 0))
            if box:
                rx, ry, rw, rh = b["x"] + box[0], b["y"] + box[1], box[2], box[3]
                for e in foes:
                    if e["life"] > 0 and not melee_cooldown and not e.get("owner_immune") and e["i"] not in swing["hit"] and E.overlaps(rx, ry, rw, rh, e["x"], e["y"], e["w"], e["h"]) and \
                            (e["through_blocks"] or Si.can_hit(b["x"], b["y"], BW, BH, facing, e, sight)):
                        dmg = Wp.hit_damage(swing["item"]["damage"], e["defense"])
                        e["life"] -= dmg
                        dealt += dmg
                        hits += 1
                        Wp.apply_knockback(e, swing["item"]["knockback"], facing, dmg)
                        hit_events.append({"tick": t, "kind": "swing", "enemy": e["i"], "damage": dmg})
                        swing["hit"].add(e["i"])
                        melee_cooldown = max(1, int(swing["duration"] * .33))
        # Enemies continue observed movement; no future attacks are invented.
        for e in foes:
            if e["life"] <= 0:
                continue
            emitted = foe_step(e, cx, cy, solid, floor, water, world.door, shape,
                               movement if not (put or dug or ropes) else None)
            if emitted:
                shots.extend(emitted)
            e["owner_immune"] = max(0, e.get("owner_immune", 0) - 1)
            if spacing_ticks and e["damage"] > 0:
                dx = max(0, e["x"] - b["x"] - BW, b["x"] - e["x"] - e["w"])
                dy = max(0, e["y"] - b["y"] - BH, b["y"] - e["y"] - e["h"])
                ranged = bool(use and use[0] == "shoot" or resume_use and resume_use[0] == "shoot" or
                              use is None and held and Wp.way(held) == "shoot")
                pressure += E.touch_damage(e, phys.get("defense", 0), phys.get("defense_eff", .5),
                                           1-E.hurt_factor(sb)) * E.proximity(b, e, (dx*dx+dy*dy)**.5,
                                                                           phys, spacing_ticks, ranged)
        if stab and t - stab["start"] < Wp.STAB_TICKS:
            item = stab["item"]
            ux, uy = stab["direction"]
            for e in foes:
                if e["life"] <= 0 or e["i"] in stab["hit"]:
                    continue
                if (Wp.stab_overlaps(cx, cy, ux, uy, item["shoot_speed"], t - stab["start"], e)
                        and (e["through_blocks"] or Si.can_hit(b["x"], b["y"], BW, BH, facing, e, sight))):
                    dmg = Wp.hit_damage(item["damage"], e["defense"])
                    e["life"] -= dmg
                    dealt += dmg
                    hits += 1
                    direction = 1 if e["x"] + e["w"] / 2 >= cx else -1
                    Wp.apply_knockback(e, item["knockback"], direction, dmg)
                    hit_events.append({"tick": t, "kind": "stab", "enemy": e["i"], "damage": dmg})
                    stab["hit"].add(e["i"])
        if shots:
            flying = []
            for s in shots:
                shot_pressure = 0.0
                for _ in range(1 + s.get("extra_updates", 0)):
                    owner = b if s.get("friendly") and s.get("owner", 0) == sb.get("player_id", 0) else None
                    if not P.advance(s, shot_cover, shape, owner=owner, using=hand_pressed,
                                     cache=movement if not (put or dug or ropes) else None, targets=foes):
                        break
                    if spacing_ticks and s.get("hostile"):
                        shot_pressure = max(shot_pressure, s["_harm"] * E.proximity(
                            b, s, P.clearance(s, b, movement), phys, spacing_ticks, True))
                    # Geometry, including a warning, is a potential full hit.
                    # Missing samples carry no invented health loss. Neither
                    # grants physical recoil, shield loss or immunity.
                    if s.get("hostile") and s.get("_attack") is not None and not projecting:
                        overlap = P.hits_player(s, b)
                        if overlap is None:
                            area_unknown = True
                            unknown_ids.add(s["id"])
                        elif overlap and t > s["_protected_until"]:
                            dmg = s["_harm"]
                            slot = s["_hurt_slot"]
                            area_damage[slot] = max(area_damage.get(slot, 0), dmg)
                            area_ids.add(s["id"])
                    slot = s.get("_hurt_slot", -1)
                    protected = hurt_cooldowns[slot] if slot >= 0 else immune
                    if s.get("hostile") and s.get("_attack") is None and protected == 0 and not projecting and P.hits_player(s, b):
                        dmg = E.shot_damage(s, strength, phys.get("defense", 0), phys.get("defense_eff", 0.5), 1 - E.hurt_factor(hit_state))
                        E.hurt_consumes(hit_state)
                        taken += dmg
                        hit_events.append({"tick": t, "kind": "hostile_shot", "projectile": s.get("id"), "damage": dmg})
                        if E.hurt_slot(s) >= 0:
                            hurt_cooldowns[E.hurt_slot(s)] = E.hurt_immunity(dmg, sb)
                        else:
                            immune = E.hurt_immunity(dmg, sb)
                        direction = 1 if b["x"] + BW / 2 >= s["x"] + s["w"] / 2 else -1
                        if not (pulling or hook["latched"]):
                            E.hurt_recoil(b, direction, sb)
                        # Projectile.Damage has no generic hostile penetration loss.
                        # Only these types consume it after striking the player.
                        if s["id"] in (435, 436, 437, 682):
                            s["penetrate"] -= 1
                            if s["penetrate"] == 0:
                                s["time_left"] = 0
                                break
                        elif s["id"] == 681:
                            s["time_left"] = 0
                            break
                    if s.get("_blast") and s.get("self_hurt") and not immune and P.collides(s, body):
                        dmg = E.touch_damage(s, phys.get("defense", 0), phys.get("defense_eff", .5), 1-E.hurt_factor(hit_state))
                        taken += dmg
                        immune = E.hurt_immunity(dmg, sb)
                        hit_events.append({"tick":t,"kind":"own_explosion","damage":dmg,"projectile":s["id"]})
                    if s.get("friendly"):
                        for e in foes:
                            local = s.get("uses_local_npc_immunity", False)
                            shared = s.get("uses_static_npc_immunity", False)
                            if e["life"] <= 0 or (local and s.get("_hits", {}).get(str(e["i"]), 0)) or (shared and static_hits.get((s["id"], e["i"]), 0) > t):
                                continue
                            if not local and not shared and s.get("max_penetrate", s.get("penetrate", 1)) != 1 and e.get("owner_immune"):
                                continue
                            if P.collides(s, e):
                                dmg = Wp.hit_damage(s["damage"], e["defense"])
                                e["life"] -= dmg
                                dealt += dmg
                                hits += 1
                                Wp.apply_knockback(e, s["knockback"], 1 if s["vx"] > 0 else -1, dmg)
                                hit_events.append({"tick": t, "kind": "shot", "enemy": e["i"],
                                                   "projectile": s.get("id"), "damage": dmg})
                                if local:
                                    s.setdefault("_hits", {})[str(e["i"])] = s["local_npc_hit_cooldown"]
                                elif shared:
                                    static_hits[(s["id"],e["i"])] = t + s["static_npc_hit_cooldown"]
                                elif s.get("penetrate", 1) != 1:
                                    e["owner_immune"] = 10
                                if s.get("penetrate", 1) > 0:
                                    s["penetrate"] = s.get("penetrate", 1) - 1
                                    if not s["penetrate"]:
                                        s["time_left"] = 0
                                        break
                        if s.get("time_left", 1) <= 0:
                            break
                if s.get("time_left", 1) > 0:
                    flying.append(s)
                pressure += shot_pressure
            shots = flying
        if area_damage:
            area_ticks += 1
            if area_first is None:
                area_first = t
            for slot, damage in area_damage.items():
                if area_cooldowns.get(slot, 0) <= t:
                    area_risk += damage
                    area_hit_damage[slot] = damage
                    area_cooldowns[slot] = t + E.hurt_immunity(damage, sb)
                elif damage > area_hit_damage[slot]:
                    area_risk += damage - area_hit_damage[slot]
                    area_hit_damage[slot] = damage
        if area_unknown:
            unknown_ticks += 1
            if unknown_first is None:
                unknown_first = t
        # The game updates existing projectiles before the player fires this tick.
        # A new bullet first appears at its spawn position, then flies next tick.
        shots.extend(launched)
        if regen < 0:
            # Player.UpdateLifeRegen: one health for each -120 accumulated units.
            regen_count += regen
            if regen_count <= -120:
                loss = -regen_count // 120
                regen_count += loss * 120
                taken += loss
                hit_events.append({"tick": t, "kind": "ongoing_damage", "damage": loss})
        if immune > 0:
            immune -= 1
        hurt_cooldowns = [max(0, ticks - 1) for ticks in hurt_cooldowns]
        # air (rules «breath», «drowning»)
        head = (int(cx // CELL), int((b["y"] + 4) // CELL))
        head_wet = water(*head) or (b["wet"] and solid(*head))
        if head_wet:
            if t % 7 == 0:
                if breath > 0:
                    breath -= 1
                else:
                    taken += 2
        elif not solid(*head):
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
    b.update(life=sb["life"] + healed - taken - area_risk, mana=mana, breath=breath, hooked=engaged, hook=hook_state,
             jump_down=prev_jump, immune_time=immune, immune=immune > 0, dir=facing,
             hurt_cooldowns=hurt_cooldowns,
             wing_time=b.get("wing", 0), item_animation=max(0, ready - t))
    b["attack_cooldown"] = melee_cooldown
    b["item_time"] = item_time
    b["life_regen"], b["life_regen_count"] = regen, regen_count
    for field in ("solar_shields", "beetle_orbs"):
        if field in hit_state:
            b[field] = hit_state[field]
    b["selected"] = selected
    if held and (Wp.way(held) == "shoot" or held.get("heal", 0) > 0):
        b.update(held_item=held["id"], item_animation=animation, item_animation_max=held["use_anim"])
    if swing:
        b["held_item"], b["item_animation_max"] = swing["item"]["id"], swing["duration"]
        for e in foes:
            e["melee_immune"] = max(0, swing["end"] - t) if e["i"] in swing["hit"] else 0
    used = dict(ammo_used)
    if potion_at is not None:
        b["potion_used"] = True
        used[potion_slot] = used.get(potion_slot, 0) + 1
    if area_ticks:
        hit_events.append({"tick": area_first, "kind": "attack_area_risk", "damage": area_risk,
                           "exposure_ticks": area_ticks, "projectiles": sorted(area_ids)})
    if unknown_ticks:
        hit_events.append({"tick": unknown_first, "kind": "attack_area_unknown", "damage": 0,
                           "uncovered_ticks": unknown_ticks, "projectiles": sorted(unknown_ids)})
    result = {"body": b, "enemies": foes, "put": put, "dug": dug, "ropes": ropes, "hits_on": hits_on,
            "damage": taken + area_risk, "healed": healed, "dealt": dealt, "hits": hits, "hit_events": hit_events,
            "shots": sum(ammo_used.values()), "fired": shots_fired, "contacts": contacts,
            "path": path, "aim": aim, "stabs_at": stabs_at, "puts_at": puts_at, "swap": swap, "ticks": t,
            "projectiles": shots, "unknown_landing": unknown_landing, "used": used}
    if spacing_ticks:
        result["pressure"] = pressure / t
    if resume_at is not None:
        result.update(potion_at=potion_at, resume_at=resume_at, resume_use=resume_use)
    return result


def settling_damage(state, world, forecast, phys, ticks):
    """Forecast damage with the buttons released after a waiting move."""
    settled = dict(
        state, body=dict(state["body"], **forecast["body"]), shots=forecast["projectiles"])
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


