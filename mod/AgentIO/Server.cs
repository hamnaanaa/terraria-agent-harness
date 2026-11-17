using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace AgentIO
{
	// The HTTP side. A request is read on a pool thread; whatever touches the game is queued for the game's own thread
	// (PostUpdateEverything, once a tick) and the request waits for the answer.
	//
	//   GET  /ping                 no game needed; "paused" while the world is held still
	//   GET  /state?x0=&y0=&w=&h=  the body, its physics, the bag, the cells around it, the enemies
	//   POST /run                  keys tick by tick, answered with the body's trace (Script.cs)
	//   POST /keys                 keys held until replaced (for the motor)
	//   POST /overlay              drawings over the world: cells, paths, labels (Overlay.cs)
	//   POST /stamp, /gym          the test gym (Gym.cs)
	//   POST /pause                the world held still, or let run again (single player)
	public class Server : ModSystem
	{
		public const string Prefix = "http://127.0.0.1:17900/";
		static HttpListener _listener;
		static Thread _thread;
		static volatile bool _running;
		public static volatile bool Paused;      // POST /pause: the world held still
		static bool _canPause;                    // the game's update took the pause in (Load)
		public static long Loaded;                // the real time in ms when a world last loaded: /state's session.loaded

		public sealed class Job
		{
			public Func<Job, string> Work;    // the answer, or null when it comes later through Finish
			public readonly TaskCompletionSource<string> Done = new(TaskCreationOptions.RunContinuationsAsynchronously);
			public void Finish(string json) => Done.TrySetResult(json);
		}

		static readonly ConcurrentQueue<Job> Jobs = new();

		public override void Load()
		{
			// The pause is the game's own: Main.DoUpdate skips the world's update whenever Main.CanPauseGame says so (its
			// menu open, in single player), and nothing in the world moves, the clock included. Its answer is taken with
			// the mod's pause added. While paused no job runs: the game's thread waits like it does in its menu.
			Terraria.IL_Main.DoUpdate += il =>
			{
				var c = new MonoMod.Cil.ILCursor(il);
				if (c.TryGotoNext(MonoMod.Cil.MoveType.After,
					    i => MonoMod.Cil.ILPatternMatchingExt.MatchCall(i, typeof(Main), "CanPauseGame")))
				{
					c.EmitDelegate<Func<bool, bool>>(game => game || (Paused && Main.netMode == NetmodeID.SinglePlayer));
					_canPause = true;
				}
				else Mod.Logger.Warn("pause: no call of Main.CanPauseGame found in Main.DoUpdate; POST /pause cannot hold the world");
			};
			try
			{
				_listener = new HttpListener();
				_listener.Prefixes.Add(Prefix);
				_listener.Start();
				_running = true;
				_thread = new Thread(Loop) { IsBackground = true, Name = "AgentIO http" };
				_thread.Start();
				Mod.Logger.Info("AgentIO listening on " + Prefix);
			}
			catch (Exception e) { Mod.Logger.Error("AgentIO could not listen on " + Prefix + ": " + e); }
		}

		public override void Unload()
		{
			_running = false;
			try { _listener?.Stop(); _listener?.Close(); } catch { }
			_listener = null;
			_thread = null;
		}

		public override void OnWorldLoad()
		{
			Loaded = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
			Paused = false;             // a world always starts running
			Script.Cancel();
			Counters.Hits.Clear();      // the game counts its ticks from 0 again at a world load
		}

		// the tick the hits carry (Counters.Hit): the one this frame's /state shows. The game adds 1 to its count after
		// the players update and before the shots (Main.DoUpdateInWorld): stamped with the count itself, a touch, a
		// fall, lava or drowning would carry the tick of the state read before them
		public override void PreUpdatePlayers() => Counters.FrameTick = Main.GameUpdateCount + 1;

		public override void PostUpdateEverything()
		{
			Script.AfterTick(Main.LocalPlayer);
			Script.Watch();
			while (Jobs.TryDequeue(out var job))
			{
				// a request that gave up waiting has its answer already (a paused world keeps every poll queued): run
				// late, a /state would only cost time, and a craft, a save or keys would act unasked
				if (job.Done.Task.IsCompleted) continue;
				try
				{
					string r = job.Work(job);
					if (r != null) job.Finish(r);
				}
				catch (Exception e) { job.Finish(Json.Error(e.GetType().Name + ": " + e.Message)); }
			}
		}

		// runs on the game's thread and hands the answer back
		public static string OnGame(Func<Job, string> work, int timeoutMs)
		{
			var job = new Job { Work = work };
			Jobs.Enqueue(job);
			if (!job.Done.Task.Wait(timeoutMs))
				job.Finish(Json.Error("the game did not answer within " + timeoutMs + " ms: paused, or not in a world"));
			return job.Done.Task.Result;
		}

		void Loop()
		{
			while (_running && _listener != null)
			{
				HttpListenerContext ctx;
				try { ctx = _listener.GetContext(); } catch { break; }
				ThreadPool.QueueUserWorkItem(_ => Handle(ctx));
			}
		}

		static void Handle(HttpListenerContext ctx)
		{
			string answer;
			int code = 200;
			try
			{
				string path = ctx.Request.Url.AbsolutePath.TrimEnd('/');
				string body = "";
				if (ctx.Request.HasEntityBody)
					using (var sr = new System.IO.StreamReader(ctx.Request.InputStream, Encoding.UTF8))
						body = sr.ReadToEnd();
				answer = Route(path, body, ctx.Request.QueryString);
			}
			catch (Exception e)
			{
				answer = Json.Error(e.GetType().Name + ": " + e.Message);
				code = 500;
			}
			try
			{
				byte[] bytes = Encoding.UTF8.GetBytes(answer);
				ctx.Response.StatusCode = code;
				ctx.Response.ContentType = "application/json";
				ctx.Response.ContentLength64 = bytes.Length;
				ctx.Response.OutputStream.Write(bytes, 0, bytes.Length);
				ctx.Response.Close();
			}
			catch { }
		}

		static string Route(string path, string body, NameValueCollection q)
		{
			switch (path)
			{
				// paused: the world is held still (POST /pause, the game's menu, its window in the back), so the routes on
				// the game's thread wait; a caller can tell that from a game gone
				case "/ping": return Json.Of(new Dictionary<string, object>
				{
					["ok"] = true, ["mod"] = "AgentIO", ["paused"] = Main.gamePaused && !Main.gameMenu,
				});
				case "/state": return OnGame(_ => State.Read(q), 3000);
				case "/keys": return OnGame(_ => Keys.Hold(body), 3000);
				case "/overlay": return Overlay.Set(body);       // pictures only: no game state is touched
				case "/ghost": return OnGame(_ => Ghosts.Handle(body), 10000);    // the body recorded, then replayed as see-through copies
				case "/camera": return OnGame(_ => Ghosts.Camera(body), 3000);    // the camera pinned, the zoom, the interface hidden
				case "/trace": return Script.Last ?? "{\"ticks\":0,\"trace\":[]}";   // the last run's trace
				case "/texture": return OnGame(_ => Tex.Png(q), 10000);   // a sprite sheet for the replay page
				case "/craft": return OnGame(_ => Craft.Make(body), 5000);
				case "/bag": return OnGame(_ => Craft.Swap(body), 3000);      // an item dragged into the hotbar
				case "/wear": return OnGame(_ => Craft.Wear(body), 3000);     // armour or an accessory put on
				case "/newplayer": return Menu.NewPlayer(body);   // character creation also works at the title screen
				case "/swing": return OnGame(_ => SwingRecorder.Handle(body), 3000);   // the engine's melee hitbox per tick
				case "/chest": return OnGame(_ => Craft.Chest(body), 5000);
				case "/trash": return OnGame(_ => Craft.Trash(body), 3000);      // named items into the trash slot
				case "/fits": return OnGame(_ => Craft.Fits(body), 5000);      // where the game would place an item's tile
				case "/stamp": return OnGame(_ => Gym.Stamp(body), 10000);
				case "/gym": return OnGame(_ => Gym.Apply(body), 10000);
				case "/save": return OnGame(_ => Gym.Save(), 20000);           // the world and the player to disk
				case "/pause": return Pause(body);      // here, not on the game's thread: a paused game runs no job
				case "/menu": return Menu.Leave();          // the world saved and left (Menu.cs)
				case "/reload": return Menu.Reload();       // at the title screen: the mods loaded again from disk
				case "/enter": return Menu.Enter(body);     // at the title screen: a character into a world
				case "/run/cancel": return Script.Cancel();
				case "/run":
					var run = Script.Parse(body);
					return OnGame(job => Script.Start(run, job), run.TimeoutMs);
				default: return Json.Error("no path " + path + " (ping, state, run, keys, overlay, craft, chest, trash, stamp, gym, pause)");
			}
		}

		// POST /pause {"on": true | false}. The game reads the flag at its next update, a sixtieth of a second away, so
		// the answer waits up to 2 s for the game's own Main.gamePaused to follow: {"paused": bool}, and a «why» when
		// it did not.
		static string Pause(string body)
		{
			bool on;
			using (var doc = System.Text.Json.JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body))
			{
				if (!doc.RootElement.TryGetProperty("on", out var v) ||
				    v.ValueKind is not (System.Text.Json.JsonValueKind.True or System.Text.Json.JsonValueKind.False))
					return Json.Error("POST /pause {\"on\": true} or {\"on\": false}");
				on = v.GetBoolean();
			}
			string why = !_canPause ? "the mod found no place in the game's update to hold it (the mod's log says more)"
				: Main.netMode != NetmodeID.SinglePlayer ? "the game pauses only in single player"
				: Main.gameMenu ? "no world is loaded" : null;
			if (why != null)
			{
				Paused = false;
				return Json.Of(new Dictionary<string, object> { ["paused"] = false, ["why"] = why });
			}
			Paused = on;
			var until = DateTime.UtcNow.AddSeconds(2);
			while (Main.gamePaused != on && DateTime.UtcNow < until) Thread.Sleep(10);
			bool paused = Main.gamePaused;
			var answer = new Dictionary<string, object> { ["paused"] = paused };
			if (paused != on)
			{
				if (on) Paused = false;     // a pause that did not take is not left to take later, unasked
				answer["why"] = on ? "the game did not stop within 2 s"
					: "the game holds a pause of its own: its menu is open, or its window is in the back";
			}
			return Json.Of(answer);
		}
	}

	public static class Json
	{
		public static string Of(object o) => System.Text.Json.JsonSerializer.Serialize(o);
		public static string Error(string why) => Of(new System.Collections.Generic.Dictionary<string, object> { ["error"] = why });
	}
}
