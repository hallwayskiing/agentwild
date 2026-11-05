# Route into HALL (512-tile map)

The tribe migrated on 2026-11-05. The totem is at (341,265), its land radius is 6, and the training ground is at (342,266). The migration finished with the main character standing at (340,265).

The forest waypoint is at (356,256). From the camp, use the unlocked forest waypoint, then walk toward the tribe using the danger-aware route in `wild/route.py` and `wild/nav.py`. The main character walked this approach during migration; the server quoted 16 AP from the waypoint to (340,265), in addition to the 8 AP waypoint travel. Costs and danger vary by character and time.

The route watcher uses the existing strong-creature clearance and the danger memory. Unsafe routes wait; `force` remains an explicit owner order. The old routes and coordinates on the 256-tile map no longer apply.
