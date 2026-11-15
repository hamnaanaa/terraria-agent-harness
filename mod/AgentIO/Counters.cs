using System.Collections.Generic;
using Terraria;
using Terraria.DataStructures;
using Terraria.ID;
using Terraria.Localization;
using Terraria.ModLoader;

namespace AgentIO
{
	// Exact counts from the game's own hooks, for the bench's numbers: shots the body fired, hits it landed (by projectile
	// and by hand), touches and shots it took and what they cost. Read in /state as "counters"; never reset, so a run
	// takes the difference between its start and its end.
	public static class Counters
	{
		public static long ProjectilesFired, ProjectileHits, MeleeAttacks, MeleeHits, TouchesTaken, ShotsTaken, DamageDealt, DamageTaken, Kills;
		public static readonly Dictionary<string, long> KillsByName = new();     // the same kills, by the enemy's name

		public static Dictionary<string, object> Json() => new()
		{
			["projectiles_fired"] = ProjectilesFired, ["projectile_hits"] = ProjectileHits,
			["melee_attacks"] = MeleeAttacks, ["melee_hits"] = MeleeHits, ["kills"] = Kills,
			["by_weapon"] = Rows(Weapons), ["by_projectile"] = Rows(Projectiles),
			["touches_taken"] = TouchesTaken, ["shots_taken"] = ShotsTaken, ["damage_dealt"] = DamageDealt,
			["damage_taken"] = DamageTaken, ["kills_by_name"] = new Dictionary<string, long>(KillsByName),
		};

		// Counts are observations of game hooks. An attack is one weapon-use animation; a hit is one
		// damage event. A single explosion/swing can hit several enemies, so attacks_hit is counted once.
		public class Tally
		{
			public int Id;
			public string Name;
			public bool Melee;
			public long Attempts, Hits, Landed, Damage;
		}
		public class Attack
		{
			public Tally Weapon;
			public bool Landed;
		}
		public static readonly Dictionary<int, Tally> Weapons = new(), Projectiles = new();
		public static Attack CurrentAttack;

		public static Tally Get(Dictionary<int, Tally> rows, int id, string name)
		{
			if (!rows.TryGetValue(id, out var row)) rows[id] = row = new Tally { Id = id, Name = name };
			return row;
		}

		static List<Dictionary<string, object>> Rows(Dictionary<int, Tally> rows)
		{
			var result = new List<Dictionary<string, object>>();
			bool weapons = object.ReferenceEquals(rows, Weapons);
			foreach (var r in rows.Values)
				result.Add(new() { ["id"] = r.Id, ["name"] = r.Name,
					[weapons ? "attacks" : "fired"] = r.Attempts, ["hits"] = r.Hits,
					[weapons ? "attacks_hit" : "projectiles_hit"] = r.Landed, ["damage"] = r.Damage });
			return result;
		}

		public static bool IsWeapon(Item item)
		{
			if (item.damage > 0) return true;
			// Bombs have zero item damage; their projectile acquires damage when it explodes.
			// Use the engine's explosive set / AI style, including its own default projectile sample.
			return item.shoot > 0 && ContentSamples.ProjectilesByType.TryGetValue(item.shoot, out var sample)
				&& (sample.aiStyle == ProjAIStyleID.Explosive || ProjectileID.Sets.Explosive[item.shoot]);
		}

		public static void Began(Item item)
		{
			CurrentAttack = null;
			if (!IsWeapon(item)) return;
			var row = Get(Weapons, item.type, item.Name);
			row.Melee = item.CountsAsClass(DamageClass.Melee);
			row.Attempts++;
			CurrentAttack = new Attack { Weapon = row };
			if (row.Melee) MeleeAttacks++;
		}

		public static void WeaponHit(Tally weapon, Attack attack, int damage)
		{
			if (weapon == null) return;
			weapon.Hits++; weapon.Damage += damage;
			if (attack != null && !attack.Landed) { attack.Landed = true; weapon.Landed++; }
		}

		public static void Killed(NPC npc)
		{
			Kills++;
			KillsByName.TryGetValue(npc.TypeName, out long n);
			KillsByName[npc.TypeName] = n + 1;
		}

		// The last 40 hits the body took, oldest first, read in /state as "hits": the tick, the health it cost and what
		// dealt it. Emptied when a world loads (Server.OnWorldLoad): the game counts its ticks from 0 again there.
		public const int HitsKept = 40;
		public static readonly List<Dictionary<string, object>> Hits = new();
		public static uint FrameTick;      // the tick a hit carries: the one this frame's /state shows (Server.PreUpdatePlayers)

		public static Dictionary<string, object> Hit(int damage, string source, string name)
		{
			var hit = new Dictionary<string, object>
			{
				["tick"] = FrameTick, ["damage"] = damage, ["source"] = source, ["name"] = name,
			};
			Hits.Add(hit);
			if (Hits.Count > HitsKept) Hits.RemoveAt(0);
			return hit;
		}

		// Drowning takes 2 health every 7 ticks once the breath is gone (Player.CheckDrowning; 42 with a Diving Helmet),
		// so 280 ticks of it, under 5 s, would fill all 40 and push the task's other hits out. One stretch of drowning is
		// one hit, its damage summed and its tick the last loss's; it ends when the body has air again (CountTaken.Load)
		// or another hit comes.
		public static Dictionary<string, object> Drowning;

		public static void Drowned(int damage)
		{
			if (Drowning != null && Hits.Count > 0 && Hits[^1] == Drowning)
			{
				Drowning["damage"] = (int)Drowning["damage"] + damage;
				Drowning["tick"] = FrameTick;
			}
			else Drowning = Hit(damage, "drown", null);
		}

		// deaths since the mod loaded, and the last one's line as the game's chat printed it, with its tick
		public static long Deaths;
		public static string DeathLine;
		public static uint? DeathTick;

		public static Dictionary<string, object> DeathsJson() => new()
		{
			["count"] = Deaths, ["last_line"] = DeathLine, ["last_tick"] = DeathTick,
		};

		// whether a block let loose the shot in each projectile slot, set as it spawns: the game flags its dart, spear
		// and flame traps (Projectile.trap) but not a boulder rolling off its pressure plate. Sand, silt and slush falling
		// come from a broken block too (WorldGen.SpawnFallingBlockProjectile) and hurt for 10; they are no trap
		public static readonly bool[] FromTile = new bool[Main.maxProjectiles + 1];
	}

	public class CountUses : GlobalItem
	{
		public override void UseAnimation(Item item, Player player)
		{
			if (player.whoAmI == Main.myPlayer) Counters.Began(item);
		}
	}

	public class CountShots : GlobalProjectile
	{
		public override bool InstancePerEntity => true;
		public Counters.Tally Weapon, Flight;
		public Counters.Attack Attack;
		bool landed;

		public override void OnSpawn(Projectile projectile, IEntitySource source)
		{
			if (projectile.whoAmI >= 0 && projectile.whoAmI < Counters.FromTile.Length)
				Counters.FromTile[projectile.whoAmI] = source is AEntitySource_Tile && projectile.aiStyle != ProjAIStyleID.FallingTile;
			if (projectile.owner != Main.myPlayer) return;
			if (source is EntitySource_ItemUse use && Counters.IsWeapon(use.Item))
			{
				Weapon = Counters.Get(Counters.Weapons, use.Item.type, use.Item.Name);
				Weapon.Melee = use.Item.CountsAsClass(DamageClass.Melee);
				if (Counters.CurrentAttack?.Weapon == Weapon) Attack = Counters.CurrentAttack;
			}
			else if (source is EntitySource_Parent parent && parent.Entity is Projectile origin)
			{
				var previous = origin.GetGlobalProjectile<CountShots>();
				Weapon = previous.Weapon; Attack = previous.Attack;
			}
			if (Weapon == null) return;     // only attributed weapon shots; falling stars can carry this player owner too
			Counters.ProjectilesFired++;
			Flight = Counters.Get(Counters.Projectiles, projectile.type, projectile.Name);
			Flight.Attempts++;
		}

		public void Hit(int damage)
		{
			if (Flight != null)
			{
				Flight.Hits++; Flight.Damage += damage;
				if (!landed) { landed = true; Flight.Landed++; }
			}
			Counters.WeaponHit(Weapon, Attack, damage);
		}
	}

	public class CountHits : GlobalNPC
	{
		public override void OnKill(NPC npc)
		{
			int player = Main.myPlayer;
			if (player >= 0 && player < Main.maxPlayers &&
				(npc.lastInteraction == player || npc.playerInteraction[player])) Counters.Killed(npc);
		}
		public override void OnHitByProjectile(NPC npc, Projectile projectile, NPC.HitInfo hit, int damageDone)
		{
			if (projectile.owner != Main.myPlayer) return;
			var shot = projectile.GetGlobalProjectile<CountShots>();
			if (shot.Flight == null) return;
			Counters.ProjectileHits++;
			if (shot.Weapon.Melee) Counters.MeleeHits++;
			shot.Hit(damageDone);
			Counters.DamageDealt += damageDone;
		}

		public override void OnHitByItem(NPC npc, Player player, Item item, NPC.HitInfo hit, int damageDone)
		{
			if (player.whoAmI != Main.myPlayer) return;
			Counters.MeleeHits++;
			var weapon = Counters.Get(Counters.Weapons, item.type, item.Name);
			Counters.WeaponHit(weapon, Counters.CurrentAttack?.Weapon == weapon ? Counters.CurrentAttack : null, damageDone);
			Counters.DamageDealt += damageDone;
		}
	}

	public class CountTaken : ModPlayer
	{
		public override void Load()
		{
			// drowning takes its 2 health at a time straight off the life (Player.CheckDrowning), past Player.Hurt, so no
			// hurt hook sees it: the loss is read around the game's own check, and breath back ends the stretch
			Terraria.On_Player.CheckDrowning += (orig, self) =>
			{
				int before = self.statLife;
				orig(self);
				if (self.whoAmI != Main.myPlayer) return;
				if (self.statLife < before) Counters.Drowned(before - self.statLife);
				else if (self.breath > 0) Counters.Drowning = null;
			};
			// the death line is the text Player.KillMe makes for the game's chat, taken as it is made: asked for again,
			// the game would pick another of its random lines
			Terraria.IL_Player.KillMe += il =>
			{
				var c = new MonoMod.Cil.ILCursor(il);
				if (c.TryGotoNext(MonoMod.Cil.MoveType.After, i => MonoMod.Cil.ILPatternMatchingExt.MatchCallvirt(i,
					    typeof(PlayerDeathReason), nameof(PlayerDeathReason.GetDeathText))))
				{
					c.Emit(Mono.Cecil.Cil.OpCodes.Dup);
					c.Emit(Mono.Cecil.Cil.OpCodes.Ldarg_0);
					c.EmitDelegate<System.Action<NetworkText, Player>>((text, p) =>
					{
						if (p.whoAmI == Main.myPlayer) Counters.DeathLine = text.ToString();
					});
				}
				else Mod.Logger.Warn("deaths: no death text found in Player.KillMe; last_line stays null");
			};
		}

		public override void OnHitByNPC(NPC npc, Player.HurtInfo hurtInfo)
		{
			if (Player.whoAmI != Main.myPlayer) return;
			Counters.TouchesTaken++;
			Counters.DamageTaken += hurtInfo.Damage;
		}

		public override void OnHitByProjectile(Projectile proj, Player.HurtInfo hurtInfo)
		{
			if (Player.whoAmI != Main.myPlayer) return;
			Counters.ShotsTaken++;
			Counters.DamageTaken += hurtInfo.Damage;
		}

		// Every hit the body takes passes here, before the health goes (Player.Hurt). What dealt it is the game's own
		// record of the hit, the one its death line is made from: an enemy, a shot, else one of its numbered causes
		// (0 a fall, 1 drowning, 2 lava; the rest, spikes and cactus among them, stay «other» with no name).
		public override void OnHurt(Player.HurtInfo info)
		{
			if (Player.whoAmI != Main.myPlayer) return;
			var src = info.DamageSource;
			string source = "other", name = null;
			if (src.SourceNPCIndex >= 0 && src.SourceNPCIndex < Main.maxNPCs)
			{
				source = "enemy";
				name = Main.npc[src.SourceNPCIndex].TypeName;
			}
			else if (src.SourceProjectileLocalIndex >= 0 && src.SourceProjectileLocalIndex < Main.maxProjectiles)
			{
				// a trap: the game's own trap flag, or a shot a block let loose (a boulder off its plate)
				var s = Main.projectile[src.SourceProjectileLocalIndex];
				bool same = s.active && s.type == src.SourceProjectileType;
				source = same && (s.trap || Counters.FromTile[src.SourceProjectileLocalIndex]) ? "trap" : "shot";
				name = Lang.GetProjectileName(src.SourceProjectileType).Value;
			}
			else if (src.SourceOtherIndex == 0) source = "fall";
			else if (src.SourceOtherIndex == 1) source = "drown";
			else if (src.SourceOtherIndex == 2) source = "lava";
			Counters.Hit(info.Damage, source, name);
		}

		// the game calls this before it makes the death line (Player.KillMe), so the line left from the last death is
		// cleared here and the new one set right after (Load)
		public override void Kill(double damage, int hitDirection, bool pvp, PlayerDeathReason damageSource)
		{
			if (Player.whoAmI != Main.myPlayer) return;
			Counters.Deaths++;
			Counters.DeathTick = Counters.FrameTick;
			Counters.DeathLine = null;
		}
	}
}
