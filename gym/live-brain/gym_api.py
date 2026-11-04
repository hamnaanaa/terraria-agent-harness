"""The gym, from Python: the AgentIO mod's calls (mod/AgentIO/Server.cs) and the test site.

Tests are stamped at SITE, in the sky east of the gym world's spawn, on a canvas W wide and H high with
walls two tiles thick at the sides and a floor starting at FLOOR.
"""
import time

from mod.client import call, state

SITE = (2700, 150)
W, H, FLOOR = 100, 66, 60


def in_gym():
    """Allow Gym setup only in the disposable world named Gym."""
    name = state(w=2, h=2).get("world_name")
    if name != "Gym":
        raise RuntimeError("the game is in «%s», not the Gym: the bench's stamps and dials are not run there" % name)


def gym(**dials):
    """The gym's dials. A dead body is brought back first (the game respawns it on the next tick)."""
    in_gym()
    for _ in range(50):
        out = call("/gym", dials)
        if not out.get("dead"):
            return out
        time.sleep(0.1)
    raise RuntimeError("the body did not come back")


def run(steps, watch=(), timeout_s=None):
    body = {"steps": steps, "watch": [list(c) for c in watch]}
    if timeout_s:
        body["timeout_s"] = timeout_s
    return call("/run", body, timeout=(timeout_s or 60) + 10)


def canvas(w=W, h=H, floor=FLOOR):
    m = [["."] * w for _ in range(h)]
    fill(m, 0, 0, 1, h - 1, "#")
    fill(m, w - 2, 0, w - 1, h - 1, "#")
    fill(m, 0, floor, w - 1, h - 1, "#")
    return m


def fill(m, x1, y1, x2, y2, ch):
    for y in range(min(y1, y2), max(y1, y2) + 1):
        for x in range(min(x1, x2), max(x1, x2) + 1):
            m[y][x] = ch


def stamp(m, reveal=True):
    """The map, stamped at SITE over a box cleared a little wider (whatever the last test built or dug is gone).
    reveal=False leaves its cells unseen on the character's map, as a place never visited (rule «seen»)."""
    in_gym()
    x0, y0 = SITE
    return call("/stamp", {"x0": x0, "y0": y0, "rows": ["".join(r) for r in m], "reveal": reveal,
                           "clear": [x0 - 4, y0 - 6, x0 + len(m[0]) + 3, y0 + len(m) + 2]})


def clean_body(feet, bag=(), wear=(), life_max=100):
    """The body standing on a canvas cell (its left column and feet row), healed, no buffs, nothing worn but `wear`,
    exactly this bag, the clock at noon and no enemies; then settled (see settle)."""
    x, y = SITE[0] + feet[0], SITE[1] + feet[1]
    out = gym(wear=list(wear), life_max=life_max, bag=[list(b) for b in bag], feet=[x, y], heal=True,
              clear_buffs=True, freeze="noon", spawns=0, butcher=True)
    settle(feet)
    return out


def settle(feet=None):
    """Idle ticks until the body carries no buff and moves at its bare speed: a buff picked up where it stood before
    (the spawn's sunflowers give «Happy!», runs ×1.21, after every respawn) lasts until the game looks around again."""
    for _ in range(10):
        row = run([{"ticks": 20, "keys": []}])["trace"][-1]
        if not row["buffs"] and abs(row["move_speed"] - 1.0) < 1e-6:
            break
        gym(clear_buffs=True)
    if feet is not None:
        gym(feet=[SITE[0] + feet[0], SITE[1] + feet[1]], heal=True)
        run([{"ticks": 2, "keys": []}])
