using Terraria.ModLoader;

namespace AgentIO
{
	// The agent's way into the game, over HTTP on 127.0.0.1:17900: the state out (State.cs), the keys in (Keys.cs), a
	// tick-exact runner for tests (Script.cs) and the test gym (Gym.cs). Nothing in this mod acts by itself: the body
	// moves only on keys it is sent, and the world changes only on the gym's calls.
	public class AgentIO : Mod
	{
	}
}
