"""Walking and danger-aware access to HALL via the forest waypoint.

The totem, land, training ground and waypoint are defined in rules; route.CHAINS holds the approach.
"""
import time

from . import api, care, danger, rules

TOTEM = rules.TRIBE_TOTEM
TRIBE_SPOT = rules.TRIBE_SPOT
TRAINING = rules.TRIBE_TRAINING
TRIBE_LAND = rules.TRIBE_LAND
BLOCKING = "~^BT"             # map tiles nobody walks on: water, mountain, buildings
WALLED_SECS = 3600            # a tile the server refused stays out of the detours this long
_walled = {}                  # (x, y) -> when the server answered "no way there" for it


def open_waypoints(m):
    return {w["ring"] for w in m.get("waypoints") or [] if w.get("open")}


def can_reach_tribe(m):
    """A walker needs the forest waypoint used by the current route."""
    return "forest" in open_waypoints(m)


def dist(m, p):
    return max(abs(p[0] - m["x"]), abs(p[1] - m["y"]))


def at_tribe(m):
    return dist(m, TOTEM) <= TRIBE_LAND


def walkable(around, x, y):
    """False for a tile the server refused lately, or one the view shows as water, mountain or a building."""
    if time.time() - _walled.get((x, y), 0) < WALLED_SECS:
        return False
    tl, rows = (around or {}).get("top_left"), (around or {}).get("map")
    if tl and rows and 0 <= y - tl[1] < len(rows) and 0 <= x - tl[0] < len(rows[0]):
        return rows[y - tl[1]][x - tl[0]] not in BLOCKING
    return True


def _move(unit, m, target):
    """Move toward target. A near tile the server cannot reach is remembered (detours skip it); when the server names
    the nearest tile it can reach and that one is nearer the target than we stand, the hero goes there instead."""
    code, r = api.act(unit, {"action": "move", "to": list(target)})
    if code == 400 and "no way there" in str(r.get("error", "")):
        if dist(m, target) <= 12:   # far off, "no way" may only mean "too far for one move"
            _walled[tuple(target)] = time.time()
        near = tuple(r.get("closest") or ())
        if near and dist(m, near) >= 2 and max(abs(near[0] - target[0]), abs(near[1] - target[1])) < dist(m, target):
            code, r = api.act(unit, {"action": "move", "to": list(near)})
    return code, r


def hop(unit, m, final, stops, around=None, force=False):
    """One step toward final: the farthest stop of the way still ahead (stops after the one we stand at) that the server
    accepts. A stop the straight walk to which passes a known strong creature is replaced by a detour or a small shift
    round it (on walkable ground); with none, return 0 (wait: they wander)."""
    if stops:
        cur = min(range(len(stops)), key=lambda i: dist(m, stops[i]))
        ahead = stops[cur + 1 if dist(m, stops[cur]) <= 2 else cur:]
    else:
        ahead = []
    ok = lambda x, y: walkable(around, x, y)
    for stop in [final] + ahead[::-1]:
        target = stop
        if not force and not danger.clear(m, stop):
            target = danger.detour(m, stop, ok) or sneak(m, stop, ok)
            if target is None:
                api.warning("[%s] strong creature on the way to %s: waiting" % (unit, stop))
                return 0
            api.info("[%s] round a strong creature: via %s" % (unit, target))
        code, _ = _move(unit, m, target)
        if code != 400:
            return code
    return 400


def sneak(m, stop, ok=None):
    """No wide detour: a small shift of the stop (up to 4 tiles each way) that keeps just outside the strong creatures' strike
    range (rules.AGGRO_RANGE) all the way there, as near to the stop as can be."""
    best = None
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            cand = (stop[0] + dx, stop[1] + dy)
            if cand == tuple(stop) or (ok and not ok(*cand)) or danger.gap(cand[0], cand[1], m["level"]) <= rules.AGGRO_RANGE:
                continue
            if _path_gap(m, cand) > rules.AGGRO_RANGE:
                d = max(abs(dx), abs(dy))
                if best is None or d < best[0]:
                    best = (d, cand)
    return best[1] if best else None


def _path_gap(m, target):
    """Smallest distance to a strong creature along the straight walk to target."""
    x0, y0, (x1, y1) = m["x"], m["y"], target
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    return min(danger.gap(round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps), m["level"])
               for i in range(0, min(steps, 30) + 1))


def step_away(unit, m, final, around=None):
    """A strong creature is close: slip a few tiles sideways (never back to the camp) to the walkable spot that is farthest
    from it and, among those, nearest the way on. False if every spot is no better than where we stand."""
    here = danger.gap(m["x"], m["y"], m["level"])
    best = None
    for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3), (3, 3), (-3, -3), (3, -3), (-3, 3), (2, 0), (-2, 0), (0, 2), (0, -2)):
        cand = (m["x"] + dx, m["y"] + dy)
        g = danger.gap(cand[0], cand[1], m["level"])
        if g <= here or g <= rules.AGGRO_RANGE or not walkable(around, *cand):
            continue
        key = (-min(g, 8), max(abs(final[0] - cand[0]), abs(final[1] - cand[1])))
        if best is None or key < best[0]:
            best = (key, cand)
    if best is None:
        return False
    api.warning("[%s] a strong creature is close: slipping aside to %s" % (unit, best[1]))
    _move(unit, m, best[1])
    return True


def pick_waypoint(m):
    """The open waypoint whose way into the tribe land has the most clearance (route.json)."""
    from . import route
    ok = [r for r in ("forest",) if r in open_waypoints(m)]
    return route.best_entry(ok)[0].split("-")[0] if ok else "forest"


class RouteClosed(Exception):
    """The way to the tribe stayed closed longer than the caller was willing to wait (or another order came)."""


class Bailed(Exception):
    """A hard push toward the tribe took too much damage: the hero went back to the camp."""


def _fall_report(tag, m, around):
    near = sorted((max(abs(t["x"] - m["x"]), abs(t["y"] - m["y"])), t.get("kind"), t.get("level"), t["x"], t["y"])
                  for t in (around or {}).get("things") or [] if t.get("type") == "creature" and t.get("attacks"))
    api.warning("[%s] fell at (%s,%s) on the road; creatures within 14 tiles: %s" % (
        tag, m["x"], m["y"], ", ".join("%s L%s at (%s,%s) %d tiles" % (k, lv, x, y, d) for d, k, lv, x, y in near if d <= 14)
        or "none in view"))


def walk(unit, final, tol=2, stops=(), label=None, min_hp=0, patience=None, force=False, bail=None, until=None,
         land_ok=False):
    """Walk `unit` to within `tol` tiles of `final` (with `land_ok`: anywhere on the tribe's land will do); from far away:
    home, a waypoint, then hops along `stops`. A walk into the tribe land starts at the camp only while route.json says
    a way is safe; `patience`: seconds to wait there for that before RouteClosed (None: for ever). `force` (the owner's
    forced order): straight through the strong creatures. `until`: a callable; when it says so (another order for the
    hero) the walk gives way with RouteClosed."""
    from . import route
    stops = list(stops or [])
    tag = label or unit
    stuck = waited = 0
    fell = False
    while True:
        if until and until():
            raise RouteClosed(tag)
        m, a = api.me(unit)
        if not m:
            time.sleep(15)
            continue
        danger.observe(m, a)
        if m["hp"] <= 0 or m.get("down_until"):
            if not fell:   # once per fall (it lies there a while)
                fell = True
                danger.record_death(m)
                route.record("fell", unit, m.get("ring"))
                _fall_report(tag, m, a)
            time.sleep(60)
            continue
        fell = False
        if bail and m["hp"] < m["max_hp"] * bail and not at_tribe(m):   # a push: hurt on the road, back to the camp
            api.warning("[%s] hurt on the road (hp %s/%s): back to the camp" % (tag, m["hp"], m["max_hp"]))
            api.act(unit, {"action": "home"})
            raise Bailed(tag)
        if dist(m, final) <= tol or (land_ok and at_tribe(m)):
            if land_ok:
                route.record("arrived", unit, m.get("ring"))
            return m
        api.wait_busy(m)
        if care.eat(unit, m):
            continue
        if m["hp"] < m["max_hp"] * min_hp or m["ap"] < 9:
            time.sleep(25)
            continue
        if dist(m, final) < 45 or (m["ring"] == "highlands" and dist(m, final) < 70 and m["x"] > 190):
            # on the way: hop. A strong creature in the way that cannot be gone round: if it is near us, slip aside;
            # otherwise wait where we stand (every read refreshes the danger memory, so one that wandered off frees the
            # way) and after five waits in a row go through
            if hop(unit, m, final, stops, a, force=force or stuck >= 5) == 0:
                if danger.strong_near(m["x"], m["y"], m["level"], rules.AGGRO_RANGE + 1) and step_away(unit, m, final, a):
                    stuck = 0
                    time.sleep(8)
                else:
                    stuck += 1
                    time.sleep(30)
            else:
                stuck = 0
        elif m["ring"] != "camp":
            api.act(unit, {"action": "home"})
        else:
            if land_ok and not force and not route.safe(
                    [r for r in ("highlands", "forest") if r in open_waypoints(m)] or ("forest",)):
                if patience is not None and waited >= patience:
                    api.warning("[%s] the way to the tribe stayed closed: giving up" % tag)
                    raise RouteClosed(tag)
                waited += 60
                time.sleep(60)
                continue
            api.act(unit, {"action": "travel", "to": pick_waypoint(m)})
        api.debug("[%s] walking at (%s,%s) %s ap %s" % (tag, m["x"], m["y"], m["ring"], m["ap"]))
        time.sleep(3)


def walk_to_tribe(unit, min_hp=0, patience=None, force=False, bail=None, until=None):
    """Into the land by the way with the most clearance from the strong creatures (route.py: the south-west door or the
    north-east corner; a L28 bear at the door killed 927, 929 and 933, zombies at the east side killed 931). With every
    way guarded (clearance under route.SAFE) the hero waits where it stands (RouteClosed), unless the walk is forced."""
    from . import route
    m, _ = api.me(unit)
    rings = [r for r in ("forest",) if r in open_waypoints(m or {})] or ["forest"]
    name, a = route.best_entry(rings)
    if a["clearance"] < route.SAFE and not force:
        api.warning("[%s] every way into the land is guarded (best: %s, %s tiles): waiting" % (unit, name, a["clearance"]))
        raise RouteClosed(unit)
    chain = route.CHAINS[name]
    api.info("[%s] into the land by the %s way (clearance %s)" % (unit, name, a["clearance"]))
    return walk(unit, chain[-1], 2, stops=chain[1:-1], min_hp=min_hp, patience=patience, force=force, bail=bail,
                until=until, land_ok=True)


def scout(unit, ring="forest", deep=False):
    """Look, do not fight: travel to the ring's waypoint, let the danger memory take what the view shows (creatures that
    wandered off are forgotten), then go home. False if the hero is too weak or short of points to try."""
    from . import route
    m, a = api.me(unit)
    if not m or m["hp"] < m["max_hp"] * 0.6 or m["ap"] < 25 or ring not in open_waypoints(m):
        return False
    if m["ring"] != "camp":
        api.act(unit, {"action": "home"})
        time.sleep(3)
    api.act(unit, {"action": "travel", "to": ring})
    time.sleep(3)
    for step in range(7):
        m, a = api.me(unit)
        if m:
            api.wait_busy(m)
            danger.observe(m, a)
            api.debug("[%s] scouting at (%s,%s) %s hp %s ap %s" % (unit, m["x"], m["y"], m["ring"], m["hp"], m["ap"]))
            if m["hp"] < m["max_hp"] * 0.8 or danger.strong_near(m["x"], m["y"], m["level"]):   # noticed, or one close: out
                break
            if step == 3 and deep:   # three tiles further in, to see what the waypoint's view misses
                api.act(unit, {"action": "move", "to": [m["x"] + 3, m["y"] + 1]})
        time.sleep(4)
    api.act(unit, {"action": "home"})
    route.record()
    return True
