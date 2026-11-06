"""Agent Wilds: the one script. Every job is a module of the `wild` package; the owner's standing orders are all in
wild/rules.py.

    python wild.py run                          the whole plan in one process (hunting squads, gear, Abyss, route, orders)
    python wild.py status                       a table of every member
    python wild.py rules                        the standing orders in force
    python wild.py order UNIT [force] ACTION [ARG]   queue an order (UNIT 0 = main): train [n] | deposit | walk-tribe | home | eat [item];
                                                     `force` = go at once whatever the road looks like (closed way, strong creatures)
    python wild.py gear [--dry]                 one round of gear dealing (--dry: only show it)
    python wild.py fx [status|book|buy QTY PRICE|sell QTY PRICE|cancel ID|withdraw [AMOUNT]|voucher]
    python wild.py abyss | danger | model | skillcheck | identify
    python wild.py buy UNIT GOODS QTY | repair UNIT ITEM_ID | give FROM TO GOODS|item:ID QTY | build KIND
"""
import argparse
import json
import os
import re
import threading
import time
import traceback

from wild import abyss, api, care, ladder, combat, danger, gear, hunt, market, queue, route, rules, tribe, watch
from wild import landwatch, nav
from wild.regions import REGIONS
from wild.nav import TOTEM, TRIBE_LAND

RING_ZH = {"camp": "营地", "meadow": "草甸", "forest": "森林", "highlands": "高地", "ashlands": "灰烬地", "ruins": "废墟"}


def where(full, r):
    """The place as a word (the owner's order: no coordinates): tribe land, or the ring the hero stands in."""
    x, y = (full.get("x"), full.get("y")) if full.get("x") is not None else tuple(r["at"])
    if max(abs(x - TOTEM[0]), abs(y - TOTEM[1])) <= TRIBE_LAND:
        return "部落"
    return RING_ZH.get(full.get("ring") or r.get("ring"), "?")


def status():
    """A table of every member (one read each), then the way to the tribe, the tribe, camp orders and corrupted land."""
    m = api.squad()
    if not m:
        print("read failed")
        return
    rows = [dict(m, at=[m["x"], m["y"]], unit=None)] + [dict(u, unit=u["hero"]) for u in m["units"]]
    print("| 成员 | 分工 | 等级 | 经验 | 血量 | 行动点 | 饱食 | 位置 | 队列 | 备注 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    queues = queue.read()
    try:
        last_xp = json.load(open("xp_last.json", encoding="utf-8"))
    except (OSError, ValueError):
        last_xp = {}
    for r in rows:
        unit = r["unit"]
        full = api.me(unit)[0] or {}
        r = dict(r, level=full.get("level", r["level"]))  # the squad list can lag a level behind
        xp = "%s/%s" % (full.get("xp", "?"), full.get("xp_next", "?"))
        key = str(unit or 0)  # change since the last table (stored in xp_last.json)
        if isinstance(full.get("xp"), int):
            before = last_xp.get(key)
            if before and before[0] == r["level"]:
                xp += " (%+d)" % (full["xp"] - before[1])
            elif before:
                xp += " (升级)" if r["level"] > before[0] else " (降级)"
            last_xp[key] = [r["level"], full["xp"]]
        w = (full.get("worn") or {}).get("weapon") or {}
        bag = full.get("bag") or {}
        note = []
        if full.get("down_until") or r["hp"] <= 0:
            note.append("倒下")
        if w.get("durability") is not None and w["durability"] < 30:
            note.append("武器耐久%s" % w["durability"])
        if r["food"] < 40:
            note.append("饿")
        mats = ["%s%s" % (k, bag[k]) for k in ("wood", "stone", "hide", "wheat_seed", "wheat") if bag.get(k)]
        if mats:
            note.append(" ".join(mats))
        q = queues.get(key) or {}
        qtext = " → ".join(([">" + q["running"]] if q.get("running") else []) + (q.get("waiting") or [])) or "打怪"
        print("| %s | %s | %s | %s | %s/%s | %s | %s | %s | %s | %s |" % (
            unit or "主角", rules.squad_name(unit), r["level"], xp, r["hp"], r["max_hp"], r["ap"], r["food"],
            where(full, r), qtext, "、".join(note)))
    with open("xp_last.json", "w", encoding="utf-8") as f:
        json.dump(last_xp, f)
    t = tribe.info()
    if t:
        print("部落 %s：%s/%s 人，建筑 %s" % (t["tag"], t["members"], t["member_cap"],
                                            "、".join("%s(%s)" % (b["kind"], b["status"]) for b in t["building_list"])))
    print("营地订单：", [(o["id"], o["goods"], "剩%s" % o["left"], "我们可交%s" % o["yours_left"]) for o in m.get("camp_orders") or []])
    print("腐化之地：", m.get("corrupted_land"))


def guarded(name, fn, *args, **kw):
    """Run a job in its own thread's loop; log the crash instead of killing the process."""
    try:
        fn(*args, **kw)
        print("[job %s] finished" % name, flush=True)
    except Exception:
        print("[job %s] crashed:\n%s" % (name, traceback.format_exc()), flush=True)


def squads():
    """The hunting parties of rules.SQUADS, each on its own side of the camp."""
    return [hunt.Squad(name, s["members"], hero_goal=s.get("hero_goal", rules.HERO_GOAL),
                       region=REGIONS[s["region"]], claims_bounties=s.get("bounties", False), elites=s.get("elites", False),
                       rings=s.get("rings"), close=s.get("close"),
                       bread=s.get("bread", False), bandage=s.get("bandage", False), center=s.get("center"), land=s.get("land", False))
            for name, s in rules.SQUADS.items()]


def gear_groups(parties):
    """Gear is dealt inside each squad only."""
    return [list(p.members) for p in parties if not getattr(p, "land", False)]


def plan():
    """What runs: the hunting squads, the queues (restored from the last run first), the hand orders, the gear rounds,
    the Abyss watch, the way into the tribe and the skill.md watch."""
    api.info("[system] wild.py daemon started / restarted (PID %d)" % os.getpid())
    landwatch._update_stage()
    queue.restore()
    parties = squads()
    jobs = {p.name: p.run for p in parties}
    jobs.update({
        "abyss": abyss.watch,                               # everybody >= L7 (or abyss.go): bank points, go in
        "ladder": ladder.watch,                             # 19:30 (UTC+8) every evening: all heroes join the main character, the squad ladder
        "landwatch": landwatch.watch,                       # every 10 minutes: the corrupted land open in the meadow at levels 1-6 -> the main character and the lord go there
        "gear": lambda: gear.loop(gear_groups(parties)),    # every 5 minutes: talent points, food, gear dealing, market listings
        "orders": queue.read_orders,                        # orders.txt -> the heroes' queues
        "dispatch": queue.dispatch,                         # orders that could not run yet start when they can
        "skill": watch.loop,                                # "[SKILL UPDATE]" when skill.md changes
    })
    if rules.AUTO_COLLECT_MAIN:
        jobs["coins"] = gear.coin_loop
    ts = [threading.Thread(target=guarded, args=(n, f), daemon=True) for n, f in jobs.items()]
    for t in ts:
        t.start()
        time.sleep(2)
    for t in ts:
        t.join()


_LEVELLED = re.compile(r"(DEBUG|INFO|NOTICE|WARNING|ERROR)\s")


class _Stamped:
    """stdout that starts every line with HH:MM:SS (api.log writes the level; a bare print counts as INFO)."""

    def __init__(self, out):
        self.out, self.fresh, self.lock = out, True, threading.Lock()

    def write(self, s):
        with self.lock:
            for part in s.splitlines(True):
                if self.fresh and part.strip():
                    self.out.write(time.strftime("%H:%M:%S ") + ("" if _LEVELLED.match(part) else "INFO    "))
                self.fresh = part.endswith("\n")
                self.out.write(part)
        return len(s)

    def flush(self):
        self.out.flush()

    def __getattr__(self, name):
        return getattr(self.out, name)


def stamp_output():
    import sys
    sys.stdout = _Stamped(sys.stdout)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("status", "run", "rules", "identify", "skillcheck", "model", "danger", "abyss"):
        sub.add_parser(name)
    s = sub.add_parser("gear", help="show (--dry) or run one round of the gear dealing")
    s.add_argument("--dry", action="store_true")
    s.add_argument("--all", action="store_true", help="one pool for everybody standing together (tribe), not per squad")
    s = sub.add_parser("simulate", help="try a fight: KIND LEVEL [--elite] [--hurt] against every hero (the private fighting model)")
    s.add_argument("kind")
    s.add_argument("level", type=int)
    s.add_argument("--elite", action="store_true")
    s.add_argument("--hurt", action="store_true", help="with the health the heroes have now (default: full health)")
    sub.add_parser("bestiary", help="the creatures of the fighting model: ring, levels, trait, health, how much data")
    s = sub.add_parser("order", help="UNIT (0 = main) [force] hunt | recover | train [n] | deposit | walk-tribe | home | eat [item]")
    s.add_argument("parts", nargs="+")
    s = sub.add_parser("build")
    s.add_argument("kind")
    s = sub.add_parser("buy")
    s.add_argument("unit", type=int)
    s.add_argument("goods")
    s.add_argument("qty", type=int)
    s = sub.add_parser("repair")
    s.add_argument("unit", type=int)
    s.add_argument("item", type=int)
    s = sub.add_parser("give")
    s.add_argument("src", type=int)
    s.add_argument("dst")
    s.add_argument("goods")
    s.add_argument("qty", type=int, nargs="?", default=1)
    s = sub.add_parser("unstash")
    s.add_argument("item", type=int)
    s.add_argument("--unit", type=int, default=0)
    s = sub.add_parser("fx", help="the coin exchange (https://agentwild.fun/exchange): trade coins for BOLT")
    s.add_argument("action", nargs="?", default="status", choices=["status", "book", "buy", "sell", "cancel", "withdraw", "voucher"])
    s.add_argument("args", nargs="*", help="arguments for fx action")
    a = p.parse_args()

    if a.cmd == "status":
        status()
    elif a.cmd == "run":
        stamp_output()
        plan()
    elif a.cmd == "rules":
        print("\n".join(rules.describe()))
    elif a.cmd == "gear":
        groups = gear_groups(squads())
        if a.all:   # everybody standing on the tribe's land is one pool (gear is dealt to the highest level first)
            seen = {u: api.me(u)[0] for g in groups for u in g}
            groups = [[u for u, m in seen.items() if m and nav.at_tribe(m)]]
        gear.balance(groups, dry=a.dry, everything=a.all)
    elif a.cmd == "bestiary":
        from wild import bestiary
        for r in bestiary.table():
            print("  ".join("%s=%s" % (k, v) for k, v in r.items()))
    elif a.cmd == "simulate":
        from wild import bestiary
        names = {None: "主角"}
        print("%s L%d%s: health %s" % (a.kind, a.level, " elite" if a.elite else "", round(bestiary.hp_at(a.kind, a.level, a.elite))))
        for u, m, r in bestiary.simulate_all(a.kind, a.level, a.elite, full_health=not a.hurt):
            print("%-5s L%-2d atk %-3s def %-3s hp %-3s | win %3.0f%%  die %3.0f%%  hp left %3.0f%%  %4.1f exchanges" % (
                names.get(u, u), m["level"], m.get("attack"), m.get("defense"), m["hp"], r["win"] * 100, r["die"] * 100,
                r["hp_left"] * 100, r["exchanges"]))
    elif a.cmd == "order":
        with open(queue.ORDERS, "a", encoding="utf-8") as f:
            f.write(" ".join(a.parts) + "\n")
        print("queued (the running process reads it within seconds)")
    elif a.cmd == "fx":
        from wild import fx
        fx.cli(a.action, a.args)
    elif a.cmd == "build":
        print(tribe.build(a.kind))
    elif a.cmd == "skillcheck":
        print("changed" if watch.check() else "skill.md unchanged")
    elif a.cmd == "abyss":
        print("goals met:", abyss.goals_met(), "runs left:", abyss.runs_left())
    elif a.cmd == "danger":
        for lvl, kind, x, y, age in danger.summary():
            print("L%-3s %-8s (%s,%s) seen %d min ago" % (lvl, kind, x, y, age))
    elif a.cmd == "model":
        combat.fit()
    elif a.cmd == "buy":
        print(market.buy(a.unit, a.goods, a.qty))
    elif a.cmd == "identify":
        print(care.identify())
    elif a.cmd == "repair":
        print(care.repair(a.unit, a.item))
    elif a.cmd == "unstash":
        unit = None if a.unit == 0 else a.unit
        code, r = api.act(unit, {"action": "unstash", "item": a.item})
        print(code, json.dumps(api.clean(r), ensure_ascii=False)[:200])
    elif a.cmd == "give":  # GOODS may also be item:ID to hand over a piece of gear
        src = None if a.src == 0 else a.src
        to = None if a.dst in ("0", "main") else (int(a.dst) if a.dst.isdigit() else a.dst)
        gear.meet(src, to)
        body = {"action": "give", "to": "main" if to is None else to, "item": int(a.goods[5:])} if a.goods.startswith("item:") \
            else {"action": "give", "to": "main" if to is None else to, "goods": a.goods, "qty": a.qty}
        code, r = api.act(src, body)
        print(code, json.dumps(api.clean(r), ensure_ascii=False)[:200])
        if code == 200 and a.goods.startswith("item:"):
            m_to, _ = api.me(to)
            if m_to:
                care.auto_equip(to, m_to)


if __name__ == "__main__":
    main()
