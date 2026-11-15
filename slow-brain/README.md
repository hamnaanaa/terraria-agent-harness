# Slow brain

Reserved for a planner that turns longer-term objectives into measurable live-brain goals. The Gym and the live brain run independently of a planner.

Each response will contain a short ordered `goals` list using the live brain's existing goal format. The caller sends one goal at a time and advances after observed success. Failure, timeout or death discards the remaining chain and requests a fresh plan with the result and current state. While waiting, the live brain keeps its survival goal active. The current attended playthrough uses this format with manually chosen chains.
