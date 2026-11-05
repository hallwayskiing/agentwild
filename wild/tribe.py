"""The tribe HALL (chief: the main character): every member of the squad belongs to it. A visit is one routine for every
reason to go (healing, training, the owner's order): walk there (only on foot, only while the way is safe), fill the store
and top up the food, then train and heal as asked, then home."""
import datetime
import time

from . import api, care, nav, rules, state

TAG = "HALL"
LEADER = None   # the chief (the owner, 2026-11-05: the main character; was hero 931)


def info():
    code, r = api.call("GET", "/world/tribes/" + TAG)
    return r["tribe"] if code == 200 else None


def building_done(building_id):
    info_ = info()
    return bool(info_) and any(b["id"] == building_id and b["status"] == "done" for b in info_["building_list"])


def open_tile_beside(unit, reach=4):
    """Open ground round the unit, nearest first (the eight tiles beside it were all taken once the storehouse stood: trees, ore
    and buildings crowd the totem), up to `reach` tiles away; the server says what it accepts."""
    m, a = api.me(unit)
    tl = a["top_left"]
    seen = []
    for r in range(1, reach + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                x, y = m["x"] + dx, m["y"] + dy
                j, i = y - tl[1], x - tl[0]
                if 0 <= j < len(a["map"]) and 0 <= i < len(a["map"][j]) and a["map"][j][i] == ".":
                    seen.append([x, y])
    for p in seen:
        yield p


def build(kind, members=(), builder=LEADER):
    """The builder (the chief by default) places a building on an open tile beside it (paid from the store), the given members
    work on it."""
    bid = _build_by_walking(kind, members, builder)
    if bid:
        return bid
    tries = 0
    for at in open_tile_beside(builder):
        tries += 1
        if tries > 12:   # (the server refuses them all the same way: enough)
            break
        code, r = api.act(builder, {"action": "build", "kind": kind, "at": at})
        api.info("build %s at %s: %s %s" % (kind, at, code, r.get("error") or ""))
        if code == 200:
            bid = r.get("building")
            for u in members:
                code, r = api.act(u, {"action": "work", "building": bid})
                api.info("  %s works on %s: %s %s" % (u, bid, code, r.get("error") or ""))
            return bid
    return None


# --- training: a daily limit per hero, learnt from the answers -------------------------------------------------------
QUOTA_DONE = {}  # unit -> the day (UTC+8) on which its training limit was reached


def today():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date()


def has_quota(unit):
    """Unknown until a train says otherwise (the limit shows in 'today' of every answer)."""
    return QUOTA_DONE.get(unit) != today()


def train_once(unit):
    code, r = api.act(unit, {"action": "train"})
    t = r.get("today") or {}
    if code == 200 and t and t.get("trained", 0) >= t.get("most", 1 << 30):
        QUOTA_DONE[unit] = today()
    elif code in (400, 403, 409) and any(s in str(r.get("error", "")).lower() for s in ("daily", "quota", "limit", "today")):
        QUOTA_DONE[unit] = today()
    return code, r


# --- the store ----------------------------------------------------------------------------------------------------------
def surplus(m, keep=rules.FOOD_KEEP):
    bag = m.get("bag") or {}
    return {k: bag[k] - keep for k in ("bread", "meat", "roast") if bag.get(k, 0) > keep}


def currencies(unit, m):
    """{currency: amount} that goes into the store: runes, stones, whetstones, seals, dust, crystals, shards, gems; not
    rules.KEEP_CURRENCIES."""
    if not rules.STORE_CURRENCIES or not unit:   # the main character keeps its runes and gems (the owner, 2026-11-05)
        return {}
    bag = m.get("bag") or {}
    names = {c["currency"] for c in m.get("currencies") or []} | {"dust", "crystal", "shard"}
    names |= {k for k in bag if k.startswith(("gem_", "essence_"))}
    keep = set(rules.KEEP_CURRENCIES)
    return {k: bag[k] for k in names if bag.get(k) and k not in keep}


def worth_storing(m):
    """Does the bag hold enough to make a stop at the store worthwhile (materials, seeds, food over the limit)?"""
    bag = m.get("bag") or {}
    return sum(bag.get(k, 0) for k in rules.STORE_KINDS) + sum(surplus(m).values()) >= rules.WORTH_STORING


def depositable_goods(m):
    """How many depositable materials the bag holds (wood, stone, hide, ore and the other STORE_KINDS)."""
    bag = (m or {}).get("bag") or {}
    return sum(bag.get(k, 0) for k in rules.STORE_KINDS)


def recover_goes_to_tribe(unit, m):
    """Recovery heads to the tribe when either is enough on its own: depositable goods over
    rules.RECOVER_TRIBE_GOODS, or training quota left today."""
    return depositable_goods(m) > rules.RECOVER_TRIBE_GOODS or has_quota(unit)


def deposit_all(unit):
    """On the tribe's land, in the owner's order: the materials (and the heroes' currencies) go in first, then
    bread and meat are topped up to rules.FOOD_KEEP each, then any food still over that goes in too. Bandages
    are never deposited: they stay in the bag for healing."""
    m, _ = api.me(unit)
    api.wait_busy(m)
    bag = (m or {}).get("bag") or {}
    items = {k: bag[k] for k in rules.STORE_KINDS if bag.get(k)}
    runes = currencies(unit, m or {})
    if items or runes:
        code, r = api.act(unit, {"action": "deposit", "items": dict(items, **runes)})
        if code != 200 and runes and items:  # the store may refuse the currencies: the goods still go in
            api.warning("[%s] store refused %s: %s %s" % (api.who(unit), runes, code, r.get("error")))
            runes = {}
            code, r = api.act(unit, {"action": "deposit", "items": items})
        api.info("[%s] deposited %s: %s %s" % (api.who(unit), dict(items, **runes), code, "" if code == 200 else r.get("error")))
    restock_food(unit)
    m, _ = api.me(unit)
    if not m:
        return
    api.wait_busy(m)
    extra = surplus(m)
    if extra:
        code, r = api.act(unit, {"action": "deposit", "items": extra})
        api.info("[%s] deposited surplus food %s: %s %s" % (api.who(unit), extra, code, "" if code == 200 else r.get("error")))
    balance_coins(unit, m)


def balance_coins(unit, m):
    """Carry at most rules.COIN_KEEP coins: the excess goes into the tribe treasury, a shortfall is topped
    up from it (only as far as the treasury reaches; taking coins out may be refused for a hero who is not
    the chief, which is logged and leaves the rest of the visit alone)."""
    have = (m or {}).get("coins") or 0
    if have > rules.COIN_KEEP:
        code, r = api.act(unit, {"action": "deposit", "coins": have - rules.COIN_KEEP})
        api.info("[%s] put %d coins in the treasury (carries %d): %s %s" % (
            api.who(unit), have - rules.COIN_KEEP, rules.COIN_KEEP, code, "" if code == 200 else r.get("error")))
    elif have < rules.COIN_KEEP:
        take = min(rules.COIN_KEEP - have, (info() or {}).get("coins") or 0)
        if take > 0:
            code, r = api.act(unit, {"action": "withdraw", "coins": take})
            if code == 200:
                api.info("[%s] took %d coins from the treasury (carries %d)" % (api.who(unit), take, have + take))
            else:
                api.warning("[%s] cannot take %d coins from the treasury: %s %s" % (api.who(unit), take, code, r.get("error")))


def restock_food(unit):
    m, _ = api.me(unit)
    store = (info() or {}).get("store") or {}
    bag = (m or {}).get("bag") or {}
    want = rules.FOOD_KEEP
    take = {k: min(want - bag.get(k, 0), store.get(k, 0)) for k in ("meat", "bread")
            if bag.get(k, 0) < want and store.get(k, 0) > 0}
    if take:
        code, r = api.act(unit, {"action": "withdraw", "items": take})
        api.info("[%s] took %s from the store: %s %s" % (api.who(unit), take, code, "" if code == 200 else r.get("error")))


# --- going there ------------------------------------------------------------------------------------------------------
def recover(unit, force=False):
    """Heal a hurt hunter. When depositable goods exceed rules.RECOVER_TRIBE_GOODS or training quota remains
    (either one is enough), go to the tribe and heal there, straight via the forest waypoint with no camp
    rest first; when neither holds, go home and rest in the camp until rules.HEALED."""
    from . import queue
    while True:
        if queue.waiting(unit):
            return True
        m, _ = api.me(unit)
        if not m:
            time.sleep(15)
            continue
        if m.get("hp", 0) <= 0:
            time.sleep(60)
            continue
        if nav.at_tribe(m):
            return visit(unit, heal=True, train=99, patience=0, force=force)
        if recover_goes_to_tribe(unit, m):
            if m.get("ring") == "camp":
                if not force and (not nav.can_reach_tribe(m) or not path_safe() or m.get("ap", 0) < rules.TRIP_MIN_AP):
                    if not nav.can_reach_tribe(m) and m["hp"] >= m["max_hp"] * rules.HEALED:
                        return True   # no way out exists: healed in the camp, the trip is impossible anyway
                    state.note(unit, "营地等待回部落")
                    time.sleep(30)
                    continue
            elif not force and (not path_safe() or m.get("ap", 0) < rules.TRIP_MIN_AP):
                if not queue._go_home(unit):   # hurt in the field and the tribe trip cannot start: wait safely in the camp
                    return False
                continue
            return visit(unit, heal=True, train=99, patience=0, force=force)
        if m.get("ring") != "camp":
            if not queue._go_home(unit):
                return False
            continue
        if m["hp"] >= m["max_hp"] * rules.HEALED:
            return True
        state.note(unit, "营地休息回血")
        time.sleep(30)


def stage_rest(unit, force=False):
    """Wait for a corrupted land of our kind (landwatch stage), reusing the recovery trip: when depositable
    goods exceed rules.RECOVER_TRIBE_GOODS or the hero is hurt, go to the tribe directly (storing and
    resupplying there with deposit_all) and wait on its land; otherwise go home and rest in the camp. Ends
    when the land is due, so the land squad can take over at once."""
    from . import landwatch, queue
    deposited = False
    while landwatch.STATE.get("stage") and not queue.waiting(unit):
        due = landwatch.due_in()
        if due is not None and due <= 0:
            break
        m, _ = api.me(unit)
        if not m:
            time.sleep(15)
            continue
        if m.get("hp", 0) <= 0:
            time.sleep(60)
            continue
        if nav.at_tribe(m):
            if not deposited:
                deposit_all(unit)
                deposited = True
            state.note(unit, "部落等待腐化之地")
            time.sleep(30)
            continue
        if depositable_goods(m) > rules.RECOVER_TRIBE_GOODS or m["hp"] < m["max_hp"] * rules.HEALED:
            if go(unit, patience=0, force=force):
                deposit_all(unit)
                deposited = True
                continue
            if m.get("ring") != "camp" and not queue._go_home(unit):   # the way stayed closed: wait safely in the camp
                return False
            continue
        if m.get("ring") != "camp":
            if not queue._go_home(unit):
                return False
            continue
        state.note(unit, "营地等待腐化之地")
        time.sleep(30)
    return True


def path_safe():
    from . import route
    return route.safe()


def go(unit, patience=None, force=False, bail=None, min_hp=0, until=None):
    """To the tribe land on foot. True once there; False when the way stayed closed for `patience` seconds, every way in
    is guarded, `until` said stop, or this hero has no waypoint into the highlands or the forest yet. `force`: whatever
    the road looks like."""
    m, _ = api.me(unit)
    if not m:
        return False
    if nav.at_tribe(m):   # on the tribe's own land: the store and the training ground work here
        return True
    import sys as _sys, os as _os
    f = _sys._getframe(1)   # the owner (2026-11-04): every trip to the tribe is explained in the log
    api.info("[tribe] %s walks to the tribe: called by %s.%s (hp %s/%s, ap %s)" % (
        "main" if not unit else unit, _os.path.basename(f.f_code.co_filename), f.f_code.co_name, m["hp"], m["max_hp"], m["ap"]))
    if not nav.can_reach_tribe(m):
        api.warning("[%s] no waypoint into the tribe's rings yet" % (api.who(unit)))
        return False
    try:
        nav.walk_to_tribe(unit, min_hp=min_hp, patience=patience, force=force, bail=bail, until=until)
        return True
    except (nav.RouteClosed, nav.Bailed):
        return False


def visit(unit, train=0, heal=False, patience=None, max_minutes=25, force=False, bail=None):
    """One trip: go, store and restock, then train up to `train` times (and, with `heal`, stay until rules.HEALED; the land
    heals 6 a minute), then home. Returns False when the hero never got there. `direct`: travel straight to the
    """
    if not go(unit, patience, force, bail):
        return False
    deposit_all(unit)
    nav.walk(unit, nav.TRAINING, 2)
    trained, until = 0, time.time() + max_minutes * 60
    while time.time() < until:
        m, _ = api.me(unit)
        if not m:
            time.sleep(15)
            continue
        healed = m["hp"] >= m["max_hp"] * rules.HEALED
        if (trained >= train or not has_quota(unit)) and (not heal or healed):
            break
        if care.eat(unit, m):
            time.sleep(6)
            continue
        care.dust_repair(unit, m)   # (the owner, 2026-11-05) under 30% durability: repaired with dust at the camp or the tribe
        if has_quota(unit) and (trained < train or heal and not healed):  # training while the land heals
            fresh, _ = api.me(unit)
            if not fresh:
                time.sleep(15)
                continue
            api.wait_busy(fresh)
            code, r = train_once(unit)
            t = r.get("today") or {}
            if code == 200:
                trained += 1
            elif not has_quota(unit):
                pass  # the server confirmed that today's training is exhausted
            else:
                api.warning("[%s] training failed (%s): stopping this visit" % (api.who(unit), code))
                return False
            state.note(unit, "部落训练 %s/%s" % (t.get("trained"), t.get("most")))
            api.info("[%s] trains at the tribe (hp %s/%s): %s %s/%s" % (api.who(unit), m["hp"], m["max_hp"], code, t.get("trained"), t.get("most")))
        else:
            api.debug("[%s] resting on the tribe land hp %s/%s" % (api.who(unit), m["hp"], m["max_hp"]))
        time.sleep(62)
    api.act(unit, {"action": "home"})
    api.info("[%s] done at the tribe: back to the camp" % (api.who(unit)))
    return True

# --- mustering: everybody at the camp with enough points, then off to the tribe together --------------------------------
_muster = {"at": 0.0, "ready": False, "text": ""}


def _everyone_ready(ap, group=None):
    """Has every hero (a) reached the camp with `ap` points, or (b) already stands on the tribe land? Looked at most every
    45 seconds for all the waiting heroes together."""
    import threading
    now = time.time()
    if now - _muster["at"] < 45 and _muster.get("group") == group:
        return _muster["ready"]
    _muster["at"] = now
    _muster["group"] = group
    units = list(group) if group else [u for sq in rules.SQUADS.values() for u in sq["members"]]
    waiting = []
    for u in units:
        m, _ = api.me(u)
        if not m:
            waiting.append("%s?" % (api.who(u)))
        elif nav.at_tribe(m) or (m["ring"] == "camp" and m["ap"] >= ap and m["hp"] > 0):
            continue
        else:
            waiting.append("%s %s ap%d" % (api.who(u), m["ring"], m["ap"]))
    _muster["ready"] = not waiting
    _muster["text"] = ", ".join(waiting)
    return _muster["ready"]


def muster(unit, ap=70, squad=False):
    """Go to the camp (8 points) and wait there until EVERYBODY is at the camp with `ap` points; then True (the next order,
    `gather`, takes them to the tribe together)."""
    last = [0.0]
    while True:
        m, _ = api.me(unit)
        if m and m["hp"] > 0 and not nav.at_tribe(m) and m["ring"] != "camp" and m["ap"] >= 8:
            api.act(unit, {"action": "home"})
        from . import queue
        if queue.waiting(unit):   # another order is queued behind the muster (`gather`): the others have left already
            return True
        group = next((tuple(sq["members"]) for sq in rules.SQUADS.values() if unit in sq["members"]), None) if squad else None
        if _everyone_ready(ap, group):
            api.info("[muster %s] everybody is ready: off to the tribe" % (api.who(unit)))
            return True
        if time.time() - last[0] > 300:
            last[0] = time.time()
            api.debug("[muster %s] waiting for: %s" % (api.who(unit), _muster["text"]))
        time.sleep(30)

def raise_totem(unit):
    """Work on the totem while the tribe raises it (`work` keeps a hero building 5 minutes within 2 tiles): stands beside the
    totem and sends it again every 5 minutes until the totem has reached its next level or another order comes."""
    from . import queue
    last = [0.0]
    while True:
        if queue.waiting(unit):
            return True
        t = (info() or {})
        totem = next((b for b in t.get("building_list", []) if b["kind"] == "totem"), None)
        if not totem or not (t.get("totem") or {}).get("raising"):
            api.info("[%s] the totem is no longer being raised: done" % (api.who(unit)))
            return True
        m, _ = api.me(unit)
        if m and m["hp"] > 0:
            if max(abs(m["x"] - totem["x"]), abs(m["y"] - totem["y"])) > 2:
                api.act(unit, {"action": "move", "to": [totem["x"] + 1, totem["y"] + 1], "until_there": True})
            code, r = api.act(unit, {"action": "work", "building": totem["id"]})
            if time.time() - last[0] > 600 or code != 200:
                last[0] = time.time()
                api.info("[%s] works on the totem: %s %s" % (api.who(unit), code, str(api.clean(r))[:160]))
        time.sleep(240)


def gather(unit, force=False):
    """The owner's order "everybody gathers at the tribe": walk there (the route's own rules, round the strong creatures;
    health plays no part: no turning back), store and restock, then STAY: train, eat and heal on the
    tribe land. It never goes home by itself; it ends when another order is queued for the hero (e.g. `home`)."""
    from . import queue
    other_order = lambda: queue.waiting(unit)
    while True:
        # health plays no part; a closed way: wait where the hero stands (no trip home: it costs the points the walk
        # needs) and try again; a new order for the hero ends the gathering, even halfway
        if go(unit, 0, force, None, 0, until=other_order):
            break
        if other_order():
            api.info("[%s] gather ends on the way: another order is waiting" % (api.who(unit)))
            return True
        time.sleep(120)
    deposit_all(unit)
    nav.walk(unit, nav.TRAINING, 2)
    api.info("[%s] gathered at the tribe" % (api.who(unit)))
    while not queue.waiting(unit):
        m, _ = api.me(unit)
        if not m:
            time.sleep(15)
            continue
        if not nav.at_tribe(m):   # carried off (a fall, a home): walk back
            if not go(unit, 0, force, None, 0, until=other_order):
                time.sleep(60)
            continue
        care.eat(unit, m)
        care.spend_talents(unit, m)   # a level gained in training brings a point
        care.auto_repair(unit, m)   # (the squads, which do this for hunters, are not looking after a gathered hero)
        if has_quota(unit):
            code, r = train_once(unit)
            if code == 200:
                t = r.get("today") or {}
                state.note(unit, "部落训练 %s/%s" % (t.get("trained"), t.get("most")))
                api.info("[%s] trains at the tribe (hp %s/%s): %s/%s" % (api.who(unit), m["hp"], m["max_hp"], t.get("trained"), t.get("most")))
        time.sleep(62)
    api.info("[%s] gather ends: another order is waiting" % (api.who(unit)))
    return True


def construct(unit, kinds=("storehouse", "mill", "quarry", "infirmary", "workbench")):
    """The owner's order (2026-11-04): the main character walks to the tribe, stores everything it carries, then builds `kinds` ALONE,
    one after the other (paid from the store), working on each until it stands. Returns True (the order is over, done or not)."""
    who = api.who(unit)
    m0, _v = api.me(unit)
    if m0 and not nav.at_tribe(m0):
        # the owner (2026-11-04): TELEPORT to the totem (10 coins and 8 points, from anywhere): no walk through the guarded ground
        code, r = api.act(unit, {"action": "teleport", "to": "totem"})
        api.info("[construct %s] teleports to the totem: %s %s" % (who, code, "" if code == 200 else str(r.get("error"))[:120]))
        if code == 200:
            m1, _v = api.me(unit)
            api.wait_busy(m1)
    if not go(unit, patience=900):   # (already there after the teleport: True at once; else on foot as before)
        api.warning("[construct %s] the way to the tribe stayed closed: nothing built" % who)
        return True
    deposit_for(unit, kinds)
    for kind in kinds:
        t = info() or {}
        existing = next((b for b in t.get("building_list") or [] if b["kind"] == kind), None)
        if existing and existing.get("status") == "done":
            api.info("[construct %s] %s is there already" % (who, kind))
            continue
        if existing:   # placed earlier and not finished: just work on it
            api.info("[construct %s] %s stands half built: works on it" % (who, kind))
            bid = existing["id"]
            totem = next((b for b in t["building_list"] if b["kind"] == "totem"), None)
            until = time.time() + 25 * 60
            while time.time() < until:
                if totem:
                    mm, _v = api.me(unit)
                    if mm and max(abs(mm["x"] - existing.get("x", totem["x"])), abs(mm["y"] - existing.get("y", totem["y"]))) > 2:
                        api.act(unit, {"action": "move", "to": [existing.get("x", totem["x"]) + 1, existing.get("y", totem["y"]) + 1], "until_there": True})
                api.act(unit, {"action": "work", "building": bid})
                time.sleep(240)
                if building_done(bid):
                    api.notice("[construct %s] %s stands" % (who, kind))
                    break
            continue
        deposit_for(unit, (kind,))   # (the store's slots were full at first: what each building lacks is stored just before it)
        t = info() or {}
        store = t.get("store") or {}
        needs = {b["kind"]: b["needs"] for b in t.get("can_build") or []}.get(kind)
        missing = {r: n - store.get(r, 0) for r, n in (needs or {}).items() if store.get(r, 0) < n}
        if not needs or missing:
            api.warning("[construct %s] %s cannot be paid yet: needs %s, missing %s" % (who, kind, needs, missing))
            continue
        totem = next((b for b in t["building_list"] if b["kind"] == "totem"), None)
        if totem:
            api.act(unit, {"action": "move", "to": [totem["x"] + 1, totem["y"] + 1], "until_there": True})
        bid = build(kind, members=(unit,), builder=unit)
        api.info("[construct %s] builds %s: %s" % (who, kind, bid))
        if not bid:
            continue
        until = time.time() + 25 * 60
        while time.time() < until:   # each `work` keeps it building for five minutes
            code, r = api.act(unit, {"action": "work", "building": bid})
            time.sleep(240)
            if building_done(bid):
                api.notice("[construct %s] %s stands" % (who, kind))
                break
    api.notice("[construct %s] finished: store %s" % (who, {k: v for k, v in ((info() or {}).get("store") or {}).items() if k in ("wood", "stone", "ore")}))
    return True


def deposit_for(unit, kinds):
    """Store only what the buildings still lack (the level-2 store has 40 slots: depositing the whole bag was refused, 409), the
    scarcest first; on a refusal the amount is halved."""
    t = info() or {}
    store = t.get("store") or {}
    built = {b["kind"] for b in t.get("building_list") or []}
    cost = {b["kind"]: b["needs"] for b in t.get("can_build") or []}
    need = {}
    for kind in kinds:
        if kind not in built:
            for r, n in (cost.get(kind) or {}).items():
                need[r] = need.get(r, 0) + n
    m, _v = api.me(unit)
    bag = (m or {}).get("bag") or {}
    for r in ("stone", "wood", "ore"):
        qty = min(max(0, need.get(r, 0) - store.get(r, 0)), bag.get(r, 0))
        while qty > 0:
            code, res = api.act(unit, {"action": "deposit", "items": {r: qty}})
            api.info("[%s] deposits %s %s for the buildings: %s %s" % (api.who(unit), qty, r, code, "" if code == 200 else str(res.get("error"))[:120]))
            if code == 200:
                break
            qty //= 2


def _build_by_walking(kind, members, builder):
    """`build` wants "an open tile next to you": round the totem every tile beside the builder was taken (trees, ore, buildings),
    so it walks to a spot that has open ground beside it and builds there."""
    m, a = api.me(builder)
    if not m:
        return None
    tl, rows = a["top_left"], a["map"]

    def open_at(x, y):
        j, i = y - tl[1], x - tl[0]
        return 0 <= j < len(rows) and 0 <= i < len(rows[j]) and rows[j][i] == "."
    cands = []
    for dx in range(-6, 7):
        for dy in range(-6, 7):
            sx, sy = m["x"] + dx, m["y"] + dy
            if not open_at(sx, sy):
                continue
            nbrs = [(sx + ex, sy + ey) for ex in (-1, 0, 1) for ey in (-1, 0, 1) if (ex or ey) and open_at(sx + ex, sy + ey)]
            if len(nbrs) >= 3:   # room for a building of more than one tile
                cands.append((max(abs(dx), abs(dy)), sx, sy, nbrs[0]))
    cands.sort()
    for _d, sx, sy, (tx, ty) in cands[:8]:
        code, r = api.act(builder, {"action": "move", "to": [sx, sy], "until_there": True})
        mm, _v = api.me(builder)
        api.wait_busy(mm)
        code, r = api.act(builder, {"action": "build", "kind": kind, "at": [tx, ty]})
        api.info("build %s from (%s,%s) at (%s,%s): %s %s" % (kind, sx, sy, tx, ty, code, r.get("error") or ""))
        if code in (200, 202):   # 202: queued behind the walk: it is placed a moment later, the building's id comes from the tribe's list
            bid = r.get("building")
            for _ in range(10):
                if bid:
                    break
                time.sleep(6)
                bid = next((b["id"] for b in (info() or {}).get("building_list") or [] if b["kind"] == kind and b.get("status") != "done"), None)
            for u in members:
                api.act(u, {"action": "work", "building": bid})
            return bid
    return None
