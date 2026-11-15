using System.Collections.Generic;
using System.Collections.Specialized;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;
using Terraria.GameContent;

namespace AgentIO
{
	// The state, as the game holds it this tick. Numbers are the game's own fields, read live: the movement numbers change
	// with gear and with water, the item numbers are the items' own. Most values pass through; Room and the convenience fields below use our code.
	public static class State
	{
		public static string Read(NameValueCollection q)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			int w = Int(q["w"], 80), h = Int(q["h"], 50);
			int x0 = Int(q["x0"], (int)(p.Center.X / 16f) - w / 2), y0 = Int(q["y0"], (int)(p.Center.Y / 16f) - h / 2);
			var state = new Dictionary<string, object>
			{
				["tick"] = Main.GameUpdateCount,
				["game_version"] = Main.versionNumber,
				["world_flags"] = new Dictionary<string, object>
				{
					["expert"] = Main.expertMode, ["master"] = Main.masterMode,
					["remix"] = Main.remixWorld, ["get_good"] = Main.getGoodWorld, ["zenith"] = Main.zenithWorld,
					["slime_rain"] = Main.slimeRain, ["raining"] = Main.raining,
					["blood_moon"] = Main.bloodMoon, ["eclipse"] = Main.eclipse,
					["pumpkin_moon"] = Main.pumpkinMoon, ["snow_moon"] = Main.snowMoon,
				},
				["execution"] = Script.Execution(),
				["time"] = new Dictionary<string, object> { ["day"] = Main.dayTime, ["time"] = Main.time, },
				// where the character comes back to: its bed if it has one set, else the world's spawn point (cells)
				["spawn"] = Main.LocalPlayer != null && Main.LocalPlayer.SpawnX >= 0
					? new[] { Main.LocalPlayer.SpawnX, Main.LocalPlayer.SpawnY } : new[] { Main.spawnTileX, Main.spawnTileY },
				["world_name"] = Main.worldName,
				// the real time in ms when the world was last loaded: it changes with every load, a restart's included
				["session"] = new Dictionary<string, object> { ["loaded"] = Server.Loaded },
				// the rows where the surface ends and the caverns begin: the sky above, the underground's backdrop below
				["surface"] = (int)Main.worldSurface, ["rock"] = (int)Main.rockLayer,
				// the row where the underworld begins (the game's own: 200 rows above the bottom) and the world's height
				["underworld"] = Main.UnderworldLayer, ["world_h"] = Main.maxTilesY,
				// what the world list shows a player before playing: the width in cells (4200 small, 6400 medium, 8400
				// large), which evil the world has (both in a drunk world), the mode
				["world_w"] = Main.maxTilesX,
				["evil"] = Main.drunkWorld ? "both" : WorldGen.crimson ? "crimson" : "corruption",
				["mode"] = Main.GameModeInfo.IsJourneyMode ? "journey" : Main.GameModeInfo.IsMasterMode ? "master"
					: Main.GameModeInfo.IsExpertMode ? "expert" : "classic",
				["biomes"] = Biomes(p),
				["progress"] = Progress(p),
				["body"] = Body(p),
				["physics"] = Physics(p),
				["bag"] = Bag(p),
				// the trash slot: what the last trash put there, until the next trash, a death or leaving the world takes it
				// (the wiki, Inventory: Trash); null when empty (Craft.Trash)
				["trash"] = TrashSlot(p),
				["worn"] = Worn(p),
				["room"] = Room(p),
				["events"] = Events(p),
				["buffs"] = Buffs(p),
				["cells"] = Cells(x0, y0, w, h),
				["enemies"] = Enemies(),
				["shots"] = Shots(),
				["items"] = Items(),
				["counters"] = Counters.Json(),
				["hits"] = Counters.Hits.ToArray(),            // the last 40 hits taken, oldest first (Counters.cs)
				["deaths"] = Counters.DeathsJson(),
				// the game's brightness at the body's middle, 0 to 1: for the light rule (a torch when it is dark)
				["light"] = Brightness(p),
				// the world's multiplier on an enemy's damage, the one the game applies: Journey's enemy strength slider in a
				// Journey world, else the mode's own (1 Classic, 2 Expert, 3 Master). An enemy's damage below has it already
				// (NPC.ScaleStats, when it spawns); a hostile shot hits for twice its damage times it (Projectile.Damage).
				// The slider reads 0.5 in a world of another mode, where the game never uses it
				["strength"] = Main.GameModeInfo.IsJourneyMode
					? Terraria.GameContent.Creative.CreativePowerManager.Instance
						.GetPower<Terraria.GameContent.Creative.CreativePowers.DifficultySliderPower>().StrengthMultiplierToGiveNPCs
					: Main.GameModeInfo.EnemyDamageMultiplier,
			};
			if (q["attack_shapes"] == "1") state["attack_shapes"] = AttackShapes.Sample(x0, y0, w, h, q);
			return Json.Of(state);
		}

		// How far the world and the character have come, by the game's own flags: the bosses beaten (the Wall of Flesh's
		// fall is what starts hardmode, so «wall» is hardmode), the most health without potions or gear (statLifeMax) and
		// the life crystals used, as the game counts them (at most 15, 20 health each)
		static Dictionary<string, object> Progress(Player p) => new()
		{
			["bosses"] = new Dictionary<string, object>
			{
				["eye"] = NPC.downedBoss1, ["king_slime"] = NPC.downedSlimeKing, ["eater_or_brain"] = NPC.downedBoss2,
				["queen_bee"] = NPC.downedQueenBee, ["skeletron"] = NPC.downedBoss3, ["deerclops"] = NPC.downedDeerclops,
				["wall"] = Main.hardMode,
			},
			["hardmode"] = Main.hardMode,
			["life_max_perm"] = p.statLifeMax,
			["life_crystals"] = p.ConsumedLifeCrystals,
		};

		// the player's grappling hook, if one is out: its centre in world px and its state (the game's ai[0]: 0 flying out,
		// 1 coming back, 2 latched). A latched hook pulls the player toward it until the player rests against it.
		static Dictionary<string, object> Hook(Player p)
		{
			for (int i = 0; i < Main.maxProjectiles; i++)
			{
				var pr = Main.projectile[i];
				if (pr == null || !pr.active || pr.owner != p.whoAmI || pr.aiStyle != 7) continue;
				string st = pr.ai[0] == 2f ? "latched" : pr.ai[0] == 1f ? "back" : "flying";
				return new Dictionary<string, object> { ["x"] = pr.Center.X, ["y"] = pr.Center.Y, ["state"] = st };
			}
			return null;
		}

		public static Dictionary<string, object> Body(Player p) => new()
		{
			["player_id"] = p.whoAmI, ["selected"] = p.selectedItem,
			["x"] = p.position.X, ["y"] = p.position.Y, ["w"] = p.width, ["h"] = p.height,
			["vx"] = p.velocity.X, ["vy"] = p.velocity.Y, ["dir"] = p.direction,
			["ground"] = p.velocity.Y == 0f,     // the game's own test: a jump starts only then
			["wet"] = p.wet, ["lava"] = p.lavaWet, ["honey"] = p.honeyWet,
			["rope"] = p.pulley, ["wing_time"] = p.wingTime, ["hooked"] = p.grapCount > 0,   // on a rope, flight left, on a hook (Oct 20)
			["hook"] = Hook(p),                  // the hook out: where it is and whether it has latched (Oct 20)
			["life"] = p.statLife, ["life_max"] = p.statLifeMax2, ["breath"] = p.breath,
			["life_regen"] = p.lifeRegen, ["life_regen_count"] = p.lifeRegenCount,
			["mana"] = p.statMana, ["mana_max"] = p.statManaMax2,
			["no_knockback"] = p.noKnockback, ["long_invince"] = p.longInvince,
			["damage_reduction"] = p.endurance, ["gravity_dir"] = p.gravDir,
			["height_offset"] = p.HeightOffsetHitboxCenter,
			["solar_set"] = p.setSolar, ["solar_shields"] = p.solarShields,
			["beetle_defense"] = p.beetleDefense, ["beetle_orbs"] = p.beetleOrbs,
			["cart"] = p.mount.Active && p.mount.Cart,
			["hurt_cooldowns"] = (int[])p.hurtCooldowns.Clone(),
			["defense"] = (int)p.statDefense,
			// the share of the defense a hit loses: 0.5 in Classic and Journey, 0.75 Expert, 1 Master
			["defense_eff"] = p.DefenseEffectiveness.Value,
			["jump"] = p.jump, ["fall_start"] = p.fallStart,
			["immune"] = p.immune, ["immune_time"] = p.immuneTime, ["potion_delay"] = p.potionDelay,
			["item_animation"] = p.itemAnimation, ["item_animation_max"] = p.itemAnimationMax,
			["item_time"] = p.itemTime, ["held_item"] = p.HeldItem.type,
			["attack_cooldown"] = p.attackCD,
			["channel"] = p.channel, ["mana_regen_delay"] = p.manaRegenDelay,
			["minion_slots"] = p.slotsMinions, ["max_minions"] = p.maxMinions, ["max_sentries"] = p.maxTurrets,
			["whip_range"] = p.whipRangeMultiplier, ["arm_offset"] = ArmOffset(p),
			["dead"] = p.dead, ["chest"] = p.chest,
		};

		static readonly Projectile ArmProbe = new Projectile();
		static float[] ArmOffset(Player p)
		{
			ArmProbe.owner = p.whoAmI;
			var arm = Main.GetPlayerArmPosition(ArmProbe) - p.position;
			return new[] { arm.X, arm.Y };
		}

		public static Dictionary<string, object> Physics(Player p)
		{
			var result = new Dictionary<string, object>
			{
			["run_acc"] = p.runAcceleration, ["run_top"] = p.maxRunSpeed, ["run_slow"] = p.runSlowdown,
			["jump_speed"] = Player.jumpSpeed, ["jump_hold"] = Player.jumpHeight,
			["gravity"] = p.gravity, ["fall_top"] = p.maxFallSpeed, ["extra_fall"] = p.extraFall,
			["no_fall_damage"] = p.noFallDmg, ["range_x"] = Player.tileRangeX, ["range_y"] = Player.tileRangeY,
			["block_range"] = p.blockRange, ["wing_time_max"] = p.wingTimeMax,
			["wing_acc_mult"] = p.wingsLogic > 0 ? p.GetWingStats(p.wingsLogic).AccRunAccelerationMult : 1f,
			["wing_run_top"] = p.wingsLogic > 0 ? p.GetWingStats(p.wingsLogic).AccRunSpeedOverride : -1f,
			["wing_air"] = p.wingsLogic > 0 && p.velocity.Y != 0f && !p.merman && !p.mount.Active && p.grappling[0] == -1 && !p.tongued,
			// what the gear and potions make safe: lava (Obsidian Skin, Lava Charm's remaining ticks in lava_time,
			// Lava Waders), hot blocks (Obsidian Skull: fire_walk), swimming (Flipper), breathing (Gills)
			["lava_immune"] = p.lavaImmune, ["fire_walk"] = p.fireWalk, ["lava_time"] = p.lavaTime,
			["flipper"] = p.accFlipper, ["gills"] = p.gills, ["ignore_water"] = p.ignoreWater,
			// gear and potions change these: boots the run's top (acc_run), a Mining Potion pick_speed (the factor on a
			// pickaxe's use time), a Brick Layer tile_speed (on a block's)
			["acc_run"] = p.accRunSpeed, ["pick_speed"] = p.pickSpeed,
			["tile_speed"] = p.tileSpeed,
			};
			var wings = p.GetModPlayer<WingReadout>();
			if (wings.Wing == p.wingsLogic && p.wingsLogic > 0)
				foreach (var pair in wings.Vertical) result[pair.Key] = pair.Value;
			return result;
		}

		public static List<Dictionary<string, object>> Bag(Player p)
		{
			var bag = new List<Dictionary<string, object>>();
			for (int i = 0; i < 58; i++)
			{
				var it = p.inventory[i];
				if (it == null || it.IsAir) continue;
				var row = Item(it, i);
				if (it.damage > 0 && it.ammo == 0)
				{
					var combat = new Dictionary<string, object>
					{
						["damage"] = p.GetWeaponDamage(it), ["knockback"] = p.GetWeaponKnockback(it),
						["use_time"] = CombinedHooks.TotalUseTime(it.useTime, p, it),
						["use_anim"] = CombinedHooks.TotalAnimationTime(it.useAnimation, p, it),
						["reuse_delay"] = (int)(it.reuseDelay / CombinedHooks.TotalUseSpeedMultiplier(p, it)),
						["scale"] = p.GetAdjustedItemScale(it),
						["mana_cost"] = p.GetManaCost(it), ["crit"] = p.GetWeaponCrit(it),
						["owned_projectiles"] = it.shoot > 0 ? p.ownedProjectileCounts[it.shoot] : 0,
					};
					var drawFrame = Terraria.Item.GetDrawHitbox(it.type, p);
					combat["draw_frame"] = new[] { drawFrame.Width, drawFrame.Height };
					combat["claw"] = Terraria.Item.claw[it.type];
					combat["better_melee_location"] = ItemID.Sets.UsesBetterMeleeItemLocation[it.type];
					// Preview the same ammo selection and modifiers the next shot uses, without consuming ammo.
					int selected = p.selectedItem;
					try
					{
						p.selectedItem = i;
						if (it.useAmmo > 0 && p.PickAmmo(it, out int type, out float speed, out int damage,
							out float knockback, out int ammoId, dontConsume: true) && ContentSamples.ProjectilesByType.TryGetValue(type, out var sample))
						{
							var shot = Flight(sample, p);
							shot["speed"] = speed; shot["damage"] = damage; shot["knockback"] = knockback;
							shot["ammo_slot"] = System.Array.IndexOf(p.inventory, p.ChooseAmmo(it));
							combat["shot"] = shot;
						}
						else if (it.useAmmo == 0 && it.shoot > 0 && ContentSamples.ProjectilesByType.TryGetValue(it.shoot, out var directSample))
						{
							var shot = Flight(directSample, p);
							float directSpeed = it.shootSpeed;
							if (it.CountsAsClass(DamageClass.Melee) && !ProjectileID.Sets.NoMeleeSpeedVelocityScaling[it.shoot])
								directSpeed *= p.GetTotalAttackSpeed(DamageClass.Melee);
							shot["speed"] = directSpeed; shot["damage"] = p.GetWeaponDamage(it);
							shot["knockback"] = p.GetWeaponKnockback(it);
							combat["shot"] = shot;
						}
					}
					finally { p.selectedItem = selected; }
					row["combat"] = combat;
				}
				bag.Add(row);
			}
			return bag;
		}

		static Dictionary<string, object> TrashSlot(Player p) => p.trashItem == null || p.trashItem.IsAir ? null
			: new Dictionary<string, object> { ["id"] = p.trashItem.type, ["name"] = p.trashItem.Name, ["stack"] = p.trashItem.stack };

		public static Dictionary<string, object> Item(Item it, int slot) => new()
		{
			["slot"] = slot, ["id"] = it.type, ["name"] = it.Name, ["stack"] = it.stack,
			["use_time"] = it.useTime, ["use_anim"] = it.useAnimation, ["use_style"] = it.useStyle,
			["place_tile"] = it.createTile, ["tile_boost"] = it.tileBoost,
			// a building block, by the game's own tables: the tile it places is solid and one cell (not a furnace, a
			// torch, a platform or a chest: the fast brain took a furnace for its blocks, Oct 26)
			["block_tile"] = it.createTile >= 0 && Main.tileSolid[it.createTile] && !Main.tileSolidTop[it.createTile] &&
			                 !Main.tileFrameImportant[it.createTile],
			["pick"] = it.pick, ["axe"] = it.axe, ["hammer"] = it.hammer,
			["damage"] = it.damage, ["knockback"] = it.knockBack,
			["no_melee"] = it.noMelee, ["no_use_graphic"] = it.noUseGraphic,
			["consumable"] = it.consumable, ["mana_cost"] = it.mana,
			["channel"] = it.channel, ["auto_reuse"] = it.autoReuse,
			["damage_class"] = it.DamageType.Name, ["buff_type"] = it.buffType, ["buff_time"] = it.buffTime,
			["base_use_anim"] = it.useAnimation,
			["width"] = it.width, ["height"] = it.height,
			["shoot"] = it.shoot, ["shoot_speed"] = it.shootSpeed, ["use_ammo"] = it.useAmmo, ["ammo"] = it.ammo,
			["heal"] = it.healLife,
			// a grappling hook: the game's own flag for the shot it fires (Main.projHook, what the grapple key looks for)
			["hook"] = it.shoot > 0 && it.shoot < Main.projHook.Length && Main.projHook[it.shoot],
		};

		// The space around the character that enemies can walk or fly through. The search starts at the body and
		// way the game's own housing check spreads, with the safety rules: a solid block stops it (a platform or an
		// actuated block does not), every door stops it as part of the frame and is reported open or shut, and past
		// 750 cells (the game's room limit) the space counts as open to the world. inside: a closed room, doors shut.
		static readonly HashSet<int> DoorTiles = new() { TileID.ClosedDoor, TileID.OpenDoor, TileID.TallGateClosed,
			TileID.TallGateOpen, TileID.TrapdoorClosed, TileID.TrapdoorOpen };
		static readonly HashSet<int> OpenDoorTiles = new() { TileID.OpenDoor, TileID.TallGateOpen, TileID.TrapdoorOpen };

		static bool Blocks(int x, int y)
		{
			var t = Main.tile[x, y];
			return t.HasTile && !t.IsActuated && Main.tileSolid[t.TileType] && !Main.tileSolidTop[t.TileType];
		}

		static bool IsDoor(int x, int y) { var t = Main.tile[x, y]; return t.HasTile && DoorTiles.Contains(t.TileType); }

		static Dictionary<string, object> Room(Player p)
		{
			const int Limit = 750;
			int x0 = (int)(p.position.X / 16f), x1 = (int)((p.position.X + p.width - 1) / 16f);
			int y0 = (int)(p.position.Y / 16f), y1 = (int)((p.position.Y + p.height - 1) / 16f);
			var seen = new HashSet<(int, int)>();
			var todo = new Queue<(int, int)>();
			var doorCells = new HashSet<(int, int)>();
			for (int x = x0; x <= x1; x++)
				for (int y = y0; y <= y1; y++)
					if (WorldGen.InWorld(x, y, 2) && !Blocks(x, y) && !IsDoor(x, y) && seen.Add((x, y))) todo.Enqueue((x, y));
			bool open = false;
			int bx1 = int.MaxValue, by1 = int.MaxValue, bx2 = int.MinValue, by2 = int.MinValue;
			var steps = new[] { (1, 0), (-1, 0), (0, 1), (0, -1) };
			while (todo.Count > 0 && !open)
			{
				var (cx, cy) = todo.Dequeue();
				if (cx < bx1) bx1 = cx; if (cx > bx2) bx2 = cx; if (cy < by1) by1 = cy; if (cy > by2) by2 = cy;
				foreach (var (dx, dy) in steps)
				{
					int nx = cx + dx, ny = cy + dy;
					if (!WorldGen.InWorld(nx, ny, 2)) { open = true; break; }
					if (seen.Contains((nx, ny))) continue;
					if (IsDoor(nx, ny)) { doorCells.Add((nx, ny)); continue; }
					if (Blocks(nx, ny)) continue;
					seen.Add((nx, ny));
					if (seen.Count > Limit) { open = true; break; }
					todo.Enqueue((nx, ny));
				}
			}
			// the door cells in doors: the cells of one door touch; each door by its top left cell, open if any is
			var doors = new List<object[]>();
			var left = new HashSet<(int, int)>(doorCells);
			while (left.Count > 0)
			{
				var first = default((int, int)); foreach (var c in left) { first = c; break; }
				var part = new List<(int, int)>(); var q = new Queue<(int, int)>(); q.Enqueue(first); left.Remove(first);
				while (q.Count > 0)
				{
					var c = q.Dequeue(); part.Add(c);
					for (int ddx = -1; ddx <= 1; ddx++)
						for (int ddy = -1; ddy <= 1; ddy++)
							if (left.Remove((c.Item1 + ddx, c.Item2 + ddy))) q.Enqueue((c.Item1 + ddx, c.Item2 + ddy));
				}
				int mx = int.MaxValue, my = int.MaxValue; bool isOpen = false;
				foreach (var (dxx, dyy) in part)
				{
					if (dxx < mx || (dxx == mx && dyy < my)) { mx = dxx; my = dyy; }
					if (OpenDoorTiles.Contains(Main.tile[dxx, dyy].TileType)) isOpen = true;
				}
				doors.Add(new object[] { mx, my, isOpen ? "open" : "shut" });
			}
			bool enclosed = !open;
			bool shut = true; foreach (var d in doors) if ((string)d[2] == "open") shut = false;
			return new Dictionary<string, object>
			{
				["enclosed"] = enclosed, ["inside"] = enclosed && shut, ["cells"] = seen.Count,
				["box"] = enclosed ? new[] { bx1, by1, bx2, by2 } : null, ["doors"] = enclosed ? doors : new List<object[]>(),
			};
		}

		// the game's own flags for the times enemies open doors or come in numbers (the wiki: Blood Moon zombies and
		// skeletons, the Goblin Army, now and then in a graveyard), and the other events
		static List<string> Events(Player p)
		{
			var e = new List<string>();
			if (Main.bloodMoon) e.Add("blood moon");
			if (Main.eclipse) e.Add("solar eclipse");
			if (Main.invasionType == InvasionID.GoblinArmy) e.Add("goblin army");
			else if (Main.invasionType == InvasionID.SnowLegion) e.Add("frost legion");
			else if (Main.invasionType == InvasionID.PirateInvasion) e.Add("pirate invasion");
			else if (Main.invasionType == InvasionID.MartianMadness) e.Add("martian madness");
			if (Main.pumpkinMoon) e.Add("pumpkin moon");
			if (Main.snowMoon) e.Add("frost moon");
			if (Main.slimeRain) e.Add("slime rain");
			if (p.ZoneGraveyard) e.Add("graveyard");
			return e;
		}

		// the biome the body is in, by the game's own flags (Player.UpdateBiomes, from block counts the game redoes as it
		// draws, about every 4th lighting pass, and after a spawn or a teleport): the ones that pick the music and draw
		// the backdrop, so a player has them too. Each is on when enough of its blocks lie in the 169 by 124 cells around
		// the body, seen or not (SceneMetrics: 300 for the Corruption or the Crimson, 140 Jungle, 1,500 snow or desert,
		// 100 glowing mushrooms, 75 a meteorite). Never the counts: they would tell more than a player gets
		static List<string> Biomes(Player p)
		{
			var z = new List<string>();
			if (p.ZoneForest) z.Add("forest");
			if (p.ZoneCorrupt) z.Add("corruption");
			if (p.ZoneCrimson) z.Add("crimson");
			if (p.ZoneHallow) z.Add("hallow");
			if (p.ZoneJungle) z.Add("jungle");
			if (p.ZoneSnow) z.Add("snow");
			if (p.ZoneDesert) z.Add("desert");
			if (p.ZoneUndergroundDesert) z.Add("underground desert");
			if (p.ZoneBeach) z.Add("ocean");
			if (p.ZoneDungeon) z.Add("dungeon");
			if (p.ZoneGlowshroom) z.Add("glowing mushroom");
			if (p.ZoneMeteor) z.Add("meteorite");
			if (p.ZoneGranite) z.Add("granite cave");
			if (p.ZoneMarble) z.Add("marble cave");
			if (p.ZoneHive) z.Add("hive");
			if (p.ZoneGemCave) z.Add("gem cave");
			if (p.ZoneLihzhardTemple) z.Add("jungle temple");
			if (p.ZoneShimmer) z.Add("shimmer");
			return z;
		}

		static List<string> Worn(Player p)
		{
			var worn = new List<string>();
			for (int a = 0; a < 10; a++)
				if (p.armor[a] != null && !p.armor[a].IsAir) worn.Add(p.armor[a].Name);
			return worn;
		}

		static List<Dictionary<string, object>> Buffs(Player p)
		{
			var buffs = new List<Dictionary<string, object>>();
			for (int b = 0; b < Player.MaxBuffs; b++)
				if (p.buffType[b] > 0)
					buffs.Add(new() { ["id"] = p.buffType[b], ["name"] = Lang.GetBuffName(p.buffType[b]), ["time"] = p.buffTime[b] });
			return buffs;
		}

		// The cells in a box, row by row: the tile's type (-1 none), its shape (0 full, 1 to 4 the slopes, 5 a half
		// block), the liquid's amount (0 to 255) and kind (0 water, 1 lava, 2 honey, 3 shimmer), the back wall, and
		// whether the character has seen the cell. kinds: name and solidity of each tile type present
		public static Dictionary<string, object> Cells(int x0, int y0, int w, int h)
		{
			var tile = new int[h][]; var shape = new int[h][]; var liquid = new int[h][]; var kind = new int[h][];
			var wall = new int[h][]; var seen = new int[h][]; var fx = new int[h][]; var fy = new int[h][];
			var kinds = new Dictionary<string, object>();
			for (int r = 0; r < h; r++)
			{
				tile[r] = new int[w]; shape[r] = new int[w]; liquid[r] = new int[w]; kind[r] = new int[w];
				wall[r] = new int[w]; seen[r] = new int[w]; fx[r] = new int[w]; fy[r] = new int[w];
				for (int c = 0; c < w; c++)
				{
					int x = x0 + c, y = y0 + r;
					if (!WorldGen.InWorld(x, y)) { tile[r][c] = -2; continue; }
					var t = Main.tile[x, y];
					tile[r][c] = t.HasTile ? t.TileType : -1;
					shape[r][c] = t.IsHalfBlock ? 5 : (int)t.Slope;
					liquid[r][c] = t.LiquidAmount;
					kind[r][c] = t.LiquidType;
					wall[r][c] = t.WallType;
					// which picture of the tile's sprite sheet the game draws there (for the replay page)
					if (t.HasTile) { fx[r][c] = t.TileFrameX; fy[r][c] = t.TileFrameY; }
					seen[r][c] = Main.Map != null && Main.Map.IsRevealed(x, y) ? 1 : 0;
					if (t.HasTile && !kinds.ContainsKey(t.TileType.ToString()))
						kinds[t.TileType.ToString()] = new Dictionary<string, object>
						{
							["name"] = TileID.Search.GetName(t.TileType), ["solid"] = Main.tileSolid[t.TileType],
							["solid_top"] = Main.tileSolidTop[t.TileType],
							// a torch, a plant, a cobweb: a block beside it finds nothing to hold on to (rule «place»)
							["no_attach"] = Main.tileNoAttach[t.TileType],
							// a plant, a vine, a cobweb: a block set into its cell cuts it (the game's tileCut; Player.cs's placement
							// condition takes a cut tile as an empty cell)
							["cut"] = Main.tileCut[t.TileType],
							// what one hit of the bag's best pickaxe does to it, by the game's own function, and whether
							// the game lets it break at all (a block under a tree, a chest or an altar does not)
							["pick_damage"] = PickDamage(x, y), ["can_break"] = WorldGen.CanKillTile(x, y),
							["drops"] = Drops(t.TileType),     // the item it breaks into, or null (below)
						};
				}
			}
			return new Dictionary<string, object>
			{
				["x0"] = x0, ["y0"] = y0, ["w"] = w, ["h"] = h, ["tile"] = tile, ["shape"] = shape, ["liquid"] = liquid,
				["liquid_kind"] = kind, ["wall"] = wall, ["seen"] = seen, ["kinds"] = kinds, ["fx"] = fx, ["fy"] = fy,
			};
		}

		static List<Dictionary<string, object>> Enemies()
		{
			var list = new List<Dictionary<string, object>>();
			for (int i = 0; i < Main.maxNPCs; i++)
			{
				var n = Main.npc[i];
				if (n == null || !n.active || n.friendly || n.townNPC) continue;
				// only what can hurt, within 100 cells: a real world lists critters and enemies far off (Oct 20)
				if (n.CountsAsACritter || n.damage <= 0) continue;
				var me = Main.LocalPlayer;
				if (me != null && Vector2.Distance(n.Center, me.Center) > 1600f) continue;
				// Player.Update_NPCCollision, Terraria 1.4.4.9: a separate
				// protection counter for these contact sources.
				int slot = n.type is 396 or 397 or 398 or 400 or 401 or 636 ? 1 : -1;
				float multiplier = 1f;
				var contactBox = n.Hitbox;
				NPC.GetMeleeCollisionData(me.Hitbox, i, ref slot, ref multiplier, ref contactBox);
				list.Add(new()
				{
					["i"] = i, ["id"] = n.type, ["net_id"] = n.netID, ["name"] = n.TypeName, ["x"] = n.position.X, ["y"] = n.position.Y,
					["w"] = n.width, ["h"] = n.height, ["vx"] = n.velocity.X, ["vy"] = n.velocity.Y,
					["life"] = n.life, ["life_max"] = n.lifeMax, ["damage"] = n.damage, ["defense"] = n.defense,
					["hurt_slot"] = slot,
					["kb_taken"] = n.knockBackResist, ["ai"] = n.aiStyle, ["through_blocks"] = n.noTileCollide,
					["flies"] = n.noGravity, ["boss"] = n.boss,
					["wet"] = n.wet, ["collide_x"] = n.collideX, ["collide_y"] = n.collideY,
					["gravity"] = n.gravity, ["fall_top"] = n.maxFallSpeed,
					["dir"] = n.direction, ["dir_y"] = n.directionY,
					["old_vx"] = n.oldVelocity.X, ["old_x"] = n.oldPosition.X,
					["old_vy"] = n.oldVelocity.Y,
					["scale"] = n.scale,
					["strength_multiplier"] = n.strengthMultiplier,
					["just_hit"] = n.justHit, ["rotation"] = n.rotation,
					["no_aggro"] = me.npcTypeNoAggro[n.type], ["dont_take_damage"] = n.dontTakeDamage,
					["chaseable"] = n.CanBeChasedBy(),
					["chases"] = n.aiStyle != 3 || NPC.DespawnEncouragement_AIStyle3_Fighters_NotDiscouraged(n.type, n.position, n),
					["base_damage"] = n.defDamage, ["base_defense"] = n.defDefense,
					["melee_immune"] = Main.LocalPlayer.meleeNPCHitCooldown[i], ["owner_immune"] = n.immune[Main.myPlayer],
					// the picture of its sprite sheet the game draws now, and which way it faces (for the replay page)
					["frame"] = new[] { n.frame.X, n.frame.Y, n.frame.Width, n.frame.Height }, ["sdir"] = n.spriteDirection,
					["timers"] = new[] { n.ai[0], n.ai[1], n.ai[2], n.ai[3] },   // the game's own AI counters: a slime's
					                                                              // countdown to its next hop (Oct 20)
					["local_ai"] = new[] { n.localAI[0], n.localAI[1], n.localAI[2], n.localAI[3] },
				});
			}
			return list;
		}

		static float Brightness(Player p)
		{
			var c = Terraria.Lighting.GetColor((int)(p.Center.X / 16f), (int)(p.Center.Y / 16f));
			return System.Math.Max(c.R, System.Math.Max(c.G, c.B)) / 255f;
		}

		// every item lying in the world
		public static List<Dictionary<string, object>> Items()
		{
			var list = new List<Dictionary<string, object>>();
			for (int i = 0; i < Main.maxItems; i++)
			{
				var it = Main.item[i];
				if (it == null || !it.active) continue;
				list.Add(new()
				{
					["i"] = i, ["id"] = it.type, ["name"] = it.Name, ["stack"] = it.stack, ["x"] = it.position.X,
					["y"] = it.position.Y, ["w"] = it.width, ["h"] = it.height, ["vx"] = it.velocity.X, ["vy"] = it.velocity.Y,
				});
			}
			return list;
		}

		// every projectile in flight: the enemies' shots and the body's own arrows
		static Dictionary<string, object> Flight(Projectile s, Player player = null)
		{
			var row = new Dictionary<string, object>
			{
			["id"] = s.type, ["w"] = s.width, ["h"] = s.height, ["ai"] = s.aiStyle, ["ai0"] = s.ai[0], ["ai1"] = s.ai[1],
			// Projectile.Damage, Terraria 1.4.4.9. This selects a real player
			// counter; it does not predict this projectile's behavior.
			["hurt_slot"] = s.ModProjectile?.CooldownSlot ??
				(s.type is 452 or 454 or 455 or 462 or 871 or 872 or 873 or 874 or 919 or 923 or 924 ? 1 : -1),
			["scale"] = s.scale,
			["arrow"] = s.arrow, ["tile_collide"] = s.tileCollide, ["extra_updates"] = s.extraUpdates,
        ["time_left"] = s.timeLeft, ["penetrate"] = s.penetrate, ["max_penetrate"] = s.maxPenetrate,
			["uses_local_npc_immunity"] = s.usesLocalNPCImmunity, ["local_npc_hit_cooldown"] = s.localNPCHitCooldown,
			["uses_static_npc_immunity"] = s.usesIDStaticNPCImmunity, ["static_npc_hit_cooldown"] = s.idStaticNPCHitCooldown,
			["owner_hit_check"] = s.ownerHitCheck, ["ignore_water"] = s.ignoreWater,
			["wet"] = s.wet,
			["minion"] = s.minion, ["sentry"] = s.sentry, ["minion_slots"] = s.minionSlots,
			["whip"] = ProjectileID.Sets.IsAWhip[s.type],
			["whip_segments"] = s.WhipSettings.Segments, ["whip_range"] = s.WhipSettings.RangeMultiplier,
			["yoyo_range"] = ProjectileID.Sets.YoyosMaximumRange[s.type],
			["yoyo_speed"] = ProjectileID.Sets.YoyosTopSpeed[s.type],
			["yoyo_life"] = ProjectileID.Sets.YoyosLifeTimeMultiplier[s.type],
			["local_ai"] = new[] { s.localAI[0], s.localAI[1] },
			["rotation"] = s.rotation, ["direction"] = s.direction, ["sprite_direction"] = s.spriteDirection,
			};
			if (player != null && s.aiStyle == 99)
			{
				float reach = ProjectileID.Sets.YoyosMaximumRange[s.type];
				if (player.yoyoString) reach = reach * 1.25f + 30f;
				float factor = (1f + (1f / player.GetTotalAttackSpeed(DamageClass.Melee)) * 3f) / 4f;
				row["yoyo_range"] = reach / factor;
				row["yoyo_speed"] = ProjectileID.Sets.YoyosTopSpeed[s.type] / factor;
			}
			if (s.aiStyle == 16)
			{
				var blast = new Projectile(); blast.SetDefaults(s.type);
				blast.damage = s.damage; blast.knockBack = s.knockBack;
				blast.PrepareBombToBlow();
				row["blast_w"] = blast.width; row["blast_h"] = blast.height;
				row["self_hurt"] = ProjectileID.Sets.Explosive[s.type];
			}
			if (s.usesLocalNPCImmunity)
			{
				var immunity = new Dictionary<string, int>();
				for (int i = 0; i < Main.maxNPCs; i++)
					if (s.localNPCImmunity[i] != 0) immunity[i.ToString()] = s.localNPCImmunity[i];
				row["local_immune"] = immunity;
			}
			return row;
		}

		public static List<Dictionary<string, object>> Shots()
		{
			var list = new List<Dictionary<string, object>>();
			for (int i = 0; i < Main.maxProjectiles; i++)
			{
				var s = Main.projectile[i];
				if (s == null || !s.active) continue;
				var row = Flight(s, s.owner >= 0 && s.owner < Main.maxPlayers ? Main.player[s.owner] : null);
				row["i"] = i; row["name"] = s.Name; row["x"] = s.position.X; row["y"] = s.position.Y;
				row["vx"] = s.velocity.X; row["vy"] = s.velocity.Y;
				row["damage"] = s.damage; row["knockback"] = s.knockBack;
				row["hostile"] = s.hostile; row["friendly"] = s.friendly; row["owner"] = s.owner;
				list.Add(row);
			}
			return list;
		}

		// the game's own Player.GetPickaxeDamage, which is not public: called by reflection, so the number is the game's
		static System.Reflection.MethodInfo _pickDamage;

		static int PickDamage(int x, int y)
		{
			var p = Main.LocalPlayer;
			int best = 0;
			for (int i = 0; i < 58; i++)
				if (p.inventory[i] != null && !p.inventory[i].IsAir && p.inventory[i].pick > best) best = p.inventory[i].pick;
			if (best <= 0) return 0;
			_pickDamage ??= typeof(Player).GetMethod("GetPickaxeDamage",
				System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Public);
			if (_pickDamage == null) return -1;
			var args = _pickDamage.GetParameters().Length == 5
				? new object[] { x, y, best, 0, Main.tile[x, y] }
				: new object[] { x, y, best, Main.tile[x, y] };
			return (int)_pickDamage.Invoke(p, args);
		}

		// The item a tile kind breaks into, by its name, read once from the game's own drop lookup
		// (WorldGen.KillTile_GetItemDrops): in its switch on the kind, a kind with one fixed drop is a case that only sets
		// the item and returns. The lookup is not called on a cell: for some kinds it spawns bees, breaks a pressure
		// plate or rolls the world's dice. A kind with no fixed drop there gives null: one whose drop hangs on its style,
		// its cell or chance (a torch, a tree), and the objects of several cells, which drop through other code (a chest,
		// a pot, a life crystal, an altar).
		static Dictionary<int, int> _drops;

		static string Drops(int kind)
		{
			_drops ??= ReadDrops();
			return _drops.TryGetValue(kind, out int item) && item > 0 ? Lang.GetItemNameValue(item) : null;
		}

		static Dictionary<int, int> ReadDrops()
		{
			var table = new Dictionary<int, int>();
			var log = Terraria.ModLoader.ModContent.GetInstance<AgentIO>().Logger;
			try
			{
				var m = typeof(WorldGen).GetMethod("KillTile_GetItemDrops",
					System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
				using var dmd = new MonoMod.Utils.DynamicMethodDefinition(m);
				Mono.Cecil.Cil.Instruction big = null;
				foreach (var ins in dmd.Definition.Body.Instructions)
					if (ins.OpCode == Mono.Cecil.Cil.OpCodes.Switch &&
					    (big == null || ((Mono.Cecil.Cil.Instruction[])ins.Operand).Length > ((Mono.Cecil.Cil.Instruction[])big.Operand).Length))
						big = ins;
				if (big != null)
				{
					// the cases count from 0, unless the compiler took the first kind off just before the switch
					int first = big.Previous?.OpCode == Mono.Cecil.Cil.OpCodes.Sub && big.Previous.Previous != null &&
					            MonoMod.Cil.ILPatternMatchingExt.MatchLdcI4(big.Previous.Previous, out int k) ? k : 0;
					var cases = (Mono.Cecil.Cil.Instruction[])big.Operand;
					for (int i = 0; i < cases.Length; i++)
					{
						var a = cases[i];     // dropItem = N; return (dropItem is the lookup's fourth argument)
						if (a.OpCode == Mono.Cecil.Cil.OpCodes.Ldarg_3 && a.Next != null &&
						    MonoMod.Cil.ILPatternMatchingExt.MatchLdcI4(a.Next, out int item) &&
						    a.Next.Next?.OpCode == Mono.Cecil.Cil.OpCodes.Stind_I4 && a.Next.Next.Next?.OpCode == Mono.Cecil.Cil.OpCodes.Ret)
							table[first + i] = item;
					}
				}
				log.Info("drops: " + table.Count + " tile kinds with one fixed drop, read from the game's drop lookup");
			}
			catch (System.Exception e) { log.Warn("drops: the game's drop lookup could not be read, every drops is null: " + e); }
			return table;
		}

		static int Int(string s, int fallback) => int.TryParse(s, out int v) ? v : fallback;
	}

	// Read the parameters when the real player uses the equipped wings.
	public class WingReadout : ModPlayer
	{
		public int Wing;
		public Dictionary<string, object> Vertical = new();
	}

	public class WingNumbers : GlobalItem
	{
		public override void VerticalWingSpeeds(Item item, Player player, ref float ascentWhenFalling,
			ref float ascentWhenRising, ref float maxCanAscendMultiplier, ref float maxAscentMultiplier, ref float constantAscend)
		{
			var readout = player.GetModPlayer<WingReadout>();
			readout.Wing = player.wingsLogic;
			readout.Vertical["wing_constant"] = constantAscend;
			readout.Vertical["wing_falling"] = ascentWhenFalling;
			readout.Vertical["wing_rising"] = ascentWhenRising;
			readout.Vertical["wing_rise_limit"] = maxCanAscendMultiplier;
			readout.Vertical["wing_top"] = maxAscentMultiplier;
		}
	}
}
