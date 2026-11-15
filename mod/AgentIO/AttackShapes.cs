using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Diagnostics;
using System.Text.Json;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.DataStructures;
using Terraria.GameContent;
using Terraria.ModLoader;

namespace AgentIO
{
	// Opt-in read-only collision measurements, in any world.
	// Colliding samples geometry; ModifyDamageHitbox observes the real damage
	// path after vanilla's eligibility checks. Neither runs projectile AI.
	public class AttackShapes : GlobalProjectile
	{
		public override bool InstancePerEntity => true;
		static bool enabled;
		static readonly List<Dictionary<string, object>> hits = new();
		uint born, checkedAt;
		uint? firstDamage;
		Rectangle damageBox;
		bool checkedOnce;

		public static void Reset() { enabled = false; hits.Clear(); }

		public override void OnSpawn(Projectile projectile, IEntitySource source)
		{
			born = Main.GameUpdateCount;
			checkedOnce = false;
			firstDamage = null;
		}

		public override void ModifyDamageHitbox(Projectile projectile, ref Rectangle hitbox)
		{
			if (!enabled || !projectile.hostile) return;
			checkedAt = Main.GameUpdateCount;
			checkedOnce = true;
			firstDamage ??= checkedAt;
			damageBox = hitbox;
		}

		public override void OnHitPlayer(Projectile projectile, Player target, Player.HurtInfo hurtInfo)
		{
			if (!enabled) return;
			var rect = target.Hitbox;
			hits.Add(new()
			{
				["tick"] = Main.GameUpdateCount, ["i"] = projectile.whoAmI, ["id"] = projectile.type,
				["name"] = projectile.Name, ["damage"] = hurtInfo.Damage,
				["player_box"] = new[] { rect.X, rect.Y, rect.Width, rect.Height },
				["damage_checked"] = checkedOnce && checkedAt == Main.GameUpdateCount,
				["collision"] = projectile.Colliding(damageBox, rect),
				["age"] = Main.GameUpdateCount - born,
			});
			if (hits.Count > 40) hits.RemoveAt(0);
		}

		public static object Sample(int x0, int y0, int w, int h, NameValueCollection q)
		{
			if (w < 1 || h < 1 || w > 100 || h > 60) throw new ArgumentException("shape window must be at most 100x60 tiles");
			enabled = true;
			int step = int.TryParse(q["shape_step"], out int s) ? s : 16;
			if (step < 4 || step > 32) throw new ArgumentException("shape_step must be 4 to 32 pixels");
			int type = int.TryParse(q["shape_type"], out int t) ? t : -1;
			int cols = (w * 16 + step - 1) / step, rows = (h * 16 + step - 1) / step;
			var player = Main.LocalPlayer;
			var target = new Rectangle(0, 0, player.width, player.height);
			var shapes = new List<Dictionary<string, object>>();
			long tests = 0;
			long footprintTests = 0, footprintTicks = 0;
			var watch = Stopwatch.StartNew();
			for (int i = 0; i < Main.maxProjectiles; i++)
			{
				var p = Main.projectile[i];
				if (!p.active || !p.hostile || p.damage <= 0 || (type >= 0 && p.type != type)) continue;
				var observed = p.GetGlobalProjectile<AttackShapes>();
				bool active = observed.checkedOnce && observed.checkedAt == Main.GameUpdateCount;
				var box = active ? observed.damageBox : p.Hitbox;
				var runs = Runs(p, box, x0 * 16, y0 * 16, cols, rows, step, target, ref tests);
				long localStart = Stopwatch.GetTimestamp();
				// The sprite chooses a bounded query region, never the collision
				// shape. Colliding remains the authority, including outside sprites.
				Main.instance.LoadProjectile(p.type);
				var texture = TextureAssets.Projectile[p.type].Value;
				int frameHeight = texture.Height / Math.Max(1, Main.projFrames[p.type]);
				double radius = Math.Sqrt((double)texture.Width * texture.Width + (double)frameHeight * frameHeight) * p.scale / 2;
				radius = Math.Max(radius, Math.Max(Math.Max(Math.Abs(box.Left - p.Center.X), Math.Abs(box.Right - p.Center.X)),
					Math.Max(Math.Abs(box.Top - p.Center.Y), Math.Abs(box.Bottom - p.Center.Y))));
				// One grid cell of padding on each side, plus the last sample.
				// A local query uses at most the existing window's number of samples.
				int localCols = Math.Min(cols, (int)Math.Ceiling((2 * radius + target.Width) / step) + 3);
				int localRows = Math.Min(rows, (int)Math.Ceiling((2 * radius + target.Height) / step) + 3);
				int lx = x0 * 16 + (int)Math.Floor((p.Center.X - target.Width / 2f - (localCols - 1) * step / 2f - x0 * 16) / step) * step;
				int ly = y0 * 16 + (int)Math.Floor((p.Center.Y - target.Height / 2f - (localRows - 1) * step / 2f - y0 * 16) / step) * step;
				var patches = new List<Dictionary<string, object>>();
				if (lx < x0 * 16 || ly < y0 * 16 || lx + localCols * step > x0 * 16 + cols * step || ly + localRows * step > y0 * 16 + rows * step)
					patches.Add(new()
					{
						["origin"] = new[] { lx, ly }, ["cols"] = localCols, ["rows"] = localRows,
						["runs"] = Runs(p, box, lx, ly, localCols, localRows, step, target, ref footprintTests),
					});
				footprintTicks += Stopwatch.GetTimestamp() - localStart;
				shapes.Add(new()
				{
					["i"] = i, ["id"] = p.type, ["identity"] = p.identity, ["name"] = p.Name,
					["x"] = p.position.X, ["y"] = p.position.Y, ["rotation"] = p.rotation,
					["age"] = Main.GameUpdateCount - observed.born,
					["damage_checked"] = active, ["first_damage_tick"] = observed.firstDamage,
					["origin_box"] = new[] { p.Hitbox.X, p.Hitbox.Y, p.width, p.height },
					["damage_box"] = new[] { box.X, box.Y, box.Width, box.Height },
					["runs"] = runs,
					["patches"] = patches,
				});
			}
			watch.Stop();
			return new Dictionary<string, object>
			{
				["tick"] = Main.GameUpdateCount, ["origin"] = new[] { x0 * 16, y0 * 16 },
				["step"] = step, ["cols"] = cols, ["rows"] = rows,
				["target_size"] = new[] { player.width, player.height },
				["shapes"] = shapes, ["hits"] = hits.ToArray(),
				["tests"] = tests + footprintTests, ["sample_ms"] = watch.Elapsed.TotalMilliseconds,
				["footprint_tests"] = footprintTests, ["footprint_ms"] = footprintTicks * 1000.0 / Stopwatch.Frequency,
			};
		}

		static List<int[]> Runs(Projectile p, Rectangle box, int x, int y, int cols, int rows, int step, Rectangle target, ref long tests)
		{
			var runs = new List<int[]>();
			for (int row = 0; row < rows; row++)
			{
				int start = -1;
				target.Y = y + row * step;
				for (int col = 0; col <= cols; col++)
				{
					bool overlap = false;
					if (col < cols)
					{
						target.X = x + col * step;
						overlap = p.Colliding(box, target);
						tests++;
					}
					if (overlap && start < 0) start = col;
					if (!overlap && start >= 0) { runs.Add(new[] { row, start, col - start }); start = -1; }
				}
			}
			return runs;
		}

		// Gym fixtures: query recorded poses atomically, without advancing AI or
		// leaving test projectiles in the world. Uses the same export as /state.
		public static object Probe(JsonElement fixture)
		{
			if (Main.worldName != "Gym") throw new ArgumentException("collision fixtures require Gym");
			var saved = new Dictionary<int, Projectile>();
			try
			{
				foreach (var seed in fixture.GetProperty("shots").EnumerateArray())
				{
					int i = seed.GetProperty("i").GetInt32();
					if (i < 0 || i >= Main.maxProjectiles || saved.ContainsKey(i)) throw new ArgumentException("invalid fixture slot");
					saved[i] = Main.projectile[i];
					var p = new Projectile();
					p.SetDefaults(seed.GetProperty("id").GetInt32());
					p.whoAmI = i;
					p.position = new Vector2(seed.GetProperty("x").GetSingle(), seed.GetProperty("y").GetSingle());
					p.velocity = new Vector2(seed.GetProperty("vx").GetSingle(), seed.GetProperty("vy").GetSingle());
					p.width = seed.GetProperty("w").GetInt32(); p.height = seed.GetProperty("h").GetInt32();
					p.rotation = seed.GetProperty("rotation").GetSingle(); p.scale = seed.GetProperty("scale").GetSingle();
					p.damage = seed.GetProperty("damage").GetInt32();
					p.ai[0] = seed.GetProperty("ai0").GetSingle(); p.ai[1] = seed.GetProperty("ai1").GetSingle();
					var local = seed.GetProperty("local_ai");
					for (int n = 0; n < Math.Min(local.GetArrayLength(), p.localAI.Length); n++) p.localAI[n] = local[n].GetSingle();
					p.active = p.hostile = true; p.friendly = false;
					var observed = p.GetGlobalProjectile<AttackShapes>();
					observed.born = Main.GameUpdateCount - seed.GetProperty("age").GetUInt32();
					observed.checkedOnce = seed.GetProperty("damage_checked").GetBoolean();
					observed.checkedAt = Main.GameUpdateCount;
					var box = seed.GetProperty("damage_box");
					observed.damageBox = new Rectangle(box[0].GetInt32(), box[1].GetInt32(), box[2].GetInt32(), box[3].GetInt32());
					Main.projectile[i] = p;
				}
				var w = fixture.GetProperty("window");
				return Sample(w.GetProperty("x0").GetInt32(), w.GetProperty("y0").GetInt32(),
					w.GetProperty("w").GetInt32(), w.GetProperty("h").GetInt32(),
					new NameValueCollection { ["shape_step"] = w.GetProperty("step").GetInt32().ToString() });
			}
			finally { foreach (var pair in saved) Main.projectile[pair.Key] = pair.Value; }
		}
	}

	public class AttackShapeSession : ModSystem
	{
		public override void OnWorldLoad() => AttackShapes.Reset();
		public override void OnWorldUnload() => AttackShapes.Reset();
	}
}
