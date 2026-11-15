"""Combat data cases. Enemy names are Terraria NPC IDs and offsets are in tiles."""

EARLY = ("Gold Helmet", "Gold Chainmail", "Gold Greaves")
LATE = ("Adamantite Helmet", "Adamantite Breastplate", "Adamantite Leggings")

# (NPC ID, horizontal offset, vertical offset from the arena floor, terrain)
PRE_HARDMODE = (
    ("BlueSlime", 10, 0, "flat"),
    ("Zombie", 12, 0, "flat"),
    ("DemonEye", 12, -7, "flat"),
    ("CaveBat", 10, -5, "flat"),
    ("Vulture", 6, 0, "flat"),
    ("Antlion", 10, 0, "ledge"),
    ("WalkingAntlion", 12, 0, "flat"),
    ("Hornet", 14, -6, "flat"),
    ("FireImp", 13, 0, "flat"),
    ("GiantWormHead", 13, 4, "deep"),
    ("Piranha", 8, 0, "pool"),
)
HARDMODE = (
    ("ArmoredSkeleton", 12, 0, "flat"),
    ("SkeletonArcher", 15, 0, "flat"),
    ("Wraith", 12, -4, "wall"),
    ("Unicorn", 16, 0, "flat"),
    ("Pixie", 12, -6, "flat"),
    ("WanderingEye", 12, -6, "flat"),
    ("IchorSticker", 14, -5, "flat"),
    ("GiantTortoise", 13, 0, "flat"),
    ("Mimic", 10, 0, "flat"),
)
BOSSES = (
    ("EyeofCthulhu", 19, -13, "boss"),
    ("SkeletronHead", 17, -11, "boss"),
    ("Retinazer", 19, -15, "boss"),
    ("Spazmatism", -19, -12, "boss"),
    ("QueenSlimeBoss", 16, 0, "boss"),
)

WEAPONS = {
    "shortsword": (("Copper Shortsword", 1, 0),),
    "sword": (("Gold Broadsword", 1, 0),),
    "bow": (("Gold Bow", 1, 0), ("Wooden Arrow", 200, 54)),
    "fire_bow": (("Molten Fury", 1, 0), ("Flaming Arrow", 200, 54)),
    "gun": (("The Undertaker", 1, 0), ("Musket Ball", 200, 54)),
    "boomerang": (("Wooden Boomerang", 1, 0),),
    "grenade": (("Grenade", 25, 0),),
    "magic": (("Amethyst Staff", 1, 0),),
    "whip": (("Leather Whip", 1, 0),),
    "summon": (("Finch Staff", 1, 0), ("Leather Whip", 1, 1)),
    "hard_sword": (("Adamantite Sword", 1, 0),),
    "hard_gun": (("Megashark", 1, 0), ("Crystal Bullet", 200, 54)),
    "hard_magic": (("Crystal Storm", 1, 0),),
    "hard_summon": (("Spider Staff", 1, 0), ("Firecracker", 1, 1)),
}

MIXED = (
    ("zombie-eye", (("Zombie", 12, 0, "flat"), ("DemonEye", -12, -8, "flat")), "early"),
    ("archer-tortoise", (("SkeletonArcher", 19, 0, "wall"), ("GiantTortoise", -13, 0, "wall")), "late"),
    ("wraith-pixie", (("Wraith", -13, -5, "wall"), ("Pixie", 12, -8, "wall")), "late"),
)


def cases():
    for tier, roster in (("early", PRE_HARDMODE), ("late", HARDMODE), ("boss", BOSSES)):
        for foe in roster:
            for motion in ("stand", "move"):
                yield dict(name=f"{foe[0]}-{motion}", phase="movement", tier=tier, foes=(foe,),
                           weapon=None, action=motion, repeat=1)
    for weapon in WEAPONS:
        tier = "late" if weapon.startswith("hard_") else "early"
        foe = "ArmoredSkeleton" if tier == "late" else "FaceMonster"
        # Short melee probes start close. The ranged probes leave room for flight.
        distance = 2 if weapon in {"shortsword", "sword", "whip", "hard_sword"} else 8
        for repeat in (1, 2):
            yield dict(name=f"{weapon}-{repeat}", phase="weapon", tier=tier,
                       foes=((foe, distance, 0, "flat"),), weapon=weapon, action="attack", repeat=repeat)
    for foe, weapon, health, size in ((BOSSES[0], "fire_bow", 800, (100, 110)),
                                      (BOSSES[-1], "hard_gun", 6000, (114, 100))):
        for phase in ("opening", "low-health"):
            yield dict(name=f"{foe[0]}-boss-attack-{phase}", phase="weapon", tier="boss", foes=(foe,),
                       weapon=weapon, action="attack", repeat=1,
                       spawn_health=(health, *size) if phase == "low-health" else None)
    for index, roster in enumerate((PRE_HARDMODE, HARDMODE)):
        tier = ("early", "late")[index]
        names = ({"Zombie", "Vulture", "Antlion", "Hornet"} if tier == "early"
                 else {"SkeletonArcher", "GiantTortoise", "Wraith", "Unicorn"})
        for foe in roster:
            if foe[0] not in names:
                continue
            for weapon in (("sword", "bow") if tier == "early" else ("hard_sword", "hard_gun")):
                for repeat in (1, 2, 3):
                    yield dict(name=f"{foe[0]}-{weapon}-{repeat}", phase="live", tier=tier, foes=(foe,),
                               weapon=weapon, action="defeat", repeat=repeat)
    for name, foes, tier in MIXED:
        for action in ("guard", "cross"):
            for repeat in (1, 2, 3):
                yield dict(name=f"{name}-{action}-{repeat}", phase="live", tier=tier, foes=foes,
                           weapon="bow" if tier == "early" else "hard_gun", action=action, repeat=repeat)
        yield dict(name=f"{name}-cross-control", phase="live", tier=tier, foes=(), terrain=foes[0][3],
                   weapon="bow" if tier == "early" else "hard_gun", action="cross", repeat=1)
        if name == "archer-tortoise":
            for repeat in (1, 2, 3):
                yield dict(name=f"{name}-matched-defeat-{repeat}", phase="live", tier=tier, foes=foes,
                           weapon="hard_gun", action="defeat", layout="cross", repeat=repeat)
    for foe in (BOSSES[0], BOSSES[-1]):
        yield dict(name=f"{foe[0]}-guard", phase="live", tier="boss", foes=(foe,),
                   weapon="fire_bow" if foe == BOSSES[0] else "hard_gun", action="guard", repeat=1)
    yield dict(name="twins-guard", phase="live", tier="boss", foes=BOSSES[2:4],
               weapon="hard_gun", action="guard", repeat=1)
