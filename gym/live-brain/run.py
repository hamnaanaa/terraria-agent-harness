"""List or run Gym episodes against the isolated live brain."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
from textwrap import wrap
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from episodes import EPISODES, EXTENDED
from support import setup
sys.path.insert(0, str(ROOT / "gym" / "episodes"))
from recorded import EPISODES as RECORDED, LAKE, VULTURE
sys.path.insert(0, str(ROOT / "live-brain"))
from loop import Loop
from mod import client
from overlay import LAYER


def show(title, text, result=None):
    lines = [title, *wrap(text, width=66)]
    if result:
        lines.append(result)
    client.call("/overlay", {"layer": LAYER, "labels": [
        {"at": [24, 120 + 24 * i], "text": line, "screen": True}
        for i, line in enumerate(lines)
    ]}, timeout=2)


def run_episode(name, folder, draw, heading=None):
    episode = (EPISODES | EXTENDED | RECORDED)[name]
    if heading:
        show(heading, episode["text"])
        time.sleep(2)
    if "setup" in episode:
        episode["setup"]()
    else:
        setup(episode)
    episode_label = f"{heading} · {episode['text']}" if heading else episode["text"]
    brain = Loop(draw=draw, world_name="Gym")
    if "memory" in episode:
        brain.session = brain.read()["session"]["loaded"]
        brain.world.update(episode["memory"])
    results, samples, misses, starts = [], {}, 0, {}
    path = folder / (name + ".jsonl")
    with path.open("x") as log:
        def record(row):
            nonlocal misses
            log.write(json.dumps(row, separators=(",", ":")) + "\n")
            if row["type"] == "round":
                misses += row["deadline_missed"]
                for span in row["spans"]:
                    samples.setdefault(span["phase"], []).append(span["end_ms"] - span["start_ms"])
                if row["ack"] and row["ack"].get("ticks"):
                    starts[row["goal_id"] + ":" + str(row["round"])] = row["tick"]
                finished = row["execution"]["finished"]
                if finished and finished["command_id"] in starts and finished["applied_tick"]:
                    lag = finished["applied_tick"] - starts.pop(finished["command_id"])
                    samples.setdefault("state_to_applied_ticks", []).append(lag)
            elif row["type"] == "round_end":
                samples.setdefault("round", []).append(row["round_ms"])
                samples.setdefault("record", []).append(row["record_ms"])

        record({"type": "episode", "name": name, "description": episode["text"],
                **({"source": episode["source"]} if "source" in episode else {})})
        if "memory" in episode:
            record({"type": "memory", "cells": episode["memory"]})
        initial = None
        for step in episode["goals"]():
            before = brain.read()
            if initial is None:
                initial = before
            if len(step) == 3:
                endpoint, payload, verify = step
                record({"type": "goal", "goal": {"kind": "interact", "label": endpoint}, "state": before})
                if heading:
                    show(heading, episode["text"])
                try:
                    ack = client.call(endpoint, payload, timeout=5)
                    after = brain.read()
                    if endpoint == "/run":
                        record({"type": "probe", "trace": ack["trace"]})
                        evidence = verify(before, ack["trace"])
                        verified = evidence["verified"]
                        result = dict(status="done" if verified else "failed", **evidence)
                    else:
                        verified = verify(before, after)
                        result = {"status": "done", "reason": "observed inventory changes", "ack": ack}
                except (RuntimeError, OSError) as error:
                    after = brain.read()
                    verified = False
                    result = {"status": "failed", "reason": str(error)}
                result.update(ticks=after["tick"] - before["tick"], life=after["body"]["life"], verified=verified)
                results.append(result)
                record({"type": "result", "result": result, "state": after})
                if not verified:
                    break
                continue
            goal, verify = step
            verify_from = before
            if goal["kind"] == "defeat" and results:
                enemy = goal["enemy"]
                already = (before["counters"]["kills_by_name"].get(enemy, 0)
                           - initial["counters"]["kills_by_name"].get(enemy, 0))
                goal = dict(goal, count=max(0, goal["count"] - already))
                verify_from = initial
            record({"type": "goal", "goal": goal, "state": before})
            wait_ticks = episode.get("wait_ticks")
            if wait_ticks:
                arrived = None

                def reached(result, state):
                    nonlocal arrived
                    arrived = state
                    results.append(dict(result, verified=verify(before, state)))
                    record({"type": "result", "result": results[-1], "state": state})

                result = brain.run(goal, record, episode_label, next_goal=lambda: None, on_result=reached,
                                   stop_waiting=lambda state: state["tick"] - arrived["tick"] >= wait_ticks)
                after = brain.read()
                if arrived is not None:
                    verified = (result["status"] == "stopped" and not after["body"]["dead"]
                                and after["deaths"]["count"] == arrived["deaths"]["count"])
                    result = dict(result, status="done", reason="survived the waiting period") if verified else result
                else:
                    verified = False
            else:
                options = {}
                if goal.get("waiting"):
                    options = dict(next_goal=lambda: None, stop_waiting=lambda state:
                                   state["tick"] - before["tick"] >= goal["limit_ticks"])
                result = brain.run(goal, record, episode_label, **options)
                after = brain.read()
                verified = verify(verify_from, after)
            expected = goal.get("expected", "done")
            verified = verified and result["status"] == expected
            results.append(dict(result, verified=verified))
            record({"type": "result", "result": results[-1], "state": after})
            if not verified:
                break
    timings = {phase: {"n": len(values), "median": round(statistics.median(values), 2),
                       "p95": round(sorted(values)[(len(values) * 95 - 1) // 100], 2),
                       "max": round(max(values), 2)} for phase, values in samples.items()}
    return {"episode": name, "results": results, "timings": timings, "deadline_misses": misses, "log": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", nargs="*", metavar="episode")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--watch", action="store_true", help="show each test title and result for two seconds")
    args = parser.parse_args()
    catalog = EPISODES | EXTENDED | RECORDED
    numbers = {name: index for index, name in enumerate(catalog, 1)}
    if args.list or not args.episodes:
        for name, episode in catalog.items():
            print(f"{numbers[name]:02d}  {name:24s} {episode['text']}")
        return 0
    suites = {"regular": EPISODES, "extended": EXTENDED, "all": EPISODES | EXTENDED,
              "recorded": RECORDED, "lake": LAKE, "vulture": VULTURE}
    names = list(suites[args.episodes[0]]) if len(args.episodes) == 1 and args.episodes[0] in suites else args.episodes
    by_number = {f"{index:02d}": name for name, index in numbers.items()}
    names = [by_number.get(name.zfill(2), name) for name in names]
    unknown = set(names) - catalog.keys()
    if unknown:
        parser.error("unknown episodes: " + ", ".join(sorted(unknown)))
    folder = ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    summaries = []
    for name in names:
        heading = f"{numbers[name]:02d} / {len(catalog)}   {name.replace('_', ' ')}" if args.watch and not args.no_overlay else None
        if heading:
            print(f"{heading} · {catalog[name]['text']}", flush=True)
        result = run_episode(name, folder, not args.no_overlay, heading)
        summaries.append(result)
        print(json.dumps(result), flush=True)
        (folder / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
        if heading:
            passed = all(r["verified"] for r in result["results"])
            ending = result["results"][-1]
            failure = "game state did not match the test" if ending["status"] == "done" else ending["reason"]
            show(heading, catalog[name]["text"], "Passed" if passed else f"Failed · {failure}")
            time.sleep(2)
    return int(any(not r["verified"] for s in summaries for r in s["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
