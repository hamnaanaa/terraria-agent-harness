using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using Microsoft.Xna.Framework.Graphics;
using Terraria;
using Terraria.GameContent;

namespace AgentIO
{
	// The game's own pictures, for the replay page: a tile's, a wall's or an enemy's sprite sheet as the game loads it,
	// as a PNG. GET /texture?tile=N, ?wall=N or ?npc=N. Read on the game's thread; nothing in the game changes.
	public static class Tex
	{
		public static string Png(NameValueCollection q)
		{
			Texture2D tex = null;
			int frames = 1;
			if (q["tile"] != null)
			{
				int i = int.Parse(q["tile"]);
				Main.instance.LoadTiles(i);
				tex = TextureAssets.Tile[i].Value;
			}
			else if (q["wall"] != null)
			{
				int i = int.Parse(q["wall"]);
				Main.instance.LoadWall(i);
				tex = TextureAssets.Wall[i].Value;
			}
			else if (q["npc"] != null)
			{
				int i = int.Parse(q["npc"]);
				Main.instance.LoadNPC(i);
				tex = TextureAssets.Npc[i].Value;
				frames = Main.npcFrameCount[i];
			}
			if (tex == null) return Json.Error("no texture: ask for tile=N, wall=N or npc=N");
			using var ms = new MemoryStream();
			tex.SaveAsPng(ms, tex.Width, tex.Height);
			return Json.Of(new Dictionary<string, object>
			{
				["w"] = tex.Width, ["h"] = tex.Height, ["frames"] = frames, ["png"] = Convert.ToBase64String(ms.ToArray()),
			});
		}
	}
}
