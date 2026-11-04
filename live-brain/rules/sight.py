"""Can a melee hit get through the blocks to the enemy: the game's own check, ported from tModLoader.dll (Nov 1).

A sword's swing lands on an enemy its rectangle meets only when the enemy goes through blocks or Player.CanHit
says yes (Player.ProcessHitAgainstNPC); a shortsword's stab the same (Projectile.Damage: ownerHitCheck, then
Projectile.CanHitWithMeleeWeapon, the same four checks from the player's body, the distance under 1000 px).
Player.CanHit is four walks over the cells; any one that gets through lets the hit land:

    1. Collision.CanHit, from the body's centre cell to the enemy's, one cell a step along the longer way: stopped
       by a block in the cell it steps into, or by a step between two full blocks (a slope or half block lets it by)
    2. Collision.CanHitLine from the body's front (half its width the way it faces) a third of its height up, to the
       enemy's centre a third of its height up
    3. the same from the body's front to the enemy's centre
    4. from the body's front at mid height to the enemy's centre a third of its height down

A CanHitLine walk is a line of cells; it stops at a block in it or beside it, of any shape. A block is a tile the
game counts as solid and not a platform: dirt, stone, a closed door; a rope, a torch or a platform never stops it.

Classic, Oct 31: 172 stabs at a Red Slime through one block of dirt, its health 35 the whole 174 s; all 172 fail here.
"""
import math

OPEN, BLOCK, SHAPED = 0, 1, 2          # what cell(x, y) answers: no block, a full block, a slope or half block


def f32(x):
    """x rounded to the nearest float32 (the game's floats; CanHitLine's steps depend on them)."""
    if x == 0.0 or x != x or math.isinf(x):
        return x
    m, e = math.frexp(x)
    return math.ldexp(round(math.ldexp(m, 24)), e - 24)


def idiv(a, b):
    """C#'s integer division: toward zero."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def walk_hit(x1, y1, x2, y2, cell):
    """Collision.CanHit's walk between two cells."""
    while True:
        if x1 == x2 and y1 == y2:
            return True
        if abs(x1 - x2) > abs(y1 - y2):
            x1 += 1 if x1 < x2 else -1
            sides = cell(x1, y1 - 1), cell(x1, y1 + 1)
        else:
            y1 += 1 if y1 < y2 else -1
            sides = cell(x1 - 1, y1), cell(x1 + 1, y1)
        if sides == (BLOCK, BLOCK) or cell(x1, y1):
            return False


def walk_line(x, y, x2, y2, cell):
    """Collision.CanHitLine's walk between two cells, with the game's float32 step counters."""
    dx, dy = float(abs(x - x2)), float(abs(y - y2))
    if dx == 0.0 and dy == 0.0:
        return True
    rx = ry = 1.0
    if dx == 0.0 or dy == 0.0:
        rx, ry = (0.0 if dx == 0.0 else 1.0), (0.0 if dy == 0.0 else 1.0)
    elif dx > dy:
        rx = f32(dx / dy)
    else:
        ry = f32(dy / dx)
    ax = ay = 0.0
    mode = 2 if y < y2 else 1                       # 2 steps across, 1 steps up or down
    nx, ny = int(dx), int(dy)
    sx, sy = (x2 > x) - (x2 < x), (y2 > y) - (y2 < y)
    done = False
    while True:
        if mode == 2:
            ax = f32(ax + rx)
            k, ax = int(ax), math.fmod(ax, 1.0)
            for _ in range(k):
                if cell(x, y - 1) or cell(x, y) or cell(x, y + 1):
                    return False
                if nx == 0 and ny == 0:
                    done = True
                    break
                x += sx
                nx -= 1
                if nx == 0 and ny == 0 and k == 1:
                    done = True
            if ny != 0:
                mode = 1
        else:
            ay = f32(ay + ry)
            k, ay = int(ay), math.fmod(ay, 1.0)
            for _ in range(k):
                if cell(x - 1, y) or cell(x, y) or cell(x + 1, y):
                    return False
                if nx == 0 and ny == 0:
                    done = True
                    break
                y += sy
                ny -= 1
                if nx == 0 and ny == 0 and k == 1:
                    done = True
            if nx != 0:
                mode = 2
        if cell(x, y):
            return False
        if done:
            return True


def line(ax, ay, bx, by, cell):
    """CanHitLine between two points (px): each point's cell is int(p / 16) in float32."""
    return walk_line(int(f32(f32(ax) / 16)), int(f32(f32(ay) / 16)), int(f32(f32(bx) / 16)), int(f32(f32(by) / 16)), cell)


def can_hit(bx, by, bw, bh, facing, e, cell):
    """Player.CanHit from the body's box (top left bx, by) facing 1 right or -1 left, to the enemy e (x, y, w, h)."""
    ex, ey, ew, eh = e["x"], e["y"], e["w"], e["h"]
    if walk_hit(idiv(int(bx) + bw // 2, 16), idiv(int(by) + bh // 2, 16),
                idiv(int(ex) + ew // 2, 16), idiv(int(ey) + eh // 2, 16), cell):
        return True
    cx, cy = f32(f32(bx) + bw // 2), f32(f32(by) + bh // 2)
    ecx, ecy = f32(f32(ex) + ew // 2), f32(f32(ey) + eh // 2)
    fx, fy = f32(cx + idiv(facing * bw, 2)), f32(cy + f32(f32(-bh) / 3))
    return (line(fx, fy, ecx, f32(ecy + idiv(-eh, 3)), cell) or line(fx, fy, ecx, ecy, cell) or
            line(fx, cy, ecx, f32(ecy + eh // 3), cell))
