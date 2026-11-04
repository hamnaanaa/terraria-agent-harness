"""Regular travel episodes."""
from support import area, arrived, count, reach

EPISODES = {
    "walk": {
        "text": "Walk east and stop inside the marked area.",
        "goals": lambda: [reach(area(35, 59, 37, 59))],
    },
    "ledge": {
        "text": "Reach the top of a ledge by jumping.",
        "terrain": [(30, 57, 48, 59, "#")],
        "goals": lambda: [reach(area(34, 56, 37, 56))],
    },
    "cave": {
        "text": "Go west to the opening, then head east under the shelf.",
        "feet": (21, 43),
        "terrain": [(17, 44, 98, 49, "#")],
        "goals": lambda: [reach(area(34, 59, 37, 59))],
    },
    "bridge": {
        "text": "Cross a ten-cell gap using dirt or wood from the bag.",
        "feet": (21, 49),
        "terrain": [(2, 50, 97, 59, "#"), (29, 50, 38, 65, ".")],
        "bag": (("Wood", 20, 1), ("Dirt Block", 30, 6)),
        "goals": lambda: [(dict(reach(area(42, 49, 45, 49))[0], limit_ticks=1200, preserve={"Wood": 1}),
                           lambda a, b: arrived(b, area(42, 49, 45, 49)) and count(b, "Wood") == 20
                           and count(b, "Dirt Block") < 30)],
    },
}
