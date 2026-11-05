"""Boltbook Agent Wilds API: one throttled HTTP client and the calls every module shares.

An act with `unit` is played by that hero; without it, by the main character. The throttle is shared by all
threads of the process (60 reads and 30 writes a minute are allowed).
"""
import datetime
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "https://boltbook.fun/api/v1"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # agentwild/, where .env lives


def _load_key():
    key = os.environ.get("BOLTBOOK_API_KEY")
    path = os.path.join(HERE, ".env")
    if not key and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOLTBOOK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("BOLTBOOK_API_KEY not found in environment or .env")
    return key


_KEY = _load_key()
_lock = threading.Lock()
_last = {"GET": 0.0, "POST": 0.0}
_gap = {"GET": 1.2, "POST": 2.5}


def _throttle(method):
    with _lock:
        wait = _last[method] + _gap[method] - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[method] = time.time()


RETRIES = 3       # every request is tried this many times when it gets no answer (status 0)
RETRY_GAP = 2     # seconds between tries
# acts that move goods or coins: if the answer is lost after the request was sent (a read timeout), sending it again could do
# it twice, so these are only repeated when the request certainly never left (connection refused, DNS ...)
_SPENDS = {"buy", "sell", "bid", "unbid", "unlist", "requisition", "give", "deposit", "withdraw", "use", "socket", "repair",
           "identify", "list", "claim", "bank", "teleport", "build", "craft", "migrate"}


def _once(method, path, body):
    """One request: (status, json, sent) where `sent` is False only when it certainly never reached the server."""
    _throttle(method)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "Authorization": "Bearer " + _KEY, "content-type": "application/json", "user-agent": "agentwild/3.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r), True
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e), True
        except Exception:
            return e.code, {"error": str(e)}, True
    except urllib.error.URLError as e:   # (a read timeout is a TimeoutError, not a URLError: that one was sent)
        return 0, {"error": repr(e)}, False
    except Exception as e:
        return 0, {"error": repr(e)}, True


def call(method, path, body=None, retries=None):
    """(status, json). Status 0 means the request itself failed even after `retries` tries (default RETRIES): the one place
    where timeouts are handled, so no caller needs its own retry."""
    tries = RETRIES if retries is None else retries
    spends = method == "POST" and isinstance(body, dict) and body.get("action") in _SPENDS
    for i in range(tries):
        code, r, sent = _once(method, path, body)
        if code != 0 or i == tries - 1:
            return code, r
        if spends and sent:   # the answer may only have got lost: do not do it twice
            return code, r
        time.sleep(RETRY_GAP)
    return code, r


def iso_wait(ts):
    """Seconds until an ISO timestamp (0 if past or missing)."""
    if not ts:
        return 0
    t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return max(0, (t - datetime.datetime.now(datetime.timezone.utc)).total_seconds())


def me(unit=None):
    """(me, around) of a hero or of the main character; (None, None) when the read failed."""
    code, r = call("GET", "/world/me" + ("?unit=%d" % unit if unit else ""))
    if code != 200:
        return None, None
    _level_up(unit, r["me"])
    return r["me"], r.get("around") or {}



_levels = {}


def _level_up(unit, m):
    """NOTICE when a hero's level is higher than at its last read here (the first read only learns it)."""
    lv = m.get("level")
    if lv is None:
        return
    old = _levels.get(unit)
    _levels[unit] = lv
    if old is not None and lv > old:
        notice("[%s] level up: L%s -> L%s (xp %s/%s)" % (who(unit), old, lv, m.get("xp"), m.get("xp_next")))


_ap = {"at": 0.0, "data": {}}


def ap_all(max_age=15):
    """Every hero's points from ONE read of /world/hero/ap: {unit: {ap, ap_max, next_ap_at, full_at, where}}; the main
    character is unit None. Cached for `max_age` seconds; {} when the read failed."""
    if time.time() - _ap["at"] < max_age and _ap["data"]:
        return _ap["data"]
    code, r = call("GET", "/world/hero/ap")
    if code != 200:
        return _ap["data"] if time.time() - _ap["at"] < 120 else {}
    out = {}
    lead = r.get("leader") or {}
    out[None] = dict(lead, where="lead")
    for h in r.get("heroes") or []:
        out[h["id"]] = h
    _ap["at"], _ap["data"] = time.time(), out
    return out


def squad():
    """The main character plus every hero out on its own, from one read (place, level, hp, ap, food)."""
    code, r = call("GET", "/world/me")
    return r["me"] if code == 200 else None


_FAILED = re.compile(r": (0|[45]\d\d)(\s|$)")   # a status code that is not a success, as the call sites print it


def log(level, text):
    """One log line: `LEVEL text` (wild.py puts the time in front). DEBUG routine chatter, INFO what was done, NOTICE a result (kill, built, claimed),
    WARNING something odd or bad (deaths, refusals, trips home), ERROR a failure."""
    level = level.upper()
    if level in ("INFO", "NOTICE") and _FAILED.search(text):   # "...: 409 error text": whatever the caller thought, the call failed
        level = "WARNING"
    print("%-7s %s" % (level, text), flush=True)


def debug(text):
    log("DEBUG", text)


def info(text):
    log("INFO", text)


def notice(text):
    log("NOTICE", text)


def warning(text):
    log("WARNING", text)


def error(text):
    log("ERROR", text)


def who(unit):
    """A unit's name for the log: the hero's number, or "main" for the main character (unit None)."""
    return "main" if unit is None else unit


FOUGHT = {}   # unit -> when it last attacked (its own fights cost health: not an ambush)


def act(unit, payload):
    """POST an act as `unit` (None = main). Returns (status, json); a 429 for points is not retried."""
    if "why" in payload or payload.get("action") == "home":
        payload = dict(payload)
        why = payload.pop("why", None)
        if payload.get("action") == "home":   # the owner (2026-11-04): every trip home is explained in the log
            f = sys._getframe(1)
            warning("[home] %s: %s (%s:%s)" % ("main" if not unit else unit, why or "no reason given", os.path.basename(f.f_code.co_filename), f.f_code.co_name))
    if payload.get("action") == "attack":
        FOUGHT[unit] = time.time()
    if unit:
        payload = dict(payload, unit=unit)
    return call("POST", "/world/act", payload)


def after_attack(m, r):
    """(m with the health and points the answer to an attack reports, seconds until the next act can go), or None when the
    answer carries no `me` (then read again). The answer already says how we stand after the fight: no read is needed."""
    me = (r or {}).get("me") or {}
    if me.get("hp") is None:
        return None
    return dict(m, hp=me["hp"], ap=me.get("ap", m.get("ap"))), (me.get("ready_in_seconds") or 0) + 0.5


def wait_busy(m):
    w = iso_wait((m or {}).get("busy_until"))
    if w > 0:
        time.sleep(w + 1)


def clean(r):
    """An answer without the bulky map and me blocks, for logging."""
    return {k: v for k, v in r.items() if k not in ("around", "me")}
