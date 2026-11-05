"""The creatures as the private fighting model knows them: where each kind lives, the level range, its own trait (an elite's
affix always on: wolves and scorpions swift, spiders and liches chilling, golems and gargoyles shielded, imps enraged),
how much health it has at a level, and a Monte Carlo of a fight so the outcome of a hunt can be tried before it is sent.

The numbers come from the game's own pages (the wiki gives ring and level range), the skill text (traits) and
combat.jsonl (every recorded fight: health by level, blows by level and defence). What the data cannot tell yet is marked
`guess`; `python wild.py bestiary` shows the table, `python wild.py simulate KIND LEVEL` the result for every hero."""
import random
import statistics

from . import api, combat

# kind -> (ring, lowest level, highest level, trait, attacks first)
KINDS = {
    "hare": ("meadow", 1, 6, None, False), "deer": ("meadow", 1, 6, None, False),
    "hog": ("meadow", 1, 6, None, True), "slime": ("meadow", 1, 6, None, True),
    "fox": ("forest", 7, 18, None, True), "beaver": ("forest", 7, 18, None, True),
    "wolf": ("forest", 7, 18, "swift", True), "spider": ("forest", 7, 18, "chilling", True),
    "tiger": ("highlands", 19, 32, None, True), "bear": ("highlands", 19, 32, None, True),
    "ram": ("highlands", 19, 32, None, True), "golem": ("highlands", 19, 32, "shielded", True),
    "polar": ("highlands", 19, 32, None, True),
    "zombie": ("ashlands", 33, 46, None, True), "skeleton": ("ashlands", 33, 46, None, True),
    "scorpion": ("ashlands", 33, 46, "swift", True), "imp": ("ashlands", 33, 46, "enraged", True),
    "ghost": ("ruins", 47, 60, None, True), "vampire": ("ruins", 47, 60, "vampiric", True),
    "lich": ("ruins", 47, 60, "chilling", True), "gargoyle": ("ruins", 47, 60, "shielded", True),
}
GENERIC_HP = lambda level: 33 * level + 45      # forest creatures: spider 309 at 8, 342 at 9, wolf 276 at 7 (the data)
HP_FACTOR = {"deer": 0.77, "hare": 0.77, "hog": 0.77}   # the meadow beasts are lighter (deer 187 at 6 against 243)
ELITE_HP_FACTOR = 2.5                           # "two and a half times the health" (the skill text)
BLOW_SPREAD = 0.2                               # a creature blow varies about +-20% round the mean (the recorded blows)


def trait_blow(kind):
    """The creature's own trait as a multiplier of its blow (the elite affix tables of combat.py)."""
    trait = KINDS.get(kind, (None, 0, 0, None, True))[3]
    return combat.ELITE_BLOW.get(trait, 1.0) if trait else 1.0


def trait_out(kind):
    """How much of OUR damage gets through (a shield soaks blows)."""
    trait = KINDS.get(kind, (None, 0, 0, None, True))[3]
    return combat.ELITE_OUT.get(trait, 1.0) if trait else 1.0


def hp_at(kind, level, elite=False):
    """Health of a creature of that kind and level: the average recorded one, else the curve."""
    seen = [r["creature"]["max_hp"] or r["creature"]["hp"] for r in combat.load()
            if r["creature"].get("kind") == kind and r["creature"].get("level") == level and not r["creature"].get("elite")
            and (r["creature"].get("max_hp") or r["creature"].get("hp"))]
    base = statistics.mean(seen) if seen else GENERIC_HP(level) * HP_FACTOR.get(kind, 1.0)
    return base * (ELITE_HP_FACTOR if elite else 1.0)


def table():
    """Rows for `wild.py bestiary`: kind, ring, levels, trait, hp at a few levels, how often the model has seen it."""
    rows = combat.load()
    seen = {}
    for r in rows:
        seen[r["creature"].get("kind")] = seen.get(r["creature"].get("kind"), 0) + 1
    out = []
    for kind, (ring, lo, hi, trait, attacks) in KINDS.items():
        mid = (lo + hi) // 2
        out.append({"kind": kind, "ring": ring, "levels": "%d-%d" % (lo, hi), "trait": trait or "-", "attacks": attacks,
                    "blow x": round(trait_blow(kind), 2), "ours x": trait_out(kind),
                    "hp@%d" % lo: round(hp_at(kind, lo)), "hp@%d" % mid: round(hp_at(kind, mid)),
                    "seen": seen.get(kind, 0), "data": ("fights" if seen.get(kind, 0) >= 20 else "few" if seen.get(kind) else "guess")})
    return out


def simulate(me, kind, level, hp=None, elite=False, allies=(), trials=1000, stop_below=0.0, max_rounds=40):
    """Monte Carlo of one fight of `me` (a /me read) against a creature, after the allies' swings (each a /me read; they
    swing until the next blow would take them under 62% health, like combat.plan). Per exchange: our blow (mean of the
    model, a critical about twice), then the creature's (its level, our defence, its trait, +-20%). Returns
    {win, die, hp_left (fraction, mean), exchanges, xp}."""
    chp0 = hp if hp is not None else hp_at(kind, level, elite)
    out_mult = trait_out(kind)
    blow_mult = trait_blow(kind) * (1.15 if elite else 1.0)
    base_theirs = combat.their_blow(level, me.get("defense"))
    mine = combat.my_blow(me.get("attack"), me.get("crit")) * out_mult
    pre = 0.0
    for a in allies:
        b = combat.their_blow(level, a.get("defense")) * blow_mult
        room = a["hp"] - 0.62 * a["max_hp"]
        n = max(0, int(room // b) + 1) if room >= 0 else 0
        pre += n * combat.my_blow(a.get("attack"), a.get("crit")) * out_mult
    wins = deaths = 0
    left, rounds = [], []
    for _ in range(trials):
        chp, h, n = max(chp0 - pre, 1.0), me["hp"], 0
        while n < max_rounds:
            n += 1
            chp -= mine * random.uniform(0.85, 1.15)
            if chp <= 0:
                break
            h -= base_theirs * blow_mult * random.uniform(1 - BLOW_SPREAD, 1 + BLOW_SPREAD)
            if h <= 0 or h < stop_below * me["max_hp"]:
                break
        wins += chp <= 0 and h > 0
        deaths += h <= 0
        left.append(max(h, 0) / me["max_hp"])
        rounds.append(n)
    return {"win": wins / trials, "die": deaths / trials, "hp_left": statistics.mean(left),
            "exchanges": statistics.mean(rounds), "xp": level * 12, "creature_hp": round(chp0)}


def simulate_all(kind, level, elite=False, units=(None, 928, 925, 927, 929, 930, 931, 932, 933, 934), full_health=True):
    """The same creature against each hero (full health unless told otherwise): who wins alone, who would die."""
    res = []
    for u in units:
        m, _ = api.me(u)
        if not m:
            continue
        me = dict(m, hp=m["max_hp"]) if full_health else m
        r = simulate(me, kind, level, elite=elite)
        res.append((u, m, r))
    return res
