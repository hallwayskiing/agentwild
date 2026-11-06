"""Gang hunting: everyone hits the same creature, one chosen finisher takes the kill.

The creature's HP is shared and only the unit that lands the killing blow gets the XP and drops, so helpers
soften the target and the finisher delivers the last hit. Several squads can hunt side by side: each has its own
members, its own goals and a region of the map (so they do not overlap), and a creature one squad has
picked is off limits to the others.

    Squad("west", members=[928], region=lambda x, y: x < 136).run()
"""
import json
import threading
import time

from . import abyss, ambush, api, bestiary, care, combat, danger, landwatch, lease, queue, rules, state, tribe

FIN_STOP = round(rules.FIGHT_FLOOR * 100)   # the finisher's stop_below: the game now halts BEFORE the round that may cross it (wiki bug #12)
ELITE_STOP = FIN_STOP   # ... against an elite
FINISH_AT = 30        # creature HP at or below which the finisher steps in
IDLE_RELAX = 3        # after this many cycles without a target the squad stops staying close and walks further for one
FIN_AP = 25           # a finisher below this many points steps back for the next level up
ELITE_AVOID = 7        # tiles: a squad without elites takes no target with an elite this close to it
CROWD = None          # (off, the owner 00:22) a target with this many other creatures within 6 tiles is skipped
SOLO_FLOOR = rules.FIGHT_FLOOR   # pre-fight model floor for a lone finisher
HOME_AP = 8           # what `home` costs: a helper never spends below it
GONE_SECS = 600       # a creature that was gone when we arrived is not tried again for this long
LEVEL_SPAN = rules.LEVEL_SPAN
CLAIMED = {}          # creature id -> (squad, time): shared by all squads
_claim_lock = threading.Lock()


def what(t):
    """A creature in words for the log: `deer L3`, or `slime L6 elite[shielded,chilling]`."""
    e = t.get("elite")
    names = [n if isinstance(n, str) else str(n.get("id") or n) for n in e] if isinstance(e, (list, tuple)) else []
    return "%s L%s%s" % (t.get("kind"), t.get("level"), (" elite[%s]" % ",".join(names)) if e else "")


def creature_hp(resp):
    hits = [x.get("hp") for x in (resp.get("rounds") or resp.get("exchanges") or []) if x.get("by") == "a"]
    return hits[-1] if hits else None


def walk_cost(m, tx, ty):
    d = max(abs(tx - m["x"]), abs(ty - m["y"]))
    mult = {"camp": 1, "meadow": 1, "forest": 1.5, "ashlands": 1.5}.get(m["ring"], 2)
    return d * mult


def claim(creature_id, squad, secs=150):
    """Reserve a creature for a squad. False if another squad holds it."""
    with _claim_lock:
        owner = CLAIMED.get(creature_id)
        if owner and owner[0] != squad and time.time() - owner[1] < secs:
            return False
        CLAIMED[creature_id] = (squad, time.time())
        return True


class Squad:
    def __init__(self, name, members, hero_goal=rules.HERO_GOAL, region=None,
                 claims_bounties=False, hold=(), elites=False, rings=None, bread=False, bandage=False, close=None, center=None, land=False):
        self.name = name
        self.land = land                   # the corrupted-land squad: runs only while landwatch.STATE is on; creatures of the land are fair game
        self.center = center               # (x, y): with no target in sight the members drift toward it (every squad has its own, spread apart)
        self.close = close                 # members hunt within this many tiles of each other; a straggler walks over (owner 02:0x)
        self.bread = bread                 # bread heals the members in the field (below rules.HEAL_BELOW) and is shared
        self.heal_items = care.HEAL_ITEMS if bandage else ("bread", "roast")   # bandages are the main character's and the lord's squad only
        self.rings = tuple(rings) if rings else None   # the rings it hunts in (a creature without a known ring is left alone)
        self.elites = elites               # this squad also takes elite creatures (the demon lord, alone, for their gear)
        self.members = list(members)       # unit ids; None is the main character
        self.hero_goal = hero_goal         # then every other member until this level
        self.region = region or (lambda x, y: True)
        self.claims_bounties = claims_bounties
        self.hold = set(hold)              # members that sit out
        self.skipped = {}                  # creature id -> when the crowd was too small for it
        self.gone = {}                     # creature id -> when it was found gone (slain by others)
        self.fallen = {}                   # unit -> when its fall was recorded
        self.last_read = {}                # unit -> (time, health, x, y) of the last cycle read
        self.fought = set()                # finishers that fought since the last check of their talent points
        self.view = {}                     # unit -> its last 'around'
        self.idle = 0                      # cycles in a row without a target (the wait grows)

    def food_heal(self, u, m):
        """Food first (the owner, 2026-11-05): eat and heal with items before any trip home is even considered."""
        care.eat(u, m)
        if self.bread:
            care.heal_with_items(u, m, items=self.heal_items)

    def queue_recover_if_hurt(self, u, m):
        """Queue recovery only for a hero still below rules.HOME_BELOW after food healing. True when queued."""
        if rules.AUTO_RECOVER and 0 < m["hp"] < m["max_hp"] * rules.HOME_BELOW:
            queue.add(u, "recover", "resume-hunt", prio=9)
            return True
        return False

    def explain_fall(self, u, m, view):
        """Write down what could have killed the hero: its last read, the fights of ours it was in, and every creature the
        view of the body and of the squadmates near it shows (any level, with `attacks`). The killer of an ambush by a
        wandering monster is in none of our fight records, so this is the only trace."""
        last = self.last_read.get(u)
        if last:
            self.say("WARNING", "  %s: health %s at the last read %ds ago at (%s,%s); now down" % (api.who(u), last[1], time.time() - last[0], last[2], last[3]))
        seen = {}
        views = [(u, view)]
        for v in self.members:
            if v == u or lease.leased(v, self.name):
                continue
            vm, vview = api.me(v)
            if vm and max(abs(vm["x"] - m["x"]), abs(vm["y"] - m["y"])) <= 14:
                danger.observe(vm, vview)
                views.append((v, vview))
        for _v, vw in views:
            for t in (vw or {}).get("things") or []:
                if t.get("type") == "creature" and max(abs(t["x"] - m["x"]), abs(t["y"] - m["y"])) <= 14:
                    seen[t["id"]] = t
        strong = sorted(seen.values(), key=lambda t: -t.get("level", 0))[:6]
        self.say("WARNING", "  creatures within 14 tiles of the fall: %s" % (", ".join("%s L%s%s at (%s,%s)%s" % (
            t["kind"], t["level"], " elite" if t.get("elite") else "", t["x"], t["y"], " attacks" if t.get("attacks") else "")
            for t in strong) or "none in any view"))

    def regroup(self, units, sit_out):
        """The lowest-level member walks over to the others when it stands more than 2x `close` tiles away: whole enough
        (hp >= 80%), points for the walk and a fight after it. The step is as long as the points allow, the rest next round."""
        ids = [u for u in units if u not in sit_out]
        if len(ids) < 2:
            return
        afield = [u for u in ids if units[u]["ring"] != "camp"]
        incamp = [u for u in ids if units[u]["ring"] == "camp"]
        if incamp and afield:   # one was sent home (hurt, outmatched): once whole it walks back to the mate in the field
            mover, goal, reach = min(incamp, key=lambda u: units[u]["level"]), units[afield[0]], self.close
        elif len(afield) > 1:
            mover = min(afield, key=lambda u: (units[u]["level"], -units[u]["xp"]))
            goal, reach = next(units[u] for u in afield if u != mover), self.close   # (13-24 tiles apart was a dead zone: nobody moved)
        else:
            return
        m = units[mover]
        tx, ty = goal["x"], goal["y"]
        far = max(abs(m["x"] - tx), abs(m["y"] - ty))
        if far <= reach or m["ap"] < 30 or m["hp"] < 0.8 * m["max_hp"]:
            return
        step = min(far - self.close, int((m["ap"] - 20) / 1.3))
        if step < 4:
            return
        k = step / far
        to = [int(round(m["x"] + (tx - m["x"]) * k)), int(round(m["y"] + (ty - m["y"]) * k))]
        code, r = api.act(mover, {"action": "move", "to": to, "until_there": True})
        self.say("DEBUG", "%s stands %d tiles from the others: walks toward them to %s: %s %s" % (
            api.who(mover), far, to, code, "" if code == 200 else str(api.clean(r))[:100]))
        if code == 200:
            units[mover]["ap"] -= int(step * 1.3)

    def drift(self, ready):
        """No target: every member that is out walks toward the squad's centre point (the owner, 2026-11-05), as far as its
        points allow (keeping 20 for a fight); the centres of the squads lie far apart."""
        if not (self.center or self.land):
            return
        cx, cy = tuple(landwatch.AREA[:2]) if self.land else self.center
        for u, m in ready.items():
            far = max(abs(m["x"] - cx), abs(m["y"] - cy))
            if far <= 8 or m["ap"] < 30 or m["hp"] < 0.7 * m["max_hp"]:
                continue
            step = min(far - 4, int((m["ap"] - 20) / 1.3))
            if step < 4:
                continue
            k = step / far
            to = [int(round(m["x"] + (cx - m["x"]) * k)), int(round(m["y"] + (cy - m["y"]) * k))]
            code, r = api.act(u, {"action": "move", "to": to, "until_there": True})
            self.say("DEBUG", "%s finds nothing: drifts toward the centre %s (%d tiles away) to %s: %s %s" % (
                api.who(u), self.center, far, to, code, "" if code == 200 else str(api.clean(r))[:100]))

    def fresh(self, unit):
        """A hero's fresh /me; the API client handles transport retries."""
        m, _v = api.me(unit)
        return m

    def say(self, level, text):
        api.log(level, "[%s] %s" % (self.name, text))

    def fetch(self, fin_id, fin):
        """The nearest creatures for this finisher, from /world/hunt (relative to the unit, up to 20 of a level
        range): one read instead of eight, and not limited to what the 15x15 view shows."""
        lo, hi = rules.LEVEL_MIN, fin["level"] + LEVEL_SPAN   # the owner: from LEVEL_MIN up to the finisher's level +3
        code, h = api.call("GET", "/world/hunt?min=%d&max=%d&limit=20%s" % (lo, hi, "&unit=%d" % fin_id if fin_id else ""))
        if code == 200 and isinstance(h.get("creatures"), list):
            from . import bestiary
            for c in h["creatures"]:
                if "hp" not in c:
                    c["hp"] = c.get("max_hp") or bestiary.hp_at(c.get("kind"), c.get("level", 1), bool(c.get("elite")))
            return h["creatures"]
        return None

    def usable(self, t):
        """Plain creature, inside this squad's region, not known to be gone."""
        if self.rings and t.get("ring") not in self.rings:
            return False
        if (t.get("corrupted") and not self.land) or (t.get("boss") and not self.land) or (t.get("elite") and not self.elites) or not self.region(t["x"], t["y"]):
            return False  # elites have 2.5x the health and their affixes heal, shield or hit harder (only the lord takes them)
        return time.time() - self.gone.get(t["id"], 0) > GONE_SECS

    def pick_target(self, fin_id, fin, ready, centre=None):
        """Creatures the squad can take, best first: experience (level*12) per health to chew through and per step
        to walk. A creature the finisher and its helpers can wear down together (their attack stats say how much)
        is fair game, so a strong lead can also hunt alone."""
        around = self.view.get(fin_id) or {}
        pals = [m for u, m in ready.items() if u != fin_id and m["ring"] != "camp"] if self.close else []   # (a mate at the camp has nothing round it: no limit then)
        seen, out = set(), []
        found = self.fetch(fin_id, fin)
        if found is None:
            return None
        pools = [found, [t for t in (around.get("things") or []) if t.get("type") == "creature"]]
        crowd = [c for pool in pools for c in pool]   # every creature we know of: a target in a crowd is left alone
        for pool in pools:
            for t in pool:
                if "hp" not in t:
                    t["hp"] = t.get("max_hp") or bestiary.hp_at(t.get("kind"), t.get("level", 1), bool(t.get("elite")))
                if t["id"] in seen or not self.usable(t):
                    continue
                if not self.elites and any(o.get("elite") and o["id"] != t["id"]
                                           and max(abs(o["x"] - t["x"]), abs(o["y"] - t["y"])) <= ELITE_AVOID for o in crowd):
                    continue   # (the owner, 01:16) a squad that does not fight elites keeps away from them: no target near one
                if CROWD and sum(1 for o in crowd if o["id"] != t["id"] and max(abs(o["x"] - t["x"]), abs(o["y"] - t["y"])) <= 6) >= CROWD:
                    continue   # (931 fell beside a deer at (92,124) with a wolf, a spider, a fox and a beaver around it)
                if self.close and pals and self.idle < IDLE_RELAX and min(max(abs(t["x"] - p["x"]), abs(t["y"] - p["y"])) for p in pals) > self.close:
                    continue   # (the pair hunts together: the lord finished nearly everything while the main stood 76 tiles away)
                seen.add(t["id"])
                wc = walk_cost(fin, t["x"] - 1, t["y"])
                if wc > (fin["ap"] - 5) * (0.85 if self.idle >= IDLE_RELAX else 0.7):  # keep points for the fight itself (a squad idle for a while walks further)
                    continue
                if t["level"] > fin["level"] + LEVEL_SPAN or t["level"] < rules.LEVEL_MIN:
                    continue
                if danger.strong_near(t["x"], t["y"], fin["level"], margin=4) or danger.bad_zone(t["x"], t["y"], fin["level"]):
                    continue  # a strong creature stands by it, or the area is a crowd of them / where somebody fell
                out.append((t, wc))
        # elites drop gear (35%): when the elite hunter is nearly whole they count one and a half times in the ranking,
        # otherwise (hurt) plain creatures lead; plain creatures stay the bread and butter
        boost = 1.5 if self.elites and fin["hp"] >= 0.85 * fin["max_hp"] else 1.0
        out.sort(key=lambda p: -p[0]["level"] * 12 * (boost if p[0].get("elite") else 1) / ((p[0].get("hp") or p[0].get("max_hp") or 100) + p[1] * 3))
        return [t for t, _ in out]

    def feasible(self, t, fin_id, ready):
        """The helpers that can reach the creature, if with the finisher they can wear it down to FINISH_AT."""
        stand = [t["x"] - 1, t["y"]]
        if t.get("elite"):  # an elite is fought by the finisher alone, if its health and the model say it holds out
            return [] if combat.solo_elite(ready[fin_id], t)[0] else None
        # a helper keeps HOME_AP after the walk and a swing: hurt, it must still be able to go home (932 fell to 9 hp
        # with 7 points, unable to leave)
        team = [(u, m) for u, m in ready.items() if u != fin_id
                and m["ap"] >= walk_cost(m, stand[0], stand[1]) + 6 + HOME_AP]
        thp = t.get("hp") or t.get("max_hp") or 100
        t["hp"] = thp
        if thp <= 45:
            return team
        ok, dealt, cost, detail = combat.plan([m for _, m in team], ready[fin_id], t)
        if not ok and not team and SOLO_FLOOR is not None:
            # nobody to help (the others are gathering): the finisher alone, if it ends with a fair part of its health
            # (the owner: the main character levels up alone, in the forest)
            if combat.solo_elite(ready[fin_id], dict(t, elite=[]), floor=SOLO_FLOOR)[0]:
                return []
        return team if ok else None

    def can_fight_back(self, m, near, floor=ambush.FLOOR):
        return ambush.can_fight_back(m, near, floor)

    def fight_back(self, u, m, near):
        return ambush.fight_back(u, m, near)

    def claim_bounties(self, units):
        """Finished daily bounties pay a lot and are account-wide: the claim goes to the lowest-level member at
        the camp."""
        sample = next(iter(units.values()), None)
        ready = [b for b in ((sample or {}).get("bounties") or []) if b["done"] >= b["n"] and not b["claimed"]]
        if not ready:
            return
        for u in sorted(units, key=lambda u: (units[u]["level"], units[u]["xp"])):
            m = units[u]
            if m["ring"] != "camp" and m["ap"] < 16:
                continue
            if m["ring"] != "camp":
                api.act(u, {"action": "home", "why": "goes to the camp to claim the daily bounty"})
                fresh, _ = api.me(u)
                api.wait_busy(fresh)
            code, r = api.act(u, {"action": "bounty"})
            self.say("INFO", "bounty claim by %s: %s %s" % (api.who(u), code, json.dumps({k: r.get(k) for k in ("claimed", "xp", "coins", "got", "level_up", "error")}, ensure_ascii=False)))
            if code == 200:
                return

    def cycle(self):
        if abyss.PAUSE.is_set():  # the squad is assembling for, or inside, the Abyss
            time.sleep(20)
            return
        if self.land and not landwatch.STATE["on"]:
            time.sleep(30)
            return
        # one cheap read of /world/hero/ap: when nobody of the squad has points to act, don't read every hero
        # (the points come back one by one: wait for the first)
        active = [u for u in self.members if (rules.AUTO_HUNT or queue.hunting(u))
                  and not lease.leased(u, self.name)
                  and (self.land or not landwatch.STATE["on"] or u not in rules.CORRUPT_LAND["units"])]
        if not active:
            time.sleep(30)
            return
        pts = api.ap_all()
        mine = [pts[u] for u in active if u in pts]
        if mine and len(mine) == len(self.members) and all(p.get("ap", 99) < 8 for p in mine):
            nxt = min(api.iso_wait(p.get("next_ap_at")) for p in mine)
            time.sleep(min(max(nxt, 5) + 1, 60))
            return
        units = {}
        for u in self.members:
            if not rules.AUTO_HUNT and not queue.hunting(u):
                continue
            if lease.leased(u, self.name):  # another job (build crew, Abyss) is in charge of this hero now
                continue
            if not self.land and landwatch.STATE["on"] and u in rules.CORRUPT_LAND["units"]:   # away at the corrupted land
                continue
            m, view = api.me(u)
            if not m:
                continue
            self.view[u] = view
            danger.observe(m, view)
            if m["hp"] <= 0 and (u not in self.fallen or time.time() - self.fallen[u] > 600):
                self.fallen[u] = time.time()
                danger.record_death(m)
                self.say("WARNING", "%s fell at (%s,%s): the area is banned for hours" % (api.who(u), m["x"], m["y"]))
                self.explain_fall(u, m, view)
            self.last_read[u] = (time.time(), m["hp"], m["x"], m["y"])
            state.update(u, m, self.name)
            if "tribe" not in (state._notes.get(u or 0, ("",))[0] or ""):
                state.note(u, "狩猎待命(%s)" % self.name)
            if m["hp"] > 0 and m["ring"] != "camp" and m["ap"] >= HOME_AP:
                def _log_ambush(t):
                    if "beatable" in t or "fighting back" in t:
                        self.say("INFO", t)
                    else:
                        self.say("WARNING", t)
                if ambush.handle(u, m, view, say=_log_ambush) == "home":   # a real ambush: fought if winnable, else home
                    m["ring"] = "camp"
            care.claim_chest(u, m)   # (the main character only, in the camp, once a day after 12:00 UTC+8)
            self.food_heal(u, m)   # food healing outranks going home: only a hero still hurt afterwards recovers
            if self.queue_recover_if_hurt(u, m):
                continue  # the recovery worker owns all subsequent actions
            if care.repair_due(m, u):
                if not (m.get("bag") or {}).get("dust", 0):
                    queue.add(u, "repair-tribe", "resume-hunt", prio=9)
                    continue
                if m.get("ring") != "camp":
                    queue.add(u, "repair-camp", "resume-hunt", prio=9)
                    continue
            if self.bread:
                if self.heal_items is care.HEAL_ITEMS:
                    care.restock_bandage(u, m)   # no-op while rules.AUTO_RESTOCK_BANDAGE is off
                care.restock_bread(u, m)     # none left, in the camp: the quartermaster again (deeds permitting), else rest
            units[u] = m
            care.auto_equip(u, m)
            care.spend_talents(u, m)
            care.auto_repair(u, m)
            if m["ring"] == "camp":  # reading a piece costs 5 coins and 2 a level since skill 0.24.30
                for g in [g for g in m.get("gear_bag") or [] if g.get("unidentified")]:
                    if g.get("level", 0) < rules.IDENTIFY_MIN_LEVEL:
                        continue
                    cost = 5 + 2 * g["level"]
                    if m.get("coins", 0) - cost < rules.IDENTIFY_KEEP_COINS:
                        break
                    code, _r = api.act(u, {"action": "identify", "item": g["id"]})
                    if code == 200:
                        m["coins"] = m.get("coins", 0) - cost
                care.auto_salvage(u, m)
        if self.bread:
            care.share_bread(units)
        if self.claims_bounties:
            self.claim_bounties(units)
        # the owner's gather order is waiting for points: no fights meanwhile (they would eat the points); in the field the
        # hero goes home (the walk to the tribe starts from the camp anyway)
        resting = {u for u in units if queue.resting(u)}
        staging = {u for u in units if landwatch.STATE["stage"] and u in rules.CORRUPT_LAND["units"]}   # a land of our kind is due soon
        resting |= staging
        for u in resting:
            if units[u]["ring"] != "camp" and units[u]["ap"] >= 8:
                api.act(u, {"action": "home", "why": "a corrupted land is due: rests in the camp to build up points" if u in staging
                            else "a gather order waits: rests to build up points"})
                units[u]["ring"] = "camp"
        for u, m in units.items():
            if u in self.hold or u in resting or m["ring"] == "camp" or m["ap"] < 8:
                continue
            if m["hp"] < m["max_hp"] * rules.HOME_BELOW:
                api.act(u, {"action": "home", "why": "hurt in the field: health %d/%d is under %d%%" % (m["hp"], m["max_hp"], 100 * rules.HOME_BELOW)})
                m["ring"] = "camp"
            elif m["ring"] not in ("meadow", "forest") and m["ring"] not in (self.rings or ()):
                # far from the hunting ground (it fell and woke at the totem, or a trip ended): home first
                self.say("DEBUG", "%s is far from its ground (%s,%s): home" % (api.who(u), m["x"], m["y"]))
                api.act(u, {"action": "home", "why": "far from its hunting ground (%s,%s), ring %s" % (m["x"], m["y"], m["ring"])})
                m["ring"] = "camp"
        sit_out = self.hold | resting
        if self.close and len(units) > 1:
            self.regroup(units, sit_out)
        ready = {u: m for u, m in units.items() if u not in sit_out and m["ap"] >= 6}
        # the heroes below their goal level finish first, the lowest level first (the slowest holds the Abyss back), but a hero
        # under FIN_AP waits behind the next one with enough points ("if the points are short, the next level up takes it")
        low = sorted((u for u in self.members if u in ready and units[u]["level"] < self.hero_goal),
                     key=lambda u: (units[u]["level"], -units[u]["xp"], -ready[u]["ap"]))
        order = [u for u in low if ready[u]["ap"] >= FIN_AP] + [u for u in low if ready[u]["ap"] < FIN_AP]
        # behind them whoever has points and health to spare, so a squad is not held up by a hero out of points
        order += sorted((u for u in ready if u not in order and ready[u]["ap"] >= 30), key=lambda u: -ready[u]["ap"])
        if not order and ready:   # nobody is below its goal and everyone is short: the one in the best shape finishes
            order = sorted(ready,
                           key=lambda u: -ready[u]["hp"] / ready[u]["max_hp"])
        if not order:
            self.say("DEBUG", "finisher not ready (low hp/AP), waiting")
            time.sleep(30)
            return
        t = fin_id = team = None
        for cand in order[:8]:
            fm = ready[cand]
            pals = sorted((m["x"], m["y"]) for u, m in ready.items() if u != cand and m["ap"] >= 12)
            centre = (pals[len(pals) // 2][0], sorted(p[1] for p in pals)[len(pals) // 2]) if pals else None
            targets = self.pick_target(cand, fm, ready, centre)
            if targets is None:
                self.say("WARNING", "hunt read failed; waiting without treating it as an empty area")
                time.sleep(15)
                return
            for tt in targets[:8]:
                if time.time() - self.skipped.get(tt["id"], 0) < 150:
                    continue
                tm = self.feasible(tt, cand, ready)
                if tm is None:
                    self.skipped[tt["id"]] = time.time()  # not now: try the next creature
                elif claim(tt["id"], self.name):
                    tm = self.feasible(tt, cand, ready)  # its health may have changed
                    if tm is not None:
                        t, fin_id, team = tt, cand, tm
                        break
            if t:
                break
        if not t:
            self.idle += 1
            wait = 30   # nothing worth walking to: look again in 30 s (fixed; the owner, 2026-11-05)
            self.say("DEBUG", "no target now (best finisher AP %s): next look in %ds" % (ready[order[0]]["ap"], wait))
            self.drift(ready)
            time.sleep(wait)
            return
        self.idle = 0
        fin = ready[fin_id]
        self.fought.add(fin_id)   # (its talent points are looked at once the fight is over: run())
        stand = [t["x"] - 1, t["y"]]
        self.say("DEBUG", "finisher %s L%s xp %s/%s hp %s ap %s -> %s hp %s at (%s,%s); helpers %s" % (
            api.who(fin_id), fin["level"], fin["xp"], fin["xp_next"], fin["hp"], fin["ap"], what(t),
            t["hp"], t["x"], t["y"], [u for u, _ in team]))
        # the finisher goes first and looks: other players hunt here too, and a creature that is gone costs one
        # walker a trip instead of the whole squad
        api.act(fin_id, {"action": "move", "to": stand, "until_there": True})
        fm2, around = api.me(fin_id)
        api.wait_busy(fm2)
        fm2, around = api.me(fin_id)
        there = [c for c in (around or {}).get("things") or [] if c.get("type") == "creature" and c["id"] == t["id"]]
        if not there:
            self.gone[t["id"]] = time.time()
            self.say("DEBUG", "%s #%s is gone: aborting before the others set out" % (what(t), t["id"]))
            return
        t["hp"] = there[0].get("hp") or there[0].get("max_hp") or t.get("hp") or bestiary.hp_at(t.get("kind"), t.get("level", 1), bool(t.get("elite")))
        if self.feasible(t, fin_id, ready) is None and t["hp"] > 45:
            self.skipped[t["id"]] = time.time()
            self.say("WARNING", "%s #%s has more health than seen (%s): letting it go" % (what(t), t["id"], t["hp"]))
            return
        for u, _m in team:
            api.act(u, {"action": "move", "to": stand})
        time.sleep(6)
        hp, killed, active = t["hp"], False, [u for u, _m in team]
        hpn = {}   # helper -> its health after its last swing
        free_at = {u: 0.0 for u in active}  # when each helper can swing again (a swing takes it a few seconds)
        deadline = time.time() + 180
        # rotate single swings so no helper soaks the counter-attacks alone; "wait": false refuses a busy character
        # instead of queueing behind itself, so every answer is a real fight with its exchanges
        while active and not killed and hp > FINISH_AT and time.time() < deadline:
            now = time.time()
            due = [u for u in active if free_at[u] <= now]
            if not due:
                time.sleep(max(0.3, min(5.0, min(free_at[u] for u in active) - now)))
                continue
            u = due[0]
            # a helper should not land the killing blow (only the killer gets the XP): swing only while the expected
            # damage of the swing leaves the creature standing (a lucky critical may still kill it: accepted)
            peak = combat.my_blow(ready[u].get("attack"), ready[u].get("crit"))   # the expected blow, crit chance counted
            rounds = 2 if hp > 100 else 1
            while rounds > 1 and rounds * peak >= hp:
                rounds -= 1
            if peak >= hp:
                active.remove(u)   # out of the rotation: the finisher takes it from here
                continue
            hnow = dict(ready[u], hp=hpn.get(u, ready[u]["hp"]))
            if not combat.safe_round(hnow, t, FIN_STOP):   # the first round always fires: a helper swings only over its line
                active.remove(u)
                continue
            req = {"action": "attack", "target": t["id"], "rounds": rounds, "stop_below": 55}
            code, r = api.act(u, dict(req, wait=False))
            if code == 200 and (r.get("me") or {}).get("hp") is not None:
                hpn[u] = r["me"]["hp"]
            if code == 404:
                killed = True
            elif code == 200 and (r.get("rounds") or r.get("exchanges")):
                combat.record(u, ready[u], dict(t, hp=hp), req, r)
                got = creature_hp(r)
                hp = got if got is not None else hp
                free_at[u] = time.time() + ((r.get("me") or {}).get("ready_in_seconds") or 6) + 0.5
                if r.get("killed"):
                    killed = True
                    self.say("INFO", "helper %s took the kill of %s (no XP for the finisher)" % (api.who(u), what(t)))
                elif r.get("broke_off") or r.get("down"):
                    active.remove(u)  # hurt: out of the rotation
                elif ((r.get("me") or {}).get("ap") or 99) < HOME_AP + 3:
                    active.remove(u)  # the points for the way home stay untouched
            elif code == 429 and "action points" not in str(r.get("error", "")):
                free_at[u] = time.time() + (r.get("retry_after_seconds") or 6) + 0.5  # still busy
            else:
                active.remove(u)
        if not killed and t.get("elite"):
            # an elite: four rounds at a time, alone, until it falls or the finisher's health drops under 40%
            fin2 = self.fresh(fin_id)
            api.wait_busy(fin2)
            for _ in range(14):
                if not fin2 or not combat.safe_round(fin2, t, ELITE_STOP):   # the first round always fires: not over the line
                    code, r = 0, {"broke_off": True}
                    break
                req = {"action": "attack", "target": t["id"], "rounds": 6, "stop_below": ELITE_STOP}
                code, r = api.act(fin_id, req)
                if code == 200:
                    combat.record(fin_id, fin, dict(t, hp=hp), req, r)
                    got = creature_hp(r)
                    hp = got if got is not None else hp
                if code != 200 or r.get("killed") or r.get("broke_off") or r.get("down"):
                    break
                nxt = api.after_attack(fin2, r)   # health and points after the round come with the answer
                if nxt:
                    fin2 = nxt[0]
                    time.sleep(nxt[1])
                else:
                    fin2 = self.fresh(fin_id)
                    api.wait_busy(fin2)
            if code == 200 and r.get("killed"):
                self.say("INFO", "finisher %s killed %s (loot: %s)" % (api.who(fin_id), what(t), json.dumps(r.get("loot") or r.get("got") or "", ensure_ascii=False)[:120]))
            else:
                self.skipped[t["id"]] = time.time()
                self.say("WARNING", "%s not finished (code %s, creature hp %s): let go" % (what(t), code, hp))
            return
        if not killed:
            fin2 = self.fresh(fin_id)
            api.wait_busy(fin2)
            req = {"action": "attack", "target": t["id"], "rounds": 6, "stop_below": FIN_STOP}
            code, r = 0, {}
            # locked on until it falls. The game stops before the round that may take us under FIN_STOP; the first round of
            # every call still fires, so each call is made only when its worst blow keeps us over the line
            for i in range(8):
                if not fin2:   # the read failed (a slow server): not a reason to let the creature go
                    self.say("WARNING", "finisher %s: no health read, leaving the fight (creature hp %s)" % (api.who(fin_id), hp))
                    break
                # the creature's health left counts: a round that kills it takes no blow back, so it is always safe
                finishing = hp <= combat.my_blow(fin2.get("attack"), fin2.get("crit"))
                if not finishing and not combat.safe_round(fin2, dict(t, hp=hp), FIN_STOP):
                    self.skipped[t["id"]] = time.time()
                    self.say("DEBUG", "finisher %s stops: the next round could take it under %d%% (creature hp %s)" % (api.who(fin_id), FIN_STOP, hp))
                    break
                code, r = api.act(fin_id, req)
                if code == 200:
                    combat.record(fin_id, fin, dict(t, hp=hp), req, r)
                    got = creature_hp(r)
                    hp = got if got is not None else hp
                if code == 0:   # timed out: the blow may or may not have landed. Only a real read says how we stand now
                    time.sleep(2)   # (never the last known values): swing again if the checks still pass on it
                    fin2 = self.fresh(fin_id)
                    api.wait_busy(fin2)
                    continue
                if code != 200 or r.get("killed") or r.get("broke_off") or r.get("down"):
                    break
                if ((r.get("me") or {}).get("ap") or 99) < HOME_AP + 3:
                    break
                nxt = api.after_attack(fin2, r)   # health and points after the round come with the answer: no read needed
                if nxt:
                    fin2 = nxt[0]
                    time.sleep(nxt[1])
                else:
                    fin2 = self.fresh(fin_id)
                    api.wait_busy(fin2)
            if code == 200 and r.get("killed"):
                self.say("INFO", "finisher %s killed %s%s" % (api.who(fin_id), what(t), (" (xp +%s)" % r["xp"]) if isinstance(r.get("xp"), (int, float)) else ""))
            elif code != 200:
                if code != 0:
                    self.gone[t["id"]] = time.time()  # slain or walked off while we came
                # not a bug: the creature was slain or walked off while we came (4xx), or the server did not answer (0)
                self.say("WARNING" if code in (0, 202) or code >= 500 else "DEBUG",
                         "finisher %s: the attack did not go through (%s %s)" % (api.who(fin_id), code, json.dumps(api.clean(r))[:140]))

    def run(self):
        """Hunt for ever (the goals only decide who finishes first)."""
        while True:
            try:
                self.cycle()
                for u in list(self.fought):   # the owner (2026-11-04): talent points are checked after EVERY fight
                    m, _v = api.me(u)
                    if m:
                        care.spend_talents(u, m)
                self.fought.clear()
            except Exception as e:
                import traceback
                self.say("ERROR", "exception %r\n%s" % (e, traceback.format_exc()))
                time.sleep(20)
            time.sleep(2)
