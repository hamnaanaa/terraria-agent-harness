using System.Collections.Generic;
using System.Text.Json;
using Terraria;

namespace AgentIO
{
	// The runner for tests: a list of steps, each a press held for a number of ticks, played tick by tick, and the body's
	// trace sent back: where it was, how fast, on the ground or not, its breath and health, what the hand was doing, and
	// what stood in the watched cells, after every tick. Rule tests compare this trace with what the rule predicts.
	//
	// POST /run {"steps": [{"ticks": 15, "keys": ["jump"]}, {"ticks": 40, "keys": []}], "watch": [[x, y]], "timeout_s": 20}
	// The loop's motor uses it too, with "replace": true, "wait": false: the steps start at once in place of whatever
	// ran, the answer comes back right away, and GET /trace gives the last run's trace (the replaced run's: what the body
	// really did on the previous pick, for the prediction's honesty).
	public static class Script
	{
		public sealed class Step
		{
			public int Ticks;
			public Keys.Press Press;
		}

		public sealed class Run
		{
			public List<Step> Steps = new();
			public List<(int x, int y)> Watch = new();
			public int TimeoutMs = 20000;
			public int Total;
			public bool Replace, Wait = true;
			public string Id, Goal;
			public long Session;
			public int CancelVersion;
			public ulong Expiry, AppliedTick;
			public ulong StartTick;                       // the game tick the steps start on (0: at once)
			public Server.Job Job;                        // for a run nobody waits for
		}

		static Run _run;
		static Server.Job _job;
		static int _step, _tickInStep;
		static List<Dictionary<string, object>> _trace;
		static volatile string _last;
		static Run _next;                                 // a run waiting for its start tick
		public static string Last => _last;

		static readonly object Gate = new();
		static int _cancelVersion;
		static object _finished;

		static object Status(Run run, string status) => run?.Id == null ? null : new
		{
			command_id = run.Id, goal = run.Goal, scheduled_tick = run.StartTick,
			applied_tick = run.AppliedTick, expiry_tick = run.Expiry, status,
		};

		public static object Execution()
		{
			lock (Gate) return new { active = Status(_run, "active"), pending = Status(_next, "pending"), finished = _finished };
		}

		static void DropPending(string reason)
		{
			if (_next == null) return;
			if (_next.Id != null) _finished = Status(_next, reason);
			_next.Job?.Finish(Json.Error(reason));
			_next = null;
		}

		public static string Cancel()
		{
			lock (Gate)
			{
				_cancelVersion++;
				if (_run != null)
				{
					if (_run.Id != null) _finished = Status(_run, "cancelled");
					_job?.Finish(Json.Error("cancelled"));
					_run = null;
				}
				DropPending("cancelled");
				Keys.Release();
				return Json.Of(new { cancelled = true });
			}
		}

		// the press of this tick: a run due now takes over first (its first step lands on exactly its start tick)
		public static Keys.Press Current
		{
			get
			{
				lock (Gate)
				{
					var tick = Main.GameUpdateCount;
					if (_next?.Id != null && (Main.LocalPlayer.dead || tick >= _next.Expiry))
						DropPending(Main.LocalPlayer.dead ? "died" : "expired");
					if (_next != null && tick >= _next.StartTick) Begin(_next);
					if (_run == null || _step >= _run.Steps.Count) return null;
					if (_run.Id != null && tick >= _run.Expiry)
					{
						Finish(Main.LocalPlayer, false, reason: "expired");
						return null;
					}
					if (_run.AppliedTick == 0) _run.AppliedTick = tick + 1;
					return _run.Steps[_step].Press;
				}
			}
		}

		public static Run Parse(string body)
		{
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			var run = new Run { CancelVersion = System.Threading.Volatile.Read(ref _cancelVersion) };
			if (root.TryGetProperty("command_id", out var id))
			{
				run.Id = id.GetString();
				run.Goal = root.GetProperty("goal").GetString();
				run.Session = root.GetProperty("session").GetInt64();
				run.Expiry = root.GetProperty("expiry_tick").GetUInt64();
			}
			int total = 0;
			if (root.TryGetProperty("steps", out var steps))
				foreach (var s in steps.EnumerateArray())
				{
					int ticks = s.TryGetProperty("ticks", out var t) ? t.GetInt32() : 1;
					run.Steps.Add(new Step { Ticks = System.Math.Max(1, ticks), Press = Keys.Parse(s) });
					total += ticks;
				}
			for (int i = 1; i < run.Steps.Count; i++)                      // a jump held across steps stays held
				if (run.Steps[i].Press.Jump && run.Steps[i - 1].Press.Jump) run.Steps[i].Press.HoldJump = true;
			if (root.TryGetProperty("watch", out var watch))
				foreach (var c in watch.EnumerateArray())
					run.Watch.Add((c[0].GetInt32(), c[1].GetInt32()));
			run.TimeoutMs = root.TryGetProperty("timeout_s", out var to) ? (int)(to.GetDouble() * 1000)
				: 5000 + total * 1000 / 60 * 2;
			run.Total = total;
			run.Replace = root.TryGetProperty("replace", out var rp) && rp.GetBoolean();
			run.Wait = !root.TryGetProperty("wait", out var w) || w.GetBoolean();
			run.StartTick = root.TryGetProperty("start_tick", out var st) ? st.GetUInt64() : 0;
			return run;
		}

		public static string Start(Run run, Server.Job job)
		{
			lock (Gate) return StartLocked(run, job);
		}

		static string StartLocked(Run run, Server.Job job)
		{
			if (run.Steps.Count == 0) return Json.Error("no steps");
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			if (_run != null && _job != null && !_job.Done.Task.IsCompleted && !run.Replace)
				return Json.Error("a run is going on");
			if (run.Id != null)
			{
				if (p.dead || run.Session != Server.Loaded || run.CancelVersion != _cancelVersion)
					return Json.Error("command session ended");
				if (Main.GameUpdateCount >= run.Expiry || run.Expiry <= run.StartTick)
					return Json.Error("command expired");
			}
			DropPending("replaced");
			run.Job = run.Wait ? job : new Server.Job();
			if (run.StartTick > Main.GameUpdateCount)
			{
				_next = run;                              // the run going on keeps the keys until that tick
				if (!run.Wait)
					return Json.Of(new Dictionary<string, object> { ["started"] = false, ["at"] = run.StartTick,
						["now"] = Main.GameUpdateCount, ["ticks"] = run.Total });
				return null;
			}
			bool late = run.StartTick > 0;
			Begin(run);
			if (!run.Wait)
				return Json.Of(new Dictionary<string, object> { ["started"] = true, ["late"] = late,
					["now"] = Main.GameUpdateCount, ["ticks"] = run.Total });
			return null;                                  // answered by AfterTick when the last step is done
		}

		// the run takes the keys now: whatever ran is answered with what the body did in it
		static void Begin(Run run)
		{
			var p = Main.LocalPlayer;
			// a run that takes over mid-hold keeps the jump down when its first step holds it too
			if (_run != null && _step < _run.Steps.Count && _run.Steps[_step].Press.Jump && run.Steps.Count > 0 && run.Steps[0].Press.Jump)
				run.Steps[0].Press.HoldJump = true;
			if (_run != null && _job != null && !_job.Done.Task.IsCompleted) Finish(p, false, true);
			_next = null;
			_run = run;
			if (run.Job != null) _job = run.Job;
			_step = 0;
			_tickInStep = 0;
			_trace = run.Id == null ? new List<Dictionary<string, object>> { Row(p, 0) } : null;
		}

		// once a tick, whatever the body does: a dead body is not updated, so its run ends here, with the death in it
		public static void Watch()
		{
			lock (Gate) WatchLocked();
		}

		static void WatchLocked()
		{
			if (_next?.Id != null && Main.LocalPlayer.dead) DropPending("died");
			if (_run == null) return;
			if (_job.Done.Task.IsCompleted) { _run = null; return; }
			var p = Main.LocalPlayer;
			if (p != null && p.dead)
			{
				_trace?.Add(Row(p, _trace.Count));
				Finish(p, true);
			}
		}

		static void Finish(Player p, bool died, bool replaced = false, string reason = null)
		{
			if (_run.Id != null)
			{
				_finished = Status(_run, reason ?? (died ? "died" : replaced ? "replaced" : "finished"));
				_job.Finish(Json.Of(_finished));
				_run = null;
				return;
			}
			string json = Json.Of(new Dictionary<string, object>
			{
				["ticks"] = _trace.Count - 1, ["trace"] = _trace, ["watch"] = _run.Watch.ConvertAll(c => new[] { c.x, c.y }),
				["end"] = State.Body(p), ["died"] = died, ["replaced"] = replaced,
			});
			_last = json;
			_job.Finish(json);
			_run = null;
		}

		public static void AfterTick(Player p)
		{
			lock (Gate) AfterTickLocked(p);
		}

		static void AfterTickLocked(Player p)
		{
			if (_run == null) return;
			if (_job.Done.Task.IsCompleted) { _run = null; return; }    // the request gave up waiting
			_trace?.Add(Row(p, _trace.Count));
			if (++_tickInStep >= _run.Steps[_step].Ticks)
			{
				_step++;
				_tickInStep = 0;
			}
			if (_step >= _run.Steps.Count) Finish(p, false);
		}

		static List<float[]> EnemyRows()
		{
			var list = new List<float[]>();
			for (int i = 0; i < Main.maxNPCs; i++)
			{
				var n = Main.npc[i];
				if (n == null || !n.active || n.friendly || n.townNPC) continue;
				list.Add(new[] { i, n.position.X, n.position.Y, n.velocity.X, n.velocity.Y, n.life, n.ai[0], n.ai[1], n.ai[2], n.ai[3] });
			}
			return list;
		}

		static List<float[]> ShotRows()
		{
			var list = new List<float[]>();
			for (int i = 0; i < Main.maxProjectiles; i++)
			{
				var s = Main.projectile[i];
				if (s == null || !s.active) continue;
				list.Add(new[] { i, s.position.X, s.position.Y, s.velocity.X, s.velocity.Y, s.type });
			}
			return list;
		}

		static int[] Buffs(Player p)
		{
			var ids = new List<int>();
			for (int b = 0; b < Player.MaxBuffs; b++)
				if (p.buffType[b] > 0) ids.Add(p.buffType[b]);
			return ids.ToArray();
		}

		static Dictionary<string, object> Row(Player p, int t)
		{
			var row = new Dictionary<string, object>
			{
				["t"] = t, ["x"] = p.position.X, ["y"] = p.position.Y, ["vx"] = p.velocity.X, ["vy"] = p.velocity.Y,
				["ground"] = p.velocity.Y == 0f, ["jump"] = p.jump, ["wet"] = p.wet, ["lava"] = p.lavaWet,
				["breath"] = p.breath, ["life"] = p.statLife, ["fall_start"] = p.fallStart, ["dead"] = p.dead,
				["item_animation"] = p.itemAnimation, ["item_time"] = p.itemTime, ["selected"] = p.selectedItem,
				["target"] = new[] { Player.tileTargetX, Player.tileTargetY },
				// the movement numbers this tick ran on: buffs, gear and liquids change them from one tick to the next
				["run_acc"] = p.runAcceleration, ["run_top"] = p.maxRunSpeed, ["run_slow"] = p.runSlowdown,
				["gravity"] = p.gravity, ["fall_top"] = p.maxFallSpeed, ["jump_speed"] = Player.jumpSpeed,
				["jump_hold"] = Player.jumpHeight, ["move_speed"] = p.moveSpeed, ["buffs"] = Buffs(p),
				["acc_run"] = p.accRunSpeed, ["pick_speed"] = p.pickSpeed, ["tile_speed"] = p.tileSpeed,
				["immune"] = p.immune, ["immune_time"] = p.immuneTime, ["dir"] = p.direction,
				// every enemy and shot this tick, compact: [index, x, y, vx, vy, life] and [index, x, y, vx, vy]
				["enemies"] = EnemyRows(), ["shots"] = ShotRows(),
			};
			if (_run != null && _run.Watch.Count > 0)
			{
				var cells = new int[_run.Watch.Count];
				for (int i = 0; i < cells.Length; i++)
				{
					var (x, y) = _run.Watch[i];
					var tile = Main.tile[x, y];
					cells[i] = tile.HasTile ? tile.TileType : -1;
				}
				row["watched"] = cells;
			}
			return row;
		}
	}
}
