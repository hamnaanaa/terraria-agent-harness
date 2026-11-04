"""Combat playthroughs on the current live brain."""
from collections import Counter

from combat.fixtures import BAG, LIFE, SCENES, WEAR
from support import cell, count, reach_near

NAMES = {"BlueSlime": "Blue Slime", "DemonEye": "Demon Eye", "CaveBat": "Cave Bat"}


def goals(name, scene):
    if "goal" in scene:
        return [reach_near(scene["goal"])]
    targets = Counter(NAMES.get(kind, kind) for kind, _, _ in scene["foes"])
    if name == "slime_sealed":
        targets.pop("Blue Slime")
    return [
        ({"kind": "defeat", "enemy": enemy, "count": n, "limit_ticks": 1200},
         lambda a, b, enemy=enemy, n=n: (
             b["counters"]["kills_by_name"].get(enemy, 0) - a["counters"]["kills_by_name"].get(enemy, 0) >= n))
        for enemy, n in targets.items()
    ]


def episode(name, scene):
    x, y = scene["feet"]
    return {"text": scene["note"], "feet": (x, y), "bag": scene.get("bag", BAG), "wear": WEAR, "life_max": LIFE,
            "terrain": scene.get("map", ()), "torches": not scene.get("dark", False),
            "dials": {"freeze": "midnight", "spawn": [[kind, *cell(x + dx, y + dy)] for kind, dx, dy in scene["foes"]]},
            "goals": lambda: goals(name, scene)}


EPISODES = {name: episode(name, scene) for name, scene in SCENES.items()}


def weapon_use(state, name, counter):
    return next((row[counter] for row in state["counters"]["by_weapon"] if row["name"] == name), 0)


def weapon_choice(name, weak_weapon):
    strong_weapon = "Platinum Bow"
    scene = {
        "feet": (48, 59), "foes": [("Zombie", 14, 0)],
        "bag": [(weak_weapon, 1, 0), (strong_weapon, 1, 17), ("Wooden Arrow", 100, 54)],
        "note": f"Defeat a zombie. Prefer the Platinum Bow in the backpack over the {weak_weapon} on the hotbar.",
    }
    case = episode(name, scene)
    goal, killed_zombie = case["goals"]()[0]

    def verify(before, after):
        strong_attacks = weapon_use(after, strong_weapon, "attacks") - weapon_use(before, strong_weapon, "attacks")
        weak_attacks = weapon_use(after, weak_weapon, "attacks") - weapon_use(before, weak_weapon, "attacks")
        return (
            killed_zombie(before, after)
            and any(item["name"] == strong_weapon and item["slot"] == 17 for item in before["bag"])
            and any(item["name"] == strong_weapon and item["slot"] < 10 for item in after["bag"])
            and strong_attacks > weak_attacks
            and weapon_use(after, strong_weapon, "hits") > weapon_use(before, strong_weapon, "hits")
        )

    return dict(case, goals=lambda: [(goal, verify)])


def ammo_fallback():
    bow, sword, ammo = "Platinum Bow", "Iron Shortsword", "Wooden Arrow"
    case = episode("ammo_fallback", {
        "feet": (48, 59), "foes": [("Zombie", 14, 0)],
        "bag": [(bow, 1, 0), (sword, 1, 17), (ammo, 2, 54)],
        "note": "Spend the last two arrows, then draw the shortsword from the backpack and defeat the zombie.",
    })
    goal, killed_zombie = case["goals"]()[0]

    def verify(before, after):
        return (
            killed_zombie(before, after)
            and count(before, ammo) == 2 and count(after, ammo) == 0
            and weapon_use(after, bow, "attacks") - weapon_use(before, bow, "attacks") == 2
            and any(item["name"] == sword and item["slot"] == 17 for item in before["bag"])
            and any(item["name"] == sword and item["slot"] < 10 for item in after["bag"])
            and weapon_use(after, sword, "hits") > weapon_use(before, sword, "hits")
        )

    return dict(case, goals=lambda: [(goal, verify)])


WEAPON_EPISODES = {
    "upgrade_bow": weapon_choice("upgrade_bow", "Wooden Bow"),
    "bow_over_shortsword": weapon_choice("bow_over_shortsword", "Iron Shortsword"),
    "ammo_fallback": ammo_fallback(),
}
