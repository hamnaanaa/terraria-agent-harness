"""Cave trips that depend on light placed by the live brain."""
import gym_api as G
from support import area, arrived, cell, count

TORCHES = ("Torch", 12, 2)
SWORD = "Iron Shortsword"
CAVE = ((27, 44, 97, 59, "%"), (27, 52, 96, 59, "B"))
CHAMBER = ((27, 23, 156, 59, "%"), (27, 53, 34, 59, "B"),
           (35, 33, 151, 59, "B"), (152, 49, 155, 59, "B"),
           (54, 33, 59, 39, "%"), (74, 33, 80, 43, "%"), (112, 33, 117, 39, "%"))


def torch_columns(cells):
    west, ceiling = cell(27, 33)
    east, floor = cell(155, 59)
    return sorted(cells["x0"] + x for y, row in enumerate(cells["tile"])
                  if ceiling <= cells["y0"] + y <= floor
                  for x, tile in enumerate(row) if tile == 4 and west <= cells["x0"] + x <= east)


def enter_cave(bounds, minimum=1):
    def verify(before, after):
        x0, y0 = cell(27, 33)
        columns = torch_columns(G.state(x0=x0, y0=y0, w=bounds[2] - x0 + 8, h=27)["cells"])
        return (arrived(after, bounds) and before["light"] >= 0.5 and not torch_columns(before["cells"])
                and count(before, "Torch") - count(after, "Torch") == len(columns) >= minimum
                and all(b - a >= 6 for a, b in zip(columns, columns[1:])))

    return {"kind": "reach", "area": bounds, "limit_ticks": 900}, verify


def fight_in_cave(before, after):
    def sword_hits(state):
        return next((row["hits"] for row in state["counters"]["by_weapon"] if row["name"] == SWORD), 0)

    return (after["counters"]["kills_by_name"].get("Blue Slime", 0)
            > before["counters"]["kills_by_name"].get("Blue Slime", 0)
            and sword_hits(after) > sword_hits(before) and torch_columns(after["cells"]) and after["light"] >= 0.25)


EPISODES = {
    "cave_entrance_light": {
        "text": "Walk out of daylight into a dark cave and place a real torch.",
        "terrain": CAVE, "bag": (TORCHES,),
        "goals": lambda: [enter_cave(area(53, 59, 56, 59))],
    },
    "cave_chamber_light": {
        "text": "Cross a wide, dark cave chamber. Space the torches out as the light fades.",
        "terrain": CHAMBER, "canvas": (160, 66, 60), "bag": (TORCHES,),
        "goals": lambda: [enter_cave(area(125, 59, 128, 59), minimum=2)],
    },
    "cave_fight_light": {
        "text": "Light the cave on the way in, then fight a slime with the light still on.",
        "terrain": CAVE, "bag": (TORCHES, (SWORD, 1, 1)),
        "dials": {"spawn": [["BlueSlime", *cell(64, 59)]]},
        "goals": lambda: [enter_cave(area(52, 59, 55, 59)),
                          ({"kind": "defeat", "enemy": "Blue Slime", "count": 1, "limit_ticks": 480}, fight_in_cave)],
    },
}
