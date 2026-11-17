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
from rules.hands import in_reach
from rules.projectiles import observe_shapes

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


from rules.motion import observe as observe_motion


class Loop:
    def __init__(self, io=client, draw=True, world_name=None):
        self.io = io
        self.draw = draw
        self.world_name = world_name
        self.world = Map()
        self.session = None

    def read(self):
        state = self.io.call("/state?w=90&h=50&attack_shapes=1&shape_step=16", timeout=4)
        if "can_break" not in state["cells"]:
            raise RuntimeError("update AgentIO: per-cell mining permissions are required")
        return state

    def run(self, goal, record, episode=None, *, next_goal=None, on_result=None, stop_waiting=None,
            initial_baseline=None, replace_goal=None):
        """Run one goal, or stay active when a caller supplies a goal source."""
        if goal is None and next_goal is None:
            raise ValueError("expected a goal or goal source")
        if goal is not None and (goal["kind"] not in goals.KINDS or goal["limit_ticks"] <= 0):
            raise ValueError("expected a known goal and positive limit_ticks")
        goal_id = uuid4().hex
        plan = signature = baseline = first = previous = None
        air_route = air_signature = None
        expected = None
        commands = {}
        send_seconds = 1 / 60
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
                if state.get("attack_shapes"):
                    with timing.span("attack_shapes.observe"):
                        state = observe_shapes(state)
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
                with timing.span("motion.observe"):
                    state = observe_motion(state)
                with timing.span("goal.select"):
                    incoming = replace_goal(state) if replace_goal and not state["body"]["dead"] else None
                    if incoming is None and not state["body"]["dead"] and (goal is None or goal.get("waiting")):
                        incoming = next_goal()
                    if incoming is not None:
                        if incoming["kind"] not in goals.KINDS or incoming["limit_ticks"] <= 0:
                            raise ValueError("expected a known goal and positive limit_ticks")
                        goal = incoming
                        first = plan = signature = initial_baseline = None
                    elif goal is None:
                        goal = goals.waiting_goal(state)
                    if first is None:
                        goal_id = uuid4().hex
                        baseline = goals.begin(goal, state) if initial_baseline is None else initial_baseline
                        initial_baseline = None
                        goal_started = timing.now()
                        first_round = round_no
                        record({"type": "goal", "goal": goal, "baseline": baseline, "state": state})
                with timing.span("map.update"):
                    changes = self.world.update(state["cells"])
                    if air_route:
                        grid = air_route.grid
                        if any(grid.x0 <= x < grid.x0 + grid.w and grid.y0 <= y < grid.y0 + grid.h
                               for x, y in changes):
                            air_signature = None
                with timing.span("effects.observe"):
                    execution = state["execution"]
                    if first is None:
                        first = state
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
                    if not check and not goal.get("waiting") and time.monotonic() - goal_started > goal["limit_ticks"] / 60 + 30:
                        check = "failed", "game clock stopped or ran too slowly"
                    if not check and goal.get("waiting") and stop_waiting and stop_waiting(state):
                        check = "stopped", "waiting period ended"
                    finished = execution.get("finished")
                    if not check and expected and not any(e and e["command_id"] == expected for e in execution.values()):
                        check = "failed", "the mod lost the last command"
                    if finished and finished["command_id"] == expected and finished["status"] != "finished":
                        check = check or ("failed", "command " + finished["status"])
                decision = {}
                ack = None
                measurement = None
                busy = execution["pending"] or execution["active"]
                deadline_missed = False
                replan = None
                if not check and not execution["pending"]:
                    with timing.span("pathfinder.inputs"):
                        target, area, approach = destination(goal, state, self.world)
                        bag, kit = fast.items_for(state, goal)
                        if approach and goal["kind"] == "change":
                            uses = fast.tile_uses(goals.wrong_cells(goal, self.world), self.world, bag)
                            kit["target_boost"] = max((use[1].get("tile_boost", 0) for use in uses
                                                       if use[3] == target), default=0)
                        phys = dict(state["physics"], defense=state["body"]["defense"])
                        # Slot moves and irrelevant inventory changes leave the route valid.
                        relevant = tuple((k, v["id"], v["use_time"], v["tile_boost"]) if isinstance(v, dict) else (k, v)
                                         for k, v in kit.items())
                        key = (target, area, approach, self.world.version, relevant, phys)
                        body = state["body"]
                        at = plan.at(*plan.node_of(body)) if plan else None
                        off_route = plan and body["ground"] and (at is None or
                            approach and at == 0 and not in_reach(body, target, phys, kit.get("target_boost", 0)))
                        wet_head = pathfinder.head_underwater(self.world, body)
                        box = state["cells"]
                        water_near = not phys.get("gills") and (wet_head or any(
                            amount and kind == 0 and seen
                            for amounts, kinds, visibility in zip(box["liquid"], box["liquid_kind"], box["seen"])
                            for amount, kind, seen in zip(amounts, kinds, visibility)))
                        keep = goal.get("protect", ())
                        air_key = (relevant, phys, keep)
                        air_replan = plan and (plan.dry and not wet_head or
                            not plan.dry and wet_head and not phys.get("gills") and
                            pathfinder.air_needed(plan, body) >= body["breath"] * pathfinder.AIR_TICKS_PER_POINT)
                    if water_near:
                        x, _, _, y = cells_of(body)
                        if (air_key != air_signature or abs(x - air_route.cell[0]) > 20
                                or abs(y - air_route.cell[1]) > 12):
                            with timing.span("pathfinder.air"):
                                air_route = pathfinder.plan_to_air(self.world, body, kit, phys, keep)
                            air_signature = air_key
                    else:
                        air_route = air_signature = None
                    if target is not None and (key != signature or off_route or air_replan):
                        replan = "no route" if signature is None else (
                            "destination changed" if key[:3] != signature[:3] else
                            "terrain knowledge changed" if key[3] != signature[3] else
                            "capabilities changed" if key[4:] != signature[4:] else "air budget changed" if air_replan else "outside route")
                        with timing.span("pathfinder.plan"):
                            plan = pathfinder.breathable_plan(self.world, target, body, kit, phys, area=area,
                                                   approach=approach, keep=keep, air_route=air_route,
                                                   search_area=goal["area"] if goal["kind"] == "search" else None)
                            signature = key
                    if target is None:
                        plan = signature = None
                    with timing.span("goal.measure"):
                        measurement = goals.measure(goal, state, self.world, baseline, pathfinder.estimate(plan, body))
                    # Planning can run while the last script still holds the buttons. Schedule
                    # the replacement far enough ahead to forecast from its likely start.
                    # Use the review interval as the planning window. Adapting
                    # it to the previous compute time shrank both search and
                    # its next budget until only a few options were considered.
                    lead = fast.REVIEW_TICKS
                    start_tick = state["tick"] + lead
                    active = execution["active"]
                    active_steps = commands.get(active["command_id"], []) if active else []
                    applied_tick = active["applied_tick"] if active else state["tick"] + 1
                    with timing.span("fast.project"):
                        projected = fast.project(state, self.world, lead, active_steps, applied_tick)
                    continuing = fast.pressed_ticks(active_steps)[max(0, start_tick - applied_tick + 1):]
                    # The scheduled handoff, not just the review interval, limits
                    # thinking time. Reserve the measured send time and a game tick.
                    cutoff = timing.start + (lead - 1) / 60
                    deadline = min(timing.start + REVIEW_SECONDS, cutoff - send_seconds)
                    decision = fast.decide(projected, self.world, goal, baseline, plan, target, timing,
                                           deadline, continuing=continuing, observed_body=body,
                                           air_route=air_route, reaction_ticks=lead + fast.REVIEW_TICKS)
                    decision["forecast_start_tick"] = start_tick
                    decision["evaluation_deadline_ms"] = round((deadline - timing.start) * 1000, 3)
                    sent = timing.now()
                    with timing.span("command.submit"):
                        if "endpoint" in decision:
                            if active:
                                self.io.call("/run/cancel", {}, timeout=2)
                            ack = self.io.call(decision["endpoint"], decision["payload"], timeout=6)
                            expected = None
                            if ack.get("made") == 0 and ack.get("stopped"):
                                check = ("failed", ack["stopped"])
                            if decision["endpoint"] == "/chest":
                                check = goals.chest_result(ack)
                        elif "steps" in decision:
                            ticks = sum(step["ticks"] for step in decision["steps"])
                            command_id = f"{goal_id}:{round_no}"
                            ack = self.io.call("/run", {
                                "steps": decision["steps"], "replace": True, "wait": False,
                                "start_tick": start_tick,
                                "command_id": command_id, "goal": goal_id, "session": session,
                                "expiry_tick": start_tick + ticks + fast.REVIEW_TICKS,
                            }, timeout=4)
                            if ack.get("accepted") is False:
                                decision["submission"] = "kept current command because the game tick was missed"
                            else:
                                expected = command_id
                                commands = {expected: decision["steps"], **({active["command_id"]: active_steps} if active else {})}
                    if ack:
                        send_seconds = max(1 / 60, timing.now() - sent)
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
                        if ack and ack.get("accepted") is False:
                            delay = 0  # Take a fresh state after a game-clock rejection.
                        elif ack and "steps" in decision:
                            delay = max(0, min(ticks, fast.REVIEW_TICKS) / 60 - (timing.now() - timing.start))
                        elif busy:
                            delay = 1 / 60 if execution["pending"] else min(fast.REVIEW_TICKS / 60, max(1 / 60, REVIEW_SECONDS - (timing.now() - timing.start)))
                        else:
                            delay = 1 / 60
                        time.sleep(delay)
                failure_blockers = None
                if check and check[0] in ("failed", "timeout"):
                    with timing.span("goal.explain"):
                        failure_blockers = goals.blockers(goal, state, self.world)
                with timing.span("record.prepare"):
                    effects["life"] = state["body"]["life"]
                    effects["light"] = state.get("light")
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
                    if check[0] in ("failed", "timeout"):
                        result["blockers"] = failure_blockers
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
