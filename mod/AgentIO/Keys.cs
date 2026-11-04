using System.Text.Json;
using Terraria;
using Terraria.ModLoader;

namespace AgentIO
{
	// The keys. A press is the controls of one tick: the arrows, jump, use, the hotbar slot and where the cursor points.
	// They are written over the player's own controls in SetControls, the place the game reads them from; with no press
	// the person at the keyboard plays as usual.
	public class Keys : ModPlayer
	{
		public sealed class Press
		{
			public bool Left, Right, Up, Down, Jump, Use, Interact, Hook;
			public int Slot = -1;
			public float AimX = float.NaN, AimY = float.NaN;    // world px
			public bool Fresh = true;                            // not yet applied: a new press
			public bool HoldJump;                                // the step before held jump too: keep it down, no release
		}

		static volatile Press _held;
		static ulong _heldUntil;
		public static void Release() => _held = null;
		bool _useDown;      // the use key as pressed last tick: an item without autoReuse fires only on a fresh press
		bool _actDown;      // the interact key as pressed last tick
		bool _hookDown;     // the grapple key as pressed last tick
		bool _jumpDown;     // the jump key as pressed last tick: a new press with jump lets it up for a tick first, or the
		                    // game, which jumps once per press, never jumps again (the walks, Oct 18 night). Inside a
		                    // script, a step that follows a step with jump keeps it down (HoldJump): the game ends a jump
		                    // the tick the key is up, and the hand's aim changes split a hold into steps (Oct 19 night)

		// POST /keys {"keys": ["right", "jump"], "aim_cell": [x, y], "slot": 1, "ticks": 30}: held for that many ticks
		// (30 when not said), or until the next /keys. {"keys": []} lets go
		public static string Hold(string body)
		{
			using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
			var press = Parse(doc.RootElement);
			int ticks = doc.RootElement.TryGetProperty("ticks", out var t) ? t.GetInt32() : 30;
			_held = press;
			_heldUntil = Main.GameUpdateCount + (ulong)System.Math.Max(1, ticks);
			return Json.Of(new System.Collections.Generic.Dictionary<string, object> { ["held_until"] = _heldUntil });
		}

		public static Press Parse(JsonElement e)
		{
			var p = new Press();
			if (e.TryGetProperty("keys", out var keys))
				foreach (var k in keys.EnumerateArray())
					switch (k.GetString())
					{
						case "left": p.Left = true; break;
						case "right": p.Right = true; break;
						case "up": p.Up = true; break;
						case "down": p.Down = true; break;
						case "jump": p.Jump = true; break;
						case "use": p.Use = true; break;
						case "interact": p.Interact = true; break;    // the right click: doors, chests, town folk
						case "hook": p.Hook = true; break;            // the grapple key: the first hook in the bag flies at the aim
						default: throw new System.ArgumentException("no key " + k.GetString() + " (left, right, up, down, jump, use, interact, hook)");
					}
			if (e.TryGetProperty("slot", out var s)) p.Slot = s.GetInt32();
			if (e.TryGetProperty("aim_cell", out var c))
			{
				p.AimX = c[0].GetInt32() * 16f + 8f;
				p.AimY = c[1].GetInt32() * 16f + 8f;
			}
			if (e.TryGetProperty("aim", out var a))
			{
				p.AimX = a[0].GetSingle();
				p.AimY = a[1].GetSingle();
			}
			return p;
		}

		public override void SetControls()
		{
			if (Player.whoAmI != Main.myPlayer) return;
			var press = Script.Current ?? (_held != null && Main.GameUpdateCount <= _heldUntil ? _held : null);
			if (press == null) return;
			Player.controlLeft = press.Left;
			Player.controlRight = press.Right;
			Player.controlUp = press.Up;
			Player.controlDown = press.Down;
			bool jump = press.Jump;
			if (press.Fresh && jump && _jumpDown && !press.HoldJump) jump = false;   // a release for a tick, for a new jump
			press.Fresh = false;
			Player.controlJump = jump;
			_jumpDown = jump;
			// a switch of the hotbar slot waits for the hand to be free, and the use key waits for the switch: with the
			// key held the old item refired on the very tick the hand freed, and a potion asked for after the bow was
			// never drunk (Oct 18 night)
			bool switching = press.Slot >= 0 && press.Slot < 10 && Player.selectedItem != press.Slot;
			if (switching && Player.itemAnimation == 0) { Player.selectedItem = press.Slot; switching = false; }
			// «use» held: an item with autoReuse (a pickaxe, a sword in 1.4.4) fires on its own; one without (a bow, a
			// potion, a block) needs the key up for a tick between uses, so the key is pressed on alternate ticks
			bool use = press.Use && !switching;
			if (use && !Player.HeldItem.autoReuse && _useDown) use = false;
			Player.controlUseItem = use;
			_useDown = use;
			// interact fires on a fresh press too: pressed on alternate ticks
			bool act = press.Interact && !_actDown;
			Player.controlUseTile = act;
			_actDown = act;
			// the grapple key fires on a fresh press too (a held one would fire hook after hook)
			bool hook = press.Hook && !_hookDown;
			Player.controlHook = hook;
			_hookDown = press.Hook;
			if (press.Interact)
			{
				Player.delayUseItem = false;
				Player.mouseInterface = false;
			}
			if (!float.IsNaN(press.AimX))
			{
				Main.mouseX = (int)(press.AimX - Main.screenPosition.X);
				Main.mouseY = (int)(press.AimY - Main.screenPosition.Y);
				Main.SmartCursorWanted_Mouse = false;    // it would move the target to a cell of its own choosing
			}
			if (press.Use)
			{
				Player.delayUseItem = false;            // the cursor over the bag or a button would swallow the use
				Player.mouseInterface = false;
			}
		}

		public override void PostUpdate()
		{
			if (Player.whoAmI == Main.myPlayer) Script.AfterTick(Player);
		}
	}
}
