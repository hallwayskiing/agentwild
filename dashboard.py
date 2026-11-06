"""Local dashboard, a service of its own: python dashboard.py  ->  http://127.0.0.1:8765
It does not depend on wild.py or any job: it asks the game itself (ONE request per page refresh, no background loop) and only
borrows wild.log and the notes/targets of state.json when they exist."""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
_cache = {"at": 0.0, "live": {}}
FOOD = ("bandage", "bread", "roast", "meat", "stew", "pie", "veggie_soup", "wheat", "carrot", "corn", "pumpkin")   # consumables of the bag
MATS = ("wood", "stone", "ore", "hide", "herb", "bone", "iron_ore", "dust", "copper_ore", "whetstone", "plating")


def items(bag):
    return {k: bag[k] for k in FOOD if bag.get(k)}


def mats(bag):
    return {k: bag[k] for k in MATS if bag.get(k)}


GRADE = {"gold": "黄金", "silver": "白银", "iron": "黑铁"}
ROLE = {"hunter": "猎人", "woodcutter": "伐木工", "miner": "矿工", "builder": "建造师"}


def title(rec):
    """'黄金 · 猎人' of a hero record (/world/hero/ap or the main character's `hero`); the demon lord is '魔王 · N阶'."""
    if rec.get("kind") == "demon_lord":
        return "魔王 · %s阶" % rec.get("rank", "?")
    return " · ".join(x for x in (GRADE.get(rec.get("grade"), rec.get("grade")), ROLE.get(rec.get("role"), rec.get("role"))) if x)


_xp = {"i": -1}   # unit -> (xp, xp_next, ring) from its last own read


def live():
    """ONE request to the game per page refresh (/world/me lists the main character and all nine heroes); cached 12 seconds
    so several open tabs share it. Nothing runs in the background."""
    if time.time() - _cache["at"] < 12:
        return _cache["live"]
    from wild import api
    code, r = api.call("GET", "/world/me")
    if code != 200:
        return _cache["live"]
    m, now, out = r["me"], time.time(), {}
    roles = api.ap_all(600)   # grades and roles hardly change: one read in ten minutes
    w = (m.get("worn") or {}).get("weapon") or {}
    offhand = (m.get("worn") or {}).get("offhand") or {}
    out[0] = {"unit": 0, "level": m.get("level"), "xp": m.get("xp"), "xp_next": m.get("xp_next"), "hp": m.get("hp"),
              "max_hp": m.get("max_hp"), "ap": m.get("ap"), "ap_max": m.get("ap_max"), "ring": m.get("ring"), "x": m.get("x"),
              "y": m.get("y"), "coins": m.get("coins"), "bread": (m.get("bag") or {}).get("bread"), "items": items(m.get("bag") or {}), "mats": mats(m.get("bag") or {}), "weapon": w.get("base"), "offhand": offhand.get("base"), "offhand_level": offhand.get("level"), "offhand_dur": offhand.get("durability"), "offhand_max": offhand.get("durability_max"), "title": title(m.get("hero") or {}), "at": now}
    for u in r["me"].get("units") or []:
        bag = u.get("bag") or {}
        at = u.get("at") or [None, None]
        out[u["hero"]] = {"unit": u["hero"], "level": u.get("level"), "hp": u.get("hp"), "max_hp": u.get("max_hp"), "ap": u.get("ap"),
                          "ap_max": u.get("ap_max"), "x": at[0], "y": at[1], "coins": u.get("coins"), "bread": bag.get("bread"), "items": items(bag), "mats": mats(bag), "title": title(roles.get(u["hero"]) or {}), "at": now}
    # the list carries no experience: one more hero's own read per refresh (so each hero's xp is at most ~2 minutes old)
    ids = sorted(k for k in out if k)
    if ids:
        _xp["i"] += 1
        u = ids[_xp["i"] % len(ids)]
        code, r2 = api.call("GET", "/world/me?unit=%d" % u)
        if code == 200:
            _xp[u] = (r2["me"].get("xp"), r2["me"].get("xp_next"), r2["me"].get("ring"), r2["me"].get("level"))
    for u, v in _xp.items():
        if u in out:
            out[u].update(xp=v[0], xp_next=v[1], ring=v[2], xp_level=v[3])
    _cache["at"], _cache["live"] = now, out
    return out



NOISE = re.compile(r"takes \d+ point|^\s*$")
NAMES = {0: "主角", 928: "魔王"}

_hist = {}   # unit -> {"last": (level, xp, xp_next), "cum": gained so far, "pts": [(time, cum)]}


def track(h):
    """Experience gained in the last 10 minutes (a level-up counts what was left of the level plus the new xp)."""
    if h.get("xp") is None or h.get("level") is None:
        return
    s = _hist.setdefault(h["unit"], {"last": None, "cum": 0, "pts": []})
    cur = (h.get("xp_level") or h["level"], h["xp"], h.get("xp_next") or 0)   # the level of the SAME read as the xp (the list shows a newer level)
    if s["last"]:
        pl, px, pn = s["last"]
        if cur[0] > pl:
            s["cum"] += max(0, pn - px) + cur[1]
        elif cur[0] == pl and cur[1] > px:
            s["cum"] += cur[1] - px
    s["last"] = cur
    now = time.time()
    if not s["pts"] or s["pts"][-1][1] != s["cum"] or now - s["pts"][-1][0] > 60:
        s["pts"].append((now, s["cum"]))
    s["pts"] = [q for q in s["pts"] if now - q[0] <= 1200]
    older = [q for q in s["pts"] if now - q[0] >= 600]
    base = older[-1] if older else s["pts"][0]   # the newest point at least 10 minutes old (or the oldest we have)
    h["xp_gain"], h["xp_win"] = s["cum"] - base[1], int(now - base[0])


def snapshot():
    from wild import rules
    try:
        with open(os.path.join(HERE, "state.json"), encoding="utf-8") as f:
            raw = json.load(f)
        heroes, meta = raw["heroes"], raw.get("meta") or {}
    except (OSError, ValueError, KeyError):
        heroes, meta = {}, {}
    for k, h in live().items():   # the fresh request wins over the worker's snapshot (xp, ring, notes and targets stay from it)
        heroes[str(k)] = dict(heroes.get(str(k)) or {}, **{a: b for a, b in h.items() if b is not None})
    out = []
    for h in sorted(heroes.values(), key=lambda h: (h["unit"] not in (0, 928), h["unit"])):
        if h.get("squad") not in rules.SQUADS:
            unit = h["unit"] or None
            h["squad"] = next((k for k, v in rules.SQUADS.items() if unit in v["members"] and not v.get("land")), "")
        track(h)
        out.append(dict(h, name=NAMES.get(h["unit"], str(h["unit"]))))
    try:
        with open(os.path.join(HERE, "wild.log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 60000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        lines = []
    log = [x for x in lines if not NOISE.search(x)][-400:]
    return {"heroes": out, "log": log, "meta": meta, "squads": {k: v["zh"] for k, v in rules.SQUADS.items()}}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/state"):
            body, kind = json.dumps(snapshot(), ensure_ascii=False).encode(), "application/json"
        else:
            with open(os.path.join(HERE, "dashboard.html"), "rb") as f:   # read per request: edits show on refresh
                body, kind = f.read(), "text/html"
        self.send_response(200)
        self.send_header("content-type", kind + "; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("dashboard on http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
