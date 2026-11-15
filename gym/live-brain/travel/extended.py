"""Terrain playthroughs on the current live brain."""
from gym_api import state
from support import cell, loot_chest, open_chest, reach_near, tile
from travel.fixtures import SCENES


def opened_pocket(cap):
    first, last, row = cap
    x, y = cell(first, row)
    after = state(x0=x, y0=y, w=last - first + 1, h=1)
    return any(tile(after, (column, y)) == -1 for column in range(x, x + last - first + 1))


def goals(name, scene):
    out = []
    for index, (kind, at) in enumerate(scene["goals"]):
        if kind == "interact":
            out.append(open_chest(at))
        else:
            goal, verify = reach_near(at, {"cliff_deadly_pick": 7200, "cliff_deadly_blocks": 7200, "flooded_up": 3600, "air_pocket": 5400}.get(name, 1800))
            if scene.get("expect") == "no_way":
                goal.update(limit_ticks=300, expected="timeout")
                verify = lambda a, b: (not b["body"]["dead"] and b["deaths"]["count"] == a["deaths"]["count"]
                                       and b["counters"]["damage_taken"] == a["counters"]["damage_taken"])
            elif scene.get("safe"):
                arrived = verify
                verify = lambda a, b, arrived=arrived: (arrived(a, b)
                    and b["deaths"]["count"] == a["deaths"]["count"]
                    and b["counters"]["damage_taken"] == a["counters"]["damage_taken"])
            if "pocket_cap" in scene:
                survived = verify
                verify = lambda a, b, survived=survived: survived(a, b) and opened_pocket(scene["pocket_cap"])
            out.append((goal, verify))
        if scene.get("after", {}).get(index) == "chest":
            out.append(loot_chest())
    return out


EPISODES = {
    name: {"text": scene["note"], "feet": scene["feet"], "bag": scene["bag"],
           "wear": scene.get("wear", ()), "canvas": scene.get("canvas"),
           "terrain": scene.get("map", ()), "reveal": scene.get("reveal", True), "dials": scene.get("dials", {}),
           "goals": lambda name=name, scene=scene: goals(name, scene)}
    for name, scene in SCENES.items()
}
