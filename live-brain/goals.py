"""Goal completion from observations, and progress for observations and forecasts."""
from rules.body import cells_of
from rules import hands


KINDS = {"reach", "change", "quantity", "defeat", "interact", "search", "maintain"}


def quantity(state, item):
    return sum(row["stack"] for row in state["bag"] if row["name"] == item)


def area_contains(area, x, y):
    return area[0] <= x <= area[2] and area[1] <= y <= area[3]


def waiting_goal(state):
    x, _, _, y = cells_of(state["body"])
    return {"kind": "maintain", "waiting": True, "label": "Stay alive while waiting",
            "area": (x - 40, y - 20, x + 40, y + 20)}


def at_place(goal, body):
    x, _, _, feet = cells_of(body)
    area = goal.get("area")
    inside = area_contains(area, x, feet) if area else (x, feet) == tuple(goal["cell"])
    return inside and (body["ground"] or not goal.get("ground", True))


def cells_in(area):
    x0, y0, x1, y1 = area
    return ((x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))


def wrong_cells(goal, world, put=(), dug=()):
    return [(x, y, tile) for x, y, tile in goal["cells"] if world.tile(x, y, put, dug) != tile]


def target_kills(state, goal):
    return state["counters"]["kills_by_name"].get(goal["enemy"], 0)


def begin(goal, state):
    if goal["kind"] == "quantity":
        return quantity(state, goal["item"]) if goal.get("gain", False) else 0
    if goal["kind"] == "defeat":
        return target_kills(state, goal)
    if goal["kind"] == "interact":
        if goal["operation"] == "craft" or goal["operation"] == "use" and "consume" in goal:
            return quantity(state, goal["item"])
        return state["progress"]["life_max_perm"]
    return state["tick"]


def measure(goal, state, world, baseline, estimate=None, forecast=None):
    """Remaining units, plus route ticks where a route applies."""
    k = goal["kind"]
    body = forecast["body"] if forecast else state["body"]
    if k == "reach":
        remaining = int(not at_place(goal, body))
    elif k == "change":
        put = dict(forecast["put"]) if forecast else {}
        if forecast:
            put.update((cell, 213) for cell in forecast["ropes"])
        remaining = len(wrong_cells(goal, world, put, forecast["dug"] if forecast else ()))
    elif k == "quantity":
        # The bag alone establishes pickup. Forecasts price approaching a drop.
        remaining = max(0, goal["count"] + baseline - quantity(state, goal["item"]))
    elif k == "defeat":
        kills = target_kills(state, goal) - baseline
        if forecast:
            kills += sum(e["life"] <= 0 for e in forecast["enemies"] if e["name"] == goal["enemy"])
        remaining = max(0, goal.get("count", 1) - kills)
    elif k == "search":
        unknown = 0
        remaining = 1
        for x, y in cells_in(goal["area"]):
            tile = world.raw_tile(x, y)
            if tile >= 0 and world.names.get(tile) == goal.get("tile"):
                remaining = 0
                break
            unknown += tile == -2
        else:
            remaining = unknown
    elif k == "maintain" and goal.get("waiting"):
        remaining = None
    elif k == "maintain":
        remaining = max(0, goal["ticks"] - (state["tick"] - baseline))
        if goal.get("until_day") is not None:
            remaining += int(state["time"]["day"] != goal["until_day"])
    elif k == "interact":
        operation = goal["operation"]
        if operation == "craft":
            remaining = max(0, goal.get("count", 1) + baseline - quantity(state, goal["item"]))
        elif operation == "wear":
            remaining = int(not goal["item"] in state["worn"])
        elif operation == "door":
            remaining = int(world.names.get(world.raw_tile(*goal["cell"])) != goal["tile"])
        elif operation == "chest":
            # Looting completes from the native transfer receipt, rather than opening alone.
            remaining = (1 if goal.get("take", True) else
                         int(state["body"]["chest"] < 0 if "chest_id" not in goal
                             else state["body"]["chest"] != goal["chest_id"]))
        elif operation == "life_crystal":
            remaining = int(state["progress"]["life_max_perm"] <= baseline)
        elif operation == "use":
            if "consume" in goal:
                remaining = max(0, goal["consume"] + quantity(state, goal["item"]) - baseline)
            else:
                owner = state["body"].get("player_id", 0)
                remaining = max(0, goal.get("count", 1) - sum(
                    s["id"] == goal["projectile"] and s.get("owner") == owner for s in state["shots"]))
        else:
            raise ValueError(f"unknown interaction {operation}")
    else:
        raise ValueError(f"unknown goal {k}")
    return {"remaining": remaining, "route_ticks": estimate}


def chest_result(receipt):
    """The native transfer reports what entered the bag and what could not fit."""
    if receipt.get("stopped"):
        return "failed", receipt["stopped"]
    if "took" not in receipt or "remaining" not in receipt:
        return "failed", "update AgentIO: chest transfer receipt is required"
    if receipt["remaining"]:
        return "failed", "inventory could not take: " + ", ".join(receipt["remaining"])
    return "done", "collected " + ", ".join(receipt["took"]) if receipt["took"] else "chest is empty"


def blockers(goal, state, world):
    """Explain known constraints at the final observation, without guessing a cause."""
    found = []
    if goal["kind"] == "change":
        for x, y, tile in wrong_cells(goal, world):
            if tile < 0:
                if world.raw_tile(x, y) >= 0 and not world.can_break(x, y):
                    found.append({"cell": [x, y], "reason": "the game prevents breaking this cell",
                                  "source": "game_mining_permission"})
                continue
            if world.raw_tile(x, y) == -2:
                continue
            item = next((i for i in state["bag"] if i["place_tile"] == tile), None)
            reasons = ["no carried item places the requested tile"] if item is None else []
            if item and item.get("block_tile"):
                empty_cut = lambda a, b: -1 if world.raw_tile(a, b) in world.cut else world.raw_tile(a, b)
                # Unknown neighbors cannot establish that support is missing.
                possible_support = lambda a, b: world.raw_tile(a, b) == -2 or world.holds(a, b)
                reasons = hands.placement_reasons((x, y), state["body"], state["physics"], item,
                                                  empty_cut, world.wall, possible_support)
            found.extend({"cell": [x, y], "reason": why, "source": "placement_rule"} for why in reasons)
    elif goal["kind"] == "interact" and goal["operation"] == "door" and goal["tile"] == "OpenDoor":
        x, y = goal["cell"]
        if world.names.get(world.raw_tile(x, y)) != "ClosedDoor":
            return found
        top = bottom = y
        while world.door(x, top - 1):
            top -= 1
        while world.door(x, bottom + 1):
            bottom += 1
        # A normal closed door occupies three cells. Incomplete/stacked objects stay unexplained.
        if bottom - top == 2:
            sides = [[(x + side, row) for row in range(top, bottom + 1)
                      if world.raw_tile(x + side, row) >= 0 and world.solid(x + side, row)
                      and world.raw_tile(x + side, row) not in world.cut] for side in (-1, 1)]
            if all(sides):
                found.append({"cell": [x, y], "reason": "solid tiles obstruct both door swing spaces",
                              "blocking_cells": [[a, b, world.names.get(world.raw_tile(a, b))]
                                                 for side in sides for a, b in side],
                              "source": "door_terrain_rule"})
    return found


def check(goal, state, world, baseline, tick0):
    if state["body"]["dead"]:
        return "died", "the player died"
    if goal.get("waiting"):
        return None
    if goal["kind"] == "maintain" and goal.get("area"):
        x, _, _, feet = cells_of(state["body"])
        if not area_contains(goal["area"], x, feet):
            return "failed", "the player left the requested area"
    if measure(goal, state, world, baseline)["remaining"] == 0:
        if goal["kind"] == "search":
            found = any(world.names.get(world.raw_tile(x, y)) == goal["tile"]
                        for x, y in cells_in(goal["area"]))
            return "done", "target seen" if found else "patch covered"
        return "done", "observed goal satisfied"
    if goal["kind"] == "quantity" and not any(i["name"] == goal["item"] for i in state["items"]):
        have = quantity(state, goal["item"])
        target = goal["count"] + baseline
        return "failed", f"{have}/{target} {goal['item']} collected; no matching drops remain"
    if state["tick"] - tick0 >= goal["limit_ticks"]:
        return "timeout", "game tick budget reached"
    return None
