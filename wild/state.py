"""Snapshot of the heroes for the local dashboard: the worker threads drop what they read here, state.json is rewritten at
most every 3 seconds (atomically), so the dashboard never has to spend reads of its own. Besides what a read shows (health,
points, place, food, bag) it carries what the process knows: the hero's job, what it is doing right now (`note`) and
who holds its lease."""
import json
import os
import threading
import time

from . import api, lease

PATH = os.path.join(api.HERE, "state.json")
_heroes = {}
_notes = {}     # unit -> (text, time)
_jobs = {}      # unit -> the material it was assigned
_targets = {}   # unit -> (x, y, label, time): where the hero is heading
_meta = {}      # tribe store / what is still needed / free text
_lock = threading.Lock()
_last = [0.0]
try:   # the last snapshot comes back with the process: the dashboard is not empty while the workers read
    with open(PATH, encoding="utf-8") as _f:
        _old = json.load(_f)
    _heroes.update(_old.get("heroes") or {})
    _meta.update(_old.get("meta") or {})
except (OSError, ValueError):
    pass
MATS = ("wood", "stone", "ore", "hide", "herb", "bone", "iron_ore")


def note(unit, text):
    """What the hero is doing right now (shown on the dashboard)."""
    with _lock:
        _notes[unit or 0] = (str(text)[:160], time.time())
        h = _heroes.get(str(unit or 0))
        if h is not None:   # shows at once, without waiting for the next read of the hero
            h["note"], h["note_at"] = _notes[unit or 0]
        _write()


def target(unit, x, y, label=""):
    """Where the hero is going (its node, the lord's copper node ...): shown on the dashboard."""
    with _lock:
        _targets[unit or 0] = (x, y, str(label)[:30], time.time())


def clear_target(unit):
    with _lock:
        _targets.pop(unit or 0, None)


def job(unit, res):
    with _lock:
        _jobs[unit or 0] = res


def meta(**kw):
    with _lock:
        _meta.update(kw)


def _write():
    if time.time() - _last[0] < 3:
        return
    _last[0] = time.time()
    data = {"heroes": _heroes, "meta": _meta, "notes": {str(k): v for k, v in _notes.items()}, "at": time.time()}
    tmp = PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, PATH)
    except OSError:
        pass


def update(unit, m, squad=""):
    w = (m.get("worn") or {}).get("weapon") or {}
    tool = (m.get("worn") or {}).get("tool") or {}
    bag = m.get("bag") or {}
    key = str(unit or 0)
    with _lock:
        _heroes[key] = {
            "unit": unit or 0, "squad": squad, "level": m.get("level"), "xp": m.get("xp"), "xp_next": m.get("xp_next"),
            "hp": m.get("hp"), "max_hp": m.get("max_hp"), "ap": m.get("ap"), "ap_max": m.get("ap_max"),
            "food": m.get("food"), "ring": m.get("ring"), "x": m.get("x"), "y": m.get("y"), "coins": m.get("coins"),
            "bread": bag.get("bread"), "meat": bag.get("meat"), "roast": bag.get("roast"),
            "mats": {k: bag[k] for k in MATS if bag.get(k)},
            "power": (m.get("gear_score") or {}).get("power"),
            "weapon": w.get("base"), "weapon_level": w.get("level"),
            "tool": tool.get("base"), "tool_dur": tool.get("durability"), "tool_max": tool.get("durability_max"),
            "down": bool(m.get("down")) or (m.get("hp") or 1) <= 0,
            "at": time.time()}
        h = _heroes[key]
        h["job"] = _jobs.get(unit or 0)
        n = _notes.get(unit or 0)
        h["note"], h["note_at"] = (n if n else ("", 0))
        h["lease"] = lease.holder(unit)
        t = _targets.get(unit or 0)
        h["target"] = {"x": t[0], "y": t[1], "label": t[2], "age": time.time() - t[3]} if t else None
        _write()
