"""Autonomous solo hunting: each hero operates independently without squad dependencies.

Each hero has its own center coordinate, hunting rings, and configuration. Heroes hunt solo,
relying on dynamic attack rounds and safety thresholds (combat.safe_round) to engage creatures.
When no target is nearby, heroes drift toward their designated center points.

    Hunter(928, center=(405, 175), rings=("highlands",), elites=True).run()
"""
import json
import math
import threading
import time

from . import abyss, ambush, api, bestiary, care, combat, danger, landwatch, lease, nav, queue, rules, state, tribe

FIN_STOP = round(rules.FIGHT_FLOOR * 100)
ELITE_STOP = FIN_STOP
IDLE_RELAX = 3
ELITE_AVOID = 7
CROWD = None
SOLO_FLOOR = rules.FIGHT_FLOOR
HOME_AP = 8
GONE_SECS = 600
LEVEL_SPAN = rules.LEVEL_SPAN
CLAIMED = {}          # creature id -> (owner_name, time)
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


def claim(creature_id, owner_name, secs=150):
    """Reserve a creature for a hunter. False if another hunter holds it."""
    with _claim_lock:
        owner = CLAIMED.get(creature_id)
        if owner and owner[0] != owner_name and time.time() - owner[1] < secs:
            return False
        CLAIMED[creature_id] = (owner_name, time.time())
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


class Hunter:
    """An autonomous solo hunter representing a single hero."""

    def __init__(self, unit=None, center=None, rings=None, elites=False, bounties=False,
                 bread=False, bandage=False, hero_goal=rules.HERO_GOAL, land=False,
                 name=None, members=None, region=None, close=None, hold=(), **kwargs):
        if unit is None and members:
            unit = members[0]
        self.unit = unit
        self.name = name or api.who(unit)
        self.center = center
        self.rings = tuple(rings) if rings else None
        self.elites = elites
        self.bounties = bounties or kwargs.get("claims_bounties", False)
        self.bread = bread
        self.heal_items = care.HEAL_ITEMS if bandage else ("bread", "roast")
        self.hero_goal = hero_goal
        self.land = land
        self.region = region or (lambda x, y: True)
        self.close = close
        self.members = [unit]
        self.hold = set(hold)
        self.skipped = {}
        self.gone = {}
        self.fallen_time = 0
        self.last_read = None
        self.fought = False
        self.view = {}
        self.idle = 0

    def say(self, level, text):
        api.log(level, "[%s] %s" % (self.name, text))

    def is_land_active(self, unit=None):
        """Is corrupted land currently active for this unit?"""
        if not landwatch.STATE.get("on") or not landwatch.AREA[:2]:
            return False
        cfg_units = rules.CORRUPT_LAND.get("units", [0, 928])
        uid = self.unit if unit is None else unit
        return (uid in cfg_units or (uid is None and 0 in cfg_units) or (uid == 0 and None in cfg_units))

    def food_heal(self, m):
        """Eat and heal with food/bandages before any return home is considered."""
        care.eat(self.unit, m)
        if self.bread:
            care.heal_with_items(self.unit, m, items=self.heal_items)

    def queue_recover_if_hurt(self, m):
        """Queue recovery only for a hero still below rules.HOME_BELOW after food healing."""
        if rules.AUTO_RECOVER and 0 < m["hp"] < m["max_hp"] * rules.HOME_BELOW:
            queue.add(self.unit, "recover", "resume-hunt", prio=9)
            return True
        return False

    def fresh(self, unit=None):
        """Fetch fresh /me state."""
        uid = self.unit if unit is None else unit
        m, _v = api.me(uid)
        return m

    def fetch(self, fin=None, fin_id=None):
        """Fetch nearest creatures from /world/hunt."""
        uid = self.unit if fin_id is None else fin_id
        if fin is None:
            fin = self.fresh(uid)
            if not fin:
                return None
        land_on = self.is_land_active(uid)
        lo = rules.LEVEL_MIN if land_on else max(rules.LEVEL_MIN, fin["level"] - rules.MAX_LEVEL_DIFF)
        hi = fin["level"] + LEVEL_SPAN
        code, h = api.call("GET", "/world/hunt?min=%d&max=%d&limit=40%s" % (lo, hi, "&unit=%d" % uid if uid else ""))
        if code == 200 and isinstance(h.get("creatures"), list):
            for c in h["creatures"]:
                if "hp" not in c:
                    c["hp"] = c.get("max_hp") or bestiary.hp_at(c.get("kind"), c.get("level", 1), bool(c.get("elite")))
            return h["creatures"]
        return None

    def usable(self, t, fin_id=None):
        """Check if creature t is valid for this hunter."""
        if is_boss_creature(t):
            return time.time() - self.gone.get(t["id"], 0) > GONE_SECS

        land_on = self.is_land_active(fin_id)
        in_land_area = False
        if landwatch.AREA[:2]:
            ax, ay, reach = landwatch.AREA
            in_land_area = max(abs(t["x"] - ax), abs(t["y"] - ay)) <= reach

        if land_on or in_land_area:
            if not in_land_area:
                return False
            if not (t.get("elite") or is_boss_creature(t)):
                return False

        if self.rings and t.get("ring") and t.get("ring") not in self.rings and not in_land_area:
            return False
        if not in_land_area and t.get("elite") and not self.elites:
            return False
        if (t.get("corrupted") and not in_land_area) or (t.get("boss") and not in_land_area):
            return False
        if not in_land_area and not self.region(t["x"], t["y"]):
            return False
        return time.time() - self.gone.get(t["id"], 0) > GONE_SECS

    def pick_target(self, m_or_cand, fm=None, ready=None, centre=None):
        """Pick candidate creatures ranked by score."""
        if fm is not None:
            cand = m_or_cand
            fin = fm
        else:
            cand = self.unit
            fin = m_or_cand

        land_on = self.is_land_active(cand)
        around = self.view if hasattr(self, "view") and isinstance(self.view, dict) else {}
        seen, out = set(), []
        found = self.fetch(fin, cand)
        if found is None:
            return None
        pools = [found, [t for t in (around.get("things") or []) if t.get("type") == "creature"]]
        crowd = [c for pool in pools for c in pool]
        for pool in pools:
            for t in pool:
                if "hp" not in t:
                    t["hp"] = t.get("max_hp") or bestiary.hp_at(t.get("kind"), t.get("level", 1), bool(t.get("elite")))
                if t["id"] in seen or not self.usable(t, cand):
                    continue
                is_boss = is_boss_creature(t)
                in_land = land_on or (landwatch.AREA[:2] and max(abs(t["x"] - landwatch.AREA[0]), abs(t["y"] - landwatch.AREA[1])) <= landwatch.AREA[2])
                if not in_land:
                    center = self.center if self.center else (fin["x"], fin["y"])
                    d_center = max(abs(center[0] - t["x"]), abs(center[1] - t["y"]))
                    if d_center > getattr(rules, "TARGET_MAX_DIST", 25):
                        continue
                if not is_boss:
                    if not self.elites and not in_land and any(o.get("elite") and o["id"] != t["id"]
                                               and max(abs(o["x"] - t["x"]), abs(o["y"] - t["y"])) <= ELITE_AVOID for o in crowd):
                        continue
                    if not in_land and CROWD and sum(1 for o in crowd if o["id"] != t["id"] and max(abs(o["x"] - t["x"]), abs(o["y"] - t["y"])) <= 6) >= CROWD:
                        continue
                    seen.add(t["id"])
                    wc = walk_cost(fin, t["x"] - 1, t["y"])
                    if wc > (fin["ap"] - 5) * (0.85 if self.idle >= IDLE_RELAX else 0.7):
                        continue
                    if t["level"] > fin["level"] + LEVEL_SPAN:
                        continue
                    if not in_land and t["level"] < max(rules.LEVEL_MIN, fin["level"] - rules.MAX_LEVEL_DIFF):
                        continue
                    if not in_land and (danger.strong_near(t["x"], t["y"], fin["level"], margin=4) or danger.bad_zone(t["x"], t["y"], fin["level"])):
                        continue
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

    def feasible(self, t, m_or_cand=None, ready=None):
        """Solo feasibility check: can this hero defeat creature t alone?"""
        if ready and m_or_cand in ready:
            m = ready[m_or_cand]
        elif isinstance(m_or_cand, dict):
            m = m_or_cand
        else:
            m = self.fresh() or {}
        land_on = self.is_land_active()
        if is_boss_creature(t):
            return [] if combat.safe_round(m, t, line_pct=15) else None
        if t.get("elite"):
            return [] if combat.solo_elite(m, t)[0] else None
        thp = t.get("hp") or t.get("max_hp") or 100
        t["hp"] = thp
        if thp <= 45:
            return []
        floor = 0.3 if land_on else (SOLO_FLOOR or 0.4)
        if combat.solo_elite(m, dict(t, elite=[]), floor=floor)[0]:
            return []
        return None

    def claim_bounties(self, m):
        """Claim finished daily bounties if in camp."""
        if not self.bounties or m.get("ring") != "camp":
            return
        ready = [b for b in (m.get("bounties") or []) if b["done"] >= b["n"] and not b["claimed"]]
        if not ready:
            return
        code, r = api.act(self.unit, {"action": "bounty"})
        self.say("INFO", "bounty claim by %s: %s %s" % (
            self.name, code, json.dumps({k: r.get(k) for k in ("claimed", "xp", "coins", "got", "level_up", "error")}, ensure_ascii=False)))

    def execute_combat(self, t, m):
        """Execute solo combat against creature t."""
        self.idle = 0
        self.fought = True
        u = self.unit
        stand = [t["x"] - 1, t["y"]]
        t_desc = what(t)
        state.target(u, t["x"], t["y"], t_desc)
        state.note(u, "fighting %s" % t_desc)
        self.say("DEBUG", "%s L%s xp %s/%s hp %s ap %s -> %s hp %s at (%s,%s)" % (
            self.name, m["level"], m["xp"], m["xp_next"], m["hp"], m["ap"], t_desc,
            t["hp"], t["x"], t["y"]))

        # Move to stand position adjacent to target
        api.act(u, {"action": "move", "to": stand, "until_there": True})
        fm2, around = api.me(u)
        api.wait_busy(fm2)
        fm2, around = api.me(u)
        there = [c for c in (around or {}).get("things") or [] if c.get("type") == "creature" and c["id"] == t["id"]]
        if not there:
            self.gone[t["id"]] = time.time()
            self.say("DEBUG", "%s #%s gone or claimed on arrival: aborted" % (t_desc, t["id"]))
            state.clear_target(u)
            return
        if "x" in there[0] and "y" in there[0]:
            t["x"], t["y"] = there[0]["x"], there[0]["y"]
        if max(abs(fm2["x"] - t["x"]), abs(fm2["y"] - t["y"])) > 1:
            adj_tiles = [(t["x"] + dx, t["y"] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx != 0 or dy != 0)]
            adj_tiles.sort(key=lambda p: (abs(p[0] - fm2["x"]) + abs(p[1] - fm2["y"])))
            api.act(u, {"action": "move", "to": list(adj_tiles[0]), "until_there": True})
            fm2, around = api.me(u)
            api.wait_busy(fm2)
            fm2, around = api.me(u)
            if max(abs(fm2["x"] - t["x"]), abs(fm2["y"] - t["y"])) > 1:
                self.say("DEBUG", "%s #%s cannot step adjacent (dist %d): aborted" % (
                    t_desc, t["id"], max(abs(fm2["x"] - t["x"]), abs(fm2["y"] - t["y"]))))
                self.skipped[t["id"]] = time.time()
                state.clear_target(u)
                return
        t["hp"] = there[0].get("hp") or there[0].get("max_hp") or t.get("hp") or bestiary.hp_at(t.get("kind"), t.get("level", 1), bool(t.get("elite")))
        is_boss = is_boss_creature(t)
        if not is_boss and self.feasible(t, m) is None and t["hp"] > 45:
            self.skipped[t["id"]] = time.time()
            self.say("WARNING", "%s #%s actual HP exceeds estimate (%s HP, cannot solo): skipped" % (t_desc, t["id"], t["hp"]))
            state.clear_target(u)
            return

        fin2 = self.fresh()
        api.wait_busy(fin2)
        hp = t["hp"]
        stop_line = 15 if is_boss else (ELITE_STOP if t.get("elite") else FIN_STOP)
        max_bursts = 30 if is_boss else 15
        reason = ""
        killed = False
        code = 200

        for _ in range(max_bursts):
            if not fin2:
                reason = "cannot fetch unit state"
                break
            if not combat.safe_round(fin2, t, stop_line):
                if care.has_heal_items(fin2):
                    care.heal_with_items(u, fin2, below=0.7, target=0.85)
                    fin2 = self.fresh()
                if not combat.safe_round(fin2, t, stop_line):
                    reason = "next round drops health below %d%% floor (hp %d/%d)" % (stop_line, fin2.get("hp", 0), fin2.get("max_hp", 0))
                    break
            if fin2.get("ap", 0) < HOME_AP + 3:
                reason = "low AP (AP %d < reserve %d)" % (fin2.get("ap", 0), HOME_AP + 3)
                break
            est_dmg = max(15, fin2.get("attack", 20) - max(0, t.get("defense", 0) // 2))
            rounds = max(1, min(6, int(math.ceil(hp / est_dmg))))
            req = {"action": "attack", "target": t["id"], "rounds": rounds, "stop_below": stop_line}
            code, r = api.act(u, req)
            if code == 200:
                combat.record(u, m, dict(t, hp=hp), req, r)
                got = creature_hp(r)
                hp = got if got is not None else hp
                if r.get("killed"):
                    killed = True
                    break
                if r.get("broke_off"):
                    fin2 = self.fresh()
                    api.wait_busy(fin2)
                    healed = False
                    if care.has_heal_items(fin2):
                        if care.heal_with_items(u, fin2, below=0.7, target=0.85) > 0:
                            healed = True
                            fin2 = self.fresh()
                    if hp <= 35 and combat.safe_round(fin2, t, 10):
                        req_last = {"action": "attack", "target": t["id"], "rounds": 1, "stop_below": 10}
                        code_last, r_last = api.act(u, req_last)
                        if code_last == 200:
                            combat.record(u, m, dict(t, hp=hp), req_last, r_last)
                            got = creature_hp(r_last)
                            hp = got if got is not None else hp
                            if r_last.get("killed"):
                                killed = True
                                break
                    elif healed and combat.safe_round(fin2, t, stop_line):
                        continue
                    reason = "combat broke off (broke_off)"
                    break
                if r.get("down"):
                    reason = "unit fell (down)"
                    break
            elif code == 202:
                api.wait_result(r)
                if r.get("killed"):
                    killed = True
                    break
                got = creature_hp(r)
                if got is not None:
                    hp = got
                fin2 = self.fresh()
                api.wait_busy(fin2)
                continue
            elif code == 404:
                killed = True
                break
            elif code == 400 and "stand next" in str(r.get("error", "")).lower():
                fm_curr, around_curr = api.me(u)
                api.wait_busy(fm_curr)
                there_curr = [c for c in (around_curr or {}).get("things") or [] if c.get("type") == "creature" and c["id"] == t["id"]]
                if there_curr and "x" in there_curr[0]:
                    t["x"], t["y"] = there_curr[0]["x"], there_curr[0]["y"]
                    adj_tiles = [(t["x"] + dx, t["y"] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx != 0 or dy != 0)]
                    adj_tiles.sort(key=lambda p: (abs(p[0] - fm_curr["x"]) + abs(p[1] - fm_curr["y"])))
                    api.act(u, {"action": "move", "to": list(adj_tiles[0]), "until_there": True})
                    fin2 = self.fresh()
                    api.wait_busy(fin2)
                    continue
                else:
                    reason = "target left line of sight"
                    break
            elif code == 400 and "busy" in str(r.get("error", "")).lower():
                api.wait_busy(fin2)
                fin2 = self.fresh()
                continue
            else:
                reason = "api error (code %s: %s)" % (code, r.get("error", ""))
                break
            nxt = api.after_attack(fin2, r)
            if nxt:
                fin2 = nxt[0]
                time.sleep(nxt[1])
            else:
                fin2 = self.fresh()
                api.wait_busy(fin2)

        if killed:
            self.say("INFO", "killed %s" % t_desc)
        else:
            self.skipped[t["id"]] = time.time()
            self.say("WARNING", "%s not killed (hp left: %s, reason: %s): standing by" % (
                t_desc, hp, reason or ("code %s" % code)))
            fin_end = self.fresh()
            if fin_end and fin_end.get("hp", 0) < fin_end.get("max_hp", 1) * rules.HOME_BELOW:
                api.act(u, {"action": "home", "why": "hurt in combat: health %d/%d is under %d%%" % (
                    fin_end["hp"], fin_end["max_hp"], 100 * rules.HOME_BELOW)})

        state.clear_target(u)
        state.note(u, "hunting: standby")

    def cycle(self):
        """Single hunting cycle for this autonomous hero."""
        if abyss.PAUSE.is_set():
            time.sleep(20)
            return
        u = self.unit
        if not rules.AUTO_HUNT and not queue.hunting(u):
            time.sleep(30)
            return
        if lease.leased(u, self.name):
            time.sleep(15)
            return

        m, view = api.me(u)
        if not m:
            time.sleep(5)
            return
        self.view = view
        danger.observe(m, view)

        if m["hp"] <= 0:
            if time.time() - self.fallen_time > 600:
                self.fallen_time = time.time()
                self.say("WARNING", "fell at (%s,%s) (hp 0/%s)" % (m["x"], m["y"], m.get("max_hp", 0)))
            state.update(u, m, "")
            state.note(u, "fallen: awaiting revive")
            time.sleep(30)
            return

        self.last_read = (time.time(), m["hp"], m["x"], m["y"])
        state.update(u, m, self.name)
        if "tribe" not in (state._notes.get(u or 0, ("",))[0] or ""):
            state.note(u, "hunting: standby")

        if m["hp"] > 0 and m["ring"] != "camp":
            self.food_heal(m)
            if m["ap"] >= HOME_AP:
                def _log_ambush(t):
                    if "beatable" in t or "fighting back" in t:
                        self.say("INFO", t)
                    else:
                        self.say("WARNING", t)
                if ambush.handle(u, m, view, say=_log_ambush) == "home":
                    m["ring"] = "camp"

        care.claim_chest(u, m)

        if self.queue_recover_if_hurt(m):
            time.sleep(10)
            return

        if care.repair_due(m, u):
            if not (m.get("bag") or {}).get("dust", 0):
                queue.add(u, "repair-tribe", "resume-hunt", prio=9)
                time.sleep(10)
                return
            if m.get("ring") != "camp":
                queue.add(u, "repair-camp", "resume-hunt", prio=9)
                time.sleep(10)
                return

        if m["ring"] == "camp":
            if self.bread:
                if self.heal_items is care.HEAL_ITEMS:
                    care.restock_bandage(u, m)
                care.restock_bread(u, m)
            # 1. 自动鉴定（高于10级珍藏装备）
            care.auto_identify(u, m)
            # 2. 自动存取（个人仓库取出自己升级装备，将低于自己但高于队友的存入个人仓库，优先级先于自动分解）
            care.manage_camp_stash(u, m)
            care.auto_equip(u, m)
            care.spend_talents(u, m)
            care.auto_repair(u, m)
            # 3. 自动分解
            care.auto_salvage(u, m)

            # Ready heroes in camp travel to hunting ring waypoint rather than walking across meadow
            if (m.get("worn") or {}).get("weapon") and m.get("ap", 0) >= 20 and m.get("hp", 0) >= 0.7 * m.get("max_hp", 1):
                land_on = self.is_land_active()
                if land_on and landwatch.AREA[:2]:
                    dest = tuple(landwatch.AREA[:2])
                    plan = nav.plan_route(m, dest)
                    for step in plan.get("steps", []):
                        if step.get("action") == "travel":
                            target_ring = step.get("to")
                            code, _r = api.act(u, {"action": "travel", "to": target_ring})
                            if code == 200:
                                self.say("INFO", "%s uses %s waypoint for corrupted land at %s (plan '%s', ~%.1f AP): 200" % (
                                    self.name, target_ring, dest, plan["name"], plan["ap"]))
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
                                    self.name, target_ring, plan["name"], plan["ap"]))
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
                                    self.name, to, plan["name"], code))

        if self.bounties:
            self.claim_bounties(m)

        is_resting = queue.resting(u)
        is_staging = landwatch.STATE["stage"] and (u in rules.CORRUPT_LAND["units"] or (u is None and 0 in rules.CORRUPT_LAND["units"]))
        if is_resting or is_staging:
            if not queue.busy(u) and not nav.at_tribe(m):
                if m["ring"] != "camp" and m["ap"] >= 8:
                    api.act(u, {"action": "home", "why": "a corrupted land is due: rests in camp" if is_staging else "a gather order waits: rests"})
                    m["ring"] = "camp"
            time.sleep(30)
            return

        if not (m.get("worn") or {}).get("weapon"):
            if m["ring"] != "camp" and m["ap"] >= HOME_AP:
                api.act(u, {"action": "home", "why": "unarmed: returning to camp to get a weapon"})
                m["ring"] = "camp"
            time.sleep(30)
            return

        if m["ring"] != "camp" and m["hp"] < m["max_hp"] * rules.HOME_BELOW:
            if m["ap"] >= HOME_AP:
                api.act(u, {"action": "home", "why": "hurt in field: health %d/%d is under %d%%" % (m["hp"], m["max_hp"], 100 * rules.HOME_BELOW)})
                m["ring"] = "camp"
            time.sleep(15)
            return

        # If in the field outside hunting rings/corrupted land, walk toward center/land
        dest = tuple(landwatch.AREA[:2]) if (self.is_land_active() and landwatch.AREA[:2]) else (self.center or (rules.WAYPOINTS.get(self.rings[0]) if self.rings else None))
        in_area = bool(self.is_land_active() and landwatch.AREA[:2] and max(abs(m["x"] - landwatch.AREA[0]), abs(m["y"] - landwatch.AREA[1])) <= landwatch.AREA[2])
        if dest and not in_area and (m["ring"] not in (self.rings or ()) or self.is_land_active()):
            d_dest = max(abs(m["x"] - dest[0]), abs(m["y"] - dest[1]))
            if d_dest > (landwatch.AREA[2] if self.is_land_active() else 8):
                if m["ring"] == "camp":
                    plan = nav.plan_route(m, dest)
                    step0 = (plan.get("steps") or [{}])[0]
                    if step0.get("action") == "travel":
                        target_ring = step0.get("to")
                        code, _r = api.act(u, {"action": "travel", "to": target_ring})
                        if code == 200:
                            self.say("INFO", "%s uses %s waypoint toward %s: 200" % (self.name, target_ring, dest))
                            m["ring"] = target_ring
                            return
                step = min(d_dest, int((m["ap"] - 10) / 1.3))
                if step >= 3:
                    k = step / d_dest
                    to = [int(round(m["x"] + (dest[0] - m["x"]) * k)), int(round(m["y"] + (dest[1] - m["y"]) * k))]
                    code, r = api.act(u, {"action": "move", "to": to, "until_there": True})
                    self.say("DEBUG", "%s walks toward %s %s (dist %d): %s" % (
                        self.name, "corrupted land" if self.is_land_active() else "hunt ground", to, d_dest, code))
                    return

        if m["ap"] < 6:
            nxt = api.iso_wait(m.get("next_ap_at"))
            time.sleep(min(max(nxt, 5) + 1, 60))
            return

        # Pick candidate targets
        targets = self.pick_target(m)
        if targets is None:
            self.say("WARNING", "hunt read failed; waiting without treating it as an empty area")
            time.sleep(15)
            return

        chosen = None
        for tt in targets[:8]:
            if time.time() - self.skipped.get(tt["id"], 0) < 150:
                continue
            if self.feasible(tt, m) is None:
                self.skipped[tt["id"]] = time.time()
                continue
            if claim(tt["id"], self.name):
                if self.feasible(tt, m) is not None:
                    chosen = tt
                    break

        if not chosen:
            self.idle += 1
            effective_center = tuple(landwatch.AREA[:2]) if (self.is_land_active() and landwatch.AREA[:2]) else self.center
            if effective_center:
                d_c = max(abs(m["x"] - effective_center[0]), abs(m["y"] - effective_center[1]))
                ap_cap = state.get_ap_max(u, m)
                min_drift_ap = 12 if self.is_land_active() else ap_cap * getattr(rules, "DRIFT_MIN_AP_SHARE", 0.5)
                reach_tol = landwatch.AREA[2] if self.is_land_active() else 8
                if d_c > reach_tol and m.get("ap", 0) > min_drift_ap:
                    step = min(d_c, int((m["ap"] - 10) / 1.3))
                    if step >= 3:
                        k = step / d_c
                        to = [int(round(m["x"] + (effective_center[0] - m["x"]) * k)), int(round(m["y"] + (effective_center[1] - m["y"]) * k))]
                        code, r = api.act(u, {"action": "move", "to": to, "until_there": True})
                        self.say("DEBUG", "%s drifts toward %s %s (AP %s/%s): %s" % (
                            self.name, "corrupted land" if self.is_land_active() else "center", to, m.get("ap", 0), ap_cap, code))
            wait = 30
            ap_cap = state.get_ap_max(u, m)
            self.say("DEBUG", "no target now (AP %s/%s): next look in %ds" % (m["ap"], ap_cap, wait))
            time.sleep(wait)
            return

        self.execute_combat(chosen, m)

    def run(self):
        """Autonomous solo hunt loop."""
        while True:
            try:
                self.cycle()
                if self.fought:
                    m, _v = api.me(self.unit)
                    if m:
                        care.spend_talents(self.unit, m)
                        if m.get("hp", 0) < m.get("max_hp", 1) * rules.HEAL_BELOW:
                            self.food_heal(m)
                    self.fought = False
            except Exception as e:
                import traceback
                self.say("ERROR", "exception %r\n%s" % (e, traceback.format_exc()))
                time.sleep(20)
            time.sleep(2)


# Backwards compatibility alias
Squad = Hunter
