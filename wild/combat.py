"""Combat: record every fight exchange, fit a model of the fighting system, predict the outcome of a fight.

Every `attack` answer lists the exchanges (`rounds`): who struck (a = the attacker, m = the monster, f = the falcon),
for how much, and the health left after. recorder.record() stores each answer with the attacker's stats and the
creature's before the fight, one JSON line per attack, in combat.jsonl. `python wild.py model` fits the damage rules
from that file and tests the prediction against it.
"""
import json
import os
import random
import statistics
import threading
import time

from . import api, rules

FILE = os.path.join(api.HERE, "combat.jsonl")
_lock = threading.Lock()


def attacker_stats(m):
    """What decides the damage we deal and take (from a /me read)."""
    w = (m.get("worn") or {}).get("weapon") or {}
    armor = [g for s, g in (m.get("worn") or {}).items() if g.get("armor")]
    hero = m.get("hero") or {}
    return {
        "level": m["level"], "hp": m["hp"], "max_hp": m["max_hp"], "attack": m.get("attack"), "defense": m.get("defense"),
        "crit": m.get("crit"), "range": m.get("range"), "food": m.get("food"),
        "weapon": {"base": w.get("base"), "level": w.get("level"), "damage": w.get("damage"), "rarity": w.get("rarity"),
                   "durability": w.get("durability"), "affixes": [(a["id"], a["value"]) for a in w.get("affixes") or []]},
        "armor": [{"slot": g.get("slot"), "armor": g.get("armor"), "durability": g.get("durability")} for g in armor],
        "talents": {t["id"]: t["rank"] for t in (m.get("talents") or {}).get("talents", []) if t.get("rank")},
        "tribe_buff": (m.get("tribe") or {}).get("buff") if isinstance(m.get("tribe"), dict) else None,
        "night": (hero.get("bonus") or {}).get("night_now"), "grade": hero.get("grade"), "kind": hero.get("kind"),
    }


def record(unit, m, creature, request, response):
    """Append one attack (before: attacker and creature; after: the exchanges) to combat.jsonl."""
    row = {
        "t": time.time(), "unit": unit, "attacker": attacker_stats(m),
        "creature": {k: creature.get(k) for k in ("id", "kind", "level", "hp", "max_hp", "elite", "traits")},
        "request": {k: request.get(k) for k in ("rounds", "stop_below")},
        "rounds": response.get("rounds") or response.get("exchanges"),
        "killed": response.get("killed"), "broke_off": response.get("broke_off"), "down": response.get("down"),
        "xp": response.get("xp"), "me_after": {k: (response.get("me") or {}).get(k) for k in ("hp", "ap")},
    }
    with _lock, open(FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load():
    if not os.path.exists(FILE):
        return []
    with open(FILE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def hits(rows, by):
    """(attacker_stats, creature, dmg, crit, index) for every strike by `by` in the recorded exchanges."""
    for r in rows:
        for i, x in enumerate(r.get("rounds") or []):
            if x.get("by") == by:
                yield r, x, i


def fit():
    """Print what the data says: damage dealt by us vs attack, damage taken vs creature level/kind/defense."""
    rows = load()
    print("%d attacks recorded, %d exchanges" % (len(rows), sum(len(r.get("rounds") or []) for r in rows)))
    ours = [(r["attacker"]["attack"], x["dmg"], bool(x.get("crit")), r) for r, x, i in hits(rows, "a") if r["attacker"].get("attack")]
    if ours:
        print("\nour blows: dmg / attack")
        by_attack = {}
        for atk, dmg, crit, r in ours:
            by_attack.setdefault(atk, []).append((dmg, crit))
        for atk in sorted(by_attack):
            plain = [d for d, c in by_attack[atk] if not c]
            crits = [d for d, c in by_attack[atk] if c]
            if plain:
                print("  attack %-4s n=%-3d plain min/mean/max %s/%.1f/%s  ratio %.2f-%.2f%s" % (
                    atk, len(plain), min(plain), statistics.mean(plain), max(plain), min(plain) / atk, max(plain) / atk,
                    ("  crit mean %.1f (x%.2f)" % (statistics.mean(crits), statistics.mean(crits) / statistics.mean(plain))) if crits else ""))
    theirs = {}
    for r, x, i in hits(rows, "m"):
        c = r["creature"]
        theirs.setdefault((c["level"], r["attacker"].get("defense")), []).append(x["dmg"])
    if theirs:
        print("\ncreature blows by (creature level, our defense):")
        for k in sorted(theirs, key=lambda k: (k[0], k[1] or 0)):
            v = theirs[k]
            print("  L%-2s def %-3s n=%-3d min/mean/max %s/%.1f/%s" % (k[0], k[1], len(v), min(v), statistics.mean(v), max(v)))
    return rows


def mean_blow(level, defense, rows=None):
    """Average creature damage at that level against that defense from the data (nearest level if missing)."""
    rows = rows if rows is not None else load()
    vals = [x["dmg"] for r, x, i in hits(rows, "m") if r["creature"]["level"] == level and r["attacker"].get("defense") == defense]
    if vals:
        return statistics.mean(vals)
    near = sorted({r["creature"]["level"] for r, x, i in hits(rows, "m")}, key=lambda l: abs(l - level))
    return mean_blow(near[0], defense, rows) if near and near[0] != level else 20.0


def predict(attacker, creature, rounds, stop_below=0, trials=2000, rows=None):
    """Monte Carlo of a fight: the attacker strikes, then the creature, per exchange. Uses the damage ranges seen in
    the recorded fights (our blow = attack x a factor drawn from the observed ratios; theirs = observed blows at that
    level and defense). Returns {kill, ends_hp, creature_hp}: kill probability and mean health left after."""
    rows = rows if rows is not None else load()
    ratios = [x["dmg"] / r["attacker"]["attack"] for r, x, i in hits(rows, "a")
              if r["attacker"].get("attack") and not x.get("crit")] or [1.0]
    taken = [x["dmg"] for r, x, i in hits(rows, "m")
             if r["creature"]["level"] == creature["level"] and r["attacker"].get("defense") == attacker.get("defense")]
    if not taken:
        taken = [mean_blow(creature["level"], attacker.get("defense"), rows)]
    crit_p = (attacker.get("crit") or 0) / 100.0
    worst = max(taken)
    kills, left, mon_left = 0, [], []
    for _ in range(trials):
        hp, chp = attacker["hp"], creature["hp"]
        for _ in range(rounds):
            chp -= attacker["attack"] * random.choice(ratios) * (1.5 if random.random() < crit_p else 1)
            if chp <= 0:
                break
            hp -= random.choice(taken)
            # the game (wiki bug #12) stops BEFORE the round that may take us under the line, by the heaviest blow of this
            # fight; the first round always fires
            if hp <= 0 or hp - worst < attacker["max_hp"] * stop_below / 100.0:
                break
        kills += chp <= 0
        left.append(max(hp, 0))
        mon_left.append(max(chp, 0))
    return {"kill": kills / trials, "ends_hp": statistics.mean(left), "creature_hp": statistics.mean(mon_left),
            "dies": sum(1 for h in left if h <= 0) / trials}


# --- the fight planner: expected numbers from the recorded exchanges, priors until there are data ---------------
PRIOR_HIT_RATIO = 0.73       # our blow / attack (9-13 at attack 14, 10-13 at attack 18)
PRIOR_CRIT_MULT = 2.1        # a critical blow is about this times a plain one (23 against 10.8)
PRIOR_BLOW_PER_LEVEL = 3.8   # creature blow ~ this x its level against defense 3 (20 at level 5)
PRIOR_DEF_SLOPE = 1.25       # creature damage falls by about this per point of our defense (20.2 at def 3, 17.7 at def 5)
PRIOR_DEF = 3
_cache = {"t": 0, "ratio": PRIOR_HIT_RATIO, "crit": PRIOR_CRIT_MULT, "blow": {}, "slope": PRIOR_DEF_SLOPE}


def _refresh():
    if time.time() - _cache["t"] < 120:
        return
    rows = load()
    plain = [x["dmg"] / r["attacker"]["attack"] for r, x, i in hits(rows, "a")
             if r["attacker"].get("attack") and not x.get("crit")]
    crit = [x["dmg"] / r["attacker"]["attack"] for r, x, i in hits(rows, "a")
            if r["attacker"].get("attack") and x.get("crit")]
    if len(plain) >= 8:
        _cache["ratio"] = statistics.mean(plain)
        if len(crit) >= 3:
            _cache["crit"] = statistics.mean(crit) / _cache["ratio"]
    blow = {}
    for r, x, i in hits(rows, "m"):
        blow.setdefault(r["creature"]["level"], []).append((x["dmg"], r["attacker"].get("defense") or 0))
    out, slopes = {}, []
    for lv, v in blow.items():
        if len(v) >= 3:
            out[lv] = (statistics.mean(d for d, _ in v), statistics.mean(f for _, f in v))
        by_def = {}
        for d, f in v:
            by_def.setdefault(f, []).append(d)
        if len(by_def) >= 2:  # damage against two different defences at the same level: the slope
            (f1, d1), (f2, d2) = sorted((f, statistics.mean(ds)) for f, ds in by_def.items())[:2]
            if f2 != f1:
                slopes.append((d1 - d2) / (f2 - f1))
    _cache["blow"] = out
    if slopes:
        _cache["slope"] = max(0.0, statistics.mean(slopes))
    _cache["t"] = time.time()


def my_blow(attack, crit=0):
    """Expected damage of one of our blows (a critical blow with chance crit% is about twice a plain one)."""
    _refresh()
    return _cache["ratio"] * (attack or 10) * (1 + (crit or 0) / 100.0 * (_cache["crit"] - 1))


def their_blow(level, defense=PRIOR_DEF):
    """Expected damage of one creature blow against a defender with `defense`."""
    _refresh()
    if _cache["blow"]:
        near = min(_cache["blow"], key=lambda l: abs(l - level))
        mean, ref = _cache["blow"][near]
        base, ref_def = mean * level / near, ref
    else:
        base, ref_def = PRIOR_BLOW_PER_LEVEL * level, PRIOR_DEF
    return max(1.0, 0.45 * base, base + _cache["slope"] * (ref_def - (defense or 0)))  # armour never cuts a blow under ~45% (a lord with defense 16 still took 10-16 from a level 4)


def worst_blow(creature, defense=PRIOR_DEF):
    """The heaviest blow to expect from this creature in one round (the mean plus the spread, affixes and trait counted):
    what the game's `stop_below` reckons with, and the margin the first round of an attack needs."""
    from . import bestiary
    names = [n if isinstance(n, str) else str(n.get("id") or n) for n in (creature.get("elite") or [])]
    mult = bestiary.trait_blow(creature.get("kind")) * (1.15 if names else 1.0)
    for n in names:
        mult *= ELITE_BLOW.get(n, 1.0)
    return their_blow(creature.get("level", 1), defense) * mult * (1 + bestiary.BLOW_SPREAD)


def safe_round(me, creature, line_pct):
    """Can the attack's first round (it always fires) be taken without ending under `line_pct` of the maximum health?"""
    return me["hp"] - worst_blow(creature, me.get("defense")) >= me["max_hp"] * line_pct / 100.0


def plan(team, finisher, creature, helper_floor=rules.FIGHT_FLOOR, finisher_floor=rules.FIGHT_FLOOR, soften_to=30):
    """Can the squad kill the creature, and who pays? team and finisher are /me reads (attack, crit, defense, hp,
    max_hp). Helpers swing, one exchange at a time, until the next blow would drop them under helper_floor of their
    health; the finisher then needs to bring the creature from soften_to down to nothing and must stay over
    finisher_floor. Returns (ok, softened_by_helpers, finisher_cost, detail)."""
    from . import bestiary   # the kind's own trait (wolves swift, spiders chilling, golems shielded...)
    tb, to = bestiary.trait_blow(creature.get("kind")), bestiary.trait_out(creature.get("kind"))
    dealt, swings = 0.0, {}
    for m in team:
        blow = their_blow(creature["level"], m.get("defense")) * tb
        room = m["hp"] - helper_floor * m["max_hp"]
        n = max(0, int(room // blow) + 1) if room >= 0 else 0  # the swing that crosses the floor still happens
        dealt += n * my_blow(m.get("attack"), m.get("crit")) * to
        swings[m.get("name", "?")] = n
    left = max(creature["hp"] - dealt, 0)
    f_hit = my_blow(finisher.get("attack"), finisher.get("crit")) * to
    f_blow = their_blow(creature["level"], finisher.get("defense")) * tb
    f_swings = int(-(-max(left, 1) // f_hit)) if left > 0 else 0
    cost = f_swings * f_blow
    ok = left <= soften_to + f_hit * 2 and finisher["hp"] - cost >= finisher_floor * finisher["max_hp"]
    return ok, dealt, cost, {"helper_swings": swings, "finisher_swings": f_swings, "creature_blow": round(f_blow, 1)}


ELITE_BLOW = {"swift": 1.3, "enraged": 1.3, "frenzied": 1.25, "chilling": 1.1, "burning": 1.15, "vampiric": 1.1}   # blow multipliers by affix
ELITE_OUT = {"shielded": 0.6}   # a shield soaks blows: about 40% of ours did nothing against a shielded elite
ELITE_HP = {"vampiric": 1.15, "regenerating": 1.2, "armored": 1.2}               # effective health multipliers
DRINK = 0.15   # the demon lord drinks this share of every blow it lands (8-15% by rank)


def solo_elite(fin, creature, floor=rules.FIGHT_FLOOR, drink=DRINK):
    """Can the finisher alone beat this elite? Each exchange costs its blow (times the affixes) and gives ours; the lord
    drinks `drink` of what it deals. Returns (ok, health_left_fraction): the finisher must stay over `floor`."""
    names = creature.get("elite") or []
    names = [n if isinstance(n, str) else str(n.get("id") or n) for n in names]
    hp = creature["hp"]
    for n in names:
        hp *= ELITE_HP.get(n, 1.0)
    from . import bestiary
    blow_mult = 1.15 * bestiary.trait_blow(creature.get("kind"))
    for n in names:
        blow_mult *= ELITE_BLOW.get(n, 1.0)
    mine = my_blow(fin.get("attack"), fin.get("crit")) * bestiary.trait_out(creature.get("kind"))
    for n in names:
        mine *= ELITE_OUT.get(n, 1.0)
    theirs = their_blow(creature["level"], fin.get("defense")) * blow_mult
    exchanges = hp / max(mine, 1.0)
    cost = exchanges * (theirs - drink * mine)
    left = (fin["hp"] - cost) / fin["max_hp"]
    return left >= floor, left
