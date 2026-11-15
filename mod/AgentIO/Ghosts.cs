using System;
using System.Collections.Generic;
using System.Text.Json;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Terraria;
using Terraria.DataStructures;
using Terraria.GameContent;
using Terraria.ModLoader;

namespace AgentIO
{
	// Ghosts, for filming: the character's body recorded tick by tick (with the weapon in use, its shots and the enemies
	// around it), then played back as see-through copies of the same character, many at once, each tinted its own colour
	// with a trail. Pictures only: a ghost, its arrows and its enemies touch nothing.
	// The camera can be pinned, zoomed and eased, the interface, the cursor and the real body hidden, the rain stopped
	// and the scene lit, so runs filmed one by one line up in one clean shot.
	//
	// POST /ghost {"record": "start"}   from the next tick, the body is kept: place, facing, animation frames, the item
	//                                   in use, the shots it owns and the enemies within 60 cells
	// POST /ghost {"record": "stop"}    answers {"ticks": n, "samples": [[x, y, dir, body, legs, head, dead, item,
	//                                   anim, anim_max, rot, item_dx, item_dy, shots, foes, look], ...]}; look: what the
	//                                   body wears as the game draws it [head, body, legs, wings, wing_frame, shoe,
	//                                   handon, handoff, back, front, balloon, waist, neck, face, shield], so ghosts
	//                                   recorded in different gear are drawn each in its own
	// POST /ghost {"play": [{"samples": [...], "color": "#ff5a5a", "alpha": 0.6, "delay": 0}, ...],
	//              "speed": 1, "trail": 120}       plays them all from now; a ghost holds its last pose at the end
	// POST /ghost {"clear": true}       takes the ghosts away
	// POST /camera {"cell": [x, y]} or {"center": [px, py]} pins the camera's centre; {"follow": true} lets it go;
	//              "zoom": 1..2; "ease": 0..1; "hide_ui", "hide_body", "hide_cursor", "clear_sky": bool;
	//              "map_style": 0 hidden, 1 minimap, 2 overlay; keeps terrain discovery enabled
	//              "sun": 0..1; "fullbright": 0..1.5; "tint": 0..1 (how much of its colour a ghost wears)
	public class Ghosts : ModSystem
	{
		sealed class Track
		{
			public float[] X, Y, ItemRot, ItemDX, ItemDY;
			public int[] Dir, Body, Legs, Head, ItemType, ItemAnim, ItemMax;
			public bool[] Dead;
			public List<float[]>[] Shots, Foes;
			public float[][] Look;
			public Color Color;
			public float Alpha, Delay;
			public int N => X.Length;
		}

		sealed class Play
		{
			public List<Track> Tracks = new();
			public uint Start;
			public float Speed = 1f;
			public int Trail = 120;
		}

		static List<object[]> _recording;         // the game's thread only
		static volatile Play _play;
		static Vector2? _center, _now;
		static float _zoom;
		static Texture2D _pixel;
		static readonly Dictionary<int, Item> _items = new();

		public static bool HideBody, HideCursor, ClearSky;
		public static bool Drawing;               // true while a ghost is drawn, so HideBody spares it
		public static float Ease, Sun, FullBright;
		public static Color Tint;                 // the ghost being drawn: its colour, mixed into its picture by TintShare
		public static float TintShare = 0.55f;
		static float? _cursorScale;

		public static string Handle(string body)
		{
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			if (root.TryGetProperty("record", out var rec))
			{
				if (rec.GetString() == "start")
				{
					_recording = new List<object[]>();
					return Json.Of(new Dictionary<string, object> { ["recording"] = true, ["tick"] = Main.GameUpdateCount });
				}
				var samples = _recording ?? new List<object[]>();
				_recording = null;
				return Json.Of(new Dictionary<string, object> { ["ticks"] = samples.Count, ["samples"] = samples });
			}
			if (root.TryGetProperty("clear", out var cl) && cl.GetBoolean())
			{
				_play = null;
				return Json.Of(new Dictionary<string, object> { ["ghosts"] = 0 });
			}
			if (root.TryGetProperty("play", out var list))
			{
				var play = new Play
				{
					Speed = root.TryGetProperty("speed", out var sp) ? sp.GetSingle() : 1f,
					Trail = root.TryGetProperty("trail", out var tr) ? tr.GetInt32() : 120,
				};
				foreach (var g in list.EnumerateArray())
				{
					var rows = new List<JsonElement>();
					foreach (var r in g.GetProperty("samples").EnumerateArray()) rows.Add(r);
					if (rows.Count == 0) continue;
					int n = rows.Count;
					var t = new Track
					{
						X = new float[n], Y = new float[n], Dir = new int[n], Body = new int[n], Legs = new int[n],
						Head = new int[n], Dead = new bool[n], ItemType = new int[n], ItemAnim = new int[n],
						ItemMax = new int[n], ItemRot = new float[n], ItemDX = new float[n], ItemDY = new float[n],
						Shots = new List<float[]>[n], Foes = new List<float[]>[n], Look = new float[n][],
						Color = Hex(g.TryGetProperty("color", out var c) ? c.GetString() : "#ffffff"),
						Alpha = g.TryGetProperty("alpha", out var a) ? a.GetSingle() : 0.6f,
						Delay = g.TryGetProperty("delay", out var d) ? d.GetSingle() : 0f,
					};
					for (int i = 0; i < n; i++)
					{
						var r = rows[i];
						int len = r.GetArrayLength();
						t.X[i] = r[0].GetSingle(); t.Y[i] = r[1].GetSingle();
						t.Dir[i] = (int)r[2].GetSingle(); t.Body[i] = (int)r[3].GetSingle();
						t.Legs[i] = (int)r[4].GetSingle(); t.Head[i] = (int)r[5].GetSingle();
						t.Dead[i] = len > 6 && r[6].GetSingle() > 0f;
						if (len > 12)
						{
							t.ItemType[i] = (int)r[7].GetSingle(); t.ItemAnim[i] = (int)r[8].GetSingle();
							t.ItemMax[i] = (int)r[9].GetSingle(); t.ItemRot[i] = r[10].GetSingle();
							t.ItemDX[i] = r[11].GetSingle(); t.ItemDY[i] = r[12].GetSingle();
						}
						if (len > 13) t.Shots[i] = Rows(r[13]);
						if (len > 14) t.Foes[i] = Rows(r[14]);
						if (len > 15 && r[15].ValueKind == JsonValueKind.Array && r[15].GetArrayLength() >= 15)
						{
							var look = new float[r[15].GetArrayLength()];
							for (int k = 0; k < look.Length; k++) look[k] = r[15][k].GetSingle();
							t.Look[i] = look;
						}
					}
					play.Tracks.Add(t);
				}
				play.Start = Main.GameUpdateCount;
				_play = play;
				return Json.Of(new Dictionary<string, object> { ["ghosts"] = play.Tracks.Count, ["tick"] = play.Start });
			}
			return Json.Error("say record (start or stop), play or clear");
		}

		static List<float[]> Rows(JsonElement e)
		{
			if (e.ValueKind != JsonValueKind.Array || e.GetArrayLength() == 0) return null;
			var list = new List<float[]>();
			foreach (var row in e.EnumerateArray())
			{
				var v = new float[row.GetArrayLength()];
				for (int k = 0; k < v.Length; k++) v[k] = row[k].GetSingle();
				list.Add(v);
			}
			return list;
		}

		public static string Camera(string body)
		{
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			if (root.TryGetProperty("follow", out var f) && f.GetBoolean()) _center = null;
			if (root.TryGetProperty("cell", out var cell))
				_center = new Vector2(cell[0].GetSingle() * 16f + 8f, cell[1].GetSingle() * 16f + 8f);
			if (root.TryGetProperty("center", out var cen))
				_center = new Vector2(cen[0].GetSingle(), cen[1].GetSingle());
			if (root.TryGetProperty("ease", out var e)) Ease = MathHelper.Clamp(e.GetSingle(), 0f, 1f);
			if (root.TryGetProperty("zoom", out var z)) { _zoom = MathHelper.Clamp(z.GetSingle(), 1f, 2f); if (Ease <= 0f) Main.GameZoomTarget = _zoom; }
			if (root.TryGetProperty("tint", out var ti)) TintShare = MathHelper.Clamp(ti.GetSingle(), 0f, 1f);
			if (root.TryGetProperty("hide_ui", out var ui)) Main.hideUI = ui.GetBoolean();
			if (root.TryGetProperty("map_style", out var ms))
			{
				Main.mapEnabled = true;
				Main.mapStyle = Math.Clamp(ms.GetInt32(), 0, 2);
			}
			if (root.TryGetProperty("hide_body", out var hb)) HideBody = hb.GetBoolean();
			if (root.TryGetProperty("hide_cursor", out var hc))
			{
				HideCursor = hc.GetBoolean();
				if (!HideCursor && _cursorScale.HasValue) { Main.cursorScale = _cursorScale.Value; _cursorScale = null; }
			}
			if (root.TryGetProperty("clear_sky", out var cs)) { ClearSky = cs.GetBoolean(); if (ClearSky) Clear(); }
			if (root.TryGetProperty("sun", out var sun)) Sun = MathHelper.Clamp(sun.GetSingle(), 0f, 1f);
			if (root.TryGetProperty("fullbright", out var fb)) FullBright = MathHelper.Clamp(fb.GetSingle(), 0f, 1.5f);
			return Json.Of(new Dictionary<string, object>
			{
				["pinned"] = _center.HasValue, ["zoom"] = Main.GameZoomTarget, ["hide_ui"] = Main.hideUI,
				["hide_body"] = HideBody, ["hide_cursor"] = HideCursor, ["clear_sky"] = ClearSky, ["sun"] = Sun,
				["fullbright"] = FullBright, ["raining"] = Main.raining, ["screen"] = new[] { Main.screenWidth, Main.screenHeight },
				["map_enabled"] = Main.mapEnabled, ["map_style"] = Main.mapStyle,
			});
		}

		static void Clear()
		{
			if (Main.raining) Main.StopRain();
			Main.raining = false; Main.maxRaining = 0f; Main.rainTime = 0;
			Main.cloudAlpha = 0f; Main.cloudBGActive = 0f;
		}

		public override void ModifyScreenPosition()
		{
			if (_center is Vector2 c)
			{
				_now = _now.HasValue && Ease > 0f ? Vector2.Lerp(_now.Value, c, Ease) : c;
				Main.screenPosition = _now.Value - new Vector2(Main.screenWidth, Main.screenHeight) / 2f;
			}
			else _now = null;
			if (_zoom > 0f)
				Main.GameZoomTarget = Ease > 0f ? MathHelper.Lerp(Main.GameZoomTarget, _zoom, Ease) : _zoom;
		}

		public override void PostUpdateWorld()
		{
			if (ClearSky) Clear();
		}

		public override void PostUpdateEverything()
		{
			if (HideCursor)
			{
				if (!_cursorScale.HasValue) _cursorScale = Main.cursorScale;
				Main.cursorScale = 0f;
				Main.cursorColor = Color.Transparent;
			}
			if (FullBright <= 0f || Main.gameMenu) return;
			int x0 = (int)(Main.screenPosition.X / 16f) - 2, y0 = (int)(Main.screenPosition.Y / 16f) - 2;
			int x1 = x0 + Main.screenWidth / 16 + 5, y1 = y0 + Main.screenHeight / 16 + 5;
			for (int x = x0; x <= x1; x++)
				for (int y = y0; y <= y1; y++)
					if (WorldGen.InWorld(x, y, 2)) Lighting.AddLight(x, y, FullBright, FullBright, FullBright);
		}

		public override void ModifySunLightColor(ref Color tileColor, ref Color backgroundColor)
		{
			if (Sun <= 0f) return;
			tileColor = Color.Lerp(tileColor, Color.White, Sun);
			backgroundColor = Color.Lerp(backgroundColor, Color.White, Sun * 0.6f);
		}

		public override void PostUpdatePlayers()
		{
			if (_recording == null || Main.gameMenu) return;
			var p = Main.LocalPlayer;
			bool using_ = p.itemAnimation > 0 && p.HeldItem != null && !p.HeldItem.IsAir;
			var shots = new List<float[]>();
			foreach (var pr in Main.projectile)
				if (pr.active && pr.owner == Main.myPlayer)
					shots.Add(new[] { pr.type, (float)Math.Round(pr.Center.X, 1), (float)Math.Round(pr.Center.Y, 1),
						(float)Math.Round(pr.rotation, 3), pr.frame, pr.spriteDirection, pr.alpha });
			var foes = new List<float[]>();
			foreach (var n in Main.npc)
				if (n.active && !n.friendly && !n.townNPC && n.lifeMax > 5 && Vector2.Distance(n.Center, p.Center) < 960f)
					foes.Add(new[] { n.type, (float)Math.Round(n.position.X, 1), (float)Math.Round(n.position.Y, 1),
						n.frame.Y, n.spriteDirection, n.width, n.height, (float)Math.Round(n.rotation, 3), n.scale });
			_recording.Add(new object[]
			{
				(float)Math.Round(p.position.X, 1), (float)Math.Round(p.position.Y, 1), (float)p.direction,
				(float)p.bodyFrame.Y, (float)p.legFrame.Y, (float)p.headFrame.Y, p.dead ? 1f : 0f,
				using_ ? (float)p.HeldItem.type : 0f, (float)p.itemAnimation, (float)p.itemAnimationMax,
				(float)Math.Round(p.itemRotation, 3), (float)Math.Round(p.itemLocation.X - p.position.X, 1),
				(float)Math.Round(p.itemLocation.Y - p.position.Y, 1), shots, foes, LookOf(p),
			});
		}

		public override void PostDrawTiles()
		{
			var play = _play;
			if (play == null || play.Tracks.Count == 0 || Main.gameMenu) return;
			var sb = Main.spriteBatch;
			if (_pixel == null)
			{
				_pixel = new Texture2D(sb.GraphicsDevice, 1, 1);
				_pixel.SetData(new[] { Color.White });
			}
			var p = Main.LocalPlayer;
			float now = (Main.GameUpdateCount - play.Start) * play.Speed;
			var half = new Vector2(p.width / 2f, p.height / 2f);
			var sp = Main.screenPosition;

			// the trails, under everything: each ghost's last moments, fading towards their start
			sb.Begin(SpriteSortMode.Deferred, BlendState.AlphaBlend, SamplerState.LinearClamp, DepthStencilState.None,
				RasterizerState.CullNone, null, Main.GameViewMatrix.TransformationMatrix);
			foreach (var t in play.Tracks)
			{
				int i = Index(t, now);
				if (i < 1) continue;
				int from = Math.Max(1, i - play.Trail);
				for (int k = from; k <= i; k++)
				{
					if (t.Dead[k] || t.Dead[k - 1]) continue;
					var a = new Vector2(t.X[k - 1], t.Y[k - 1]) + half - sp;
					var b = new Vector2(t.X[k], t.Y[k]) + half - sp;
					var d = b - a;
					float len = d.Length();
					if (len < 0.01f || len > 160f) continue;          // a respawn or a teleport is not a path
					float fade = (k - from + 1f) / (i - from + 1f);
					sb.Draw(_pixel, a, null, t.Color * (0.85f * fade), (float)Math.Atan2(d.Y, d.X), new Vector2(0f, 0.5f),
						new Vector2(len, 3f), SpriteEffects.None, 0f);
				}
			}
			sb.End();

			// the enemies each ghost met and the arrows it shot, as they were at that tick, see-through
			sb.Begin(SpriteSortMode.Deferred, BlendState.AlphaBlend, Main.DefaultSamplerState, DepthStencilState.None,
				Main.Rasterizer, null, Main.GameViewMatrix.TransformationMatrix);
			foreach (var t in play.Tracks)
			{
				int i = Index(t, now);
				if (i < 0) continue;
				if (t.Foes[i] != null)
					foreach (var f in t.Foes[i]) DrawFoe(sb, f, t.Alpha * 0.8f);
				if (t.Shots[i] != null)
					foreach (var s in t.Shots[i]) DrawShot(sb, s, t.Alpha);
			}
			sb.End();

			// then the bodies: the real character's own picture, drawn where each ghost is, in that ghost's pose and
			// with the weapon it was using then
			int slot = p.selectedItem;
			var held = p.inventory[slot];
			var mouse = Main.mouseItem;
			var keep = (p.direction, p.bodyFrame, p.legFrame, p.headFrame, p.itemAnimation, p.itemAnimationMax, p.itemTime,
				p.itemRotation, p.itemLocation, p.immune, p.immuneAlpha);
			var look0 = LookOf(p);
			var cam = Main.Camera;
			sb.Begin(SpriteSortMode.Deferred, BlendState.AlphaBlend, cam.Sampler, DepthStencilState.None, cam.Rasterizer,
				null, cam.GameViewMatrix.TransformationMatrix);
			Drawing = true;
			try
			{
				Main.mouseItem = new Item();
				foreach (var t in play.Tracks)
				{
					int i = Index(t, now);
					if (i < 0 || t.Dead[i]) continue;
					var at = new Vector2(t.X[i], t.Y[i]);
					p.direction = t.Dir[i] >= 0 ? 1 : -1;
					p.bodyFrame.Y = t.Body[i]; p.legFrame.Y = t.Legs[i]; p.headFrame.Y = t.Head[i];
					p.immune = false; p.immuneAlpha = 0; p.itemTime = 0;
					if (t.ItemType[i] > 0 && t.ItemAnim[i] > 0)
					{
						p.inventory[slot] = ItemOf(t.ItemType[i]);
						p.itemAnimation = t.ItemAnim[i]; p.itemAnimationMax = Math.Max(1, t.ItemMax[i]);
						p.itemRotation = t.ItemRot[i]; p.itemLocation = at + new Vector2(t.ItemDX[i], t.ItemDY[i]);
					}
					else
					{
						p.inventory[slot] = held;
						p.itemAnimation = 0;
					}
					SetLook(p, t.Look[i] ?? look0);
					Tint = t.Color;
					Main.PlayerRenderer.DrawPlayer(cam, p, at, 0f, p.fullRotationOrigin, 1f - MathHelper.Clamp(t.Alpha, 0.05f, 1f), 1f);
				}
			}
			finally
			{
				Drawing = false;
				sb.End();
				SetLook(p, look0);
				p.inventory[slot] = held;
				Main.mouseItem = mouse;
				(p.direction, p.bodyFrame, p.legFrame, p.headFrame, p.itemAnimation, p.itemAnimationMax, p.itemTime,
					p.itemRotation, p.itemLocation, p.immune, p.immuneAlpha) = keep;
			}
		}

		static float[] LookOf(Player p) => new float[] { p.head, p.body, p.legs, p.wings, p.wingFrame, p.shoe, p.handon,
			p.handoff, p.back, p.front, p.balloon, p.waist, p.neck, p.face, p.shield };

		static void SetLook(Player p, float[] l)
		{
			p.head = (int)l[0]; p.body = (int)l[1]; p.legs = (int)l[2]; p.wings = (int)l[3]; p.wingFrame = (int)l[4];
			p.shoe = (int)l[5]; p.handon = (int)l[6]; p.handoff = (int)l[7]; p.back = (int)l[8]; p.front = (int)l[9];
			p.balloon = (int)l[10]; p.waist = (int)l[11]; p.neck = (int)l[12]; p.face = (int)l[13]; p.shield = (int)l[14];
		}

		static Item ItemOf(int type)
		{
			if (!_items.TryGetValue(type, out var it))
			{
				it = new Item();
				it.SetDefaults(type);
				_items[type] = it;
			}
			return it;
		}

		// an enemy: [type, x, y, frame_y, sprite_dir, width, height, rotation, scale], drawn the way the game draws it
		static void DrawFoe(SpriteBatch sb, float[] f, float alpha)
		{
			int type = (int)f[0];
			if (type <= 0 || type >= TextureAssets.Npc.Length) return;
			Main.instance.LoadNPC(type);
			var tex = TextureAssets.Npc[type].Value;
			int frames = Math.Max(1, Main.npcFrameCount[type]);
			int fh = tex.Height / frames;
			var src = new Rectangle(0, (int)f[3], tex.Width, fh);
			float scale = f.Length > 8 && f[8] > 0f ? f[8] : 1f;
			var center = new Vector2(f[1] + f[5] / 2f, f[2] + f[6] - fh * scale / 2f + 4f) - Main.screenPosition;
			var fx = f[4] == 1f ? SpriteEffects.FlipHorizontally : SpriteEffects.None;
			var light = Lighting.GetColor((int)((f[1] + f[5] / 2f) / 16f), (int)((f[2] + f[6] / 2f) / 16f));
			sb.Draw(tex, center, src, light * alpha, f.Length > 7 ? f[7] : 0f, new Vector2(tex.Width / 2f, fh / 2f), scale, fx, 0f);
		}

		// a shot: [type, center_x, center_y, rotation, frame, sprite_dir, alpha]
		static void DrawShot(SpriteBatch sb, float[] s, float alpha)
		{
			int type = (int)s[0];
			if (type <= 0 || type >= TextureAssets.Projectile.Length) return;
			Main.instance.LoadProjectile(type);
			var tex = TextureAssets.Projectile[type].Value;
			int frames = Math.Max(1, Main.projFrames[type]);
			int fh = tex.Height / frames;
			var src = new Rectangle(0, (int)s[4] * fh, tex.Width, fh);
			var fx = s.Length > 5 && s[5] == -1f ? SpriteEffects.FlipHorizontally : SpriteEffects.None;
			float own = s.Length > 6 ? (255f - s[6]) / 255f : 1f;
			sb.Draw(tex, new Vector2(s[1], s[2]) - Main.screenPosition, src, Color.White * (alpha * own), s[3],
				new Vector2(tex.Width / 2f, fh / 2f), 1f, fx, 0f);
		}

		static int Index(Track t, float now)
		{
			float at = now - t.Delay;
			if (at < 0f) return -1;
			return Math.Min((int)at, t.N - 1);
		}

		// "#rrggbb" or "#rrggbbaa", premultiplied as the sprite batch blends
		static Color Hex(string s)
		{
			s = (s ?? "#ffffff").TrimStart('#');
			byte P(int i) => Convert.ToByte(s.Substring(i, 2), 16);
			var c = new Color(P(0), P(2), P(4), s.Length >= 8 ? P(6) : (byte)255);
			return c * (c.A / 255f);
		}
	}

	// The real character left out of the picture while its ghosts play (HideBody), the ghosts still drawn; and each
	// ghost drawn in its own colour.
	public class GhostBody : ModPlayer
	{
		public override void HideDrawLayers(PlayerDrawSet drawInfo)
		{
			if (!Ghosts.HideBody || Ghosts.Drawing || drawInfo.drawPlayer.whoAmI != Main.myPlayer) return;
			foreach (var layer in PlayerDrawLayerLoader.Layers) layer.Hide();
		}

		public override void ModifyDrawInfo(ref PlayerDrawSet d)
		{
			if (!Ghosts.Drawing || Ghosts.TintShare <= 0f) return;
			d.colorHair = Mix(d.colorHair); d.colorHead = Mix(d.colorHead); d.colorEyes = Mix(d.colorEyes);
			d.colorEyeWhites = Mix(d.colorEyeWhites); d.colorBodySkin = Mix(d.colorBodySkin); d.colorLegs = Mix(d.colorLegs);
			d.colorShirt = Mix(d.colorShirt); d.colorUnderShirt = Mix(d.colorUnderShirt); d.colorPants = Mix(d.colorPants);
			d.colorShoes = Mix(d.colorShoes); d.colorArmorHead = Mix(d.colorArmorHead); d.colorArmorBody = Mix(d.colorArmorBody);
			d.colorArmorLegs = Mix(d.colorArmorLegs);
		}

		static Color Mix(Color c)
		{
			if (c.A == 0) return c;
			float a = c.A / 255f, s = Ghosts.TintShare;
			var t = Ghosts.Tint;
			byte M(byte own, byte tint) => (byte)MathHelper.Clamp((own / a * (1f - s) + tint * s) * a, 0f, 255f);
			return new Color(M(c.R, t.R), M(c.G, t.G), M(c.B, t.B), c.A);
		}
	}
}
