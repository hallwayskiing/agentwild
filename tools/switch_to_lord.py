import time
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wild import api

print("=== Meeting and Switching Hero to Lord ===")
# 1. Get exact positions of 0 and 928
m0, _ = api.me(None)
m928, _ = api.me(928)
pos0 = m0.get("at") or [257, 262]
pos928 = m928.get("at") or [249, 258]
print(f"Unit 0 at {pos0}, Unit 928 at {pos928}")

dist = ((pos0[0] - pos928[0])**2 + (pos0[1] - pos928[1])**2)**0.5
print(f"Current distance: {dist:.1f} tiles")

if dist > 2:
    print(f"Moving Unit 928 toward Unit 0 at {pos0}...")
    c_mv, r_mv = api.act(928, {"action": "move", "to": pos0, "until_there": True})
    print(f"Move result: {c_mv}")
    time.sleep(3)
    m928, _ = api.me(928)
    print(f"Unit 928 new pos: {m928.get('at')}")

# 2. Rejoin
print("Calling rejoin for 928...")
c_rej, r_rej = api.act(928, {"action": "rejoin"})
print("Rejoin response:", c_rej, json.dumps(r_rej, ensure_ascii=False)[:300])

if c_rej != 200:
    print("Rejoin failed, aborting switch.")
    sys.exit(1)

# 3. Switch played hero to 928
print("Switching active played hero to 928...")
c_hero, r_hero = api.act(None, {"action": "hero", "id": 928})
print("Hero switch response:", c_hero, json.dumps(r_hero, ensure_ascii=False)[:300])

# 4. Detach former main hero 926
print("Detaching former main hero 926...")
c_det, r_det = api.act(None, {"action": "detach", "hero": 926})
print("Detach 926 response:", c_det, json.dumps(r_det, ensure_ascii=False)[:300])

# 5. Final verification
print("=== Final Verification ===")
m_new, _ = api.me(None)
print("New main hero:", m_new.get("name"), "Hero info:", m_new.get("hero"))
