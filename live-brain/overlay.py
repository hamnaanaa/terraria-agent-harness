"""The live brain's single in-game debug layer."""
LAYER = "live-brain"


def draw(goal, state, plan, decision, episode):
    boxes, lines, marks = [], [], []
    area = goal.get("area")
    if area is None and goal.get("cell"):
        x, y = goal["cell"]
        area = (x, y, x, y)
    if area:
        x, y, x1, y1 = area
        boxes.append([x, y, x1 - x + 1, y1 - y + 1, "#ffd166"])
    for x, y, _ in goal.get("cells", ()):
        marks.append([x, y, "#ffd16680"])
    if plan:
        route = plan.way_from(plan.node_of(state["body"]))
        if route:
            points = [[x * 16 + 8, y * 16 + 8] for x, y in [route[0]["from"]] + [e["to"] for e in route]]
            lines.append({"points": points, "color": "#66c4ff", "width": 2})
            for edge in route:
                if edge.get("arc"):
                    lines.append({"points": edge["arc"], "color": "#66c4ff80", "width": 2})
    for track in decision.get("motion_paths", ()):
        points = track["points"]
        if len(points) > 1:
            lines.append({"points": points[::3] + [points[-1]],
                          "color": "#ffae5590" if track["group"] == "shot" else "#ff828290", "width": 2})
    if decision.get("path"):
        lines.append({"points": decision["path"], "color": "#7dfa95", "width": 3})
    title = goal.get("label")
    if not title:
        title = goal.get("item") or goal.get("enemy") or goal.get("tile") or goal.get("operation")
    if not title:
        if goal["kind"] == "maintain":
            title = f"{goal['ticks'] / 60:g} s inside the marked area"
        else:
            title = "marked area" if area else "marked cells"
    labels = [{"at": [24, 146], "text": f"{goal['kind']}  ·  {title}", "screen": True},
              {"at": [24, 170], "text": decision.get("label", "observing"), "screen": True, "color": "#7dfa95"}]
    if episode:
        labels.insert(0, {"at": [24, 120], "text": episode, "screen": True, "color": "#ffd166"})
    return {"layer": LAYER, "cells": marks, "boxes": boxes, "lines": lines, "labels": labels}
