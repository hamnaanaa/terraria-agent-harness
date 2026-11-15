"""Collect scripted tick traces and live combat decision logs in the disposable Gym."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys

from cases import EARLY, LATE, WEAPONS, cases

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "live-brain"), str(ROOT / "gym" / "live-brain")]
import gym_api as gym
from loop import Loop
from overlay import LAYER


def stamp_arena(terrain, action, serial):
    boss = terrain == "boss"
    width, height, floor = (180, 106, 96) if boss else (112, 72, 60)
    x = 18 if action == "cross" else width // 2
    foot = floor - 1
    arena = gym.canvas(width, height, floor)
    if terrain == "ledge":
        foot -= 7
        gym.fill(arena, x - 3, foot + 1, x + 1, floor - 1, "#")
    elif terrain == "deep":
        gym.fill(arena, 0, floor, width - 1, height - 1, "%")
    elif terrain == "pool":
        gym.fill(arena, x + 2, floor - 7, x + 17, floor - 1, "~")
        gym.fill(arena, x + 18, floor - 7, x + 18, floor - 1, "#")
        foot -= 7
        gym.fill(arena, x - 8, foot + 1, x + 1, floor - 1, "#")
    elif terrain == "wall":
        gym.fill(arena, x + 6, foot - 4, x + 6, foot, "#")
    elif boss:
        for row in (floor - 12, floor - 25, floor - 38):
            gym.fill(arena, 28, row, width - 29, row, "=")

    # Rotate sites and check for old projectiles before stamping. All sites have
    # the same height for a given arena so paired trials share the local terrain.
    state = gym.state(w=2, h=2)
    sites = [(290 + i * 450, 146 if not boss else 285) for i in range(9)]
    for offset in range(len(sites)):
        origin = sites[(serial + offset) % len(sites)]
        left, top = origin
        if not any((left - 40) * 16 <= s["x"] <= (left + width + 40) * 16
                   and (top - 40) * 16 <= s["y"] <= (top + height + 40) * 16
                   for s in state["shots"]):
            gym.SITE = origin
            gym.stamp(arena)
            return origin, (width, height, floor), (x, foot)
    raise RuntimeError("all arena sites still have projectiles")


def setup(case, serial):
    gym.gym(butcher=True, spawns=0, freeze="midnight", background=True, clear_buffs=True)
    animation = gym.state(w=2, h=2)["body"]["item_animation"]
    if animation:
        # Finish the previous attack at its old site, before replacing its weapon and ammo.
        gym.run([{"keys": [], "ticks": animation + 2}])
    terrain = case["foes"][0][3] if case["foes"] else case["terrain"]
    layout = case.get("layout", case["action"])
    origin, size, feet = stamp_arena(terrain, layout, serial)
    left, top = origin
    width, height, floor = size
    x, foot = feet
    late = case["tier"] == "late" or (case["tier"] == "boss" and case["weapon"] != "fire_bow"
                                    and case["foes"][0][0] not in {"EyeofCthulhu", "SkeletronHead"})
    bag = case.get("bag", WEAPONS.get(case["weapon"], ()))
    ack = gym.gym(wear=case.get("wear", LATE if late else EARLY),
                  life_max=case.get("life_max", 400 if late else 200),
                  bag=bag, feet=[left + x, top + foot], heal=True, clear_buffs=True)
    if any("no item" in result or "nowhere to wear" in result for result in ack["done"]):
        raise RuntimeError(str(ack["done"]))
    gym.run([{"keys": [], "ticks": 10}])
    ready = gym.state(w=2, h=2)
    if ready["body"]["item_animation"] or any(
        left * 16 <= shot["x"] <= (left + width) * 16 and top * 16 <= shot["y"] <= (top + height) * 16
        for shot in ready["shots"]
    ):
        raise RuntimeError("arena still has an attack from setup")
    for _, stack, slot in bag:
        if not any(item["slot"] == slot and item["stack"] == stack for item in ready["bag"]):
            raise RuntimeError("loadout changed during setup")

    spawns = []
    for number, (enemy, dx, dy, _) in enumerate(case["foes"]):
        if layout == "cross":
            dx = 28 + 16 * number
        elif case["phase"] == "live" and case["repeat"] == 3:
            dx = -dx
        spawn_x, spawn_y = left + x + dx, top + floor - 1 + dy
        spawn = [enemy, spawn_x, spawn_y]
        if case.get("spawn_health"):
            life, w, h = case["spawn_health"]
            spawn.append(dict(x=spawn_x * 16 + 8 - w / 2, y=(spawn_y + 1) * 16 - h,
                              vx=0, vy=0, life=life, timers=[0, 0, 0, 0]))
        spawns.append(spawn)
    ack = gym.gym(spawn=spawns)
    spawned = [int(s.rsplit("(", 1)[1][:-1]) for s in ack["done"] if s.startswith("spawned ")]
    if len(spawned) != len(spawns):
        raise RuntimeError(str(ack["done"]))
    state = gym.state(x0=left, y0=top, w=width, h=height)
    return dict(origin=origin, size=size, feet=feet, spawns=spawns, spawned=spawned, state=state)


def scripted(case, initial):
    if case["action"] == "stand":
        return [{"keys": [], "ticks": 300}]
    if case["action"] == "move":
        return [{"keys": keys, "ticks": ticks} for keys, ticks in (
            ([], 35), (["right"], 45), (["left", "jump"], 16),
            (["left"], 75), (["right"], 65), ([], 64))]

    before = initial["state"]
    target = next(e for e in before["enemies"] if e["i"] == initial["spawned"][0])
    aim = [target["x"] + target["w"] / 2, target["y"] + target["h"] / 2]
    attack = {"slot": 0, "keys": ["use"], "aim": aim, "ticks": 1}
    if case["tier"] == "boss":
        return [dict(attack, ticks=90), dict(attack, keys=["left", "jump", "use"], ticks=16),
                dict(attack, keys=["left", "use"], ticks=28), dict(attack, ticks=226)]
    if case["weapon"] in {"summon", "hard_summon"}:
        whip = dict(attack, slot=1, keys=["right", "use"] if case["repeat"] == 2 else ["use"])
        return [attack, {"keys": [], "ticks": 55}, whip, {"keys": [], "ticks": 244}]
    # Isolate individual uses. The second trial moves between uses.
    return [step for _ in range(4) for step in
            (attack, {"keys": ["left"] if case["repeat"] == 2 else [], "ticks": 7},
             {"keys": [], "ticks": 52})]


def live(case, initial, record):
    left, top = initial["origin"]
    _, _, floor = initial["size"]
    x, _ = initial["feet"]
    before = initial["state"]
    action = case["action"]
    limit = 960 if case["tier"] == "boss" else 720
    if action == "defeat":
        foe = next(e for e in before["enemies"] if e["i"] == initial["spawned"][0])
        goal = dict(kind="defeat", enemy=foe["name"], count=1, limit_ticks=limit)
    elif action == "cross":
        goal = dict(kind="reach", area=[left + 79, top + floor - 4, left + 86, top + floor - 1],
                    limit_ticks=limit, label="Cross with enemies in the way")
    else:
        goal = dict(kind="maintain", waiting=True, area=[left + x - 44, top + floor - 42,
                    left + x + 44, top + floor + 1], limit_ticks=limit, label="Survive the encounter")

    class RecordingLoop(Loop):
        def read(self):
            state = super().read()
            snapshots.append(state)
            return state

    rounds, snapshots = [], []
    def on_record(row):
        if row["type"] == "round":
            rounds.append(row)
        record(row)

    # Buffer observations so serializing the full state never delays a decision.
    brain = RecordingLoop(draw=False, world_name="Gym")
    record(dict(type="intent", goal=goal))
    options = {}
    if action == "guard":
        options["next_goal"] = lambda: None
        options["stop_waiting"] = lambda s: s["tick"] - before["tick"] >= limit
    try:
        outcome = brain.run(goal, on_record, case["name"], **options)
    finally:
        for state in snapshots:
            record(dict(type="observation", state=state))
    return outcome, rounds


def q95(values):
    return round(sorted(values)[(len(values) * 95 - 1) // 100], 2) if values else None


def summarize(before, after, case, result, rounds, trace):
    b, a = before["counters"], after["counters"]
    ticks = after["tick"] - before["tick"]
    by_name = {name: n - b["kills_by_name"].get(name, 0) for name, n in a["kills_by_name"].items()
               if n > b["kills_by_name"].get(name, 0)}
    spans = defaultdict(list)
    for row in rounds:
        for span in row["spans"]:
            spans[span["phase"]].append(span["end_ms"] - span["start_ms"])
    dps = round((a["damage_dealt"] - b["damage_dealt"]) * 60 / ticks, 2) if ticks else 0
    return dict(name=case["name"], phase=case["phase"], tier=case["tier"], action=case["action"],
                enemies=[foe[0] for foe in case["foes"]], weapon=case["weapon"], status=result["status"],
                reason=result.get("reason"), ticks=ticks, trace_ticks=len(trace) - 1 if trace else None,
                damage_taken=a["damage_taken"] - b["damage_taken"], damage_dealt=a["damage_dealt"] - b["damage_dealt"],
                dps=dps, kills=by_name, died=after["deaths"]["count"] > before["deaths"]["count"],
                contacts=a["touches_taken"] - b["touches_taken"], shots_taken=a["shots_taken"] - b["shots_taken"],
                shots_fired=a["projectiles_fired"] - b["projectiles_fired"], life_end=after["body"]["life"],
                decisions=len(rounds), deadline_misses=sum(r["deadline_missed"] for r in rounds),
                phase_ms={name: [round(statistics.median(times), 2), q95(times)] for name, times in spans.items()})


def collect(case, serial, folder):
    with gzip.open(folder / f"{serial:03d}-{case['name']}.jsonl.gz", "xt", compresslevel=3) as file:
        def record(row):
            file.write(json.dumps(row, separators=(",", ":")) + "\n")

        record(dict(type="case", case=case))
        initial = setup(case, serial)
        record(dict(type="start", **initial))
        gym.call("/overlay", {"layer": LAYER, "labels": [
            {"at": [24, 120], "text": f"Combat Gym {serial} · {case['name']}", "screen": True}]})
        trace, rounds = [], []
        if case["phase"] == "live":
            result, rounds = live(case, initial, record)
        else:
            steps = scripted(case, initial)
            record(dict(type="inputs", steps=steps))
            probe = gym.run(steps)
            trace = probe["trace"]
            record(dict(type="trace", result=probe))
            seen = {int(row[0]) for frame in trace for row in frame["enemies"]}
            if not set(initial["spawned"]) <= seen:
                raise RuntimeError("an expected enemy was absent throughout the tick trace")
            result = dict(status="died" if probe["died"] else "observed")
        after = gym.state(w=90, h=50)
        record(dict(type="finish", state=after, result=result))
        summary = summarize(initial["state"], after, case, result, rounds, trace)
        if "motion" in case:
            summary.update(motion=case["motion"], pair=case["pair"])
        summary["file"] = Path(file.name).name
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("movement", "weapon", "live"))
    parser.add_argument("--case", action="append", default=[], help="Run cases containing this text.")
    parser.add_argument("--seed", type=int, default=24, help="Shuffle cases within each phase.")
    parser.add_argument("--resume", type=Path, help="Continue an interrupted run in the same Gym session.")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    phase = args.phase
    selected = [case for case in cases() if (not phase or phase == case["phase"])
                and (not args.case or any(part.lower() in case["name"].lower() for part in args.case))]
    if args.list:
        for case in selected:
            print(case["phase"], case["name"])
        return 0
    if not selected and not args.resume:
        parser.error("no matching cases")
    gym.in_gym()
    initial = gym.state(w=2, h=2)
    if initial["execution"]["active"] or initial["execution"]["pending"]:
        raise RuntimeError("AgentIO already has an active controller")
    random.Random(args.seed).shuffle(selected)
    selected.sort(key=lambda c: ("movement", "weapon", "live").index(c["phase"]))
    sources = [p for directory in ("live-brain", "mod/AgentIO", "gym/combat")
               for p in (ROOT / directory).rglob("*") if p.suffix in (".py", ".cs", ".json")]
    source_hashes = {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
    results = []
    if args.resume:
        folder = args.resume.resolve()
        if folder.parent != ROOT / "runs" or not folder.name.endswith("-combat-data"):
            parser.error("resume expects a combat data folder under runs/")
        manifest = json.loads((folder / "manifest.json").read_text())
        if manifest["session"] != initial["session"] or manifest["strength"] != initial["strength"]:
            parser.error("the Gym session or difficulty changed")
        if any(source_hashes.get(p) != digest for p, digest in manifest["source_hashes"].items()
               if not p.startswith("gym/combat/")):
            parser.error("live brain or AgentIO sources changed since this run started")
        selected = manifest["cases"]
        summary = folder / "summary.json"
        if summary.exists():
            results = json.loads(summary.read_text())
        manifest.setdefault("resumes", []).append(dict(next_case=len(results) + 1, source_hashes=source_hashes))
    else:
        folder = ROOT / "runs" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-combat-data")
        folder.mkdir(parents=True)
        manifest = dict(seed=args.seed, world=initial["world_name"], strength=initial["strength"],
                        session=initial["session"], cases=selected,
                        head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                        source_hashes=source_hashes)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(folder, flush=True)
    try:
        for index, case in enumerate(selected[len(results):], len(results) + 1):
            path = folder / f"{index:03d}-{case['name']}.jsonl.gz"
            if path.exists():
                path.rename(path.with_suffix(f".{datetime.now():%H%M%S%f}.incomplete.gz"))
            try:
                row = collect(case, index, folder)
            except (RuntimeError, OSError, StopIteration) as error:
                gym.in_gym()
                row = dict(name=case["name"], phase=case["phase"], tier=case["tier"], status="invalid", reason=str(error))
                if "motion" in case:
                    row.update(motion=case["motion"], pair=case["pair"])
            results.append(row)
            (folder / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"{index}/{len(selected)} {case['name']} {row['status']} "
                  f"dealt={row.get('damage_dealt')} took={row.get('damage_taken')} {row.get('reason') or ''}", flush=True)
    finally:
        gym.in_gym()
        gym.call("/run/cancel", {}, timeout=2)
        gym.gym(butcher=True, spawns=0, freeze="noon", heal=True)
        gym.call("/overlay", {"layer": LAYER, "labels": []}, timeout=2)
    print(Counter(row["status"] for row in results), flush=True)
    return int(any(row["status"] == "invalid" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
