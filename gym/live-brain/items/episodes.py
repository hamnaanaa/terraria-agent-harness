"""Regular items episodes."""
from support import PICK, area, cell, count, reach, tile

def ore():
    return [
        reach(area(26, 59, 29, 59)),
        ({"kind": "change", "cells": [(*cell(33, 58), -1), (*cell(33, 59), -1)], "limit_ticks": 420,
          "label": "mine two iron tiles"},
         lambda a, b: all(tile(b, cell(33, y)) == -1 for y in (58, 59))),
        ({"kind": "quantity", "item": "Iron Ore", "count": 2, "gain": False, "limit_ticks": 360,
          "label": "have two Iron Ore"},
         lambda a, b: count(b, "Iron Ore") >= 2),
    ]


EPISODES = {
    "ore": {
        "text": "Reach the vein, mine two iron tiles and verify the ore in the bag.",
        "terrain": [(33, 58, 33, 59, "I")],
        "bag": (PICK,),
        "goals": ore,
    },
    "place": {
        "text": "Place one dirt block in the marked cell.",
        "bag": (("Dirt Block", 10, 12),),
        "goals": lambda: [({"kind": "change", "cells": [(*cell(24, 59), 0)], "limit_ticks": 240},
                           lambda a, b: tile(b, cell(24, 59)) == 0)],
    },
    "pickup": {
        "text": "Walk to a dropped stack of ore and pick it up.",
        "dials": {"drop": [["Iron Ore", 3, 12, 0]]},
        "goals": lambda: [({"kind": "quantity", "item": "Iron Ore", "count": 3, "limit_ticks": 360},
                           lambda a, b: count(b, "Iron Ore") - count(a, "Iron Ore") >= 3)],
    },
    "craft": {
        "text": "Craft a wooden chair at the nearby work bench.",
        "bag": (("Wood", 20, 0),),
        "terrain": [(25, 59, 25, 59, "W")],
        "goals": lambda: [({"kind": "interact", "operation": "craft", "item": "Wooden Chair",
                            "cell": cell(25, 59), "limit_ticks": 240},
                           lambda a, b: count(b, "Wooden Chair") > count(a, "Wooden Chair"))],
    },
}
