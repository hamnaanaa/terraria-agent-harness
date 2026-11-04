using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Text.Json;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace AgentIO
{
	// The test gym: a map stamped into the world, the body put on a cell, its bag and gear set, the clock and the enemies
	// held still. Ported from v1's gym dials (Oct 14 to 17), which ran thousands of bench runs.
	public class Gym : ModSystem
	{
		public static bool Frozen;
		public static double FrozenTime = 27000;     // noon
		public static bool FrozenDay = true;
		public static float SpawnScale = 1f;         // 0: no enemies come; 1: the game's own rate
		// the game pauses itself whenever its window is not in front (v1, Oct 15: the clock stood still the moment another
		// app came forward); with this on it is told its window is always active
		public static bool RunInBackground = true;

		public override void Load()
		{
			// Main.DoUpdate sets Main.hasFocus from the window, and in single player a tick without focus pauses the game.
			// Right after each write of the flag it is set again while RunInBackground is on (v1: a hook on the window's
			// IsActive getter did nothing, the call is inlined)
			Terraria.IL_Main.DoUpdate += il =>
			{
				var c = new MonoMod.Cil.ILCursor(il);
				int n = 0;
				while (c.TryGotoNext(MonoMod.Cil.MoveType.After,
					       i => MonoMod.Cil.ILPatternMatchingExt.MatchStsfld(i, typeof(Main), nameof(Main.hasFocus))))
				{
					c.EmitDelegate<System.Action>(() => { if (RunInBackground) Main.hasFocus = true; });
					n++;
				}
				if (n == 0) Mod.Logger.Warn("gym: no write of Main.hasFocus found; the game will pause in the background");
			};
		}

		public override void PostUpdateTime()
		{
			if (!Frozen) return;
			Main.dayTime = FrozenDay;
			Main.time = FrozenTime;
		}

		// POST /stamp {"x0": 2150, "y0": 150, "rows": ["....", "####"], "clear": [x1, y1, x2, y2], "reveal": true}
		// rows[r][c] is the cell (x0 + c, y0 + r): '.' air, '#' dirt, '%' stone, '=' wood platform, '~' water, 'L' lava,
		// 'h' honey, '|' rope, '/' and '\' dirt sloped up to the right and up to the left, 'g' grass, 's' sand, 'i' ice,
		// 'm' mud, 'j' jungle grass, 'e' ebonstone, 'x' jungle thorns, 'w' cobweb, '^' spikes, 'H' hellstone, 'B' a back
		// wall of dirt behind air, 't' a torch (set last, on the block beside or under it), '?' left as it is. clear: a
		// box emptied first (tiles, walls, liquids, dropped items).
		public static string Stamp(string body)
		{
			using var doc = JsonDocument.Parse(body);
			var root = doc.RootElement;
			int x0 = root.GetProperty("x0").GetInt32(), y0 = root.GetProperty("y0").GetInt32();
			var rows = new List<string>();
			foreach (var r in root.GetProperty("rows").EnumerateArray()) rows.Add(r.GetString());
			bool reveal = !root.TryGetProperty("reveal", out var rv) || rv.GetBoolean();
			int bx1 = x0, by1 = y0, bx2 = x0, by2 = y0 + rows.Count - 1, cells = 0, removed = 0;
			var torches = new List<(int, int)>();
			var trees = new List<(int, int)>();
			var objects = new List<(char, int, int)>();
			foreach (var row in rows) bx2 = System.Math.Max(bx2, x0 + row.Length - 1);
			if (root.TryGetProperty("clear", out var cl))
			{
				int c1 = cl[0].GetInt32(), r1 = cl[1].GetInt32(), c2 = cl[2].GetInt32(), r2 = cl[3].GetInt32();
				for (int x = c1; x <= c2; x++)
					for (int y = r1; y <= r2; y++)
						if (WorldGen.InWorld(x, y, 10)) Main.tile[x, y].ClearEverything();
				for (int i = 0; i < Main.maxItems; i++)
				{
					var it = Main.item[i];
					if (it == null || !it.active) continue;
					int ix = (int)(it.Center.X / 16f), iy = (int)(it.Center.Y / 16f);
					if (ix >= c1 && ix <= c2 && iy >= r1 && iy <= r2) { it.active = false; removed++; }
				}
				bx1 = System.Math.Min(bx1, c1); by1 = System.Math.Min(by1, r1);
				bx2 = System.Math.Max(bx2, c2); by2 = System.Math.Max(by2, r2);
			}
			for (int r = 0; r < rows.Count; r++)
				for (int c = 0; c < rows[r].Length; c++)
				{
					char ch = rows[r][c];
					int x = x0 + c, y = y0 + r;
					if (ch == '?' || !WorldGen.InWorld(x, y, 10)) continue;
					var t = Main.tile[x, y];
					t.ClearEverything();
					switch (ch)
					{
						case '#': t.HasTile = true; t.TileType = TileID.Dirt; break;
						case 'I': t.HasTile = true; t.TileType = TileID.Iron; break;
						case '%': t.HasTile = true; t.TileType = TileID.Stone; break;
						case '/': t.HasTile = true; t.TileType = TileID.Dirt; t.Slope = SlopeType.SlopeDownLeft; break;
						case '\\': t.HasTile = true; t.TileType = TileID.Dirt; t.Slope = SlopeType.SlopeDownRight; break;
						case '=': t.HasTile = true; t.TileType = TileID.Platforms; break;
						case '|': t.HasTile = true; t.TileType = TileID.Rope; break;
						case '~': t.LiquidType = LiquidID.Water; t.LiquidAmount = 255; break;
						case 'L': t.LiquidType = LiquidID.Lava; t.LiquidAmount = 255; break;
						case 'h': t.LiquidType = LiquidID.Honey; t.LiquidAmount = 255; break;
						case 'g': t.HasTile = true; t.TileType = TileID.Grass; break;
						case 's': t.HasTile = true; t.TileType = TileID.Sand; break;
						case 'i': t.HasTile = true; t.TileType = TileID.IceBlock; break;
						case 'm': t.HasTile = true; t.TileType = TileID.Mud; break;
						case 'j': t.HasTile = true; t.TileType = TileID.JungleGrass; break;
						case 'e': t.HasTile = true; t.TileType = TileID.Ebonstone; break;   // the Corruption's stone (biome checks)
						case 'x': t.HasTile = true; t.TileType = TileID.JungleThorns; break;
						case 'w': t.HasTile = true; t.TileType = TileID.Cobweb; break;
						case '^': t.HasTile = true; t.TileType = TileID.Spikes; break;
						case 'H': t.HasTile = true; t.TileType = TileID.Hellstone; break;
						case 'B': t.WallType = WallID.Dirt; break;
						case 't': torches.Add((x, y)); break;
						case 'T': t.HasTile = true; t.TileType = TileID.Grass; trees.Add((x, y)); break;   // grass with a tree grown on it
						case 'D': case 'C': case 'W': case 'A': case 'F': case 'X': objects.Add((ch, x, y)); break;
					}
					cells++;
				}
			WorldGen.RangeFrame(bx1 - 1, by1 - 1, bx2 + 2, by2 + 2);
			// torches after the rest, so each has the block beside or under it to hold on to: light for the person watching
			int lit = 0;
			foreach (var (tx, ty) in torches)
				if (WorldGen.PlaceTile(tx, ty, TileID.Torches, true, true)) lit++;
			// objects after the rest (v1's gym): 'D' a closed door (its bottom cell), 'C' a chest with a few things (its
			// bottom left cell), 'W' a work bench, 'A' an anvil, 'F' a furnace (their bottom left cell)
			var made = new List<string>();
			// trees after the frame (GrowTree refused on the gym's floor): a trunk of six set by hand, straight pieces with
			// the tree top's frame on the last, as the game frames a branchless tree, so a later frame keeps it
			foreach (var (tx, ty) in trees)
			{
				for (int k = 1; k <= 6; k++)
				{
					var t = Main.tile[tx, ty - k];
					t.ClearEverything(); t.HasTile = true; t.TileType = TileID.Trees;
					t.TileFrameX = (short)(k == 6 ? 22 : 0); t.TileFrameY = (short)(k == 6 ? 198 : 0);
				}
				made.Add("tree");
			}
			foreach (var (ch, ox, oy) in objects)
			{
				if (ch == 'D')
				{
					for (int k = 0; k < 3; k++)
					{
						var t = Main.tile[ox, oy - 2 + k];
						t.ClearEverything(); t.HasTile = true; t.TileType = TileID.ClosedDoor; t.TileFrameX = 0; t.TileFrameY = (short)(18 * k);
					}
					made.Add("door");
				}
				else if (ch == 'C')
				{
					for (int dx = 0; dx < 2; dx++)
						for (int dy = 0; dy < 2; dy++)
						{
							var t = Main.tile[ox + dx, oy - 1 + dy];
							t.ClearEverything(); t.HasTile = true; t.TileType = TileID.Containers; t.TileFrameX = (short)(18 * dx); t.TileFrameY = (short)(18 * dy);
						}
					int ci = Chest.FindChest(ox, oy - 1);
					if (ci < 0) ci = Chest.CreateChest(ox, oy - 1);
					if (ci >= 0)
					{
						for (int k = 0; k < Main.chest[ci].item.Length; k++) Main.chest[ci].item[k] = new Item();
						int[,] loot = { { ItemID.Torch, 10 }, { ItemID.Rope, 20 }, { ItemID.LesserHealingPotion, 2 } };
						for (int k = 0; k < loot.GetLength(0); k++) { Main.chest[ci].item[k].SetDefaults(loot[k, 0]); Main.chest[ci].item[k].stack = loot[k, 1]; }
					}
					made.Add(ci >= 0 ? "chest" : "chest (no entry)");
				}
				else
				{
					// 'X' a demon altar (3 by 2): where the Suspicious Looking Eye is crafted
					int type = ch == 'W' ? TileID.WorkBenches : ch == 'A' ? TileID.Anvils : ch == 'X' ? TileID.DemonAltar : TileID.Furnaces;
					int w = (ch == 'F' || ch == 'X') ? 3 : 2, ht = (ch == 'F' || ch == 'X') ? 2 : 1;
					for (int dx = 0; dx < w; dx++)
						for (int dy = 0; dy < ht; dy++)
						{
							var t = Main.tile[ox + dx, oy - ht + 1 + dy];
							t.ClearEverything(); t.HasTile = true; t.TileType = (ushort)type; t.TileFrameX = (short)(18 * dx); t.TileFrameY = (short)(18 * dy);
						}
					made.Add(ch == 'W' ? "work bench" : ch == 'A' ? "anvil" : ch == 'X' ? "demon altar" : "furnace");
				}
			}
			if (Main.Map != null)
			{
				var empty = Terraria.Map.MapTile.Create(0, 0, 0);
				for (int x = bx1; x <= bx2; x++)
					for (int y = by1; y <= by2; y++)
						if (WorldGen.InWorld(x, y, 10))
						{
							if (reveal) Main.Map.UpdateLighting(x, y, 255);
							else Main.Map.SetTile(x, y, ref empty);
						}
			}
			return Json.Of(new Dictionary<string, object>
			{
				["stamped"] = cells, ["box"] = new[] { bx1, by1, bx2, by2 }, ["items_removed"] = removed, ["torches"] = lit,
				["objects"] = made,
			});
		}

		// POST /gym, every key optional, applied in this order:
		//   "wear": ["Tin Helmet", ...]       exactly this armor and these accessories, nothing else worn
		//   "life_max": 160                   as life crystals (20 each) and life fruit (5 each) give it
		//   "bag": [["Dirt Block", 99, 1], ...]  exactly this in the bag: name, count, slot (first free when left out),
		//                                     true for a favorite
		//   "feet": [x, y]                    the body standing with its left column at x and its feet in row y
		//   "at": [px, py]                    the body's top left corner, in pixels
		//   "heal": true                      full health, mana and breath, no debuffs, no potion sickness, standing still
		//   "clear_buffs": true               every buff and debuff off (a potion from an earlier test lasts minutes)
		//   "life": 40, "breath": 100         set them
		//   "freeze": "noon" | "midnight" | false
		//   "spawns": 0 | 1                   enemies come or not
		//   "spawn": [["Zombie", x, y]]       an enemy with its feet on that cell
		//   "drop": [["Gel", 5, dx, dy]]      an item on the ground, dx cells right of the body and dy rows below its middle
		//   "buff": [["MiningPotion", 60]]    a buff by its internal name, for that many seconds
		//   "butcher": true                   the hostile ones there go
		//   "background": true | false        the game runs on with its window behind others
		// POST /save: the world and the player written to disk now, as the game does on a clean exit. A restart kills
		// the process and the game saves nothing then: a chest emptied and arrows crafted were gone after one (Oct 20)
		public static string Save()
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			Terraria.IO.WorldFile.SaveWorld();
			Player.SavePlayer(Main.ActivePlayerFileData, true);
			return Json.Of(new Dictionary<string, object> { ["saved"] = Main.worldName, ["player"] = p.name });
		}

		public static string Apply(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			var done = new List<string>();
			if (p.dead)
			{
				// a dead body is not updated and cannot be moved: it comes back on the next tick, and the caller asks again
				p.respawnTimer = 0;
				return Json.Of(new Dictionary<string, object> { ["done"] = new List<string> { "dead: respawning" }, ["dead"] = true });
			}
			if (root.TryGetProperty("wear", out var wear)) done.Add(Wear(p, wear));
			if (root.TryGetProperty("life_max", out var lm)) done.Add(LifeMax(p, lm.GetInt32()));
			if (root.TryGetProperty("bag", out var bag)) done.Add(Bag(p, bag));
			if (root.TryGetProperty("feet", out var feet))
				done.Add(Put(p, feet[0].GetInt32() * 16f + 6f, (feet[1].GetInt32() + 1) * 16f - p.height));
			if (root.TryGetProperty("at", out var at)) done.Add(Put(p, at[0].GetSingle(), at[1].GetSingle()));
			if (root.TryGetProperty("heal", out var heal) && heal.GetBoolean()) done.Add(Heal(p));
			if (root.TryGetProperty("clear_buffs", out var cb) && cb.GetBoolean())
			{
				int n = 0;
				for (int b = 0; b < Player.MaxBuffs; b++)
					if (p.buffType[b] > 0) { p.DelBuff(b); b--; n++; }
				done.Add(n + " buffs off");
			}
			if (root.TryGetProperty("life", out var life))
			{
				p.statLife = System.Math.Max(1, System.Math.Min(life.GetInt32(), p.statLifeMax2));
				done.Add("life " + p.statLife);
			}
			if (root.TryGetProperty("breath", out var br))
			{
				p.breath = System.Math.Max(0, System.Math.Min(br.GetInt32(), p.breathMax));
				done.Add("breath " + p.breath);
			}
			if (root.TryGetProperty("freeze", out var fr))
			{
				Frozen = fr.ValueKind == JsonValueKind.String;
				if (Frozen)
				{
					bool noon = fr.GetString() == "noon";
					FrozenDay = noon;
					FrozenTime = noon ? 27000 : 16200;     // noon; midnight is 4.5 hours into the night
				}
				done.Add(Frozen ? "frozen at " + fr.GetString() : "the clock runs");
			}
			if (root.TryGetProperty("spawns", out var sp))
			{
				SpawnScale = sp.GetSingle();
				done.Add("spawns " + SpawnScale);
			}
			if (root.TryGetProperty("spawn", out var spawn))
				foreach (var e in spawn.EnumerateArray())
					done.Add(Spawn(e[0].GetString(), e[1].GetInt32(), e[2].GetInt32()));
			if (root.TryGetProperty("buff", out var buff))
				foreach (var e in buff.EnumerateArray())
				{
					int id = BuffID.Search.TryGetId(e[0].GetString(), out int bid) ? bid : 0;
					if (id > 0) { p.AddBuff(id, e[1].GetInt32() * 60); done.Add("buff " + Lang.GetBuffName(id)); }
					else done.Add("no buff " + e[0].GetString());
				}
			if (root.TryGetProperty("drop", out var drop))
				foreach (var e in drop.EnumerateArray())
				{
					int type = ItemType(e[0].GetString());
					if (type <= 0) { done.Add("no item " + e[0].GetString()); continue; }
					int i = Item.NewItem(new Terraria.DataStructures.EntitySource_SpawnNPC(), (int)p.Center.X + e[2].GetInt32() * 16 - 8,
						(int)p.Center.Y + e[3].GetInt32() * 16 - 8, 16, 16, type, e[1].GetInt32());
					if (i >= 0 && i < Main.maxItems) Main.item[i].velocity = Vector2.Zero;
					done.Add("dropped " + Lang.GetItemNameValue(type) + " x" + e[1].GetInt32());
				}
			if (root.TryGetProperty("butcher", out var bu) && bu.GetBoolean())
			{
				int n = 0;
				for (int i = 0; i < Main.maxNPCs; i++)
				{
					var npc = Main.npc[i];
					if (npc == null || !npc.active || npc.friendly || npc.townNPC || npc.CountsAsACritter) continue;
					npc.active = false;
					n++;
				}
				done.Add(n + " enemies gone");
			}
			if (root.TryGetProperty("background", out var bg))
			{
				RunInBackground = bg.GetBoolean();
				done.Add("background " + RunInBackground);
			}
			return Json.Of(new Dictionary<string, object> { ["done"] = done, ["body"] = State.Body(p) });
		}

		// an enemy standing with its feet on the cell's bottom (x, y in cells), by its internal name («Zombie»,
		// «BlueSlime», «DemonEye») or its English name
		static string Spawn(string name, int x, int y)
		{
			int type = NPCID.Search.TryGetId(name, out int id) ? id : 0;
			for (int t = 1; t < NPCLoader.NPCCount && type == 0; t++)
				if (string.Equals(Lang.GetNPCNameValue(t), name, System.StringComparison.OrdinalIgnoreCase)) type = t;
			if (type <= 0) return "no enemy " + name;
			int i = NPC.NewNPC(new Terraria.DataStructures.EntitySource_SpawnNPC(), x * 16 + 8, (y + 1) * 16, type);
			return i < Main.maxNPCs ? "spawned " + Main.npc[i].TypeName + " (" + i + ")" : "no room for " + name;
		}

		static string Put(Player p, float x, float y)
		{
			p.position = new Vector2(x, y);
			p.oldPosition = p.position;
			p.velocity = Vector2.Zero;
			p.fallStart = (int)(p.position.Y / 16f);
			p.jump = 0;
			return "body at " + x + "," + y;
		}

		static string Heal(Player p)
		{
			for (int b = 0; b < Player.MaxBuffs; b++)
				if (p.buffType[b] > 0 && Main.debuff[p.buffType[b]]) { p.DelBuff(b); b--; }
			p.statLife = p.statLifeMax2;
			p.statMana = p.statManaMax2;
			p.breath = p.breathMax;
			p.potionDelay = 0;
			p.velocity = Vector2.Zero;
			p.fallStart = (int)(p.position.Y / 16f);
			return "healed";
		}

		// the game works the most health out every tick from the life crystals and life fruit eaten (v1, Oct 17: set
		// directly it went back to 100 the next tick)
		static string LifeMax(Player p, int n)
		{
			int crystals = System.Math.Min(15, System.Math.Max(0, (n - 100) / 20));
			int fruit = System.Math.Min(20, System.Math.Max(0, (n - 400) / 5));
			p.ConsumedLifeCrystals = crystals;
			p.ConsumedLifeFruit = fruit;
			n = 100 + crystals * 20 + fruit * 5;
			p.statLifeMax = n;
			p.statLifeMax2 = System.Math.Max(p.statLifeMax2, n);
			p.statLife = n;
			return "most health " + n;
		}

		static int ItemType(string name)
		{
			if (int.TryParse(name, out int id)) return id;
			for (int t = 1; t < ItemLoader.ItemCount; t++)
				if (string.Equals(Lang.GetItemNameValue(t), name, System.StringComparison.OrdinalIgnoreCase)) return t;
			return 0;
		}

		static string Bag(Player p, JsonElement bag)
		{
			for (int i = 0; i < 58; i++) p.inventory[i] = new Item();
			var put = new List<string>();
			foreach (var e in bag.EnumerateArray())
			{
				string name = e[0].ValueKind == JsonValueKind.Number ? e[0].GetInt32().ToString() : e[0].GetString();
				int type = ItemType(name), n = e.GetArrayLength() > 1 ? e[1].GetInt32() : 1;
				if (type <= 0) { put.Add("no item " + name); continue; }
				int slot = e.GetArrayLength() > 2 ? e[2].GetInt32() : -1;
				for (int i = 0; slot < 0 && i < 50; i++)
					if (p.inventory[i].IsAir) slot = i;
				if (slot < 0 || slot >= 58) { put.Add(name + ": no free slot"); continue; }
				var it = new Item();
				it.SetDefaults(type);
				it.stack = System.Math.Min(n, it.maxStack);
				p.inventory[slot] = it;
				if (e.GetArrayLength() > 3 && e[3].ValueKind == JsonValueKind.True) it.favorited = true;   // trash and store leave it
				put.Add(it.Name + " x" + it.stack + " in " + slot);
			}
			return "bag: " + string.Join(", ", put);
		}

		static string Wear(Player p, JsonElement names)
		{
			for (int a = 0; a < 10; a++) p.armor[a] = new Item();
			var worn = new List<string>();
			foreach (var e in names.EnumerateArray())
			{
				int type = ItemType(e.GetString());
				if (type <= 0) { worn.Add("no item " + e.GetString()); continue; }
				var it = new Item();
				it.SetDefaults(type);
				int slot = it.headSlot >= 0 ? 0 : it.bodySlot >= 0 ? 1 : it.legSlot >= 0 ? 2 : -1;
				if (slot < 0 && it.accessory)
					for (int a = 3; a < 10 && slot < 0; a++)
						if (p.armor[a].IsAir) slot = a;
				if (slot < 0) { worn.Add(it.Name + ": nowhere to wear it"); continue; }
				p.armor[slot] = it;
				worn.Add(it.Name);
			}
			return "worn: " + (worn.Count == 0 ? "nothing" : string.Join(", ", worn));
		}
	}

	public class GymSpawns : GlobalNPC
	{
		public override void EditSpawnRate(Player player, ref int spawnRate, ref int maxSpawns)
		{
			if (Gym.SpawnScale <= 0f)
			{
				maxSpawns = 0;
				spawnRate = int.MaxValue / 2;
			}
			else if (Gym.SpawnScale != 1f)
			{
				spawnRate = System.Math.Max(1, (int)(spawnRate / Gym.SpawnScale));
				maxSpawns = System.Math.Max(1, (int)(maxSpawns * Gym.SpawnScale));
			}
		}
	}
}
