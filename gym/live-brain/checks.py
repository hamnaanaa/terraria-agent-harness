"""Component and AgentIO contract checks for the isolated live brain."""
import copy
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import gym_api as gym
sys.path.insert(0, str(ROOT / "live-brain"))

import goals
from loop import Loop, Timing
from map import Map
import pathfinder
from rules import body, weapons
from rules.forecast import enemies_of, play

fast = importlib.import_module("fast-brain")


def window(x0=0, y0=0, w=30, h=12):
    blank = lambda value: [[value] * w for _ in range(h)]
    tiles = blank(-1)
    for row in tiles[9:]:
        row[:] = [0] * w
    return {"x0": x0, "y0": y0, "w": w, "h": h, "tile": tiles, "liquid": blank(0), "shape": blank(0),
            "liquid_kind": blank(0), "wall": blank(0), "seen": blank(True), "can_break": blank(True),
            "kinds": {"0": {"name": "Dirt", "solid": True, "solid_top": False, "pick_damage": 70}}}


def observation():
    return {"tick": 4, "session": {"loaded": 1}, "world_name": "Gym",
            "body": dict(body.start(32, 102), dead=False, life=100, life_max=100, h=42, w=20, defense=0),
            "physics": {}, "time": {"day": True}, "bag": [], "worn": [], "items": [], "enemies": [],
            "execution": {"active": None, "pending": None, "finished": None},
            "cells": window(), "counters": {"kills_by_name": {}, "damage_taken": 0, "damage_dealt": 0}}


def map_check():
    m = Map()
    box = window()
    box["seen"][2][3] = False
    box["shape"][9][4] = 5
    box["liquid"][8][3] = 91
    box["liquid_kind"][8][3] = 2
    original = copy.deepcopy(box)
    m.update(box)
    assert m.raw_tile(3, 2) == -2 and not m.empty(3, 2)
    assert m.shape(4, 9) == 5 and m.cells[(3, 8)][2:5] == (91, 2, 0)
    assert not m.update(box) and m.version == 1
    assert box == original
    m.update(window(x0=15))
    assert m.shape(4, 9) == 5
    assert m.tile(4, 9, dug={(4, 9)}) == -1 and m.raw_tile(4, 9) == 0


def exploration_route_check():
    state = json.loads((ROOT / "gym/episodes/quarry/observation.json").read_text())
    _, kit = fast.items_for(state, {})
    phys = dict(state["physics"], defense=state["body"]["defense"])
    box = window(w=30, h=24)
    for r in range(15, 20):
        for c in range(10, 14):
            box["seen"][r][c] = False
    world = Map(); world.update(box)
    start = dict(state["body"], x=32, y=102, vx=0, vy=0, ground=True, wet=False)
    area = (10, 15, 13, 19)
    route = pathfinder.plan(world, (10, 15), start, kit, phys, search_area=area)
    assert route.explore and route.ticks is not None and route.way
    end = route.way[-1]["to"]
    assert route.at(*end) == 0
    assert any(world.raw_tile(x, y) == -2 and goals.area_contains(area, x, y)
               for y in range(end[1] - 3, end[1] + 3) for x in range(end[0] - 1, end[0] + 3))
    assert any(e.get("dig") for e in route.way), route.way
    # A landing inside fully observed terrain cannot be an exploration destination.
    known = window(w=30, h=24)
    for r in range(9, 17):
        for c in range(10, 18):
            known["tile"][r][c] = -1
    world = Map(); world.update(known)
    prices = pathfinder.Prices(phys, kit)
    grid = pathfinder.Grid(world, 0, 0, 30, 24, prices)
    seeds, built = pathfinder.frontier(grid, prices, 15, 20)
    assert not seeds and not built


def platform_support_order_check():
    state = json.loads((ROOT / "gym/episodes/quarry/observation.json").read_text())
    _, kit = fast.items_for(state, {})
    kit.update(platform={"use_time": 10}, platforms=4)
    prices = pathfinder.Prices(state["physics"], kit)
    world = Map(); world.update(window())
    grid = pathfinder.Grid(world, 0, 0, 30, 12, prices)
    cells = grid.rungs(12, 5, prices)
    assert cells == [(12, 8), (12, 7), (12, 6), (12, 5)]
    put = {}
    for cell in cells:
        assert fast.hands.has_support(cell, world.wall, lambda x, y: world.holds(x, y, put)), cell
        put[cell] = 19


def exploration_no_reveal_check():
    from unittest.mock import patch
    state = observation()
    state["cells"]["seen"][8][4] = False
    state["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                            jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10, range_x=5, range_y=4)
    state["body"]["breath"] = 200
    class IO:
        def call(self, path, payload=None, timeout=None):
            if path.startswith("/state?"):
                state["tick"] += 16
                return copy.deepcopy(state)
            if path == "/run":
                state["execution"]["active"] = {"command_id": payload["command_id"],
                                                   "applied_tick": payload["start_tick"] + 1}
                return {"now": state["tick"]}
            return {}
    with patch.object(fast, "project", side_effect=lambda state, *_: state), \
            patch.object(fast, "decide", return_value={"steps": [{"ticks": 15, "keys": []}]}), \
            patch("loop.time.sleep"):
        result = Loop(IO(), draw=False, world_name="Gym").run(
            {"kind": "search", "area": (4, 8, 4, 8), "tile": "Heart", "limit_ticks": 120}, lambda _: None)
    assert result["status"] == "timeout" and result["ticks"] >= 120
    assert result["rounds"] >= 4  # Route arrival alone never completes a search.


def cell_mining_permission_check():
    box = window()
    box["can_break"][9][3] = False
    world = Map(); world.update(box)
    assert not world.can_break(3, 9) and world.can_break(4, 9)
    assert world.raw_tile(3, 9) == world.raw_tile(4, 9)
    reason = goals.blockers({"kind": "change", "cells": [[3, 9, -1]]}, observation(), world)
    assert reason[0]["source"] == "game_mining_permission"
    version = world.version
    box["can_break"][9][3] = True
    world.update(box)
    assert world.can_break(3, 9) and world.version == version + 1
    assert not world.can_break(-10, -10)


def quantity_shortfall_check():
    s = observation();world = Map();world.update(s["cells"])
    goal = {"kind": "quantity", "item": "Wood", "count": 20, "gain": False, "limit_ticks": 300}
    s["bag"] = [{"name": "Wood", "stack": 19}]
    s["items"] = [{"name": "Wood", "stack": 1}]
    assert goals.check(goal, s, world, 0, s["tick"]) is None
    s["items"] = []
    failure = goals.check(goal, s, world, 0, s["tick"])
    assert failure[0] == "failed" and "19/20" in failure[1]
    s["bag"][0]["stack"] = 20
    assert goals.check(goal, s, world, 0, s["tick"])[0] == "done"


def quantity_total_check():
    s = observation();world = Map();world.update(s["cells"])
    s["bag"] = [{"name": "Wood", "stack": 25}]
    s["items"] = [{"name": "Wood", "stack": 14}]
    goal = {"kind": "quantity", "item": "Wood", "count": 20, "limit_ticks": 300}
    baseline = goals.begin(goal, s)
    assert baseline == 0 and goals.check(goal, s, world, baseline, s["tick"])[0] == "done"
    goal["count"] = 39
    assert goals.check(goal, s, world, goals.begin(goal, s), s["tick"]) is None
    s["bag"][0]["stack"] = 39;s["items"] = []
    assert goals.check(goal, s, world, baseline, s["tick"])[0] == "done"
    assert goals.begin(dict(goal, gain=True), s) == 39


def excavation_route_check():
    phys = dict(run_top=3, run_acc=.08, run_slow=.2, acc_run=3, jump_hold=15,
                jump_speed=5.01, gravity=.4, fall_top=10, range_x=5, range_y=4)
    kit = {"pick": {"pick": 35, "use_time": 15, "tile_boost": -1}, "target_boost": -1}
    world = Map();world.update(window(h=20))
    start = body.start(32, 102)
    route = pathfinder.plan(world, (15, 13), start, kit, phys, approach=True)
    assert not route.explore and route.ticks is not None
    assert any(edge.get("dig") for edge in route.way)
    x, y = route.way[-1]["to"]
    assert fast.hands.in_reach({"x": x*16, "y": (y+1)*16-42, "w": 20, "h": 42}, (15, 13), phys, -1)
    # A cell-aligned endpoint can be in reach while the actual body is just outside it.
    offset = body.start(9*16 + .004, 102)
    route = pathfinder.plan(world, (5, 10), offset, kit, phys, approach=True)
    assert not fast.hands.in_reach(offset, (5, 10), phys, -1)
    assert route.at(*route.node_of(offset)) > 0
    box = window()
    box["tile"][9][8] = box["tile"][9][9] = -1
    box["tile"][6][8] = 0
    world = Map();world.update(box)
    price = pathfinder.Prices(phys, kit)
    grid = pathfinder.Grid(world, 0, 0, 30, 12, price)
    step = next(edge for u, cost, edge in pathfinder.preds(grid, price, (8, 9), {}) if u == (9, 8))
    assert step[0] == "dig" and (8, 6) in step[2]
    shaft = window(h=16)
    for row in (10, 11):
        shaft["tile"][row][8] = shaft["tile"][row][9] = -1
    world = Map();world.update(shaft)
    grid = pathfinder.Grid(world, 0, 0, 30, 16, price)
    fall = next(edge for u, cost, edge in pathfinder.preds(grid, price, (8, 11), {}) if u == (8, 8))
    assert fall[0] == "dig_down" and set(fall[1]) == {(8, 9), (9, 9)}


def goal_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    goal = {"kind": "change", "cells": [[3, 8, 0]], "limit_ticks": 60}
    assert goals.measure(goal, s, m, 0)["remaining"] == 1
    forecast = {"body": s["body"], "put": {(3, 8): 1}, "dug": set(), "ropes": set()}
    assert goals.measure(goal, s, m, 0, forecast=forecast)["remaining"] == 1
    forecast["put"][(3, 8)] = 0
    assert goals.measure(goal, s, m, 0, forecast=forecast)["remaining"] == 0
    assert goals.check(goal, s, m, 0, s["tick"]) is None
    kill = {"kind": "defeat", "enemy": "Zombie", "limit_ticks": 60}
    assert goals.check(kill, s, m, 0, s["tick"]) is None
    s["counters"]["kills_by_name"]["Zombie"] = 1
    assert goals.check(kill, s, m, 0, s["tick"])[0] == "done"
    s["body"]["dead"] = True
    assert goals.check(kill, s, m, 0, s["tick"])[0] == "died"


def failure_explanation_check():
    from unittest.mock import patch
    state = observation()
    state["physics"].update(range_x=5, range_y=4)
    state["bag"] = [{"name": "Dirt Block", "stack": 10, "place_tile": 0, "block_tile": True, "tile_boost": 0}]
    goal = {"kind": "change", "cells": [[6, 6, 0]], "limit_ticks": 60}
    world = Map(); world.update(state["cells"])
    missing_support = goals.blockers(goal, state, world)
    assert "nothing holds it" in missing_support[0]["reason"]
    assert goals.check(goal, state, world, state["tick"], state["tick"]) is None
    class IO:
        def call(self, path, payload=None, timeout=None):
            return copy.deepcopy(state) if path.startswith("/state") else {}
    with patch.object(goals, "check", return_value=("timeout", "game tick budget reached")):
        result = Loop(IO(), draw=False).run(goal, lambda _: None)
    assert result["status"] == "timeout" and result["blockers"] == missing_support
    # Unknown terrain cannot establish a missing anchor.
    state["cells"]["seen"][6][7] = False
    unknown = Map(); unknown.update(state["cells"])
    assert goals.blockers(goal, state, unknown) == []
    # Cuttable plants are cleared by placement, rather than treated as a solid obstruction.
    state["cells"]["seen"][6][7] = True
    state["cells"]["tile"][6][6] = 3
    state["cells"]["tile"][7][6] = 0
    state["cells"]["kinds"]["3"] = {"name": "Plants", "solid": False, "solid_top": False, "cut": True}
    planted = Map(); planted.update(state["cells"])
    assert goals.blockers(goal, state, planted) == []
    state["cells"]["kinds"]["10"] = {"name": "ClosedDoor", "solid": True, "solid_top": False}
    for row in range(5, 8):
        state["cells"]["tile"][row][9] = 10
    state["cells"]["tile"][7][8] = state["cells"]["tile"][7][10] = 0
    door = {"kind": "interact", "operation": "door", "tile": "OpenDoor", "cell": [9, 6]}
    blocked = Map(); blocked.update(state["cells"])
    facts = goals.blockers(door, state, blocked)
    assert facts[0]["blocking_cells"] == [[8, 7, "Dirt"], [10, 7, "Dirt"]]
    state["cells"]["tile"][7][10] = -1
    one_side = Map(); one_side.update(state["cells"])
    assert goals.blockers(door, state, one_side) == []


def route_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    phys = {"run_top": 3.0, "run_acc": 0.08, "run_slow": 0.2, "acc_run": 3.0,
            "jump_hold": 15, "jump_speed": 5.01, "gravity": 0.4, "fall_top": 10}
    plan = pathfinder.plan(m, (18, 8), s["body"], {}, phys)
    assert plan.way[-1]["to"] == (18, 8) and not plan.explore
    assert pathfinder.estimate(plan, s["body"]) > pathfinder.estimate(plan, dict(s["body"], x=128))
    unseen = pathfinder.plan(m, (37, 8), s["body"], {}, phys)
    assert unseen.ticks is None or unseen.explore


def quarry_route_check():
    folder = ROOT / "gym" / "episodes" / "quarry"
    state = json.loads((folder / "observation.json").read_text())
    goal = json.loads((folder / "starts.json").read_text())["escape"]["goal"]
    world = Map()
    world.update(state["cells"])
    _, kit = fast.items_for(state, goal)
    phys = dict(state["physics"], defense=state["body"]["defense"])
    area = goal["area"]
    target = ((area[0] + area[2]) // 2, (area[1] + area[3]) // 2)
    route = pathfinder.plan(world, target, state["body"], kit, phys, area=area)
    assert route.ticks is not None and not route.explore, route.words()
    assert route.way and goals.area_contains(area, *route.way[-1]["to"])
    assert any(e.get("dig") for e in route.way) and any(e.get("put") for e in route.way)


def route_progress_check():
    state = json.loads((ROOT / "gym/episodes/quarry/observation.json").read_text())
    world = Map()
    world.update(state["cells"])
    _, kit = fast.items_for(state, {})
    phys = dict(state["physics"], defense=state["body"]["defense"])
    area = [2157, 201, 2164, 207]
    route = pathfinder.plan(world, (2160, 204), state["body"], kit, phys, area=area)
    work = route.work_at(route.node_of(state["body"]))
    empty = {"put": {}, "dug": set(), "hits_on": {}, "ropes": set(), "swap": {}}
    before = pathfinder.estimate(route, state["body"])
    assert pathfinder.estimate(route, state["body"], empty, work=work) == before

    # One support block is useful even before an entire climb segment is complete.
    first = work["put"][0]
    raised = dict(state["body"], y=state["body"]["y"] - 16)
    partial = dict(empty, put={first: 0})
    assert pathfinder.estimate(route, raised, partial, work=work) < before
    unrelated = dict(empty, put={(first[0] - 100, first[1]): 0})
    assert pathfinder.estimate(route, state["body"], unrelated, work=work) == before

    # Reaching the next planned position already includes its completed building work.
    edge = route.next_edge(route.node_of(state["body"]))
    assert edge["kind"] == "pillar"
    end = dict(state["body"], x=edge["to"][0] * 16, y=(edge["to"][1] + 1) * 16 - 42)
    complete = dict(empty, put=dict.fromkeys(edge["put"], 0), dug=set(edge.get("dig", ())))
    assert abs(pathfinder.estimate(route, end, complete, work=work) - route.at(*edge["to"])) < 1e-6

    # A partly mined wall counts; movement past the finished wall must not count it twice.
    wall = window(w=30, h=15)
    for r in range(9):
        wall["tile"][r][8] = 0
    world = Map()
    world.update(wall)
    start = dict(state["body"], x=80, y=102)
    route = pathfinder.plan(world, (15, 8), start, {"pick": kit["pick"]}, phys)
    work = route.work_at(route.node_of(start))
    cell = work["dig"][0]
    before = pathfinder.estimate(route, start)
    assert pathfinder.estimate(route, start, dict(empty, hits_on={cell: 70}), work=work) < before
    through = dict(start, x=144)
    complete = dict(empty, dug=set(work["dig"]))
    assert abs(pathfinder.estimate(route, through, complete, work=work) - pathfinder.estimate(route, through)) < 1e-6


def pillar_reach_check():
    state = json.loads((ROOT / "gym/episodes/quarry/observation.json").read_text())
    _, kit = fast.items_for(state, {})
    block = kit["block"]
    for extra_reach in (0, 10):
        world = Map()
        world.update(window(w=30, h=15))
        phys = dict(state["physics"], range_y=state["physics"]["range_y"] + extra_reach)
        b = dict(state["body"], x=80, y=102, vx=0, vy=0, ground=True, jump=0,
                 released=True, jump_down=False, item_animation=0, item_time=0)
        sample = dict(state, body=b, enemies=[], shots=[])
        cells = tuple((5, 8 - n) for n in range(4))
        result = play(sample, world, [], ("jump",), ("put", block, None, cells), phys,
                      state["strength"], {"under": set(cells)}, 0, 60, 16, lambda *_: False)
        prices = pathfinder.pillar_ticks(phys, block["use_time"])
        in_one_forecast = max(n for n, (ticks, _) in prices.items() if ticks <= 60)
        assert in_one_forecast == len(result["put"]), (extra_reach, prices, result["puts_at"])
        if not extra_reach:
            assert prices[4][0] > 60  # The remaining blocks need another jump.


def forecast_floor_check():
    world = Map()
    world.update(window())
    source = json.loads((ROOT / "gym/episodes/quarry/observation.json").read_text())
    b = dict(source["body"], x=80, y=102, ground=False)
    route = pathfinder.plan(world, (15, 8), b, {}, source["physics"])
    removed = {(5, 9), (6, 9)}
    assert route.node_of(b) == (5, 8)
    assert route.node_of(b, dug=removed) == (5, 9)
    assert route.node_of(b, put={(5, 9): 4}, dug=removed) == (5, 9)  # A torch cannot hold the feet.
    assert route.node_of(b, put={(5, 9): 19}, dug=removed) == (5, 8)


def air_budget_check():
    from types import SimpleNamespace

    grid = SimpleNamespace(x0=0, y0=0, head_wet=lambda x, y: 1 <= x <= 3)
    edges = [{"from": (x, 8), "to": (x + 1, 8), "ticks": 12} for x in range(4)]
    route = SimpleNamespace(grid=grid, node_of=lambda b: (b["column"], 8), explore=False,
                            way_from=lambda node: edges[node[0]:])
    assert pathfinder.air_needed(route, {"column": 0}) == 48
    assert pathfinder.air_needed(route, {"column": 2}) == 24
    edges.pop()
    route.explore = True
    assert pathfinder.air_needed(route, {"column": 2}) == float("inf")


def candidate_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    pumpkin = {"slot": 3, "id": 1725, "name": "Pumpkin", "stack": 8, "place_tile": 251,
               "block_tile": True, "pick": 0, "axe": 0, "heal": 0, "hook": False,
               "value": 0, "use_time": 15, "tile_boost": 0}
    wood = dict(pumpkin, slot=7, id=9, name="Wood", place_tile=30)
    goal = {"kind": "reach", "cell": (14, 8), "preserve": {"Wood": 1}}
    s["bag"] = [wood, pumpkin]
    bag, kit = fast.items_for(s, goal)
    assert kit["block"]["name"] == "Pumpkin"
    pumpkin["slot"], wood["slot"] = wood["slot"], pumpkin["slot"]
    assert fast.items_for(s, goal)[1]["block"]["name"] == "Pumpkin"
    moves = list(fast.candidates(s, m, goal, None, {"put": [(4, 8)]}, bag))
    assert {use[1]["name"] for _, use in moves if use} == {"Pumpkin"}
    assert fast.items_for(dict(s, bag=[wood]), goal)[1]["block"]["name"] == "Wood"


def material_choice_check():
    s, world = observation(), Map();world.update(s["cells"])
    dirt = dict(slot=3, id=2, name="Dirt Block", stack=22, place_tile=0, block_tile=True,
                value=0, use_time=10, tile_boost=0, pick=0, axe=0, heal=0, hook=False)
    ore = dict(dirt, slot=9, id=12, name="Copper Ore", stack=6, place_tile=7, value=250, use_time=15)
    goal = {"kind": "reach", "cell": [14, 8]}
    take = lambda bag: fast.materials(goal, bag, lambda i: i["block_tile"])
    assert take([ore, dirt]) == [dirt]
    assert fast.items_for(dict(s, bag=[ore, dirt]), goal)[1]["block"] == dirt
    # A sole unfamiliar material remains usable; extra stock, reach or speed keeps an alternative.
    assert take([ore]) == [ore]
    for extra in ({"stack": 23}, {"tile_boost": 1}, {"use_time": 5}):
        assert len(take([dirt, dict(ore, **extra)])) == 2
    unknown = dict(ore);unknown.pop("value")
    assert unknown in take([dirt, unknown])
    # Material prices must not change selection of movement equipment.
    hook = dict(dirt, hook=True, block_tile=False, place_tile=-1, value=1000, stack=1)
    other_hook = dict(hook, id=3, value=0)
    assert fast.items_for(dict(s, bag=[hook, other_hook]), goal)[1]["hook"] == hook
    # Named construction goals keep their requested tile, regardless of its price.
    s["bag"] = [dirt, ore]
    goal = {"kind": "change", "cells": [[4, 8, 7]]}
    uses = list(fast.tile_uses(goal["cells"], world, s["bag"]))
    assert len(uses) == 1 and uses[0][1] == ore


def handoff_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(ground=True, vx=0, vy=0, wet=False, jump=0, defense_eff=0.5,
                     immune=False, immune_time=0, item_animation=0, breath=200)
    s.update(shots=[], strength=1)
    s["physics"].update(run_top=3, run_acc=0.08, run_slow=0.2, acc_run=3,
                        jump_hold=15, jump_speed=5.01, gravity=0.4, fall_top=10)
    original = copy.deepcopy(s)
    steps = fast.script((("right", "jump"), 20, 55, 16), None, {"ticks": 55})
    ticks = fast.pressed_ticks(steps)
    assert len(ticks) == 55 and "jump" not in ticks[19] and "jump" in ticks[20]
    assert "jump" not in ticks[36] and "right" in ticks[-1]
    active = [{"keys": ["right"], "ticks": 55}]
    later = fast.project(s, m, 15, active, s["tick"])
    assert later["body"]["x"] > s["body"]["x"]
    neutral = fast.project(s, m, 15, active, s["tick"] - 54)
    assert neutral["body"]["x"] == s["body"]["x"]
    assert s == original


def projectile_handoff_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(ground=True, defense_eff=0.5, immune=False, immune_time=0,
                     item_animation=0, breath=200)
    s["physics"].update(run_top=3, run_acc=0.08, run_slow=0.2, acc_run=3,
                        jump_hold=15, jump_speed=5.01, gravity=0.4, fall_top=10)
    sand_ball = dict(id=31, ai0=2, tile_collide=True, x=120, y=115, vx=-10, vy=-0.2,
                     w=10, h=10, damage=10, hostile=True, time_left=100, penetrate=-1, max_penetrate=-1)
    s.update(shots=[sand_ball], strength=1.5)

    handoff = fast.project(s, m, 5, [], s["tick"] + 1)
    after = play(handoff, m, [], (), None, s["physics"], s["strength"],
                 {}, 0, 0, 0, lambda *_: False, horizon=2)
    assert after["damage"] == 30
    assert sand_ball["x"] == 120 and sand_ball["time_left"] == 100

    s["cells"]["tile"][7][4] = 0
    m.update(s["cells"])
    covered = fast.project(s, m, 5, [], s["tick"] + 1)
    assert covered["shots"] == [] and covered["body"]["life"] == 100


def attack_shape_check():
    from rules import projectiles as p

    s = observation()
    shot = dict(i=2, id=99999, x=300, y=0, w=8, h=8, hostile=True)
    s["shots"] = [shot]
    s["attack_shapes"] = dict(origin=[0, 0], step=16, cols=30, rows=12, target_size=[20, 42],
                              shapes=[dict(i=2, id=99999, x=300, y=0, damage_checked=False,
                                           runs=[[6, 2, 1]])])
    original = copy.deepcopy(s)
    indexed = p.observe_shapes(s)["shots"][0]
    assert not p.hits_player(shot, s["body"])
    assert p.hits_player(indexed, s["body"])  # Warning geometry reaches far beyond the origin box.
    assert not p.hits_player(indexed, dict(s["body"], x=64))  # A gap stays usable.
    assert p.hits_player(dict(indexed, x=332), dict(s["body"], x=64))
    assert not p.hits_player(dict(indexed, x=332), s["body"])
    assert p.hits_player(indexed, dict(s["body"], x=-20)) is None
    assert p.hits_player(indexed, dict(s["body"], x=29 * 16)) is None
    assert s == original


def attack_shape_handoff_check():
    from rules import projectiles as p

    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(ground=True, immune=False, immune_time=0, item_animation=0, breath=200,
                      defense_eff=.5, no_knockback=True)
    s["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                         jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    s.update(strength=1, shots=[dict(i=2, id=99999, x=300, y=0, w=8, h=8, vx=1, vy=0,
                                    damage=10, hostile=True, tile_collide=False, time_left=100)])
    s["attack_shapes"] = dict(origin=[0, 0], step=16, cols=30, rows=12, target_size=[20, 42],
                              shapes=[dict(i=2, id=99999, x=300, y=0, damage_checked=False,
                                           runs=[[6, 1, 2]])])
    s = p.observe_shapes(s)
    whole = fast.project(s, m, 10, [], s["tick"] + 1)
    first = fast.project(s, m, 5, [], s["tick"] + 1)
    split = fast.project(dict(first, tick=s["tick"] + 5), m, 5, [], s["tick"] + 6)
    assert whole["body"] == split["body"] and whole["body"]["life"] == 100
    assert not whole["body"]["immune"] and whole["body"]["immune_time"] == 0
    assert whole["shots"][0]["x"] == split["shots"][0]["x"] == 310
    # Handoff projects controls and observed timers, never a possible hit as
    # health loss or protection. Every following candidate still sees danger.
    brief = fast.project(s, m, 1, [], s["tick"] + 1)
    assert brief["body"]["life"] == whole["body"]["life"]
    stationary = dict(s, shots=[dict(s["shots"][0], vx=0)], body=dict(s["body"], no_knockback=False))
    def roll(state, keys=(), ticks=60):
        return play(state, m, [], keys, None, s["physics"], 1, {}, 0, ticks, 0,
                    lambda *_: False, horizon=ticks)
    stay = roll(stationary)
    leave = roll(stationary, ("right",))
    assert leave["damage"] < stay["damage"] and stay["damage"] > 0
    assert roll(brief, ticks=1)["damage"] == 20
    assert "area_cooldown" not in brief["body"]
    low = fast.project(dict(s, body=dict(s["body"], life=1)), m, 15, [], s["tick"] + 1)
    assert low["body"]["life"] == 1 and roll(low, ("right",))["ticks"] > 1
    double = fast.project(dict(s, shots=s["shots"] * 2), m, 10, [], s["tick"] + 1)
    assert double["body"]["life"] == whole["body"]["life"]
    invulnerable = fast.project(dict(s, body=dict(s["body"], immune=True, immune_time=8)),
                               m, 10, [], s["tick"] + 1)
    assert invulnerable["body"]["life"] == 100 and not invulnerable["body"]["immune"]
    assert (whole["body"]["x"], whole["body"]["y"]) == (s["body"]["x"], s["body"]["y"])
    stronger = dict(s["shots"][0], id=99998, damage=20)
    mixed = fast.project(dict(s, shots=[*s["shots"], stronger]), m, 10, [], s["tick"] + 1)
    assert roll(dict(s, shots=[*s["shots"], stronger]), ticks=10)["damage"] == 40
    outside = dict(s, shots=[dict(s["shots"][0], x=1300)])
    unknown = play(outside, m, [], (), None, s["physics"], 1, {}, 0, 0, 0,
                   lambda *_: False, horizon=10)
    assert unknown["damage"] == 0 and unknown["body"]["life"] == 100
    assert any(e["kind"] == "attack_area_unknown" and e["uncovered_ticks"] == 10
               for e in unknown["hit_events"])


def firing_duration_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(ground=True, defense_eff=.5, immune=False, immune_time=0,
                     item_animation=0, item_time=0, breath=200, mana=0)
    s["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                        jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    gun = dict(id=9999, name="Test gun", slot=0, stack=1, damage=30, shoot=14,
               shoot_speed=20, use_time=3, use_anim=3, knockback=0, use_ammo=0,
               mana_cost=0, consumable=False, no_melee=True, melee=False, scale=1)
    gun["shot"] = dict(id=14, w=4, h=4, speed=20, extra_updates=0, damage=30,
                        knockback=0, time_left=300, tile_collide=True, penetrate=1)
    foe = dict(i=2, id=9998, name="Target", x=300, y=102, vx=0, vy=0, w=20, h=42,
               damage=0, defense=0, life=10000, kb_taken=0, flies=True, through_blocks=True)
    s.update(bag=[gun], shots=[], enemies=[foe], strength=1)
    use = ("shoot", gun, None, 2)
    for hold in (8, 15, 60):
        move = (("left",), 0, hold, 0)
        r = play(s, m, enemies_of(s), move[0], use, s["physics"], 1, {}, 0, hold, 0,
                 lambda *_: False, horizon=60)
        keys = fast.pressed_ticks(fast.script(move, use, r))
        assert r["fired"] == 20 and all("use" in k for k in keys)
        assert all(("left" in k) == (t < hold) for t, k in enumerate(keys))


def protection_counter_check():
    from rules import projectiles as p
    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(ground=True, immune=True, immune_time=50, hurt_cooldowns=[0, 19, 0, 0, 0],
                     item_animation=0, breath=200, defense_eff=.5)
    s["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                         jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    s.update(strength=1, shots=[dict(i=2, id=99999, x=300, y=0, w=8, h=8, vx=0, vy=0,
                                    hurt_slot=1, damage=10, hostile=True, time_left=100)])
    s["attack_shapes"] = dict(origin=[0, 0], step=16, cols=30, rows=12, target_size=[20, 42],
                              shapes=[dict(i=2, id=99999, x=300, y=0, runs=[[6, 1, 2]])])
    projected = fast.project(p.observe_shapes(s), m, 15, [], s["tick"]+1)
    assert projected["body"]["life"] == 100
    assert projected["body"]["hurt_cooldowns"] == [0, 4, 0, 0, 0]
    assert projected["body"]["immune_time"] == 35
    def roll(ticks):
        return play(projected, m, [], (), None, s["physics"], 1, {}, 0, 0, 0,
                    lambda *_: False, horizon=ticks)
    assert roll(4)["damage"] == 0
    assert roll(5)["damage"] == 20  # General protection cannot block a slot-1 hit.


def clearance_reuse_check():
    from rules import projectiles as p
    from unittest.mock import patch
    s = observation()
    s["shots"] = [dict(i=1, id=9, x=300, y=0, w=8, h=8)]
    s["attack_shapes"] = dict(origin=[0, 0], step=16, cols=30, rows=12, target_size=[20, 42],
                              shapes=[dict(i=1, id=9, x=300, y=0, runs=[[6, 1, 2]])])
    shot = p.observe_shapes(s)["shots"][0]
    cache = {}
    for x in (-20, 0, 32, 64, 96, 29*16):
        b = dict(s["body"], x=x)
        expected = p.hits_player(shot, b), p.clearance(shot, b)
        with patch.object(p, "_shape_hit", wraps=p._shape_hit) as hit:
            assert p.clearance(shot, b, cache) == expected[1]
            n = hit.call_count
            assert p.clearance(shot, b, cache) == expected[1]
            assert hit.call_count == n
    # The indexed four-sample coverage preserves boundary/unknown behavior.
    for row in range(-1, 13):
        for col in range(-1, 31):
            expected = (5 <= row <= 6 and 0 <= col <= 2) if 0 <= row < 11 and 0 <= col < 29 else None
            assert p._shape_hit(shot["_attack"], row, col) == expected


def attack_patch_check():
    from rules import projectiles as p
    s = observation()
    s["shots"] = [dict(i=1, id=9, x=500, y=0, w=8, h=8)]
    s["attack_shapes"] = dict(origin=[0, 0], step=16, cols=10, rows=12, target_size=[20, 42],
                              shapes=[dict(i=1, id=9, x=500, y=0, runs=[], patches=[
                                  dict(origin=[448, 64], cols=10, rows=8, runs=[[2, 3, 2]])])])
    shot = p.observe_shapes(s)["shots"][0]
    # Collision outside the player window is retained and follows the shot.
    b = dict(s["body"], x=480, y=96)
    assert p.hits_player(shot, b) is True
    moved = dict(shot, x=20)
    target = dict(b, x=0)
    assert p.hits_player(moved, target) is True and p.clearance(moved, target) == 0
    # Empty measured space differs from the unmeasured gap between patches.
    assert p.hits_player(shot, dict(b, x=0)) is False
    assert p.hits_player(shot, dict(b, x=300)) is None
    assert p.clearance(shot, dict(b, x=300)) is None
    assert p.clearance(shot, dict(b, x=0)) > 0
    # Either patch may report a hit; their overlap must not erase it.
    overlapping = copy.deepcopy(s)
    overlapping["attack_shapes"]["shapes"][0]["patches"].append(
        dict(origin=[0, 0], cols=10, rows=12, runs=[[6, 2, 1]]))
    assert p.hits_player(p.observe_shapes(overlapping)["shots"][0], s["body"]) is True


def spacing_path_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(ground=True, immune=True, immune_time=60, item_animation=0, breath=200,
                     defense_eff=.5)
    s["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                         jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    s.update(strength=1, shots=[dict(id=999, x=55, y=102, w=8, h=42, vx=0, vy=0,
                                    damage=10, hostile=True, time_left=20)])
    def roll(keys):
        return play(s, m, [], keys, None, s["physics"], 1, {}, 0, 60, 0,
                    lambda *_: False, horizon=60, spacing_ticks=15)
    toward, away = roll(("right",)), roll(("left",))
    assert not toward["projectiles"] and toward["pressure"] > away["pressure"] > 0
    assert toward["damage"] == away["damage"] == 0


def enemy_observation_check():
    s = observation()
    s["enemies"] = [dict(i=1, id=3, name="observed enemy", x=1500, y=102, w=20, h=40,
                         vx=-4, vy=0, life=100, damage=20, defense=0, kb_taken=0)]
    assert enemies_of(s)[0]["x"] == 1500


def knockback_check():
    immune = {"vx": -1.0, "kb_taken": 0.0}
    weapons.apply_knockback(immune, 4, 1, 20)
    assert immune["vx"] == -1.0
    enemy = {"vx": -3.72, "vy": 0, "kb_taken": .285, "life": 600, "life_max": 600}
    weak = dict(enemy)
    weapons.apply_knockback(weak, 6, 1, 50)
    assert abs(weak["vx"] - .48735) < 1e-6 and abs(weak["vy"] + .3655125) < 1e-6
    strong = dict(enemy)
    weapons.apply_knockback(strong, 6, 1, 70)
    assert abs(strong["vx"] + .3) < 1e-6 and abs(strong["vy"] + 1.2825) < 1e-6


def friendly_projectile_check():
    from rules import projectiles

    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(ground=True, defense_eff=.5, immune=False, immune_time=0,
                     item_animation=3, item_time=3, held_item=533, selected=0, breath=200)
    s["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                        jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    prototype = dict(id=89, w=4, h=4, ai=1, ai0=0, ai1=0, scale=1.2, speed=15,
                     tile_collide=True, extra_updates=1, time_left=600,
                     penetrate=1, damage=36, knockback=2, ammo_slot=54)
    gun = dict(id=533, slot=0, stack=1, damage=25, use_ammo=97, use_style=5,
               use_time=7, use_anim=7, shoot_speed=10, combat={"shot": prototype})
    ammo = dict(id=515, slot=54, stack=100, ammo=97, shoot_speed=5, damage=9, knockback=1)
    s.update(shots=[], strength=1, bag=[gun, ammo])
    steps = [{"keys": ["use"], "ticks": 30, "slot": 0, "aim": [400, 123]}]
    original = copy.deepcopy(s)
    part = fast.project(s, m, 5, steps, s["tick"] + 1)
    assert len(part["shots"]) == 1 and part["shots"][0]["id"] == 89
    assert part["shots"][0]["x"] == 100 and part["shots"][0]["y"] == 121
    assert part["body"]["item_time"] == 5
    split = fast.project(dict(part, tick=s["tick"] + 5), m, 5, steps, s["tick"] + 1)
    whole = fast.project(s, m, 10, steps, s["tick"] + 1)
    assert split["shots"] == whole["shots"] and split["body"] == whole["body"]
    assert s == original

    # A bullet already in flight can hit and push an enemy with no new attack.
    s["shots"] = [dict(prototype, x=80, y=110, vx=15, vy=0, friendly=True, hostile=False)]
    s["enemies"] = [dict(i=2, id=82, name="Wraith", x=112, y=102, vx=-1, vy=0,
                         w=26, h=42, life=200, damage=10, defense=0, kb_taken=.5,
                         through_blocks=True, flies=True, timers=[0, 0, 0, 0])]
    hit = fast.project(s, m, 2, [], s["tick"] + 1)
    assert hit["enemies"][0]["life"] == 164 and hit["enemies"][0]["vx"] > 0
    assert not hit["shots"]
    s["cells"]["tile"][6][6] = s["cells"]["tile"][7][6] = 0
    m.update(s["cells"])
    blocked = fast.project(s, m, 2, [], s["tick"] + 1)
    assert blocked["enemies"][0]["life"] == 200 and not blocked["shots"]
    arrow = dict(prototype, id=1, x=0, y=0, vx=10, vy=0)
    bullet = dict(arrow, id=89)
    for _ in range(20):
        projectiles.advance(arrow, lambda *_: False, None)
        projectiles.advance(bullet, lambda *_: False, None)
    assert arrow["vy"] > 0 and bullet["vy"] == 0


def combat_forecast_check():
    # The Nov 4 Gym opening: real rectangles, Adamantite armor timing, incoming Unicorn.
    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(immune=False, immune_time=0, item_animation=0, breath=200, life=400, life_max=400,
                     defense=50, defense_eff=.5)
    s.update(shots=[], strength=1.5, mode="journey")
    s["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                        jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    sword = dict(id=482, slot=0, damage=61, knockback=6, use_time=21, use_anim=20,
                 combat=dict(damage=74, knockback=6, use_time=17, use_anim=16, scale=1))
    s["bag"] = [sword]
    foe = dict(i=2, id=86, name="Unicorn", x=104.63, y=102, vx=-3.37, vy=0, w=46, h=42,
               life=600, life_max=600, damage=97, defense=30, kb_taken=.285, dir=-1, timers=[0, 0, 0, 0])
    s["enemies"] = [foe]
    phys = dict(s["physics"], defense=50, defense_eff=.5)
    result = play(s, m, enemies_of(s), (), ("swing", sword, None, 2), phys, 1.5,
                  {}, 0, 48, 16, lambda *_: False, horizon=48)
    outgoing = [h for h in result["hit_events"] if h["kind"] == "swing"]
    assert outgoing[0]["tick"] == 7 and outgoing[0]["damage"] == 59 and result["damage"] == 0
    cycle = play(s, m, enemies_of(s), (), ("swing", sword, None, 2), phys, 1.5,
                 {}, 0, 48, 16, lambda *_: False, horizon=33)
    assert cycle["body"]["item_animation"] == 16  # Recorded starts were ticks 1, 17, and 33.
    sw = weapons.swing_of(sword)
    assert weapons.swing_box(sw, 10, 1, 16, 1.2) == [16, -60, 72, 72]
    assert weapons.hit_damage(20, 15) == 12
    s["body"].update(held_item=482, item_animation=10, item_animation_max=16)
    foe.update(x=62, vx=0, melee_immune=10)
    active = play(s, m, enemies_of(s), (), None, phys, 1.5,
                  {}, 0, 5, 0, lambda *_: False, horizon=5)
    assert not active["hits"]  # This running swing already hit this enemy in the game.


def movement_reuse_check():
    from unittest.mock import patch

    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(immune=False, immune_time=0, item_animation=0, breath=200,
                     life=400, life_max=400, defense_eff=.5)
    s.update(shots=[], strength=1)
    phys = dict(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10, range_x=5, range_y=4)
    movement = {}
    def forecast(use=None, foes=(), *, cached=True):
        return play(s, m, foes, ("right",), use, phys, 1, {}, 0, 30, 0,
                    lambda *_: False, horizon=30, movement=movement if cached else None)

    plain = forecast(cached=False)
    with patch.object(body, "tick", wraps=body.tick) as tick:
        assert forecast() == plain
        count = tick.call_count
        assert count > 0 and forecast() == plain and tick.call_count == count

    # The same motion with a newly placed obstacle must use the changed terrain.
    block = dict(id=2, slot=0, place_tile=0, use_time=15, tile_boost=0)
    placed = forecast(("put", block, None, (5, 8)))
    assert placed == forecast(("put", block, None, (5, 8)), cached=False)
    assert placed["put"] and placed["body"]["y"] < plain["body"]["y"]

    # A hit changes velocity. Previously cached safe movement must not erase it.
    s["enemies"] = [dict(i=2, id=82, name="Wraith", x=88, y=102, vx=-1, vy=0,
                         w=26, h=42, life=200, damage=20, defense=0, kb_taken=.5,
                         through_blocks=True, flies=True, timers=[0, 0, 0, 0])]
    hurt = forecast(foes=enemies_of(s))
    assert hurt["damage"] > 0 and hurt == forecast(foes=enemies_of(s), cached=False)






def gear_hurt_check():
    from rules import enemies

    original = dict(body.start(32, 102), vx=2, vy=0)
    stopped = dict(original)
    assert not enemies.hurt_recoil(stopped, -1, {"no_knockback": True}) and stopped == original
    assert enemies.hurt_recoil(stopped, -1, {})
    assert (stopped["vx"], stopped["vy"]) == (-4.5, -3.5)
    assert enemies.hurt_immunity(1, {}) == 20
    assert enemies.hurt_immunity(20, {"long_invince": True}) == 80
    assert enemies.touch_damage({"damage": 40}, 20, .5, .25) == 22
    assert weapons.way(dict(damage=20, use_style=1, no_melee=True)) is None


def hostile_penetration_check():
    from rules import projectiles

    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(immune=False, immune_time=0, item_animation=0, breath=200,
                     defense_eff=.5, no_knockback=True)
    s.update(shots=[projectiles.spawn(55, 42, 123, 0, 0, 10, hostile=True, friendly=False)], strength=1)
    s["shots"][0]["penetrate"] = 1
    phys = dict(run_top=3, run_acc=.08, run_slow=.2, acc_run=3, gravity=.4,
                fall_top=10, jump_hold=15, jump_speed=5.01, defense=0, defense_eff=.5)
    forecast = play(s, m, (), (), None, phys, 1, {}, 0, 1, 0, lambda *_: False, horizon=1)
    assert forecast["damage"] == 20 and len(forecast["projectiles"]) == 1
    assert forecast["projectiles"][0]["penetrate"] == 1
    assert forecast["body"]["vx"] == 0


def ongoing_damage_check():
    s, m = observation(), Map()
    m.update(s["cells"])
    s["body"].update(immune=False, immune_time=0, item_animation=0, breath=200,
                     defense_eff=.5, life_regen=-4, life_regen_count=-116)
    s.update(shots=[], strength=1)
    s["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                        jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    first = fast.project(s, m, 1, [], s["tick"] + 1)
    assert first["body"]["life"] == 99 and first["body"]["life_regen_count"] == 0
    split = fast.project(dict(first, tick=s["tick"] + 1), m, 30, [], s["tick"] + 1)
    whole = fast.project(s, m, 31, [], s["tick"] + 1)
    assert split["body"] == whole["body"] and whole["body"]["life"] == 98


def loop_check():
    state = observation()
    events = []

    class IO:
        def call(self, path, payload=None, timeout=None):
            events.append(path)
            if path.startswith("/state?"):
                return state
            return {}

    goal = {"kind": "reach", "cell": (2, 8), "limit_ticks": 60}
    original = copy.deepcopy(state)
    outcome = Loop(IO(), draw=False, world_name="Gym").run(goal, lambda r: events.append(r["type"]))
    assert outcome["status"] == "done"
    assert events == ["/state?w=90&h=50&attack_shapes=1&shape_step=16", "goal", "round", "round_end", "/run/cancel", "cleanup"]
    assert state == original


def chest_loot_check():
    from unittest.mock import patch

    # Run the actual interaction chooser and loop: opening must not end the goal.
    def attempt(receipt, take=True):
        state = observation()
        state["body"].update(chest=-1, item_animation=0, item_time=0, breath=200, defense_eff=.5)
        state["physics"].update(range_x=5, range_y=4)
        state.update(progress={"life_max_perm": 100}, strength=1, shots=[])
        calls, rows = [], []
        class IO:
            def call(self, path, payload=None, timeout=None):
                calls.append((path, payload))
                if path.startswith("/state?"):
                    state["tick"] += 16
                    return copy.deepcopy(state)
                if path == "/run":
                    state["body"]["chest"] = 7
                    state["execution"]["active"] = {"command_id": payload["command_id"],
                                                       "applied_tick": payload["start_tick"] + 1}
                    return {"now": state["tick"]}
                if path == "/run/cancel":
                    state["execution"]["active"] = None
                if path == "/chest":
                    assert state["body"]["chest"] == 7 and payload == {"take": "all", "cell": (3, 8)}
                    state["body"]["chest"] = -1
                    if receipt.get("took"):
                        state["bag"].append({"name": "Torch", "stack": 10})
                    return receipt
                return {}

        with patch.object(pathfinder, "breathable_plan", return_value=None), \
                patch.object(fast, "project", side_effect=lambda state, *_: state), \
                patch("loop.time.sleep"):
            goal = {"kind": "interact", "operation": "chest", "cell": (3, 8), "limit_ticks": 120}
            if not take:
                goal["take"] = False
            outcome = Loop(IO(), draw=False, world_name="Gym").run(goal, rows.append)
        return outcome, calls, state, rows

    outcome, calls, state, rows = attempt({"took": ["Torch x10"], "remaining": []})
    assert outcome["status"] == "done" and "Torch x10" in outcome["reason"]
    assert goals.quantity(state, "Torch") == 10
    assert [p for p, _ in calls].index("/run") < [p for p, _ in calls].index("/chest")
    assert any(r["type"] == "round" and r["decision"].get("label") == "take chest loot" for r in rows)
    outcome, _, _, _ = attempt({"took": ["Torch x10"], "remaining": ["Wood x5"]})
    assert outcome["status"] == "failed" and "Wood x5" in outcome["reason"]
    assert attempt({"took": [], "remaining": []})[0]["reason"] == "chest is empty"
    assert attempt({"took": ["Torch x10"]})[0]["status"] == "failed"  # An old mod cannot falsely confirm completion.
    assert goals.chest_result({"stopped": "the open chest is not the requested chest"})[0] == "failed"
    outcome, calls, _, _ = attempt({}, take=False)
    assert outcome["status"] == "done" and "/chest" not in [p for p, _ in calls]
    assert goals.chest_result({"stopped": "the open chest is not the requested chest"}) == (
        "failed", "the open chest is not the requested chest")


def death_with_pending_command_check():
    from unittest.mock import patch

    state = observation()
    sent = []

    class IO:
        def call(self, path, payload=None, timeout=None):
            if path.startswith("/state?"):
                state["tick"] += 1
                if sent:
                    state["body"].update(dead=True, life=0)
                return copy.deepcopy(state)
            if path == "/run":
                sent.append(payload)
                return {"now": state["tick"]}
            return {}

    with patch.object(fast, "project", side_effect=lambda state, *_: state), \
            patch.object(fast, "decide", return_value={"steps": [{"keys": [], "ticks": 60}]}):
        result = Loop(IO(), draw=False, world_name="Gym").run(
            {"kind": "maintain", "area": [0, 0, 29, 11], "ticks": 50, "limit_ticks": 60}, lambda _: None)
    assert len(sent) == 1 and result["status"] == "died", result


def waiting_check():
    state = observation()
    world = Map()
    world.update(state["cells"])
    waiting = goals.waiting_goal(state)
    x, y, x1, y1 = waiting["area"]
    assert (x1 - x + 1, y1 - y + 1) == (81, 41)
    assert goals.measure(waiting, state, world, 0)["remaining"] is None
    assert goals.check(waiting, state, world, 0, state["tick"]) is None
    stationary = {"body": state["body"], "damage": 10, "healed": 0, "shots": 0}
    outside = dict(stationary, body=dict(state["body"], x=(x1 + 1) * 16), damage=0)
    assert fast.rank(waiting, state, outside, None, 0, 0, 0) < fast.rank(waiting, state, stationary, None, 0, 0, 0)
    stay = dict(stationary, damage=0, settling_damage=10)
    shoot = dict(stay, shots=1, settling_damage=0)
    assert fast.rank(waiting, state, shoot, None, 0, 0, 0) < fast.rank(waiting, state, stay, None, 0, 0, 0)
    clear = dict(stay, settling_damage=0)
    assert fast.rank(waiting, state, clear, None, 0, 0, 0) < fast.rank(waiting, state, shoot, None, 0, 0, 0)
    strike = dict(clear, dealt=5)
    assert fast.rank(waiting, state, strike, None, 0, 0, 0) < fast.rank(waiting, state, clear, None, 0, 0, 0)
    maintain = {"kind": "maintain", "area": waiting["area"], "ticks": 900, "limit_ticks": 1200}
    assert fast.rank(maintain, state, strike, None, 0, 0, 0) == fast.rank(waiting, state, strike, None, 0, 0, 0)
    assert fast.rank(maintain, state, strike, None, 0, 0, 0) < fast.rank(maintain, state, clear, None, 0, 0, 0)
    ramming = dict(strike, dealt=50, contacts=1)
    assert fast.rank(maintain, state, strike, None, 0, 0, 0) < fast.rank(maintain, state, ramming, None, 0, 0, 0)
    assert fast.rank(maintain, state, clear, None, 0, 0, 0) < fast.rank(maintain, state, ramming, None, 0, 0, 0)
    hurt = dict(strike, damage=1, dealt=100)
    assert fast.rank(maintain, state, clear, None, 0, 0, 0) < fast.rank(maintain, state, hurt, None, 0, 0, 0)
    fighting = dict(strike, contacts=0, measurement={"remaining": 1}, ticks=60)
    rushing = dict(fighting, contacts=1, dealt=50)
    defeat = {"kind": "defeat"}
    assert fast.rank(defeat, state, fighting, 100, 0, 0, 0) < fast.rank(defeat, state, rushing, 50, 0, 0, 0)
    assert fast.rank(defeat, state, fighting, float("inf"), 0, 0, 0) < fast.rank(defeat, state, rushing, float("inf"), 0, 0, 0)
    reach = {"kind": "reach"}
    safe = dict(clear, contacts=0, dealt=0, measurement={"remaining": 1}, ticks=60)
    collision = dict(safe, damage=15, contacts=1)
    assert fast.rank(reach, state, safe, 60, 0, 0, 0, False) < fast.rank(reach, state, collision, 60, 0, 0, 0, True)
    state["body"]["dead"] = True
    assert goals.check(waiting, state, world, 0, state["tick"])[0] == "died"


def review_timing_check():
    from unittest.mock import patch
    runtime = importlib.import_module("loop")
    state, clock, sleeps, deadlines = observation(), [0.0], [], []

    class IO:
        def call(self, path, payload=None, timeout=None):
            if path.startswith("/state?"):
                state["tick"] += 1
                return copy.deepcopy(state)
            if path == "/run":
                clock[0] += .04
                return {"now": state["tick"]}
            return {}

    def decide(*args, **kwargs):
        deadlines.append(args[7])
        clock[0] += .1
        return {"steps": [{"keys": [], "ticks": 60}]}

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    with patch.object(runtime.time, "monotonic", lambda: clock[0]), \
            patch.object(runtime.time, "sleep", sleep), \
            patch.object(fast, "project", side_effect=lambda state, *_: state), \
            patch.object(fast, "decide", side_effect=decide):
        Loop(IO(), draw=False, world_name="Gym").run(
            None, lambda _: None, next_goal=lambda: None, stop_waiting=lambda s: s["tick"] >= 6)
    assert len(sleeps) == 1 and abs(sleeps[0] - .11) < 1e-9, sleeps
    assert len(deadlines) == 1 and abs(deadlines[0] - 13 / 60) < 1e-9, deadlines


def waiting_handoff_check():
    state = observation()
    incoming = iter((None, {"kind": "reach", "cell": (2, 8), "limit_ticks": 60}, None))
    events, results = [], []

    class IO:
        def call(self, path, payload=None, timeout=None):
            if path.startswith("/state?"):
                state["tick"] += 1
                return copy.deepcopy(state)
            if path == "/run":
                state["execution"]["active"] = {"command_id": payload["command_id"],
                                                "applied_tick": payload["start_tick"] + 1}
                return {"now": state["tick"]}
            events.append(path)
            return {}

    from unittest.mock import patch
    with patch.object(fast, "project", side_effect=lambda state, *_: state), \
            patch.object(fast, "decide", return_value={"steps": [{"keys": [], "ticks": 60}]}):
        result = Loop(IO(), draw=False, world_name="Gym").run(
            None, lambda row: events.append(row["goal"]["kind"]) if row["type"] == "goal" else None,
            next_goal=lambda: next(incoming), on_result=lambda result, _: results.append(result["status"]),
            stop_waiting=lambda state: state["tick"] >= 7,
        )
    assert result["status"] == "stopped" and results == ["done"]
    assert events == ["maintain", "reach", "maintain", "/run/cancel"]


def weapon_resources_check():
    from rules import projectiles
    s, world = observation(), Map()
    world.update(s["cells"])
    s["body"].update(mana=20, player_id=0, item_animation=0, item_time=0, held_item=739,
                     selected=0, defense_eff=.5, immune_time=0, immune=False, breath=200, arm_offset=[10,21])
    s["enemies"] = [dict(i=2,id=3,name="Zombie",x=180,y=102,vx=0,vy=0,w=18,h=40,
                         life=1000,life_max=1000,damage=1,defense=0,kb_taken=0,ai=3,timers=[0,0,0,0])]
    s["physics"].update(run_top=3,run_acc=.08,run_slow=.2,acc_run=3,jump_hold=15,jump_speed=5.01,gravity=.4,fall_top=10)
    prototype=projectiles.spawn(121,0,0,0,0,20)
    prototype["speed"]=8
    item=dict(id=739,slot=0,stack=1,damage=20,shoot=121,shoot_speed=8,use_ammo=0,
              use_style=5,use_time=10,use_anim=10,mana_cost=5,combat={"shot":prototype})
    s["bag"]=[item]
    s["shots"]=[]
    assert weapons.way(item)=="shoot"
    assert weapons.affordable(s,item)
    original=copy.deepcopy(s)
    args=(world,enemies_of(s),(),("shoot",item,None,2),s["physics"],1,{},0,60,0,lambda *_:False)
    result=play(s,*args)
    assert result["fired"]==4 and result["shots"]==0 and result["body"]["mana"]==0, result
    assert s==original
    s["body"]["mana"]=0
    assert not weapons.affordable(s,item)
    thrown=dict(item,id=42,stack=2,mana_cost=0,consumable=True)
    s["bag"]=[thrown]
    result=play(s,world,enemies_of(s),(),("shoot",thrown,None,2),s["physics"],1,{},0,60,0,lambda *_:False)
    assert result["shots"]==2 and result["used"]=={0:2}
    goal=dict(kind="interact",operation="use",item="Finch Staff",projectile=759,limit_ticks=60)
    assert goals.measure(goal,s,world,None)["remaining"]==1
    s["shots"]=[dict(id=759,owner=1)]
    assert goals.measure(goal,s,world,None)["remaining"]==1
    s["shots"][0]["owner"]=0
    assert goals.measure(goal,s,world,None)["remaining"]==0
    # FirstLight: an idle forecast must account for our grenade's blast on the player.
    s["enemies"]=[]
    s["shots"]=[projectiles.spawn(30,s["body"]["x"]+10,s["body"]["y"]+21,0,0,60,
               owner=0,friendly=True,time_left=2,blast_w=128,blast_h=128,self_hurt=True)]
    original=copy.deepcopy(s)
    result=play(s,world,[],(),None,s["physics"],1,{},0,0,0,lambda *_:False,horizon=3)
    assert result["damage"]>0 and result["body"]["life"]<s["body"]["life"]
    assert s==original
    # Considering a supporting block must preserve the selected weapon's item data.
    s["shots"]=[]
    s["body"]["held_item"]=thrown["id"]
    block=dict(id=2,slot=1,stack=2,place_tile=0,use_time=10,tile_boost=0)
    s["bag"]=[thrown,block]
    result=play(s,world,[],(),("put",block,None,(2,9)),s["physics"],1,
                {"under":((2,9),)},0,1,0,lambda *_:False,horizon=1)
    assert result["body"]["held_item"]==thrown["id"]


def consumable_use_check():
    s, world = observation(), Map()
    world.update(s["cells"])
    goal = dict(kind="interact", operation="use", item="Recall Potion", consume=1, limit_ticks=60)
    s["bag"] = [dict(name="Recall Potion", slot=41, stack=6)]
    baseline = goals.begin(goal, s)
    assert baseline == 6 and goals.measure(goal, s, world, baseline)["remaining"] == 1
    # Moving or splitting the stack does not establish consumption.
    s["bag"] = [dict(name="Recall Potion", slot=0, stack=2), dict(name="Recall Potion", slot=41, stack=4)]
    assert goals.check(goal, s, world, baseline, s["tick"]) is None
    predicted = dict(body=s["body"], used={41:1})
    assert goals.measure(goal, s, world, baseline, forecast=predicted)["remaining"] == 1
    s["bag"][1]["stack"] = 3
    assert goals.check(goal, s, world, baseline, s["tick"])[0] == "done"
    two = dict(goal, consume=2)
    assert goals.measure(two, s, world, baseline)["remaining"] == 1
    s["bag"][1]["stack"] = 2
    assert goals.measure(two, s, world, baseline)["remaining"] == 0
    s["body"]["dead"] = True
    assert goals.check(goal, s, world, baseline, s["tick"])[0] == "died"
    s["body"]["dead"] = False
    s["bag"][1]["stack"] = 4
    s["tick"] += goal["limit_ticks"]
    assert goals.check(goal, s, world, baseline, s["tick"]-goal["limit_ticks"])[0] == "timeout"


def review_lead_check():
    from unittest.mock import patch
    runtime = importlib.import_module("loop")
    state, clock, leads, reactions, deadlines = observation(), [0.0], [], [], []

    class IO:
        def call(self, path, payload=None, timeout=None):
            if path.startswith("/state?"):
                state["tick"] += 1
                return copy.deepcopy(state)
            if path == "/run":
                leads.append(payload["start_tick"] - state["tick"])
                return {"now": state["tick"]}
            return {}

    def decide(*args, **kwargs):
        deadlines.append(args[7] - clock[0])
        reactions.append(kwargs["reaction_ticks"])
        clock[0] += .01 if len(deadlines) % 2 else .4
        return {"steps": [{"keys": [], "ticks": 60}]}

    with patch.object(runtime.time, "monotonic", lambda: clock[0]), \
            patch.object(runtime.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
            patch.object(fast, "project", side_effect=lambda state, *_: state), \
            patch.object(fast, "decide", side_effect=decide):
        Loop(IO(), draw=False, world_name="Gym").run(
            None, lambda _: None, next_goal=lambda: None, stop_waiting=lambda s: s["tick"] >= 9)
    assert leads == [fast.REVIEW_TICKS] * len(leads), leads
    assert reactions == [lead + fast.REVIEW_TICKS for lead in leads]
    assert all(deadline < 15 / 60 for deadline in deadlines), deadlines


def fighting_distance_check():
    state = observation()
    state["physics"].update(run_acc=.08, run_top=3)
    foe = dict(i=1, name="enemy", x=90, y=102, w=20, h=42, vx=-2, vy=0,
               damage=20, life=100, defense=0)
    result = dict(body=dict(state["body"], vx=0, vy=0), enemies=[foe], damage=0, healed=0,
                  dealt=10, contacts=0, ticks=60, shots=1, measurement={"remaining": 1})
    pressure = fast.contact_pressure(state, result, 30)
    assert pressure > 0
    safer = dict(result, body=dict(result["body"], x=-200))
    farther = dict(result, body=dict(result["body"], x=-400))
    assert fast.contact_pressure(state, safer, 30) == fast.contact_pressure(state, farther, 30) == 0
    # Clearing a nearby enemy helps even while the boss remains alive and distant.
    boss = dict(foe, i=2, name="boss", x=500)
    surrounded = dict(result, enemies=[foe, boss])
    cleared = dict(surrounded, enemies=[dict(foe, life=0), boss])
    assert fast.contact_pressure(state, cleared, 30) < fast.contact_pressure(state, surrounded, 30)
    goal = {"kind": "defeat"}
    exposed = dict(result, pressure=pressure, healing_value=0)
    safe_attack = dict(safer, pressure=0, healing_value=0)
    assert fast.rank(goal, state, safe_attack, 200, 0, 0, 0) < fast.rank(goal, state, exposed, 100, 0, 0, 0)
    fleeing = dict(safe_attack, dealt=0)
    assert fast.rank(goal, state, safe_attack, 200, 0, 0, 0) < fast.rank(goal, state, fleeing, float("inf"), 0, 0, 0)
    # Being unable to accelerate does not manufacture an escape speed.
    state["physics"]["run_acc"] = 0
    assert fast.contact_pressure(state, result, 30) >= pressure


def fighting_heal_check():
    state = observation()
    state["tick"] = 120
    state["body"].update(life=70, life_max=200)
    potion = dict(heal=100)
    result = dict(body=dict(state["body"], life=170), healed=100, damage=0, ticks=60)
    use = ("potion", potion, None, None)
    assert fast.healing_value(state, result, use, 300) == 0  # Comfortable fight saves it.
    state["hits"] = [{"tick": 115, "damage": 60}]
    assert fast.healing_value(state, result, use, 300) == 100
    state["hits"] = [{"tick": 1, "damage": 60}]
    assert fast.healing_value(state, result, use, 300) == 0  # Old harm is no longer a current rate.
    state["hits"] = [{"tick": 115, "damage": 60}]
    waste = dict(result, body=dict(state["body"], life=200), healed=10)
    assert fast.healing_value(state, waste, use, 300) == 0


def shared_combat_safety_check():
    state = observation()
    safe = dict(body=state["body"], damage=0, healed=0, dealt=12, contacts=0, pressure=0,
                ticks=60, shots=0, measurement={"remaining": 2}, hits_on={})
    for kind in goals.KINDS:
        goal = {"kind": kind, "area": [0, 0, 29, 11]}
        rank = lambda result, eta: fast.rank(goal, state, result, eta, 0, 0, 0)
        hurt_progress = dict(safe, damage=30, dealt=20, measurement={"remaining": 0})
        close_progress = dict(safe, pressure=1, dealt=20, measurement={"remaining": 0})
        assert rank(safe, 100) < rank(hurt_progress, 60), kind
        if kind in ("maintain", "defeat"):
            assert rank(safe, 100) < rank(close_progress, 60), kind
        else:
            assert rank(close_progress, 60) < rank(safe, 100), kind
    # The recorded jump deals 16 over 82 ticks; standing deals 12 over 60.
    wait = {"kind": "maintain", "area": [0, 0, 29, 11]}
    jump = dict(safe, dealt=16, ticks=82)
    assert fast.rank(wait, state, safe, 0, 0, 0, 0) < fast.rank(wait, state, jump, 0, 0, 0, 0)


def ranged_spacing_check():
    state = observation()
    state["physics"].update(run_acc=.08, run_top=3)
    foe = dict(i=1, x=90, y=102, w=20, h=42, vx=2, vy=0, damage=20, life=100, defense=0)
    near = dict(body=dict(state["body"], vx=0, vy=0), enemies=[foe], damage=0, healed=0,
                dealt=10, contacts=0, ticks=60, shots=1, measurement={"remaining": 1})
    # Drifting away does not erase the room a ranged fighter needs to react.
    assert fast.contact_pressure(state, near, 30) == 0
    assert fast.contact_pressure(state, near, 30, ranged=True) > 0
    far = dict(near, body=dict(near["body"], x=-400))
    farther = dict(far, body=dict(far["body"], x=-800))
    assert fast.contact_pressure(state, far, 30, True) == fast.contact_pressure(state, farther, 30, True) == 0
    a = dict(near, ranged=True, pressure=0, healing_value=0)
    b = dict(far, ranged=True, pressure=0, healing_value=0)
    assert fast.rank({"kind": "defeat"}, state, a, 200, 0, 0, 0) == fast.rank({"kind": "defeat"}, state, b, 200, 0, 0, 0)
    assert fast.rank({"kind": "defeat"}, state, a, 200, 0, 0, 0) < fast.rank({"kind": "defeat"}, state, dict(b, dealt=0), float("inf"), 0, 0, 0)


def fighting_controls_check():
    from rules import projectiles

    state, world = observation(), Map()
    world.update(state["cells"])
    state["body"].update(ground=True, vx=0, vy=0, wet=False, jump=0, defense_eff=.5,
                         immune=False, immune_time=0, item_animation=0, item_time=0,
                         held_item=9999, selected=0, breath=200, potion_delay=0, wing_time=60)
    state["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                            jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10,
                            wing_time_max=60)
    prototype = projectiles.spawn(14, 0, 0, 0, 0, 30)
    prototype["speed"] = 20
    gun = dict(id=9999, name="Test gun", slot=0, stack=1, damage=30, shoot=14,
               shoot_speed=20, use_time=3, use_anim=3, knockback=0, use_ammo=0,
               no_melee=True, use_style=5, pick=0, axe=0, heal=0, hook=False,
               block_tile=False, place_tile=-1, combat={"shot": prototype})
    state.update(bag=[gun], shots=[], strength=1)
    state["enemies"] = [dict(i=2, name="target", x=130, y=102, w=20, h=42, vx=2, vy=0,
                              life=10000, life_max=10000, damage=20, defense=0,
                              kb_taken=0, flies=True, through_blocks=True)]
    goal = {"kind": "defeat", "enemy": "target"}
    state["body"]["ground"] = False
    moves = list(fast.candidates(state, world, goal, None, {}, state["bag"]))
    assert all(use and use[0] == "shoot" for _, use in moves)
    assert len({move for move, _ in moves}) == len(moves)
    assert {tuple(move[0]) for move, _ in moves[:9]} >= {
        (), ("left",), ("right",), ("down",), ("left", "down"), ("right", "down"),
        ("jump",), ("left", "jump"), ("right", "jump")}
    assert any(move[0] == ("right", "jump") and move[1] == 8 for move, _ in moves[:16])
    use = moves[0][1]
    state["body"]["ground"] = True
    arguments = (state, world, enemies_of(state), (), None, state["physics"], 1,
                 {}, 0, 60, 16, lambda *_: False)
    held = play(*arguments, spacing_ticks=30)
    firing = play(*arguments[:4], use, *arguments[5:], spacing_ticks=30)
    assert held["path"] == firing["path"] and held["damage"] == firing["damage"]
    assert held["pressure"] == firing["pressure"] > 0
    assert firing["dealt"] > held["dealt"]
    # A temporarily invulnerable target still permits moving and holding fire.
    state["enemies"][0]["dont_take_damage"] = True
    clock = Timing()
    decision = fast.decide(state, world, goal, goals.begin(goal, state), None, None,
                           clock, clock.now()+1)
    assert any("use" in step["keys"] for step in decision["steps"]), decision
    # Healing gets an early turn while distinct movements remain ahead of duplicates.
    state["body"]["life"] = 40
    potion = dict(gun, id=28, name="Healing Potion", slot=6, heal=100, shoot=0,
                  damage=0, no_melee=False, use_anim=17, use_time=17, combat={})
    state["bag"].append(potion)
    choices = list(fast.candidates(state, world, goal, None, {}, state["bag"]))
    distinct = len({move for move, _ in choices})
    assert len({move for move, _ in choices[:distinct]}) == distinct
    assert any(use[0] == "potion" for _, use in choices[:3])
    # No affordable attack restores ordinary movement rather than stopping the fight.
    state["bag"] = [dict(gun, use_ammo=1)]
    assert all(use is None for _, use in fast.candidates(state, world, goal, None, {}, state["bag"]))


def homing_check():
    from rules import projectiles as p
    foe = dict(i=3, x=120, y=80, w=20, h=42, life=100, life_max=100)
    shot = p.spawn(207, 0, 0, 10, 0, 20)
    before = copy.deepcopy(shot)
    p.advance(shot, lambda *_: False, None, targets=[foe])
    assert shot["ai1"] == 4 and shot["vy"] > 0 and shot["vx"] < 10
    assert shot["local_ai"][0] == 10
    nano = p.spawn(837, 0, 0, 10, 0, 20)
    p.advance(nano, lambda *_: False, None, targets=[foe])
    assert nano["ai1"] == 4 and nano["vy"] == shot["vy"] / 4
    # A copied forecast must not modify the observation's localAI array.
    assert before.get("local_ai", [0])[0] == 0
    second = dict(shot)
    p.advance(second, lambda *_: False, None, targets=[dict(foe, x=600)])
    assert second["ai1"] == 4  # Outside acquisition range, retained lock.
    p.advance(second, lambda *_: False, None, targets=[dict(foe, life=0)])
    assert second["ai1"] == 0
    blocked = p.spawn(207, 0, 0, 10, 0, 20)
    p.advance(blocked, lambda x, y: x == 4, None, targets=[foe])
    assert not blocked.get("ai1") and blocked["vy"] == 0
    immune = p.spawn(207, 0, 0, 10, 0, 20)
    p.advance(immune, lambda *_: False, None, targets=[dict(foe, dont_take_damage=True)])
    assert not immune.get("ai1")
    # No steering without a target, and no alteration of hostile-shot motion.
    unknown = p.spawn(207, 0, 0, 10, 0, 20)
    p.advance(unknown, lambda *_: False, None)
    assert (unknown["vx"], unknown["vy"]) == (10, 0)
    hostile = p.spawn(207, 0, 0, 10, 0, 20, hostile=True, friendly=False)
    p.advance(hostile, lambda *_: False, None, targets=[foe])
    assert (hostile["vx"], hostile["vy"]) == (10, 0)


def summon_count_check():
    state, world = observation(), Map()
    state["shots"] = [dict(id=423, owner=0), dict(id=423, owner=0), dict(id=423, owner=1)]
    goal = dict(kind="interact", operation="use", item="Xeno Staff", projectile=423, count=3)
    assert goals.measure(goal, state, world, 0)["remaining"] == 1
    state["shots"].append(dict(id=423, owner=0))
    assert goals.measure(goal, state, world, 0)["remaining"] == 0
    del goal["count"]
    assert goals.measure(goal, state, world, 0)["remaining"] == 0


def bullet_reuse_check():
    from rules import projectiles as p
    from unittest.mock import patch
    empty = lambda *_: False
    target = dict(i=2, x=120, y=80, w=20, h=42, life=100, life_max=100)
    for kind in (1, 207, 837):
        start = p.spawn(kind, 0, 0, 10, 0, 20)
        cache = {}
        plain, warm = copy.deepcopy(start), copy.deepcopy(start)
        expected = []
        for _ in range(60):
            a = p.advance(plain, empty, None, targets=[target])
            b = p.advance(warm, empty, None, targets=[target], cache=cache)
            assert a == b and plain == warm
            expected.append(copy.deepcopy(plain))
        again = copy.deepcopy(start)
        with patch.object(p, "_advance_friendly", wraps=p._advance_friendly) as physics:
            for state in expected:
                p.advance(again, empty, None, targets=[target], cache=cache)
                assert again == state
            assert physics.call_count == 0
        # Target movement, eligibility and death cannot reuse the old homing.
        if kind != 1:
            for changed in (dict(target, y=160), dict(target, life=0), dict(target, chaseable=False)):
                a, b = copy.deepcopy(start), copy.deepcopy(start)
                p.advance(a, empty, None, targets=[changed])
                p.advance(b, empty, None, targets=[changed], cache=cache)
                assert a == b
    assert start == p.spawn(837, 0, 0, 10, 0, 20)


def forecast_budget_check():
    from unittest.mock import patch
    state, world = observation(), Map()
    world.update(state["cells"])
    state["body"].update(ground=True, wet=False, jump=0, defense_eff=.5,
                          immune=False, immune_time=0, item_animation=0, breath=200)
    state.update(shots=[], strength=1)
    state["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                            jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    goal = dict(kind="reach", cell=(18, 8))
    clock, calls = [0.0], [0]
    normal = fast.play
    def play(*args, **kwargs):
        result = normal(*args, **kwargs)
        calls[0] += 1
        clock[0] += .04
        return result
    timing = Timing()
    timing.start = 0
    timing.now = lambda: clock[0]
    with patch.object(fast, "play", side_effect=play):
        result = fast.decide(state, world, goal, 0, None, (18, 8), timing, .09)
    assert calls[0] == 2 and clock[0] < .09 and result.get("steps")


def game_clock_submission_check():
    from unittest.mock import patch
    runtime = importlib.import_module("loop")
    state, clock, requests, rows = observation(), [0.0], [], []
    class IO:
        def call(self, path, payload=None, timeout=None):
            if path.startswith("/state?"):
                state["tick"] += 1
                return copy.deepcopy(state)
            if path == "/run":
                requests.append(payload)
                if len(requests) == 2:
                    return dict(accepted=False, late=True, now=state["tick"])
                state["execution"]["active"] = dict(command_id=payload["command_id"], applied_tick=state["tick"])
                return dict(now=state["tick"])
            return {}
    def decide(*args, **kwargs):
        clock[0] += .3  # Real time advances faster than this stub's game clock.
        return {"steps": [{"keys": [], "ticks": 60}]}
    with patch.object(runtime.time, "monotonic", lambda: clock[0]), \
            patch.object(runtime.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
            patch.object(fast, "project", side_effect=lambda state, *_: state), \
            patch.object(fast, "decide", side_effect=decide):
        result = Loop(IO(), draw=False, world_name="Gym").run(None, rows.append,
            next_goal=lambda: None, stop_waiting=lambda s: s["tick"] >= 8)
    assert result["status"] == "stopped" and len(requests) == 3
    rejected = next(r for r in rows if r.get("decision", {}).get("submission"))
    following = next(r for r in rows if r.get("type") == "round" and r["tick"] > rejected["tick"])
    assert following["execution"]["active"]["command_id"] == requests[0]["command_id"]


def potion_resume_check():
    from rules import projectiles

    state, world = observation(), Map()
    world.update(state["cells"])
    state["body"].update(life=40, life_max=200, ground=True, defense_eff=.5, immune=False,
                         immune_time=0, item_animation=0, item_time=0, held_item=99,
                         selected=0, breath=200, potion_delay=0)
    state["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                            jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    shot = projectiles.spawn(1, 0, 0, 0, 0, 20)
    shot["speed"] = 8
    bow = dict(id=99, name="bow", slot=0, stack=1, damage=20, shoot=1, shoot_speed=8,
               use_ammo=1, use_style=5, use_time=20, use_anim=20, combat={"shot": shot})
    ammo = dict(id=40, slot=54, stack=100, ammo=1, damage=0, shoot_speed=0, knockback=0)
    potion = dict(id=28, name="Healing Potion", slot=6, stack=8, heal=100, use_anim=17, use_time=17)
    state.update(bag=[bow, potion, ammo], shots=[], strength=1)
    state["enemies"] = [dict(i=1, name="target", x=180, y=102, vx=0, vy=0, w=18, h=40,
                              life=1000, damage=0, defense=0, kb_taken=0)]
    use, follow = ("potion", potion, None, None), ("shoot", bow, ammo, 1)
    original = copy.deepcopy(state)
    result = play(state, world, enemies_of(state), (), use, state["physics"], 1,
                  {}, 0, 60, 0, lambda *_: False, follow=follow)
    assert result["healed"] == 100 and result["body"]["life"] == 140
    assert result["fired"] > 0 and result["dealt"] > 0
    assert result["used"][6] == 1 and result["used"][54] == result["fired"]
    assert result["resume_at"] == result["potion_at"] + potion["use_anim"] + 1
    steps = fast.script(((), 0, 60, 0), use, result)
    ticks = [step for step in steps for _ in range(step["ticks"])]
    assert sum("use" in step["keys"] and step.get("slot") == 6 for step in ticks) == 1
    assert all(step.get("slot") == 0 and "use" in step["keys"] for step in ticks[result["resume_at"]-1:])
    projected = fast.project(state, world, 5, steps, state["tick"]+1)
    assert projected["body"]["life"] == 140 and projected["body"]["potion_used"]
    assert projected["body"]["held_item"] == potion["id"]
    assert projected["body"]["item_animation"] == potion["use_anim"] - 4
    assert next(i["stack"] for i in projected["bag"] if i["slot"] == 6) == 7
    whole = fast.project(state, world, 60, steps, state["tick"]+1)
    assert whole["body"]["life"] == 140 and whole["body"]["selected"] == 0
    assert sum(i["stack"] for i in whole["bag"] if i["slot"] == 6) == 7
    assert sum(i["stack"] for i in whole["bag"] if i["slot"] == 54) < 100
    assert state == original


def motor_check():
    if gym.state(w=2, h=2).get("world_name") != "Gym":
        raise RuntimeError("motor check requires the Gym world")
    s = gym.state(w=2, h=2)
    def command(name, start, expiry):
        return {"command_id": name, "goal": "motor check", "session": s["session"]["loaded"],
                "start_tick": start, "expiry_tick": expiry, "wait": False, "replace": True,
                "steps": [{"keys": [], "ticks": 2}]}
    try:
        now = s["tick"]
        ack = gym.call("/run", command("future", now + 12, now + 24))
        assert ack["at"] == now + 12 and not ack["started"]
        while True:
            st = gym.state(w=2, h=2)
            if st["tick"] >= now + 15:
                break
        done = st["execution"]["finished"]
        assert done["command_id"] == "future" and done["applied_tick"] == now + 13 and done["status"] == "finished"
        now = st["tick"]
        gym.call("/run", command("pending survives rejection", now + 120, now + 180))
        rejected = gym.call("/run", command("late replacement", now - 1, now + 60))
        assert rejected.get("accepted") is False and rejected["late"]
        assert gym.state(w=2, h=2)["execution"]["pending"]["command_id"] == "pending survives rejection"
        gym.call("/run/cancel", {})
        now = gym.state(w=2, h=2)["tick"]
        running = command("active survives rejection", now + 3, now + 180)
        running["steps"][0]["ticks"] = 120
        gym.call("/run", running)
        while True:
            st = gym.state(w=2, h=2)
            if st["execution"]["active"]: break
        rejected = gym.call("/run", command("late with active", st["tick"] - 1, st["tick"] + 60))
        assert rejected.get("accepted") is False
        assert gym.state(w=2, h=2)["execution"]["active"]["command_id"] == "active survives rejection"
        gym.call("/run/cancel", {})
        now = gym.state(w=2, h=2)["tick"]
        assert gym.call("/run", command("late without active", now - 1, now + 60)).get("accepted") is False
        gym.call("/run", command("cancelled in pause", now + 180, now + 240))
        assert gym.call("/pause", {"on": True})["paused"]
        gym.call("/run/cancel", {}, timeout=2)
        assert not gym.call("/pause", {"on": False})["paused"]
        execution = gym.state(w=2, h=2)["execution"]
        assert not execution["active"] and not execution["pending"]
        assert execution["finished"]["status"] == "cancelled"
        try:
            gym.call("/run", command("expired", now - 4, now - 1))
        except RuntimeError as error:
            assert "expired" in str(error)
        else:
            raise AssertionError("mod accepted expired command")
    finally:
        gym.call("/pause", {"on": False}, timeout=3)
        gym.call("/run/cancel", {}, timeout=2)


def angled_stab_check():
    gym.in_gym()
    gym.stamp(gym.canvas())
    for side in (-1, 1):
        gym.clean_body((48, 59), bag=[("Copper Shortsword", 1, 0)])
        player = gym.state(w=2, h=2)["body"]
        seed = {"x": player["x"] + 10 + side * 37 - 18, "y": player["y"] - 41,
                "vx": 0, "vy": 0, "life": 40, "timers": [1, 0, 0, 0]}
        gym.gym(background=True, spawn=[["Vulture", int(seed["x"] // 16), int(seed["y"] // 16), seed]])
        state = gym.state(w=24, h=20)
        world = Map()
        world.update(state["cells"])
        foe, = enemies_of(state)
        move = ((), 0, 12, 0)
        use = ("stab", state["bag"][0], None, foe["i"])
        result = play(state, world, [foe], (), use, state["physics"], state["strength"],
                      {}, 0, 12, 0, lambda *_: False, horizon=12)
        assert result["hits"] == 1 and result["damage"] == 0, (side, result)
        steps = fast.script(move, use, result)
        actual = gym.run(steps)["trace"]
        shots = [s for row in actual for s in row["shots"] if s[5] == use[1]["shoot"]]
        assert shots and side * shots[0][3] > 0 and shots[0][4] < 0, (side, shots)
        assert actual[-1]["enemies"][0][5] < foe["life"], (side, actual[-1])
        assert all(row["life"] == actual[0]["life"] for row in actual), (side, actual[-1])


if __name__ == "__main__":
    checks = [map_check, exploration_route_check, platform_support_order_check, exploration_no_reveal_check,
              cell_mining_permission_check, quantity_shortfall_check, quantity_total_check, excavation_route_check,
              goal_check, failure_explanation_check, route_check, quarry_route_check, route_progress_check, pillar_reach_check,
              forecast_floor_check, air_budget_check, candidate_check, material_choice_check, handoff_check,
              projectile_handoff_check, attack_shape_check, attack_shape_handoff_check, firing_duration_check,
              protection_counter_check, clearance_reuse_check, attack_patch_check, spacing_path_check, enemy_observation_check,
              friendly_projectile_check, knockback_check, combat_forecast_check, movement_reuse_check,
              gear_hurt_check, hostile_penetration_check,
              ongoing_damage_check, loop_check, chest_loot_check, death_with_pending_command_check, waiting_check,
              review_timing_check, waiting_handoff_check, weapon_resources_check, consumable_use_check,
              review_lead_check, fighting_distance_check, fighting_heal_check, shared_combat_safety_check, potion_resume_check,
              ranged_spacing_check, fighting_controls_check, homing_check, summon_count_check,
              bullet_reuse_check, forecast_budget_check, game_clock_submission_check]
    from motion_checks import CHECKS
    checks.extend(CHECKS)
    if "--gym" in sys.argv:
        checks.extend((motor_check, angled_stab_check))
    for check in checks:
        check()
        print(check.__name__, "passed", flush=True)
