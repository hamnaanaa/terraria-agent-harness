"""Gym setups and independent checks for the live brain."""
import gym_api as G

PICK = ("Copper Pickaxe", 1, 0)
BOW = ("Wooden Bow", 1, 0)
ARROWS = ("Wooden Arrow", 100, 54)


def cell(x, y):
    return G.SITE[0] + x, G.SITE[1] + y


def area(x, y, x1, y1):
    return (*cell(x, y), *cell(x1, y1))


def count(state, name):
    return sum(i["stack"] for i in state["bag"] if i["name"] == name)


def tile(state, at):
    cells = state["cells"]
    x, y = at
    c, r = x - cells["x0"], y - cells["y0"]
    return cells["tile"][r][c] if 0 <= c < cells["w"] and 0 <= r < cells["h"] else -2


def arrived(state, bounds):
    b = state["body"]
    x, y = int(b["x"] // 16), int((b["y"] + b["h"] - 1) // 16)
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3] and b["vy"] == 0


def reach(bounds):
    return {"kind": "reach", "area": bounds, "limit_ticks": 900}, lambda a, b: arrived(b, bounds)


def setup(episode):
    if G.state(w=2, h=2).get("world_name") != "Gym":
        raise RuntimeError("episodes require the Gym world")
    G.call("/run/cancel", {}, timeout=2)
    canvas = G.canvas(*(episode.get("canvas") or (G.W, G.H, G.FLOOR)))
    if episode.get("torches"):
        for x in range(3, len(canvas[0]) - 3, 6):
            canvas[59][x] = "t"
    for fixture in episode.get("terrain", ()):
        if fixture[0] == "tunnel":
            _, x, y, length = fixture
            for i in range(length):
                G.fill(canvas, x + i, y + i - 2, x + i + 2, y + i, ".")
        else:
            G.fill(canvas, *(fixture[1:] if fixture[0] == "fill" else fixture))
    G.stamp(canvas, reveal=episode.get("reveal", True))
    G.clean_body(episode.get("feet", (21, 59)), bag=episode.get("bag", ()), wear=episode.get("wear", ()), life_max=episode.get("life_max", 100))
    if episode.get("pretrash"):
        G.call("/trash", {"items": [episode["pretrash"]]})
    G.gym(background=True, **episode.get("dials", {}))


def reach_near(at, limit_ticks=1200):
    """The old travel targets allowed the body to overlap the target column."""
    x, y = at
    goal, verify = reach(area(x - 1, y, x + 1, y))
    goal["limit_ticks"] = limit_ticks
    return goal, verify


def open_chest(at, take=True):
    target = cell(*at)
    return ({"kind": "interact", "operation": "chest", "cell": target, "take": take, "limit_ticks": 1200},
            lambda a, b: (any(count(b, i["name"]) > count(a, i["name"]) for i in b["bag"]) if take else
                          b["body"]["chest"] >= 0 and a["body"]["chest"] != b["body"]["chest"]))
