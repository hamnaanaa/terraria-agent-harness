"""Current-velocity movement shared by enemies and hostile projectiles.

Each observation stands alone. No enemy tracks or behavior state are retained.
"""


def observe(state):
    """Express the reported velocity in game-tick units without changing the input."""
    arrays = {}
    for group in ("enemies", "shots"):
        rows = []
        for entity in state.get(group, ()):
            row = dict(entity)
            if group == "shots" and not row.get("hostile"):
                rows.append(row)
                continue
            updates = 1 + row.get("extra_updates", 0) if group == "shots" else 1
            row["motion"] = {"vx": row["vx"] * updates, "vy": row["vy"] * updates,
                             "source": "current velocity", "tick": state["tick"]}
            rows.append(row)
        arrays[group] = rows
    return dict(state, **arrays)


def advance(entity, solid, floor, shape=None, *, gravity=0, fall_top=float("inf"),
            tile_collide=True, platforms=True, cache=None):
    """One physical update; shared cache reuses paths until an action changes them."""
    from rules.enemies import move_box

    key = ("motion", entity["x"], entity["y"], entity["w"], entity["h"],
           entity["vx"], entity["vy"], gravity, fall_top, tile_collide, platforms)
    result = cache.get(key) if cache is not None else None
    if result is None:
        x, y, vx, vy = (entity[k] for k in ("x", "y", "vx", "vy"))
        vy = min(vy + gravity, fall_top)
        if tile_collide:
            x, y, vx, vy, _ = move_box(x, y, entity["w"], entity["h"], vx, vy, solid, floor, shape)
        else:
            x, y = x + vx, y + vy
        result = x, y, vx, vy
        if cache is not None:
            cache[key] = result
    entity.update(zip(("x", "y", "vx", "vy"), result))
    return result


def prepare_shot(shot):
    if shot.get("hostile") and "motion" in shot:
        shot = dict(shot)
        updates = 1 + shot.get("extra_updates", 0)
        shot.update(vx=shot["motion"]["vx"] / updates, vy=shot["motion"]["vy"] / updates)
        shot.pop("motion")  # Projected shots already carry the advanced velocity.
    return shot


def shot_tick(shot, solid, shape=None, cache=None):
    """Hostile flight: observed movement and reported collision/lifetime only."""
    if shot.get("time_left", 1) <= 0:
        return False
    old_vx, old_vy = shot["vx"], shot["vy"]
    advance(shot, solid, lambda *_: False, shape,
            tile_collide=shot.get("tile_collide", False), platforms=False, cache=cache)
    if (shot["vx"], shot["vy"]) != (old_vx, old_vy):
        shot["time_left"] = 0
        return False
    if "time_left" in shot:
        shot["time_left"] -= 1 / (1 + shot.get("extra_updates", 0))
    return True


def paths(state, foes, world, ticks, cache):
    """Warm common paths once for a decision and expose them for recording/drawing."""
    from rules.enemies import tick

    tracks = []
    for group, entities in (("enemy", foes), ("shot", [s for s in state["shots"] if s.get("hostile")])):
        for original in entities:
            entity = dict(original) if group == "enemy" else dict(prepare_shot(original))
            points = [[entity["x"] + entity["w"] / 2, entity["y"] + entity["h"] / 2]]
            for _ in range(ticks):
                if group == "enemy":
                    tick(entity, 0, 0, world.solid, world.floor, shape=world.shape, cache=cache)
                else:
                    alive = all(shot_tick(entity, lambda x, y: world.raw_tile(x, y) >= 0 and world.solid(x, y),
                                          world.shape, cache) for _ in range(1 + entity.get("extra_updates", 0)))
                    if not alive:
                        break
                points.append([entity["x"] + entity["w"] / 2, entity["y"] + entity["h"] / 2])
            tracks.append({"group": group, "i": original.get("i"), "id": original.get("id"),
                           "w": original["w"], "h": original["h"], "points": points})
    return tracks
