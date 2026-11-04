"""Run one measurable goal through AgentIO."""
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError
from uuid import uuid4

sys.path.append(str(Path(__file__).resolve().parents[1]))
from mod import client

import goals
from map import Map
import overlay
import pathfinder
from rules.body import cells_of

fast = importlib.import_module("fast-brain")
REVIEW_SECONDS = fast.REVIEW_TICKS / 60


class Timing:
    def __init__(self):
        self.spans = []
        self.now = time.monotonic
        self.start = self.now()

    @contextmanager
    def span(self, name):
        start = self.now()
        try:
            yield
        finally:
            self.spans.append({"phase": name, "start_ms": round((start - self.start) * 1000, 3),
                               "end_ms": round((self.now() - self.start) * 1000, 3)})


def destination(goal, state, world):
    kind = goal["kind"]
    x, _, _, y = cells_of(state["body"])
    if kind == "reach":
        area = goal.get("area")
        target = goal.get("cell") or [(area[0] + area[2]) // 2, (area[1] + area[3]) // 2]
        return tuple(target), area, False
    if kind == "change":
        wrong = goals.wrong_cells(goal, world)
        target = min(wrong, key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)
        return target[:2], None, True
    if kind == "search":
        target = min((c for c in goals.cells_in(goal["area"]) if world.raw_tile(*c) == -2),
                     key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)
        return target, None, False
    if kind == "quantity":
        drops = [i for i in state["items"] if i["name"] == goal["item"]]
        if drops:
            item = min(drops, key=lambda i: (i["x"] - x * 16) ** 2 + (i["y"] - y * 16) ** 2)
            c, r = int((item["x"] + item["w"] / 2) // 16), int((item["y"] + item["h"] - 1) // 16)
            return (c, r), (c - 1, r - 1, c + 1, r + 1), False
    if kind == "interact" and goal.get("cell"):
        return tuple(goal["cell"]), None, True
    return None, None, False


class Loop:
    def __init__(self, io=client, draw=True, world_name=None):
        self.io = io
        self.draw = draw
        self.world_name = world_name
        self.world = Map()
        self.session = None

    def read(self):
        return self.io.call("/state?w=90&h=50", timeout=4)

    def run(self, goal, record, episode=None, *, next_goal=None, on_result=None, stop_waiting=None):
        """Run one goal, or stay active when a caller supplies a goal source."""
        if goal is None and next_goal is None:
            raise ValueError("expected a goal or goal source")
        if goal is not None and (goal["kind"] not in goals.KINDS or goal["limit_ticks"] <= 0):
            raise ValueError("expected a known goal and positive limit_ticks")
        goal_id = uuid4().hex
        plan = signature = baseline = first = previous = None
        expected = None
        commands = {}
        decision_seconds = 0.08
        round_no = 0
        first_round = 1
        goal_started = time.monotonic()
        state = None
        started = time.monotonic()
        own = False
        result = None
        try:
            while True:
                timing = Timing()
                round_no += 1
                with timing.span("state.read"):
                    state = self.read()
                with timing.span("session.check"):
                    session = state["session"]["loaded"]
                    if self.world_name is not None and state["world_name"] != self.world_name:
                        raise RuntimeError(f"expected {self.world_name}, saw {state['world_name']}")
                    if own and session != self.session:
                        own = False
                        raise RuntimeError("world reloaded")
                    if first is None and session != self.session:
                        self.world = Map()
                        self.session = session
                    if "execution" not in state:
                        raise RuntimeError("AgentIO command status is required")
                    own = True
                    if round_no == 1 and (state["execution"]["active"] or state["execution"]["pending"]):
                        own = False
                        raise RuntimeError("AgentIO already has an active controller")
                with timing.span("goal.select"):
                    if goal is None or goal.get("waiting"):
                        incoming = next_goal()
                        if incoming is not None:
                            if incoming["kind"] not in goals.KINDS or incoming["limit_ticks"] <= 0:
                                raise ValueError("expected a known goal and positive limit_ticks")
                            goal = incoming
                        elif goal is None:
                            goal = goals.waiting_goal(state)
                        if incoming is not None or first is None:
                            goal_id = uuid4().hex
                            first = plan = signature = None
                            goal_started = timing.now()
                            first_round = round_no
                            record({"type": "goal", "goal": goal, "state": state})
                with timing.span("map.update"):
                    changes = self.world.update(state["cells"])
                with timing.span("effects.observe"):
                    execution = state["execution"]
                    if first is None:
                        first = state
                        baseline = goals.begin(goal, state)
                    effects = {}
                    if previous is not None:
                        effects = {
                            "damage": state["counters"]["damage_taken"] - previous["counters"]["damage_taken"],
                            "dealt": state["counters"]["damage_dealt"] - previous["counters"]["damage_dealt"],
                            "terrain": [(cell, before, after) for cell, (before, after) in changes.items() if before],
                        }
                    previous = state
                with timing.span("goal.check"):
                    check = goals.check(goal, state, self.world, baseline, first["tick"])
                    if not goal.get("waiting") and time.monotonic() - goal_started > goal["limit_ticks"] / 60 + 30:
                        check = "failed", "game clock stopped or ran too slowly"
                    if not check and goal.get("waiting") and stop_waiting and stop_waiting(state):
                        check = "stopped", "waiting period ended"
                    finished = execution.get("finished")
                    if expected and not any(e and e["command_id"] == expected for e in execution.values()):
                        check = "failed", "the mod lost the last command"
                    if finished and finished["command_id"] == expected and finished["status"] != "finished":
                        check = check or ("failed", "command " + finished["status"])
                if any(e.get("boss") for e in state["enemies"]):
                    check = check or ("unsupported", "boss movement model has not been validated")
                decision = {}
                ack = None
                measurement = None
                busy = execution["pending"] or execution["active"]
                deadline_missed = False
                replan = None
                if not check and not execution["pending"]:
                    with timing.span("pathfinder.inputs"):
                        target, area, approach = destination(goal, state, self.world)
                        _, kit = fast.items_for(state, goal)
                        phys = dict(state["physics"], defense=state["body"]["defense"])
                        # Slot moves and irrelevant inventory changes leave the route valid.
                        relevant = tuple((k, v["id"], v["use_time"], v["tile_boost"]) if isinstance(v, dict) else (k, v)
                                         for k, v in kit.items())
                        key = (target, area, approach, self.world.version, relevant, phys)
                        body = state["body"]
                        off_route = plan and body["ground"] and plan.at(*plan.node_of(body)) is None
                        wet_head = pathfinder.head_underwater(self.world, body)
                        air_replan = plan and (plan.dry and not wet_head or
                            not plan.dry and wet_head and not phys.get("gills") and
                            pathfinder.air_needed(plan, body) >= body["breath"] * pathfinder.AIR_TICKS_PER_POINT)
                    if target is not None and (key != signature or off_route or air_replan):
                        replan = "no route" if signature is None else (
                            "destination changed" if key[:3] != signature[:3] else
                            "terrain knowledge changed" if key[3] != signature[3] else
                            "capabilities changed" if key[4:] != signature[4:] else "air budget changed" if air_replan else "outside route")
                        with timing.span("pathfinder.plan"):
                            plan = pathfinder.breathable_plan(self.world, target, body, kit, phys, area=area,
                                                   approach=approach, keep=goal.get("protect", ()))
                            signature = key
                    if target is None:
                        plan = signature = None
                    with timing.span("goal.measure"):
                        measurement = goals.measure(goal, state, self.world, baseline, pathfinder.estimate(plan, body))
                    # Planning can run while the last script still holds the buttons. Schedule
                    # the replacement far enough ahead to forecast from its likely start.
                    elapsed = timing.now() - timing.start
                    lead = max(2, int((elapsed + decision_seconds) * 60) + 2)
                    start_tick = state["tick"] + lead
                    active = execution["active"]
                    active_steps = commands.get(active["command_id"], []) if active else []
                    applied_tick = active["applied_tick"] if active else state["tick"] + 1
                    with timing.span("fast.project"):
                        projected = fast.project(state, self.world, lead, active_steps, applied_tick)
                    continuing = fast.pressed_ticks(active_steps)[max(0, start_tick - applied_tick + 1):]
                    compute = timing.now()
                    decision = fast.decide(projected, self.world, goal, baseline, plan, target, timing,
                                           timing.start + REVIEW_SECONDS, continuing=continuing, observed_body=body)
                    decision_seconds = max(0.04, (timing.now() - compute) * 1.25)
                    decision["forecast_start_tick"] = start_tick
                    with timing.span("command.submit"):
                        if "endpoint" in decision:
                            if active:
                                self.io.call("/run/cancel", {}, timeout=2)
                            ack = self.io.call(decision["endpoint"], decision["payload"], timeout=6)
                            expected = None
                            if ack.get("made") == 0 and ack.get("stopped"):
                                check = ("failed", ack["stopped"])
                        elif "steps" in decision:
                            ticks = sum(step["ticks"] for step in decision["steps"])
                            # A late decision was forecast from the wrong body position.
                            if active and (timing.now() - timing.start) * 60 + 1 >= lead:
                                decision["submission"] = "kept current command because the new forecast was late"
                            else:
                                expected = f"{goal_id}:{round_no}"
                                commands = {expected: decision["steps"], **({active["command_id"]: active_steps} if active else {})}
                                ack = self.io.call("/run", {
                                    "steps": decision["steps"], "replace": True, "wait": False,
                                    "start_tick": start_tick,
                                    "command_id": expected, "goal": goal_id, "session": session,
                                    "expiry_tick": start_tick + ticks + fast.REVIEW_TICKS,
                                }, timeout=4)
                    deadline_missed = timing.now() - timing.start > REVIEW_SECONDS
                    if self.draw:
                        with timing.span("overlay.build"):
                            layer = overlay.draw(goal, state, plan, decision, episode)
                        with timing.span("overlay.send"):
                            try:
                                self.io.call("/overlay", layer, timeout=1)
                            except (OSError, RuntimeError) as error:
                                decision["overlay_error"] = str(error)
                with timing.span("wait"):
                    if not check:
                        if ack and "steps" in decision:
                            delay = (max(0, start_tick - ack["now"]) + min(ticks, fast.REVIEW_TICKS)) / 60
                        elif busy:
                            delay = 1 / 60 if execution["pending"] else min(fast.REVIEW_TICKS / 60, max(1 / 60, REVIEW_SECONDS - (timing.now() - timing.start)))
                        else:
                            delay = 1 / 60
                        time.sleep(delay)
                with timing.span("record.prepare"):
                    effects["life"] = state["body"]["life"]
                    effects["cells"] = [[*cell, after[0], after[3], after[4]] for cell, (_, after) in changes.items()]
                    effects["bag"] = [[i["name"], i["stack"]] for i in state["bag"]]
                    route = {"ticks": plan.ticks, "frontier": plan.explore, "refresh": replan, "air_detour": plan.dry} if plan else None
                row = {"type": "round", "goal_id": goal_id, "round": round_no, "tick": state["tick"],
                       "session": session, "start_ms": round((timing.start - started) * 1000, 3),
                       "body": state["body"], "execution": execution, "observed": effects,
                       "measurement": measurement, "decision": decision, "ack": ack,
                       "route": route, "enemies": state["enemies"], "deadline_missed": deadline_missed}
                row["spans"] = timing.spans
                record(row)
                # The sink is timed separately so the row can contain its own write time.
                end = timing.now()
                record({"type": "round_end", "round": round_no, "goal_id": goal_id,
                        "record_ms": (end - timing.start) * 1000 - timing.spans[-1]["end_ms"],
                        "round_ms": (end - timing.start) * 1000})
                if check:
                    result = {"status": check[0], "reason": check[1], "ticks": state["tick"] - first["tick"],
                              "rounds": round_no - first_round + 1, "life": state["body"]["life"], "goal_id": goal_id}
                    if next_goal is not None and check[0] == "done" and not goal.get("waiting"):
                        if on_result:
                            on_result(result, state)
                        goal = first = None
                        continue
                    break
        except (OSError, URLError, RuntimeError) as error:
            result = {"status": "failed", "reason": str(error), "ticks": state["tick"] - first["tick"] if first else 0,
                      "rounds": round_no, "life": state["body"]["life"] if state else None, "goal_id": goal_id}
            record({"type": "round_error", "goal_id": goal_id, "round": round_no, "error": str(error),
                    "spans": timing.spans})
        finally:
            if own:
                cleanup = Timing()
                with cleanup.span("command.cancel"):
                    try:
                        self.io.call("/run/cancel", {}, timeout=2)
                    except (OSError, RuntimeError) as error:
                        record({"type": "cleanup_error", "goal_id": goal_id, "error": str(error)})
                if self.draw:
                    with cleanup.span("overlay.clear"):
                        try:
                            self.io.call("/overlay", {"layer": overlay.LAYER, "clear": True}, timeout=1)
                        except (OSError, RuntimeError) as error:
                            record({"type": "cleanup_error", "goal_id": goal_id, "error": str(error)})
                record({"type": "cleanup", "goal_id": goal_id, "spans": cleanup.spans})
        return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal", type=Path, help="JSON file with one goal")
    parser.add_argument("--world", required=True, help="expected world name")
    parser.add_argument("--no-overlay", action="store_true")
    args = parser.parse_args()
    brain = Loop(draw=not args.no_overlay, world_name=args.world)
    result = brain.run(json.loads(args.goal.read_text()), lambda row: print(json.dumps(row), flush=True))
    print(json.dumps({"type": "result", "result": result}))
    sys.exit(result["status"] != "done")
