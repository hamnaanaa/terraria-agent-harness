# Slow brain

These are the instructions for an agent that plans Terraria gameplay while the live brain plays. The planning agent runs through your coding assistant's subagent tools. `run.py` keeps the live brain active and exchanges planning requests and goal batches through files. It makes no model API calls.

## Start a planning agent

If you are the coding assistant hosting this repository, create a new subagent for each play session. Start it without the host's conversation history (`fork_context:false` where supported), and set its reasoning effort explicitly to `high` where supported. Give it this README's path, the repository path, the user's objective and strategy, new game or selected character/world, a new directory under `runs/`, and any duration limit. Let it read the game and choose the first tasks. Keep the host available to observe and stop the trial.

Do not resume a planning agent from a previous trial or pass it private gameplay notes, earlier plans, failure diagnoses or host-selected tasks. As the delegated agent, use repository documentation and the current session's files. For this isolated session, skip surrounding workspace journals, weekly/task files and saved assistant memory. Previous `runs/` directories are not required context. This should work for someone with a fresh clone and AgentIO running.

If you are that delegated slow brain, follow the instructions below yourself. Keep planning until the user stops, the session's duration ends, or a problem requires the host's help.

Before starting the controller:

1. Follow the [AgentIO setup](../README.md#set-up-agentio). Check `http://127.0.0.1:17900/ping`. `AGENTIO_URL` can select another local address.
2. For a new game, use tModLoader's character and world screens to create a fresh Classic character and a new Classic world with an unused name. Use your host's computer-control tools, or the command-line setup below when the window is unavailable. For a resumed game, enter the character and world the user selected. Preserve existing saves.
3. Enter the world. Read `GET /state` and verify the character, world and starting inventory. Leave the inventory and menus closed. Restore normal clock and enemy spawning after any earlier Gym use, and keep the game updating in the background: `POST /gym {"freeze":false,"spawns":1,"background":true}`. If an earlier recording enabled artificial brightness, turn it off through `/camera` before playing.
4. Start the controller as a background terminal process from the repository root:

```sh
python3 slow-brain/run.py start --world "WORLD NAME" --run runs/my-playthrough
```

`--objective` and `--strategy` set the initial planning instructions. The default objective is to beat the Moon Lord from the current position. The default strategy is to make progress with available resources, learn from observed outcomes and adapt. `--seconds` optionally bounds an attended trial. Choose a new run directory each time.

The controller immediately runs the live brain's survival task and publishes the first request. Now follow the six rules below.

### Setup when the game window is unavailable

tModLoader's installed `serverconfig.txt` documents its world-creation flags. Use its ordinary dedicated-server launcher with `-world NEW_PATH.wld -autocreate 1 -worldname NEW_NAME -difficulty 0` to generate a small Classic world. Choose a new path and leave the seed unspecified for a random world. Use the same save profile as the client; the launcher also accepts `-tmlsavedirectory PROFILE`.

AgentIO uses one local HTTP port, so first return the client to its menu through `POST /menu`, then stop that idle client before starting the temporary server. Once the new world is generated and saved, send `exit` to the server's console to shut it down. Start the client again with that profile. At its menu, `POST /newplayer {"name":"NEW_CHARACTER","difficulty":0}` creates a fresh Classic character with the three starter tools. Then use `POST /enter {"player":"NEW_CHARACTER","world":"NEW_NAME"}`. This setup generates ordinary saves; gameplay starts in the single-player client.

If your installation cannot complete either setup method, report the specific blocker to the host. Never overwrite a save to get past a setup problem.

## 1. Your job and strategy

Choose what to do next to advance the objective. Work from the game you actually find: equipment, health, time of day, explored terrain and completed milestones. Decide which opportunities are worthwhile and which risks need preparation. The starting instructions define the strategy; you choose and revise the steps throughout the playthrough.

The live brain handles movement, navigation, tool use, fighting and immediate survival. Give it measurable tasks. Use the actual results to decide whether your plan worked and what to try next. If a capability is missing or repeatedly fails, describe what happened to the host. Keep the implementation fixed during a gameplay trial.

## 2. Read the current situation

Wait for a request without blocking the gameplay process:

```sh
python3 slow-brain/run.py wait --run runs/my-playthrough --seconds 30
```

This prints a request ID and the name of `context.json`. Read that file in the run directory. It contains:

- `objective` and `strategy`: the initial instructions for this run.
- `state`: the observed game state, including body, health, inventory, worn gear, time, biomes, milestones, enemies, projectiles, items and nearby terrain. Crafting failures include missing materials or stations in the recorded endpoint response; state has no recipe catalogue.
- `map`: remembered explored terrain. `kinds` maps tile IDs to properties. Each cell is `[x, y, tile, shape, liquid_amount, liquid_kind, wall, can_break]`. Mining permission belongs to the cell: ground under a tree or furniture can be protected while another block of the same type is breakable. Coordinates are world tiles; each tile is 16 pixels. Missing cells are unknown.
- `active_goal` and `queued_goals`: what the live brain is doing and what it will do next.
- `recent_results`: the last ten completed or failed goals, with their observed results.
- `last_planning_seconds`: how long your previous accepted reply took, including inspecting the situation and submitting the plan. Initially this is unknown. Compare it with the actual execution times in `recent_results` (`ticks / 60` at normal game speed).

Use a small local script to inspect terrain or inventory rather than printing the entire file into your context. Keep unknown terrain unknown. Raw enemy AI values are metadata, not universal instructions about the next attack.

Failed or timed-out results can include `blockers`, each with a target `cell`, a `reason` and its `source`. These describe constraints checked against the final observed state. `game_mining_permission` reports the game's per-cell permission. `placement_rule` comes from the existing placement rules; `door_terrain_rule` identifies observed solid tiles in both swing spaces of a normal door. Placement and door explanations come from our rules, not engine error messages. An empty list means no cause was established. Treat a timeout alone as an unexplained failure. Use the cells and current map to decide the next plan; do not assume every failed action needs a longer timeout.

The snapshot is from the moment the request was made. `status.json` gives the current active goal, queue, position and health. `play.jsonl.gz` contains observations, decisions, timing spans, planning requests, accepted batches and goal results.

## 3. Produce executable goals

Write a JSON file containing `reason` and an ordered `goals` list. Explain the immediate plan briefly in `reason`. Give each goal a short `label` describing what it accomplishes, so a person watching the game can follow your plan. Every goal needs `kind` and a positive `limit_ticks`. Terraria normally runs at 60 ticks per second. Give the goal enough time to execute and use a timeout to learn when the plan is failing.

These are the supported formats. The coordinates, items and quantities illustrate the interface; choose your own from the observed situation.

| Kind | Example | Completion |
|---|---|---|
| Reach | `{"kind":"reach","area":[100,200,105,203],"limit_ticks":900}` | The player's feet are in the area, standing on support. `cell:[x,y]` selects an exact position. `ground:false` permits airborne arrival. |
| Change terrain | `{"kind":"change","cells":[[110,202,-1],[111,203,0]],"limit_ticks":900}` | All requested tiles match. `-1` means remove a tile; nonnegative values are tile IDs to place. |
| Item quantity | `{"kind":"quantity","item":"Iron Ore","count":20,"limit_ticks":900}` | At least 20 are in the bag. `count` is a fixed inventory total by default. |
| Defeat | `{"kind":"defeat","enemy":"Blue Slime","count":1,"limit_ticks":900}` | The observed kill count increases by the requested number. Use the enemy name from state. |
| Interact | `{"kind":"interact","operation":"craft","item":"Work Bench","count":1,"limit_ticks":300}` | The crafted quantity appears in inventory. Other interactions are below. |
| Search | `{"kind":"search","area":[100,210,120,225],"tile":"DemonAltar","limit_ticks":1800}` | The tile is found, or the patch is fully observed. The result distinguishes those outcomes. |
| Maintain | `{"kind":"maintain","area":[80,180,140,230],"ticks":600,"limit_ticks":900}` | Stay alive inside the area for the requested duration. Optional `until_day:true` also requires daytime. |

Other `interact` operations:

- `wear`: `item` is the equipment name. Completion checks worn gear.
- `door`: `cell` is the door position and `tile` is the desired tile kind, such as `ClosedDoor` or `OpenDoor`.
- `chest`: `cell` is the chest position. Open it and take all its items. Completion uses the game's transfer receipt; items left because the bag cannot fit them cause a failure with the remaining items listed. An empty chest completes with that explanation. Optional `chest_id` verifies its identity. Use `take:false` to open it for a later deposit without taking its contents.
- `life_crystal`: use a Life Crystal already in the bag. Completion checks permanent maximum health.
- `use`: specify `item` and `consume:1` for a consumable. Completion checks the inventory count decreased. For a summon, specify `projectile` from that item's `shoot` field and optional `count`; completion checks owned projectiles. Optional `aim:[pixel_x,pixel_y]` selects an aim point.

For `interact/craft`, `count` is the number of new items required in the bag, **not the number of recipe executions**. A recipe can make several items at once. Request 75 Wooden Arrows with `count:75`, and six torches with `count:6`. The game may produce more than requested when a recipe makes a stack. Keep the goal's label consistent with that item quantity.

The live brain does not find an unseen source of an item just because you request a quantity. Choose where to search, which known resource tiles to remove, and when to collect the drops. Use tile and item properties from state to choose tools and placements. Crafting requires the materials and station to be available in the real game.

Use fixed inventory totals in queued plans. If you have 19 wood and want 20 additional wood, request a total of 39. Wood picked up while chopping already counts toward that target. Explicit `gain:true` remains available for collecting more than the inventory at the quantity task's start; it starts measuring after earlier tasks have finished.

For incidental construction, the live brain uses the game's item `value` as its default material preference. A costlier option stays available when its stock, reach or placement speed adds a capability. An explicit terrain goal still uses the requested tile. A goal can supply `preserve`, such as `{"Wood":1}`, to prefer saving a material needed later; omitted materials have preference 0. This is a preference rather than a ban. Price alone does not express an item's usefulness to the plan.

A quantity task picks up available drops. If the requested total is still short after no matching drops remain, it fails with the observed quantity, such as `19/20 Wood collected; no matching drops remain`. Plan another source or harvesting task for the shortfall.

For furniture such as a Work Bench or Campfire, `POST /fits {"item":"Work Bench","r":12}` returns nearby coordinates accepted by the game's placement check, including the object's footprint and anchors. Use those coordinates when planning a placement. The ordinary single-block rule does not validate a furniture object's full shape.

Submit the file against the request you read:

```sh
python3 slow-brain/run.py submit --run runs/my-playthrough --request REQUEST_ID --plan runs/my-playthrough/plan.json
```

The file has this shape: `{"reason":"your immediate plan","goals":[...]}`. The controller validates it before accepting it. If the request expired, read the new request and reconsider from its context.

## 4. Let the live brain execute

Give a coherent chain as far ahead as the observed situation supports. The live brain runs tasks one at a time and checks actual completion before advancing. It keeps the same remembered map across the chain.

Make location prerequisites explicit. Include a reach to a known crafting station immediately before crafting there when necessary. Survival can move the character while the queue is empty, and death respawns it elsewhere, so a previously observed position does not guarantee the next task begins there.

Keep the controller running while you think or wait. During play, send goals through `submit`. Let the live brain send controls and perform game interactions. Pausing, teleporting, changing inventory, stamping terrain or editing the game to rescue a plan would invalidate the trial. New-character/world creation and restoring normal clock, spawns and display settings are setup steps.

## 5. Plan ahead while the queue runs

The controller requests another plan as soon as a batch begins executing. Read the active and remaining goals and plan what follows **assuming they all succeed**. It keeps at most one future batch ready, then requests another when that batch begins. A failure invalidates both the queued continuation and any pending request.

Plan a whole useful stage of work when the observed situation supports it. Give the live brain a longer sequence of concrete goals, including the resources, travel, crafting and placements that you can already work out. Several quick crafting actions can finish before you answer another request. Include related steps together rather than splitting them into separate replies.

Use `last_planning_seconds` and observed goal durations to judge how much work to queue. Aim for a chain that lasts longer than your planning turn, with room for the next reply to arrive. For example, a 30-second planning turn needs more coverage than a few one-second crafting tasks. Use the duration of useful work as the guide; a task count alone says little. Do not pad a batch with waiting, repeated satisfied goals or assumptions about undiscovered terrain. If new information is needed, plan a measurable search or trip and reconsider when the observation arrives.

Make the plan reliable before making it long. Include station approaches, ingredient quantities and other prerequisites, and recheck current `status.json` before submitting if health, location or the request may have changed. A failed prerequisite discards the remaining chain. The on-screen slow-brain text shows your stated plan, active task, upcoming tasks and whether another plan is being prepared.

After submitting a batch, wait for a different request ID:

```sh
python3 slow-brain/run.py wait --run runs/my-playthrough --after LAST_REQUEST_ID --seconds 30
```

If no request arrives during that wait, wait again. If the controller reports `stopped`, read the final results and report to the host. Keep tool waits bounded so you can respond to the host's stop instructions.

## 6. Replan from failures; resume unfinished work after death

If a task fails, times out or the character dies, the controller clears the remaining chain and invalidates its pending continuation request. The next request includes the failed goal and result. Inspect observations around it when the result alone does not explain enough. Decide whether to try again, change the approach or prepare something else. Explain that choice in the next batch's `reason`.

After death, the game respawns the character and the live brain resumes the interrupted task while you prepare a replacement plan. The original progress baseline is preserved: collecting 20 wood still means the same target quantity, and already placed blocks remain complete. A maintain task starts its survival duration again. The new request has `resuming_after_death:true` and includes `active_baseline`; reconsider from the new location and inventory. Your next batch replaces the resumed task as soon as it arrives, rather than waiting for that task to finish. A genuine failure or timeout stops the retry and requests a new plan.

While there is no unfinished task or queued plan, the live brain uses its existing survival goal in a broad area around the character. It can fight and evade threats while you plan. If the game connection fails or the wrong world is loaded, report the problem rather than treating it as progress.

To stop:

```sh
python3 slow-brain/run.py stop --run runs/my-playthrough
```

The controller releases its inputs and saves the game. Once it reports `stopped`, use `POST /menu` to leave the character safely at the menu while preparing your report. Report the goals it chose, what actually completed, failures, time spent waiting for plans and any missing capabilities. Preserve the logs so we can improve the harness from what happened.

Run `python3 slow-brain/checks.py` to check the queue, failure invalidation and stop behavior without the game.
