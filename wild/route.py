"""The way into the tribe land, with its risks, recorded in route.json.

The current approach starts at the forest waypoint and follows the migration route to HALL.
Every few minutes the chains are checked against the strong creatures in the danger memory (the same memory the walkers
use: a sighting lasts until a view shows the spot empty, or danger.TTL passes): the clearance of a chain is
the smallest distance from any of its legs to a strong creature. A chain is safe with at least SAFE tiles of clearance
(rules.AGGRO_RANGE is the owner's measure of how far a strong creature strikes, plus one tile). The best entry, its clearance, the blockers and the
history of walks (arrived / fell) are written to route.json so the knowledge outlives restarts.
"""
import json
import threading
import time

from . import api, danger, nav, rules

FILE = "route.json"
SAFE = rules.AGGRO_RANGE + 1   # tiles of clearance a leg needs
MIN_LEVEL = 10           # creatures under this level never matter on this way
CHAINS = {
    "forest": [rules.TRIBE_WAYPOINT, (350, 259), (345, 262), nav.TRIBE_SPOT],
}
NAMES_ZH = {"forest": "森林传送点"}
_lock = threading.Lock()


def _dist_point_segment(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return max(abs(p[0] - ax), abs(p[1] - ay))
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / float(dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return max(abs(p[0] - cx), abs(p[1] - cy))


def threats():
    now = time.time()
    return [v for v in list(danger._seen.values()) if now - v["t"] <= danger.TTL and v["level"] >= MIN_LEVEL]


def assess(chain=None):
    """{entry: {"clearance": tiles, "blockers": [...]}} for the chains, from the creatures seen lately."""
    ts = threats()
    out = {}
    for name, stops in (chain or CHAINS).items():
        worst, blockers = 99.0, []
        for t in ts:
            d = min(_dist_point_segment((t["x"], t["y"]), a, b) for a, b in zip(stops, stops[1:]))
            if d < SAFE:
                blockers.append({"kind": t["kind"], "level": t["level"], "at": [t["x"], t["y"]], "dist": round(d, 1)})
            worst = min(worst, d)
        out[name] = {"clearance": round(worst, 1), "blockers": sorted(blockers, key=lambda b: b["dist"])}
    return out


def best_entry(open_entries=("highlands", "forest")):
    """(chain, assessment): of the chains starting at an open waypoint, the one with the most clearance."""
    a = assess()
    names = [n for n in a if n.split("-")[0] in open_entries] or list(a)
    best = max(names, key=lambda n: a[n]["clearance"])
    return best, a[best]


def safe(open_entries=("highlands", "forest")):
    return best_entry(open_entries)[1]["clearance"] >= SAFE


def _load():
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def record(outcome=None, unit=None, entry=None):
    """Write the current assessment; with `outcome` ("arrived" / "fell") also append it to the walk history."""
    with _lock:
        data = _load()
        data["chains"] = {k: [list(p) for p in v] for k, v in CHAINS.items()}
        data["safe_clearance"] = SAFE
        data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["assessment"] = assess()
        data["best"] = best_entry()[0]
        data["safe_now"] = safe()
        if outcome:
            hist = data.setdefault("history", [])
            hist.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "unit": unit, "entry": entry, "outcome": outcome})
            del hist[:-60]
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return data


def watch(every=300):
    """Job: keep route.json current and say when the way opens or closes."""
    last = None
    while True:
        try:
            d = record()
            if d["safe_now"] != last:
                last = d["safe_now"]
                api.debug("[route] way to the tribe is now %s (best: %s, clearance %s)" % (
                    "SAFE" if last else "closed", d["best"], d["assessment"][d["best"]]["clearance"]))
        except Exception as e:
            api.error("[route] error %r" % e)
        time.sleep(every)
