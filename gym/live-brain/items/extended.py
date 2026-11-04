"""Placement, crafting and inventory playthroughs."""
from support import PICK, cell, count, loot_chest, open_chest, tile

NAMED = ("Gravestone", "Grave Marker", "Tombstone")
BAG = [(name, n, slot) for slot, (name, n) in enumerate(
    [(name, n) for name in NAMED for n in (3, 5)] +
    [("Copper Pickaxe", 1), ("Dirt Block", 99), ("Torch", 30), ("Wood", 40), ("Cross Grave Marker", 4), ("Gel", 5)]
)]
BAG[3] = (*BAG[3], True)
CHEST = [(45, 59, 45, 59, "C")]


def change(cells):
    return ({"kind": "change", "cells": [(*cell(x, y), t) for x, y, t in cells], "limit_ticks": 1200},
            lambda a, b: all(tile(b, cell(x, y)) == t for x, y, t in cells))


def moved_named(before, after):
    expected = {name: 5 if name == "Grave Marker" else 0 for name in NAMED}
    return (all(count(after, name) == n for name, n in expected.items()) and
            all(count(after, name) == count(before, name) for name in
                ("Copper Pickaxe", "Dirt Block", "Torch", "Wood", "Cross Grave Marker")))


HOUSE = ([(x, y, 0) for x in (40, 47) for y in range(56, 60)] +
         [(x, 55, 0) for x in range(40, 48)])
EPISODES = {
    "place_door": {"text": "Fit a wooden door into a three-tile opening.", "feet": (30, 59),
                   "bag": [("Wooden Door", 1, 0)], "terrain": [(40, 54, 40, 56, "#")],
                   "goals": lambda: [change([(40, y, 10) for y in range(57, 60)])]},
    "build_house": {"text": "Build the walls and roof of a dirt room from the blueprint.", "feet": (34, 59),
                    "bag": [("Dirt Block", 99, 1), PICK], "goals": lambda: [change(HOUSE)]},
    "open_chest": {"text": "Walk to the chest, open it, and verify the loot in the bag.", "feet": (30, 59),
                   "terrain": CHEST, "goals": lambda: [open_chest((45, 58)), loot_chest()]},
    "craft_chair": {"text": "Walk to the work bench and craft a wooden chair.", "feet": (30, 59),
                    "bag": [("Wood", 4, 0)], "terrain": [(45, 59, 45, 59, "W")],
                    "goals": lambda: [({"kind": "interact", "operation": "craft", "cell": cell(45, 59),
                                       "item": "Wooden Chair", "limit_ticks": 1200},
                                      lambda a, b: count(b, "Wooden Chair") > count(a, "Wooden Chair"))]},
    "dig_shaft": {"text": "Clear the eight requested cells directly below the starting position.",
                  "feet": (30, 59), "bag": [PICK],
                  "goals": lambda: [change([(x, y, -1) for x in (30, 31) for y in range(60, 64)])]},
    "trash_named": {"text": "Trash the named items and preserve the favorite and similarly named decoys.",
                    "feet": (30, 59), "bag": BAG, "pretrash": "Gel",
                    "goals": lambda: [("/trash", {"items": NAMED}, moved_named)]},
    "store_named": {"text": "Reach the near chest and store only the named non-favorite items.",
                    "feet": (30, 59), "bag": BAG, "terrain": CHEST + [(90, 59, 90, 59, "C")],
                    "goals": lambda: [open_chest((45, 58)), ("/chest", {"put": NAMED}, moved_named)]},
}
