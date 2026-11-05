"""Looking after a character: food, talent points, gear (identify, equip, repair)."""
import json
import os
import re
import threading
import time

from . import api, rules


def eat(unit, m, below=rules.EAT_BELOW):
    """Eat when food is under `below`, or when hurt and food leaves room: roast first, then bread, else roast some meat
    first (raw meat is never eaten). True if it ate."""
    bag = m.get("bag") or {}
    if m["food"] < below or (m["hp"] < m["max_hp"] * 0.5 and m["food"] <= 85):
        for f in ("roast", "bread"):  # roast +25 food and +15 hp (the owner: first), bread +40
            if bag.get(f):
                api.act(unit, {"action": "eat", "item": f})
                return True
        if bag.get("meat") and m.get("ap", 0) >= 3 + 5:
            return roast_and_eat(unit, bag["meat"])
    return False


HEAL_ITEMS = ("bandage", "bread", "roast")   # the owner: bandage > bread > roast


def has_heal_items(m, items=HEAL_ITEMS):
    bag = m.get("bag") or {}
    return any(bag.get(i) for i in items)


def heal_with_items(unit, m, below=rules.HEAL_BELOW, target=rules.HEAL_TO, items=HEAL_ITEMS):
    """Hurt below `below` of the maximum: eat bread (about 25 health each, whatever the hunger) up to `target`. Returns the
    number eaten (the forest squad only, the owner's order)."""
    bag = m.get("bag") or {}
    if m["hp"] <= 0 or m["hp"] >= below * m["max_hp"]:
        return 0
    eaten = 0
    for item in items:
        n = bag.get(item) or 0
        while m["hp"] < target * m["max_hp"] and n > 0 and eaten < 15:
            code, r = api.act(unit, {"action": "eat", "item": item})
            if code != 200 or r.get("hp") is None or r["hp"] <= m["hp"]:
                break
            m["hp"], n, eaten = r["hp"], n - 1, eaten + 1
        bag[item] = n
    if eaten:
        api.info("[%s] ate %d bandage/bread/roast: hp %d/%d (%d bandage, %d bread left)" % (api.who(unit), eaten, m["hp"], m["max_hp"], bag.get("bandage") or 0, bag.get("bread") or 0))
    return eaten


def restock_bread(unit, m):
    """The bread is all eaten and the hero stands in the camp: ask the quartermaster again (5 bread per 100 deeds, at most 20
    packs a call) if any deeds are left; with none, the hero simply rests (owner 01:08). True if bought."""
    if m["ring"] != "camp" or (m.get("bag") or {}).get("bread"):
        return False
    me, _ = api.me(None)   # the deeds are the account's (the main character's read carries them)
    deeds = ((me or {}).get("prize") or {}).get("deeds_banked") or 0
    if _bandages_short(me):   # bandages for the lord and the main character come first (the owner's stock order)
        return False
    packs = min(20, deeds // 100)
    if packs < 1:
        return False
    code, r = api.act(unit, {"action": "requisition", "item": "bread", "qty": packs})
    api.info("[%s] requisition %d packs of bread: %s %s" % (api.who(unit), packs, code,
                                                       str(r.get("got") or r.get("error"))[:80]))
    if code == 200:
        m.setdefault("bag", {})["bread"] = packs * 5
    return code == 200


def _bandages_short(me):
    """True while the lord or the main character is under rules.BANDAGE_WANT and the quartermaster could still sell a pack."""
    want = rules.BANDAGE_WANT
    if not want or not rules.AUTO_RESTOCK_BANDAGE:
        return False
    code, q = api.call("GET", "/world/quartermaster")
    if code != 200 or (q.get("deeds_banked") or 0) < 150:
        return False
    if ((me or {}).get("bag") or {}).get("bandage", 0) < want.get(None, 0):
        return True
    lord, _ = api.me(rules.LORD)
    return bool(lord) and ((lord.get("bag") or {}).get("bandage", 0) < want.get(rules.LORD, 0))


def restock_bandage(unit, m):
    """In the camp: top the bandages up to rules.BANDAGE_WANT (3 per 150 deeds at the quartermaster). The main character
    waits until the lord is stocked (the owner's order: the lord 30 first, then the main character 60)."""
    want = rules.BANDAGE_WANT.get(unit, 0)
    if not want or not rules.AUTO_RESTOCK_BANDAGE or m["ring"] != "camp":
        return False
    have = (m.get("bag") or {}).get("bandage", 0)
    if have >= want:
        return False
    if unit is None:
        lord, _ = api.me(rules.LORD)
        if lord and ((lord.get("bag") or {}).get("bandage", 0) < rules.BANDAGE_WANT.get(rules.LORD, 0)):
            return False   # the lord is stocked first
    code, q = api.call("GET", "/world/quartermaster")
    deeds = (q.get("deeds_banked") or 0) if code == 200 else 0
    packs = min((want - have + 2) // 3, deeds // 150)
    if packs < 1:
        return False
    code, r = api.act(unit, {"action": "requisition", "item": "bandage", "qty": packs})
    api.info("[%s] requisition %d packs of bandages: %s %s" % (api.who(unit), packs, code,
                                                             str(r.get("got") or r.get("error"))[:80]))
    if code == 200:
        m.setdefault("bag", {})["bandage"] = have + packs * 3
    return code == 200


def share_bread(units, low=30):
    """A member with fewer than `low` bread gets half of the surplus of a squadmate standing within 3 tiles (the owner: the
    forest pair asks each other when one runs out)."""
    for u, m in units.items():
        have = (m.get("bag") or {}).get("bread") or 0
        if have >= low:
            continue
        for v, o in units.items():
            other = (o.get("bag") or {}).get("bread") or 0
            if v == u or other <= low * 2 or max(abs(o["x"] - m["x"]), abs(o["y"] - m["y"])) > 3:
                continue
            qty = (other - have) // 2
            code, _r = api.act(v, {"action": "give", "to": "main" if u is None else u, "goods": "bread", "qty": qty})
            api.info("[%s] gives %d bread to %s: %s" % (api.who(v), qty, api.who(u), code))
            if code == 200:
                m.setdefault("bag", {})["bread"] = have + qty
                o["bag"]["bread"] = other - qty
            break


def roast_and_eat(unit, meat):
    """Cook roast from the meat (3 points a batch of up to 10, anywhere) and eat one. True if it ate."""
    qty = min(meat, rules.ROAST_BATCH)   # a few at a time: the rest stays meat for the store and the squad
    code, r = api.act(unit, {"action": "cook", "recipe": "roast", "qty": qty})
    api.info("[%s] roast %d meat: %s %s" % (api.who(unit), qty, code, "" if code == 200 else r.get("error")))
    if code != 200:
        return False
    api.wait_busy(api.me(unit)[0])
    api.act(unit, {"action": "eat", "item": "roast"})
    return True


TALENT_ORDER = ("sharp", "tough", "hawk", "slayer", "stride", "bounty", "thrift", "green", "prospector", "mason", "salvager", "artisan")


def spend_talents(unit, m):
    """Every free point goes to the first talent of TALENT_ORDER that is open and not at its most. It used to name only sharp and tough,
    so once both stood at 5 ranks every try was refused (400 'is at its most') and the points lay unspent (928 held 2): the
    talents come from `me.talents` now: a talent opens when its PATH holds `opens_at` points, and takes `max` ranks."""
    tal = m.get("talents") or {}
    pts = tal.get("points", 0)
    if not pts:
        return
    spent = dict(tal.get("paths") or {})
    ranks = {t["id"]: t for t in tal.get("talents") or []}
    for _ in range(10):
        if pts <= 0:
            return
        pick = None
        for name in TALENT_ORDER:
            t = ranks.get(name)
            if t and t["rank"] < t["max"] and spent.get(t["path"], 0) >= t.get("opens_at", 0):
                pick = t
                break
        if not pick:
            api.debug("  %s has %d talent point(s) and nothing open to take them" % (api.who(unit), pts))
            return
        n = min(pts, pick["max"] - pick["rank"])
        code, r = api.act(unit, {"action": "talent", "talent": pick["id"], "points": n})
        api.info("  %s takes %d point(s) in %s: %s %s" % (api.who(unit), n, pick["id"], code, "" if code == 200 else str(r.get("error"))[:80]))
        if code != 200:
            return
        pts -= n
        pick["rank"] += n
        spent[pick["path"]] = spent.get(pick["path"], 0) + n


TIERS = {"plain": 0, "runed": 1, "treasured": 2, "set": 2, "mythic": 3}   # iron < silver < gold < beyond: the grade decides first
# what one point of an affix is worth next to one point of armour/damage (an unknown affix counts 0.2)
AFFIX_WEIGHT = {"damage": 0.5, "crit": 1.5, "critDamage": 0.2, "beast": 0.25, "undead": 0.25, "slayer": 0.25, "hunter": 0.25,
                "boss": 0.25, "grit": 1.0, "hp": 0.05, "armor": 0.5, "xp": 0.3, "magicFind": 0.2, "thorns": 0.1}


def power(item):
    """Grade first (a better grade always beats a worse one), then armour/damage plus the affixes by their VALUES (a
    count of affixes said a 3-affix piece with weak values beat a 2-affix piece with +34% damage)."""
    if not item:
        return -1
    base = item.get("armor") or item.get("damage") or 0
    return 100 * TIERS.get(item.get("rarity"), 0) + base + sum(
        AFFIX_WEIGHT.get(a.get("id"), 0.2) * (a.get("value") or 0) for a in item.get("affixes") or [])


def score(item, m=None, worn=False):
    """The game's own gear score (power, 'what it adds against same-level creatures'): a worn piece carries `worth.power`; a
    bag piece carries `vs_worn.power`, the change over what the same slot wears now, so its worth is that slot's worth plus
    the change. Pieces the server gave no score (or an old read) fall back to our own estimate."""
    if not item:
        return -1
    if item.get("broken"):
        return -1
    if worn:
        w = (item.get("worth") or {}).get("power")
        return w if w is not None else power(item)
    d = (item.get("vs_worn") or {}).get("power")
    if d is not None and m is not None:
        return (((m.get("worn") or {}).get(item.get("slot")) or {}).get("worth") or {}).get("power", 0) + d
    return power(item)


_equip_cooldown = {}  # (unit, slot) -> monotonic deadline, learned from the server or a successful change
_equip_lock = threading.Lock()
_TWO_HANDED = {"maul", "bow", "longbow"}


def auto_equip(unit, m):
    """Wear any piece the game scores above the worn one (an empty slot counts as 0), up to 2 levels above us, not broken."""
    worn = m.get("worn") or {}
    for g in m.get("gear_bag") or []:
        if g.get("broken") or g["level"] > m["level"] + 2 or g.get("unidentified"):
            continue
        if g.get("slot") in rules.LOCKED_SLOTS.get(unit, ()):   # a locked slot keeps what it wears
            continue
        slot = g["slot"]
        comparison = g.get("vs_worn") or {}
        # A missing power score can mean the server rejected the combination, not that the item has no score.
        if comparison.get("two_handed_weapon") or (slot == "offhand" and (worn.get("weapon") or {}).get("base") in _TWO_HANDED):
            continue
        if slot == "weapon" and g.get("base") in _TWO_HANDED and worn.get("offhand"):
            continue
        with _equip_lock:
            if _equip_cooldown.get((unit, slot), 0) > time.monotonic():
                continue
        d = comparison.get("power")
        better = d > 0 if d is not None else power(g) > power(worn.get(g["slot"]))
        if better:
            code, r = api.act(unit, {"action": "equip", "item": g["id"]})
            api.info("  %s equips %s L%s %s: %s %s" % (api.who(unit), g["base"], g["level"], g["rarity"], code,
                                                      "" if code == 200 else json.dumps(api.clean(r), ensure_ascii=False)[:200]))
            if code == 200:
                with _equip_lock:
                    _equip_cooldown[(unit, slot)] = time.monotonic() + 300
                worn[g["slot"]] = g
                return
            if code == 409 and "changed lately" in str(r.get("error", "")):
                match = re.search(r"changes again in\s+(\d+)\s*s", str(r.get("error", "")))
                seconds = int(match.group(1)) + 1 if match else 300
                with _equip_lock:
                    _equip_cooldown[(unit, slot)] = time.monotonic() + seconds


def identify():
    """Runed and better drops are unidentified and count for nothing until read at the camp (main must be there)."""
    code, r = api.act(None, {"action": "identify", "item": "all"})
    return code, [g.get("name") for g in r.get("identified") or []]


def repair(unit, item_id):
    """Repair one piece (works anywhere since skill.md 0.24.12; costs coins, a few for a basic piece)."""
    code, r = api.act(unit, {"action": "repair", "item": item_id})
    return code, r.get("error") or r.get("cost")


DUST_BELOW = 0.30   # the owner (2026-11-05): at the camp or the tribe, worn pieces under 30% durability are repaired with dust


def dust_repair(unit, m):
    """At the camp or on the tribe's land: every worn piece (tools too) under DUST_BELOW of its durability is repaired with
    dust (`with: dust`), while the hero has some."""
    from . import nav
    if not (m.get("ring") == "camp" or nav.at_tribe(m)):
        return
    for slot, g in (m.get("worn") or {}).items():
        top = g.get("durability_max") or 0
        if not top or g.get("durability") is None or g["durability"] >= DUST_BELOW * top:
            continue
        if not (m.get("bag") or {}).get("dust"):
            return
        code, r = api.act(unit, {"action": "repair", "item": g["id"], "with": "dust"})
        api.info("  %s repairs %s with dust (%s/%s): %s %s" % (api.who(unit), g.get("base"), g["durability"], top, code,
                                                          str(r.get("error") or r.get("cost") or "")[:80]))
        if code == 200:
            g["durability"] = top
            m["bag"]["dust"] = max(0, m["bag"].get("dust", 0) - (r.get("cost") if isinstance(r.get("cost"), int) else 1))


def auto_repair(unit, m, min_coins=8):
    """Repair every worn piece that is broken (broken gear counts for nothing); never tools
    (nobody farms or mines); first, at the camp or the tribe, the pieces under 30% are repaired with dust."""
    dust_repair(unit, m)
    if m.get("coins", 0) < min_coins:
        return
    for slot, g in (m.get("worn") or {}).items():
        if slot == "tool":
            continue
        top = g.get("durability_max") or 0
        if top and g.get("durability") is not None and (g["durability"] <= 0 or g.get("broken")):   # only once broken (the owner, 2026-11-05)
            code, res = repair(unit, g["id"])
            api.info("  %s repairs %s (%s/%s): %s %s" % (api.who(unit), g["base"], g["durability"], top, code, res))
            if code == 200:
                m["coins"] = m.get("coins", 0) - (res if isinstance(res, int) else 8)
            if m.get("coins", 0) < min_coins:
                return


def needs_repair(m):
    for slot, g in (m.get("worn") or {}).items():
        top = g.get("durability_max") or 0
        if slot != "tool" and top and g.get("durability") is not None and (g["durability"] <= 0 or g.get("broken")):
            return True
    return False


def lend_for_repair(unit, m, others, need=12, gift=10, keep=12):
    """A hero with worn-out gear and too few coins gets `gift` coins from a squadmate within 3 tiles who can spare it.
    True if coins were handed over."""
    if m.get("coins", 0) >= need or not needs_repair(m):
        return False
    for u, o in others.items():
        if u == unit or o.get("coins", 0) < gift + keep:
            continue
        if max(abs(o["x"] - m["x"]), abs(o["y"] - m["y"])) > 3:
            continue
        code, _ = api.act(u, {"action": "give", "to": api.who(unit), "coins": gift})
        api.info("  %s lends %d coins to %s for repairs: %s" % (api.who(u), gift, api.who(unit), code))
        if code == 200:
            o["coins"] -= gift
            m["coins"] = m.get("coins", 0) + gift
            return True
    return False


# --- the daily chest (the owner, 2026-11-04) ----------------------------------------------------------------------------
# Once a day, after 12:00 (UTC+8), when the main character happens to stand in the camp (it never walks there for this):
# bank the chest, `pay` mode = the owner's "80% tier" (four fifths kept, a fifth is tribute). Banking is bookkeeping inside the
# game; sending the banked goods to a wallet stays the owner's own business (never done here).
CHEST_MODE = "pay"
CHEST_AFTER_HOUR = 0   # 12 a.m. UTC+8 = 00:00: the new game day has begun (the owner)
_CHEST_FILE = os.path.join(api.HERE, "daily.json")
_chest = {"day": None, "try": 0.0}


def _cn_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


def claim_chest(unit, m):
    """Cheap to call whenever the main character is read: acts at most once a day. Returns True if it banked."""
    if unit is not None or m.get("ring") != "camp" or m.get("hp", 0) <= 0:
        return False
    # the owner: "after 12 a.m. UTC+8" = after MIDNIGHT UTC+8, the turn of the game's day (the chest fills from then on): once per game day
    now = _cn_now()
    if now.hour < CHEST_AFTER_HOUR:
        return False
    day = now.date().isoformat()
    if _chest["day"] is None:
        try:
            with open(_CHEST_FILE, encoding="utf-8") as f:
                _chest["day"] = json.load(f).get("chest_day")
        except (OSError, ValueError):
            _chest["day"] = ""
    if _chest["day"] == day or time.time() - _chest["try"] < 1800:
        return False
    _chest["try"] = time.time()
    chest = (m.get("prize") or {}).get("chest") or {}
    if not any(float(v or 0) > 0 for v in chest.values()):   # nothing in it: that is today's answer too
        _chest["day"] = day
        _save_chest(day)
        api.info("[chest] %s: the chest is empty: nothing to bank" % day)
        return False
    api.notice("[chest] %s: the main character is at the camp after %d:00 UTC+8: banking the chest %s with mode %s (the 80%% tier)" % (
        day, CHEST_AFTER_HOUR, chest, CHEST_MODE))
    code, r = api.act(None, {"action": "bank", "mode": CHEST_MODE})
    api.notice("[chest] bank answer: %s %s" % (code, json.dumps(api.clean(r), ensure_ascii=False)[:300]))
    if code == 200:
        _chest["day"] = day
        _save_chest(day)
        return True
    return False


def _save_chest(day):
    try:
        with open(_CHEST_FILE, "w", encoding="utf-8") as f:
            json.dump({"chest_day": day}, f)
    except OSError:
        pass
