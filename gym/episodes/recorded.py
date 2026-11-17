"""Small sections of real worlds, rebuilt in the disposable Gym."""
import json
from math import hypot
from pathlib import Path

import gym_api as G

HERE = Path(__file__).parent


def read(folder, file):
    return json.loads((HERE / folder / (file + ".json")).read_text())


def episode(folder, case, text, verify, *, goal=None, site=None):
    recording = read(folder, "starts")
    start = recording[case]
    x0, y0, x1, y1 = recording["box"]
    sx, sy = site or start.get("site", G.SITE)
    dx, dy = sx - x0, sy - y0
    observation = read(folder, start["observation"]) if "observation" in start else None

    def setup():
        G.in_gym()
        G.call("/run/cancel", {}, timeout=2)
        G.gym(butcher=True, spawns=0, freeze=start.get("freeze", "noon"), background=True,
              feet=G.state(w=2, h=2)["spawn"], clear_projectiles=True)
        tiles = read(folder, "terrain")
        if "terrain_patch" in start:
            tiles += read(folder, start["terrain_patch"])
        # Isolate the copied terrain from falling tiles and liquid in older Gym scenes.
        right, bottom = max(G.W - 1, x1 - x0) + 5, max(G.H - 1, y1 - y0) + 4
        frame = {(x, y) for x in range(-5, right + 1) for y in (-7, bottom)}
        frame.update((x, y) for x in (-5, right) for y in range(-7, bottom + 1))
        tiles += [[x, y, 1, 0, 0, 0, 0, 0, 0] for x, y in frame]
        G.call("/stamp", {"x0": sx, "y0": sy, "tiles": tiles,
                          "reveal": observation is None,
                          "clear": [sx - 4, sy - 6, sx + max(G.W - 1, x1 - x0) + 4,
                                    sy + max(G.H - 1, y1 - y0) + 3]})
        if observation:
            # Reveal each recorded run of known cells, preserving the surrounding fog.
            window = observation["cells"]
            rows = {(t[0], t[1]): t for t in tiles}
            for r, visibility in enumerate(window["seen"]):
                first = None
                for c, seen in enumerate([*visibility, 0]):
                    if seen and first is None:
                        first = c
                    elif not seen and first is not None:
                        G.call("/stamp", {"x0": sx + first, "y0": sy + r,
                            "tiles": [[cc - first, 0, *rows[cc, r][2:]] for cc in range(first, c)]})
                        first = None
        b = start["body"]
        dials = dict(at=[b["x"] + dx * 16, b["y"] + dy * 16], velocity=[b["vx"], b["vy"]],
                     wear=start.get("wear", []), bag=start["bag"], life_max=100, heal=True, life=b["life"], breath=b["breath"],
                     clear_buffs=True)
        spawn = []
        enemies = start.get("enemies", [start["enemy"]] if "enemy" in start else [])
        for original in enemies:
            e = dict(original)
            e.update(x=e["x"] + dx * 16, y=e["y"] + dy * 16)
            spawn.append(["BlueSlime" if e["id"] == 1 else e["name"], int(e["x"] // 16), int(e["y"] // 16), e])
        if spawn:
            dials["spawn"] = spawn
        ack = G.gym(**dials)
        if spawn and sum(s.startswith("spawned ") for s in ack["done"]) != len(spawn):
            raise RuntimeError("the recorded enemy did not spawn")

    if "steps" in start:
        steps = [("/run", {"steps": start["steps"]}, verify)]
    else:
        goal = dict(goal if goal is not None else start["goal"])
        if "cells" in goal:
            goal["cells"] = [[x + dx, y + dy, tile] for x, y, tile in goal["cells"]]
        if "area" in goal:
            goal["area"] = [v + (dy if i % 2 else dx) for i, v in enumerate(goal["area"])]
        steps = [(goal, verify)]
    result = {"text": text, "setup": setup, "goals": lambda: steps,
              "source": {"world": recording["world"], "tick": start["tick"], "box": recording["box"]}}
    if observation:
        result["memory"] = dict(observation["cells"], x0=sx, y0=sy)
    return result


def got_air(before, after):
    return (before["body"]["wet"] and after["body"]["breath"] == 200
            and not any(h["tick"] >= before["tick"] and h["source"] == "drown" for h in after["hits"]))


def finished_dry(before, after):
    return not after["body"]["wet"] and after["body"]["breath"] == 200


def at_most_one_hit(before, after):
    return (any(e["name"] == "Vulture" for e in before["enemies"])
            and sum(h["tick"] >= before["tick"] and h["name"] == "Vulture" for h in after["hits"]) <= 1)


def survived_vulture(before, after):
    return at_most_one_hit(before, after) and not after["body"]["dead"]


def fought_vulture(before, after):
    return (survived_vulture(before, after)
            and after["counters"]["kills_by_name"].get("Vulture", 0)
            > before["counters"]["kills_by_name"].get("Vulture", 0))


def flight_error(before, trace):
    from map import Map
    from rules.forecast import BW, BH, enemies_of, foe_step

    world = Map()
    world.update(before["cells"])
    foe = next((e for e in enemies_of(before) if e["name"] == "Vulture"), None)
    # /run traces are sampled just after the player updates, before the enemies do.
    actual = next((e for e in trace[1]["enemies"] if e[0] == foe["i"]), None) if foe and len(trace) > 1 else None
    if actual is None:
        return {"verified": False, "reason": "no initial vulture sample"}
    foe.update(zip(("x", "y", "vx", "vy", "life", "timer"), actual[1:7]))
    errors = []
    for prior, row in zip(trace[1:], trace[2:]):
        actual = next((e for e in row["enemies"] if e[0] == foe["i"]), None)
        if actual is None:
            return {"verified": False, "reason": "the recorded vulture disappeared"}
        # Feed both the model and the game the same player positions, to isolate the enemy forecast.
        foe_step(foe, prior["x"] + BW / 2, prior["y"] + BH / 2,
                 world.solid, world.floor, world.water, world.door, world.shape)
        errors.append(hypot(foe["x"] - actual[1], foe["y"] - actual[2]))
    error = max(errors, default=float("inf"))
    verified = len(errors) == 27 and error <= 8
    return {"verified": verified, "reason": (f"peak flight error {error:.1f} px (limit 8 px)" if errors
                                              else "no flight samples"),
            "peak_error_px": round(error, 2) if errors else None}


LAKE = {
    "lake_waiting": episode("lake", "waiting", "Get back to air while waiting for a goal.", got_air),
    "lake_bridge": episode("lake", "bridge", "Finish the lake bridge and end above water.", finished_dry),
}
VULTURE = {
    "vulture": episode("vulture", "crossing",
        "Cross the ridge past the vulture. Reach the other side with at most one hit.", at_most_one_hit),
    "vulture_flight": episode("vulture", "flight",
        "Compare predicted vulture flight with the game as it comes close to the ground.", flight_error),
    "vulture_waiting": episode("vulture_approach", "approach",
        "While waiting for a goal, defeat the diving vulture with Scout01's gear and take at most one hit.",
        fought_vulture, goal={"kind": "maintain", "waiting": True, "area": [1742, 130, 1902, 290],
                              "label": "Stay alive while waiting", "limit_ticks": 1200, "expected": "stopped"}),
    "vulture_retreat": episode("vulture_approach", "approach",
        "Head east with a diving vulture nearby. Reach the far side with at most one hit.",
        survived_vulture, goal={"kind": "reach", "area": [1841, 200, 1845, 214], "limit_ticks": 600}),
}
QUARRY = {
    "quarry_escape": episode("quarry", "escape",
        "Climb the full quarry shaft back to the surface with Scout01's recorded gear.",
        lambda before, after: not after["body"]["dead"]
            and before["deaths"]["count"] == after["deaths"]["count"]),
}
def requested_cells(folder):
    source = read(folder, "starts")
    goal = source["replay"]["goal"]
    site = source["replay"].get("site", G.SITE)
    dx, dy = site[0] - source["box"][0], site[1] - source["box"][1]
    cells = goal.get("cells")
    if cells:
        return [(x + dx, y + dy) for x, y, _ in cells]
    x0, y0, x1, y1 = goal["area"]
    return [(x + dx, y + dy) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def native_search_covered(before, after):
    cells = requested_cells("exploration")
    x0, y0 = min(x for x, y in cells), min(y for x, y in cells)
    box = G.state(x0=x0, y0=y0, w=max(x for x, y in cells)-x0+1,
                  h=max(y for x, y in cells)-y0+1)["cells"]
    found = any(seen and box["kinds"].get(str(tile), {}).get("name") == "Heart"
                for visible, tiles in zip(box["seen"], box["tile"]) for seen, tile in zip(visible, tiles))
    return not after["body"]["dead"] and (found or all(all(row) for row in box["seen"]))


def native_iron_cleared(before, after):
    cells = requested_cells("platform_climb")
    x0, y0 = min(x for x, y in cells), min(y for x, y in cells)
    box = G.state(x0=x0, y0=y0, w=max(x for x, y in cells)-x0+1,
                  h=max(y for x, y in cells)-y0+1)["cells"]
    return not after["body"]["dead"] and all(box["tile"][y-y0][x-x0] == -1 for x, y in cells)


NAVIGATION = {
    "recorded_exploration": episode("exploration", "replay",
        "Search the recorded terrain without enemies. Reveal the patch or find a life crystal.",
        native_search_covered,
        goal=dict(read("exploration", "starts")["replay"]["goal"], limit_ticks=10800)),
    "recorded_platform_climb": episode("platform_climb", "replay",
        "Climb the recorded terrain without enemies, reach the iron and mine its twelve tiles.",
        native_iron_cleared),
}
EPISODES = LAKE | VULTURE | QUARRY | NAVIGATION
