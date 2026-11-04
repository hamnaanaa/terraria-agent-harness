"""The 12 regular Gym episodes, in walkthrough order."""
from combat.episodes import EPISODES as COMBAT
from items.episodes import EPISODES as ITEMS
from search.episodes import EPISODES as SEARCH
from survival.episodes import EPISODES as SURVIVAL
from travel.episodes import EPISODES as TRAVEL

EPISODES = {
    **TRAVEL,
    **{name: ITEMS[name] for name in ("ore", "place", "pickup")},
    **COMBAT,
    "craft": ITEMS["craft"],
    **SEARCH,
    **SURVIVAL,
}

from combat.extended import EPISODES as COMBAT_EXTENDED, WEAPON_EPISODES
from items.extended import EPISODES as ITEMS_EXTENDED
from travel.extended import EPISODES as TRAVEL_EXTENDED

EXTENDED = {
    **{("cave_tunnel" if name == "cave" else name): episode for name, episode in TRAVEL_EXTENDED.items()},
    **COMBAT_EXTENDED,
    **ITEMS_EXTENDED,
    **WEAPON_EPISODES,
}
