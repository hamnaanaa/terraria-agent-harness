# Combat Gym data

Start AgentIO in the disposable Journey world named `Gym`. The collector stamps arenas, equips the Gym character, disables natural spawns, and spawns the named Terraria NPCs directly.

## Live-brain tests

```sh
python3 gym/combat/observed_motion.py --case 15
python3 gym/combat/weapons.py --case mana_backup
```

Omit `--case` to run all 18 common-enemy cases or all 29 weapon cases. These are separate from the 70 numbered scenarios and seven recorded episodes in `gym/live-brain/run.py`.

The enemy cases cover individual enemies, groups, travel and survival with early and Underworld gear. They record observed outcomes, predicted paths and per-step timings. Optional `--recorder /path/to/recorder --window ID` uses an external recorder that accepts a window ID, duration in seconds and output path.

The weapon cases cover swords, bows, guns, thrown weapons, magic bolts, boomerangs, grenades, spears, whips, yoyos, flails, summons and sentries. They also check depleted mana, exhausted ammo and consumed throwing items with a backup weapon, flying targets, groups and travel. Each case displays its purpose in the game. `--out` selects the log folder.

## Data collector

```sh
python3 gym/combat/collect.py --list
python3 gym/combat/collect.py --phase movement
python3 gym/combat/collect.py --phase weapon
python3 gym/combat/collect.py --phase live --case zombie-eye
```

Running without filters collects all three phases. Movement cases compare standing with a fixed sequence of steps, a jump, and a turn. Weapon cases record direct use of each item, with a stationary and a moving trial. Live cases use the normal live brain for kills, survival, and crossing. Each mixed crossing has a version with the same terrain and gear but no enemies.

Use `--resume runs/<folder>` after an interruption in the same Gym session. Completed traces stay in place.

The live brain uses current reported velocities for every enemy and hostile projectile, without retaining previous observations. It requests the game's sampled collision areas for all hostile positive-damage projectiles in both Gym and ordinary worlds. AI styles and attack timers are recorded as raw data and do not select a motion model.

Each run writes a manifest, a summary, and one compressed JSONL file per case under `runs/`. Every case includes the requested setup, a full initial and final game snapshot, and the observed outcome.

- Scripted cases include the requested keys and aim and AgentIO's tick trace. Enemy rows contain slot, position, velocity, life, and four AI values. Projectile rows contain slot, position, velocity, and type ID. The start and end snapshots contain the richer entity metadata.
- Live cases include the actual input snapshots, proposed and applied commands, and phase timings. These snapshots are at decision rounds. Use the scripted cases for tick-level model checks.

The pre-Hardmode and Hardmode labels describe the NPC roster and gear. All trials use the same disposable world. Boss probes sample short windows in stamped arenas. The low-health cases seed the boss's HP before the game runs its own AI.

## Weapon predictions

AgentIO supplies player-adjusted damage, timing, mana cost, ammo choice and projectile properties. The forecast accounts for resources already spent by running commands. Ranged spacing uses player acceleration and observed enemy speed to estimate room to react. Predictions share movement calculations within a round, including already-flying bullets; homing reuse also depends on predicted target positions.

`live-brain/rules/projectile-types.json` contains the installed Terraria 1.4.4.9 projectile defaults. `weapon-behaviors.json` contains spear extension parameters from that version's weapon code. Friendly Chlorophyte and Nano bullets use that version's homing rule. These describe the player's weapons; hostile motion continues the currently observed velocity.

Summons and sentries use an `interact` goal with `operation: "use"`, the staff's `item` name and its `projectile` ID from `bag[].shoot`. Completion checks `count` owned projectiles, defaulting to one. The game controls the summoned creature. Continuous beams, multi-shot patterns and other special homing weapons still need dedicated checks.
