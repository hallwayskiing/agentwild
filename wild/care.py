"""Looking after a character: food, talent points, gear (identify, equip, repair)."""
import json
import os
import re
import threading
import time

from . import api, rules


def cook_roast(unit, m, max_batch=None):
    """Cook raw meat into roast (3 AP, 20s busy, batch of up to 10). Roast gives +25 food and +15 HP."""
    bag = m.get("bag") or {}
    meat = bag.get("meat") or 0
    if meat <= 0 or m.get("ap", 0) < 3:
        return 0
    if max_batch is None:
        max_batch = getattr(rules, "ROAST_BATCH", 10)
    qty = min(meat, max_batch)
    code, r = api.act(unit, {"action": "cook", "recipe": "roast", "qty": qty})
    if code != 200:
        api.debug("[%s] cook roast failed: %s %s" % (api.who(unit), code, str(r.get("error"))[:60]))
        return 0
    api.info("[%s] roasted %d meat -> roast" % (api.who(unit), qty))
    api.wait_result(r)
    fresh, _ = api.me(unit)
    if fresh:
        m.update(fresh)
    else:
        bag["meat"] = max(0, meat - qty)
        bag["roast"] = bag.get("roast", 0) + qty
    return qty


def eat(unit, m, below=rules.EAT_BELOW):
    """Eat when food is under or at `below` (default 0: eat only when food drops to 0), or when hurt and food leaves room:
    roast first, then bread, else roast some meat first (raw meat is never eaten). True if it ate."""
    bag = m.get("bag") or {}
    no_food = not bag.get("roast") and not bag.get("bread")
    need_heal = m["hp"] < m["max_hp"] * rules.HEAL_BELOW and m["food"] <= 85
    should_eat = m["food"] <= below or need_heal

    # If other foods are gone, and hero carries meat, roast it!
    if no_food and bag.get("meat") and m.get("ap", 0) >= 4:
        if should_eat or (m.get("ap", 0) >= 15 and m["food"] <= 20):
            if cook_roast(unit, m) > 0:
                bag = m.get("bag") or {}

    if should_eat:
        for f in ("roast", "bread"):  # roast +25 food and +15 hp (the owner: first), bread +40
            if bag.get(f):
                code, r = api.act(unit, {"action": "eat", "item": f})
                if code == 200:
                    api.wait_result(r)
                    return True
        if bag.get("meat") and m.get("ap", 0) >= 4:
            return roast_and_eat(unit, bag["meat"])
    return False


HEAL_ITEMS = ("bandage", "bread", "roast")   # the owner: bandage > bread > roast


def has_heal_items(m, items=HEAL_ITEMS):
    bag = m.get("bag") or {}
    return any(bag.get(i) for i in items) or bool(bag.get("meat") and m.get("ap", 0) >= 4)


def heal_with_items(unit, m, below=rules.HEAL_BELOW, target=rules.HEAL_TO, items=HEAL_ITEMS):
    """Hurt below `below` of the maximum: eat bandage/bread/roast up to `target`. Returns the number eaten."""
    bag = m.get("bag") or {}
    if m["hp"] <= 0 or m["hp"] >= below * m["max_hp"]:
        return 0
    eaten = 0
    # When other heal items (bandage, bread, roast) are gone, but hero carries meat: cook roast first
    if not any(bag.get(i) for i in items) and bag.get("meat") and m.get("ap", 0) >= 4:
        cook_roast(unit, m)
        bag = m.get("bag") or {}
    for item in items:
        n = bag.get(item) or 0
        while m["hp"] < target * m["max_hp"] and n > 0 and eaten < 15:
            code, r = api.act(unit, {"action": "eat", "item": item})
            if code != 200:
                bag[item] = n
                return eaten
            hp = r.get("hp")
            if not isinstance(hp, (int, float)):
                hp = (r.get("me") or {}).get("hp")
            if hp is None or hp <= m["hp"]:
                return eaten
            m["hp"], n, eaten = hp, n - 1, eaten + 1
            api.wait_result(r)
        bag[item] = n
    if eaten:
        api.info("[%s] ate %d bandage/bread/roast: hp %d/%d (%d bandage, %d bread, %d roast left)" % (
            api.who(unit), eaten, m["hp"], m["max_hp"], bag.get("bandage") or 0, bag.get("bread") or 0, bag.get("roast") or 0))
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
    api.debug("[%s] requisition %d packs of bread: %s %s" % (api.who(unit), packs, code,
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
    api.debug("[%s] requisition %d packs of bandages: %s %s" % (api.who(unit), packs, code,
                                                             str(r.get("got") or r.get("error"))[:80]))
    if code == 200:
        m.setdefault("bag", {})["bandage"] = have + packs * 3
    return code == 200


def share_bread(units, low=30):
    """A member with fewer than `low` bread gets half of the surplus of a squadmate standing within 3 tiles (the owner: the
    forest pair asks each other when one runs out)."""
    for u, m in units.items():
        if u is None and not rules.AUTO_GIVE_MAIN:
            continue
        have = (m.get("bag") or {}).get("bread") or 0
        if have >= low:
            continue
        for v, o in units.items():
            other = (o.get("bag") or {}).get("bread") or 0
            if v == u or other <= low * 2 or max(abs(o["x"] - m["x"]), abs(o["y"] - m["y"])) > 3:
                continue
            qty = (other - have) // 2
            code, _r = api.act(v, {"action": "give", "to": "main" if u is None else u, "goods": "bread", "qty": qty})
            api.debug("[%s] gives %d bread to %s: %s" % (api.who(v), qty, api.who(u), code))
            if code == 200:
                m.setdefault("bag", {})["bread"] = have + qty
                o["bag"]["bread"] = other - qty
            break


def roast_and_eat(unit, meat):
    """Cook roast from the meat (3 points a batch of up to 10, anywhere) and eat one. True if it ate."""
    m, _ = api.me(unit)
    if not m or m.get("ap", 0) < 4:
        return False
    bag = m.get("bag") or {}
    if not bag.get("roast"):
        if cook_roast(unit, m) <= 0 and not (m.get("bag") or {}).get("roast"):
            return False
    code, r = api.act(unit, {"action": "eat", "item": "roast"})
    if code == 200:
        api.wait_result(r)
        return True
    return False


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
        api.debug("  %s takes %d point(s) in %s: %s %s" % (api.who(unit), n, pick["id"], code, "" if code == 200 else str(r.get("error"))[:80]))
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


def compatible_hands(item, m):
    """A shield and a two-handed weapon cannot be worn together."""
    worn = m.get("worn") or {}
    slot = item.get("slot")
    if slot == "offhand" and (worn.get("weapon") or {}).get("base") in _TWO_HANDED:
        return False
    if slot == "weapon" and item.get("base") in _TWO_HANDED and worn.get("offhand"):
        return False
    return True


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
        if comparison.get("two_handed_weapon") or not compatible_hands(g, m):
            continue
        with _equip_lock:
            if _equip_cooldown.get((unit, slot), 0) > time.monotonic():
                continue
        d = comparison.get("power")
        better = d > 0 if d is not None else power(g) > power(worn.get(g["slot"]))
        if better:
            code, r = api.act(unit, {"action": "equip", "item": g["id"], "wait": False})
            api.debug("  %s equips %s L%s %s: %s %s" % (api.who(unit), g["base"], g["level"], g["rarity"], code,
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
            if code in (0, 202):
                return


def identify():
    """Identify the main character's eligible pieces individually, respecting the level and coin reserve."""
    m, _ = api.me(None)
    if not m:
        return 0, []
    identified = []
    code = 200
    for g in m.get("gear_bag") or []:
        level = g.get("level", 0)
        if not g.get("unidentified") or level < rules.IDENTIFY_MIN_LEVEL:
            continue
        cost = 5 + 2 * level
        if m.get("coins", 0) - cost < rules.IDENTIFY_KEEP_COINS:
            continue
        api.wait_busy(m)
        code, r = api.act(None, {"action": "identify", "item": g["id"], "wait": False})
        if code != 200:
            break
        identified.extend(x.get("name") for x in r.get("identified") or [])
        m, _ = api.me(None)
        if not m:
            return 0, identified
    return code, identified


def repair(unit, item_id):
    """Repair one piece with dust at a smith or workbench, never with coins."""
    code, r = api.act(unit, {"action": "repair", "item": item_id, "with": "dust"})
    return code, r.get("error") or r.get("cost")


DUST_BELOW = rules.DUST_BELOW   # the owner (2026-11-05): at the camp or the tribe, worn pieces under 20% are repaired with dust
_dust_shortfall = {}


def dust_repair(unit, m):
    """Dust-repair worn pieces and broken bag pieces needed for an empty slot, at a smith or workbench."""
    from . import nav
    if not (m.get("ring") == "camp" or nav.dist(m, rules.TRIBE_WORKBENCH) <= 2):
        return
    pieces = list((m.get("worn") or {}).values()) + list(broken_for_empty_slots(unit, m).values())
    better_weapon = broken_better_weapon(unit, m)
    if better_weapon and all(not g or g.get("id") != better_weapon.get("id") for g in pieces):
        pieces.append(better_weapon)
    for g in pieces:
        if not g:
            continue
        top = g.get("durability_max") or 0
        if not top or g.get("durability") is None or g["durability"] >= DUST_BELOW * top:
            continue
        if not (m.get("bag") or {}).get("dust"):
            return False
        key = (unit, g["id"])
        dust = m["bag"]["dust"]
        last = _dust_shortfall.get(key)
        if last and last[0] == dust and time.monotonic() - last[1] < 300:
            continue
        api.wait_busy(m)
        code, r = api.act(unit, {"action": "repair", "item": g["id"], "with": "dust"})
        api.debug("  %s repairs %s with dust (%s/%s): %s %s" % (api.who(unit), g.get("base"), g["durability"], top, code,
                                                          str(r.get("error") or r.get("cost") or "")[:80]))
        if code == 200:
            g["durability"] = top
            g["broken"] = False
            m["bag"]["dust"] = max(0, m["bag"].get("dust", 0) - (r.get("cost") if isinstance(r.get("cost"), int) else 1))
        elif code in (400, 402) and "dust" in str(r.get("error", "")).lower() and "missing" in str(r.get("error", "")).lower():
            _dust_shortfall[key] = (dust, time.monotonic())
        # One action per pass: another action could replace a queued repair.
        return code == 200
    return False


def auto_repair(unit, m):
    """Repair with dust only, at the camp smith or near the tribe workbench."""
    return dust_repair(unit, m)


def is_pickaxe(item):
    base = (item.get("base") or "").lower()
    return "pickaxe" in base


def is_axe(item):
    base = (item.get("base") or "").lower()
    return "axe" in base or "hatchet" in base


def plain_salvage_candidates(m):
    """Items in gear_bag that can be salvaged under the owner's rule (2026-11-05):
    - Must be rarity 'plain' (white gear)
    - Must not be unidentified
    - Must preserve at least 1 pickaxe and at least 1 axe in the bag (besides whatever is worn),
      prioritizing highest rarity/level/durability to keep.
    """
    if not rules.SALVAGE_PLAIN_GEAR:
        return []
    gear_bag = list(m.get("gear_bag") or [])
    if not gear_bag:
        return []

    pickaxes = []
    axes = []
    for g in gear_bag:
        if is_pickaxe(g):
            pickaxes.append(g)
        elif is_axe(g):
            axes.append(g)

    tier_order = {"plain": 0, "runed": 1, "treasured": 2, "set": 2, "mythic": 3}
    def _rank(x):
        return (tier_order.get(x.get("rarity"), 0), x.get("level", 0), x.get("durability", 0) or 0)

    protected_ids = set()
    if pickaxes:
        pickaxes.sort(key=_rank, reverse=True)
        protected_ids.add(pickaxes[0]["id"])
    if axes:
        axes.sort(key=_rank, reverse=True)
        protected_ids.add(axes[0]["id"])

    candidates = []
    for g in gear_bag:
        if g.get("id") in protected_ids:
            continue
        if g.get("unidentified"):
            continue
        if g.get("rarity") == "plain":
            candidates.append(g)
    return candidates


def auto_salvage(unit, m):
    """At the camp or tribe, automatically salvage all plain gear in gear_bag,
    keeping at least 1 pickaxe and 1 axe in the bag besides worn gear."""
    from . import nav
    candidates = plain_salvage_candidates(m)
    if not candidates:
        return False

    is_camp = m.get("ring") == "camp"
    is_tribe = nav.at_tribe(m)
    if not (is_camp or is_tribe):
        return False

    target_wb = rules.CAMP_WORKBENCH if is_camp else rules.TRIBE_WORKBENCH
    if nav.dist(m, target_wb) > 2:
        if is_camp:
            dest = [rules.CAMP_WORKBENCH[0], rules.CAMP_WORKBENCH[1] + 1]
        else:
            dest = [rules.TRIBE_WORKBENCH[0], rules.TRIBE_WORKBENCH[1] - 1]
        api.act(unit, {"action": "move", "to": dest, "until_there": True})
        m, _ = api.me(unit)
        if not m or nav.dist(m, target_wb) > 2:
            return False

    salvaged_any = False
    for g in list(candidates):
        api.wait_busy(m)
        code, r = api.act(unit, {"action": "salvage", "item": g["id"]})
        got = r.get("got") or {}
        if code == 200:
            salvaged_any = True
            api.info("[%s] salvaged plain %s L%s (#%s): got %s" % (
                api.who(unit), g.get("base"), g.get("level"), g["id"], got or "materials"))
            if "gear_bag" in m:
                m["gear_bag"] = [x for x in m["gear_bag"] if x.get("id") != g["id"]]
            for k, v in got.items():
                if "bag" in m and k in m["bag"]:
                    m["bag"][k] = m["bag"].get(k, 0) + v
            m["busy_until"] = r.get("busy_until") or (r.get("me") or {}).get("busy_until")
        else:
            api.debug("[%s] salvage %s (#%s) failed: %s %s" % (
                api.who(unit), g.get("base"), g["id"], code, r.get("error")))
            break
    return salvaged_any


def broken_for_empty_slots(unit, m):
    """At most one suitable broken bag item per empty slot, only when no intact substitute exists."""
    worn = m.get("worn") or {}
    by_slot = {}
    for g in m.get("gear_bag") or []:
        slot = g.get("slot")
        if not slot or worn.get(slot) or slot in rules.LOCKED_SLOTS.get(unit, ()) or g.get("unidentified"):
            continue
        if g.get("level", 0) > m.get("level", 0) + 2:
            continue
        if not compatible_hands(g, m):
            continue
        by_slot.setdefault(slot, []).append(g)
    return {slot: max(items, key=power) for slot, items in by_slot.items()
            if not any(g.get("durability", 0) > 0 and not g.get("broken") for g in items)
            and any(g.get("durability") == 0 for g in items)}


def broken_count(m, unit=None):
    return sum(1 for g in (m.get("worn") or {}).values() if g and g.get("durability") == 0) + len(broken_for_empty_slots(unit, m))


def broken_better_weapon(unit, m):
    """Estimate broken bag weapons against worn weapon: the server omits their comparison score."""
    if not rules.REPAIR_BETTER_BAG_WEAPON or "weapon" in rules.LOCKED_SLOTS.get(unit, ()):
        return None
    worn = (m.get("worn") or {}).get("weapon")
    eligible = [g for g in m.get("gear_bag") or []
                if g.get("slot") == "weapon" and (g.get("broken") or g.get("durability") == 0)
                and not g.get("unidentified") and g.get("level", 0) <= m.get("level", 0) + 2
                and compatible_hands(g, m) and power(g) > power(worn)]
    return max(eligible, key=power) if eligible else None


def repair_due(m, unit=None):
    weapon = (m.get("worn") or {}).get("weapon") or {}
    return ((rules.REPAIR_BROKEN_WEAPON and weapon.get("durability") == 0)
            or bool(broken_better_weapon(unit, m))
            or broken_count(m, unit) >= rules.REPAIR_TRIBE_BROKEN)


def needs_repair(m):
    for slot, g in (m.get("worn") or {}).items():
        top = g.get("durability_max") or 0
        if slot != "tool" and top and g.get("durability") is not None and (g["durability"] <= 0 or g.get("broken")):
            return True
    return False


def lend_for_repair(unit, m, others, need=12, gift=10, keep=12):
    """A hero with worn-out gear and too few coins gets `gift` coins from a squadmate within 3 tiles who can spare it.
    True if coins were handed over."""
    if (unit is None and not rules.AUTO_GIVE_MAIN) or m.get("coins", 0) >= need or not needs_repair(m):
        return False
    for u, o in others.items():
        if u == unit or o.get("coins", 0) < gift + keep:
            continue
        if max(abs(o["x"] - m["x"]), abs(o["y"] - m["y"])) > 3:
            continue
        code, _ = api.act(u, {"action": "give", "to": api.who(unit), "coins": gift})
        api.debug("  %s lends %d coins to %s for repairs: %s" % (api.who(u), gift, api.who(unit), code))
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
        api.debug("[chest] %s: the chest is empty: nothing to bank" % day)
        return False
    api.info("[chest] %s: the main character is at the camp after %d:00 UTC+8: banking the chest %s with mode %s (the 80%% tier)" % (
        day, CHEST_AFTER_HOUR, chest, CHEST_MODE))
    code, r = api.act(None, {"action": "bank", "mode": CHEST_MODE})
    api.info("[chest] bank answer: %s %s" % (code, json.dumps(api.clean(r), ensure_ascii=False)[:300]))
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
