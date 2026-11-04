using System.Collections.Generic;
using System.Text.Json;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Terraria;
using Terraria.ModLoader;

namespace AgentIO
{
	// Drawings over the world for watching the agent think: half-transparent cells, paths and short labels, in layers
	// that the agent replaces whole. They are pictures only: no tile, no collision, nothing the body can touch.
	//
	// POST /overlay {"layer": "options", "cells": [[x, y, "#3fa9f580"]], "boxes": [[x, y, w, h, "#ffffff"]],
	//                "lines": [{"points": [[px, py], ...], "color": "#e8470ccc", "width": 3}],
	//                "labels": [{"at": [px, py], "text": "A  5.3 s", "color": "#ffffff", "scale": 0.8}]}
	// {"layer": "options", "clear": true} takes a layer away; {"clear_all": true} takes them all.
	public class Overlay : ModSystem
	{
		sealed class Layer
		{
			public List<(int x, int y, Color c)> Cells = new();
			public List<(int x, int y, int w, int h, Color c)> Boxes = new();
			public List<(Vector2[] pts, Color c, float width)> Lines = new();
			public List<(Vector2 at, string text, Color c, float scale, bool screen)> Labels = new();   // screen: fixed to the screen, not the world
		}

		static volatile Dictionary<string, Layer> _layers = new();
		static readonly object _lock = new();
		static Texture2D _pixel;

		public static string Set(string body)
		{
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var root = doc.RootElement;
			lock (_lock)
			{
				var next = new Dictionary<string, Layer>(_layers);
				if (root.TryGetProperty("clear_all", out var ca) && ca.GetBoolean()) next.Clear();
				string name = root.TryGetProperty("layer", out var ln) ? ln.GetString() : "default";
				if (root.TryGetProperty("clear", out var cl) && cl.GetBoolean()) next.Remove(name);
				else if (!root.TryGetProperty("clear_all", out _))
				{
					var layer = new Layer();
					if (root.TryGetProperty("cells", out var cells))
						foreach (var c in cells.EnumerateArray())
							layer.Cells.Add((c[0].GetInt32(), c[1].GetInt32(), Hex(c[2].GetString())));
					if (root.TryGetProperty("boxes", out var boxes))
						foreach (var b in boxes.EnumerateArray())
							layer.Boxes.Add((b[0].GetInt32(), b[1].GetInt32(), b[2].GetInt32(), b[3].GetInt32(), Hex(b[4].GetString())));
					if (root.TryGetProperty("lines", out var lines))
						foreach (var l in lines.EnumerateArray())
						{
							var pts = new List<Vector2>();
							foreach (var p in l.GetProperty("points").EnumerateArray()) pts.Add(new Vector2(p[0].GetSingle(), p[1].GetSingle()));
							float w = l.TryGetProperty("width", out var lw) ? lw.GetSingle() : 2f;
							layer.Lines.Add((pts.ToArray(), Hex(l.GetProperty("color").GetString()), w));
						}
					if (root.TryGetProperty("labels", out var labels))
						foreach (var t in labels.EnumerateArray())
						{
							var at = t.GetProperty("at");
							float sc = t.TryGetProperty("scale", out var s) ? s.GetSingle() : 0.8f;
							string col = t.TryGetProperty("color", out var tc) ? tc.GetString() : "#ffffffff";
							bool screen = t.TryGetProperty("screen", out var scr) && scr.GetBoolean();
							layer.Labels.Add((new Vector2(at[0].GetSingle(), at[1].GetSingle()), t.GetProperty("text").GetString(), Hex(col), sc, screen));
						}
					next[name] = layer;
				}
				_layers = next;
				return Json.Of(new Dictionary<string, object> { ["layers"] = new List<string>(next.Keys) });
			}
		}

		// "#rrggbb" or "#rrggbbaa"
		static Color Hex(string s)
		{
			s = (s ?? "#ffffff").TrimStart('#');
			byte P(int i) => System.Convert.ToByte(s.Substring(i, 2), 16);
			var c = new Color(P(0), P(2), P(4), s.Length >= 8 ? P(6) : (byte)255);
			return c * (c.A / 255f);                     // premultiplied, as the sprite batch blends
		}

		public override void PostDrawTiles()
		{
			var layers = _layers;
			if (layers.Count == 0) return;
			var sb = Main.spriteBatch;
			if (_pixel == null)
			{
				_pixel = new Texture2D(sb.GraphicsDevice, 1, 1);
				_pixel.SetData(new[] { Color.White });
			}
			sb.Begin(SpriteSortMode.Deferred, BlendState.AlphaBlend, SamplerState.LinearClamp, DepthStencilState.None,
				RasterizerState.CullNone, null, Main.GameViewMatrix.TransformationMatrix);
			var sp = Main.screenPosition;
			foreach (var layer in layers.Values)
			{
				foreach (var (x, y, c) in layer.Cells)
					sb.Draw(_pixel, new Rectangle((int)(x * 16 - sp.X), (int)(y * 16 - sp.Y), 16, 16), c);
				foreach (var (x, y, w, h, c) in layer.Boxes)
				{
					int sx = (int)(x * 16 - sp.X), sy = (int)(y * 16 - sp.Y), pw = w * 16, ph = h * 16;
					sb.Draw(_pixel, new Rectangle(sx, sy, pw, 2), c);
					sb.Draw(_pixel, new Rectangle(sx, sy + ph - 2, pw, 2), c);
					sb.Draw(_pixel, new Rectangle(sx, sy, 2, ph), c);
					sb.Draw(_pixel, new Rectangle(sx + pw - 2, sy, 2, ph), c);
				}
				foreach (var (pts, c, width) in layer.Lines)
					for (int i = 1; i < pts.Length; i++)
					{
						var a = pts[i - 1] - sp;
						var d = pts[i] - pts[i - 1];
						float len = d.Length();
						if (len < 0.01f) continue;
						sb.Draw(_pixel, a, null, c, (float)System.Math.Atan2(d.Y, d.X), new Vector2(0f, 0.5f),
							new Vector2(len, width), SpriteEffects.None, 0f);
					}
				foreach (var (at, text, c, scale, screen) in layer.Labels)
					Utils.DrawBorderString(sb, text, screen ? at : at - sp, c, scale);   // Screen labels stay fixed as the camera moves.
			}
			sb.End();
		}
	}
}
