"""Fetch the world definition (GET /world/map) into worldmap.json.

The main world grew to 512 tiles (2026-11-05): `worldmap.json` still held the old 256 map, so `harvest.py` looked for nodes
at half coordinates. Run this whenever the size changes, then restart `wild.py`:

    python tools/worldmap.py

It prints the size, the ring edges and the waypoints it saved.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wild import api  # noqa: E402  (reads .env itself; the only HTTP client)


def main():
    code, r = api.call("GET", "/world/map", retries=2)
    if code != 200 or not r.get("size"):
        print("failed: %s %s" % (code, r))
        return 1
    path = os.path.join(api.HERE, "worldmap.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    size = r["size"]
    print("saved %s: size %s, seed %s, %s tiles of ground" % (path, size, r.get("seed"), len(r.get("ground") or "")))
    print("rings %s" % (r.get("rings"),))
    print("waypoints %s" % (r.get("waypoints"),))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
