"""The Abyss Expedition: a tower fought by the main character's whole squad (5-10 heroes walking with it).

Rules (GET /world/abyss): enter at the camp portal for 30 action points, 2 runs a day, 30 minutes a run; each floor
is 2 elite packs and a boss; `fight` costs 5 points and goes on to the end of the stage (40 rounds at most); every
hero acts each round; a hero down sits out, the main character down ends the run (all won is kept, no death penalty);
experience is shared among the heroes standing, into each hero's own level but never above the main character's.

Our squad is detached heroes ("units"); they must `rejoin` within 3 tiles of the main character to walk with it and
are sent out again (`detach`) afterwards.
"""
import json
import os
import threading
import time

from . import api, care, lease, queue, rules

PAUSE = threading.Event()         # set while the squad is assembling or inside: hunting squads stand still
LORD = rules.LORD
GO_FILE = "abyss.go"              # the owner says "go now", whatever the levels: touch this file
ENTER_AP = 60   # what the main character holds before the climb starts (30 to enter, a few floors after)
TICKET = 30   # coins the climb costs besides 30 action points (skill 0.24.30)


def goals_met(state=None):
    """The owner's rule: the lord, the main character and every hero at least rules.ABYSS_MIN_LEVEL."""
    m = state or api.squad()
    if not m or not m.get("units"):
        return False
    return m.get("level", 0) >= rules.ABYSS_MIN_LEVEL and all(u["level"] >= rules.ABYSS_MIN_LEVEL for u in m["units"])


def info():
    code, r = api.call("GET", "/world/abyss")
    return r if code == 200 else {}


def live_run():
    """The run live on the server right now (GET /world/abyss -> you.run), or None."""
    run = (info().get("you") or {}).get("run") or {}
    return run if run.get("status") == "live" else None


def runs_left():
    """Runs left today, from the signed-in view of /world/abyss (the field name is not documented: look for it)."""
    r = info()
    for key in ("runs_left", "left", "runsLeft", "left_today"):
        if isinstance(r.get(key), int):
            return r[key]
    mine = r.get("you") or r.get("me") or r.get("mine") or {}
    for key in ("runs_left", "left", "runsLeft", "left_today"):
        if isinstance(mine.get(key), int):
            return mine[key]
    return None  # unknown: try, the server refuses when none is left


def assemble(before_join=None):
    """Everyone to the camp, next to the main character, healed and fed, then `rejoin`."""
    m = api.squad()
    units = [u["hero"] for u in m["units"]]
    for u in units:   # (a hero already at the camp keeps its 8 points)
        h, _ = api.me(u)
        if h and h["ring"] != "camp":
            api.act(u, {"action": "home", "why": "the Abyss: to the camp to form the squad"})
    if m["ring"] != "camp":
        api.act(None, {"action": "home", "why": "the Abyss: the main character to the camp to form the squad"})
    time.sleep(25)
    main, _ = api.me(None)
    for u in units:
        api.act(u, {"action": "move", "to": [main["x"], main["y"]], "until_there": True})
    time.sleep(10)
    if before_join:   # (the heroes still walk alone here: their own reads and gifts work)
        before_join()
    joined = []
    for u in units:
        code, r = api.act(u, {"action": "rejoin"})
        api.debug("[abyss] %s rejoin: %s %s" % (u, code, "" if code == 200 else r.get("error")))
        if code == 200 or (code == 404 and "out on its own" in str(r.get("error"))):   # (404: it already walks with us)
            joined.append(u)
    return joined


def prepare(joined):
    """Eat, repair and top up health before going in (the run keeps the health we bring)."""
    main, _ = api.me(None)
    # since skill 0.24.30 the climb needs a 30-coin ticket besides the points: the heroes standing next to the main
    # character chip in what they can spare
    for u in joined if rules.AUTO_GIVE_MAIN else ():
        if main.get("coins", 0) >= TICKET + 5:
            break
        m, _ = api.me(u)
        spare = (m or {}).get("coins", 0) - 8
        if spare > 0:
            code, _r = api.act(u, {"action": "give", "to": "main", "coins": spare})
            if code == 200:
                main["coins"] = main.get("coins", 0) + spare
    for u in joined:  # everyone stands within 3 tiles of the main character: it hands out meat and repair coins
        m, _ = api.me(u)
        if not m:
            continue
        bag = m.get("bag") or {}
        if m["food"] < 70 and not (bag.get("bread") or bag.get("meat")) and (main["bag"].get("meat") or 0) >= 3:
            code, r = api.act(None, {"action": "give", "to": u, "goods": "meat", "qty": 3})
            api.debug("[abyss] meat to %s: %s" % (u, code))
            main["bag"]["meat"] -= 3
        if m.get("coins", 0) < 12 and main.get("coins", 0) >= 12:
            api.act(None, {"action": "give", "to": u, "coins": 10})
            main["coins"] -= 10
    for u in [None] + joined:
        for _ in range(3):
            m, _ = api.me(u)
            if not m or not care.eat(u, m, below=70):
                break
            time.sleep(1)
        m, _ = api.me(u)
        if m:
            care.auto_repair(u, m)


def fight_loop(seconds_left=None):
    """Fight stage after stage until the run ends, the main character is low, or about 3 minutes are left of the 60 the game
    gives (`seconds_left`: from the server when a run is resumed)."""
    deadline = time.time() + (seconds_left if seconds_left is not None else 55 * 60) - 180   # (the game gives 60 minutes)
    while time.time() < deadline:
        code, r = api.act(None, {"action": "abyss", "do": "fight"})
        api.debug("[abyss] fight: %s %s" % (code, json.dumps(api.clean(r), ensure_ascii=False)[:300]))
        if code == 429 and "action points" in str(r.get("error")):
            time.sleep(min(60, max(5, r.get("retry_after_seconds") or 20)))  # 3 points a minute come back: wait, never leave
            continue
        if code != 200:
            break
        if r.get("over") or r.get("ended") or r.get("finished"):
            break
        me = r.get("me") or {}
        if me.get("hp") is not None and me.get("max_hp") and me["hp"] < 0.15 * me["max_hp"]:
            api.act(None, {"action": "abyss", "do": "leave"})
            break
        time.sleep(max(2, me.get("ready_in_seconds") or 6))


def run_once():
    """One expedition: enter, fight stage after stage until the answer says it ended, leave, send the heroes out."""
    PAUSE.set()
    joined = []
    try:
        joined = assemble()
        prepare(joined)
        lease.take(None, "abyss", secs=2 * 3600)   # the main character spends nothing while the points come back
        while True:   # the squad stands together at the camp until the main character has the points for the climb
            main, _ = api.me(None)
            if main and main.get("ap", 0) >= ENTER_AP:
                break
            time.sleep(30)
        lease.release(None, "abyss")
        prepare(joined)   # (food and repairs once more before going in)
        code, r = api.act(None, {"action": "abyss", "do": "enter"})
        api.info("[abyss] enter: %s %s" % (code, json.dumps(api.clean(r), ensure_ascii=False)[:400]))
        if code != 200:
            return False
        fight_loop()
        api.act(None, {"action": "abyss", "do": "leave"})
        care.identify()
        m_after, _ = api.me(None)
        if m_after:
            care.auto_salvage(None, m_after)
        return True
    finally:
        try:
            m_curr, _ = api.me(None)
            squad_heroes = [h["id"] for h in (m_curr.get("hero") or {}).get("squad") or []] if m_curr else []
            for u in squad_heroes:
                code, _ = api.act(None, {"action": "detach", "hero": u})
                if code == 200:
                    api.info("[abyss] detached %s; standing by in camp" % u)
                    queue.add(u, "stay", prio=9, force=True)
        except Exception as e:
            api.warning("[abyss] detach after run failed: %r" % e)
        PAUSE.clear()


def resume():
    """After a restart of the process: a run still live on the server is fought on (heroes walk with the main character)."""
    run = live_run()
    if not run:
        return
    left = run.get("seconds_left") or 0
    api.info("[abyss] resuming run %s: floor %s stage %s, %ss left" % (run.get("run"), run.get("floor"), run.get("stage"), left))
    PAUSE.set()
    try:
        fight_loop(left)
        api.act(None, {"action": "abyss", "do": "leave"})
        care.identify()
        m_after, _ = api.me(None)
        if m_after:
            care.auto_salvage(None, m_after)
    finally:
        try:
            m_curr, _ = api.me(None)
            squad_heroes = [h["id"] for h in (m_curr.get("hero") or {}).get("squad") or []] if m_curr else []
            for u in squad_heroes:
                code, _ = api.act(None, {"action": "detach", "hero": u})
                if code == 200:
                    api.info("[abyss] detached %s; standing by in camp" % u)
                    queue.add(u, "stay", prio=9, force=True)
        except Exception as e:
            api.warning("[abyss] detach after run failed: %r" % e)
        PAUSE.clear()


def watch():
    """Job: wait for the goals (or abyss.go), bank the main character's points, then go in."""
    while True:
        try:
            resume()   # a run live on the server (restart, or entered by a queue order) is fought on from here
        except Exception as e:
            api.error("[abyss] resume error %r" % e)
        try:
            if not getattr(rules, "ABYSS", True) and not os.path.exists(GO_FILE):
                lease.release(None, "abyss")   # off (2026-11-04): it held the main character's lease and would have pulled every hero in
                time.sleep(180)
                continue
            go = os.path.exists(GO_FILE)  # the owner said: go now, whatever the levels (touch abyss.go)
            left = runs_left()
            if go and (left is None or left > 0):   # the owner's word: muster at once, wait for the points in the camp
                os.remove(GO_FILE)
                run_once()
                time.sleep(35 * 60)
                continue
            if (go or goals_met()) and (left is None or left > 0):
                # bank the main character's points (30 to enter, 10 a floor, 3 come back a minute): it stops hunting,
                # the rest of the squads carry on until the climb starts
                lease.take(None, "abyss", secs=2 * 3600)
                main, _ = api.me(None)
                if main and main.get("ap", 0) >= (60 if go else rules.ABYSS_BANK_AP):
                    if go:
                        os.remove(GO_FILE)
                    lease.release(None, "abyss")
                    run_once()
                    time.sleep(35 * 60)
            else:
                lease.release(None, "abyss")
        except Exception as e:
            api.error("[abyss] watch error %r" % e)
        time.sleep(180)
