"""Regular survival episodes."""
from support import ARROWS, BOW, area, arrived, cell, reach

EPISODES = {
    "maintain": {
        "text": "Stay inside the marked area for two game seconds.",
        "goals": lambda: [({"kind": "maintain", "area": area(19, 59, 23, 59), "ticks": 120, "limit_ticks": 180},
                           lambda a, b: b["tick"] - a["tick"] >= 120 and arrived(b, area(19, 59, 23, 59)))],
    },
    "mixed": {
        "text": "Reach the ledge, then stay alive while waiting for the next goal.",
        "wait_ticks": 600,
        "terrain": [(30, 57, 48, 59, "#")],
        "bag": (BOW, ARROWS),
        "wear": ("Iron Helmet",),
        "dials": {"spawn": [["BlueSlime", *cell(32, 56)]]},
        "goals": lambda: [reach(area(38, 56, 41, 56))],
    },
}
