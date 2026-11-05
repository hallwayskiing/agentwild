"""One-off (the owner, 2026-11-04): every hero at the camp hands everything depositable to the main character, who then walks to
the tribe and builds. Run when the heroes stand by in the camp (`stay`):  python tools/handover.py [dry]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repo root, for the `wild` package
from wild import api, rules

HEROES = (925, 927, 928, 929, 930, 931, 932, 933, 934)
dry = len(sys.argv) > 1 and sys.argv[1] == "dry"


def me(u):
    for _ in range(4):
        m, _v = api.me(u)
        if m:
            return m
    return None


main = me(None)
print("main at", main["x"], main["y"], main["ring"], "hp", main["hp"], "ap", main["ap"])
total = {}
for u in HEROES:
    m = me(u)
    if not m:
        print(u, "no read")
        continue
    items = {k: (m.get("bag") or {}).get(k, 0) for k in rules.STORE_KINDS if (m.get("bag") or {}).get(k, 0) > 0}
    print(u, m["ring"], (m["x"], m["y"]), "hp", m["hp"], "carries", items)
    if m["ring"] != "camp" or m["hp"] <= 0 or not items:
        continue
    if dry:
        continue
    main = me(None) or main
    if max(abs(m["x"] - main["x"]), abs(m["y"] - main["y"])) > 2:
        c, r = api.act(u, {"action": "move", "to": [main["x"] + 1, main["y"]], "until_there": True})
        if c != 200:
            print("   move failed", c, str(r.get("error"))[:80])
        m = me(u) or m
        api.wait_busy(m)
    for k, n in items.items():
        c, r = api.act(u, {"action": "give", "to": "main", "goods": k, "qty": n})
        print("   gives %s %s to main: %s %s" % (n, k, c, "" if c == 200 else str(r.get("error"))[:100]))
        if c == 200:
            total[k] = total.get(k, 0) + n
print("handed over:", total)
main = me(None)
print("main now carries:", {k: (main.get("bag") or {}).get(k, 0) for k in rules.STORE_KINDS})
