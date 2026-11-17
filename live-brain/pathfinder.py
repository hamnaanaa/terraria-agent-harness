"""Route search and estimates for travel and terrain work."""
import heapq
import math
import time

from rules import body as B  # noqa: E402
from rules import hands as Hd  # noqa: E402
from rules import tiles as Tl  # noqa: E402

CELL, BW, BH = 16, B.W, B.H
MAX_BODY_ROWS = math.ceil(BH / CELL) + 1
REACH = 7             # cells each way the hands' work is gathered over: the game's reach (5 columns, 4 rows from
                      # the body's sides) plus the body's own width and the first pickaxes' boost
HOLDS = (16, 8, 3)
WING_HOLDS = B.WINGS["holds"]  # the jump key held through the wings' push and the glide (rule «wings»), one run-up only
OPEN_TICKS = 10               # a door's right-click and swing before the walk on (rule «door»)
ROPE_MOST = 30                # ropes placed in one move, down from an edge or up a shaft
HOOK_ROWS = 2
HOOK_PX = 18                  # the hook projectile's box, from the game (/state shots while one flies)
                              # from rest the jump rises 61 px, the feet 29 px under the hook's row (gym, Oct 20)            # jump key ticks tried: the full press and hold, half of it, a tap (rule «jump»)
RUNS = (0, 20, 40)            # ticks run from a stand before the press: the fast brain's own jump timings
MAX_FALL = 60                 # rows a fall is followed (into water; a dry fall stops at the hurting rows)
MARGIN_X, MARGIN_Y = 40, 30   # the box around the body and the goal the search runs in
STONE = 1                     # the game's tile type for stone: what a never-seen cell is taken for
_ARCS = {}


def arcs(phys, depth=0):
    """Jump paths from the body rule, with tile checks only where the footprint or floor crossing changes.

    Offsets are relative to the takeoff cell. A running jump holds the side for `run` ticks first.
    At nonzero depth the body's movement switches between water and air at the surface.
    """
    key = (depth,) + tuple(sorted((k, v) for k, v in phys.items() if isinstance(v, (int, float))))
    if key in _ARCS:
        return _ARCS[key]
    water = (lambda x, y: y >= 1 - depth) if depth else None
    out = []
    winged = phys.get("wing_time_max", 0) > 0 and not depth
    for hold in HOLDS + (WING_HOLDS if winged else ()):
        for side in (-1, 1):
            for run in (RUNS if hold in HOLDS else (0,)):
                b = B.start(0.0, -(BH - CELL))                 # the feet at the top of row 1: standing on it
                b["wet"] = depth > 0
                b["wing"] = phys.get("wing_time_max", 0)
                checks, pts = [], []
                previous_box, previous_floor = None, 1
                ground = lambda x, y: y >= 1                   # the floor under the run-up; none once in the air
                for t in range(1, (240 if hold in HOLDS else 420) + run):
                    way = "left" if side < 0 else "right"
                    keys = {way} if t <= run or t > run + hold else {"jump", way}
                    B.tick(b, keys, phys, ground if t <= run else (lambda x, y: False),
                           ground if t <= run else (lambda x, y: False), water)
                    dx, dy = b["x"], b["y"] + (BH - CELL)
                    c0, c1 = B.span(dx, BW)
                    r0, r1 = B.span(dy - (BH - CELL), BH)
                    feet = dy + CELL
                    box = (c0, c1, r0, r1)
                    floor = math.floor(feet / CELL)
                    crossings = range(previous_floor + 1, floor + 1)
                    runway = t <= run and (previous_box is None or box[:2] != previous_box[:2])
                    body_changed = box != previous_box
                    if runway or crossings or body_changed:
                        checks.append((t, box, runway, crossings, body_changed, b["fall_start"] + 2))
                    pts.append((dx, dy))                    # the body's centre, from the standing place's
                    previous_box, previous_floor = box, floor
                    if feet > MAX_FALL * CELL:
                        break
                out.append({"hold": hold, "side": side, "run": run, "checks": checks, "pts": pts, "depth": depth})
    _ARCS[key] = out
    return out


def fall_table(phys, n=MAX_FALL):
    """Ticks to fall k rows from a stand, by the gravity rule."""
    g, top = B.f32(phys["gravity"]), B.f32(phys["fall_top"])
    out, y, vy, t, k = [0] * (n + 1), 0.0, 0.0, 0, 1
    while k <= n:
        t += 1
        vy = min(B.f32(vy + g), top)
        y += vy
        while k <= n and y >= k * CELL:
            out[k] = t
            k += 1
    return out


def rise_table(phys):
    """How high a jump from a stand goes, by the body rule: the rows the feet clear and the tick each row is reached
    (the jump key held for its full hold, nothing in the way)."""
    b = B.start(0.0, 0.0)
    nothing = lambda x, y: False
    out, top = {}, 0.0
    for t in range(1, 120):
        B.tick(b, {"jump"} if t <= HOLDS[0] else set(), phys, nothing, nothing)
        top = min(top, b["y"])
        if b["vy"] >= 0 and t > 2:
            break
        k = int(-b["y"] // CELL)
        for j in range(1, k + 1):
            out.setdefault(j, t)
    return out


def pillar_ticks(phys, place, most=4, tile_boost=0):
    """Ticks to build k rows upward using the body and reach rules, jumping again after landing when needed."""
    out = {}
    for k in range(1, most + 1):
        b = B.start(0.0, -(BH - CELL))
        placed, t_last, at, jumped_at = 0, -99, [], 1
        for t in range(1, 120 * k):
            top = 1 - placed                               # the floor is row 1; each block raises it one row
            floor = (lambda x, y, tr=top: y >= tr)
            if b["ground"] and b["released"]:
                jumped_at = t
            B.tick(b, {"jump"} if t - jumped_at < HOLDS[0] else set(), phys, floor, floor)
            box = dict(b, w=BW, h=BH)
            if (placed < k and t - t_last >= place and b["y"] + BH <= -placed * CELL
                    and Hd.in_reach(box, (0, -placed), phys, tile_boost)):
                placed += 1
                t_last = t
                at.append(t)
            if placed == k and b["ground"]:
                out[k] = (t, at)
                break
    return out


class Prices:
    """What each move costs, from the rules with the live numbers."""

    def __init__(self, phys, kit):
        self.phys = phys
        self.top = B.f32(max(phys["run_top"], phys.get("acc_run", 0)))   # rule «run»: boots raise the ground top
        self.walk = CELL / self.top
        self.fall = fall_table(phys)
        self.arcs = arcs(phys)
        pick, block = kit.get("pick"), kit.get("block")
        self.pick_power = pick["pick"] if pick else 0
        self.dig_use = Hd.use_ticks(pick, phys.get("pick_speed", 1.0)) if pick else None
        self.place = Hd.use_ticks(block, phys.get("tile_speed", 1.0)) if block else None
        self.block_boost = block.get("tile_boost", 0) if block else 0
        self.pillars = pillar_ticks(phys, self.place, tile_boost=self.block_boost) if self.place else {}
        self.pillar = self.pillars.get(1, (None, None))[0]
        self.blocks = kit.get("blocks", 0) if block else 0
        plat = kit.get("platform")
        self.platform_place = Hd.use_ticks(plat, phys.get("tile_speed", 1.0)) if plat else None
        self.platforms = kit.get("platforms", 0) if plat else 0
        self.rise = rise_table(phys)                           # rows a jump clears -> the tick it gets there
        rope = kit.get("rope")
        self.rope_place = Hd.use_ticks(rope, phys.get("tile_speed", 1.0)) if rope else None
        self.ropes = kit.get("ropes", 0) if rope else 0
        self.climb_up, self.climb_down = 5.0, 4.0           # ticks a row on a rope (rule «rope»: 3 to 4.5 px a tick)
        self.hook = kit.get("hook")
        self.defense = phys.get("defense", 0)
        self.extra_fall = phys.get("extra_fall", 0)
        self.safe = max(k for k in range(MAX_FALL + 1) if not self.hurts(k))   # the tallest fall that costs nothing
        self.lava_safe = Tl.lava_safe(phys)            # rules «lava», «hot_blocks»: what the gear makes harmless
        self.hot_safe = Tl.hot_safe(phys)

    def arcs_at(self, depth):
        return arcs(self.phys, min(depth, 8))

    def dig(self, damage):
        """Ticks to dig one cell the pickaxe does `damage` to a hit (rules «mine», «use»), None when it cannot."""
        if not self.dig_use or damage <= 0:
            return None
        return Hd.dig_hits(damage) * self.dig_use

    def hurts(self, rows):
        return B.fall_damage(rows, self.defense, self.extra_fall) > 0


class Grid:
    """The search box as plain lists over the map: what each cell is, for fast lookups. Cells are box-relative."""

    def __init__(self, cells, x0, y0, w, h, prices, keep=()):
        self.x0, self.y0, self.w, self.h = x0, y0, w, h
        T = [[cells.tile(x0 + c, y0 + r) for c in range(w)] for r in range(h)]
        sk, tk, pk, na, cb = cells.solid_kind, cells.top_kind, cells.pick, cells.no_attach, cells.can_break
        self.floor_kind = {t: sk.get(t, False) or tk.get(t, False) for t in sk.keys() | tk.keys()}
        hk = cells.hurt_kind
        self.tile = T
        # the tiles that hurt (rules/tiles.py): a thorn is closed to walk through and open to dig; lava closes its
        # cell unless the gear makes it safe (a block may still go into it: a bridge); a spike or a hot block
        # closes the places that touch it, below (support) or beside (ring), unless the gear says otherwise
        self.lava_cell = [[cells.lava(x0 + c, y0 + r) for c in range(w)] for r in range(h)]
        self.lava = [[(not prices.lava_safe) and self.lava_cell[r][c] for c in range(w)] for r in range(h)]
        self.touchy = [[hk.get(t) == "spike" or (hk.get(t) == "hot" and not prices.hot_safe) if t >= 0 else False
                        for t in row] for row in T]
        thorn = lambda t: t >= 0 and hk.get(t) == "thorn"
        self.solid = [[t == -2 or (t >= 0 and (sk.get(t, True) or thorn(t))) or self.lava[r][c]
                       for c, t in enumerate(row)] for r, row in enumerate(T)]
        self.floor = [[t >= 0 and (sk.get(t, True) or tk.get(t, False)) and not self.touchy[r][c]
                       for c, t in enumerate(row)] for r, row in enumerate(T)]
        self.water = [[cells.water(x0 + c, y0 + r) for c in range(w)] for r in range(h)]
        self.rope = [[cells.rope(x0 + c, y0 + r) for c in range(w)] for r in range(h)]
        self.doors = [[cells.door(x0 + c, y0 + r) if hasattr(cells, "door") else False for c in range(w)] for r in range(h)]
        self.start = None                                      # the body's own place, for the hook (set by plan)
        # rule «place»: a block or a platform holds on a solid tile (platforms too) beside, under or over it, or on a
        # back wall behind it (the engine's placement check; the gym's place_rules with a tree trunk, Oct 20)
        self.holds = [[t >= 0 and (sk.get(t, False) or tk.get(t, False)) for t in row] for row in T]
        self.walls = [[cells.wall(x0 + c, y0 + r) if hasattr(cells, "wall") else 0 for c in range(w)] for r in range(h)]
        self._plat = {}


        def dig_of(t, c, r):
            if self.lava[r][c]:
                return None
            if cells.door(x0 + c, y0 + r) if hasattr(cells, "door") else False:
                return 0                                   # a closed door is opened, not dug (the walk prices it)
            if t == -1:
                return 0
            if t == -2:
                return None
            if not sk.get(t, True) and not thorn(t):
                return 0                                   # a platform, a torch, a plant: the body walks through
            if self.touchy[r][c]:
                return None                                # digging a spike or a hot block means touching it
            if any(kx0 <= x0 + c <= kx1 and ky0 <= y0 + r <= ky1 for kx0, ky0, kx1, ky1 in keep):
                return None                                # a zone kept as it is (a real world's home)
            return prices.dig(pk.get(t, 0)) if cb(x0 + c, y0 + r) else None
        self.dig = [[dig_of(t, c, r) for c, t in enumerate(row)] for r, row in enumerate(T)]
        self._fits, self._free = {}, {}
        self._jump_masks = None

    def jump_masks(self):
        """One bit per column. Combine rows so a whole jump footprint can be tested at once."""
        if self._jump_masks is None:
            def row_masks(matrix):
                return [sum(1 << c for c, cell in enumerate(row) if cell) for row in matrix]

            def spans(rows):
                combined = [None, rows]
                for offset in range(1, MAX_BODY_ROWS):
                    combined.append([v | rows[r + offset] if r + offset < self.h else v
                                     for r, v in enumerate(combined[-1])])
                return combined

            solid, water = row_masks(self.solid), row_masks(self.water)
            self._jump_masks = (row_masks(self.floor), spans(solid),
                                spans([s | w for s, w in zip(solid, water)]))
        return self._jump_masks

    def S(self, c, r):
        return self.solid[r][c] if 0 <= r < self.h and 0 <= c < self.w else True

    def F(self, c, r):
        return self.floor[r][c] if 0 <= r < self.h and 0 <= c < self.w else False

    def Wt(self, c, r):
        return self.water[r][c] if 0 <= r < self.h and 0 <= c < self.w else False

    def head_wet(self, c, r):
        return self.Wt(c, r - 2) or self.Wt(c + 1, r - 2)

    def D(self, c, r):
        return self.dig[r][c] if 0 <= r < self.h and 0 <= c < self.w else None

    def H(self, c, r):
        return self.holds[r][c] if 0 <= r < self.h and 0 <= c < self.w else False

    def Wl(self, c, r):
        return self.walls[r][c] if 0 <= r < self.h and 0 <= c < self.w else 0

    def held(self, c, r):
        """A cell a platform or block set there would hold on: a solid tile on a side, under or over it, or a wall."""
        return self.H(c - 1, r) or self.H(c + 1, r) or self.H(c, r + 1) or self.H(c, r - 1) or bool(self.Wl(c, r))

    def rungs(self, rc, r1, P):
        """Rule «platform» (the gym probe, Oct 20): a platform goes into an empty cell held like a block, never into
        the body's cells; one stacked on another holds. The platforms a rung at (rc, r1) takes: itself, and the ones
        stacked under it down to a held cell; None when that needs more than the bag has or meets a tile."""
        cells = []
        for y in range(r1, r1 + P.platforms):
            if not self.air(rc, y):
                return None
            cells.append((rc, y))
            if self.held(rc, y):
                return cells[::-1]  # Build from the existing support upward.
        return None

    def platform_steps(self, c, r, P):
        """The steps of a stair that end standing at (c, r) on a platform set under one of its columns: from a
        supported place one column aside and 2 to rise rows down, the rungs in the column the body does not fill
        there (the jump goes up through the platforms: rule «platform»). Cached per place."""
        k0 = (c, r)
        if k0 in self._plat:
            return self._plat[k0]
        out = []
        if P.platform_place is not None and P.platforms > 0 and self.free(c, r) and not self.support(c, r):
            for side in (-1, 1):
                rc = c + 1 if side < 0 else c
                for k in range(2, max(self.rise_rows(P), 2) + 1):
                    u = (c + side, r + k)
                    if not (self.free(*u) and self.support(*u)):
                        continue
                    if u[1] - 6 > r + 1:
                        continue                              # the rung is out of the hand's reach from u
                    cols = range(min(c, c + side), max(c + 1, c + side + 1) + 1)
                    if any(self.S(x, y) for x in cols for y in range(r - 2, r + k - 2)):
                        continue                              # the way up is blocked
                    cells = self.rungs(rc, r + 1, P)
                    if cells is None:
                        continue
                    ticks = len(cells) * P.platform_place + P.rise.get(k, 60) + 8
                    out.append((u, ticks, cells))
        self._plat[k0] = out
        return out

    def rise_rows(self, P):
        return max(P.rise) if P.rise else 0

    def air(self, c, r):
        """A cell a block can go into: no tile and no lava (the game refuses a block in lava: the gym, Oct 19)."""
        return 0 <= r < self.h and 0 <= c < self.w and self.tile[r][c] == -1 and not self.lava_cell[r][c]

    def Tc(self, c, r):
        return self.touchy[r][c] if 0 <= r < self.h and 0 <= c < self.w else False

    def touches(self, c, r):
        """Whether a body standing at (c, r) touches a spike or a hot block: under it, beside it or over its head."""
        return (self.Tc(c, r + 1) or self.Tc(c + 1, r + 1) or self.Tc(c, r - 3) or self.Tc(c + 1, r - 3) or
                any(self.Tc(c - 1, rr) or self.Tc(c + 2, rr) for rr in (r - 2, r - 1, r)))

    def free(self, c, r):
        """Rule «body»: the six cells of a standing place hold no solid tile."""
        k = (c, r)
        v = self._free.get(k)
        if v is None:
            v = not (self.S(c, r) or self.S(c + 1, r) or self.S(c, r - 1) or self.S(c + 1, r - 1) or
                     self.S(c, r - 2) or self.S(c + 1, r - 2) or self.touches(c, r))
            self._free[k] = v
        return v

    def fits(self, c, r):
        """A standing place once its solid cells are dug: each of the six is free or diggable."""
        k = (c, r)
        v = self._fits.get(k)
        if v is None:
            v = all(self.D(cc, rr) is not None for cc in (c, c + 1) for rr in (r - 2, r - 1, r)) and not self.touches(c, r)
            self._fits[k] = v
        return v

    def digs(self, cells):
        """Ticks to dig the solid ones among these cells, and which they are; None when one cannot be dug."""
        ticks, which = 0.0, []
        for cc, rr in cells:
            d = self.D(cc, rr)
            if d is None:
                return None, None
            if d > 0:
                ticks += d
                which.append((cc, rr))
        return ticks, which

    def door(self, c, r):
        return self.doors[r][c] if 0 <= r < self.h and 0 <= c < self.w else False

    def R(self, c, r):
        return self.rope[r][c] if 0 <= r < self.h and 0 <= c < self.w else False

    def columns(self):
        """Per column, once: the run of cells a rope can hang in (air or rope) ending at each row going up, the run
        of free standing places ending at each row going up, and the rows where a floor's top is (a floor with no
        solid cell over it): what a rope down a cliff needs, looked up instead of scanned."""
        if getattr(self, "_air_up", None) is None:
            W, H = self.w, self.h
            self._air_up = [[0] * H for _ in range(W)]
            self._free_up = [[0] * H for _ in range(W)]
            self._tops = [[] for _ in range(W)]
            self._rope_cols = {c for c in range(W) for r in range(H) if self.R(c, r)}
            for c in range(W):
                a = f = 0
                for r in range(H):
                    a = a + 1 if (self.air(c, r) or self.R(c, r)) else 0
                    f = f + 1 if self.free(c, r) else 0
                    self._air_up[c][r], self._free_up[c][r] = a, f
                    if self.F(c, r) and not self.S(c, r - 1):
                        self._tops[c].append(r)
        return self._air_up, self._free_up, self._tops

    def support(self, c, r):
        """Something holds the feet: a floor under either column, or a rope in either at the feet's row (rule «rope»:
        the game counts a hanging body as standing)."""
        return self.F(c, r + 1) or self.F(c + 1, r + 1) or self.hold(c, r)

    def holdable(self, c, r):
        """A rope could be set at this place's grab point: an empty cell there with a wall beside it (rule «rope»)."""
        return any(self.air(cc, r - 3) and self.beside(cc, r - 3) for cc in (c, c + 1))

    def hold(self, c, r):
        """A rope holds the character at this place: the game grabs a rope only at the point 8 px over the head in
        the centre column, three rows over the feet row (rule «rope», gym Oct 20)."""
        return self.R(c, r - 3) or self.R(c + 1, r - 3)

    def beside(self, c, r):
        """A solid tile left or right of the cell: what a rope can hold on to."""
        return self.S(c - 1, r) or self.S(c + 1, r)

    def wet(self, c, r):
        """Rule «water_move»: the body's middle in water (its feet row or the one above)."""
        return self.Wt(c, r) or self.Wt(c + 1, r) or self.Wt(c, r - 1) or self.Wt(c + 1, r - 1)

    def depth(self, c, r):
        """Rows of water from the feet row up to the surface, 0 when the feet stand dry."""
        d = 0
        while d < 8 and (self.Wt(c, r - d) or self.Wt(c + 1, r - d)):
            d += 1
        return d


def jump_edges(g, P, u, arcs_, dry):
    """Yield (landing cell, tick, arc index) for jumps whose run-up, flight and landing are clear."""
    c, r = u
    floors, solid, solid_or_water = g.jump_masks()
    flight = solid_or_water if dry else solid
    for ai, arc in enumerate(arcs_):
        for tick, (c0, c1, r0, r1), runway, crossings, body_changed, fall_start in arc["checks"]:
            left, right = c + c0, c + c1
            in_width = 0 <= left and right < g.w
            columns = (1 << (c1 - c0 + 1)) - 1
            columns = columns << left if left >= 0 else columns >> -left
            if runway and (not in_width or not 0 <= r + 1 < g.h or floors[r + 1] & columns != columns):
                break
            top = r + r0
            landed = False
            for row in crossings:
                floor = r + row
                if not 0 <= floor < g.h or not floors[floor] & columns:
                    continue
                landed = True
                # A wall above the floor can stop the body before it lands.
                height = min(row, r1 + 1) - r0
                if height > 0 and (not in_width or top < 0 or top + height > g.h or solid[height][top] & columns):
                    break
                for col in range(left, right):
                    v = (col, floor - 1)
                    if g.support(*v) and g.free(*v):
                        safe = not P.hurts(row - 3 - fall_start) or (g.Wt(*v) and g.Wt(col, floor - 2))
                        if v != u and safe:
                            yield v, tick, ai
                        break
                break
            if landed:
                break
            height = r1 - r0 + 1
            if body_changed and (not in_width or top < 0 or top + height > g.h or flight[height][top] & columns):
                break


def rope_descents(g, P, c, r, hanging=False):
    """Descend to a known landing, or place enough rope to stop while still holding it."""
    out = []
    # Start beside the ledge, where the first rope can attach to the edge block.
    # Look up ledges by column so the route search can skip open sky.
    air_up, free_up, tops = g.columns()
    for col, uc, face in ((c, c - 2, c - 1), (c + 1, c + 1, c + 2)):
        if not (0 <= col < g.w and 0 <= face < g.w):
            continue
        for e in tops[face]:
            if e > r or r - e >= MAX_FALL + ROPE_MOST:
                continue
            u = (uc, e - 1)
            if r - e + 1 > air_up[col][r] or r - e + 2 > free_up[c][r] or not g.free(*u):
                continue
            if hanging and r < e + 3:
                continue
            if not hanging and not P.hurts(r - e - 1):
                continue                                   # a plain fall from the edge is safe: no rope needed
            n = r - e - 2 if hanging else max(1, r - 2 - P.safe - e)
            # the ropes the game needs: from 4 rows over the edge (where the character's grab point is when it
            # stands at the edge) down; set beside the edge block first, then up to that point, then down
            order = [e] + [e - i for i in range(1, 5)] + [e + i for i in range(1, n)]
            if not all(g.air(col, y) or g.R(col, y) for y in order):
                continue
            cells = [(col, y) for y in order if not g.R(col, y)]
            if not cells or len(cells) > P.ropes or n > ROPE_MOST:
                continue
            rows = r - e + 1
            climb = rows if hanging else min(rows, n + 1)
            travel = climb * P.climb_down + P.fall[rows - climb]
            out.append((u, len(cells) * P.rope_place + travel + 20, ("rope_down", cells)))
    # more ropes under the end of a rope already hanging over here: the character holds the rope's end, sets the
    # next ropes under it and climbs down them, then drops the rest when it is a safe fall (a descent done in
    # steps; only in columns that have a rope, so it costs nothing elsewhere)
    for col in (c, c + 1):
        if col not in g._rope_cols:
            continue
        q = None
        for y in range(r - (4 if hanging else 1), max(-1, r - MAX_FALL - ROPE_MOST - 1), -1):
            if g.R(col, y):
                q = y
                break
            if not g.air(col, y):
                break
        if q is None or (not hanging and r - q - 3 <= P.safe):
            continue                                       # no rope over here, or the drop from its end is safe
        u = (c, q + 3)                                     # holding the rope's end: its grab point at row q
        if not g.free(*u):
            continue
        n = r - 3 - q if hanging else r - 2 - P.safe - q
        cells = [(col, q + i) for i in range(1, n + 1)]
        if n > P.ropes or n > ROPE_MOST or not all(g.air(x, y) for x, y in cells):
            continue
        rows = r - (q + 3)
        out.append((u, n * P.rope_place + min(rows, n) * P.climb_down + P.fall[max(0, rows - n)] + 10, ("rope_down", cells)))
    return out


def landing_steps(g, P, c, r, max_drop):
    """A lower tread built from solid footing. Place the support before stepping off."""
    for side in (-1, 1):
        anchor, tread = (c, c + 1) if side > 0 else (c + 1, c)
        if not g.H(anchor, r + 1):
            continue
        nx = c + side
        support = []
        for drop in range(1, max_drop + 1):
            row = r + drop
            if not g.free(nx, row - 1) or not g.free(nx, row) or g.support(nx, row):
                break
            if not g.H(anchor, row + 1):
                if not g.air(anchor, row + 1):
                    break
                support.append((anchor, row + 1))
            cell = (tread, row + 1)
            if not g.air(*cell):
                continue
            cells = [*support, cell]
            if len(cells) <= P.blocks:
                yield (nx, row), len(cells) * P.place + P.walk + P.fall[drop], ("landing", cells)


def frontier(g, P, gx, gy, search_area=None):
    """Price standing places that border unknown cells, optionally inside a requested search area.
    A stop that needs building can only be reached by the move that builds it."""
    out, built_stops, T = [], {}, g.tile
    reach = int(P.phys["range_y"] + P.block_boost + P.phys.get("block_range", 0) - 2) if P.blocks else 0
    max_drop = min(P.safe, reach)
    def price(c, r):
        return P.walk * (abs(c + g.x0 - gx) + abs(r + g.y0 - gy))
    def unknown_near(c, r):
        edge = [(x, y) for x in (c - 1, c + 2) for y in range(r - 2, r + 1)]
        edge += [(x, y) for x in (c, c + 1) for y in (r - 3, r + 1)]
        return any(0 <= x < g.w and 0 <= y < g.h and T[y][x] == -2 and (search_area is None or
                   search_area[0] <= x + g.x0 <= search_area[2] and
                   search_area[1] <= y + g.y0 <= search_area[3])
                   for x, y in edge)
    for r in range(3, g.h - 1):
        for c in range(1, g.w - 2):
            supported = g.fits(c, r) and g.support(c, r)
            if max_drop > 0 and supported and g.free(c, r) and r + g.y0 < gy:
                for node, ticks, edge in landing_steps(g, P, c, r, min(max_drop, gy - r - g.y0)):
                    if price(*node) >= price(c, r) or not unknown_near(*node):
                        continue
                    built_stops.setdefault(node, []).append(((c, r), ticks, edge))
            if supported and unknown_near(c, r):
                out.append(((c, r), 0 if search_area is not None else price(c, r)))
            elif (P.ropes and not supported and r + g.y0 < gy and g.free(c, r)
                  and unknown_near(c, r)):
                edges = rope_descents(g, P, c, r, hanging=True)
                if edges:
                    built_stops.setdefault((c, r), []).extend(edges)
    out.extend((node, 0 if search_area is not None else price(*node)) for node in built_stops)
    return out, built_stops


def flood(g, P, goal_nodes, start=None, margin=600, seeds=(), avoid_submerged=False, built_stops=None):
    """Dijkstra from the goal over the moves run backward: ticks to go for every standing place reached, and the
    move each takes next. Stops `margin` ticks past the start's own number once it has one (10 s: what the fast
    brain can land on from there), so a far goal does not flood the whole box."""
    inf = float("inf")
    built_stops = built_stops or {}
    into = {}
    ropes = P.rope_place is not None and P.ropes > 0
    for r in range(g.h):
        for c in range(g.w):
            # jumps from where the character stands, holds an existing rope, or could hold one it sets (the top of
            # ropes up a shaft ends below the surface: the character jumps off onto the ground, Oct 20)
            if avoid_submerged and g.head_wet(c, r):
                continue
            if g.free(c, r) and (g.support(c, r) or (ropes and g.holdable(c, r)) or g.platform_steps(c, r, P)):
                depth = g.depth(c, r)
                for v, t, ai in jump_edges(g, P, (c, r), P.arcs_at(depth), depth == 0):
                    into.setdefault(v, []).append(((c, r), float(t), ("jump", ai)))
    dist, nxt, heap, limit = {}, {}, [], inf
    made = set()                                              # places the body makes for itself, jumps added once
    for gn in goal_nodes:
        if avoid_submerged and g.head_wet(*gn):
            continue
        dist[gn] = 0.0
        heapq.heappush(heap, (0.0, gn))
    for gn, d0 in seeds:                                      # the edge of the seen map, priced (frontier())
        if (not avoid_submerged or not g.head_wet(*gn)) and d0 < dist.get(gn, inf):
            dist[gn] = d0
            heapq.heappush(heap, (d0, gn))
    while heap:
        d, v = heapq.heappop(heap)
        if d > dist.get(v, inf):
            continue
        if d > limit:
            break
        if v == start:
            limit = d + margin
        edges = built_stops[v] if v in built_stops else preds(g, P, v, into)
        for u, cost, edge in edges:
            if avoid_submerged and g.head_wet(*u):
                continue
            nd = d + cost
            if nd < dist.get(u, inf):
                dist[u] = nd
                nxt[u] = (v, edge)
                heapq.heappush(heap, (nd, u))
            if edge[0] == "hook":
                h = edge[2]
                if d + 30 < dist.get(h, inf):
                    dist[h] = d + 30                              # at rest on the hook: the ledge's number plus the half
                    nxt[h] = (v, ("jump_up",))                    # jump up onto it, 30 ticks (rule «hook», gym)
    return dist, nxt


def preds(g, P, v, into):
    """The moves that end at v: (u, ticks, what the move is)."""
    c, r = v
    out = list(into.get(v, ()))
    sup = g.support(c, r)
    free_v = g.free(c, r)
    # a place held by a rope, not a floor: getting there is a grab (down or up held while the body passes the
    # rope), not a walk, so it is not priced by the pixel and it needs the climb keys (rule «rope»)
    held = sup and not (g.F(c, r + 1) or g.F(c + 1, r + 1))
    step_kind = "grab" if held else "walk"
    if sup:
        wet_v = g.wet(c, r)
        for d in (-1, 1):
            # a walk into the next column, its solid cells dug first (rules «run», «mine»)
            u = (c - d, r)
            col = c + 1 if d > 0 else c
            if g.fits(*u):
                ticks, cells = g.digs([(col, r - 2), (col, r - 1), (col, r)])
                if ticks is not None:
                    w = P.walk * (2 if wet_v or g.wet(*u) else 1)
                    doors = [(col, rr) for rr in (r - 2, r - 1, r) if g.door(col, rr)]
                    if doors:
                        # a closed door in the way: a right-click opens it (the hand), then the walk (rule «door»)
                        out.append((u, w + ticks + OPEN_TICKS, ("open", d, doors)))
                    else:
                        out.append((u, w + ticks, ("dig", d, cells) if cells else (step_kind, d)))
            if free_v:
                # a step up or down one row (rule «step»)
                for k in (-1, 1):
                    u = (c - d, r - k)
                    if g.free(*u):
                        # On a down-step the head crosses the near column before the body falls.
                        cost, clearance = g.digs([(col, r - 3)]) if k == 1 else (0, [])
                        if cost is not None:
                            edge = ("dig", d, clearance) if clearance else (step_kind, d)
                            out.append((u, cost + P.walk * (2 if wet_v or g.wet(*u) else 1), edge))
                if held:
                    continue  # A fall needs a floor. Rope requires a grab.
                # Off an edge and down to here, with no floor in between.
                for k in range(2, MAX_FALL + 1):
                    u, off = (c - d, r - k), (c, r - k)
                    if g.F(c, r - k + 1) or g.F(c + 1, r - k + 1):
                        break                              # a floor in the way: no taller fall lands here either
                    if not g.free(*off):
                        break
                    if g.support(*off) or not g.free(*u) or not g.support(*u):
                        continue
                    if P.hurts(k) and not (g.Wt(c, r) and g.Wt(c, r - 1)):
                        break
                    out.append((u, P.walk + P.fall[k], ("fall", d, k)))
        if free_v and not held:
            # down through a platform with down held (rule «platform»)
            for k in range(1, MAX_FALL + 1):
                u = (c, r - k)
                if k > 1 and (g.F(c, r - k + 2) or g.F(c + 1, r - k + 2)):
                    break
                if not g.free(*u):
                    break
                if g.S(c, r - k + 1) or g.S(c + 1, r - k + 1) or not g.support(*u):
                    continue
                if P.hurts(k) and not (g.Wt(c, r) and g.Wt(c, r - 1)):
                    break
                out.append((u, float(P.fall[k]), ("drop", k)))
        # Dig the floor, then fall to the next supported place. With one column already open the character
        # would stand on one column beside that opening: only when the drop under it is a safe fall (a shaft dug at
        # a deadly cliff's edge had one side open to a 40-row drop and the character slid off it, Oct 20)
        for drop in range(1, MAX_FALL + 1):
            if P.hurts(drop):
                break
            row = r - drop + 1
            ticks, cells = g.digs([(c, row), (c + 1, row)])
            if ticks is None:
                break
            if not cells:
                continue
            u = (c, r - drop)
            opened = [cc for cc in (c, c + 1) if (cc, row) not in cells]
            safe = all(g.F(cc, r + 1) or any(g.F(cc, y) for y in range(r + 2, u[1] + P.safe + 2)) for cc in opened)
            if g.fits(*u) and safe:
                out.append((u, ticks + P.fall[drop], ("dig_down", cells)))
            break  # Higher floors can be reached through this one; do not skip it.
    if g.hold(c, r) and free_v:
        # on a rope: a climb from the row below or above along it (rule «rope»)
        for k, cost in ((1, P.climb_up), (-1, P.climb_down)):
            u = (c, r + k)
            if g.hold(c, r + k) and g.free(*u):
                out.append((u, cost, ("climb", -k)))
    if P.rope_place is not None and P.ropes > 0 and free_v and sup:
        out.extend(rope_descents(g, P, c, r))
    if P.rope_place is not None and P.ropes > 0 and free_v:
        for col in (c, c + 1):
            # ropes up a shaft: each one holds on to a wall beside it, set from below and climbed; the place below
            # may itself be one the body made (its own ropes or blocks), as with pillars (rule «rope»)
            for k in range(1, ROPE_MOST + 1):
                u = (c, r + k)
                # the ropes from the grab point at the bottom (3 rows over u's feet) to the one at the top (3 rows
                # over v's feet), the lowest first, each held by the wall beside it (rule «rope»)
                cells = [(col, r + k - 3 - j) for j in range(k + 1)]
                if not g.free(*u):
                    break
                if not all((g.air(x, y) or g.R(x, y)) and g.beside(x, y) for x, y in cells):
                    break
                if not all(g.free(c, rr) for rr in range(r, r + k)):
                    break
                missing = [xy for xy in cells if not g.R(*xy)]
                if not missing or len(missing) > P.ropes:
                    break
                out.append((u, len(missing) * P.rope_place + k * P.climb_up + 10, ("rope_up", missing)))
    if P.hook is not None and sup and free_v:
        # the hook: on the wall under one of v's edges, from any standing place in the open in front of it within
        # reach along a clear line; the body hangs at the latch and jumps up onto v (rule «hook», rules/hands.py)
        reach_cells = int(Hd.HOOK["reach"] // CELL) + 1
        for side, wall, hang_c in ((-1, c, c - 2), (1, c + 1, c + 1)):
            for j in range(0, HOOK_ROWS + 1):
                T = (wall, r + 1 + j)
                h = (hang_c, r + 2 + j)                   # the hang: the body's near edge at the wall's face, its
                                                          # centre at the latch's row, so its feet a row lower (gym)
                if not g.S(*T) or not g.free(*h) or g.S(wall + side, r + 1 + j):
                    continue
                tx, ty = T[0] * CELL + (-1 if side < 0 else CELL + 1), T[1] * CELL + CELL / 2   # the near face
                for uc in range(wall + side, wall + side * reach_cells, side):
                    for ur in range(r - 2, r + 1 + j + reach_cells):
                        u = (uc, ur)
                        if u == v or not g.free(*u) or not g.support(*u):
                            continue
                        ucx, ucy = uc * CELL + BW / 2, (ur + 1) * CELL - BH / 2
                        d = ((tx - ucx) ** 2 + (ty - ucy) ** 2) ** 0.5
                        if d > Hd.HOOK["reach"] or d < CELL:
                            continue
                        n = int(d // 8) + 1
                        # the hook is a box of HOOK_PX, not a line: it latches on the first solid cell its box touches
                        # (fired up along a wall's face it latched 2 rows up, not at the ledge: the gym, Oct 20). The
                        # flight is clear when that first cell is the target, or its wall neighbour one row off
                        half = HOOK_PX / 2
                        clear = True
                        for i in range(1, n + 1):
                            px, py = ucx + (tx - ucx) * i / n, ucy + (ty - ucy) * i / n
                            touched = [(int((px + dx) // CELL), int((py + dy) // CELL)) for dx in (-half, half) for dy in (-half, half)]
                            touched = [cc for cc in touched if g.S(*cc)]
                            if touched:
                                clear = all(cc[0] == T[0] and abs(cc[1] - T[1]) <= 1 for cc in touched)
                                break
                        if not clear:
                            continue
                        out.append((u, d / Hd.HOOK["speed"] + d / Hd.HOOK["pull"] + 30, ("hook", T, h)))
                break
    for u, ticks, cells in g.platform_steps(c, r, P):
        out.append((u, ticks, ("platform", cells)))          # a step of a platform stair (rule «platform»)
    if P.place is not None and P.blocks > 0:
        # a pillar: from k rows down, jumping and setting k blocks under the feet (rules «jump», «use», «reach»);
        # the rows the body rises through are free, or dug first from where it stands (a block over the head in a
        # shaft, our own from a pillar gone wrong, left the pathfinder no way up: it routed 156 s around, Oct 19)
        for k, (ticks_k, _) in P.pillars.items():
            u = (c, r + k)
            # As with sideways mining, the preceding move may have cleared this body space.
            if not g.fits(*u) or ticks_k is None:
                continue
            dticks, dcells = g.digs([(cc, rr) for cc in (c, c + 1) for rr in range(r - 2, r + k - 2)])
            if dticks is None:
                continue
            col = None
            for cc in (c, c + 1):
                # either column of the place: the bottom block holds on to a tile below or beside it (rule «place»),
                # and the cells the blocks go into are air, or dug by this very move (a block over the head in the
                # shaft is dug and a pillar block takes its cell, Oct 20)
                holds = g.H(cc, r + k + 1) or g.H(cc - 1, r + k) or g.H(cc + 1, r + k) or not g.support(*u)
                if holds and all(g.air(cc, r + j) or (cc, r + j) in dcells for j in range(1, k + 1)):
                    col = cc
                    break
            if col is None:
                continue
            cells = [(col, r + j) for j in range(k, 0, -1)]   # bottom block first
            out.append((u, ticks_k + dticks, ("pillar", cells, dcells)))
        # a bridge: nothing holds the feet here, a block goes under the near column, then the walk
        if not sup and free_v:
            for d in (-1, 1):
                u = (c - d, r)
                cell = (c, r + 1) if d > 0 else (c + 1, r + 1)
                far = (c - 1, r + 1) if d > 0 else (c + 2, r + 1)
                if g.free(*u) and g.air(*cell) and (g.H(*far) or not g.support(*u)):
                    out.append((u, P.place + P.walk, ("bridge", d, cell)))
    return out


class Plan:
    """A goal's flood: ticks to go per standing place (world cells), the way from the start, and what the fast
    brain's hands should work on first."""

    def __init__(self, cell, grid, prices, dist, nxt, start, version):
        self.cell, self.grid, self.prices, self.version = tuple(cell), grid, prices, version
        ox, oy = grid.x0, grid.y0
        self.T = {(c + ox, r + oy): t for (c, r), t in dist.items()}
        self.nxt = {(u[0] + ox, u[1] + oy): ((v[0] + ox, v[1] + oy), e) for u, (v, e) in nxt.items()}
        self.start = start
        self.ticks = self.T.get(start) if start else None
        self.way = self.way_from(start) if start in self.T else []
        self.blocks = sum(len(e.get("put", [])) for e in self.way if e["kind"] in ("pillar", "bridge", "landing"))
        self.platforms = sum(len(e.get("platform", [])) for e in self.way)
        self.ropes = sum(len(e.get("rope", [])) for e in self.way)
        self._edges = {}
        self.ms = 0
        self.explore = False

    def at(self, c, r):
        return self.T.get((c, r))

    def node_of(self, b, put=(), dug=()):
        """The standing place of a body (x, y in px): its left column and feet row, or the column beside when it
        straddles three cells. Of the two, the one that has support in the map (the body stands on it, not on a
        block still to come), then the one with the fewer ticks to go."""
        x0, x1, top, feet = B.cells_of(b)
        g = self.grid
        cands = [(x0, feet)] + ([(x0 + 1, feet)] if x1 - x0 >= 2 else [])
        if b.get("rope"):
            col, row = B.rope_grab(b)
            nodes = [(c, row + 3) for c in (x0, col, col - 1) if col in (c, c + 1)]
            return min(nodes, key=lambda n: self.T.get(n, float("inf")))
        if b.get("hooked"):
            known = [n for n in cands if n in self.T]          # on its hook the body hangs where it is
            return min(known, key=lambda n: self.T[n]) if known else cands[0]
        def floor(c, r):
            if (c, r) in put:
                tile = put[c, r] if isinstance(put, dict) else 0
                return g.floor_kind.get(tile, True)
            return (c, r) not in dug and g.F(c - g.x0, r - g.y0)

        def supported(c, r):
            return floor(c, r + 1) or floor(c + 1, r + 1) or g.hold(c - g.x0, r - g.y0)

        if not any(supported(*n) for n in cands):
            # a body in the air (or swimming) belongs to the place it would come down on: the first row below with
            # support. Read at its own row mid-jump, the way's work was the pillar's upper cells, which cannot be set
            # before the lower ones, so every pillar stopped at one block (cliff, Oct 19 night)
            for dr in range(1, 40):
                low = [(c, r + dr) for c, r in cands if supported(c, r + dr)]
                if low:
                    cands = low
                    break
        known = [n for n in cands if n in self.T]
        if not known:
            return cands[0]
        real = [n for n in known if supported(*n)]
        return min(real or known, key=lambda n: self.T[n])

    def next_edge(self, node):
        """The way's first move from a standing place, or None."""
        if node not in self.nxt:
            return None
        if node not in self._edges:
            v, e = self.nxt[node]
            self._edges[node] = self.edge(node, v, e)
        return self._edges[node]

    def work_at(self, node, reach=REACH):
        """What the hands can do from here: the cells the way digs and the blocks it sets as far along as the hand
        reaches from this standing place (REACH cells each way, a little past the game's own reach; the fast brain
        checks the exact reach when it plays the hand). The same work and its costs are used for action generation
        and progress measurement, including when a forecast stops partway through a move."""
        out = {"dig": [], "put": [], "under": set(), "rope": [], "hook": None, "open": [], "platform": [],
               "ticks": self.at(*node), "costs": {kind: {} for kind in ("dig", "put", "rope", "open", "platform")}}
        u = node
        near = lambda c: abs(c[0] - node[0]) <= reach and abs(c[1] - node[1]) <= reach
        for _ in range(4 * reach):
            e = self.next_edge(u)
            if e is None or not near(u):
                break                                      # the move starts out of reach; a long move from near
                                                           # here (ropes up a shaft) still has cells in reach
            for c in e.get("rope", []):
                if near(c):
                    out["rope"].append(c)
                    out["costs"]["rope"][c] = self.prices.rope_place
            for c in e.get("open", []):
                if near(c) and c not in out["open"]:
                    out["open"].append(c)
                    out["costs"]["open"][c] = OPEN_TICKS / len(e["open"])
            for c in e.get("platform", []):
                if near(c) and c not in out["platform"]:
                    out["platform"].append(c)
                    out["costs"]["platform"][c] = self.prices.platform_place
            if e["kind"] == "hook" and out["hook"] is None and u == node:
                out["hook"] = e["hook"]
            for c in e.get("dig", []):
                if near(c):
                    out["dig"].append(c)
                    out["costs"]["dig"][c] = self.grid.D(c[0] - self.grid.x0, c[1] - self.grid.y0) or 0
            if e["kind"] == "landing" and all(near(c) for c in e["put"]):
                out["landing"] = e["put"]
            for c in e.get("put", []):
                if near(c):
                    out["put"].append(c)
                    out["costs"]["put"][c] = self.prices.place
                    if e["kind"] == "pillar":
                        out["under"].add(c)            # a block to stand on: set only from above (rule «pillar»)
            u = e["to"]
        return out

    def way_from(self, node):
        out, u, seen = [], node, set()
        while u in self.nxt and u not in seen:
            seen.add(u)
            v, e = self.nxt[u]
            out.append(self.edge(u, v, e))
            u = v
        return out

    def edge(self, u, v, e):
        g, P, ox, oy = self.grid, self.prices, self.grid.x0, self.grid.y0
        kind = e[0]
        d = {"kind": kind, "from": u, "to": v, "ticks": round(self.T[u] - self.T.get(v, 0), 1)}
        if kind == "jump":
            arc = P.arcs_at(g.depth(u[0] - ox, u[1] - oy))[e[1]]
            x0, y0 = u[0] * CELL + BW / 2, (u[1] + 1) * CELL - BH / 2
            n = int(round(self.T[u] - self.T.get(v, 0)))
            d["arc"] = [[round(x0 + dx), round(y0 + dy)] for dx, dy in arc["pts"][:n]]
            d["hold"], d["side"], d["run"] = arc["hold"], arc["side"], arc["run"]
        elif kind == "dig":
            d["dig"] = [(x + ox, y + oy) for x, y in e[2]]
        elif kind == "dig_down":
            d["dig"] = [(x + ox, y + oy) for x, y in e[1]]
        elif kind == "pillar":
            d["put"] = [(x + ox, y + oy) for x, y in e[1]]
            d["dig"] = [(x + ox, y + oy) for x, y in e[2]]
        elif kind == "landing":
            d["put"] = [(x + ox, y + oy) for x, y in e[1]]
        elif kind == "bridge":
            d["put"] = [(e[2][0] + ox, e[2][1] + oy)]
        elif kind == "platform":
            d["platform"] = [(x + ox, y + oy) for x, y in e[1]]
        elif kind in ("rope_down", "rope_up"):
            d["rope"] = [(x + ox, y + oy) for x, y in e[1]]
        elif kind == "hook":
            d["hook"] = (e[1][0] + ox, e[1][1] + oy)
            d["hang"] = (e[2][0] + ox, e[2][1] + oy)
        elif kind == "climb":
            d["rows"] = e[1]
        elif kind == "open":
            d["open"] = [(x + ox, y + oy) for x, y in e[2]]
        elif kind == "jump_up":
            pass                                                  # from a hang up onto the ledge
        # the spot: the body's x (px) where this move can begin, for the fast brain's tie-break (a move that ends
        # with the same ticks to go but nearer the spot is the better one: the rope one step to the left, Oct 20)
        if kind in ("climb", "rope_up", "grab") or (kind == "rope_down" and d["from"][0] == d["to"][0]):
            at = v if kind == "grab" else u
            col = d["rope"][0][0] if d.get("rope") else next((cc for cc in (at[0], at[0] + 1) if g.R(cc - ox, at[1] - 3 - oy)), at[0])
            d["spot"] = (col * CELL - 10, col * CELL + 5)          # the centre in the rope's column (rule «rope»)
        elif kind == "rope_down":
            col = d["rope"][0][0]
            d["spot"] = (col * CELL - 20, col * CELL - 20) if u[0] < col else ((col + 1) * CELL, (col + 1) * CELL)
        elif kind == "pillar" and d.get("put"):
            col = d["put"][0][0]
            d["spot"] = (col * CELL - 10, col * CELL + 5)          # the centre over the pillar's column
        elif kind in ("walk", "step", "dig", "bridge", "open"):
            d["spot"] = (v[0] * CELL, v[0] * CELL + 15) if v[0] != u[0] else None
        elif kind == "fall":
            d["rows"] = e[2]
            d["spot"] = (v[0] * CELL, v[0] * CELL + 2 * CELL - BW)   # over the opening, both columns: it falls in
        elif kind == "drop":
            d["rows"] = e[1]
            d["spot"] = (v[0] * CELL, v[0] * CELL + 2 * CELL - BW)
        if kind in ("fall", "drop") and not (g.F(v[0] - ox, v[1] + 1 - oy) or g.F(v[0] + 1 - ox, v[1] + 1 - oy)):
            # a fall onto a rope's grab point: the body's centre must pass down the rope's column (rule «rope»), so
            # the spot is the opening's window cut to that column (a 5 px window at a cliff's edge, Oct 20 night)
            col = next((cc for cc in (v[0], v[0] + 1) if g.R(cc - ox, v[1] - 3 - oy)), None)
            if col is not None:
                lo, hi = max(d["spot"][0], col * CELL - 10), min(d["spot"][1], col * CELL + 5)
                if lo <= hi:
                    d["spot"] = (lo, hi)
        return d

    def words(self):
        if self.ticks is None:
            return "no way found to %d,%d" % self.cell
        kinds = {}
        for e in self.way:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return "%.1f s to %d,%d: %s%s" % (self.ticks / 60, self.cell[0], self.cell[1],
                                          ", ".join("%d %s" % (n, k) for k, n in kinds.items()),
                                          " (%d blocks)" % self.blocks if self.blocks else "")


def start_node(cells_of_body):
    x0, x1, top, feet = cells_of_body
    return (x0, feet)


def plan(cells, goal_cell, body, kit, phys, box=None, version=None, dry=False, keep=(), area=None, approach=False, avoid_submerged=False,
         search_area=None):
    """The flood for one goal from the map (or a window): a Plan. body: the state's body dict. With dry=True the
    goal is any standing place whose head is out of the water (the breathe goal's: rule «breath»), the nearest by
    ticks, digging and blocks included; goal_cell is then only where the box is centred. With area (c0, r0, c1, r1,
    go's: Nov 1) the goal is any open standing place in it with ground under it (any that digging makes, when none is
    open); goal_cell is then the place the edge of the seen map is priced toward. With search_area, the route ends
    beside an unknown cell in that patch; actual observations determine search completion."""
    t0 = time.monotonic()
    P = Prices(phys, kit)
    s = start_node(B.cells_of(B.start(body["x"], body["y"])))
    gx, gy = goal_cell
    if box is None:
        x0, x1 = min(s[0], gx) - MARGIN_X, max(s[0], gx) + MARGIN_X
        y0, y1 = min(s[1], gy) - MARGIN_Y, max(s[1], gy) + MARGIN_Y
        if search_area is not None:
            x0, x1 = min(x0, search_area[0] - 3), max(x1, search_area[2] + 3)
            y0, y1 = min(y0, search_area[1] - 3), max(y1, search_area[3] + 3)
        box = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)
    g = Grid(cells, *box, P, keep=keep)
    g.start = (s[0] - g.x0, s[1] - g.y0)
    if dry:
        # a head out of the water that stays out: no water at the head's row or anywhere above it in the head's
        # columns and the ones beside (water runs down and sideways into a dug cell; a cell dug above the water
        # line stays air: rule «breath»)
        top_water = []
        for c in range(g.w):
            rows_w = [r for r in range(g.h) if g.water[r][c]]
            top_water.append(min(rows_w) if rows_w else g.h)
        goals = [(c, r) for r in range(3, g.h) for c in range(1, g.w - 2)
                 if g.fits(c, r) and all(top_water[cc] > r - 2 for cc in (c - 1, c, c + 1, c + 2))]
    elif approach:
        goals = [(x - g.x0, y - g.y0)
                 for y in range(gy - 4, gy + 5) for x in range(gx - 5, gx + 5)
                 if g.fits(x - g.x0, y - g.y0) and g.support(x - g.x0, y - g.y0)
                 and Hd.in_reach(body if (x, y) == s else
                                 {"x": x * CELL, "y": (y + 1) * CELL - BH, "w": BW, "h": BH},
                                 (gx, gy), phys, kit.get("target_boost", 0))]
    elif area is not None:
        c0, r0, c1, r1 = area
        inside = [(c - g.x0, r - g.y0) for c in range(max(c0, g.x0 + 1), min(c1, g.x0 + g.w - 3) + 1)
                  for r in range(max(r0, g.y0 + 3), min(r1, g.y0 + g.h - 2) + 1)]
        goals = ([n for n in inside if g.free(*n) and g.support(*n)] or
                 [n for n in inside if g.fits(*n) and g.support(*n)])
    else:
        goals = [(c - g.x0, gy - g.y0) for c in (gx,) if g.fits(c - g.x0, gy - g.y0)]
    if search_area is not None:
        seeds, built_stops = frontier(g, P, gx, gy, search_area)
        dist, nxt = flood(g, P, [], start=g.start, seeds=seeds, built_stops=built_stops,
                          avoid_submerged=avoid_submerged)
    else:
        dist, nxt = flood(g, P, goals, start=g.start, avoid_submerged=avoid_submerged)
    explore = search_area is not None
    near = [(s[0] + dx - g.x0, s[1] + dy - g.y0) for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, 1), (-1, 1), (1, 1))]
    if not dry and not any(n in dist for n in near):
        # the goal is past what the character has seen, or walled off from it on the seen map: the way goes to the
        # edge of the seen map nearest the goal by its price, the unseen ground counted as open (frontier())
        edge, built_stops = frontier(g, P, gx, gy)
        if edge:
            dist, nxt = flood(g, P, goals, start=g.start, seeds=edge, avoid_submerged=avoid_submerged,
                              built_stops=built_stops)
            explore = True
    start = s if g.start in dist else None
    if start is None:
        # the body's columns may straddle three cells, or it hangs in the air: the standing place nearest its feet
        for cand in ((s[0] - 1, s[1]), (s[0] + 1, s[1]), (s[0], s[1] + 1), (s[0] - 1, s[1] + 1), (s[0] + 1, s[1] + 1)):
            if (cand[0] - g.x0, cand[1] - g.y0) in dist:
                start = cand
                break
    out = Plan(goal_cell, g, P, dist, nxt, start or s, version if version is not None else getattr(cells, "version", 0))
    out.dry = dry
    out.explore = explore                                  # the way ends at the edge of the seen map, not at the goal
    out.ms = round((time.monotonic() - t0) * 1000)
    out.nodes = len(dist)
    return out


AIR_TICKS_PER_POINT = 7


def head_underwater(world, body, put=(), dug=()):
    return world.water(int((body["x"] + BW / 2) // CELL), int((body["y"] + 4) // CELL), put, dug)


def can_breathe(world, body, put=(), dug=()):
    x, y = int((body["x"] + BW / 2) // CELL), int((body["y"] + 4) // CELL)
    return not world.solid(x, y, put, dug) and not world.water(x, y, put, dug)


def plan_to_air(world, body, kit, phys, keep=()):
    x, _, _, y = B.cells_of(body)
    box = (x - 40, y - 25, 80, 50)
    route = plan(world, (x, y), body, kit, phys, box=box, dry=True, keep=keep)
    # Each edge fits the kit on its own. An escape must fit the kit for the whole route.
    over_budget = [(kind, count) for kind, count in (("block", "blocks"), ("platform", "platforms"), ("rope", "ropes"))
                   if getattr(route, count) > kit.get(count, 0)]
    if over_budget:
        kit = dict(kit)
        for kind, count in over_budget:
            kit[kind], kit[count] = None, 0
        route = plan(world, (x, y), body, kit, phys, box=box, dry=True, keep=keep)
    return route


def air_needed(route, body):
    """Estimated underwater time on the route before the next breath."""
    wet = lambda node: route.grid.head_wet(node[0] - route.grid.x0, node[1] - route.grid.y0)
    here = route.node_of(body)
    wet_now = wet(here)
    longest = underwater = 0
    way = route.way_from(here)
    for edge in way:
        if wet(edge["from"]) or wet(edge["to"]):
            underwater += edge["ticks"]
            longest = max(longest, underwater)
        if not wet(edge["to"]):
            if wet_now:
                return underwater
            underwater = 0
    last = way[-1]["to"] if way else here
    return float("inf") if wet(last) and (route.explore or not way) else longest


def breathable_plan(world, target, body, kit, phys, *, area=None, approach=False, keep=(), air_route=None,
                    search_area=None):
    route = plan(world, target, body, kit, phys, area=area, approach=approach, keep=keep, search_area=search_area)
    if phys.get("gills"):
        return route
    underwater = head_underwater(world, body)
    available = body["breath"] * AIR_TICKS_PER_POINT
    if air_needed(route, body) < available and (route.ticks is not None or not underwater):
        return route
    if not underwater:
        dry_route = plan(world, target, body, kit, phys, area=area, approach=approach, keep=keep, avoid_submerged=True,
                         search_area=search_area)
        if dry_route.ticks is not None:
            return dry_route
    return air_route if air_route is not None else plan_to_air(world, body, kit, phys, keep)



def estimate(route, body, forecast=None, *, work=None):
    """Remaining route ticks, with forecast work measured against the same work used to generate the action."""
    if route is None:
        return None
    node = route.node_of(body, forecast["put"] if forecast else (), forecast["dug"] if forecast else ())
    ticks = route.at(*node)
    if ticks is None:
        return None
    edge = route.next_edge(node)
    if edge and edge["kind"] == "walk":
        x = body["x"]
        target = edge["to"][0]
        px = target * CELL - x if target > node[0] else x - (target + 1) * CELL
        ticks = route.at(*edge["to"]) + edge["ticks"] * max(0, min(CELL, px)) / CELL
    if forecast:
        if work is None:
            raise ValueError("a forecast needs the route work from its starting state")
        credit = 0
        for cell, cost in work["costs"]["dig"].items():
            hits = 100 if cell in forecast["dug"] else forecast["hits_on"].get(cell, 0)
            credit += cost * min(hits, 100) / 100
        placed = {c for c, tile in forecast["put"].items() if route.grid.floor_kind.get(tile, True)}
        placed.update(c for c, actual in forecast["swap"].items() if actual in placed)
        completed = {"put": placed, "platform": {c for c, tile in forecast["put"].items() if tile == 19},
                     "rope": forecast["ropes"], "open": forecast["dug"]}
        for kind, cells in completed.items():
            credit += sum(cost for cell, cost in work["costs"][kind].items() if cell in cells)
        if work["ticks"] is not None:
            # Position may already account for completed work. Deduct only the part not reflected there.
            credit = max(0, credit - max(0, work["ticks"] - ticks))
            ticks -= credit
    return max(0, ticks)
