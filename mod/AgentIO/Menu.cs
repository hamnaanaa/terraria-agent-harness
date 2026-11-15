using System;
using System.Collections.Generic;
using System.Reflection;
using System.Text.Json;
using System.Threading.Tasks;
using Terraria;
using Terraria.IO;
using Terraria.ModLoader;

namespace AgentIO
{
	// The title screen without clicks, for a mod rebuilt from the command line (dotnet tModLoader.dll -server -build):
	//   POST /menu                                   the world saved and left for the title screen
	//   POST /reload                                 at the title screen: the mods loaded again from disk
	//   POST /enter {"player": "Name", "world": "Gym"}   at the title screen: that character into that world
	// The game's routes run in its world update (Server.OnGame); these run on its main thread through
	// Main.QueueMainThreadAction, which the title screen runs too.
	public static class Menu
	{
		static string OnMain(Func<string> work, int timeoutMs = 10000)
		{
			var done = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
			Main.QueueMainThreadAction(() =>
			{
				try { done.TrySetResult(work()); }
				catch (Exception e) { done.TrySetResult(Json.Error(e.GetType().Name + ": " + e.Message)); }
			});
			return done.Task.Wait(timeoutMs) ? done.Task.Result : Json.Error("the game's main thread did not take the job");
		}

		public static string Leave() => OnMain(() =>
		{
			if (Main.gameMenu) return Json.Of(new Dictionary<string, object> { ["menu"] = true });
			string world = Main.worldName;
			WorldGen.SaveAndQuit();
			return Json.Of(new Dictionary<string, object> { ["leaving"] = world });
		});

		public static string Reload() => OnMain(() =>
		{
			if (!Main.gameMenu) return Json.Error("leave the world first (POST /menu)");
			var m = typeof(ModLoader).GetMethod("Reload", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
			if (m == null) return Json.Error("this tModLoader has no ModLoader.Reload");
			m.Invoke(null, null);
			return Json.Of(new Dictionary<string, object> { ["reloading"] = true });
		});

		public static string Enter(string body) => OnMain(() =>
		{
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			string player = root.TryGetProperty("player", out var p) ? p.GetString() : null;
			string world = root.TryGetProperty("world", out var w) ? w.GetString() : null;
			if (!Main.gameMenu) return Json.Error("already in a world: " + Main.worldName);
			Main.LoadPlayers();
			Main.LoadWorlds();
			var pd = Main.PlayerList.Find(d => d.Name == player);
			var wd = Main.WorldList.Find(d => d.Name == world);
			if (pd == null || wd == null)
				return Json.Error("no " + (pd == null ? "player " + player : "world " + world) + "; players: " +
					string.Join(", ", Main.PlayerList.ConvertAll(d => d.Name)) + "; worlds: " +
					string.Join(", ", Main.WorldList.ConvertAll(d => d.Name)));
			Main.SelectPlayer(pd);
			wd.SetAsActive();
			Main.menuMode = 10;
			WorldGen.playWorld();
			return Json.Of(new Dictionary<string, object> { ["entering"] = wd.Name, ["player"] = pd.Name });
		}, 20000);
	}
}
