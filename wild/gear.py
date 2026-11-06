"""Spreading the gear over each squad. Every few minutes the pieces the heroes of one squad own (worn or in the bag) are
pooled and dealt out again, best piece of each slot to the hero that matters most (the demon lord, the main character,
then by level); a hero keeps what it wears unless a clearly better piece (+GAIN) is around. The squads hunt together, so
the pieces change hands where they stand: the receiver steps next to the giver (no trip to the camp), the giver takes
the piece off and gives it, the receiver puts it on. A worn piece leaves only against a replacement (a swap in the
same meeting, or a spare in the giver's bag). Pieces nobody in the squad needs go on the market (sell_surplus).
Runes and seals are never put on gear by themselves (rules.USE_CURRENCIES)."""
import time

from . import api, care, lease, rules

LORD = rules.LORD
GAIN = rules.GEAR_GAIN  # a piece must beat the worn one by this much (empty slot: any piece)
NEAR = 3            # `give` works within 3 tiles
MAX_WALK = 20       # a receiver farther than this from the giver waits for the next round
SKIP_SLOTS = ("tool",)   # nobody farms or mines: tools are not dealt out


GRADES = {"demon_lord": 3, "gold": 2, "silver": 1, "iron": 0}   # the hero's own grade (the NFT): gold > silver > iron


def _priority(unit, m):
    """The hero's own grade first (gold, silver, iron; the demon lord on top), the level second, then the attack. The gear a hero happens to
    wear plays no part (the owner's order)."""
    h = m.get("hero") or {}
    rank = rules.GEAR_PRIORITY.index(unit) if unit in rules.GEAR_PRIORITY else len(rules.GEAR_PRIORITY)
    return (rank, -GRADES.get(h.get("grade") or h.get("kind"), 0), -m["level"], -(m.get("attack") or 0))


def _usable(g, m):
    return not g.get("unidentified") and g["level"] <= m["level"] + 2 and care.compatible_hands(g, m)


def survey(members):
    """{unit: me} for the given units (None is the main character)."""
    out = {}
    for u in members:
        m, _ = api.me(u)
        if m:
            out[u] = m
    return out


def plan(heroes):
    """[(giver, receiver, item, slot_if_worn_by_giver, gain)] that improves the group's gear, best gains first."""
    pool = {}   # slot -> [(power, item, holder, worn?)]
    for u, m in heroes.items():
        for slot, g in (m.get("worn") or {}).items():
            if slot not in SKIP_SLOTS and slot not in rules.LOCKED_SLOTS.get(u, ()):   # a locked piece is not in the pool
                pool.setdefault(slot, []).append((care.score(g, m, worn=True), g, u, True))
        for g in m.get("gear_bag") or []:
            if g["slot"] not in SKIP_SLOTS:
                pool.setdefault(g["slot"], []).append((care.score(g, m), g, u, False))
    moves = []
    for slot, items in pool.items():
        left = sorted(items, key=lambda x: -x[0])
        gone = set()   # worn pieces already dealt to somebody else: their wearer stands empty-handed in this slot, whatever it wore
        for u in sorted(heroes, key=lambda u: _priority(u, heroes[u])):
            if slot in rules.LOCKED_SLOTS.get(u, ()):   # the locked slot is not dealt to anybody else either
                continue
            m = heroes[u]
            cur = (m.get("worn") or {}).get(slot)
            cur_p = care.score(cur, m, worn=True) if cur and cur["id"] not in gone else 0

            def keep_own():   # the hero keeps what it wears: nobody later in the line may take it
                for x in list(left):
                    if cur and x[1]["id"] == cur["id"]:
                        left.remove(x)
            best = next((x for x in left if x[0] >= 0 and _usable(x[1], m)
                         and (rules.AUTO_GIVE_MAIN or u is not None or x[2] is None)), None)
            if best is None:
                keep_own()
                continue
            left.remove(best)
            if best[2] == u:
                continue                      # already the hero's own worn piece
            if best[0] - cur_p >= GAIN:       # worth the step
                moves.append((best[2], u, best[1], slot if best[3] else None, best[0] - cur_p))
                if best[3]:
                    gone.add(best[1]["id"])   # (its wearer's old piece is out; it may stand empty-handed until its turn)
            else:
                left.append(best)             # keep it for somebody else
                left.sort(key=lambda x: -x[0])
                keep_own()
    moves.sort(key=lambda mv: -mv[4])
    return moves


def _dist(a, b):
    return max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))


def meet(giver, receiver, tries=3):
    """The receiver walks to the giver (who may be moving too) until they are within reach. True once they are."""
    for _ in range(tries):
        g, _ = api.me(giver)
        r, _ = api.me(receiver)
        if not g or not r:
            return False
        if _dist(g, r) <= NEAR:
            return True
        api.act(receiver, {"action": "move", "to": [g["x"], g["y"]], "until_there": True})
        time.sleep(5)
        api.wait_busy(api.me(receiver)[0])
    g, _ = api.me(giver)
    r, _ = api.me(receiver)
    return bool(g and r and _dist(g, r) <= NEAR)


def hand_over(giver, receiver, items, heroes):
    """Between two heroes that stand close (rules.GEAR_REACH): take off, give, put on. Nobody walks to meet."""
    if receiver is None and giver is not None and not rules.AUTO_GIVE_MAIN:
        return False
    g, r = heroes[giver], heroes[receiver]
    if g["hp"] <= 0 or r["hp"] <= 0 or _dist(g, r) > rules.GEAR_REACH:
        return False
    got = [u for u in (giver, receiver) if lease.take(u, "gear", secs=600)]
    try:
        if len(got) < 2:
            return False
        for item, worn_slot in items:
            if item.get("slot") in rules.LOCKED_SLOTS.get(giver, ()):   # belt and braces: a locked slot never leaves its owner
                continue
            if item.get("slot") in ("weapon", "offhand"):
                recipient, _ = api.me(receiver)
                if not recipient or not _usable(item, recipient):
                    api.debug("[gear] %s cannot currently equip %s; transfer skipped" % (api.who(receiver), item.get("base")))
                    return False
            fresh, _ = api.me(giver)
            if not fresh:
                return False
            api.wait_busy(fresh)
            if worn_slot:
                code, res = api.act(giver, {"action": "unequip", "slot": worn_slot, "wait": False})
                if code != 200:
                    if code == 409 and "changed lately" in (res.get("error") or ""):
                        api.debug("[gear] slot %s changed lately (cooldown): %s %s" % (worn_slot, code, res.get("error") or ""))
                    else:
                        api.warning("[gear] cannot unequip %s for transfer: %s %s" % (item.get("base"), code, res.get("error") or ""))
                    return False
                api.wait_result(res)
            code, res = api.act(giver, {"action": "give", "to": "main" if receiver is None else receiver, "item": item["id"], "wait": False})
            api.debug("[gear] %s %s L%s: %s -> %s: %s %s" % (item["base"], item.get("rarity"), item["level"], api.who(giver),
                                                         api.who(receiver), code, "" if code == 200 else res.get("error")))
            if code != 200:
                return False
            api.wait_result(res)
        for _ in range(len(items) + 1):  # one equip per act: put the pieces on one by one
            m, _ = api.me(receiver)
            if not m:   # the server refused the read (rate limit): the next round puts it on
                break
            api.wait_busy(m)
            care.auto_equip(receiver, m)
        return True
    finally:
        for u in got:
            lease.release(u, "gear")


def balance(groups, dry=False, everything=False):
    """One round (the timed one and the manual `wild.py gear` are the same): for every squad (a list of units), deal its
    gear out and hand over what changes hands between heroes standing within rules.GEAR_REACH of each other."""
    all_moves = []
    for members in groups:
        if len(set(members)) < 2:
            continue
        heroes = survey(members)
        moves = plan(heroes)
        all_moves += moves
        if dry:
            for g, r, item, slot, gain in moves:
                print("%s -> %s: %s %s L%s (+%.1f)" % (api.who(g), api.who(r), item["base"], item.get("rarity"), item["level"], gain))
            continue
        # a worn piece is given away only against a replacement that comes in the same meeting (the swap partner hands over
        # the same slot): otherwise the giver stands empty-handed if the other half of the plan is not carried out
        # (the demon lord lost its bow to 932 this way while its new weapon was out of reach)
        swaps = {(g, r, item["slot"]) for g, r, item, slot, gain in moves}

        def spare(u, slot):  # the giver has another whole piece of that slot in its bag: it puts that on
            return any(p.get("slot") == slot and not p.get("broken") and not p.get("unidentified") and p["id"] != item_id
                       for p in heroes[u].get("gear_bag") or [])
        keep = []
        for mv in moves:
            item_id = mv[2]["id"]
            if mv[3] is None or (mv[1], mv[0], mv[2]["slot"]) in swaps or spare(mv[0], mv[2]["slot"]):
                keep.append(mv)
        if everything:   # the whole ranking in one pass (everybody stands together): no swap guard, no cap on the meetings
            keep = moves
        moves = [mv for mv in keep if _dist(heroes[mv[0]], heroes[mv[1]]) <= rules.GEAR_REACH]   # only those standing close
        # one meeting per unordered pair carries both halves of a swap (933 once gave both its weapons away in
        # separate meetings and stood empty-handed: the other halves never happened)
        pairs = {}
        for g, r, item, slot, gain in moves:
            key = tuple(sorted((g, r), key=lambda x: -1 if x is None else x))
            pairs.setdefault(key, []).append((g, r, item, slot))
        for (a, b), parts in list(pairs.items())[:None if everything else 2]:   # a couple of meetings a squad and round
            if lease.leased(a, "gear") or lease.leased(b, "gear"):
                continue
            if not everything:   # a worn weapon/piece leaves only when the same slot comes back in this meeting
                back = {(g, r, it["slot"]) for g, r, it, s in parts}
                def _ok(p):
                    nonlocal item_id
                    item_id = p[2]["id"]
                    return p[3] is None or (p[1], p[0], p[2]["slot"]) in back or spare(p[0], p[2]["slot"])
                parts = [p for p in parts if _ok(p)]
            for giver in (a, b):
                items = [(it, s) for g, r, it, s in parts if g == giver]
                if items:
                    hand_over(giver, b if giver == a else a, items, heroes)
    return all_moves


def feed(groups, keep=rules.FOOD_KEEP):
    """Inside each squad, a hero with fewer than `keep` bread+meat is handed meat (up to `keep`) by a squadmate holding more
    than `keep`: the receiver steps next to the giver. What is left over beyond `keep` goes to the store on tribe visits."""
    for members in groups:
        heroes = survey(members)
        food = lambda m: sum((m.get("bag") or {}).get(k, 0) for k in ("meat", "bread", "roast"))
        for r, rm in heroes.items():
            if r is None and not rules.AUTO_GIVE_MAIN:
                continue
            if food(rm) >= keep or rm["hp"] <= 0 or rm["ap"] < 12 or lease.leased(r, "gear"):
                continue
            for g, gm in heroes.items():
                spare = (gm.get("bag") or {}).get("meat", 0) - keep
                if g == r or spare <= 0 or gm["hp"] <= 0 or _dist(gm, rm) > MAX_WALK or lease.leased(g, "gear"):
                    continue
                give = min(spare, keep - food(rm))
                got = [u for u in (g, r) if lease.take(u, "gear", secs=300)]
                try:
                    if len(got) < 2:
                        continue
                    if not meet(g, r):
                        continue
                    code, res = api.act(g, {"action": "give", "to": "main" if r is None else r, "goods": "meat", "qty": give})
                    api.debug("[gear] %d meat: %s -> %s: %s %s" % (give, api.who(g), api.who(r), code, "" if code == 200 else res.get("error")))
                    if code == 200:
                        m, _ = api.me(r)
                        care.eat(r, m, below=60)
                finally:
                    for u in got:
                        lease.release(u, "gear")
                break


MAX_LISTINGS = 3     # pieces listed per hero and round


def sell_surplus(groups):
    """The owner's rule: a bag piece that every squadmate already beats with what it wears goes on the market at the owner's
    price (rules.PRICE_*), never a treasured, mythic or set one. Selling works in the camp only: a hero outside waits."""
    if not rules.SELL_SURPLUS:
        return
    for members in groups:
        heroes = survey(members)
        for u, m in heroes.items():
            if m.get("ring") != "camp":
                continue
            listed = 0
            for g in m.get("gear_bag") or []:
                if listed >= MAX_LISTINGS:
                    break
                if g.get("rarity") in rules.NOT_FOR_SALE or g.get("unidentified") or g.get("slot") in SKIP_SLOTS:
                    continue
                p = care.score(g, m)
                beaten = all(((h.get("worn") or {}).get(g["slot"]) or {}).get("broken") is False
                             and care.score(h["worn"][g["slot"]], h, worn=True) >= p for h in heroes.values())
                if not beaten:
                    continue
                if g.get("rarity") not in rules.PRICE_BASE:
                    continue   # no price set for this grade: it stays in the bag
                price = rules.PRICE_BASE[g["rarity"]] + rules.PRICE_PER_AFFIX * len(g.get("affixes") or [])
                code, r = api.act(u, {"action": "sell", "item": g["id"], "price": price})
                api.info("[gear] %s lists %s %s L%s at %s: %s %s" % (api.who(u), g["rarity"], g["base"], g["level"], price, code,
                                                                  "" if code == 200 else r.get("error")))
                if code == 200:
                    listed += 1


def loop(groups):
    """The one periodic job (every rules.UPKEEP_EVERY seconds): talent points, food, gear dealing, market listings."""
    from . import abyss
    time.sleep(120)
    while True:
        try:
            if not abyss.PAUSE.is_set():
                for u in dict.fromkeys(u for members in groups for u in members):
                    m, _ = api.me(u)
                    if m:
                        care.spend_talents(u, m)
                        from . import nav
                        if m.get('ring') == 'camp' or nav.at_tribe(m):
                            care.auto_salvage(u, m)
                feed(groups)
                balance(groups)
                sell_surplus(groups)
        except Exception as e:
            api.error("[gear] error %r" % e)
        time.sleep(rules.UPKEEP_EVERY)


KEEP_COINS = rules.COIN_KEEP   # nobody carries more than this; the rest is handed over / banked at the tribe


def collect_coins():
    """The heroes standing beside the main character (within rules.GEAR_REACH) give it all their coins above KEEP_COINS."""
    if not rules.AUTO_GIVE_MAIN:
        return
    main, _ = api.me(None)
    if not main:
        return
    for u in [u for sq in rules.SQUADS.values() for u in sq["members"] if u is not None]:
        m, _ = api.me(u)
        if not m or m["hp"] <= 0 or _dist(m, main) > rules.GEAR_REACH:
            continue
        spare = (m.get("coins") or 0) - KEEP_COINS
        if spare > 0:
            code, r = api.act(u, {"action": "give", "to": "main", "coins": spare})
            api.debug("[coins] %s gives %d to the main character: %s %s" % (u, spare, code, "" if code == 200 else r.get("error")))


def collect_valuables():
    """The same hand-over for what a fall does not take away (the owner, 2026-11-05): the heroes standing beside the main
    character (within rules.GEAR_REACH) give it every rune, stone, seal, dust, crystal, shard and gem they carry (tribe.currencies,
    minus rules.KEEP_CURRENCIES)."""
    if not rules.AUTO_COLLECT_MAIN:
        return
    from . import tribe
    main, _ = api.me(None)
    if not main:
        return
    for u in [u for sq in rules.SQUADS.values() for u in sq["members"] if u is not None]:
        m, _ = api.me(u)
        if not m or m["hp"] <= 0 or _dist(m, main) > rules.GEAR_REACH:
            continue
        for name, qty in tribe.currencies(u, m).items():
            code, r = api.act(u, {"action": "give", "to": "main", "goods": name, "qty": qty})
            api.debug("[valuables] %s gives %d %s to the main character: %s %s" % (u, qty, name, code, "" if code == 200 else r.get("error")))


def coin_loop(every=300):
    """Job (the owner, 02:4x): every 5 minutes the heroes next to the main character hand over their coins above KEEP_COINS, and
    their runes, stones, gems ... (collect_valuables)."""
    time.sleep(150)
    while True:
        try:
            collect_coins()
        except Exception as e:
            api.error("[coins] error %r" % e)
        try:
            collect_valuables()
        except Exception as e:
            api.error("[valuables] error %r" % e)
        time.sleep(every)
