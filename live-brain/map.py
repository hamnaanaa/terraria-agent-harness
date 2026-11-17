"""Terrain observed in the current world session."""
from rules import tiles


class Cells:
    """What the two share: the kinds table and the overlays."""

    def _kinds(self, kinds):
        self.solid_kind = {int(k): v["solid"] and not v["solid_top"] for k, v in kinds.items()}
        self.top_kind = {int(k): v["solid_top"] for k, v in kinds.items()}
        self.pick = {int(k): v.get("pick_damage", 0) for k, v in kinds.items()}
        self.no_attach = {int(k) for k, v in kinds.items() if v.get("no_attach")}
        self.cut = {int(k) for k, v in kinds.items() if v.get("cut")}          # cut by a block set into the cell
        self.names = {int(k): v["name"] for k, v in kinds.items()}
        self.hurt_kind = {int(k): tiles.kind(v["name"]) for k, v in kinds.items() if tiles.kind(v["name"])}
        # a platform the hands may set before one was ever seen: the game's own flags for tile 19 (solid, solid top)
        self.solid_kind.setdefault(19, False)
        self.top_kind.setdefault(19, True)
        self.names.setdefault(19, "Platforms")
        # A torch is non-solid even before one appears in the observed tile kinds.
        self.solid_kind.setdefault(4, False)

    def tile(self, x, y, put=(), dug=()):
        if (x, y) in put:
            return put[(x, y)] if isinstance(put, dict) else 0     # what a play set there: a block, or a platform (19)
        if (x, y) in dug:
            return -1
        return self.raw_tile(x, y)

    def solid(self, x, y, put=(), dug=()):
        t = self.tile(x, y, put, dug)
        return t == -2 or (t >= 0 and self.solid_kind.get(t, True))

    def floor(self, x, y, put=(), dug=()):
        t = self.tile(x, y, put, dug)
        return t == -2 or (t >= 0 and (self.solid_kind.get(t, True) or self.top_kind.get(t, False)))

    def water(self, x, y, put=(), dug=()):
        if (x, y) in put:
            return False
        return self.raw_liquid(x, y) == 0

    def lava(self, x, y, put=(), dug=()):
        return (x, y) not in put and self.raw_liquid(x, y) == 1

    def honey(self, x, y, put=(), dug=()):
        return (x, y) not in put and self.raw_liquid(x, y) == 2

    def hurt(self, x, y):
        """What a tile there does to a body touching it: 'spike', 'thorn', 'hot', or None (rules/tiles.py)."""
        t = self.raw_tile(x, y)
        return self.hurt_kind.get(t) if t >= 0 else None

    def door(self, x, y):
        """A closed door in the cell: walkers stop at it (rule «walker»)."""
        t = self.raw_tile(x, y)
        return t >= 0 and self.names.get(t) == "ClosedDoor"

    def rope(self, x, y):
        """Rule «rope»: a rope tile in the cell (the game's tile 213)."""
        return self.raw_tile(x, y) == 213

    def empty(self, x, y, put=(), dug=()):
        """A cell a block can go into: no tile, or one a block cuts (plants, vines, cobwebs: Player.cs takes a tileCut
        tile as an empty cell when placing)."""
        t = self.tile(x, y, put, dug)
        return t == -1 or t in self.cut

    def holds(self, x, y, put=(), dug=()):
        """Rule «place»: a tile a block can hold on to: a solid one (platforms too). The game's placement check gives
        cobwebs, coins and banners their own branch, «next to any tile», because a plain block needs a solid
        neighbour (Player.cs, PlaceThing_Tiles_BlockPlacementForAssortedThings; a tree trunk held nothing, Oct 20)."""
        t = self.tile(x, y, put, dug)
        return t >= 0 and (self.solid_kind.get(t, False) or self.top_kind.get(t, False))


class Map(Cells):
    def __init__(self):
        self.cells = {}
        self.kinds = {}
        self.version = 0
        self._kinds({})

    def update(self, window):
        kinds = window["kinds"]
        kinds_changed = any(self.kinds.get(k) != v for k, v in kinds.items())
        if kinds_changed:
            self.kinds.update(kinds)
            self._kinds(self.kinds)
        changed = {}
        seen = window["seen"]
        shape = window["shape"]
        permissions = window.get("can_break")
        for r, row in enumerate(window["tile"]):
            for c, tile in enumerate(row):
                if not seen[r][c] or tile == -2:
                    continue
                cell = (window["x0"] + c, window["y0"] + r)
                amount = window["liquid"][r][c]
                value = (tile, shape[r][c], amount, window["liquid_kind"][r][c] if amount else -1,
                         window["wall"][r][c], permissions[r][c] if permissions is not None else
                         kinds.get(str(tile), {}).get("can_break", True))  # Archived observations used type-level permission.
                before = self.cells.get(cell)
                if value != before:
                    changed[cell] = (before, value)
                    self.cells[cell] = value
        self.version += bool(changed or kinds_changed)
        return changed

    def raw_tile(self, x, y):
        value = self.cells.get((x, y))
        return value[0] if value else -2

    def shape(self, x, y):
        value = self.cells.get((x, y))
        return value[1] if value else 0

    def raw_liquid(self, x, y):
        value = self.cells.get((x, y))
        return value[3] if value else -1

    def wall(self, x, y):
        value = self.cells.get((x, y))
        return value[4] if value else 0

    def can_break(self, x, y):
        value = self.cells.get((x, y))
        return bool(value and len(value) > 5 and value[5])
