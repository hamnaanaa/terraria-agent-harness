"""Regular combat episodes."""
from support import ARROWS, BOW, cell

def fight():
    return [({"kind": "defeat", "enemy": "Blue Slime", "count": 1, "limit_ticks": 480,
              "label": "defeat a Blue Slime"},
             lambda a, b: b["counters"]["kills_by_name"].get("Blue Slime", 0)
             > a["counters"]["kills_by_name"].get("Blue Slime", 0))]


EPISODES = {
    "fight": {
        "text": "Defeat a Blue Slime with a bow and confirm the kill.",
        "bag": (BOW, ARROWS),
        "dials": {"spawn": [["BlueSlime", *cell(34, 59)]]},
        "goals": fight,
    },
}
