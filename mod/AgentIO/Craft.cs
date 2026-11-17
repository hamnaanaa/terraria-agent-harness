using System.Collections.Generic;
using System.Text.Json;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace AgentIO
{
	// The verbs with no physics in them: crafting, a chest's things taken or put, an item worn or thrown away. Each
	// answers with what it did, or why it could not (the reason is data for whoever sets the goals: the station needed,
	// the ingredients short).
	public static class Craft
	{
		static int ItemType(string name)
		{
			if (int.TryParse(name, out int id)) return id;
			if (ItemID.Search.TryGetId(name.Replace(" ", ""), out int byInternal)) return byInternal;
			for (int t = 1; t < ItemLoader.ItemCount; t++)
				if (string.Equals(Lang.GetItemNameValue(t), name, System.StringComparison.OrdinalIgnoreCase)) return t;
			return 0;
		}

		// POST /bag {"from": 35, "to": 7}: the two bag slots swapped (0 to 9 the hotbar, 10 to 49 the backpack), as a
		// player drags an item into the hotbar: the game uses items from the hotbar only (the kit rule, Oct 20)
		public static string Swap(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			int a = doc.RootElement.GetProperty("from").GetInt32(), b = doc.RootElement.GetProperty("to").GetInt32();
			if (a < 0 || a >= 50 || b < 0 || b >= 50) return Json.Error("a slot is 0 to 49");
			var t = p.inventory[a]; p.inventory[a] = p.inventory[b]; p.inventory[b] = t;
			Recipe.FindRecipes();
			return Json.Of(new Dictionary<string, object> { ["hotbar"] = p.inventory[b].Name, ["backpack"] = p.inventory[a].Name });
		}

		// POST /wear {"item": "Tin Helmet"}: the item put on, as a player drags it onto the armour slots: a helmet, a
		// chainmail or greaves into its slot, an accessory into the first free of the five, else the last; what was
		// there goes back into the bag's slot (the kit rule's cousin, Oct 20)
		public static string Wear(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			string name = doc.RootElement.GetProperty("item").GetString();
			int i = -1;
			for (int k = 0; k < 50; k++)
				if (p.inventory[k] != null && !p.inventory[k].IsAir && p.inventory[k].Name == name) { i = k; break; }
			if (i < 0) return Json.Error("no " + name + " in the bag");
			var it = p.inventory[i];
			int slot = -1;
			if (it.headSlot >= 0) slot = 0;
			else if (it.bodySlot >= 0) slot = 1;
			else if (it.legSlot >= 0) slot = 2;
			else if (it.accessory)
			{
				for (int a = 3; a < 8; a++) if (p.armor[a] == null || p.armor[a].IsAir) { slot = a; break; }
				if (slot < 0) slot = 7;
			}
			if (slot < 0) return Json.Error(name + " is not armour or an accessory");
			var old = p.armor[slot];
			p.armor[slot] = it.Clone();
			p.inventory[i] = (old == null || old.IsAir) ? new Item() : old.Clone();
			return Json.Of(new Dictionary<string, object> { ["worn"] = name, ["slot"] = slot, ["back"] = (old == null || old.IsAir) ? null : old.Name });
		}

		// POST /newplayer {"name": "02", "difficulty": 0}: a fresh character written to disk with the game's own starting
		// items, as the character screen makes one (0 Classic, 1 Mediumcore, 2 Hardcore, 3 Journey). A Journey character
		// can only enter Journey worlds, so the Classic world gets its own (Oct 23)
		public static string NewPlayer(string body)
		{
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			string name = doc.RootElement.TryGetProperty("name", out var n) ? n.GetString() : "02";
			byte diff = doc.RootElement.TryGetProperty("difficulty", out var d) ? (byte)d.GetInt32() : (byte)0;
			var p = new Player();
			p.name = name;
			p.difficulty = diff;
			// the game's starting items: a copper shortsword, pickaxe and axe (Player.cs, the character screen)
			p.inventory[0].SetDefaults(ItemID.CopperShortsword);
			p.inventory[1].SetDefaults(ItemID.CopperPickaxe);
			p.inventory[2].SetDefaults(ItemID.CopperAxe);
			var data = Terraria.IO.PlayerFileData.CreateAndSave(p);
			return Json.Of(new Dictionary<string, object> { ["saved"] = data.Path, ["name"] = p.name, ["difficulty"] = p.difficulty,
				["items"] = string.Join(", ", System.Array.FindAll(p.inventory, it => it != null && !it.IsAir).Length + " items") });
		}

		// POST /craft {"item": "Wooden Chair", "n": 1}
		public static string Make(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			int type = ItemType(root.GetProperty("item").GetString());
			int n = root.TryGetProperty("n", out var ne) ? ne.GetInt32() : 1;
			if (type <= 0) return Json.Error("no item named " + root.GetProperty("item").GetString());
			// the game looks for stations around the body only with the bag open (Player.AdjTiles under the bag's UI)
			bool wasOpen = Main.playerInventory;
			Main.playerInventory = true;
			p.AdjTiles();
			Recipe.FindRecipes();
			int made = 0;
			string stop = "";
			for (int k = 0; k < n; k++)
			{
				int idx = -1;
				for (int ri = 0; ri < Main.numAvailableRecipes; ri++)
					if (Main.recipe[Main.availableRecipe[ri]].createItem.type == type) { idx = Main.availableRecipe[ri]; break; }
				if (idx < 0) { stop = made > 0 ? "no more ingredients" : Why(p, type); break; }
				var r = Main.recipe[idx];
				var item = r.createItem.Clone();
				r.Create();                         // the game consumes the ingredients and runs its hooks
				item = p.GetItem(p.whoAmI, item, GetItemSettings.InventoryEntityToPlayerInventorySettings);
				if (!item.IsAir) { stop = "the bag is full"; break; }
				made++;
				Recipe.FindRecipes();
			}
			Main.playerInventory = wasOpen;
			var ans = new Dictionary<string, object> { ["made"] = made, ["item"] = Lang.GetItemNameValue(type) };
			if (stop != "") ans["stopped"] = stop;
			return Json.Of(ans);
		}

		// POST /fits {"item": "Furnace", "r": 12}: the cells near the body where the game itself would place the item's
		// tile (TileObject.CanPlace: the object's whole footprint free, its anchors on solid ground), nearest first. A
		// furnace is 3 by 2; the spot the craft verb picked by a fixed rule touched a wall and a hole (Oct 26).
		public static string Fits(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			int type = ItemType(root.GetProperty("item").GetString());
			if (type <= 0) return Json.Error("no item named " + root.GetProperty("item").GetString());
			var it = ContentSamples.ItemsByType[type];
			if (it.createTile < 0) return Json.Error(it.Name + " places no tile");
			int r = root.TryGetProperty("r", out var re) ? re.GetInt32() : 12;
			int bx = (int)(p.Center.X / 16f), by = (int)(p.Center.Y / 16f);
			var cells = new List<int[]>();
			for (int y = by - r; y <= by + r; y++)
				for (int x = bx - r; x <= bx + r; x++)
					if (WorldGen.InWorld(x, y, 10) && TileObject.CanPlace(x, y, it.createTile, it.placeStyle, p.direction, out _, true))
						cells.Add(new[] { x, y });
			cells.Sort((a, b) => (System.Math.Abs(a[0] - bx) + System.Math.Abs(a[1] - by)).CompareTo(System.Math.Abs(b[0] - bx) + System.Math.Abs(b[1] - by)));
			if (cells.Count > 40) cells.RemoveRange(40, cells.Count - 40);
			return Json.Of(new Dictionary<string, object> { ["item"] = it.Name, ["tile"] = it.createTile, ["cells"] = cells });
		}

		// why the item cannot be crafted now: the stations and ingredients its recipes want against what is here
		static string Why(Player p, int type)
		{
			var reasons = new List<string>();
			for (int ri = 0; ri < Recipe.numRecipes; ri++)
			{
				var r = Main.recipe[ri];
				if (r.createItem.type != type) continue;
				var missing = new List<string>();
				foreach (var t in r.requiredTile)
					if (t >= 0 && !p.adjTile[t]) missing.Add("a " + TileID.Search.GetName(t) + " within reach");
				foreach (var req in r.requiredItem)
				{
					if (req.IsAir) continue;
					int have = 0;
					for (int i = 0; i < 58; i++)
						if (p.inventory[i] != null && !p.inventory[i].IsAir && (p.inventory[i].type == req.type ||
						    r.AcceptedByItemGroups(p.inventory[i].type, req.type))) have += p.inventory[i].stack;
					if (have < req.stack) missing.Add(req.stack + " " + req.Name + " (have " + have + ")");
				}
				reasons.Add(missing.Count == 0 ? "a recipe fits but the game did not offer it (the bag full?)" : "needs " + string.Join(", ", missing));
			}
			return reasons.Count == 0 ? "no recipe makes it" : string.Join("; or ", reasons);
		}

		// POST /chest {"take": "all"}: everything in the open chest into the bag, then the chest closes
		public static string Chest(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			if (p.chest < 0) return Json.Error("no chest is open (interact with it first)");
			var chest = Main.chest[p.chest];
			// {"put": "all"}: the game's «deposit all»: every item of the bag's slots 10 to 49 that is not a favorite goes
			// into the chest, onto a stack of the same item first, else into an empty slot; what does not fit stays.
			// {"put": ["Tombstone", ...]}: those items only, from all 50 slots (the hotbar's too, as a player drags one
			// in), the same way; a favorite stays and is named (store by name, his go Nov 1)
			if (body != null && body.Contains("\"put\""))
			{
				var names = Names(body, "put");              // null for "all"
				if (names != null && names.Count == 0) return Json.Error("POST /chest takes \"all\" or item names under put");
				var put = new List<string>();
				var kept = new List<string>();
				var favorites = new List<string>();
				var found = new HashSet<string>();
				for (int s = names == null ? 10 : 0; s < 50; s++)
				{
					var it = p.inventory[s];
					if (it == null || it.IsAir) continue;
					string asked = names == null ? it.Name : names.Find(x => Same(it.Name, x));
					if (asked == null) continue;
					found.Add(asked);
					if (it.favorited) { if (names != null) favorites.Add(it.Name + " x" + it.stack); continue; }
					string name = it.Name;
					int before = it.stack;
					for (int c = 0; c < chest.item.Length && it.stack > 0; c++)
					{
						var ci = chest.item[c];
						if (ci != null && !ci.IsAir && ci.type == it.type && ci.stack < ci.maxStack && ItemLoader.CanStack(ci, it))
						{
							int n = System.Math.Min(it.stack, ci.maxStack - ci.stack);
							ci.stack += n;
							it.stack -= n;
						}
					}
					for (int c = 0; c < chest.item.Length && it.stack > 0; c++)
					{
						if (chest.item[c] == null || chest.item[c].IsAir)
						{
							chest.item[c] = it.Clone();
							it.stack = 0;
						}
					}
					if (it.stack <= 0) p.inventory[s] = new Item();
					if (before - System.Math.Max(0, it.stack) > 0) put.Add(name + " x" + (before - System.Math.Max(0, it.stack)));
					if (it.stack > 0) kept.Add(name);
				}
				p.chest = -1;
				Main.playerInventory = false;
				Recipe.FindRecipes();
				return Json.Of(new Dictionary<string, object>
				{
					["put"] = put, ["kept"] = kept, ["favorites"] = favorites,
					["missing"] = names == null ? new List<string>() : names.FindAll(x => !found.Contains(x)),
				});
			}
			using var request = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = request.RootElement;
			if ((root.TryGetProperty("chest_id", out var wanted) && wanted.GetInt32() != p.chest) ||
			    (root.TryGetProperty("cell", out var cell) && cell.ValueKind != JsonValueKind.Null &&
			     (cell[0].GetInt32() < chest.x || cell[0].GetInt32() > chest.x + 1 ||
			      cell[1].GetInt32() < chest.y || cell[1].GetInt32() > chest.y + 1)))
				return Json.Of(new Dictionary<string, object> { ["stopped"] = "the open chest is not the requested chest" });
			var took = new List<string>();
			var remaining = new List<string>();
			for (int i = 0; i < chest.item.Length; i++)
			{
				var it = chest.item[i];
				if (it == null || it.IsAir) continue;
				var left = p.GetItem(p.whoAmI, it.Clone(), GetItemSettings.InventoryEntityToPlayerInventorySettings);
				int taken = it.stack - (left.IsAir ? 0 : left.stack);
				if (taken > 0) took.Add(it.Name + " x" + taken);
				if (!left.IsAir) remaining.Add(left.Name + " x" + left.stack);
				chest.item[i] = left.IsAir ? new Item() : left;
			}
			p.chest = -1;
			Main.playerInventory = false;        // the chest opened the bag's panel; closed again for the person watching
			Recipe.FindRecipes();
			return Json.Of(new Dictionary<string, object> { ["took"] = took, ["remaining"] = remaining });
		}

		// POST /trash {"items": ["Tombstone", "Grave Marker"]}: every stack of the named items in the bag's 50 slots (0 to
		// 9 the hotbar, 10 to 49 the backpack) into the trash slot in turn, as a player trashes them by hand: each one put
		// there takes the one before it out of the game (the wiki, Inventory: Trash). The coin and ammo slots stay, and so
		// does a favorite (the game trashes no favorite). Dropping was turned down Nov 1: the character walks over a
		// dropped item and picks it up again.
		public static string Trash(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			var names = Names(body, "items");
			if (names == null || names.Count == 0) return Json.Error("POST /trash takes item names under items");
			var trashed = new List<string>();
			var gone = new List<string>();
			var favorites = new List<string>();
			var missing = new List<string>();
			foreach (var name in names)
			{
				bool found = false;
				for (int s = 0; s < 50; s++)
				{
					var it = p.inventory[s];
					if (it == null || it.IsAir || !Same(it.Name, name)) continue;
					found = true;
					if (it.favorited) { favorites.Add(it.Name + " x" + it.stack); continue; }
					if (p.trashItem != null && !p.trashItem.IsAir) gone.Add(p.trashItem.Name + " x" + p.trashItem.stack);
					trashed.Add(it.Name + " x" + it.stack);
					p.trashItem = it.Clone();
					p.inventory[s] = new Item();
				}
				if (!found) missing.Add(name);
			}
			Recipe.FindRecipes();
			return Json.Of(new Dictionary<string, object>
			{
				["trashed"] = trashed, ["gone"] = gone, ["favorites"] = favorites, ["missing"] = missing,
				["in_trash"] = p.trashItem == null || p.trashItem.IsAir ? null : p.trashItem.Name + " x" + p.trashItem.stack,
			});
		}

		// the item names a request lists under key; null when it says "all", none when it lists none
		static List<string> Names(string body, string key)
		{
			var names = new List<string>();
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			if (doc.RootElement.ValueKind != JsonValueKind.Object || !doc.RootElement.TryGetProperty(key, out var v)) return names;
			if (v.ValueKind == JsonValueKind.String && v.GetString() == "all") return null;
			if (v.ValueKind == JsonValueKind.Array)
				foreach (var e in v.EnumerateArray())
					if (e.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(e.GetString())) names.Add(e.GetString().Trim());
			return names;
		}

		// Match item names regardless of case or apostrophes.
		static bool Same(string name, string asked) =>
			string.Equals(name.Replace("'", ""), asked.Replace("'", "").Trim(), System.StringComparison.OrdinalIgnoreCase);
	}
}
