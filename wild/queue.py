"""A task queue for every character. A task is an order, `ACTION [ARG]`: the squads put "recover" on a hurt hero's

queue, the owner's orders come in through orders.txt (`UNIT ACTION [ARG]`, UNIT 0 = the main character, read every few

seconds). One worker per hero runs its tasks one after the other, highest priority first, and holds the hero's lease

meanwhile so the hunting squads leave it alone; with the queue empty the hero goes back to its squad.



An order that cannot be carried out now (the way to the tribe is closed, too few points for the walk) does NOT hold the

hero: it waits in a pending list while the hero keeps hunting, and a dispatcher moves it to the hero's queue the moment

it can run. An order that finds the way closed on the road goes back to pending the same way.



The tasks (running, waiting and pending) are kept in queue.json (for `wild.py status`) and survive a restart: restore()

puts them back, so a restart never strands a hero halfway.



Actions: recover (a hurt hero heals and trains at the tribe), train [N], deposit, walk-tribe, home, eat [ITEM].

A FORCED order (`UNIT force ACTION [ARG]` in orders.txt) ignores everything above: it starts at once, goes ahead of the

hero's other tasks, and walks whatever the road looks like (closed way, strong creatures, few points: it only waits for

the points a step needs). Use it when the owner says "go, whatever happens".

"""

import json

import os

import threading

import time



from . import api, care, lease, rules



FILE = "queue.json"

ORDERS = "orders.txt"

TRIBE_ACTIONS = ("recover", "repair-tribe", "train", "deposit", "walk-tribe", "gather")   # orders that need the way into the tribe land

_lock = threading.Lock()

_tasks = {}     # unit -> [task]   task: {"action", "arg", "prio", "added"}: waiting for the unit's worker

_running = {}   # unit -> the task in progress

_pending = {}   # unit -> [task]   cannot run now: the hero hunts meanwhile

_workers = set()





def name(task):

    return ("force " if task.get("force") else "") + task["action"] + (" %s" % task["arg"] if task.get("arg") not in (None, "") else "")





def _mirror():

    snap = {}

    for u in set(_tasks) | set(_running) | set(_pending):

        ts, run, pend = _tasks.get(u) or [], _running.get(u), _pending.get(u) or []

        if ts or run or pend:

            snap[str(u if u is not None else 0)] = {

                "running": name(run) if run else None,

                "waiting": [name(t) for t in ts] + ["待命:" + name(t) for t in pend],

                "tasks": ([dict({k: run.get(k) for k in ("action", "arg", "prio", "force")}, running=True)] if run else [])

                + [{k: t.get(k) for k in ("action", "arg", "prio", "force")} for t in ts + pend]}

    try:

        with open(FILE, "w", encoding="utf-8") as f:

            json.dump(snap, f, ensure_ascii=False)

    except OSError:

        pass







def _go_home(unit, why="the order `home` (orders.txt)"):

    """`home` costs 8 points: with fewer the hero waits for them (it must really stand in the camp before the next order)."""

    from . import care, rules

    for _ in range(60):

        m, _v = api.me(unit)

        if m and m["ring"] == "camp":

            return True

        if m and m.get("hp", 0) < m.get("max_hp", 1) * rules.HEAL_BELOW:

            care.heal_with_items(unit, m)

            care.eat(unit, m)

            m, _v = api.me(unit)

        if m and m.get("ring") != "camp" and m.get("hp", 0) >= m.get("max_hp", 1) * rules.HOME_BELOW:

            if any(k in str(why) for k in ("hurt", "recovery", "badly", "cheaply", "ambush")):

                api.info("[%s] 血线已拉回 (hp %d/%d >= %d%%): 取消回城 (%s)" % (
                    api.who(unit), m["hp"], m["max_hp"], int(rules.HOME_BELOW * 100), why))

                return False

        if m and m["ap"] >= 8:

            api.wait_busy(m)

            code, result = api.act(unit, {"action": "home", "why": why})

            if code == 402:

                from . import nav

                api.warning("[queue %s] cannot pay for home: %s; walking safely" % (api.who(unit), result.get("error")))

                return nav.walk_home(unit, until=lambda: waiting(unit))

            else:

                time.sleep(25)

        else:

            time.sleep(30)

    return False





def _stay(unit):

    """`stay`: stand by in the camp (home first) and hold the hero: no hunting, no gathering, until another order is queued for it.

    The daily chest is banked here when the main character stands by (it is in the camp: the owner's "while passing")."""

    from . import care

    _go_home(unit, "standing by in the camp (order)")

    api.debug("[queue %s] standing by in the camp" % (api.who(unit)))

    while not waiting(unit):

        m, _v = api.me(unit)

        if m:

            care.claim_chest(unit, m)

        time.sleep(60)

    return True





def _whetstone_weapon(unit, arg):

    """A one-off owner order. Persist each step; never repeat an action with an uncertain outcome."""

    if unit not in (None, 928, 934):

        return True

    try:

        parts = (arg or "").split()

        item_id, total = int(parts[0]), int(parts[1])

        remaining = int(parts[2]) if len(parts) > 2 else total

        stage = parts[3] if len(parts) > 3 else "ready"

        if total not in (1, 9) or not 0 <= remaining <= total or stage not in ("ready", "inflight"):

            raise ValueError("invalid progress")

    except (ValueError, IndexError):

        api.warning("[whetstone] invalid order; no action taken")

        return True

    if stage == "inflight":

        api.warning("[whetstone] previous action outcome unknown; stopping without resending")

        return True



    def save(state):

        with _lock:

            task = _running.get(unit)

            if not task or task.get("action") != "whetstone-weapon":

                return False

            task["arg"] = "%d %d %d %s" % (item_id, total, remaining, state)

            _mirror()

            return True



    while remaining:

        m, _ = api.me(unit)

        if not m:

            api.warning("[whetstone] cannot read %s; stopping without another use" % api.who(unit))

            return True

        weapon = (m.get("worn") or {}).get("weapon") or {}

        if weapon.get("id") != item_id:

            api.warning("[whetstone] %s weapon changed; stopping with %d still to use" % (api.who(unit), remaining))

            return True

        if ((m.get("bag") or {}).get("whetstone") or 0) < remaining:

            api.warning("[whetstone] fewer stones than requested remain; stopping")

            return True

        if m.get("hp", 0) <= 0 or m.get("ap", 0) < 10:

            time.sleep(30)

            continue

        api.wait_busy(m)

        if not save("inflight"):

            return True

        code, result = api.act(unit, {"action": "use", "currency": "whetstone", "item": item_id, "wait": False})

        if code != 200:

            api.warning("[whetstone] action %d/%d returned %s %s; stopped, no repeat" % (

                total - remaining + 1, total, code, str(api.clean(result))[:350]))

            return True

        remaining -= 1

        if not save("ready"):

            return True

        api.info("[whetstone] %s weapon #%d: %d/%d applied: %s" % (

            api.who(unit), item_id, total - remaining, total, str(api.clean(result))[:350]))

        api.wait_result(result)

    return True





def _remote_withdraw(unit, arg):

    """Execute one explicit owner-specified withdrawal; do not repeat an uncertain submission."""

    from . import tribe

    if unit is not None:

        api.warning("[remote-withdraw] only the main character may run this order")

        return True

    parts = (arg or "").split()

    stage = parts.pop() if parts and parts[-1] in ("inflight", "done") else "ready"

    if stage != "ready":

        api.warning("[remote-withdraw] previous submission is %s; not sending again" % stage)

        return True

    try:

        items = {}

        for part in parts:

            key, value = part.split("=", 1)

            if key not in rules.STORE_EXCLUDED or key in items or not value.isdigit() or not 0 < int(value) <= 100:

                raise ValueError("invalid item")

            items[key] = int(value)

        if not items:

            raise ValueError("empty order")

    except ValueError:

        api.warning("[remote-withdraw] invalid item list; no action taken")

        return True

    m, _ = api.me(None)

    t = tribe.info()

    if not m or not t:

        api.warning("[remote-withdraw] character or store read failed; no action taken")

        return True

    store = t.get("store") or {}

    missing = {k: n - (store.get(k, 0) or 0) for k, n in items.items() if (store.get(k, 0) or 0) < n}

    if missing:

        api.warning("[remote-withdraw] store lacks requested items: %s; no action taken" % missing)

        return True

    if m.get("hp", 0) <= 0:

        api.warning("[remote-withdraw] main is down; no action taken")

        return True

    api.wait_busy(m)

    with _lock:

        task = _running.get(None)

        if not task or task.get("action") != "remote-withdraw":

            return True

        task["arg"] = " ".join(parts + ["inflight"])

        _mirror()

    code, result = api.act(None, {"action": "withdraw", "items": items, "wait": False})

    if code != 200:

        api.warning("[remote-withdraw] result %s %s; not sending again" % (code, str(api.clean(result))[:1200]))

        return True

    with _lock:

        task = _running.get(None)

        if task and task.get("action") == "remote-withdraw":

            task["arg"] = " ".join(parts + ["done"])

            _mirror()

    api.info("[remote-withdraw] completed %s: %s" % (items, str(api.clean(result))[:1600]))

    api.wait_result(result)

    return True





def _travel_waypoint(unit, ring):

    """Owner-ordered waypoint travel; resume the interrupted hunt after reaching the requested ring."""

    from . import danger, nav

    if unit != 928 or ring not in ("forest", "highlands"):

        api.warning("[waypoint] unsupported unit or destination")

        return True

    while True:

        m, _ = api.me(unit)

        if not m:

            api.warning("[waypoint %s] cannot read position; stopping" % api.who(unit))

            return True

        if m.get("ring") == ring:

            api.debug("[waypoint %s] arrived in %s at (%s,%s)" % (api.who(unit), ring, m.get("x"), m.get("y")))

            return True

        if m.get("hp", 0) <= 0:

            time.sleep(30)

            continue

        if m.get("ring") != "camp":

            if not _go_home(unit, "the owner ordered a trip to the %s waypoint" % ring):

                return False

            continue

        waypoint = next((w for w in m.get("waypoints") or [] if w.get("ring") == ring and w.get("open")), None)

        if not waypoint:

            api.warning("[waypoint %s] %s waypoint is not open" % (api.who(unit), ring))

            return True

        if danger.strong_near(waypoint["x"], waypoint["y"], m["level"], rules.AGGRO_RANGE + 1) or danger.bad_zone(waypoint["x"], waypoint["y"], m["level"]):

            api.warning("[waypoint %s] known danger at %s waypoint; waiting" % (api.who(unit), ring))

            time.sleep(30)

            continue

        if m.get("ap", 0) < 8:

            time.sleep(30)

            continue

        api.wait_busy(m)

        code, result = api.act(unit, {"action": "travel", "to": ring, "wait": False})

        api.debug("[waypoint %s] travel to %s: %s %s" % (api.who(unit), ring, code, "" if code == 200 else result.get("error")))

        if code != 200:

            return True

        api.wait_result(result)

        m, _ = api.me(unit)

        if m and m.get("ring") == ring:

            api.debug("[waypoint %s] arrived in %s at (%s,%s)" % (api.who(unit), ring, m.get("x"), m.get("y")))

        else:

            api.warning("[waypoint %s] travel accepted; arrival could not be confirmed" % api.who(unit))

        return True





def _runner(unit, action, arg, force=False):

    """The callable for an order (True when done, False when it could not run now), or None if the action is unknown."""

    from . import tribe

    if action == "hunt":

        return lambda: True  # _work releases the lease while this persistent order is active

    if action == "migrate-tribe" and unit is None:

        def migrate_tribe():

            from . import danger, nav

            x, y = (int(v) for v in (arg or "").split())

            target, stand = (x, y), (x - 1, y)

            def park():

                while not waiting(unit):

                    time.sleep(15)

                return True

            code, response = api.call("GET", "/world/tribes/" + tribe.TAG)

            if code != 200 or not response.get("tribe"):

                api.warning("[migrate] tribe read failed; no move")

                return park()

            t = response["tribe"]

            if (t.get("x"), t.get("y")) == target:

                api.debug("[migrate] tribe already at %s" % (target,))

                return park()

            if not t.get("free_move") or t.get("wars"):

                api.warning("[migrate] free move unavailable or war present; no move")

                return park()

            travelled = quoted = False

            while True:

                m, around = api.me(None)

                if not m:

                    time.sleep(15)

                    continue

                danger.observe(m, around)

                if m.get("hp", 0) <= 0 or m.get("down_until"):

                    api.warning("[migrate] main is down; stopping")

                    return park()

                if m["hp"] < rules.HOME_BELOW * m["max_hp"]:

                    api.warning("[migrate] main is hurt; stopping")

                    return park()

                api.wait_busy(m)

                if nav.dist(m, target) <= 1:

                    break

                if m["ring"] == "camp":

                    if m["hp"] < 0.7 * m["max_hp"] or m["ap"] < 30:

                        time.sleep(25)

                        continue

                    if "forest" not in nav.open_waypoints(m):

                        api.warning("[migrate] forest waypoint not open; stopping")

                        return park()

                    if danger.strong_near(356, 256, m["level"], rules.AGGRO_RANGE + 1):

                        api.warning("[migrate] known strong creature at forest waypoint; stopping")

                        return park()

                    code, result = api.act(None, {"action": "travel", "to": "forest", "wait": False})

                    api.debug("[migrate] travel to forest: %s %s" % (code, api.clean(result)))

                    if code != 200:

                        return park()

                    travelled = True

                    continue

                if not travelled:

                    api.warning("[migrate] main is outside camp, not at the requested route; stopping")

                    return park()

                if not quoted:

                    code, quote = api.call("GET", "/world/route?to=%d,%d" % stand)

                    if code != 200:

                        api.warning("[migrate] route quote failed; stopping")

                        return park()

                    api.debug("[migrate] waypoint route: from %s to %s, %s AP, %s tiles" % (

                        quote.get("from"), quote.get("to"), quote.get("ap"), quote.get("tiles")))

                    quoted = True

                if m["ap"] < 9:

                    time.sleep(25)

                    continue

                if nav.dist(m, stand) > 0:

                    if nav.dist(m, stand) > 6:

                        d = nav.dist(m, stand)

                        step = (round(m["x"] + (stand[0] - m["x"]) * 6 / d),

                                round(m["y"] + (stand[1] - m["y"]) * 6 / d))

                    else:

                        step = stand

                    if danger.bad_zone(step[0], step[1], m["level"]):

                        api.warning("[migrate] dangerous area at %s; stopping" % (step,))

                        return park()

                    result = nav.hop(None, m, step, (), around, force=False)

                    if result != 200:

                        api.warning("[migrate] safe walk refused (%s); stopping" % result)

                        return park()

                    continue

            code, check = api.call("GET", "/world/tribes/" + tribe.TAG)

            t = check.get("tribe") if code == 200 else None

            if not t or not t.get("free_move") or t.get("wars"):

                api.warning("[migrate] final tribe check failed; no migration")

                return park()

            code, result = api.act(None, {"action": "migrate", "to": list(target), "owner_ok": True, "wait": False})

            api.debug("[migrate] action to %s: %s %s" % (target, code, api.clean(result)))

            # A lost response may mean it succeeded. Never automatically submit it again.

            return park()

        return migrate_tribe

    if action == "abyss-enter" and unit is None:

        def abyss_enter():

            from . import abyss

            if abyss.live_run():   # a run is live on the server: abyss.watch owns it, the queue hands over

                api.debug("[abyss] live run on the server: abyss.watch owns it, queue hands over")

                return True

            m, _ = api.me(None)

            if not m:

                api.warning("[abyss] cannot read main; entry not attempted")

                return _stay(unit)

            squad = (m.get("hero") or {}).get("squad") or []

            if m.get("ring") != "camp" or m.get("ap", 0) < 30 or m.get("coins", 0) < 30 or len(squad) < 4:

                api.warning("[abyss] entry conditions not met; entry not attempted")

                return _stay(unit)

            api.wait_busy(m)

            code, response = api.call("POST", "/world/act", {"action": "abyss", "do": "enter", "wait": False}, retries=1)

            api.debug("[abyss] entry: %s %s" % (code, api.clean(response)))

            if code == 200 or abyss.live_run():   # entered: abyss.watch picks the live run up and fights it

                return True

            return _stay(unit)

        return abyss_enter

    if action == "unlist-rejoin" and unit is not None:

        def unlist_rejoin():

            code, market = api.call("GET", "/world/market/mine?unit=%d" % unit)

            if code != 200 or not isinstance(market.get("listings"), list) or not str(market.get("character") or "").endswith("-%s" % unit):

                api.warning("[queue %s] cannot verify own listings; staying" % api.who(unit))

                return _stay(unit)

            for listing in market["listings"]:

                m, _ = api.me(unit)

                if not m:

                    api.warning("[queue %s] read failed before unlist; staying" % api.who(unit))

                    return _stay(unit)

                api.wait_busy(m)

                code, result = api.act(unit, {"action": "unlist", "listing": listing["id"], "wait": False})

                api.debug("[queue %s] unlist %s: %s %s" % (api.who(unit), listing["id"], code, api.clean(result)))

                if code != 200:

                    return _stay(unit)

            return _runner(unit, "rejoin", None)()

        return unlist_rejoin

    if action == "detach" and unit is None:

        def detach_hero():

            hero = int(arg)

            m, _ = api.me(None)

            if not m:

                api.warning("[queue main] detach %s: read failed; no action" % hero)

                return _stay(unit)

            if any(u.get("hero") == hero for u in m.get("units") or []):

                api.debug("[queue main] %s already detached" % hero)

                return _stay(unit)

            if not any(h.get("id") == hero for h in (m.get("hero") or {}).get("squad") or []):

                api.warning("[queue main] %s not found in squad; no action" % hero)

                return _stay(unit)

            api.wait_busy(m)

            code, response = api.act(None, {"action": "detach", "hero": hero, "wait": False})

            api.debug("[queue main] detach %s: %s %s" % (hero, code, api.clean(response)))

            return _stay(unit)

        return detach_hero

    if action == "rejoin" and unit is not None:

        def rejoin_hero():

            from . import nav

            m, _ = api.me(unit)

            main, _ = api.me(None)

            if not m or not main:

                api.warning("[queue %s] rejoin: read failed; no action" % api.who(unit))

                return _stay(unit)

            if max(abs(m["x"] - main["x"]), abs(m["y"] - main["y"])) > 3:

                if m.get("ring") != "camp" or main.get("ring") != "camp":

                    api.warning("[queue %s] rejoin: too far from main; no action" % api.who(unit))

                    return _stay(unit)

                api.wait_busy(m)

                code, result = api.act(unit, {"action": "move", "to": [main["x"], main["y"]], "wait": False})

                if code != 200:

                    api.warning("[queue %s] rejoin meeting move failed: %s %s" % (api.who(unit), code, api.clean(result)))

                    return _stay(unit)

                m, _ = api.me(unit)

                if not m:

                    return _stay(unit)

                api.wait_busy(m)

                m, _ = api.me(unit)

                main, _ = api.me(None)

                if not m or not main or nav.dist(m, (main["x"], main["y"])) > 3:

                    api.warning("[queue %s] rejoin meeting not confirmed" % api.who(unit))

                    return _stay(unit)

            api.wait_busy(m)

            code, response = api.act(unit, {"action": "rejoin", "wait": False})

            api.debug("[queue %s] rejoin: %s %s" % (api.who(unit), code, api.clean(response)))

            if code != 200:

                return _stay(unit)

            return True

        return rejoin_hero

    if action == "recover":

        return lambda: tribe.recover(unit, force=force)

    if action == "whetstone-weapon":

        return lambda: _whetstone_weapon(unit, arg)

    if action == "remote-withdraw":

        return lambda: _remote_withdraw(unit, arg)

    if action == "travel-waypoint":

        return lambda: _travel_waypoint(unit, arg)

    if action == "repair-tribe":

        return lambda: tribe.repair_trip(unit, force=force)

    if action == "repair-camp":

        return lambda: tribe.repair_camp(unit)

    if action == "land-stage":   # wait for a corrupted land: at the tribe when worthwhile, else in the camp

        return lambda: tribe.stage_rest(unit, force=force)

    if action == "train":

        return lambda: tribe.visit(unit, train=int(arg or 5), patience=0, force=force)

    if action == "deposit":

        return lambda: tribe.go(unit, 0, force) and tribe.deposit_all(unit) and _go_home(unit, "the deposit trip is done")

    if action == "walk-tribe":

        return lambda: tribe.go(unit, 0, force)

    if action == "gather":   # everybody at the tribe, staying there until another order comes

        return lambda: tribe.gather(unit, arg == "force")   # `gather force`: through the strong creatures

    if action == "push":   # one hard try through the strong creatures: back to the camp when hurt (never repeated)

        return lambda: tribe.visit(unit, train=int(arg or 10), patience=0, force=True, bail=0.7)

    if action == "scout":

        from . import nav

        return lambda: nav.scout(unit, (arg or "forest").rstrip("+"), (arg or "").endswith("+"))   # "forest+": go further in

    if action == "harvest":   # `harvest wood|stone|ore [quota]`: raw materials for the tribe

        from . import harvest

        res, _, q = (arg or "wood").partition(" ")

        if q:   # `harvest wood 80`: gather up to 80 of it and stop (the old order)

            return lambda: harvest.harvest(unit, res, q)

        return lambda: harvest.run(unit, res)   # `harvest wood`: the tribe's building materials, wood is this hero's own job

    if action == "muster":   # everybody at the camp with `arg` points (default 70), then True: a `gather` after it walks them in together

        return lambda: tribe.muster(unit, int((arg or "70").split()[0]), squad="squad" in (arg or ""))

    if action == "raise":   # work on the totem while the tribe raises it

        return lambda: tribe.raise_totem(unit)

    if action == "cancel":   # drop the orders that wait for the hero (a gather that cannot start yet); a running one ended already

        def cancel():

            with _lock:

                _pending[unit] = []

                _mirror()

            api.debug("[queue %s] waiting orders dropped" % (api.who(unit)))

            return True

        return cancel

    if action == "construct":   # `construct`: walk to the tribe, store everything, build storehouse, mill and quarry alone (main character)

        return lambda: tribe.construct(unit)

    if action == "free":   # `free`: an order that ends at once: whatever held the hero (stay, harvest) lets go, the squads take it again

        return lambda: True

    if action == "stay":   # `stay`: park the hero in the camp until the next order (the owner: the main character stands by)

        return lambda: _stay(unit)

    if action == "home":

        return lambda: _go_home(unit)

    if action == "eat":

        return lambda: api.act(unit, {"action": "eat", "item": arg or "bread"}) and True

    return None





def can_run(unit, task):

    """Can this order run right now? Tribe orders need the way open, a waypoint and enough points (or the hero already at

    the tribe)."""

    if task["action"] == "repair-tribe":

        from . import tribe

        if time.monotonic() < tribe.REPAIR_RETRY.get(unit, 0):

            return False

    if task["action"] == "land-stage":

        m, _ = api.me(unit)

        return bool(m) and m.get("hp", 0) > 0   # the worker itself chooses tribe or camp, safely either way

    if task["action"] == "recover":

        from . import nav, tribe

        m, _ = api.me(unit)

        if not m or m.get("hp", 0) <= 0:

            return False

        if m.get("ring") == "camp" and m["hp"] >= m["max_hp"] * rules.HEALED:

            return True   # nothing left to heal and nowhere to go: the order finishes at once (it must not hold the hunt)

        if m.get("ring") != "camp" or not tribe.recover_goes_to_tribe(unit, m):

            return True  # in the field the worker heads to the tribe or home at once; in camp with neither

            # goods over the limit nor quota it just rests there

        return nav.can_reach_tribe(m) and m.get('ap', 0) >= rules.TRIP_MIN_AP

    if task["action"] == "gather":   # off to the tribe once the way is open and the points and health allow a walk

        from . import nav, tribe

        m, _ = api.me(unit)

        if not m or m["hp"] <= 0:

            return False

        return nav.at_tribe(m) or (nav.can_reach_tribe(m) and m['ap'] >= 30)

    if task["action"] == "push":

        m, _ = api.me(unit)

        from . import nav

        return bool(m) and nav.can_reach_tribe(m) and m["ap"] >= 70 and m["hp"] >= m["max_hp"] * 0.8

    if task["action"] == "scout":   # the way home (8 points) must be paid for with room to spare

        m, _ = api.me(unit)

        return bool(m) and m["ap"] >= 40 and m["hp"] >= m["max_hp"] * 0.6

    if task["action"] not in TRIBE_ACTIONS:

        return True

    from . import nav, tribe

    # path_safe mechanism removed

    pass

    m, _ = api.me(unit)

    if not m or m["hp"] <= 0:

        return False

    if nav.at_tribe(m):

        return True

    return nav.can_reach_tribe(m) and m["ap"] >= rules.TRIP_MIN_AP   # health plays no part on the way back





def _known(unit, task):

    return any(name(t) == name(task) for t in ([_running[unit]] if unit in _running else [])

               + _tasks.get(unit, []) + _pending.get(unit, []))





def _start(unit, task):

    """(under _lock) put the task on the hero's queue and make sure its worker runs."""

    _tasks.setdefault(unit, []).append(task)

    if unit not in _workers:

        _workers.add(unit)

        threading.Thread(target=_work, args=(unit,), daemon=True).start()





def add(unit, action, arg=None, prio=5, force=False, now=False):

    """Order for a hero. The same order is not queued twice. Higher prio runs first. It runs at once, or waits in the

    pending list (the hero hunts meanwhile) until it can; a forced one always runs at once, first; `now`: at once (a task

    that was running when the process stopped resumes where it was). False if refused."""

    if _runner(unit, action, arg, force) is None:

        api.warning("[orders] unknown order %s %s" % (action, arg or ""))

        return False

    task = {"action": action, "arg": arg, "prio": 10 if force else prio, "added": time.time(), "force": force}

    with _lock:

        if _known(unit, task):

            return False

    ready = force or now or can_run(unit, task)   # reads from the server: outside the lock

    with _lock:

        if _known(unit, task):

            return False

        if ready:

            _start(unit, task)

        else:

            _pending.setdefault(unit, []).append(task)

            api.debug("[queue %s] %s cannot run now: hunting on, it waits" % (api.who(unit), name(task)))

        _mirror()

    return True





def waiting(unit):

    """Is another order queued for the hero behind the one running (a gathering hero then ends its stay)?"""

    with _lock:

        return bool(_tasks.get(unit))





def resting(unit):

    """Is a gather order waiting for this hero (pending)? It then stops hunting and rests so its points can build up."""

    with _lock:

        return any(t["action"] in ("gather", "recover", "repair-tribe", "repair-camp", "land-stage") for t in _pending.get(unit, []))





def hunting(unit):

    """True only while an explicit hunt order is active and no other order is waiting."""

    with _lock:

        return bool((_running.get(unit) or {}).get("action") == "hunt" and not _tasks.get(unit)

                    and not any(t["action"] in ("recover", "repair-tribe", "repair-camp", "land-stage") for t in _pending.get(unit, [])))





def busy(unit):

    with _lock:

        return bool(_tasks.get(unit)) or unit in _running





def dispatch(every=20):

    """Job: move pending orders to the hero's queue when they can run. Cheap while the way is closed (no server read)."""

    while True:

        try:

            from . import tribe

            if any(_pending.values()):

                safe = tribe.path_safe()

                for unit in [u for u, ts in _pending.items() if ts]:

                    for task in list(_pending.get(unit) or []):

                        if (safe or task["action"] not in TRIBE_ACTIONS or task["action"] == "recover") and can_run(unit, task):

                            with _lock:

                                if task in _pending.get(unit, []):

                                    _pending[unit].remove(task)

                                    _start(unit, task)

                                    _mirror()

                            api.debug("[queue %s] %s can run now" % (api.who(unit), name(task)))

                        time.sleep(3)

        except Exception as e:

            api.error("[queue] dispatch error %r" % e)

        time.sleep(every)





def _next(unit):

    with _lock:

        _running.pop(unit, None)

        ts = _tasks.get(unit) or []

        if not ts:

            _workers.discard(unit)

            _mirror()

            return None

        ts.sort(key=lambda t: (-t["prio"], t["added"]))

        t = ts.pop(0)

        _running[unit] = t

        _mirror()

        return t





def _work(unit):

    job = "queue"

    while True:

        while not lease.take(unit, job, secs=3 * 3600):  # another job (the Abyss) has it: wait our turn

            time.sleep(5)

        t = _next(unit)

        if t is None:

            lease.release(unit, job)

            return

        who = api.who(unit)

        api.debug("[queue %s] %s" % (who, name(t)))

        if t["action"] == "hunt":

            lease.release(unit, job)

            while not waiting(unit):

                time.sleep(2)

            continue

        try:

            done = _runner(unit, t["action"], t["arg"], t.get("force", False))()

        except Exception as e:

            api.error("[queue %s] %s failed: %r" % (who, name(t), e))

            done = True

        if done is False and not t.get("force") and t["action"] != "push":   # the way closed on the road, or no waypoint: hunt on, the order waits

            with _lock:

                _pending.setdefault(unit, []).append(t)

                _mirror()

            api.debug("[queue %s] %s could not run: hunting on, it waits" % (who, name(t)))

        elif ((t["action"] in ("recover", "repair-tribe", "repair-camp", "land-stage") and t.get("arg") == "resume-hunt")

              or t["action"] in ("whetstone-weapon", "travel-waypoint", "remote-withdraw")):

            with _lock:

                if not _tasks.get(unit):

                    _tasks.setdefault(unit, []).append({"action": "hunt", "arg": None, "prio": 5, "added": time.time(), "force": False})

                    _mirror()





def read():

    try:

        with open(FILE, encoding="utf-8-sig") as f:

            return json.load(f)

    except (OSError, ValueError):

        return {}





def restore():

    """At start: put back the tasks the last run had running, waiting or pending (queue.json)."""

    for key, q in read().items():

        unit = int(key) or None

        tasks = q.get("tasks")

        if tasks is None:  # a file from before tasks were kept: "order:train 10" names

            tasks = [{"action": n.split(":", 1)[-1].split()[0], "arg": (n.split(":", 1)[-1].split() + [None])[1], "prio": 9}

                     for n in ([q["running"]] if q.get("running") else []) + (q.get("waiting") or [])]

        for t in tasks:

            t["action"] = {"recover-at-tribe": "recover", "walk-to-tribe": "walk-tribe"}.get(t["action"], t["action"])

            if add(unit, t["action"], t.get("arg"), t.get("prio", 5), bool(t.get("force")), now=bool(t.get("running"))):

                api.debug("[queue %s] restored %s" % (api.who(unit), name(t)))





def read_orders():

    """Job: turn lines of orders.txt into queued tasks (the file is emptied once read)."""

    while True:

        try:

            if os.path.exists(ORDERS) and os.path.getsize(ORDERS):

                with open(ORDERS, encoding="utf-8") as f:

                    lines = [x.split() for x in f.read().splitlines() if x.strip()]

                open(ORDERS, "w").close()

                for parts in lines:

                    unit, rest = int(parts[0]) or None, parts[1:]

                    force = rest[0] in ("force", "!") or rest[0].startswith("!")

                    if force:   # "force ACTION", "! ACTION" or "!ACTION"

                        rest = ([rest[0][1:]] if rest[0].startswith("!") and len(rest[0]) > 1 else []) + rest[1:]

                    if rest:

                        add(unit, rest[0], " ".join(rest[1:]) or None, prio=9, force=force)

        except Exception as e:

            api.error("[orders] error %r" % e)

        time.sleep(5)

