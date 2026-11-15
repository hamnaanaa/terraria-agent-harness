"""Checks for current-velocity motion, independent of enemy identities and behavior."""
from copy import deepcopy

from rules import enemies, motion, projectiles


def sample(tick, x=0, y=0, **changes):
    e = dict(i=1, id=3, name="Zombie", x=x, y=y, vx=2, vy=0,
             w=20, h=20, gravity=.3, fall_top=10, flies=False)
    e.update(changes)
    return dict(tick=tick, world_name="Gym", session={"loaded": 1}, enemies=[e], shots=[])


def stateless_motion_check():
    state = sample(25, x=30, y=15, vx=0, vy=-2)
    before = deepcopy(state)
    expected = motion.observe(state)
    for other in (sample(1, vx=100), sample(40, x=-900), dict(sample(2), enemies=[])):
        motion.observe(other)
        assert motion.observe(state) == expected
    assert state == before
    measured = expected["enemies"][0]["motion"]
    assert (measured["vx"], measured["vy"]) == (0, -2)
    assert measured["source"] == "current velocity"
    # Extra projectile updates scale speed once; simulation still uses per-update velocity.
    state = dict(sample(10), enemies=[], shots=[dict(i=2,id=100,x=0,y=0,vx=3,vy=-1,
        hostile=True,owner=-1,extra_updates=2,time_left=30)])
    shot = motion.observe(state)["shots"][0]
    assert shot["motion"]["vx"] == 9 and shot["motion"]["vy"] == -3
    assert motion.prepare_shot(shot)["vx"] == 3 and motion.prepare_shot(shot)["vy"] == -1
    state["shots"][0].update(x=-500, vx=-2, time_left=100)
    assert motion.observe(state)["shots"][0]["motion"]["vx"] == -6


def motion_physics_check():
    empty = lambda *_: False
    base = sample(0, x=64, y=32)["enemies"][0]
    a, b = deepcopy(base), deepcopy(base)
    b.update(id=99999, name="unknown", ai=120, timers=[999,999,999,999], local_ai=[9]*4)
    for _ in range(15):
        assert enemies.tick(a, 0, 0, empty, empty) is None
        enemies.tick(b, 9999, -9999, empty, empty)
    assert (a["x"],a["y"],a["vx"],a["vy"]) == (b["x"],b["y"],b["vx"],b["vy"])
    assert abs(a["x"]-94)<.001 and abs(a["y"]-68)<.001
    floor = lambda x,y: y >= 8
    a = dict(base, x=64,y=108,vx=0,vy=0)
    for _ in range(20):enemies.tick(a,0,0,floor,floor)
    assert a["y"] == 108 and a["vy"] == 0
    a = dict(base, flies=True, through_blocks=True)
    for _ in range(15):enemies.tick(a,0,0,lambda *_: True,lambda *_: True)
    assert a["y"] == 32 and a["x"] == 94


def hostile_motion_check():
    empty = lambda *_: False
    a = dict(i=1,id=176,hostile=True,x=10,y=20,w=4,h=4,vx=2,vy=-1,time_left=30,
             ai=1,ai0=999,local_ai=[99,99],tile_collide=False)
    b = dict(a,id=9999,ai=999)
    for _ in range(15):
        assert projectiles.advance(a,empty,None)
        assert projectiles.advance(b,empty,None)
    assert (a["x"],a["y"]) == (b["x"],b["y"]) == (40,5)
    assert a["time_left"] == 15


def motion_cache_check():
    empty = lambda *_: False
    base = sample(0)["enemies"][0]
    a,b,cache = deepcopy(base),deepcopy(base),{}
    for _ in range(60):enemies.tick(a,0,0,empty,empty,cache=cache)
    n = len(cache)
    for _ in range(60):enemies.tick(b,0,0,empty,empty,cache=cache)
    assert a == b and len(cache) == n == 60
    # An action's knockback branches its own forecast without changing the base path.
    c = dict(base,vx=-3)
    for _ in range(60):enemies.tick(c,0,0,empty,empty,cache=cache)
    assert c["x"] < 0 and b["x"] > 0


CHECKS = [stateless_motion_check, motion_physics_check, hostile_motion_check, motion_cache_check]
