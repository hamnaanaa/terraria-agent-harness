"""Regular search episodes."""
from support import area, cell, tile

EPISODES = {
    "search": {
        "text": "Find the altar inside the marked patch.",
        "terrain": [(80, 59, 80, 59, "X")],
        "reveal": False,
        "goals": lambda: [({"kind": "search", "area": area(77, 57, 83, 60), "tile": "DemonAltar",
                            "limit_ticks": 1500},
                           lambda a, b: a["tick"] < b["tick"] and any(b["cells"]["kinds"].get(str(tile(b, cell(x, y))), {}).get("name")
                                            == "DemonAltar" and
                                            b["cells"]["seen"][cell(x, y)[1] - b["cells"]["y0"]][cell(x, y)[0] - b["cells"]["x0"]]
                                            for x in range(77, 84) for y in range(57, 61)))],
    },
}
