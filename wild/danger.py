"""A map of the dangerous creatures we have seen, kept in danger.json, so walkers detour round them.

A creature that attacks (`attacks: true`: every one but deer and hare) falls on anyone within rules.AGGRO_RANGE tiles. Every read of
a character's surroundings (`around.things`) feeds observe(): what it shows is remembered, and a remembered creature
that is no longer where the view now covers is forgotten at once (it wandered off). What nobody looks at fades after
TTL. clear() / strong_near() say whether a walk or a target is too close to a known strong creature; route.py reads the
same memory, so the walkers and the way-into-the-tribe check always agree.
"""
import json
import os
import threading
import time

from . import api, rules

FILE = os.path.join(api.HERE, "danger.json")
TTL = 2 * 3600       # seconds a sighting is trusted
MIN_LEVEL = 8        # only creatures from this level up are worth remembering
MARGIN = rules.AGGRO_RANGE + 1   # keep this many tiles from a strong creature (rules.AGGRO_RANGE: they strike within it)
OVER = 4             # strong = this many levels above the traveller
TOTEM = rules.TRIBE_TOTEM
_lock = threading.Lock()
_seen = {}           # creature id -> {x, y, level, kind, t}
_bans = []           # places where somebody fell: {x, y, t}, avoided for BAN_SECS
BAN_SECS = 6 * 3600
BAN_RADIUS = 5
DENSE_RADIUS = 9     # this many tiles round a point ...
DENSE_COUNT = 3      # ... holding this many strong creatures make it a no-go area
_dirty = [0]
_saved = [0.0]       # when danger.json was last written


def _load():
    try:
        with open(FILE, encoding="utf-8") as f:
            data = json.load(f)
        _bans.extend(data.pop("_bans", []))
        _seen.update({int(k): v for k, v in data.items()})
    except (FileNotFoundError, ValueError):
        pass


_load()


def _save():
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(dict(_seen, _bans=_bans), f)


def record_death(m):
    """Death site banning is completely removed per user request (2026-11-06)."""
    pass


def banned(x, y):
    return False


def dense(x, y, level):
    """Is this a place with many strong creatures (DENSE_COUNT within DENSE_RADIUS)?"""
    if max(abs(x - TOTEM[0]), abs(y - TOTEM[1])) <= rules.TRIBE_LAND:
        return False
    now = time.time()
    n = sum(1 for v in list(_seen.values())
            if now - v["t"] <= TTL and v["level"] > level + OVER and max(abs(v["x"] - x), abs(v["y"] - y)) <= DENSE_RADIUS)
    return n >= DENSE_COUNT


def bad_zone(x, y, level):
    """A place to stay out of: a crowd of strong creatures (never the tribe's own land)."""
    if max(abs(x - TOTEM[0]), abs(y - TOTEM[1])) <= rules.TRIBE_LAND:
        return False
    return dense(x, y, level)


def _area(around):
    """(x0, y0, x1, y1) the view covers, or None."""
    tl, rows = (around or {}).get("top_left"), (around or {}).get("map")
    if not tl or not rows:
        return None
    return tl[0], tl[1], tl[0] + len(rows[0]) - 1, tl[1] + len(rows) - 1


def observe(m, around):
    """Remember the attacking creatures of level MIN_LEVEL+ in a character's view; forget the remembered ones the view
    covers but no longer shows, and the ones nobody has seen for TTL."""
    now = time.time()
    with _lock:
        shown = set()
        for t in (around or {}).get("things") or []:
            if t.get("type") == "creature":
                shown.add(t["id"])
                if t.get("attacks") and t.get("level", 0) >= MIN_LEVEL:
                    _seen[t["id"]] = {"x": t["x"], "y": t["y"], "level": t["level"], "kind": t["kind"], "t": now}
                    _dirty[0] += 1
        area = _area(around)
        for k, v in list(_seen.items()):
            gone = area and k not in shown and area[0] <= v["x"] <= area[2] and area[1] <= v["y"] <= area[3]
            if gone or now - v["t"] > TTL:
                del _seen[k]
                _dirty[0] += 1
        if _dirty[0] and now - _saved[0] >= 30:   # (`wild.py status` reads the file: keep it at most 30 s old)
            _dirty[0] = 0
            _saved[0] = now
            _save()


def strong_near(x, y, level, margin=MARGIN):
    """Known creatures that out-level `level` by OVER and stand within `margin` tiles of (x, y)."""
    if max(abs(x - TOTEM[0]), abs(y - TOTEM[1])) <= rules.TRIBE_LAND:
        return []  # creatures do not attack on the tribe's own land
    now = time.time()
    return [v for v in list(_seen.values())
            if now - v["t"] <= TTL and v["level"] > level + OVER and max(abs(v["x"] - x), abs(v["y"] - y)) <= margin]


def gap(x, y, level):
    """Tiles from (x, y) to the nearest known strong creature (99 if none)."""
    now = time.time()
    d = [max(abs(v["x"] - x), abs(v["y"] - y)) for v in list(_seen.values())
         if now - v["t"] <= TTL and v["level"] > level + OVER]
    return min(d) if d else 99


def clear(m, target, margin=MARGIN):
    """True if the straight walk from the character to `target` keeps its distance from the known strong creatures."""
    x0, y0, (x1, y1) = m["x"], m["y"], target
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(0, min(steps, 30) + 1, 2):  # one act walks at most 30 tiles: only that stretch matters now
        x, y = round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps)
        if strong_near(x, y, m["level"], margin) or bad_zone(x, y, m["level"]):
            return False
    return not (strong_near(x1, y1, m["level"], margin) or bad_zone(x1, y1, m["level"]))


def detour(m, target, ok=None):
    """A clear stop round the strong creatures on the way to `target`: the candidate (a ring of points round the
    target, wider and wider) that is clear to reach and leaves us nearest the target. None if nothing is clear."""
    x1, y1 = target
    best = None
    for r in (5, 8, 12, 16, 20):
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, -r), (r, -r), (-r, r),
                       (r, r // 2), (-r, r // 2), (r, -r // 2), (-r, -r // 2), (r // 2, r), (-r // 2, r), (r // 2, -r), (-r // 2, -r)):
            cand = (x1 + dx, y1 + dy)
            if ok and not ok(*cand):   # water, a building, a tile the server refused
                continue
            if clear(m, cand) and not strong_near(cand[0], cand[1], m["level"]) and not bad_zone(cand[0], cand[1], m["level"]):
                d = max(abs(cand[0] - m["x"]), abs(cand[1] - m["y"]))
                if d >= 3 and (best is None or r < best[0]):
                    best = (r, cand)
        if best:
            return best[1]
    return None


def summary():
    now = time.time()
    return sorted(((v["level"], v["kind"], v["x"], v["y"], int((now - v["t"]) / 60)) for v in _seen.values()), reverse=True)

