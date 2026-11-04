"""Component and AgentIO contract checks for the isolated live brain."""
import copy
import importlib
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
from rules import body
from rules.forecast import play

fast = importlib.import_module("fast-brain")


def window(x0=0, y0=0, w=30, h=12):
    blank = lambda value: [[value] * w for _ in range(h)]
    tiles = blank(-1)
    for row in tiles[9:]:
        row[:] = [0] * w
    return {"x0": x0, "y0": y0, "w": w, "h": h, "tile": tiles, "liquid": blank(0), "shape": blank(0),
            "liquid_kind": blank(0), "wall": blank(0), "seen": blank(True),
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
    assert m.shape(4, 9) == 5 and m.cells[(3, 8)][2:] == (91, 2, 0)
    assert not m.update(box) and m.version == 1
    assert box == original
    m.update(window(x0=15))
    assert m.shape(4, 9) == 5
    assert m.tile(4, 9, dug={(4, 9)}) == -1 and m.raw_tile(4, 9) == 0


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
               "block_tile": True, "pick": 0, "axe": 0, "heal": 0, "hook": False}
    wood = dict(pumpkin, slot=7, id=9, name="Wood", place_tile=30)
    goal = {"kind": "reach", "cell": (14, 8), "preserve": {"Wood": 1}}
    s["bag"] = [wood, pumpkin]
    bag, kit = fast.items_for(s, goal)
    assert kit["block"]["name"] == "Pumpkin"
    pumpkin["slot"], wood["slot"] = wood["slot"], pumpkin["slot"]
    assert fast.items_for(s, goal)[1]["block"]["name"] == "Pumpkin"
    moves = list(fast.candidates(s, m, goal, None, {"put": [(4, 8)]}, bag))
    assert {use[1]["name"] for _, use in moves if use} == {"Pumpkin", "Wood"}
    assert fast.items_for(dict(s, bag=[wood]), goal)[1]["block"]["name"] == "Wood"


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
    assert events == ["/state?w=90&h=50", "round", "round_end", "/run/cancel", "cleanup"]
    assert state == original


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
    state["body"]["dead"] = True
    assert goals.check(waiting, state, world, 0, state["tick"])[0] == "died"


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


if __name__ == "__main__":
    checks = [map_check, goal_check, route_check, air_budget_check, candidate_check, handoff_check, loop_check, waiting_check, waiting_handoff_check]
    if "--gym" in sys.argv:
        checks.append(motor_check)
    for check in checks:
        check()
        print(check.__name__, "passed", flush=True)
