"""Gathering raw materials for the tribe (the totem's next level needs wood, stone and ore in the store). One hero, one
resource: `gather resource=...` walks to the nearest node by itself, so each round first looks at where that node is and
waits when a strong creature stands near it. Health below HP_MIN waits at the camp; the way home (8 points) is kept.
The bag is emptied at the tribe by the usual visit (`gather` order: deposit_all)."""
import datetime
import json
import os
import threading
import time

from . import abyss, ambush, api, care, danger, nav, rules, state

QUOTA = {"wood": 80, "stone": 80, "ore": 60}   # what one hero collects before it stops (an order may give its own number)
HP_MIN = 0.35         # below this fraction of health the hero rests at the camp first
AP_MIN = 20           # the swings and the way home (8) need this much
SAFE_RINGS = ("camp", "meadow", "forest")   # where the heroes gather; the lord also goes to the highlands
LORD = rules.LORD
SWINGS = 3            # swings a call (3 points and 20 seconds each)


GUARD_RANGE = 5   # a creature this close to a node, at our level or an elite, is a guard: its node is skipped


def _guarded(view, m, x, y):
    return [t for t in (view or {}).get("things") or [] if t.get("type") == "creature" and t.get("attacks")
            and max(abs(t["x"] - x), abs(t["y"] - y)) <= GUARD_RANGE and (t.get("level", 0) >= m["level"] - 1 or t.get("elite"))]


NEAR = 4              # tiles: a beatable monster this close to the node (or the hero) is killed first
KILL_FLOOR = 0.2      # a monster is hunted when the model leaves the hero over this share of its health (the owner: 20%)


RING_NAMES = ["camp", "meadow", "forest", "highlands", "ashlands", "ruins"]
RING_RADII = [30, 72, 128, 180, 224, 252]   # the 512 world's edges; worldmap.json is the authority (tools/worldmap.py fetches it)
RING_RANK = {"camp": 0, "meadow": 0, "forest": 1, "highlands": 2, "ashlands": 3, "ruins": 4}
NODE_CHAR = {"wood": "t", "stone": "s"}   # the node grid of worldmap.json (the copper veins lie in the highlands only)
_meadow = {}
_world = {}           # size, centre and ring edges of worldmap.json (the world grew to 512 tiles on 2026-11-05)
MEADOW_FAIL = {}      # (unit, resource) -> until: the meadow had no standing node left in view: the forest is allowed meanwhile


def world():
    """(size, centre, ring edges) of worldmap.json, read once; the 512 defaults when the file is missing or old."""
    if not _world:
        size, rings = 512, list(RING_RADII)
        try:
            with open(os.path.join(api.HERE, "worldmap.json"), encoding="utf-8") as f:
                d = json.load(f)
            size = int(d.get("size") or size)
            rings = [int(r) for r in (d.get("rings") or rings)]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        _world.update(size=size, centre=(size // 2, size // 2), rings=rings)
    return _world


def ring_of(x, y):
    w = world()
    cx, cy = w["centre"]
    r = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    for edge, name in zip(w["rings"], RING_NAMES):
        if r <= edge:
            return name
    return "ruins"


def meadow_nodes(resource):
    """Every spot of the meadow where this resource's node can stand (worldmap.json); `things` says which still stand."""
    if resource not in _meadow:
        out = []
        try:
            with open(os.path.join(api.HERE, "worldmap.json"), encoding="utf-8") as f:
                grid = json.load(f)["nodes"]
            size = world()["size"]
            ch = NODE_CHAR.get(resource)
            for i, c in enumerate(grid):
                if c == ch:
                    x, y = i % size, i // size
                    if ring_of(x, y) == "meadow":
                        out.append((x, y))
        except (OSError, ValueError, KeyError):
            pass
        _meadow[resource] = out
    return _meadow[resource]


def pick_node(view, m, resource, unit=None):
    """The best node of `resource`: the lowest ring first (the owner: the meadow first), the nearest within it. In view (`things`),
    else the one the `farther` list names ({x, y, dist, ring})."""
    seen = sorted((t for t in (view or {}).get("things") or [] if t.get("type") == "node" and t.get("gives") == resource),
                  key=lambda t: (RING_RANK.get(ring_of(t["x"], t["y"]), 9), t.get("dist", 99)))
    if seen:
        t = seen[0]
        return {"x": t["x"], "y": t["y"], "dist": t.get("dist", 0), "ring": ring_of(t["x"], t["y"])}
    far = ((view or {}).get("farther") or {}).get(resource)
    return dict(far) if far else None


def monsters_near(view, x, y, within=NEAR):
    return [t for t in (view or {}).get("things") or [] if t.get("type") == "creature" and t.get("attacks")
            and max(abs(t["x"] - x), abs(t["y"] - y)) <= within]


def kill(unit, m, weak):
    """Walk up to each beatable monster and fight it (ambush.fight_back: aborts under 20% health). True if still on its feet."""
    for t in sorted(weak, key=lambda t: t.get("hp", 0)):
        api.act(unit, {"action": "move", "to": [t["x"] - 1, t["y"]], "until_there": True})
        fresh, _v = api.me(unit)
        if not fresh:
            continue
        api.wait_busy(fresh)
        if not ambush.fight_back(unit, fresh, [t]):
            return False
    return True


def _log(unit, text, level="DEBUG"):
    state.note(unit, text)
    api.log(level, "[harvest %s] %s" % (api.who(unit), text))


def harvest(unit, resource, quota=None, chain=True, until=None):
    """Gather `resource` until the bag holds `quota` of it (True), or another order for the hero is waiting (True)."""
    from . import queue
    quota = int(quota or QUOTA.get(resource, 60))
    told = [0.0]

    def say(text, level="DEBUG"):   # the same complaint at most every 5 minutes
        if time.time() - told[0] > 300:
            told[0] = time.time()
            _log(unit, text, level)
    while True:
        if queue.waiting(unit):
            return True
        if until and time.time() > until:   # the caller wants to choose again (the node may be guarded for good)
            return False
        m, view = api.me(unit)
        if not m:
            time.sleep(30)
            continue
        state.update(unit, m, next((n for n, sq in rules.SQUADS.items() if unit in sq["members"]), ""))   # (the dashboard)
        if care.has_heal_items(m) and 0 < m["hp"] < rules.HEAL_BELOW * m["max_hp"]:
            care.heal_with_items(unit, m)   # the main character and the lord carry bread: it heals them in the field (no points)
        care.claim_chest(unit, m)   # the daily chest, when the main character happens to be in the camp after 12:00 (UTC+8)
        if m["hp"] <= 0:
            say("is down at (%s,%s): waiting for the revival" % (m["x"], m["y"]), "WARNING")
            time.sleep(60)
            continue
        if view:
            danger.observe(m, view)
            # (the owner, 2026-11-04) a real ambush is fought if the model says it is won; otherwise the work goes on (the blacklist
            # of nodes was deleted: it did not work)
            if m["ring"] != "camp" and m["ap"] >= 8:
                ambush.handle(unit, m, view, say=lambda t: _log(unit, t), flee=False)
        have = (m.get("bag") or {}).get(resource) or 0
        if have >= quota:
            _log(unit, "has %d %s: done" % (have, resource), level="INFO")
            if resource == "wood" and chain:   # (927 and 933 went hunting hurt at once and 927 fell): stone is the slow one, go on with it
                resource, quota = "stone", QUOTA["stone"]
                told[0] = 0.0
                continue
            return True
        if m["hp"] < HP_MIN * m["max_hp"]:   # (the copper trip: 930 came back with 6 hp, 932 with 28)
            if m["ring"] != "camp" and m["ap"] >= 8:
                api.act(unit, {"action": "home", "why": "health %d/%d is under %d%%: rests at the camp" % (m["hp"], m["max_hp"], HP_MIN * 100)})
            _log(unit, "resting at the camp (hp %d/%d, under %d%%)" % (m["hp"], m["max_hp"], HP_MIN * 100))
            time.sleep(60)
            continue
        if m["ring"] in ("highlands", "ashlands", "ruins") and resource != "ore" and not nav.at_tribe(m) and m["ap"] >= 8:
            # (925, 927 and 932 stood in the highlands: `gather` walks to the nearest node wherever it is) only copper is worth that
            say("in the %s with a %s errand: home" % (m["ring"], resource))
            api.act(unit, {"action": "home", "why": "stands in the %s with a %s errand (only copper is worth it there)" % (m["ring"], resource)})
            time.sleep(30)
            continue
        if nav.dist(m, nav.TOTEM) <= 30 and m["ring"] != "camp" and not nav.at_tribe(m):
            # (927 woke at the totem and walked to the ore node beside the tribe: a zombie L35 and a polar L28 stand there)
            if m["ap"] >= 8:
                api.act(unit, {"action": "home", "why": "within 30 tiles of the tribe's guarded ground, outside it"})
            say("near the tribe's guarded ground: home first")
            time.sleep(30)
            continue
        if nav.at_tribe(m) and m["ap"] >= 8:   # on the tribe land: the nodes around it are guarded too, start from the camp
            api.act(unit, {"action": "home", "why": "on the tribe land: gathering starts from the camp"})
            continue
        if m["ap"] < AP_MIN:
            time.sleep(60)
            continue
        node = pick_node(view, m, resource, unit)
        if resource in NODE_CHAR and (node is None or RING_RANK.get(node.get("ring"), 9) > 0) and m["ring"] not in ("highlands", "ashlands", "ruins"):
            # the meadow first (the owner): no meadow node stands in view: walk to the nearest meadow spot of this resource; once
            # there and still nothing stands in view, the meadow is spent for now and the forest is allowed for 10 minutes
            if MEADOW_FAIL.get((unit, resource), 0) < time.time():
                if m["ring"] == "meadow" and ring_of(m["x"], m["y"]) == "meadow" and not any(
                        t.get("type") == "node" and t.get("gives") == resource for t in (view or {}).get("things") or []):
                    MEADOW_FAIL[(unit, resource)] = time.time() + 600
                    _log(unit, "no %s node stands in the meadow round here: the forest is allowed for 10 min" % resource)
                else:
                    spots = meadow_nodes(resource)
                    if spots:
                        mx, my = min(spots, key=lambda p: max(abs(p[0] - m["x"]), abs(p[1] - m["y"])))
                        if max(abs(mx - m["x"]), abs(my - m["y"])) > 3:
                            c0, r0 = api.act(unit, {"action": "move", "to": [mx, my], "until_there": True})
                            if c0 == 400 and r0.get("closest"):
                                c0, r0 = api.act(unit, {"action": "move", "to": r0["closest"], "until_there": True})
                            _log(unit, "heads for the meadow's %s spot (%s,%s) first: %s %s" % (resource, mx, my, c0, "" if c0 == 200 else str(r0.get("error"))[:70]))
                            time.sleep(5 if c0 == 200 else 30)
                            continue
                        MEADOW_FAIL[(unit, resource)] = time.time() + 600   # standing at the spot, nothing grows: give the meadow up for a while
        # (the owner, 2026-11-05): ?????????????????
        # ???????????????????? 19-32 ???? 21 ??????????????????????
        if node and unit != LORD and resource != "ore" and node.get("ring") not in SAFE_RINGS:   # (copper ore lies in the highlands only: ordered)
            # (930 walked to a highlands stone node and fell there with 0 points: only the lord goes that far out)
            say("the nearest %s node (%s,%s) lies in the %s: not for this hero, going home" % (
                resource, node["x"], node["y"], node.get("ring")))
            if m["ring"] not in ("camp", "meadow", "forest") and m["ap"] >= 8:
                api.act(unit, {"action": "home", "why": "the %s node lies in the %s, not for this hero" % (resource, node.get("ring"))})   # (934 waited in the highlands)
            time.sleep(120)
            continue
        if node and m["ap"] < 1.4 * node.get("dist", 0) + AP_MIN:   # the walk there, the swings and the way home
            say("%d points are too few for the %s node %s tiles away: resting" % (m["ap"], resource, node.get("dist")))
            time.sleep(60)
            continue
        if node and node.get("dist", 0) > 1:
            # steer to OUR node, whatever ring it lies in: `gather` goes to the nearest node of the kind, and the lord, standing beside
            # iron veins in the ashlands, got iron ore instead of the copper node 10 tiles away (the owner: find the real copper)
            code0, r0 = api.act(unit, {"action": "move", "to": [node["x"], node["y"]], "until_there": True})
            if code0 == 400 and r0.get("closest"):   # out of one walk: the nearest tile the search reaches
                code0, r0 = api.act(unit, {"action": "move", "to": r0["closest"], "until_there": True})
            _log(unit, "walks to the %s node (%s,%s), %s tiles away: %s %s" % (resource, node["x"], node["y"], node.get("dist"), code0, "" if code0 == 200 else str(r0.get("error"))[:80]))
            m3, _ = api.me(unit)
            api.wait_busy(m3)
            time.sleep(2)
            continue
        if node:
            state.target(unit, node["x"], node["y"], resource)
        if node and node.get("dist", 99) <= 1:   # standing at our node: that very node (not "the nearest of the kind")
            code, r = api.act(unit, {"action": "gather", "at": [node["x"], node["y"]], "times": SWINGS})
            if code == 400 and "at" in str(r.get("error", "")).lower():   # not accepted: the old way
                code, r = api.act(unit, {"action": "gather", "resource": resource, "times": SWINGS})
        else:
            code, r = api.act(unit, {"action": "gather", "resource": resource, "times": SWINGS})
        wt = r.get("walking_to") if isinstance(r, dict) else None
        if wt:
            state.target(unit, wt[0], wt[1], resource)
        _log_level = "INFO" if code == 200 else "DEBUG"
        _log(unit, "%s (have %d/%d): %s %s" % (resource, have, quota, code, str(api.clean(r))[:200]), level=_log_level)
        if code == 400 and r.get("closest"):   # "no way there within 120 points": walk to the nearest tile the search reaches, then again
            code2, r2 = api.act(unit, {"action": "move", "to": r["closest"], "until_there": True})
            _log(unit, "the %s node is out of one walk: moving on toward it to %s: %s %s" % (resource, r["closest"], code2, "" if code2 == 200 else r2.get("error")))
            time.sleep(5 if code2 == 200 else 60)
            continue
        if code != 200:
            time.sleep(30)
            continue
        m2, _ = api.me(unit)
        api.wait_busy(m2)

# ===== `harvest wood|stone|ore`: the hero's own material, for good (kept in queue.json, back after a restart) ==================
# (the owner, 2026-11-04: no "total needs" and no automatic switching: each hero gathers exactly what its order names; the order
# ends only when another order is queued for the hero.) The main character's copper goes to the lord's copper node.
LOAD = 999                  # one round: this much in the bag (999 = never "full")
WINDOW = (datetime.time(11, 20), datetime.time(12, 15))   # UTC: the ladder evening (19:20-20:15 UTC+8): the heroes stand by
CAMP = (256, 256)           # the middle of the 512 world (worldmap.json: size // 2)
ORE_NODE = {"xy": (182, 366)}   # the owner's copper point (2026-11-04) carried over to the 512 world (x2): a copper cluster in
                                # the highlands, the nearest veins at (182,367)/(187,364)/(177,370), all ten heroes meet there


def paused():
    t = datetime.datetime.utcnow().time()
    return abyss.PAUSE.is_set() or WINDOW[0] <= t <= WINDOW[1]


TOOL = {"wood": "hatchet", "stone": "pickaxe", "ore": "pickaxe"}   # a hatchet gives more wood; ore (and stone) need a pickaxe


def ensure_tool(unit, m, res):
    """The right tool for the job in the tool slot and in one piece: broken tools yield nothing (925, 927, 933 gathered with
    broken ones; the main character mined stone with a hoe while a pickaxe lay in its bag). Returns True if it changed anything."""
    want = TOOL.get(res)
    if not want:
        return False
    worn = (m.get("worn") or {}).get("tool") or {}
    top = worn.get("durability_max") or 0
    if worn.get("base") == want:
        if worn.get("broken"):   # (the owner: a tool is repaired only once it is broken)
            code, r = care.repair(unit, worn["id"])
            _log(unit, "repairs its %s (%s/%s): %s %s" % (want, worn.get("durability"), top, code, r))
            return code == 200
        return False
    bag = [g for g in (m.get("gear_bag") or []) if g.get("slot") == "tool" and g.get("base") == want and g.get("level", 1) <= m["level"] + 2]
    if not bag:
        return False
    best = max(bag, key=lambda g: (not g.get("broken"), g.get("level", 1), g.get("durability", 0)))
    if best.get("broken"):
        code, r = care.repair(unit, best["id"])
        _log(unit, "repairs a %s from the bag (%s/%s): %s %s" % (want, best.get("durability"), best.get("durability_max"), code, r))
        if code != 200 and best.get("broken"):
            return False
    code, r = api.act(unit, {"action": "equip", "item": best["id"]})
    _log(unit, "wears a %s L%s for %s: %s" % (want, best.get("level"), res, code))
    return code == 200


PICK_BUYERS = (929, 933, 934)          # the owner (2026-11-04): the heroes without a pickaxe buy one, nobody else
PICK_LISTINGS = [3097, 3282, 3511]     # market listings: pickaxes at 5, 5 and 8 coins (the owner: "only 5, 5, 8")
_pick_lock = threading.Lock()


def has_pickaxe(m):
    return ((m.get("worn") or {}).get("tool") or {}).get("base") == "pickaxe" or any(
        g.get("slot") == "tool" and g.get("base") == "pickaxe" for g in m.get("gear_bag") or [])


def get_pickaxe(unit, m):
    """A copper miner without a pickaxe buys one of the three listed (market sales work only in the camp: it goes home first)."""
    if m["ring"] != "camp":
        if m["ap"] >= 8:
            api.act(unit, {"action": "home", "why": "has no pickaxe: copper needs one, it is bought at the camp's market"})
        return True
    with _pick_lock:
        for lid in list(PICK_LISTINGS):
            code, r = api.act(unit, {"action": "buy", "listing": lid, "qty": 1})
            _log(unit, "buys pickaxe listing %s: %s %s" % (lid, code, "" if code == 200 else str(r.get("error"))[:90]))
            if code == 200:
                PICK_LISTINGS.remove(lid)
                return True
            if code in (400, 404, 409):   # gone or not affordable: next one
                if "coin" not in str(r.get("error", "")).lower():
                    PICK_LISTINGS.remove(lid)
        return False


def run(unit, own):
    """`harvest <material>`: gather `own` round after round (a round lasts at most 25 minutes), standing by during the ladder
    evening; True when another order waits for the hero."""
    from . import queue
    while True:
        try:
            if queue.waiting(unit):
                return True
            if paused():
                state.note(unit, "待命(天梯/深渊时段)")
                time.sleep(60)
                continue
            m, view = api.me(unit)
            if not m or m["hp"] <= 0:
                time.sleep(60)
                continue
            state.update(unit, m, next((n for n, sq in rules.SQUADS.items() if unit in sq["members"]), ""))
            care.claim_chest(unit, m)
            if own == "ore" and unit in PICK_BUYERS and not has_pickaxe(m) and PICK_LISTINGS:
                get_pickaxe(unit, m)
                time.sleep(25)
                continue
            if care.has_heal_items(m) and 0 < m["hp"] < rules.HEAL_BELOW * m["max_hp"]:
                care.heal_with_items(unit, m)   # bread first, before any walk
            if own == "ore" and ORE_NODE.get("xy"):   # every copper miner meets at the owner's point

                tx, ty = ORE_NODE["xy"]   # (not while it has given that spot up)
                far = max(abs(m["x"] - tx), abs(m["y"] - ty))
                camp_far = max(abs(CAMP[0] - tx), abs(CAMP[1] - ty))
                if far > camp_far + 20 and m["ring"] != "camp" and m["ap"] >= 8:
                    # `home` costs 8 points whatever the distance: far from the copper, the way via the camp is shorter in points
                    api.act(unit, {"action": "home", "why": "the copper node (%s,%s) is %d tiles away, %d from the camp: home (8 points) first" % (tx, ty, far, camp_far)})
                    time.sleep(25)
                    continue
                if far > 3:
                    state.target(unit, tx, ty, "集合点铜矿")
                    code, r = api.act(unit, {"action": "move", "to": [tx, ty], "until_there": True})
                    if code == 400 and r.get("closest"):
                        code, r = api.act(unit, {"action": "move", "to": r["closest"], "until_there": True})
                    state.note(unit, "去集合点铜矿 (%s,%s): %s %s" % (tx, ty, code, "" if code == 200 else str(r.get("error"))[:60]))
                    time.sleep(20 if code == 200 else 90)
                    continue
            if ensure_tool(unit, m, own):   # the right, whole tool before any swing
                m, view = api.me(unit)
                if not m:
                    time.sleep(20)
                    continue
            have = (m.get("bag") or {}).get(own, 0)
            if own != "ore":
                state.clear_target(unit)   # (a stale copper target stayed on the board after the order changed)
            state.job(unit, own)
            state.note(unit, "采集 %s" % own)
            harvest(unit, own, quota=have + LOAD, chain=False, until=time.time() + 25 * 60)
        except Exception as e:
            _log(unit, "round error %r" % e)
            time.sleep(60)
