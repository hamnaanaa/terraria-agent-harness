"""Retained enemy scenes."""
BAG = [["Wooden Bow", 1, 0], ["Iron Shortsword", 1, 1], ["Dirt Block", 99, 2], ["Lesser Healing Potion", 2, 3],
       ["Torch", 50, 4], ["Wooden Arrow", 300, 54]]
# the sword scenes: a broadsword and nothing to shoot with, so the swing rule alone decides (rule «swing», Oct 20)
BAG_SWORD = [["Tungsten Broadsword", 1, 0], ["Dirt Block", 99, 2], ["Lesser Healing Potion", 2, 3], ["Torch", 50, 4]]
WEAR = ["Tin Helmet", "Tin Chainmail", "Tin Greaves"]
LIFE = 120

SCENES = {
    "zombie_open": {"feet": (48, 59), "foes": [("Zombie", 14, 0)], "note": "one zombie, open ground"},
    "zombie_sword": {"feet": (48, 59), "foes": [("Zombie", 14, 0)], "bag": BAG_SWORD, "note": "one zombie, a broadsword only: the engine's swing"},
    "zombies_sword": {"feet": (48, 59), "foes": [("Zombie", -12, 0), ("Zombie", 12, 0)], "bag": BAG_SWORD, "note": "a zombie each side, a broadsword only"},
    "zombie_cornered": {"feet": (3, 59), "foes": [("Zombie", 12, 0)], "note": "one zombie, the body's back to the wall"},
    "zombies_both": {"feet": (48, 59), "foes": [("Zombie", -12, 0), ("Zombie", 12, 0)], "note": "a zombie on each side"},
    "zombie_eye": {"feet": (48, 59), "foes": [("Zombie", 12, 0), ("DemonEye", 10, -8)], "note": "a zombie and a demon eye"},
    # other kinds, through the movement families the game's AI style gives (none of these was measured by itself)
    "skeleton": {"feet": (48, 59), "foes": [("Skeleton", 14, 0)], "note": "a skeleton: a walker with 90 health and 8 defense"},
    "bats": {"feet": (48, 59), "foes": [("CaveBat", 12, -6), ("CaveBat", -10, -5)], "note": "two cave bats: erratic fliers, run as fliers"},
    "zombies_3": {"feet": (48, 59), "foes": [("Zombie", -12, 0), ("Zombie", 12, 0), ("Zombie", 16, 0)], "note": "three zombies"},
    "eyes_2": {"feet": (48, 59), "foes": [("DemonEye", 12, -8), ("DemonEye", -14, -6)], "note": "two demon eyes"},
    # terrain
    "zombie_wall": {"feet": (48, 59), "foes": [("Zombie", 14, 0)], "map": [("fill", 55, 57, 55, 59, "#")],
                    "note": "a zombie behind a wall of 3 (it jumps walls up to 6)"},
    "zombie_ledge": {"feet": (48, 55), "foes": [("Zombie", 14, 4), ("Zombie", -14, 4)],
                     "map": [("fill", 44, 56, 52, 59, "#")], "note": "the body on a ledge of 4, zombies below on both sides"},
    "zombie_pit": {"feet": (48, 59), "foes": [("Zombie", 14, 0)], "map": [("fill", 53, 60, 56, 62, ".")],
                   "note": "a pit 4 wide and 3 deep between the body and a zombie"},
    "slime_dark": {"feet": (48, 59), "foes": [("BlueSlime", 12, 0)], "dark": True, "note": "a slime in the dark: the light rule"},
    # the reach test (Nov 1): the slime cannot get out and the body cannot get in, so the fight is the zombie's only
    "slime_sealed": {"feet": (48, 59), "foes": [("BlueSlime", -11, -3), ("Zombie", 14, 0)],
                     "map": [("fill", 32, 52, 42, 59, "#"), ("fill", 34, 54, 40, 57, ".")],     # 7 wide: the jitter stays in
                     "note": "a slime sealed in a box of blocks, a zombie on open ground: cleared when the zombie dies"},
    # one loop: a place to reach, enemies on the way (the move set is the same; the goal is one more fact on each line)
    "walk_zombie": {"feet": (8, 59), "foes": [("Zombie", 35, 0)], "goal": (85, 59), "note": "A to B with a zombie in the way"},
    "walk_zombie_eye": {"feet": (8, 59), "foes": [("Zombie", 35, 0), ("DemonEye", 50, -8)], "goal": (85, 59),
                        "note": "A to B with a zombie and a demon eye on the way"},
}


LOW_HEALTH = {
    "text": "Survive three zombies starting at 40 health, with potions available.",
    "feet": (48, 59),
    "bag": (("Copper Pickaxe", 1, 0), ("Wooden Bow", 1, 1), ("Iron Shortsword", 1, 2),
            ("Lesser Healing Potion", 3, 3), ("Dirt Block", 99, 4), ("Wooden Arrow", 300, 54)),
    "wear": ("Tin Helmet", "Tin Chainmail", "Tin Greaves"),
    "life_max": 120,
    "dials": {"life": 40, "freeze": "midnight"},
    "enemy_offsets": (("Zombie", 12, 0), ("Zombie", 16, 0), ("Zombie", -14, 0)),
}
