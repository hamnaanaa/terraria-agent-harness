"""Generate, forecast and choose the next short input script."""
from math import inf, hypot, sqrt

import goals
import pathfinder
from rules import body, enemies, hands, weapons, motion
from rules.forecast import HORIZON, enemies_of, hold_for, motion_keys, play, settling_damage

REVIEW_TICKS = 15
SETTLE_TICKS = 120
JUMP_HOLD = 16
TORCH_TILE = 4
DARK = 0.25


def material_key(goal, item):
    return (goal.get("preserve", {}).get(item["name"], 0), -item["stack"], item["id"])


def items_for(state, goal):
    bag = [i for i in state["bag"] if i["slot"] < 50]
    kit = {}
    for kind, test in (
        ("block", lambda i: i["block_tile"]), ("platform", lambda i: i["place_tile"] == 19),
        ("rope", lambda i: i["place_tile"] == 213), ("hook", lambda i: i["hook"]),
    ):
        kit[kind] = min((i for i in bag if test(i)), key=lambda i: material_key(goal, i), default=None)
    for name, kind in (("blocks", "block"), ("platforms", "platform"), ("ropes", "rope")):
        kit[name] = kit[kind]["stack"] if kit[kind] else 0
    kit["pick"] = max((i for i in bag if i["pick"]), key=lambda i: (i["pick"], -i["use_time"], -i["id"]), default=None)
    return bag, kit


def candidates(state, world, goal, plan, work, bag):
    keys = [(), ("left",), ("right",)]
    x, x1, top, feet = body.cells_of(state["body"])
    near_rope = any(world.rope(c, r) for c in range(x - 2, x1 + 3) for r in range(top - 1, feet + 4))
    if near_rope or work.get("rope"):
        keys += [("up",), ("down",), ("left", "up"), ("right", "up"), ("left", "down"), ("right", "down")]
    elif not state["body"]["ground"] or any(world.top_kind.get(world.raw_tile(c, feet + 1)) for c in range(x, x1 + 1)):
        keys += [("down",), ("left", "down"), ("right", "down")]

    hand = [None]
    wrong = goals.wrong_cells(goal, world) if goal["kind"] == "change" else ()
    for c, r, tile in wrong:
        cell = (c, r)
        there = world.raw_tile(*cell)
        if there >= 0:
            tool = "axe" if world.names.get(there) == "Trees" else "pick"
            best = max((i for i in bag if i[tool]), key=lambda i: (i[tool], -i["use_time"]), default=None)
            if best:
                hand.append(("dig", best, None, cell))
        elif there == -1 and tile >= 0 and hands.has_support(cell, world.wall, world.holds):
            hand.extend(("put", i, None, cell) for i in bag if i["place_tile"] == tile)

    pick = max((i for i in bag if i["pick"]), key=lambda i: (i["pick"], -i["use_time"]), default=None)
    if pick:
        hand.extend(("dig", pick, None, c) for c in work.get("dig", ()) if world.raw_tile(*c) >= 0)
    under = work.get("under", ())
    for tag, kind, test in (
        ("put", "put", lambda i: i["block_tile"]), ("platform", "put", lambda i: i["place_tile"] == 19),
        ("rope", "rope", lambda i: i["place_tile"] == 213),
    ):
        if work.get(tag):
            for item in sorted((i for i in bag if test(i)), key=lambda i: material_key(goal, i)):
                first = work[tag][0]
                hand.append((kind, item, None, first))
                landing = work.get("landing", ())
                if tag == "put" and len(landing) > 1 and first == landing[0] and len(landing) <= item["stack"]:
                    hand.append((kind, item, None, tuple(landing)))
                if tag == "put" and first in under:
                    column, row = first
                    pillar = []
                    for cell in work[tag]:
                        if cell != (column, row - len(pillar)) or cell not in under or len(pillar) >= item["stack"]:
                            break
                        pillar.append(cell)
                    if len(pillar) > 1:
                        hand.append((kind, item, None, tuple(pillar)))
    if work.get("open"):
        hand.append(("open", None, None, work["open"][0]))
    hook = None
    if work.get("hook") and not state["body"].get("hook"):
        item = next((i for i in bag if i["hook"]), None)
        if item:
            hook = ("hook", item, None, work["hook"])
            hand.append(hook)

    foes = enemies_of(state)
    for enemy in sorted(foes, key=lambda e: abs(e["x"] - state["body"]["x"]) + abs(e["y"] - state["body"]["y"])):
        for item in bag:
            way = weapons.way(item)
            if not way:
                continue
            ammo = weapons.ammo_for(state["bag"], item) if way == "shoot" else None
            if weapons.affordable(state, item, ammo):
                hand.append((way, weapons.effective(item), ammo, enemy["i"]))

    b = state["body"]
    if b["life"] < b["life_max"] and b["potion_delay"] == 0 and not b.get("potion_used"):
        hand.extend(("potion", i, None, None) for i in bag if i["heal"] > 0)

    moves = [(k, 0, HORIZON, JUMP_HOLD) for k in keys]
    moves += [(k + ("jump",), delay, HORIZON, JUMP_HOLD)
              for delay in (0, 8, 20) for k in keys[:3] if k or not delay]
    if state["physics"].get("wing_time_max", 0) and not state["body"]["wet"]:
        moves += [(k + ("jump",), 0, HORIZON, HORIZON) for k in keys[:3]]
    short_steps = [(k, 0, duration, JUMP_HOLD) for duration in (8, 15) for k in keys[1:3]]
    if wrong:
        # Try short steps with the tool before committing to a full run past the work.
        moves[1:1] = short_steps
    elif foes:
        # Keep standing and both directions in the first batch, followed by short steps.
        moves[3:3] = short_steps
    else:
        moves += short_steps
    if near_rope:
        moves[3:3] = [(k, 0, duration, JUMP_HOLD) for duration in (15, 30)
                      for k in (("down",), ("left", "down"), ("right", "down"))]
    if plan:
        edge = plan.next_edge(plan.node_of(b))
        if edge and edge.get("spot") and b["ground"]:
            dx = sum(edge["spot"]) / 2 - b["x"]
            if abs(dx) > 2:
                moves.insert(3, (("right",) if dx > 0 else ("left",), 0,
                                 hold_for(dx, b["vx"], state["physics"]), JUMP_HOLD))
    if plan:
        edge = plan.next_edge(plan.node_of(b))
        if edge and edge["kind"] == "jump":
            side = "right" if edge["side"] > 0 else "left"
            ticks = max(HORIZON, round(edge["ticks"]))
            yield ((side, "jump"), edge["run"], ticks, edge["hold"]), None
    # Cycle across hand choices and motion choices so a time cut still compares different actions.
    fighting = bool(foes) and goal["kind"] == "defeat"
    if fighting:
        # Cover different controls with the available items before spending
        # a short budget on variants of the same movement duration.
        moves.sort(key=lambda move: (move[2] != HORIZON, move[1] != 0))
        if any(use and use[0] in ("shoot", "swing", "stab", "melee") for use in hand):
            hand.remove(None)
        # One hand choice per movement first. Duplicating each movement with
        # and without an attack used the budget before reaching later controls.
        for turn in range(len(hand)):
            for index, move in enumerate(moves):
                yield move, hand[(index + turn) % len(hand)]
        return
    first_moves = [] if fighting else moves[:6]
    if foes and goal["kind"] == "reach":
        first_moves += [move for move in moves if "jump" in move[0] and len(move[0]) == 2
                        and move[1] in (0, 8) and move not in first_moves]
    for move in first_moves:
        yield move, None
    if hook:
        yield moves[0], hook
    for offset in range(len(moves)):
        for index, use in enumerate(hand):
            n = (index + offset) % len(moves)
            if (use is not None or moves[n] not in first_moves) and not (hook and use is hook and n == 0):
                yield moves[n], use


def finish_time(goal, before, after, result, route):
    """Approximate ticks to finish from one short forecast."""
    if after["remaining"] == 0:
        return result["ticks"]
    if goal["kind"] == "defeat":
        start_hp = sum(e["life"] for e in before["enemies"] if e["name"] == goal["enemy"])
        end_hp = sum(max(0, e["life"]) for e in result["enemies"] if e["name"] == goal["enemy"])
        dealt = start_hp - end_hp
        return result["ticks"] * start_hp / dealt if dealt > 0 else inf
    if route is not None and after["route_ticks"] is not None:
        return result["ticks"] + after["route_ticks"]
    return inf


def contact_pressure(state, result, reaction_ticks, ranged=False):
    """Mean clearance pressure across the roll, with a standalone fallback."""
    if "pressure" in result:
        return result["pressure"]
    b, phys = result["body"], state["physics"]
    pressure = 0.0
    for e in result["enemies"]:
        if e["life"] <= 0:
            continue
        dx = max(0, e["x"] - b["x"] - body.W, b["x"] - e["x"] - e["w"])
        dy = max(0, e["y"] - b["y"] - body.H, b["y"] - e["y"] - e["h"])
        harm = enemies.touch_damage(e, state["body"]["defense"],
                                    state["body"].get("defense_eff", .5),
                                    state["body"].get("damage_reduction", 0))
        pressure += harm * enemies.proximity(b, e, hypot(dx, dy), phys, reaction_ticks, ranged)
    return pressure


def healing_value(state, result, use, attack_eta):
    """Health worth recovering for this fight, less unused potion capacity.

    Recent real hits keep a zero-damage short forecast from erasing ongoing
    pressure. Easy fights keep their potions. A point of healing wasted costs
    the same as a point recovered; predicted death remains the first priority.
    """
    if not use or use[0] != "potion" or not result["healed"]:
        return 0.0
    recent = sum(h["damage"] for h in state.get("hits", ())
                 if state["tick"] - HORIZON < h["tick"] <= state["tick"]) / HORIZON
    rate = max(recent, result["damage"] / result["ticks"])
    needed = rate * attack_eta if rate else 0
    unhealed = result["body"]["life"] - result["healed"]
    useful = min(result["healed"], max(0, needed - unhealed))
    return max(0, useful - max(0, use[1]["heal"] - result["healed"]))


def rank(goal, state, result, estimate, material, distance, preserve, advancing=None):
    if goal["kind"] == "maintain":
        x, _, _, y = body.cells_of(result["body"])
        x0, y0, x1, y1 = goal["area"]
        outside = max(0, x0 - x, x - x1) + max(0, y0 - y, y - y1)
        motion = abs(state["body"]["x"] - result["body"]["x"]) + abs(state["body"]["y"] - result["body"]["y"])
        return (result["body"]["life"] <= 0, max(0, result["damage"] - result["healed"]),
                result.get("settling_damage", 0), result.get("wet_wait", False), outside,
                result.get("contacts", 0), -result.get("dealt", 0), motion,
                material + result["shots"] + int(result["healed"] > 0))
    # Estimate the time before running out of health at this forecast's damage rate.
    life = state["body"]["life"] + result["healed"]
    damage = result["damage"]
    survival = result["ticks"] * life / damage if damage else inf
    dies = result["body"]["life"] <= 0 or result.get("unknown_landing", False)
    finishes_first = estimate < inf and estimate < survival
    work = result["measurement"]["remaining"]
    if goal["kind"] == "defeat" and "pressure" in result:
        recovery = result.get("healing_value", 0)
        useful = result.get("dealt", 0) > 0 or recovery > 0
        movement = abs(state["body"]["x"] - result["body"]["x"]) + abs(state["body"]["y"] - result["body"]["y"])
        return (dies, work != 0, max(0, damage - recovery), result.get("contacts", 0),
                not useful, result["pressure"], -recovery, estimate, -result.get("dealt", 0),
                preserve, material + result["shots"] + int(result["healed"] > 0),
                (0 if result.get("ranged") else movement) if useful else distance)
    # A partial tool hit can matter even when the tile is still present.
    partial = sum(result["hits_on"].values()) if goal["kind"] == "change" else 0
    if goal["kind"] == "change":
        return (dies, not finishes_first, work, -partial, damage, result.get("contacts", 0), result.get("head_underwater", False),
                estimate, distance, preserve, material)
    if advancing is not None:
        return (dies, damage, result["contacts"], not advancing, estimate, distance,
                result["shots"], -result["dealt"], preserve, material)
    if finishes_first:
        if goal["kind"] == "defeat":
            return (dies, False, damage, result.get("contacts", 0), estimate, work, preserve, material, distance)
        return (dies, False, estimate, work, -partial, damage, result.get("contacts", 0), preserve, material, distance)
    return (dies, True, damage, result.get("contacts", 0), work, -partial, distance, preserve, material, estimate)


def torch_placement(state, world, goal, bag, phys, move):
    b = state["body"]
    if state.get("light", 1) >= DARK or b["item_animation"]:
        return None
    torch = min((i for i in bag if i["place_tile"] == TORCH_TILE and i["slot"] < 10),
                key=lambda i: material_key(goal, i), default=None)
    if torch is None:
        return None
    left, right, top, feet = body.cells_of(b)
    keys = move[0]
    ahead = right + 1 if "right" in keys else left - 1 if "left" in keys else left
    middle = (top + feet) // 2
    cells = ((x, y) for x in range(left - 3, right + 4) for y in range(top, feet + 2))
    # Torches can rest on a floor, a side or a back wall.
    cell = min(((x, y) for x, y in cells if world.raw_tile(x, y) == -1 and world.raw_liquid(x, y) == -1
                and (world.wall(x, y) or world.holds(x - 1, y) or world.holds(x + 1, y) or world.holds(x, y + 1))
                and hands.can_place((x, y), b, phys, torch, world.raw_tile, world.wall, world.holds) is None),
               key=lambda c: abs(c[0] - ahead) + abs(c[1] - middle), default=None)
    return ("put", torch, None, cell) if cell is not None else None


def script(move, use, result, *, torch=False, idle_slot=None):
    keys, jump_at, hold, jump_hold = move
    commit = result["ticks"]
    hand = {}
    placements = ()
    placement = 0
    stabs = dict(result.get("stabs_at", ()))
    if use:
        kind, item, _, target = use
        if item and kind != "hook":
            hand["slot"] = item["slot"]
        if kind == "put" and isinstance(target[0], tuple):
            placements = result["puts_at"]
            hand["aim_cell"] = placements[0][1]
        elif kind in ("put", "dig", "rope", "hook", "open"):
            hand["aim_cell"] = result["swap"].get(target, target)
        elif result["aim"]:
            hand["aim"] = result["aim"]
    steps = []
    for t in range(1, commit + 1):
        down = motion_keys(keys, t, jump_at, hold, jump_hold)
        using = use and t <= (1 if torch else commit if kind in ("shoot", "swing", "stab", "melee") else hold)
        if result.get("resume_use"):
            current = use if t < result["resume_at"] else result["resume_use"]
            kind, item = current[:2]
            hand = {"slot": item["slot"]}
            using = t == result["potion_at"] if t < result["resume_at"] else True
            if t >= result["resume_at"] and result["aim"]:
                hand["aim"] = result["aim"]
        if use and kind == "stab" and (not result.get("resume_use") or t >= result["resume_at"]):
            using = t in stabs
            if using:
                hand["aim"] = stabs[t]
        if using:
            if placements and placement + 1 < len(placements) and t > placements[placement][0]:
                placement += 1
                hand["aim_cell"] = placements[placement][1]
            if kind in ("hook", "open"):
                if t == 1:
                    down.append("interact" if kind == "open" else "hook")
            else:
                down.append("use")
        step = dict(hand, keys=down) if using or result.get("resume_use") else {"keys": down}
        if idle_slot is not None and (torch or use is None) and not using:
            step["slot"] = idle_slot
        if steps and all(steps[-1].get(k) == v for k, v in step.items()) and len(step) + 1 == len(steps[-1]):
            steps[-1]["ticks"] += 1
        else:
            steps.append(dict(step, ticks=1))
    return steps


def pressed_ticks(steps):
    return [frozenset(step["keys"]) for step in steps for _ in range(step["ticks"])]


def project(state, world, ticks, active_steps, applied_tick):
    """Roll the observed body forward to the scheduled handoff."""
    if ticks <= 0:
        return state
    controls = [step for step in active_steps for _ in range(step["ticks"])]
    offset = state["tick"] - applied_tick + 1
    prefix = [controls[i] if 0 <= i < len(controls) else {"keys": []} for i in range(offset, offset + ticks)]
    observed = state["body"]
    phys = dict(state["physics"], defense=observed["defense"], defense_eff=observed["defense_eff"])
    jumping = 0 < offset <= len(controls) and "jump" in controls[offset - 1]["keys"]
    body_at_read = dict(observed, jump_down=jumping, released=not jumping)
    result = play(dict(state, body=body_at_read), world, enemies_of(state), (), None, phys, state["strength"],
                  {}, 0, 0, 0, lambda *_: False, prefix=prefix, projecting=True)
    predicted = dict(observed, **result["body"])
    moved_enemies = {e["i"]: e for e in result["enemies"]}
    enemies_at_handoff = []
    for e in state["enemies"]:
        moved = moved_enemies.get(e["i"])
        if moved is None:
            enemies_at_handoff.append(e)
        else:
            enemies_at_handoff.append(dict(e, x=moved["x"], y=moved["y"], vx=moved["vx"], vy=moved["vy"],
                                           life=moved["life"], damage=moved["damage"], defense=moved["defense"],
                                           melee_immune=moved.get("melee_immune", 0), owner_immune=moved.get("owner_immune", 0),
                                           dir=moved.get("dir", e.get("dir", 0)), dir_y=moved.get("dir_y", e.get("dir_y", 0)),
                                           old_x=moved.get("old_x", e.get("old_x")),
                                           old_vx=moved.get("old_vx", e.get("old_vx")),
                                           old_vy=moved.get("old_vy", e.get("old_vy")),
                                           wet=moved.get("wet", e.get("wet", False)),
                                           collide_x=moved.get("collide_x", False), collide_y=moved.get("collide_y", False),
                                           **({"local_ai": moved["local_ai"]} if moved.get("local_ai") is not None else {}),
                                           motion={"vx": moved["vx"], "vy": moved["vy"], "source": "projected"}))
    bag = [dict(item, stack=item["stack"] - result.get("used", {}).get(item["slot"], 0)) for item in state["bag"]]
    return dict(state, body=predicted, enemies=enemies_at_handoff, shots=result["projectiles"],
                bag=[item for item in bag if item["stack"] > 0])


def decide(state, world, goal, baseline, plan, target, timing, deadline, continuing=(), observed_body=None, air_route=None,
           reaction_ticks=REVIEW_TICKS * 2):
    with timing.span("fast.generate"):
        bag, _ = items_for(state, goal)
        start = state["body"]
        waiting = goal.get("waiting")
        escaping = air_route is not None and (
            (waiting and start["wet"]) or
            (not pathfinder.can_breathe(world, start) and (
                waiting or plan is None or plan.dry
                or pathfinder.air_needed(plan, start) + REVIEW_TICKS >= start["breath"] * pathfinder.AIR_TICKS_PER_POINT)))
        move_plan = air_route if escaping else plan
        move_goal = dict(goal, kind="reach") if escaping else goal
        work_by_route = {route: route.work_at(route.node_of(start)) for route in (plan, air_route) if route}
        work = work_by_route.get(move_plan, {})
        foes = enemies_of(state)
        moves = list(candidates(state, world, move_goal, move_plan, work, bag))
        observed = observed_body if observed_body is not None else state["body"]
        hook = observed.get("hook")
        if hook and (hook["state"] == "flying" or
                     hook["state"] == "latched" and (observed["vx"] or observed["vy"])):
            # A jump during the pull releases the hook without the jump from rest.
            moves = [(move, use) for move, use in moves if "jump" not in move[0]]
            if any("jump" in keys for keys in continuing):
                continuing = ()
        phys = dict(state["physics"], defense=state["body"]["defense"], defense_eff=state["body"]["defense_eff"])
        operation = goal.get("operation")
        if not escaping and operation in ("craft", "wear", "life_crystal", "chest", "door", "use"):
            body_box = state["body"]
            cell = goal.get("cell")
            in_reach = cell is None or hands.in_reach(body_box, cell, phys)
            if in_reach and operation in ("craft", "wear"):
                return {"endpoint": "/" + operation, "payload": {"item": goal["item"]}, "label": operation}
            if in_reach and operation == "life_crystal":
                item = next((i for i in bag if i["name"] == "Life Crystal"), None)
                if item:
                    return prepare(state, item) or {"steps": [{"ticks": REVIEW_TICKS, "keys": ["use"], "slot": item["slot"]}],
                                                    "label": "use Life Crystal"}
            if in_reach and operation == "use":
                item = next((i for i in bag if i["name"] == goal["item"]), None)
                if item and weapons.affordable(state, item):
                    aim = goal.get("aim", [start["x"]+start["w"]/2, start["y"]+start["h"]/2])
                    return prepare(state, item) or {"steps": [{"ticks": 1, "keys": ["use"], "slot": item["slot"], "aim": aim}],
                                                    "label": "use " + item["name"]}
            if in_reach and operation in ("chest", "door"):
                return {"steps": [{"ticks": 1, "keys": ["interact"], "aim_cell": cell}],
                        "label": f"open {operation}"}
    forecasts, movement = [], {}
    with timing.span("motion.predict"):
        motion_paths = motion.paths(state, foes, world, HORIZON, movement)
    def stop(b, put, dug, ropes):
        if move_plan and move_plan.dry:
            x, _, _, y = body.cells_of(b)
            return (pathfinder.can_breathe(world, b, put, dug) and not b["wet"]
                    and b["ground"] and move_plan.at(x, y) == 0)
        if goal["kind"] == "reach":
            return goals.at_place(goal, b)
        if goal["kind"] == "change":
            return not goals.wrong_cells(goal, world, put | dict.fromkeys(ropes, 213), dug)
        return False

    with timing.span("fast.predict"):
        forecast_seconds = 0.0
        if continuing and not (goal["kind"] == "defeat" and any(use for _, use in moves)) and all(
                not keys.intersection(("use", "interact", "hook")) for keys in continuing):
            prefix = list(continuing) + [frozenset()] * max(0, HORIZON - len(continuing))
            result = play(state, world, foes, (), None, phys, state["strength"], work, 0, 0, 0, stop,
                          prefix=prefix, land=move_plan is not None or air_route is not None, movement=movement,
                          spacing_ticks=reaction_ticks if goal["kind"] == "defeat" else 0)
            forecasts.append((None, None, result))
        for move, use in moves:
            if forecasts and timing.now() + forecast_seconds >= deadline:
                break
            before = timing.now()
            keys, jump_at, hold, jump_hold = move
            result = play(state, world, foes, keys, use, phys, state["strength"], work, jump_at, hold, jump_hold, stop,
                          horizon=max(HORIZON, hold), land=move_plan is not None or air_route is not None, movement=movement,
                          spacing_ticks=reaction_ticks if goal["kind"] == "defeat" else 0)
            forecasts.append((move, use, result))
            forecast_seconds = max(forecast_seconds, timing.now() - before)
    with timing.span("fast.filter"):
        eligible = []
        for move, use, result in forecasts:
            if use and use[0] in ("shoot", "swing", "stab", "melee") and not result["hits"] and goal["kind"] != "defeat":
                continue
            if use and use[0] == "dig" and not result["hits_on"]:
                continue
            if use and use[0] in ("put", "rope") and not (result["put"] or result["ropes"]):
                continue
            if use and use[0] == "potion" and not result["healed"]:
                continue
            if goal["kind"] == "maintain" and goal.get("area") and not goal.get("waiting") and not escaping:
                if any(not goals.area_contains(goal["area"], int((x - body.W / 2) // 16),
                                               int((y + body.H / 2 - 1) // 16)) for x, y in result["path"]):
                    continue
            eligible.append((move, use, result))
            air_ticks = 0
            air_short = False
            if air_route is not None:
                b = result["body"]
                result["head_underwater"] = not pathfinder.can_breathe(world, b, result["put"], result["dug"])
                result["wet_wait"] = waiting and (b["wet"] or result["head_underwater"])
                if result["head_underwater"] or (escaping and b["wet"]):
                    air_ticks = pathfinder.estimate(air_route, b, result, work=work_by_route[air_route])
                    air_ticks = inf if air_ticks is None else air_ticks
                    air_short = air_ticks + REVIEW_TICKS >= b["breath"] * pathfinder.AIR_TICKS_PER_POINT
                result["air_ticks"] = air_ticks if air_ticks < inf else None
            air_priority = result["ticks"] + air_ticks if escaping else air_ticks if air_short else 0
            result["air_rank"] = (air_short, air_priority)
    if goal["kind"] == "maintain" and foes and not escaping:
        with timing.span("fast.settle"):
            def waiting_rank(candidate):
                result = candidate[2]
                material = len(result["put"]) + len(result["ropes"])
                base = rank(goal, state, result, None, material, 0, 0)
                return (base[0], *result["air_rank"], *base[1:])

            ordered = sorted(eligible, key=waiting_rank)
            if ordered and ordered[0][2]["body"]["life"] > 0:
                safest = waiting_rank(ordered[0])[:4]
                compared = []
                for candidate in ordered:
                    if waiting_rank(candidate)[:4] != safest:
                        break
                    result = candidate[2]
                    result["settling_damage"] = settling_damage(state, world, result, phys, SETTLE_TICKS)
                    compared.append(candidate)
                    if result["settling_damage"] == 0:
                        break  # Later moves cannot beat this one once settling damage is zero.
                eligible = compared
    with timing.span("fast.choose"):
        best = None
        crossing = bool(foes) and goal["kind"] == "reach" and target is not None
        if crossing:
            start_body = state["body"]
            start_route = pathfinder.estimate(plan, start_body)
            start_distance = abs(start_body["x"] - target[0] * 16) + abs(start_body["y"] + body.H - (target[1] + 1) * 16)
        def measure_result(move, use, result):
            b = result["body"]
            estimate = pathfinder.estimate(plan, b, result, work=work_by_route.get(plan))
            result["measurement"] = goals.measure(goal, state, world, baseline, estimate, result)
            eta = finish_time(goal, state, result["measurement"], result, plan)
            if plan and plan.dry and estimate is None:
                eta = inf
            if target is None and goal["kind"] == "defeat":
                targets = [e for e in result["enemies"] if e["name"] == goal["enemy"] and e["life"] > 0]
                distance = min((abs(b["x"] - e["x"]) + abs(b["y"] - e["y"]) for e in targets), default=0)
            else:
                distance = abs(b["x"] - target[0] * 16) + abs(b["y"] + body.H - (target[1] + 1) * 16) if target else 0
            item = use[1] if use else None
            material = len(result["put"]) + len(result["ropes"])
            preserve = material * goal.get("preserve", {}).get(item["name"], 0) if item else 0
            advancing = None
            if crossing:
                advancing = (result["measurement"]["remaining"] == 0 or
                             (estimate < start_route if estimate is not None and start_route is not None else
                              distance < start_distance))
            return move, use, result, eta, material, distance, preserve, advancing

        evaluated = [measure_result(*candidate) for candidate in eligible]
        attacks = [candidate for candidate in evaluated if candidate[1]
                   and candidate[1][0] in ("shoot", "swing", "stab", "melee")]
        attack_eta = min((candidate[3] for candidate in attacks), default=inf)

        def scored(candidate):
            move, use, result, eta, material, distance, preserve, advancing = candidate
            if goal["kind"] == "defeat":
                attack = result.get("resume_use") or use
                held = next((i for i in state["bag"] if i["id"] == state["body"].get("held_item")), None)
                result["ranged"] = bool(attack and attack[0] == "shoot" or
                                        attack is None and held and weapons.way(held) == "shoot")
                result["pressure"] = contact_pressure(state, result, reaction_ticks, result["ranged"])
                result["healing_value"] = healing_value(state, result, use, attack_eta)
            score = rank(goal, state, result, eta, material, distance, preserve, advancing)
            score = (score[0], *result["air_rank"], *score[1:])
            return score, move, use, result

        for candidate in evaluated:
            score, move, use, result = scored(candidate)
            if best is None or score < best[0]:
                best = (score, move, use, result)
    if best and best[2] and best[2][0] == "potion" and goal["kind"] == "defeat":
        # Refine the selected healing move with a continuation already explored
        # for this motion. One extra roll avoids multiplying every potion by
        # every weapon and enemy, and preserves the normal next-round review.
        follow = [candidate for candidate in attacks if candidate[1][1]["slot"] < 10]
        same_motion = [candidate for candidate in follow if candidate[0] == best[1]]
        if follow and timing.now() + forecast_seconds < deadline:
            continuation = min(same_motion or follow, key=lambda candidate: scored(candidate)[0])[1]
            _, move, use, result = best
            keys, jump_at, hold, jump_hold = move
            with timing.span("fast.heal_resume"):
                refined = play(state, world, foes, keys, use, phys, state["strength"], work,
                               jump_at, hold, jump_hold, stop, horizon=max(HORIZON, hold),
                               land=move_plan is not None or air_route is not None,
                               movement=movement, follow=continuation,
                               spacing_ticks=reaction_ticks if goal["kind"] == "defeat" else 0)
                refined["air_rank"] = result["air_rank"]
                choices = [scored(measure_result(move, use, refined))]
                choices.extend(scored(candidate) for candidate in evaluated
                               if not candidate[1] or candidate[1][0] != "potion")
                best = min(choices, key=lambda choice: choice[0])
    torch_slots = {i["slot"] for i in bag if i["place_tile"] == TORCH_TILE and i["slot"] < 10}
    # Leave the torch slot after placing. A held torch would mask when the cave goes dark again.
    idle_slot = next((slot for slot in range(10) if slot not in torch_slots), None) if torch_slots else None
    place_torch = False
    if best and best[1] is not None and best[2] is None and idle_slot is not None:
        _, move, _, result = best
        if not (result["damage"] or result["unknown_landing"] or result.get("settling_damage")):
            with timing.span("fast.light"):
                torch = torch_placement(state, world, goal, bag, phys, move)
                if torch:
                    keys, jump_at, hold, jump_hold = move
                    lit = play(state, world, foes, keys, torch, phys, state["strength"], work,
                               jump_at, hold, jump_hold, stop, horizon=max(HORIZON, hold),
                               land=move_plan is not None or air_route is not None)
                    if lit["puts_at"] == [(1, torch[3])] and not lit["damage"] and lit["path"] == result["path"]:
                        lit["measurement"] = result["measurement"]
                        if "air_ticks" in result:
                            lit["air_ticks"] = result["air_ticks"]
                        if "settling_damage" in result:
                            lit["settling_damage"] = result["settling_damage"]
                        best = (best[0], move, torch, lit)
                        place_torch = True
    with timing.span("fast.command"):
        if best is None:
            return {"steps": [{"ticks": REVIEW_TICKS, "keys": []}], "label": "hold position"}
        _, move, use, result = best
        if move is None:
            return {"label": "continue current command", "path": result["path"],
                    "settling_damage": result.get("settling_damage")}
        if use and use[1] and use[0] != "hook":
            preparation = prepare(state, use[1])
            if preparation:
                return preparation
        label = "place torch" if place_torch else "potion then " + result["resume_use"][0] if result.get("resume_use") else use[0] if use else ""
        return {
            "steps": script(move, use, result, torch=place_torch, idle_slot=idle_slot),
            "label": " + ".join((*move[0], label)).strip(" +") or "stand",
            "path": result["path"], "settling_damage": result.get("settling_damage"), "forecast": {
                "damage": result["damage"], "healing": result["healed"], "dealt": result["dealt"],
                "hit_events": result["hit_events"],
                "unknown_landing": result["unknown_landing"],
                "put": list(result["put"].items()), "dug": sorted(result["dug"]), "ammo": result["shots"],
                "ticks": result["ticks"], "breath": result["body"]["breath"],
                "pressure": result.get("pressure"), "healing_value": result.get("healing_value"),
                "resume_tick": result.get("resume_at"),
                "air_ticks": result.get("air_ticks"), "escaping": escaping, **result["measurement"],
            },
            "candidates": {"generated": len(moves), "predicted": len(forecasts)},
            "motion_paths": motion_paths,
        }


def prepare(state, item):
    if item["slot"] < 10:
        return None
    occupied = {i["slot"] for i in state["bag"]}
    slot = next((s for s in range(10) if s not in occupied), 9)
    return {"endpoint": "/bag", "payload": {"from": item["slot"], "to": slot}, "label": f"ready {item['name']}"}
