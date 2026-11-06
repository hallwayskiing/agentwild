"""The corrupted land (the owner, 2026-11-05).

A patch of one ring is corrupted every two hours for 20 minutes, then its guardian rises for 15. `me.corrupted_land` carries the
phase and `ends_at`, so the moment the next one opens can be computed: the land that is (or was just) there started at
`ends_at` minus its phase lengths, and the next one starts `period` later. The watcher sleeps until then and looks the moment
it opens (then every 20 seconds until it shows up); with no land seen yet it looks every `fallback` seconds.

When a land is open (or its guardian has risen), in the right ring and for the right levels (rules.CORRUPT_LAND), and the main
character and the lord are both hunting (no queue order or other job holds them), `STATE["on"]` goes up: the squad flagged
`land` in rules.SQUADS takes them over and hunts there, every other squad leaves them alone (hunt.py checks `STATE`). When
the land closes, or an order takes one of them, they go back to their own squads.
"""
import json
import os
import time

from . import api, lease, rules

STATE = {"on": False, "stage": False}   # on: the land squad is in charge of the main character and the lord; stage: they rest in the camp, a land of our kind is due
AREA = [102, 110, 12]      # x, y and reach (tiles) of the land being hunted
_seen = [None]            # ends_at of the last land reported as skipped
_next = [0.0]              # epoch of the predicted start of the next land (0: unknown)
_kind = {"start": 0.0, "target": False}   # start of the last land seen, and was it of our kind (rules.CORRUPT_LAND ring and levels)
_FILE = os.path.join(api.HERE, "land.json")   # the prediction survives a restart
try:
    with open(_FILE, encoding="utf-8") as _f:
        _d = json.load(_f)
        _next[0] = float(_d.get("next", 0))
        _kind["start"], _kind["target"] = float(_d.get("seen_start", 0)), bool(_d.get("seen_target"))
except (OSError, ValueError):
    pass


def _read():
    """me.corrupted_land ({} when there is none, None when the read failed)."""
    code, r = api.call("GET", "/world/me")
    if code != 200 or not isinstance(r.get("me"), dict):
        return None
    return r["me"].get("corrupted_land") or {}


def _ours(land):
    cfg = rules.CORRUPT_LAND
    ring = land.get("ring")
    rings = cfg.get("rings") or ((cfg.get("ring"),) if cfg.get("ring") else ("meadow", "forest"))
    if ring not in rings:
        return False
    lvls = list(land.get("levels") or [])
    if not lvls:
        return False
    # Only check max level <= 12
    max_lv = max(lvls)
    return max_lv <= cfg.get("max_level", 12)


def _worth_it(land):
    return land.get("phase") in ("open", "guardian") and _ours(land) and api.iso_wait(land.get("ends_at")) > 0


def _next_is_ours():
    """Is the land due at _next[0] of our kind? Since both 1-6 and 6-12 lands are now taken, lands in our target ring are always our kind.
    If period is active, return True whenever a land is scheduled."""
    if not _next[0]:
        return False
    return True


def _free():
    """Both are in their squads' hands (no queue order, no other job)."""
    return all(lease.holder(u) is None for u in rules.CORRUPT_LAND["units"])


def _predict(land):
    """Epoch at which the land after this one starts, from its phase and `ends_at`."""
    cfg = rules.CORRUPT_LAND
    left = api.iso_wait(land.get("ends_at"))
    into = cfg["open"] if land.get("phase") == "open" else cfg["open"] + cfg["guardian"]
    return time.time() + left - into + cfg["period"]


def due_in():
    """Seconds until the predicted start of the next land (None when nothing is predicted)."""
    return _next[0] - time.time() if _next[0] else None


def _update_stage():
    """STATE["stage"]: the main character and the lord rest in the camp (gathering points) from rules.CORRUPT_LAND["stage"] seconds
    before a land of our kind is due until it shows up (or the grace after the due time is over)."""
    cfg = rules.CORRUPT_LAND
    due = _next[0] - time.time()
    want = bool(not STATE["on"] and _next_is_ours() and -cfg["grace"] < due <= cfg["stage"])
    if want and not STATE["stage"]:
        api.info("[land] a land of our kind is due in %d min: the main character and the lord wait for it (the tribe when worthwhile, else the camp)" % max(0, due // 60))
        from . import queue
        for u in cfg["units"]:
            # (the owner, 2026-11-05): 虚空腐化之地开启时暂停打怪以免抢怪冲突
            if queue.hunting(u):
                queue.add(u, "land-stage", "resume-hunt", prio=9)
    elif STATE["stage"] and not want:
        api.debug("[land] the wait in the camp is over")
    STATE["stage"] = want


def check():
    """One look: switch STATE on or off and predict the next start. Returns (seconds the land has left if we are on, land)."""
    land = _read()
    if land is None:
        return 0, None
    if land.get("ends_at"):
        _next[0] = _predict(land)
        _kind["start"], _kind["target"] = _next[0] - rules.CORRUPT_LAND["period"], _ours(land)
        try:
            with open(_FILE, "w", encoding="utf-8") as f:
                json.dump({"next": _next[0], "seen_start": _kind["start"], "seen_target": _kind["target"]}, f)
        except OSError:
            pass
    _update_stage()
    if _worth_it(land):
        left = api.iso_wait(land["ends_at"])
        AREA[:] = [land["x"], land["y"], land.get("r", 7) + rules.CORRUPT_LAND["reach"]]
        if not STATE["on"] and _free():
            STATE["on"] = True
            api.info("[land] open in the %s at (%s,%s), levels %s, %d minutes left: the main character and the lord go there together" % (
                land["ring"], land["x"], land["y"], land["levels"], left // 60))
        if STATE["on"] and not _free():
            STATE["on"] = False
            api.warning("[land] the main character or the lord was taken by an order: the land squad ends")
        return (left if STATE["on"] else 0), land
    if land.get("ends_at") and not STATE["on"]:   # a land is there but not for us: say why (once per land)
        key = land.get("ends_at")
        if _seen[0] != key:
            _seen[0] = key
            cfg = rules.CORRUPT_LAND
            wanted_rings = "/".join(cfg.get("rings", ("meadow", "forest")))
            api.debug("[land] skipped: %s in the %s, levels %s, phase %s (wanted %s, max level %s)" % (
                land.get("ring") and "land", land.get("ring"), land.get("levels"), land.get("phase"),
                wanted_rings, cfg.get("max_level", 12)))
    if STATE["on"]:
        STATE["on"] = False
        api.info("[land] over or changed: the main character and the lord are back in the forest")
    return 0, land


def _wait_for(land, left):
    """Seconds to sleep before the next look."""
    cfg = rules.CORRUPT_LAND
    if left:                                    # hunting it: look again the moment it closes
        return left + 5
    if land is None:                            # the read failed
        return 30
    until = _next[0] - time.time()
    if _next[0] and until > 0:                  # sleep until the predicted start (or until the units are to go and rest)
        if _next_is_ours() and until > cfg["stage"]:
            return until - cfg["stage"] + 1
        return until + 3
    if _next[0] and until > -cfg["grace"]:      # it should be there by now: look every 20 s for a while
        return 20
    return cfg["fallback"]                      # nothing known: look now and then


def watch():
    while True:
        wait = rules.CORRUPT_LAND["fallback"]
        try:
            left, land = check()
            wait = _wait_for(land, left)
            if _next[0] > time.time() and not left:
                api.debug("[land] next one at about %s (in %d min), %s" % (time.strftime("%H:%M", time.localtime(_next[0])), (_next[0] - time.time()) // 60, "our kind" if _next_is_ours() else "not our kind (the lesser forest land)"))
        except Exception as e:
            api.error("[land] error %r" % (e,))
        time.sleep(max(5, min(wait, 7200)))
