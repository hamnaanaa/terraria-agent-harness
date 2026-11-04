"""Terrain playthroughs on the current live brain."""
from support import loot_chest, open_chest, reach_near
from travel.fixtures import SCENES


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
            out.append((goal, verify))
        if scene.get("after", {}).get(index) == "chest":
            out.append(loot_chest())
    return out


EPISODES = {
    name: {"text": scene["note"], "feet": scene["feet"], "bag": scene["bag"],
           "wear": scene.get("wear", ()), "canvas": scene.get("canvas"),
           "terrain": scene.get("map", ()), "reveal": scene.get("reveal", True),
           "goals": lambda name=name, scene=scene: goals(name, scene)}
    for name, scene in SCENES.items()
}
