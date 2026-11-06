"""Ambushed in the field: weigh the fight (the combat model), hit back when it is won, else go home. The hunters and
the gatherers share it (the owner, 2026-11-04: "kill what we can beat, otherwise fall back, the same as in hunting")."""
import time

from . import api, combat, rules


FLOOR = rules.FIGHT_FLOOR   # an ambushed hero fights when the model leaves it over this share
ABORT = rules.FIGHT_FLOOR   # ... and breaks off when its health falls under this share


def can_fight_back(m, near, floor=FLOOR):
    """Ambushed by `near` (all attacking us): kill them weakest first while the rest keep hitting; returns
    (we end over `floor` of our health, fraction left). Elites count with their affixes (combat.solo_elite maths)."""
    from . import bestiary
    mates = []
    for t in near:
        names = t.get("elite") or []
        hp = t.get("hp") or bestiary.hp_at(t.get("kind"), t.get("level", 1), bool(t.get("elite")))   # (a view without hp: the model's)
        for n in names:
            hp *= combat.ELITE_HP.get(n if isinstance(n, str) else str(n.get("id") or n), 1.0)
        bm = bestiary.trait_blow(t.get("kind")) * (1.15 if names else 1.0)
        om = bestiary.trait_out(t.get("kind"))
        for n in names:
            n = n if isinstance(n, str) else str(n.get("id") or n)
            bm *= combat.ELITE_BLOW.get(n, 1.0)
            om *= combat.ELITE_OUT.get(n, 1.0)
        mine = combat.my_blow(m.get("attack"), m.get("crit")) * om
        theirs = combat.their_blow(t.get("level", 0), m.get("defense")) * bm
        mates.append((hp / max(mine, 1.0), theirs))
    mates.sort()   # fewest exchanges first
    lost, alive = 0.0, [b for _, b in mates]
    for ex, _b in mates:
        lost += ex * sum(alive)
        alive.pop(0)
    left = (m["hp"] - lost) / m["max_hp"]
    return left >= floor, left

def fight_back(u, m, near):
    """Swing at the ambushers, weakest first, four rounds at a time; stops when the model's floor is crossed."""
    order = sorted(near, key=lambda t: t.get("hp", 0))
    fresh = None   # our state: read once, then taken from each attack's answer (it carries the health after the round)
    for t in order:
        for _ in range(8):
            while not fresh:   # a failed read (the server is slow): try again, it says nothing about the fight
                fresh, _v = api.me(u)
                if not fresh:
                    time.sleep(3)
                else:
                    api.wait_busy(fresh)
            if fresh["hp"] < ABORT * fresh["max_hp"]:
                return False   # going badly: the caller sends it home
            code, r = api.act(u, {"action": "attack", "target": t["id"], "rounds": 4, "stop_below": int(100 * ABORT)})
            if code == 200:
                nxt = api.after_attack(fresh, r)
                if nxt:
                    fresh = nxt[0]
                    time.sleep(nxt[1])
                else:
                    fresh = None   # no state in the answer: read again
            else:
                fresh = None   # timed out or refused: read again
            if code != 200 or r.get("killed"):
                break
            if r.get("broke_off") or r.get("down"):
                return False
    return fresh is None or fresh["hp"] >= ABORT * fresh["max_hp"]


AMBUSH_RANGE = 3    # a creature falls on an agent within 2 tiles (the view may be a step late)
_last = {}          # unit -> health at the previous read


def strikers(u, m, view):
    """The creatures that really ambushed the hero: its health fell since the last read (not from a fight it started in the
    last 90 seconds) and an attacking creature stands within AMBUSH_RANGE. Nothing else counts (the owner, 2026-11-04:
    no 'surrounded' rule, only ambushes that really happen)."""
    prev = _last.get(u)
    _last[u] = m["hp"]
    if prev is None or m["hp"] >= prev or time.time() - api.FOUGHT.get(u, 0) < 90:
        return []
    return [t for t in ((view or {}).get("things") or []) if t.get("type") == "creature" and t.get("attacks")
            and max(abs(t["x"] - m["x"]), abs(t["y"] - m["y"])) <= AMBUSH_RANGE]


def handle(u, m, view, say=print, flee=True):
    """After a fresh read of a hero in the field: if it was really ambushed, weigh the fight: 'fought' (it hit back and
    stands), 'home' (sent home), or None when no ambush happened."""
    near = strikers(u, m, view)
    if not near:
        return None
    from . import care
    care.heal_with_items(u, m)
    care.eat(u, m)
    ok, left = can_fight_back(m, near)
    names = ", ".join("%s L%s" % (t["kind"], t["level"]) for t in near[:4])
    if ok:
        say("%s ambushed by %s (%s): beatable (~%d%% health left): fighting back" % (api.who(u), len(near), names, left * 100))
        if fight_back(u, m, near):
            return "fought"
    if not flee:   # the gatherers: not home, the work goes on
        say("%s ambushed by %s (%s): %s: carries on [my hp %s/%s, left est %d%%]" % (
            api.who(u), len(near), names, "the fight went badly" if ok else "cannot win cheaply", m["hp"], m["max_hp"], left * 100))
        return "avoid"
    say("%s ambushed by %s (%s): %s: home at once [my hp %s/%s, left est %d%%, creatures %s]" % (
        api.who(u), len(near), names, "the fight went badly" if ok else "cannot win cheaply", m["hp"], m["max_hp"], left * 100,
        [(t["kind"], t.get("level"), t.get("hp"), t.get("elite")) for t in near[:3]]))
    api.act(u, {"action": "home", "why": "real ambush by %s, %s" % (names, "the fight went badly" if ok else "cannot win cheaply")})
    return "home"
