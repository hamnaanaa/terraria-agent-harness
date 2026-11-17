"""The slow brain's stated plan and queue, drawn with AgentIO's game font."""
import textwrap
import time

LAYER = "slow-brain"


def title(goal):
    if goal.get("label"):
        return goal["label"]
    kind = goal["kind"]
    if kind == "reach":
        return "Go to " + str(goal.get("cell", goal.get("area")))
    if kind == "change":
        remove = sum(tile == -1 for _, _, tile in goal["cells"])
        return f"Change {len(goal['cells'])} tiles ({remove} to mine)"
    if kind == "quantity":
        return f"Collect {goal['count']} {goal['item']}"
    if kind == "defeat":
        return f"Defeat {goal.get('count', 1)} {goal['enemy']}"
    if kind == "interact":
        return goal["operation"].replace("_", " ").capitalize() + " " + goal.get("item", "")
    if kind == "search":
        return "Explore for " + goal["tile"]
    return "Stay alive in the requested area"


def draw(queue, screen):
    width = min(600, screen[0] - 48)
    columns = max(30, int(width / 11))
    content = [("Slow brain", "#ffd166")]
    reason = queue.recovery["reason"] if queue.recovery else queue.batches[0]["reason"] if queue.batches else ""
    reason = reason or queue.objective
    content.extend((line, "#ffffff") for line in textwrap.wrap(reason, columns, max_lines=3, placeholder="..."))
    active = title(queue.active) if queue.active else "Stay alive while waiting for the plan"
    content.extend((line, "#7dfa95") for line in textwrap.wrap("Now: " + active, columns, max_lines=2, placeholder="..."))
    if queue.recovery:
        content.append(("Resuming unfinished task after death", "#ffd166"))
    for index, goal in enumerate(list(queue.pending)[:3], 1):
        line = textwrap.shorten(title(goal), columns - 8, placeholder="...")
        content.append((f"Next {index}: {line}", "#c8d4e0"))
    if len(queue.pending) > 3:
        content.append((f"+ {len(queue.pending) - 3} more tasks", "#c8d4e0"))
    if queue.request:
        seconds = int(time.monotonic() - queue.request["started"])
        content.append((f"Planning {'what comes next' if queue.active else 'the next tasks'} ({seconds}s)", "#ffd166"))
    elif len(queue.batches) >= 2:
        content.append(("Next plan ready", "#7dfa95"))
    x, y = max(24, screen[0] - width - 24), max(215, screen[1] - 28 - len(content) * 24)
    return {"layer": LAYER, "labels": [
        {"at": [x, y + index * 24], "screen": True, "text": text, "color": color, "scale": .72}
        for index, (text, color) in enumerate(content)]}
