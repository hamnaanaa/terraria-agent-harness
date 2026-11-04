"""The tiles that hurt, as the gym measured them on Oct 18
and the wiki states them (rules/rules.py, family «Tiles that hurt»), and what the gear makes safe (the mod's
physics flags: lava_immune, lava_time, fire_walk).

    LAVA        80 a touch, again every 40 ticks while in it, and On Fire!: 1 every 15 ticks for 7 s
    SPIKES      60 a touch (a wooden spike 80), again every 40 ticks while touching
    HOT         hellstone and meteorite: Burning while touching, 5 every 10 ticks, half speed
    THORNS      17 (jungle) or 10 (corruption, crimson) a touch while moving through; the touched thorns break
"""

SPIKES = {"Spikes": 60, "WoodenSpikes": 80}
THORNS = {"JungleThorns": 17, "CorruptThorns": 10, "CrimsonThorns": 10}
HOT = {"Hellstone", "Meteorite"}
LAVA_HIT, HURT_EVERY = 80, 40          # the hit and the ticks of immunity after it (lava and spikes alike)
FIRE_EVERY, FIRE_TICKS = 15, 7 * 60    # On Fire!: 1 health every 15 ticks, 7 s on Classic
HOT_EVERY, HOT_HIT = 10, 5             # Burning: 5 every 10 ticks while touching (30 a second)

# the thing a tile kind is, by the game's item names (agreed Oct 31: what the slow brain looks for in view), for the
# kinds the mod sends no drops for: the mod's cells.kinds name (the game's TileID name) to the item. The scenes in the
# logs show Tin, Lead, Tungsten, Gold, Cobweb, Heart, Containers, Containers2 and Pots under these names; Copper, Iron,
# Silver, Platinum and DemonAltar are TileID's own. The ore, cobweb, life crystal and chest names are the game's English
# item names (its text inside tModLoader.dll, read Oct 31); an altar breaks into no item and a pot into its loot, so
# those two words name the thing itself
ITEM_OF_TILE = {
    # https://terraria.wiki.gg/wiki/Ores
    "Copper": "Copper Ore", "Tin": "Tin Ore", "Iron": "Iron Ore", "Lead": "Lead Ore",
    "Silver": "Silver Ore", "Tungsten": "Tungsten Ore", "Gold": "Gold Ore", "Platinum": "Platinum Ore",
    "Cobweb": "Cobweb",                                          # https://terraria.wiki.gg/wiki/Cobweb
    "Heart": "Life Crystal",                                     # https://terraria.wiki.gg/wiki/Life_Crystal
    "DemonAltar": "Demon Altar",                                 # https://terraria.wiki.gg/wiki/Altars
    "Containers": "Chest", "Containers2": "Chest",               # https://terraria.wiki.gg/wiki/Chests
    "Pots": "Pot",                                               # https://terraria.wiki.gg/wiki/Pots
}


def kind(name):
    """What a tile does to a body touching it: 'spike', 'thorn', 'hot', or None."""
    if name in SPIKES:
        return "spike"
    if name in THORNS:
        return "thorn"
    if name in HOT:
        return "hot"
    return None


def lava_safe(phys):
    """Rule «lava»: Obsidian Skin, Lava Waders and the like make lava harmless; a Lava Charm for lava_time ticks."""
    return bool(phys.get("lava_immune")) or phys.get("lava_time", 0) > 0


def hot_safe(phys):
    """Rule «hot_blocks»: the Obsidian Skull (fire_walk) ends the burn from hellstone and meteorite."""
    return bool(phys.get("fire_walk"))
