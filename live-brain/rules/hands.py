"""The hands' rules: what the held item can do to a cell, and how long it takes. The numbers are the item's own fields
read live from the game (AgentIO's /state "bag": use_time, pick, tile_boost) and the player's reach fields ("physics":
range_x, range_y, block_range). Each part names its gym test.

A body here is its top left corner and size in px: x, y, w, h.
"""
import math

CELL = 16


# rule «hook» (gym, Oct 20, Grappling Hook, the grapple key): the hook flies from the body's centre at 11 px a tick
# toward the aim and latches on the first solid tile it meets within its reach, 350 px (latched at 342, fell short
# at 374); the body is then pulled along the line at 11 px a tick and rests against the wall with its centre at the
# hook's height, no gravity; a jump during the pull lets go with no lift (the pull's speed carries on), a jump
# from rest lets go with half a jump (8 ticks of push, 61 px up). A rope item places where a solid tile or a rope
# is beside the cell (gym, Oct 20).
HOOK = {"speed": 11.0, "pull": 11.0, "reach": 350.0, "jump_rise": 61.0}   # gym, Oct 20: the pull at 10.8 to 11.0 px a
                                                                          # tick; a jump from rest rises 61 px


def in_reach(body, cell, phys, tile_boost=0):
    """Rule «reach»: the cell lies within range_x columns of the body's sides and range_y rows of its top and bottom,
    plus the item's tile_boost and the body's block_range (the game's own test, Player.cs). Gym test: place_reach."""
    rx = phys["range_x"] + tile_boost + phys.get("block_range", 0)
    ry = phys["range_y"] + tile_boost + phys.get("block_range", 0)
    x, y = cell
    # the game's own bounds (Player.cs, ItemCheck_UseMiningTools): the right edge takes one off, the bottom edge two:
    # a cell three rows under the feet is out of reach (a stone dig refused 12 times over, Classic, Oct 23)
    return (body["x"] / CELL - rx <= x <= (body["x"] + body["w"]) / CELL + rx - 1 and
            body["y"] / CELL - ry <= y <= (body["y"] + body["h"]) / CELL + ry - 2)


def overlaps(body, cell):
    x, y = cell
    return (body["x"] < (x + 1) * CELL and body["x"] + body["w"] > x * CELL and
            body["y"] < (y + 1) * CELL and body["y"] + body["h"] > y * CELL)


def has_support(cell, wall, holds):
    x, y = cell
    return wall(x, y) or any(holds(a, b) for a, b in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))


def can_place(cell, body, phys, item, tile, wall, holds=None):
    """Rule «place»: the cell holds no tile (a liquid is fine), a tile that can hold a block touches one of its four
    sides (any tile but a torch, a plant, a cobweb: the game's no-attach ones) or a back wall stands behind it, no
    body overlaps it, and it is in reach. tile(x, y) -> the tile type or -1, wall(x, y) -> the wall type or 0,
    holds(x, y) -> whether that tile holds a block (tile >= 0 when not given). Returns None when it can, else why
    not. Gym test: place_rules."""
    return next(placement_reasons(cell, body, phys, item, tile, wall, holds), None)


def placement_reasons(cell, body, phys, item, tile, wall, holds=None):
    """The same placement constraints, including all current reasons when explaining a failure."""
    x, y = cell
    holds = holds or (lambda a, b: tile(a, b) >= 0)
    if tile(x, y) >= 0:
        yield "the cell holds a tile"
    if not in_reach(body, cell, phys, item.get("tile_boost", 0)):
        yield "out of reach"
    if overlaps(body, cell):
        yield "the body stands in it"
    if not has_support(cell, wall, holds):
        yield "nothing holds it: no tile beside it that holds a block, no wall behind it"


def use_ticks(item, speed=1.0):
    """Rule «use»: with the use key held, the item works on the first tick and then every use_time ticks times the
    gear's factor, rounded down: the body's pick_speed for a pickaxe (a Mining Potion makes it 0.75: copper's 15
    becomes 11), its tile_speed for a block. Gym tests: place_time, dig_time, dig_gear."""
    return int(item["use_time"] * speed)


def axe_damage(item):
    """Rule «chop»: what one swing of an axe does to a tree's cell, the cell breaking at 100 (the base takes the whole
    tree down and drops its wood). Measured in Classic, Oct 23: the copper axe (axe 7, the game's number, 35 percent)
    fells a trunk in about 6 swings of 21 ticks, so a swing is taken as axe times 2.5 (17.5: 6 swings)."""
    return item.get("axe", 0) * 2.5


def dig_hits(pick_damage):
    """Rule «mine»: each hit adds the pickaxe's damage on that tile to the cell; it breaks at 100. The damage per hit
    is the game's own (Player.GetPickaxeDamage, read live: /state cells.kinds[type].pick_damage): a copper pickaxe does
    70 to dirt and 35 to stone, an iron one 80 and 40; 0 means this pickaxe cannot. Gym tests: dig_time, dig_gear."""
    return math.ceil(100 / pick_damage) if pick_damage > 0 else None


def dig_ticks(pick_damage, item, pick_speed=1.0):
    """Ticks from the first hit to the cell gone: the hits, use_ticks apart."""
    hits = dig_hits(pick_damage)
    return None if hits is None else (hits - 1) * use_ticks(item, pick_speed)
