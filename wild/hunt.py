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

from . import abyss, ambush, api, bestiary, care, combat, danger, landwatch, lease, nav, queue, rules, state, tribe

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


def is_boss_creature(t):
    """Is this a corrupted land guardian / boss monster?"""
    if not t:
        return False
    if t.get("boss") or t.get("guardian"):
        return True
    if getattr(landwatch, "GUARDIAN", [None])[0] and t.get("id") == landwatch.GUARDIAN[0]:
        return True
    kind = str(t.get("kind") or "").lower()
    name = str(t.get("name") or "").lower()
    traits = [str(x).lower() for x in (t.get("traits") or [])]
    for kw in ("守卫", "guardian", "boss", "巨蛛", "chief"):
        if kw in kind or kw in name:
            return True
    if any("boss" in x or "guardian" in x for x in traits):
        return True
    return False


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

    def is_land_active(self, unit=None):
        """Is corrupted land currently active for this squad / unit?"""
        if not landwatch.STATE.get("on") or not landwatch.AREA[:2]:
            return False
        cfg_units = rules.CORRUPT_LAND.get("units", [0, 928])
        if unit is not None:
            return (unit in cfg_units or (unit == 0 and None in cfg_units))
        return any(u in cfg_units or (u is None and 0 in cfg_units) for u in self.members)

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
        if far <= reach or m["ap"] < 20 or m["hp"] < 0.8 * m["max_hp"]:
            return
        # Smart navigation to regroup with teammates across any ring
        plan = nav.plan_route(m, (tx, ty))
        if plan.get("steps") and any(s.get("action") in ("travel", "home") for s in plan["steps"]):
            step0 = plan["steps"][0]
            if step0.get("action") == "home" and far > 45:
                api.act(mover, {"action": "home", "why": "smart navigation: via camp to rejoin squad"})
                time.sleep(10)
                return
            elif step0.get("action") == "travel":
                target_ring = step0.get("to")
                code, r = api.act(mover, {"action": "travel", "to": target_ring})
                if code == 200:
                    self.say("INFO", "%s uses %s waypoint to rejoin squad at (%d,%d) (plan '%s'): 200" % (
                        api.who(mover), target_ring, tx, ty, plan["name"]))
                    units[mover]["ring"] = target_ring
                    wp = next((w for w in m.get("waypoints") or [] if w.get("ring") == target_ring), None)
                    if wp:
                        units[mover]["x"], units[mover]["y"] = wp["x"], wp["y"]
                    return
        # If far afield and can teleport directly to target ring
        if far > 45 and m.get("coins", 0) >= 20 and m.get("ap", 0) >= 20:
            target_ring = goal.get("ring") or next((r for r in (self.rings or ()) if r in nav.open_waypoints(m)), None)
            if target_ring and target_ring != "camp" and target_ring in nav.open_waypoints(m):
                code, r = api.act(mover, {"action": "teleport", "to": target_ring})
                if code == 200:
                    self.say("INFO", "%s teleports to %s to rejoin squad: 200" % (api.who(mover), target_ring))
                    units[mover]["ring"] = target_ring
                    wp = next((w for w in m.get("waypoints") or [] if w.get("ring") == target_ring), None)
                    if wp:
                        units[mover]["x"], units[mover]["y"] = wp["x"], wp["y"]
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


    def fresh(self, unit):
        """A hero's fresh /me; the API client handles transport retries."""
        m, _v = api.me(unit)
        return m

    def say(self, level, text):
        api.log(level, "[%s] %s" % (self.name, text))

    def fetch(self, fin_id, fin):
        """The nearest creatures for this finisher, from /world/hunt (relative to the unit, up to 20 of a level
        range): one read instead of eight, and not limited to what the 15x15 view shows."""
        land_on = self.is_land_active(fin_id)
        # 向上跨级范围限制 LEVEL_SPAN 依然保留，腐化区域内只忽略向下等级差
        lo = rules.LEVEL_MIN if land_on else max(rules.LEVEL_MIN, fin["level"] - rules.MAX_LEVEL_DIFF)
        hi = fin["level"] + LEVEL_SPAN
        code, h = api.call("GET", "/world/hunt?min=%d&max=%d&limit=20%s" % (lo, hi, "&unit=%d" % fin_id if fin_id else ""))
        if code == 200 and isinstance(h.get("creatures"), list):
            from . import bestiary
            for c in h["creatures"]:
                if "hp" not in c:
                    c["hp"] = c.get("max_hp") or bestiary.hp_at(c.get("kind"), c.get("level", 1), bool(c.get("elite")))
            return h["creatures"]
        return None

    def usable(self, t, fin_id=None):
        """Plain creature, inside this squad's region, not known to be gone."""
        if is_boss_creature(t):
            return time.time() - self.gone.get(t["id"], 0) > GONE_SECS

        land_on = self.is_land_active(fin_id)
        in_land_area = False
        if landwatch.AREA[:2]:
            ax, ay, reach = landwatch.AREA
            in_land_area = max(abs(t["x"] - ax), abs(t["y"] - ay)) <= reach

        # When corrupted land is active for this unit:
        # User rule (2026-11-06): 忽略腐化区域内的等级限制，只限区域内的精英怪
        if land_on:
            if not in_land_area:
                return False
            # 腐化区域内只限精英怪和Boss
            if not (t.get("elite") or is_boss_creature(t)):
                return False

        if in_land_area:
            # 腐化区域内无论何时只限精英怪和Boss
            if not (t.get("elite") or is_boss_creature(t)):
                return False

        if self.rings and t.get("ring") and t.get("ring") not in self.rings and not in_land_area:
            return False
        if not in_land_area and t.get("elite") and not self.elites:
            return False  # outside corrupted land, only squads configured with elites hunt them
        if (t.get("corrupted") and not in_land_area) or (t.get("boss") and not in_land_area):
            return False
        if not in_land_area and not self.region(t["x"], t["y"]):
            return False
        return time.time() - self.gone.get(t["id"], 0) > GONE_SECS

    def pick_target(self, fin_id, fin, ready, centre=None):
        """Creatures the squad can take, best first: experience (level*12) per health to chew through and per step
        to walk. A creature the finisher and its helpers can wear down together (their attack stats say how much)
        is fair game, so a strong lead can also hunt alone."""
        land_on = self.is_land_active(fin_id)
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
                if t["id"] in seen or not self.usable(t, fin_id):
                    continue
                is_boss = is_boss_creature(t)
                in_land = land_on or (landwatch.AREA[:2] and max(abs(t["x"] - landwatch.AREA[0]), abs(t["y"] - landwatch.AREA[1])) <= landwatch.AREA[2])
                if not is_boss:
                    if not self.elites and not in_land and any(o.get("elite") and o["id"] != t["id"]
                                               and max(abs(o["x"] - t["x"]), abs(o["y"] - t["y"])) <= ELITE_AVOID for o in crowd):
                        continue   # (the owner, 01:16) a squad that does not fight elites keeps away from them: no target near one
                    if not in_land and CROWD and sum(1 for o in crowd if o["id"] != t["id"] and max(abs(o["x"] - t["x"]), abs(o["y"] - t["y"])) <= 6) >= CROWD:
                        continue   # (931 fell beside a deer at (92,124) with a wolf, a spider, a fox and a beaver around it)
                    if not land_on and self.close and pals and self.idle < IDLE_RELAX and min(max(abs(t["x"] - p["x"]), abs(t["y"] - p["y"])) for p in pals) > self.close:
                        continue   # (the pair hunts together: the lord finished nearly everything while the main stood 76 tiles away)
                    seen.add(t["id"])
                    wc = walk_cost(fin, t["x"] - 1, t["y"])
                    if wc > (fin["ap"] - 5) * (0.85 if self.idle >= IDLE_RELAX else 0.7):  # keep points for the fight itself (a squad idle for a while walks further)
                        continue
                    # 向上跨级范围限制 LEVEL_SPAN 依然保留（不打高于自身 level + LEVEL_SPAN 的怪）
                    if t["level"] > fin["level"] + LEVEL_SPAN:
                        continue
                    # 腐化区域内忽略向下等级限制 (MAX_LEVEL_DIFF)，非腐化区域保留
                    if not in_land and t["level"] < max(rules.LEVEL_MIN, fin["level"] - rules.MAX_LEVEL_DIFF):
                        continue
                    if not in_land and (danger.strong_near(t["x"], t["y"], fin["level"], margin=4) or danger.bad_zone(t["x"], t["y"], fin["level"])):
                        continue  # a strong creature stands by it, or the area is a crowd of them / where somebody fell
                else:
                    seen.add(t["id"])
                    wc = walk_cost(fin, t["x"] - 1, t["y"])
                    if wc > fin["ap"] - 3:
                        continue
                out.append((t, wc))
        def _score(pair):
            target, wc = pair
            if is_boss_creature(target):
                return 10000000 - wc * 10
            boost = 2.0 if (land_on and target.get("corrupted")) else (1.5 if self.elites and fin["hp"] >= 0.85 * fin["max_hp"] else 1.0)
            return target["level"] * 12 * (boost if target.get("elite") or target.get("corrupted") else 1) / ((target.get("hp") or target.get("max_hp") or 100) + wc * 3)

        out.sort(key=lambda p: -_score(p))
        return [t for t, _ in out]

    def feasible(self, t, fin_id, ready):
        """The helpers that can reach the creature, if with the finisher they can wear it down to FINISH_AT."""
        land_on = self.is_land_active(fin_id)
        stand = [t["x"] - 1, t["y"]]
        if is_boss_creature(t):
            # Boss / guardian in corrupted land: attack immediately!
            if combat.safe_round(ready[fin_id], t, line_pct=15):
                return []
            return None
        if t.get("elite"):  # an elite is fought by the finisher alone, if its health and the model say it holds out
            return [] if combat.solo_elite(ready[fin_id], t)[0] else None
        # a helper keeps HOME_AP after the walk and a swing: hurt, it must still be able to go home (932 fell to 9 hp
        # with 7 points, unable to leave)
        team = [(u, m) for u, m in ready.items() if u != fin_id
                and (land_on or t.get("level", 1) >= m["level"] - rules.MAX_LEVEL_DIFF)
                and m["ap"] >= walk_cost(m, stand[0], stand[1]) + 6 + HOME_AP]
        thp = t.get("hp") or t.get("max_hp") or 100
        t["hp"] = thp
        if thp <= 45:
            return team
        ok, dealt, cost, detail = combat.plan([m for _, m in team], ready[fin_id], t)
        if not ok and not team and (SOLO_FLOOR is not None or land_on):
            # nobody to help (the others are gathering): the finisher alone, if it ends with a fair part of its health
            # (the owner: the main character levels up alone, in the forest)
            if combat.solo_elite(ready[fin_id], dict(t, elite=[]), floor=0.3 if land_on else SOLO_FLOOR)[0]:
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
        # one cheap read of /world/hero/ap: when nobody of the squad has points to act, don't read every hero
        # (the points come back one by one: wait for the first)
        active = [u for u in self.members if (rules.AUTO_HUNT or queue.hunting(u))
                  and not lease.leased(u, self.name)]
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
            if m["hp"] > 0 and m["ring"] != "camp":
                self.food_heal(u, m)   # food healing outranks going home: heal first before ambush/recover!
                if m["ap"] >= HOME_AP:
                    def _log_ambush(t):
                        if "beatable" in t or "fighting back" in t:
                            self.say("INFO", t)
                        else:
                            self.say("WARNING", t)
                    if ambush.handle(u, m, view, say=_log_ambush) == "home":   # a real ambush: fought if winnable, else home
                        m["ring"] = "camp"
            care.claim_chest(u, m)   # (the main character only, in the camp, once a day after 12:00 UTC+8)
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
                    if g.get("rarity") in ("treasured", "珍藏") and g.get("level", 0) < rules.IDENTIFY_MIN_LEVEL:
                        continue
                    cost = 5 + 2 * g["level"]
                    if m.get("coins", 0) - cost < rules.IDENTIFY_KEEP_COINS:
                        break
                    code, _r = api.act(u, {"action": "identify", "item": g["id"]})
                    if code == 200:
                        m["coins"] = m.get("coins", 0) - cost
                care.auto_salvage(u, m)
            if m["ring"] == "camp":
                # Ready heroes in camp travel to hunting ring waypoint rather than walking across meadow
                if (m.get("worn") or {}).get("weapon") and m.get("ap", 0) >= 20 and m.get("hp", 0) >= 0.7 * m.get("max_hp", 1):
                    land_on = self.is_land_active(u)
                    if land_on and landwatch.AREA[:2]:
                        dest = tuple(landwatch.AREA[:2])
                        plan = nav.plan_route(m, dest)
                        for step in plan.get("steps", []):
                            if step.get("action") == "travel":
                                target_ring = step.get("to")
                                code, _r = api.act(u, {"action": "travel", "to": target_ring})
                                if code == 200:
                                    self.say("INFO", "%s uses %s waypoint for corrupted land at %s (plan '%s', ~%.1f AP): 200" % (
                                        api.who(u), target_ring, dest, plan["name"], plan["ap"]))
                                    m["ring"] = target_ring
                                    wp = next((w for w in m.get("waypoints") or [] if w.get("ring") == target_ring), None)
                                    if wp:
                                        m["x"], m["y"] = wp["x"], wp["y"]
                                break
                    else:
                        dest = self.center or (rules.WAYPOINTS.get(self.rings[0]) if self.rings else None)
                        if dest and not queue.resting(u) and not (landwatch.STATE["stage"] and u in rules.CORRUPT_LAND["units"]):
                            plan = nav.plan_route(m, dest)
                            step0 = (plan.get("steps") or [{}])[0]
                            if step0.get("action") == "travel":
                                target_ring = step0.get("to")
                                code, _r = api.act(u, {"action": "travel", "to": target_ring})
                                if code == 200:
                                    self.say("DEBUG", "%s uses %s waypoint to enter hunting ground (plan '%s', ~%.1f AP): 200" % (
                                        api.who(u), target_ring, plan["name"], plan["ap"]))
                                    m["ring"] = target_ring
                                    wp = next((w for w in m.get("waypoints") or [] if w.get("ring") == target_ring), None)
                                    if wp:
                                        m["x"], m["y"] = wp["x"], wp["y"]
                            elif step0.get("action") == "walk":
                                d_dest = max(abs(m["x"] - dest[0]), abs(m["y"] - dest[1]))
                                step = min(d_dest, int((m["ap"] - 10) / 1.3))
                                if step >= 3:
                                    k = step / d_dest
                                    to = [int(round(m["x"] + (dest[0] - m["x"]) * k)), int(round(m["y"] + (dest[1] - m["y"]) * k))]
                                    code, r = api.act(u, {"action": "move", "to": to, "until_there": True})
                                    self.say("DEBUG", "%s sets out from camp toward %s (plan '%s'): %s" % (
                                        api.who(u), to, plan["name"], code))
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
            elif m["ring"] not in (self.rings or ()):
                # far from the squad's hunting ground: use fastest path to squad's ring / center
                dest = self.center or (344, 256)
                target_ring = next((r for r in (self.rings or ()) if r in nav.open_waypoints(m)), None)
                d_dest = max(abs(m["x"] - dest[0]), abs(m["y"] - dest[1]))
                if d_dest > 30 and target_ring and m.get("coins", 0) >= 20 and m.get("ap", 0) >= 12:
                    code, r = api.act(u, {"action": "teleport", "to": target_ring})
                    if code == 200:
                        self.say("INFO", "%s teleports to %s to resume hunt: 200" % (api.who(u), target_ring))
                        m["ring"] = target_ring
                        wp = next((w for w in m.get("waypoints") or [] if w.get("ring") == target_ring), None)
                        if wp:
                            m["x"], m["y"] = wp["x"], wp["y"]
                        continue
                step = min(d_dest, int((m["ap"] - 10) / 1.3))
                if step >= 3:
                    k = step / d_dest
                    to = [int(round(m["x"] + (dest[0] - m["x"]) * k)), int(round(m["y"] + (dest[1] - m["y"]) * k))]
                    code, r = api.act(u, {"action": "move", "to": to, "until_there": True})
                    self.say("DEBUG", "%s walks directly to hunt ground %s: %s" % (api.who(u), to, code))
                    continue
        unarmed = {u for u, m in units.items() if not (m.get("worn") or {}).get("weapon")}
        for u in unarmed:
            m = units[u]
            if m["ring"] != "camp" and m["ap"] >= HOME_AP:
                api.act(u, {"action": "home", "why": "unarmed: returning to camp to get a weapon"})
                m["ring"] = "camp"
        sit_out = self.hold | resting | unarmed
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
            time.sleep(wait)
            return
        self.idle = 0
        fin = ready[fin_id]
        self.fought.add(fin_id)   # (its talent points are looked at once the fight is over: run())
        stand = [t["x"] - 1, t["y"]]
        t_desc = what(t)
        state.target(fin_id, t["x"], t["y"], t_desc)
        state.note(fin_id, "交战 %s" % t_desc)
        for u, _ in team:
            state.target(u, t["x"], t["y"], t_desc)
            state.note(u, "协助 %s" % t_desc)
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
            state.clear_target(fin_id)
            for u, _ in team:
                state.clear_target(u)
            return
        t["hp"] = there[0].get("hp") or there[0].get("max_hp") or t.get("hp") or bestiary.hp_at(t.get("kind"), t.get("level", 1), bool(t.get("elite")))
        if not is_boss_creature(t) and self.feasible(t, fin_id, ready) is None and t["hp"] > 45:
            self.skipped[t["id"]] = time.time()
            self.say("WARNING", "%s #%s has more health than seen (%s): letting it go" % (what(t), t["id"], t["hp"]))
            state.clear_target(fin_id)
            for u, _ in team:
                state.clear_target(u)
            return
        for u, _m in team:
            api.act(u, {"action": "move", "to": stand})
        time.sleep(6)
        if t.get("elite") or is_boss_creature(t):
            fin2 = self.fresh(fin_id)
            api.wait_busy(fin2)
            hp = t["hp"]
            is_boss = is_boss_creature(t)
            stop_line = 15 if is_boss else ELITE_STOP
            max_bursts = 30 if is_boss else 14
            for _ in range(max_bursts):
                if not fin2 or not combat.safe_round(fin2, t, stop_line):
                    code, r = 0, {"broke_off": True}
                    break
                req = {"action": "attack", "target": t["id"], "rounds": 6, "stop_below": stop_line}
                code, r = api.act(fin_id, req)
                if code == 200:
                    combat.record(fin_id, fin, dict(t, hp=hp), req, r)
                    got = creature_hp(r)
                    hp = got if got is not None else hp
                if code != 200 or r.get("killed") or r.get("broke_off") or r.get("down"):
                    break
                nxt = api.after_attack(fin2, r)
                if nxt:
                    fin2 = nxt[0]
                    time.sleep(nxt[1])
                else:
                    fin2 = self.fresh(fin_id)
                    api.wait_busy(fin2)
            if code == 200 and r.get("killed"):
                self.say("INFO", "%s %s killed %s (loot: %s)" % (
                    "boss hunter" if is_boss else "elite hunter",
                    api.who(fin_id), what(t), json.dumps(r.get("loot") or r.get("got") or "", ensure_ascii=False)[:120]))
            else:
                self.skipped[t["id"]] = time.time()
                self.say("WARNING", "%s not finished (code %s, creature hp %s): let go" % (what(t), code, hp))
            state.clear_target(fin_id)
            state.note(fin_id, "狩猎待命(%s)" % self.name)
            return

        fighters = [fin_id] + [u for u, _ in team]
        hp = t["hp"]

        if len(fighters) == 1:
            u = fin_id
            fin2 = self.fresh(u)
            api.wait_busy(fin2)
            killed = False
            for _ in range(15):
                if not fin2 or not combat.safe_round(fin2, t, FIN_STOP):
                    break
                if fin2.get("ap", 0) < HOME_AP + 3:
                    break
                rounds = 6 if hp > 100 else (2 if hp > 30 else 1)
                req = {"action": "attack", "target": t["id"], "rounds": rounds, "stop_below": FIN_STOP}
                code, r = api.act(u, req)
                if code == 200:
                    combat.record(u, fin, dict(t, hp=hp), req, r)
                    got = creature_hp(r)
                    hp = got if got is not None else hp
                    if r.get("killed"):
                        killed = True
                        self.say("INFO", "%s killed %s (damage [%s: %d], loot: %s)" % (
                            api.who(u), what(t), api.who(u), int(t["hp"]),
                            json.dumps(r.get("loot") or r.get("got") or "", ensure_ascii=False)[:80]))
                        break
                    if r.get("broke_off") or r.get("down"):
                        break
                elif code == 404:
                    killed = True
                    break
                elif code == 400 and "busy" in str(r.get("error", "")).lower():
                    api.wait_busy(fin2)
                    fin2 = self.fresh(u)
                    continue
                else:
                    break
                nxt = api.after_attack(fin2, r)
                if nxt:
                    fin2 = nxt[0]
                    time.sleep(nxt[1])
                else:
                    fin2 = self.fresh(u)
                    api.wait_busy(fin2)
            if not killed and hp > 0:
                self.skipped[t["id"]] = time.time()
                self.say("WARNING", "%s not finished (code %s, hp left: %s): let go" % (what(t), code, hp))
            state.clear_target(u)
            state.note(u, "狩猎待命(%s)" % self.name)
            return

        damage = {u: 0.0 for u in fighters}
        target_dmg = hp / len(fighters)
        active = list(fighters)
        hpn = {}
        free_at = {u: 0.0 for u in fighters}
        killed = False
        deadline = time.time() + 180

        # Rotate attacks to equalize damage across all participating teammates (equal EXP)
        while active and not killed and hp > 0 and time.time() < deadline:
            now = time.time()
            due = [u for u in active if free_at[u] <= now]
            if not due:
                time.sleep(max(0.3, min(5.0, min(free_at[u] for u in active) - now)))
                continue

            # Prioritize teammates who have not reached their target damage share, then lowest damage dealt
            due.sort(key=lambda u: (damage[u] >= target_dmg, damage[u], -ready[u]["ap"]))
            u = due[0]

            peak = combat.my_blow(ready[u].get("attack"), ready[u].get("crit"))
            under = [v for v in active if damage[v] < target_dmg]
            # If u has reached target share and another teammate hasn't, yield final blow to let them catch up
            if under and damage[u] >= target_dmg and hp <= peak:
                wait_v = min(free_at[v] for v in under)
                if wait_v - now <= 4.0:
                    time.sleep(max(0.3, wait_v - now))
                    continue

            quota_left = target_dmg - damage[u]
            rounds = 2 if hp > 180 and quota_left > peak * 2 else 1

            hnow = dict(ready[u], hp=hpn.get(u, ready[u]["hp"]))
            if not combat.safe_round(hnow, t, FIN_STOP):
                active.remove(u)
                continue

            req = {"action": "attack", "target": t["id"], "rounds": rounds, "stop_below": FIN_STOP}
            code, r = api.act(u, dict(req, wait=False))
            if code == 200:
                if (r.get("me") or {}).get("hp") is not None:
                    hpn[u] = r["me"]["hp"]
                if r.get("rounds") or r.get("exchanges"):
                    combat.record(u, ready[u], dict(t, hp=hp), req, r)
                    got = creature_hp(r)
                    dmg = (hp - got) if got is not None else 0
                    if r.get("killed"):
                        dmg = max(dmg, hp)
                        hp = 0
                        killed = True
                    elif got is not None:
                        hp = got
                    damage[u] += max(0, dmg)
                    busy_sec = api.iso_wait(((r.get("me") or {}).get("busy_until")) or r.get("busy_until"))
                    ready_sec = (r.get("me") or {}).get("ready_in_seconds") or r.get("ready_in_seconds") or 0
                    free_at[u] = time.time() + max(busy_sec, ready_sec, rounds * 5.0) + 0.5
                    if r.get("killed"):
                        killed = True
                        dmg_str = ", ".join("%s: %d" % (api.who(f), int(damage[f])) for f in fighters)
                        self.say("INFO", "%s killed %s (damage [%s], loot: %s)" % (
                            api.who(u), what(t), dmg_str,
                            json.dumps(r.get("loot") or r.get("got") or "", ensure_ascii=False)[:80]))
                    elif r.get("broke_off") or r.get("down"):
                        active.remove(u)
                    elif ((r.get("me") or {}).get("ap") or 99) < HOME_AP + 3:
                        active.remove(u)
            elif code == 404:
                killed = True
            elif code == 429 and "action points" not in str(r.get("error", "")):
                free_at[u] = time.time() + (r.get("retry_after_seconds") or 6) + 0.5
            elif code == 400 and "busy" in str(r.get("error", "")).lower():
                busy_w = api.iso_wait((r.get("me") or {}).get("busy_until") or r.get("busy_until"))
                free_at[u] = time.time() + max(4.0, busy_w + 0.5)
            else:
                active.remove(u)

        if not killed and hp > 0:
            self.skipped[t["id"]] = time.time()
            self.say("WARNING", "%s not finished (hp left: %s): let go" % (what(t), hp))
        for u in fighters:
            state.clear_target(u)
            state.note(u, "狩猎待命(%s)" % self.name)

    def run(self):
        """Hunt for ever (the goals only decide who finishes first)."""
        while True:
            try:
                self.cycle()
                for u in list(self.fought):   # the owner (2026-11-04): talent points and food heal after EVERY fight
                    m, _v = api.me(u)
                    if m:
                        care.spend_talents(u, m)
                        if m.get("hp", 0) < m.get("max_hp", 1) * rules.HEAL_BELOW:
                            self.food_heal(u, m)
                self.fought.clear()
            except Exception as e:
                import traceback
                self.say("ERROR", "exception %r\n%s" % (e, traceback.format_exc()))
                time.sleep(20)
            time.sleep(2)
