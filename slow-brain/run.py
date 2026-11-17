"""Keep the live brain running while a separate planning agent supplies goals."""
import argparse
from collections import deque
import gzip
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "live-brain"), str(ROOT)]
from loop import Loop
from mod import client
import goals
import hud


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    temporary.replace(path)


def validate(plan):
    """Reject malformed batches before they can reach the live loop."""
    if "reason" in plan and not isinstance(plan["reason"], str):
        raise ValueError("reason must be text")
    batch = plan.get("goals")
    if not isinstance(batch, list) or not batch:
        raise ValueError("goals must be a nonempty list")
    for goal in batch:
        if not isinstance(goal, dict) or goal.get("kind") not in goals.KINDS:
            raise ValueError("unknown goal kind")
        def number(name, default=None):
            value = goal.get(name, default)
            if type(value) is not int or value <= 0:
                raise ValueError(name + " must be a positive integer")
        def text(name):
            if not isinstance(goal.get(name), str) or not goal[name]:
                raise ValueError(name + " must be a nonempty string")
        def coordinates(name, size):
            value = goal.get(name)
            if not isinstance(value, (list, tuple)) or len(value) != size or any(type(n) is not int for n in value):
                raise ValueError(name + " has invalid coordinates")
            if size == 4 and (value[0] > value[2] or value[1] > value[3]):
                raise ValueError("area bounds are reversed")
        number("limit_ticks")
        if "label" in goal:
            text("label")
        if goal.get("waiting"):
            raise ValueError("waiting is reserved for the live brain")
        kind = goal["kind"]
        if kind == "reach":
            coordinates("area", 4) if "area" in goal else coordinates("cell", 2)
        elif kind == "change":
            cells = goal.get("cells")
            if not isinstance(cells, list) or not cells:
                raise ValueError("change needs cells")
            for cell in cells:
                if not isinstance(cell, list) or len(cell) != 3 or any(type(n) is not int for n in cell) or cell[2] < -1:
                    raise ValueError("change cells must be [x, y, tile], with -1 for removal")
        elif kind in ("quantity", "defeat"):
            text("item" if kind == "quantity" else "enemy")
            number("count", 1 if kind == "defeat" else None)
        elif kind == "search":
            coordinates("area", 4)
            text("tile")
        elif kind == "maintain":
            coordinates("area", 4)
            number("ticks")
        else:
            operation = goal.get("operation")
            if operation not in ("craft", "wear", "door", "chest", "life_crystal", "use"):
                raise ValueError("unknown interaction")
            if operation in ("craft", "wear", "use"):
                text("item")
            if operation in ("door", "chest"):
                coordinates("cell", 2)
            if operation == "door":
                text("tile")
            if operation == "craft":
                number("count", 1)
            if operation == "use":
                if "consume" in goal:
                    number("consume")
                elif type(goal.get("projectile")) is not int or goal["projectile"] < 0:
                    raise ValueError("use needs consume or a projectile ID")
                number("count", 1)
    return batch


def death_result(result, state, deaths_before):
    """A command can end on the death tick, before the last read showed death."""
    if (state["body"]["dead"] or state.get("deaths", {}).get("count", 0) > deaths_before):
        return dict(result, status="died", reason=state.get("deaths", {}).get("last_line") or "character died")
    return result


class Queue:
    def __init__(self, folder, objective, strategy, log):
        self.folder, self.objective, self.strategy, self.log = folder, objective, strategy, log
        self.pending, self.batches, self.history = deque(), deque(), []
        self.active = self.request = None
        self.reason = "initial"
        self.started = time.monotonic()
        self.requests = self.accepted = 0
        self.last_planning_seconds = None
        self.checkpoint = self.recovery = None

    def accept(self):
        path = self.folder / "response.json"
        if not path.exists():
            return
        try:
            response = json.loads(path.read_text())
            if not self.request or response.get("request_id") != self.request["request_id"]:
                self.log({"type": "response_discarded", "reason": "the plan it continues is no longer current"})
                return
            batch = validate(response)
            self.pending.extend(batch)
            self.batches.append({"remaining": len(batch), "reason": response.get("reason", "")})
            self.accepted += 1
            self.last_planning_seconds = time.monotonic() - self.request["started"]
            self.log({"type": "batch", "request_id": response["request_id"], "goals": batch,
                      "reason": response.get("reason"), "planning_seconds": self.last_planning_seconds})
            write(self.folder / "request.json", dict(self.request, status="answered"))
            self.request = None
        except (ValueError, TypeError, AttributeError) as error:
            self.log({"type": "response_invalid", "reason": str(error)})
            self.cancel_request()
            self.reason = "invalid response: " + str(error)
        finally:
            path.unlink()

    def next(self):
        self.accept()
        self.active = self.pending.popleft() if self.pending else None
        return self.active

    def began(self, goal, baseline):
        if goal == self.active:
            reason = self.recovery["reason"] if self.recovery else self.batches[0]["reason"] if self.batches else ""
            self.checkpoint = {"goal": goal, "baseline": baseline, "reason": reason}

    def resume(self, state):
        if not self.recovery:
            return None, None
        self.active = self.recovery["goal"]
        baseline = self.recovery["baseline"] if self.active["kind"] != "maintain" else goals.begin(self.active, state)
        self.checkpoint = dict(self.recovery, baseline=baseline)
        self.log({"type": "goal_resumed", "goal": self.active, "baseline": baseline, "tick": state["tick"]})
        return self.active, baseline

    def replacement(self, state):
        if not self.recovery:
            return None
        self.accept()
        if not self.pending:
            return None
        self.log({"type": "goal_replaced", "goal": self.active, "tick": state["tick"]})
        self.recovery = self.checkpoint = None
        return self.next()

    def completed(self, result, state):
        self.history.append({"goal": self.active, "result": result, "tick": state["tick"]})
        self.log({"type": "goal_result", **self.history[-1]})
        if self.active and self.batches and not self.recovery:
            self.batches[0]["remaining"] -= 1
            if self.batches[0]["remaining"] == 0:
                self.batches.popleft()
        self.active = None
        self.checkpoint = self.recovery = None

    def cancel_request(self):
        if self.request:
            write(self.folder / "request.json", dict(self.request, status="cancelled"))
        self.request = None

    def failed(self, result, state):
        skipped = list(self.pending)
        recovery = self.checkpoint if result["status"] == "died" else None
        self.completed(result, state)
        self.pending.clear()
        self.batches.clear()
        self.cancel_request()
        self.reason = "replan after " + result["status"]
        self.recovery = recovery
        self.log({"type": "chain_cancelled", "skipped": skipped, "result": result})

    def publish(self, state, world):
        if state.get("body", {}).get("dead"):
            return
        self.accept()
        # Start planning as a batch begins; keep at most one future batch ready.
        if self.request or len(self.batches) >= 2:
            return
        request_id = uuid4().hex
        context = {"objective": self.objective, "strategy": self.strategy,
                   "last_planning_seconds": self.last_planning_seconds,
                   "resuming_after_death": bool(self.recovery),
                   "active_baseline": self.checkpoint["baseline"] if self.checkpoint else None,
                   "active_goal": self.active, "queued_goals": list(self.pending),
                   "recent_results": self.history[-10:], "state": state,
                   "map": {"kinds": world.kinds, "cells": [[x, y, *v] for (x, y), v in world.cells.items()]}}
        write(self.folder / "context.json", context)
        self.request = {"request_id": request_id, "status": "open", "started": time.monotonic(),
                        "reason": "replan after death; replace the resumed task" if self.recovery else
                                  self.reason if not self.active and not self.pending else "continue after the current goals succeed",
                        "context": "context.json"}
        self.reason = "queue needs more goals"
        self.requests += 1
        write(self.folder / "request.json", self.request)
        self.log({"type": "planning_requested", **self.request})

    def status(self, state, phase):
        b = state["body"] if state else {}
        return {"phase": phase, "world": state.get("world_name") if state else None,
                "tick": state.get("tick") if state else None, "life": b.get("life"),
                "cell": [int(b["x"] // 16), int((b["y"] + b["h"] - 1) // 16)] if b else None,
                "active_goal": self.active, "queued_goals": list(self.pending),
                "plan": self.recovery["reason"] if self.recovery else self.batches[0]["reason"] if self.batches else None,
                "resuming_after_death": bool(self.recovery),
                "ready_batches": len(self.batches),
                "request_id": self.request["request_id"] if self.request else None,
                "requests": self.requests, "accepted_batches": self.accepted,
                "completed": sum(r["result"]["status"] == "done" for r in self.history),
                "failed": sum(r["result"]["status"] != "done" for r in self.history),
                "elapsed_seconds": round(time.monotonic() - self.started, 2)}


def start(args):
    folder = args.run.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "status.json").exists():
        raise ValueError("choose a new run directory")
    latest = None
    deaths_before = None
    last_drawing = None
    screen = client.call("/camera", {}).get("screen", [1280, 720]) if not args.no_overlay else None
    with gzip.open(folder / "play.jsonl.gz", "wt", compresslevel=1) as stream:
        def log(row):
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            stream.flush()
        queue = Queue(folder, args.objective, args.strategy, log)
        def stopping():
            return (folder / "STOP").exists() or args.seconds is not None and time.monotonic() - queue.started >= args.seconds
        def record(row):
            nonlocal last_drawing
            if row["type"] == "goal":
                queue.began(row["goal"], row["baseline"])
            if row["type"] == "round":
                log({"type": "observation", "state": latest})
                queue.publish(latest, brain.world)
                write(folder / "status.json", queue.status(latest, "playing" if queue.active else "surviving while waiting"))
                if screen:
                    drawing = hud.draw(queue, screen)
                    if drawing != last_drawing:
                        try:
                            client.call("/overlay", drawing, timeout=1)
                            last_drawing = drawing
                        except (OSError, RuntimeError) as error:
                            log({"type": "overlay_error", "reason": str(error)})
            log(row)
            if stopping():
                raise KeyboardInterrupt
        class ObservedLoop(Loop):
            def read(self):
                nonlocal latest, deaths_before
                latest = super().read()
                if deaths_before is None:
                    deaths_before = latest.get("deaths", {}).get("count", 0)
                return latest
        brain = ObservedLoop(io=client, world_name=args.world, draw=not args.no_overlay)
        try:
            while not stopping():
                deaths_before = None
                goal, baseline = queue.resume(latest) if latest else (None, None)
                result = brain.run(goal, record, next_goal=queue.next, on_result=queue.completed,
                                   initial_baseline=baseline, replace_goal=queue.replacement)
                brain.read()
                if latest is None or latest["world_name"] != args.world:
                    raise RuntimeError(result["reason"])
                result = death_result(result, latest, deaths_before)
                queue.failed(result, latest)
                # Respawn is the game's job. Resume unfinished work while a replacement is planned.
                while latest["body"]["dead"] and not stopping():
                    write(folder / "status.json", queue.status(latest, "respawning"))
                    time.sleep(.25)
                    brain.read()
                if result["status"] == "failed":
                    time.sleep(.25)
        except KeyboardInterrupt:
            pass
        finally:
            queue.cancel_request()
            if screen:
                try:
                    client.call("/overlay", {"layer": hud.LAYER, "clear": True}, timeout=1)
                except (OSError, RuntimeError):
                    pass
            write(folder / "status.json", queue.status(latest, "stopped"))
            log({"type": "stopped", "history": queue.history})
            if latest and latest["world_name"] == args.world:
                write(folder / "final-state.json", client.state(w=90, h=50))
                client.call("/save", {})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("start")
    launch.add_argument("--world", required=True)
    launch.add_argument("--run", type=Path, required=True)
    launch.add_argument("--objective", default="Beat the Moon Lord from this starting position.")
    launch.add_argument("--strategy", default="Make progress with the resources available. Learn from observed outcomes and adapt your plan.")
    launch.add_argument("--seconds", type=float, help="Optional wall-clock duration for an attended trial.")
    launch.add_argument("--no-overlay", action="store_true")
    for name in ("wait", "submit", "status", "stop"):
        command = sub.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
        if name == "wait":
            command.add_argument("--after", default="")
            command.add_argument("--seconds", type=float, default=30)
        if name == "submit":
            command.add_argument("--request", required=True)
            command.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "start":
        start(args)
    elif args.command == "stop":
        (args.run / "STOP").touch()
    elif args.command == "status":
        print((args.run / "status.json").read_text())
    elif args.command == "submit":
        request = json.loads((args.run / "request.json").read_text())
        if request["status"] != "open" or request["request_id"] != args.request:
            raise ValueError("request is no longer current; wait for the new request")
        response = json.loads(args.plan.read_text())
        validate(response)
        if (args.run / "response.json").exists():
            raise ValueError("a response is already waiting to be read")
        write(args.run / "response.json", dict(response, request_id=args.request))
        print("submitted", args.request)
    else:
        until = time.monotonic() + args.seconds
        while time.monotonic() < until:
            path = args.run / "request.json"
            if path.exists():
                request = json.loads(path.read_text())
                if request["status"] == "open" and request["request_id"] != args.after:
                    print(json.dumps(request))
                    return
            status = args.run / "status.json"
            if status.exists() and json.loads(status.read_text())["phase"] == "stopped":
                print("stopped")
                return
            time.sleep(.2)
        print("no new request yet")


if __name__ == "__main__":
    main()
