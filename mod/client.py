"""AgentIO's HTTP client: one address, JSON requests and state reads.

Shared by the live brain and the Gym runner.
"""
import json
import os
import urllib.request

# the mod's address; AGENTIO_URL sets another (the tests point it at a stand-in, so nothing in them reaches the game,
# Oct 31)
MOD = os.environ.get("AGENTIO_URL", "http://127.0.0.1:17900").rstrip("/")


def call(path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(MOD + path, data, {"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    if isinstance(out, dict) and out.get("error"):
        raise RuntimeError("%s: %s" % (path, out["error"]))
    return out


def state(**box):
    q = "&".join("%s=%d" % kv for kv in box.items())
    return call("/state" + ("?" + q if q else ""))


