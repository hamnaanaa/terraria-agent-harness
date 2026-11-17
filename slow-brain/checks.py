"""Check queue continuity, invalidation and stopping without a running game."""
from argparse import Namespace
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import run


def reach(x=2):
    return {"kind": "reach", "cell": [x, 8], "limit_ticks": 60}


def queue_check():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        events = []
        q = run.Queue(folder, "win", "adapt", events.append)
        world = run.Loop().world
        state = {"tick": 1, "body": {"life": 100}}
        q.publish(state, world)
        initial = q.request["request_id"]
        run.write(folder / "response.json", {"request_id": initial, "goals": [reach(2), reach(3), reach(4)]})
        assert q.next() == reach(2)
        q.publish(state, world)
        assert q.request is not None  # Plan ahead at the start, before the first goal finishes.
        q.completed({"status": "done"}, state)
        assert q.next() == reach(3)
        context = json.loads((folder / "context.json").read_text())
        assert context["active_goal"] == reach(2) and context["queued_goals"] == [reach(3), reach(4)]
        assert context["last_planning_seconds"] == q.last_planning_seconds > 0
        continuation = q.request["request_id"]
        blocker = {"cell": [2, 8], "reason": "no support", "source": "placement_rule"}
        q.failed({"status": "timeout", "reason": "blocked", "blockers": [blocker]}, state)
        run.write(folder / "response.json", {"request_id": continuation, "goals": [reach(5)]})
        assert q.next() is None and not q.pending
        q.publish(state, world)
        fresh = json.loads((folder / "context.json").read_text())
        assert fresh["recent_results"][-1]["result"]["reason"] == "blocked"
        assert fresh["recent_results"][-1]["result"]["blockers"] == [blocker]
        assert q.request["request_id"] != continuation
        assert any(e["type"] == "response_discarded" for e in events)


def append_check():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        q = run.Queue(folder, "win", "adapt", lambda _: None)
        q.active = reach(2)
        q.pending.append(reach(3))
        q.publish({"tick": 1}, run.Loop().world)
        run.write(folder / "response.json", {"request_id": q.request["request_id"], "goals": [reach(4), reach(5)]})
        q.accept()
        assert list(q.pending) == [reach(3), reach(4), reach(5)]


def lookahead_check():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        q = run.Queue(folder, "win", "adapt", lambda _: None)
        state, world = {"tick": 1}, run.Loop().world
        q.publish(state, world)
        run.write(folder / "response.json", {"request_id": q.request["request_id"], "goals": [reach(2), reach(3)]})
        q.next()
        q.publish(state, world)
        second_request = q.request["request_id"]
        run.write(folder / "response.json", {"request_id": second_request, "goals": [reach(4), reach(5)]})
        q.accept()
        q.publish(state, world)
        assert q.request is None and len(q.batches) == 2
        q.completed({"status": "done"}, state)
        assert q.next() == reach(3)
        q.completed({"status": "done"}, state)
        assert q.next() == reach(4)
        q.publish(state, world)
        assert q.request and q.request["request_id"] != second_request
        q.failed({"status": "timeout"}, state)
        assert not q.batches and not q.pending


def hud_check():
    with tempfile.TemporaryDirectory() as directory:
        q = run.Queue(Path(directory), "win", "adapt", lambda _: None)
        q.active = dict(reach(), label="Reach the observed cave entrance")
        q.pending.append({"kind": "quantity", "item": "Wood", "count": 20})
        q.batches.append({"remaining": 2, "reason": "Explore the cave for useful ore."})
        q.request = {"started": run.time.monotonic(), "request_id": "next"}
        drawing = run.hud.draw(q, [1920, 1080])
        text = "\n".join(r["text"] for r in drawing["labels"])
        assert "Explore the cave" in text and "cave entrance" in text and "Collect 20 Wood" in text
        assert "Planning what comes next" in text
        assert all(1100 <= r["at"][0] < 1920 and 600 <= r["at"][1] < 1080 for r in drawing["labels"])


def validation_check():
    for bad in ({"goals": []}, {"goals": [dict(reach(), waiting=True)]},
                {"goals": [dict(reach(), limit_ticks=-1)]},
                {"goals": [{"kind": "interact", "operation": "use", "item": "Potion", "limit_ticks": 60}]}):
        try:
            run.validate(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        q = run.Queue(folder, "win", "adapt", lambda _: None)
        q.publish({"tick": 1}, run.Loop().world)
        old = q.request["request_id"]
        run.write(folder / "response.json", {"request_id": old, "goals": [dict(reach(), kind="fly_forever")]})
        q.accept()
        assert not q.pending
        q.publish({"tick": 2}, run.Loop().world)
        assert q.request["request_id"] != old and "invalid response" in q.request["reason"]


def controller_check():
    # Reuse the live brain's observation fixture; exercise the real Loop and queue together.
    sys.path.insert(0, str(run.ROOT / "gym/live-brain"))
    spec = importlib.util.spec_from_file_location("component_checks", run.ROOT / "gym/live-brain/checks.py")
    component = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(component)
    state, calls = component.observation(), []
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        class IO:
            def call(self, path, payload=None, timeout=None):
                calls.append(path)
                if path.startswith("/state"):
                    state["tick"] += 1
                    if state["tick"] == 6:
                        request = json.loads((folder / "request.json").read_text())
                        run.write(folder / "response.json", {"request_id": request["request_id"], "goals": [reach(), reach()]})
                    if state["tick"] >= 12:
                        (folder / "STOP").touch()
                    return copy.deepcopy(state)
                if path == "/run":
                    state["execution"]["active"] = {"command_id": payload["command_id"], "applied_tick": payload["start_tick"] + 1}
                    return {"now": state["tick"]}
                return {}
            def state(self, **box):
                return self.call("/state")
        args = Namespace(run=folder, world="Gym", objective="win", strategy="adapt", seconds=None, no_overlay=True)
        with patch.object(run, "client", IO()), patch.object(run.time, "sleep"), \
                patch.object(component.fast, "project", side_effect=lambda state, *_: state), \
                patch.object(component.fast, "decide", return_value={"steps": [{"keys": [], "ticks": 60}]}):
            run.start(args)
        status = json.loads((folder / "status.json").read_text())
        assert status["phase"] == "stopped" and status["completed"] == 2, status
        assert calls.count("/run/cancel") == 1 and calls.count("/save") == 1, calls
        with gzip.open(folder / "play.jsonl.gz", "rt") as stream:
            rows = [json.loads(line) for line in stream]
        goals = [r["goal"]["kind"] for r in rows if r["type"] == "goal"]
        assert goals == ["maintain", "reach", "reach", "maintain"], goals
        assert sum(r["type"] == "planning_requested" for r in rows) == 2


def recovery_check():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        q = run.Queue(folder, "win", "adapt", lambda _: None)
        goal = {"kind": "quantity", "item": "Wood", "count": 20, "gain": True, "limit_ticks": 600}
        q.active = goal
        q.batches.append({"remaining": 2, "reason": "Prepare a home"})
        q.pending.append(reach())
        q.began(goal, 7)
        state = {"tick": 100, "body": {"dead": True, "life": 0}}
        q.failed({"status": "died"}, state)
        q.publish(state, run.Loop().world)
        assert not q.pending and q.request is None
        state["body"].update(dead=False, life=100)
        assert q.resume(state) == (goal, 7)  # Target remains 27 total, not 20 more after respawning.
        q.publish(state, run.Loop().world)
        assert "replace" in q.request["reason"]
        context = json.loads((folder / "context.json").read_text())
        assert context["resuming_after_death"] and context["active_baseline"] == 7
        run.write(folder / "response.json", {"request_id": q.request["request_id"], "goals": [reach(4), reach(5)]})
        assert q.replacement(state) == reach(4)
        q.began(reach(4), state["tick"])
        q.completed({"status": "done"}, state)
        assert q.next() == reach(5) and q.batches[0]["remaining"] == 1
        q.began(reach(5), state["tick"])
        q.failed({"status": "failed", "reason": "missing materials"}, state)
        assert q.resume(state) == (None, None)
        # Completing old work must not consume an item from a newly buffered batch.
        q.recovery = {"goal": goal, "baseline": 7, "reason": "Prepare a home"}
        q.active = goal
        q.batches.append({"remaining": 2, "reason": "new plan"})
        q.completed({"status": "done"}, state)
        assert q.batches[0]["remaining"] == 2


def death_race_check():
    failure = {"status": "failed", "reason": "/run: command session ended"}
    alive = {"body": {"dead": False}, "deaths": {"count": 2, "last_line": "slain by a Demon Eye"}}
    assert run.death_result(failure, alive, 1)["status"] == "died"
    assert run.death_result(failure, alive, 2) == failure
    assert run.death_result(failure, dict(alive, body={"dead": True}), 2)["status"] == "died"


def recovery_loop_check():
    spec = importlib.util.spec_from_file_location("component_checks", run.ROOT / "gym/live-brain/checks.py")
    component = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(component)
    state = component.observation()
    state["bag"] = [{"name": "Wood", "stack": 27}]
    state["progress"] = {"life_max_perm": 120}
    state["counters"]["kills_by_name"] = {"Slime": 3}
    cases = [({"kind": "quantity", "item": "Wood", "count": 20, "gain": True}, 7),
             ({"kind": "interact", "operation": "craft", "item": "Wood", "count": 20}, 7),
             ({"kind": "defeat", "enemy": "Slime", "count": 2}, 1),
             ({"kind": "interact", "operation": "life_crystal"}, 100),
             ({"kind": "interact", "operation": "use", "item": "Wood", "consume": 1}, 28)]
    calls = []
    class IO:
        def call(self, path, payload=None, timeout=None):
            calls.append(path)
            return copy.deepcopy(state) if path.startswith("/state") else {}
    for goal, baseline in cases:
        outcome = run.Loop(IO(), draw=False).run(dict(goal, limit_ticks=60), lambda _: None, initial_baseline=baseline)
        assert outcome["status"] == "done", (goal, outcome)
    assert "/run" not in calls  # Original targets already reached; no repeated work.
    # A replacement starts a fresh baseline, rather than inheriting the interrupted task's target.
    replacement = dict(cases[0][0], count=1, limit_ticks=1)
    supplied = iter([replacement, None])
    observed_baselines = []
    def checked(goal, snapshot, world, baseline, start):
        observed_baselines.append(baseline)
        return "stopped", "baseline inspected"
    with patch.object(run.goals, "check", side_effect=checked):
        run.Loop(IO(), draw=False).run(reach(20), lambda _: None, initial_baseline=7,
                                       replace_goal=lambda _: next(supplied))
    assert observed_baselines == [27]


def death_recovery_controller_check():
    spec = importlib.util.spec_from_file_location("component_checks", run.ROOT / "gym/live-brain/checks.py")
    component = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(component)
    state = component.observation()
    state["physics"].update(run_top=3, run_acc=.08, run_slow=.2, acc_run=3,
                            jump_hold=15, jump_speed=5.01, gravity=.4, fall_top=10)
    state["body"]["breath"] = 200
    state["deaths"] = {"count": 0}
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        class IO:
            def call(self, path, payload=None, timeout=None):
                if path.startswith("/state"):
                    state["tick"] += 1
                    tick = state["tick"]
                    if tick in (6, 12):
                        request = json.loads((folder / "request.json").read_text())
                        plan = [reach(20), reach(22)] if tick == 6 else [reach()]
                        run.write(folder / "response.json", {"request_id": request["request_id"], "goals": plan})
                    if tick == 8:
                        state["body"].update(dead=True, life=0)
                        state["deaths"]["count"] += 1
                    if tick == 10:
                        state["body"].update(dead=False, life=100)
                    if tick >= 14:
                        (folder / "STOP").touch()
                    return copy.deepcopy(state)
                if path == "/run":
                    state["execution"]["active"] = {"command_id": payload["command_id"], "applied_tick": payload["start_tick"] + 1}
                    return {"now": state["tick"]}
                if path == "/run/cancel":
                    state["execution"]["active"] = None
                return {}
            def state(self, **box):
                return self.call("/state")
        args = Namespace(run=folder, world="Gym", objective="win", strategy="adapt", seconds=None, no_overlay=True)
        with patch.object(run, "client", IO()), patch.object(run.time, "sleep"), \
                patch.object(component.fast, "project", side_effect=lambda snapshot, *_: snapshot), \
                patch.object(component.fast, "decide", return_value={"steps": [{"keys": [], "ticks": 60}]}):
            run.start(args)
        with gzip.open(folder / "play.jsonl.gz", "rt") as stream:
            rows = [json.loads(line) for line in stream]
        resumed = next(r for r in rows if r["type"] == "goal_resumed")
        assert resumed["goal"] == reach(20) and resumed["baseline"] == 6
        assert next(r for r in rows if r["type"] == "goal_replaced")["tick"] == 12
        recovery_request = next(r for r in rows[rows.index(resumed):] if r["type"] == "planning_requested")
        assert "replace" in recovery_request["reason"]
        after_respawn = [r for r in rows if r["type"] == "goal" and r["state"]["tick"] >= 10]
        assert [r["goal"] for r in after_respawn[:2]] == [reach(20), reach()]


if __name__ == "__main__":
    for check in (queue_check, append_check, lookahead_check, hud_check, validation_check, controller_check,
                  recovery_check, death_race_check, recovery_loop_check, death_recovery_controller_check):
        check()
        print(check.__name__, "passed")
