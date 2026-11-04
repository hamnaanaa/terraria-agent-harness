using System.Collections.Generic;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.ModLoader;

namespace AgentIO
{
	// The engine's own melee hitbox, tick by tick. The game computes a swinging item's rectangle each tick of its
	// animation (Player.ItemCheck_GetMeleeHitbox) and tModLoader hands it to UseItemHitbox; recorded here, relative to
	// the body's top-left, with the animation tick and the facing. POST /swing {"slot": 0} starts a recording for the
	// item in that slot (the loop then presses use, aiming right, then left); GET /swing stops it and gives the ticks.
	public class SwingRecorder : GlobalItem
	{
		public static bool Recording;
		public static readonly List<int[]> Ticks = new();   // itemAnimation, x, y, w, h, direction, noHitbox

		public override void UseItemHitbox(Item item, Player player, ref Rectangle hitbox, ref bool noHitbox)
		{
			if (!Recording || player.whoAmI != Main.myPlayer) return;
			Ticks.Add(new[] { player.itemAnimation, hitbox.X - (int)player.position.X, hitbox.Y - (int)player.position.Y,
				hitbox.Width, hitbox.Height, player.direction, noHitbox ? 1 : 0 });
		}

		public static string Handle(string body)
		{
			var p = Main.LocalPlayer;
			if (p == null || !p.active) return Json.Error("no player in a world");
			if (!string.IsNullOrWhiteSpace(body) && body.Contains("slot"))
			{
				using var doc = System.Text.Json.JsonDocument.Parse(body);
				int slot = doc.RootElement.GetProperty("slot").GetInt32();
				Ticks.Clear();
				Recording = true;
				return Json.Of(new Dictionary<string, object> { ["recording"] = p.inventory[slot].Name, ["use_anim"] = p.inventory[slot].useAnimation });
			}
			Recording = false;
			return Json.Of(new Dictionary<string, object> { ["ticks"] = Ticks });
		}
	}
}
