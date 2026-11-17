# Recorded episodes

Copy a small part of a world into Gym when a playthrough exposes a failure. Keep the terrain and the starting state together so the episode runs without the original save or logs.

- `quarry_escape`: FirstLight at tick 16110. Climb from row 235 to the surface rim at rows 201–207 with 70 dirt blocks, 42 platforms and the recorded tools. Copies the shaft, overhang, background walls and remembered terrain; the goal checks actual arrival and survival.
  Exercises partial building and headroom mining across several decisions, with actual arrival checked at the rim.

- `lake`: FirstLight's east lake. Start underwater at tick 17896, or on the partly built bridge at tick 23573. The first checks for regained breath while waiting. The second checks that the bridge gets finished and the player ends above water.
- `vulture`: FirstLight's west ridge. Reach the recorded target area with at most one vulture hit from tick 13330. A separate start at tick 13277 compares the next 27 ticks of flight to the forecast, within half a tile, using the player's actual positions.
- `vulture_waiting` and `vulture_retreat`: the airborne vulture from FirstLight at tick 3866, with Scout01's gear. Defeat it while waiting, or reach the area to the east. Each case allows one hit. Stone at the edges keeps the player from falling off the copied terrain.

- `recorded_exploration`: DawnCircuit1115 at tick 2636. Search the patch below the hollow for a life crystal. Checks the game's actual revealed cells, rather than the predicted route length. The source goal allowed 40 seconds, which revealed 126 of its 428 unseen cells after the fix. This Gym case allows 180 seconds; full coverage took 164.6 seconds with the recorded tools.
- `recorded_platform_climb`: DawnCircuit1115 at tick 34120. Place the support platforms from bottom to top, climb out, reach the eastern iron vein and mine all twelve requested tiles.

The two navigation replays preserve the recorded terrain, inventory and remembered map, with enemies omitted to test navigation separately. Cells not present in the observations are filled with stone at the crop edges; every requested goal cell has recorded terrain. A stone border keeps falling tiles and liquid from older Gym scenes outside the crop.

Run from the repository root with the game in Gym:

```sh
python3 gym/live-brain/run.py --watch recorded
python3 gym/live-brain/run.py --watch lake
python3 gym/live-brain/run.py --watch vulture
python3 gym/live-brain/run.py --watch recorded_platform_climb
```

These run separately from the 70 standard scenarios. A failed check is a baseline for a later fix. Logs and results go in `runs/`. They use Gym's Journey settings, so damage and enemy max health can differ from the source world.

For another recording, keep the source tick, crop bounds, goal, player position, velocity, health and inventory in `starts.json`. Include enemy state if it matters. `terrain.json` stores non-empty cells as `[dx, dy, tile, shape, liquid, liquid_kind, wall, frame_x, frame_y]`. Coordinates are relative to the crop. A start can add a small patch, such as the lake's existing platforms. The game runs normally after setup, so decisions can differ from the original playthrough.
