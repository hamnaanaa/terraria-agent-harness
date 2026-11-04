"""Generate, forecast and choose the next short input script."""
from math import inf

import goals
import pathfinder
from rules import body, enemies, hands, weapons
from rules.forecast import HORIZON, enemies_of, hold_for, motion_keys, play, settling_damage

REVIEW_TICKS = 15
SETTLE_TICKS = 120
JUMP_HOLD = 16


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
    elif any(world.top_kind.get(world.raw_tile(c, feet + 1)) for c in range(x, x1 + 1)):
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
        elif there == -1 and tile >= 0:
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
            if way != "shoot" or ammo:
                hand.append((way, item, ammo, enemy["i"]))

    b = state["body"]
    if b["life"] < b["life_max"] and b["potion_delay"] == 0:
        hand.extend(("potion", i, None, None) for i in bag if i["heal"] > 0)

    moves = [(k, 0, HORIZON, JUMP_HOLD) for k in keys]
    moves += [(k + ("jump",), delay, HORIZON, JUMP_HOLD)
              for delay in (0, 8, 20) for k in keys[:3] if k or not delay]
    moves += [(k, 0, duration, JUMP_HOLD) for duration in (8, 15) for k in keys[1:3]]
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
    first_moves = moves[:6]
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


def rank(goal, state, result, estimate, material, distance, preserve, advancing=None):
    if goal.get("waiting"):
        x, _, _, y = body.cells_of(result["body"])
        x0, y0, x1, y1 = goal["area"]
        outside = max(0, x0 - x, x - x1) + max(0, y0 - y, y - y1)
        motion = abs(state["body"]["x"] - result["body"]["x"]) + abs(state["body"]["y"] - result["body"]["y"])
        return (result["body"]["life"] <= 0, max(0, result["damage"] - result["healed"]),
                result.get("settling_damage", 0), outside, motion,
                material + result["shots"] + int(result["healed"] > 0))
    # Estimate the time before running out of health at this forecast's damage rate.
    life = state["body"]["life"] + result["healed"]
    damage = result["damage"]
    survival = result["ticks"] * life / damage if damage else inf
    dies = result["body"]["life"] <= 0 or result.get("unknown_landing", False)
    finishes_first = estimate < inf and estimate < survival
    work = result["measurement"]["remaining"]
    # A partial tool hit can matter even when the tile is still present.
    partial = sum(result["hits_on"].values()) if goal["kind"] == "change" else 0
    if goal["kind"] == "change":
        return (dies, not finishes_first, work, -partial, damage, estimate, distance, preserve, material)
    if advancing is not None:
        return (dies, not advancing, damage, estimate, distance, result["shots"], preserve, material)
    if finishes_first:
        if goal["kind"] == "defeat":
            return (dies, False, damage, estimate, work, preserve, material, distance)
        return (dies, False, estimate, work, -partial, damage, preserve, material, distance)
    return (dies, True, damage, work, -partial, distance, preserve, material, estimate)


def script(move, use, result):
    keys, jump_at, hold, jump_hold = move
    commit = result["ticks"]
    hand = {}
    placements = ()
    placement = 0
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
        if use and t <= hold:
            if placements and placement + 1 < len(placements) and t > placements[placement][0]:
                placement += 1
                hand["aim_cell"] = placements[placement][1]
            if kind in ("hook", "open"):
                if t == 1:
                    down.append("interact" if kind == "open" else "hook")
            else:
                down.append("use")
        step = dict(hand, keys=down) if use and t <= hold else {"keys": down}
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
    controls = pressed_ticks(active_steps)
    offset = state["tick"] - applied_tick + 1
    prefix = [controls[i] if 0 <= i < len(controls) else frozenset() for i in range(offset, offset + ticks)]
    observed = state["body"]
    phys = dict(state["physics"], defense=observed["defense"], defense_eff=observed["defense_eff"])
    body_at_read = dict(observed, jump_down=0 < offset <= len(controls) and "jump" in controls[offset - 1],
                        released=not (0 < offset <= len(controls) and "jump" in controls[offset - 1]))
    result = play(dict(state, body=body_at_read), world, enemies_of(state), (), None, phys, state["strength"],
                  {}, 0, 0, 0, lambda *_: False, prefix=prefix)
    predicted = dict(observed, **result["body"])
    moved_enemies = {e["i"]: e for e in result["enemies"]}
    enemies_at_handoff = []
    for e in state["enemies"]:
        moved = moved_enemies.get(e["i"])
        if moved is None:
            enemies_at_handoff.append(e)
        else:
            enemies_at_handoff.append(dict(e, x=moved["x"], y=moved["y"], vx=moved["vx"], vy=moved["vy"],
                                           timers=[moved["timer"], *(e.get("timers") or [])[1:]]))
    return dict(state, body=predicted, enemies=enemies_at_handoff)


def decide(state, world, goal, baseline, plan, target, timing, deadline, continuing=(), observed_body=None):
    with timing.span("fast.generate"):
        bag, _ = items_for(state, goal)
        work = plan.work_at(plan.node_of(state["body"])) if plan else {}
        foes = enemies_of(state)
        moves = list(candidates(state, world, goal, plan, work, bag))
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
        if operation in ("craft", "wear", "life_crystal", "chest", "door"):
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
            if in_reach and operation in ("chest", "door"):
                return {"steps": [{"ticks": 1, "keys": ["interact"], "aim_cell": cell}],
                        "label": f"open {operation}"}
    forecasts = []
    def stop(b, put, dug, ropes):
        if plan and plan.dry:
            x, _, _, y = body.cells_of(b)
            return not pathfinder.head_underwater(world, b) and b["ground"] and plan.at(x, y) == 0
        if goal["kind"] == "reach":
            return goals.at_place(goal, b)
        if goal["kind"] == "change":
            return not goals.wrong_cells(goal, world, put | dict.fromkeys(ropes, 213), dug)
        return False

    with timing.span("fast.predict"):
        if continuing and all(not keys.intersection(("use", "interact", "hook")) for keys in continuing):
            prefix = list(continuing) + [frozenset()] * max(0, HORIZON - len(continuing))
            result = play(state, world, foes, (), None, phys, state["strength"], work, 0, 0, 0, stop, prefix=prefix, land=plan is not None)
            forecasts.append((None, None, result))
        for move, use in moves:
            keys, jump_at, hold, jump_hold = move
            result = play(state, world, foes, keys, use, phys, state["strength"], work, jump_at, hold, jump_hold, stop,
                          horizon=max(HORIZON, hold), land=plan is not None)
            forecasts.append((move, use, result))
            if len(forecasts) >= 12 and timing.now() >= deadline:
                break
    with timing.span("fast.filter"):
        eligible = []
        for move, use, result in forecasts:
            if use and use[0] in ("shoot", "swing", "stab", "melee") and not result["hits"]:
                continue
            if use and use[0] == "dig" and not result["hits_on"]:
                continue
            if use and use[0] in ("put", "rope") and not (result["put"] or result["ropes"]):
                continue
            eligible.append((move, use, result))
    if goal.get("waiting") and foes:
        with timing.span("fast.settle"):
            def waiting_rank(candidate):
                result = candidate[2]
                material = len(result["put"]) + len(result["ropes"])
                return rank(goal, state, result, None, material, 0, 0)

            ordered = sorted(eligible, key=waiting_rank)
            if ordered and ordered[0][2]["body"]["life"] > 0:
                safest = waiting_rank(ordered[0])[:2]
                compared = []
                for candidate in ordered:
                    if waiting_rank(candidate)[:2] != safest:
                        break
                    result = candidate[2]
                    result["settling_damage"] = settling_damage(state, world, result, phys, SETTLE_TICKS)
                    compared.append(candidate)
                    if result["settling_damage"] == 0:
                        break  # The remaining moves are already ranked lower on the other measures.
                eligible = compared
    with timing.span("fast.choose"):
        best = None
        crossing = bool(foes) and goal["kind"] == "reach" and target is not None
        if crossing:
            start_body = state["body"]
            start_route = pathfinder.estimate(plan, start_body)
            start_distance = abs(start_body["x"] - target[0] * 16) + abs(start_body["y"] + body.H - (target[1] + 1) * 16)
        for move, use, result in eligible:
            b = result["body"]
            if goal["kind"] == "maintain" and goal.get("area") and not goal.get("waiting"):
                if any(not goals.area_contains(goal["area"], int((x - body.W / 2) // 16),
                                               int((y + body.H / 2 - 1) // 16)) for x, y in result["path"]):
                    continue
            estimate = pathfinder.estimate(plan, b, result)
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
            score = rank(goal, state, result, eta, material, distance, preserve, advancing)
            if best is None or score < best[0]:
                best = (score, move, use, result)
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
        return {
            "steps": script(move, use, result),
            "label": " + ".join((*move[0], use[0] if use else "" )).strip(" +") or "stand",
            "path": result["path"], "settling_damage": result.get("settling_damage"), "forecast": {
                "damage": result["damage"], "healing": result["healed"], "dealt": result["dealt"],
                "unknown_landing": result["unknown_landing"],
                "put": list(result["put"].items()), "dug": sorted(result["dug"]), "ammo": result["shots"],
                "ticks": result["ticks"], **result["measurement"],
            },
            "candidates": {"generated": len(moves), "predicted": len(forecasts)},
            "unvalidated_enemies": sorted({e["name"] for e in foes if not enemies.measured(e["name"])}),
        }


def prepare(state, item):
    if item["slot"] < 10:
        return None
    occupied = {i["slot"] for i in state["bag"]}
    slot = next((s for s in range(10) if s not in occupied), 9)
    return {"endpoint": "/bag", "payload": {"from": item["slot"], "to": slot}, "label": f"ready {item['name']}"}
