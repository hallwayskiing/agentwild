# AGENTS.md — Agent Wilds squad manager

This folder runs the owner's Boltbook **Agent Wilds** account: one main character, one demon lord and eight hero NFTs, played
through the public HTTP API (`https://boltbook.fun/api/v1`). Everything is Python (standard library only). One long-running
process (`wild.py run`) plays; one separate service (`dashboard.py`) shows what is going on.
The game's own manual is `skill_latest.md` (a copy of https://boltbook.fun/skill.md, kept up to date by `wild/watch.py`);
this file is the map of **our** code and of the parts of the game that the code depends on.

## Hard rules (never break these)

- Never read, print or log `.env` / the API key (`wild/api.py` is the only reader).
- The demon lord's worn weapon is locked (`rules.LOCKED_SLOTS`). Teleport
  (`rules.TELEPORT`) only on the owner's word.
- The owner's standing orders live in **`wild/rules.py`** and nowhere else; a rule changes there (and in `describe()`), then restart.
- One `wild.py run` process only. Orders go through `orders.txt`, never by starting a second process.

## The game, as far as the code needs it

**World.** A 512×512 map; the camp is at (256,256). Ring radii: camp ≤30, meadow ≤72, forest ≤128,
highlands ≤180, ashlands ≤224, ruins ≤252. `worldmap.json` is the 512×512 map (`tools/worldmap.py` fetches it from `GET /world/map`; `harvest.py` reads its size, centre and ring edges from it). Creatures live in rings (see `bestiary.py`); level grows with the ring. Strong
creatures (4+ levels above the walker) attack within ~3 tiles.

**Units.** The *main character* (unit `None`, id 926, level ~12, gold hunter) is the account; heroes are acted for with
`"unit": id`. Our roster: the demon lord 928 (staked, level ~15), heroes 925 927 929 930 931 932 933 934. Every act is
`POST /world/act` with `{"action": ..., ["unit": id]}`; reads are `GET /world/me[?unit=id]` (hp, ap, bag, worn, units list,
corrupted land, talents ...), `/world/hunt`, `/world/hero/ap`, `/world/tribes/HALL`, `/world/market`, `/world/quartermaster`.
Rate limit 60 reads + 30 writes a minute (`api.py` throttles for the whole process).

**Action points (AP).** Each unit has its own; +3 a minute up to its maximum (300-410). Walking ~1.3 AP a tile, attack 5,
gather 15, build/work 30, train 60, `home` 8, `use` a currency 10, eat 5. Every act also takes game time (`busy_until`); an
act sent while busy is queued (HTTP 202) — only the last queued act is kept.

**Combat.** `attack` on a creature next to you. Hit points of a creature are shared: only the unit that lands the **killing
blow** gets the XP and the drops, so a squad has helpers soften it and one *finisher* kill it (`hunt.py`). Elites have 2.5×
health and an affix (swift, chilling, shielded, enraged). `combat.py` models blows from the recorded fights (`combat.jsonl`)
to decide whether a fight is won before it starts and when to stop (`safe_round`).
**Death**: lose 1/5 of bag goods, 1/5 durability of worn pieces, 1/10 XP; currencies and gems are kept; the hero wakes at the totem.
Being ambushed: fight back if the model says it is winnable (≥30% hp left), else `home` (`ambush.py`).

**Health.** Rest at the camp/tribe heals; bread 20, roast, stew, **bandage 35** (`eat` with `item`), stew 60. Foods and
bandages come from the quartermaster (deeds): bread 100 deeds/5, bandage 150 deeds/3.

**Gear.** Worn slots (weapon, head, chest, legs, hands, feet, amulet, ring, tool). Quality: plain < runed < treasured < mythic
(set). Each slot can change at most once per 5 minutes (HTTP 409 "changed lately" is harmless). Repair: in the field only a
broken piece is repaired and only in coins (`care.auto_repair`); at the camp or on the tribe's land every worn piece under 20%
durability (`rules.DUST_BELOW`) is repaired with dust, and a tribe visit tops the hero's dust up to `rules.DUST_KEEP` (30,
about three repair rounds — a piece costs 3-5 dust) out of the tribe store, which the main character's surplus dust fills
(`tribe.restock_dust` / `share_dust`). A broken piece counts for nothing. Currencies (`whetstone`, `oraclestone`, runes, seals ...) are applied with
`{"action":"use","currency":..,"item":ID}`; gems with `socket`. We never use them by ourselves (`USE_CURRENCIES`).

**Tribe HALL.** Totem (341,265), chief = the main character. The store and the training ground work only on the tribe land
(radius 6; training within 2 tiles of (342,266)). The forest waypoint is (356,256); see `ROUTE.md` for the approach.
Buildings (storehouse, mill, quarry, infirmary, workbench ... all built) are raised with wood/stone/ore;
mill and quarry pay +130 wood / +130 stone into the store roughly once an hour. Level 3 needs wood 1000, stone 800, ore 200
(+bone/herb/hide, already enough). Store: 40 slots at level 2, 1000 wood or stone a slot. Training: 4 points → XP, daily limit.
Every tribe visit (`tribe.deposit_all`): deposit materials first, top bread and meat up to 5 each, then deposit any food over 5; bandages are never deposited. Coins: nobody carries over 30 — the excess goes to the tribe treasury, and nothing is topped up from it (only the chief may take coins out) (`rules.COIN_KEEP`).

**Daily and timed things.** Daily chest (bank mode `pay` = the "80% tier") after 00:00 UTC+8 (`care.claim_chest`); squad
ladder at 19:30 UTC+8 (`ladder.py`); the **Abyss** (off unless `abyss.go` exists); **corrupted land**: every two hours a
patch of one ring is corrupted for 20 minutes, creatures give +50% XP, then its guardian rises (`landwatch.py`).
The machine's clock is not UTC+8: convert (`care._cn_now`).

**Talents.** Spend points after every fight in the order `care.TALENT_ORDER` (sharp, tough, hawk, slayer, ...); a talent opens
when its path has enough points.

## How the process is built

`wild.py run` starts one thread per job (`wild.plan()`): every hunting squad of `rules.SQUADS`, plus `land` (corrupted-land
watch), `coins`, `ladder`, `route`, `gear`, `orders`, `dispatch`, `abyss`, `skill`. Threads share the API throttle and the
**lease** table: a hero belongs to exactly one driver at a time (a squad, a queue worker, the ladder, the Abyss), so two jobs
never move the same hero.

- **Squads (`hunt.Squad`)** run only for members with an explicit `hunt` order: read members → heal (items) / repair / identify → pick a finisher (lowest level
  first) → pick the best creature in the squad's region and rings (level ≤ finisher+3, XP per HP and per step) → helpers walk
  and soften it, the finisher kills it → talents → repeat. No target: wait 30 s and **drift** to the squad's centre.
  Food healing comes first: a hurt member eats/heals with items, and only if still below 50% queues `recover`. Then, when depositable goods (wood, stone, hide, ore …) exceed 20 or training quota remains (either one is enough) it heads to the tribe directly via the forest waypoint — no camp rest first — and trains while healing there, leaving for the hunt again the moment health passes 90% (`rules.HEALED`); with neither it goes home and rests in the camp — and the recovery order ends the moment health passes 90%, even while the way to the tribe stays shut (a running `recover` order keeps the squad from acting for that hero: waiting at full health froze every squad on 2026-11-05). The interrupted `hunt` order resumes afterwards. Bandages are never auto-restocked (`rules.AUTO_RESTOCK_BANDAGE` off).
- **Regions (`regions.py`)** keep squads apart: `west`/`east` (forest), `sec0..sec3` quarters of the circle round the camp
  (meadow pairs), `land` (the corrupted land).
- **Corrupted land (`landwatch.py`)**: `me.corrupted_land` gives the phase and `ends_at`, so the next start (every 2 h) is
  computed and the watcher looks the moment it opens (fallback: every 10 min until one has been seen). If it is open (or
  its guardian is up), in the meadow, levels 1-6, and the
main character and the lord are both free (no lease), set `STATE["on"]`: the `astral` squad takes them (the other squads
  skip them), and they go back when it ends or an order takes one of them. Before it opens (`stage`), they wait like a
  recovery: the watcher queues `land-stage`, which reuses the tribe trip — worthwhile (goods over 20 or hurt) heroes go
  to the tribe to store, resupply and heal, the rest wait in the camp; the wait ends when the land is due.
- **Orders (`queue.py`)**: the owner's orders arrive in `orders.txt` as `UNIT [force] ACTION [ARG]` (UNIT `0` = main).
  One worker per hero runs its tasks by priority and holds the lease; an order that cannot run yet waits in a pending list
  while the hero hunts, except recovery, which waits safely. Actions: `hunt` (persistent explicit hunting), `train [n]`, `recover`, `deposit`, `walk-tribe`, `gather`, `home`, `stay` (park in camp),
  `free` (end the order at once: whatever held the hero lets go), `construct`, `harvest res [quota]`, `muster`, `scout`,
  `eat [item]`, `cancel`. `force` ignores closed roads/strong creatures and runs first. `queue.json` persists the queue
  (restored at start).
- **Walking** is `nav.py` (retrying hops, strong-creature detours, `travel` to a waypoint) over `route.py` (the two ways into
  the tribe, `route.json`) and `danger.py` (a memory of strong creatures, `danger.json`).

## Files

| File | Role |
|---|---|
| `wild.py` | The one entry point: `run`, `status`, `rules`, `order`, `gear`, `abyss`, `danger`, `model`, `bestiary`, `simulate`, `buy`, `repair`, `give`, `build`. Builds the job list. |
| `dashboard.py` + `dashboard.html` | Independent web service on http://127.0.0.1:8765. Does **its own** game reads (one `/world/me` per refresh + one hero's XP read, cached 12 s), computes the 10-minute XP change, shows `wild.log` and the notes/targets that `state.json` holds. Needs nothing else running. |
| `AGENTS.md`, `ROUTE.md` | This map; the recorded way into the tribe land. |
| `skill_latest.md`, `skill_changes.txt` | The game manual (baseline) and the diffs `watch.py` found. |
| `tools/handover.py` | One-off: every hero at the camp hands its depositable goods to the main character. |
| `tools/worldmap.py` | Fetches the world definition (`GET /world/map`) into `worldmap.json`; run it when the size of the world changes. |
| `wild/rules.py` | **All policy**: squads (members, regions, centres), level range, health/food thresholds, tribe store, gear, Abyss, corrupted land; `describe()` prints it in Chinese. |
| `wild/api.py` | The only HTTP client: throttle, `call`, `act` (logs `home` reasons, marks own fights), `me`, `ap_all`, `who` (log name). Reads the key from `.env`. |
| `wild/hunt.py` | `Squad`: the hunting loop described above (target choice, fights, ambush handling, drift, bounties, gear/food care hooks). |
| `wild/regions.py` | `REGIONS` used by squads (`west`, `east`, `sec0-3`, `land`). |
| `wild/landwatch.py` | The corrupted-land watcher and its `STATE`/`AREA`. |
| `wild/ambush.py` | Fight back when winnable, else go home. |
| `wild/combat.py`, `wild/bestiary.py` | The fight recorder/model (`combat.jsonl`) and the creature table + Monte Carlo (`wild.py model/simulate`). |
| `wild/danger.py` | Memory of strong creatures; `strong_near`, `clear`, `detour`. |
| `wild/nav.py`, `wild/route.py` | Walking, waypoints, the two ways into the tribe, route safety. |
| `wild/tribe.py` | Tribe land: `visit`/`go`/`gather`, store deposit/withdraw, training, `build`, `construct`, totem work, muster. |
| `wild/queue.py`, `wild/lease.py` | Order queue/workers/dispatcher; the hero lease table. |
| `wild/care.py` | Eat, heal with items, restock bread, talents, equip, identify, repair (coins, dust), daily chest. |
| `wild/gear.py` | Deal gear inside a squad, coin and valuables (runes, stones, gems) pooling to the main character, market listings (off), meat sharing. |
| `wild/harvest.py` | Gathering for the tribe (wood/stone/ore, pickaxes). Only used by `harvest` orders. |
| `wild/market.py` | Buy cheapest listings at the camp. |
| `wild/ladder.py`, `wild/abyss.py` | Evening squad ladder; the Abyss expedition (idle unless `abyss.go`). |
| `wild/state.py` | Snapshot for the dashboard (`state.json`: notes, targets, jobs, leases). |
| `wild/watch.py` | Watches skill.md for changes (`[SKILL UPDATE]` in the log). |

Data files (all in this folder): `queue.json` (persisted orders), `orders.txt` (appended by `wild.py order`), `daily.json`
(chest claimed day), `state.json`, `route.json`, `danger.json`, `combat.jsonl`, `worldmap.json` (map nodes), `xp_last.json`,
`gear_all.log`, `wild.log` / `wild.err` (the process output). `backup/` holds old versions; ignore it.

## Running it (Windows)

Start (hidden, appends to the logs): `Start-Process cmd -ArgumentList '/c python -u wild.py run >> wild.log 2>> wild.err' -WindowStyle Hidden`;
dashboard: `Start-Process cmd -ArgumentList '/c python -u dashboard.py' -WindowStyle Hidden`.
Restart = stop the python processes whose command line matches `wild.py run`, start again (orders in `queue.json` are
restored). `wild.err` is only appended — look at its modification time, not its tail, to tell old from new.
Give an order: `python wild.py order 0 force home`, `python wild.py order 928 free`. Quick checks: `python wild.py status`,
`python wild.py rules`.
Every log line is `HH:MM:SS LEVEL [tag] text` (levels via `api.debug/info/notice/warning/error`: DEBUG routine chatter, INFO what was done, NOTICE a result: kill, built, claimed, land opened, WARNING deaths/trips home/refusals, ERROR failures; the dashboard filters by level).
Log tags: `[meadowA]` ... squad lines, `[home] unit: reason` every trip home, `[land]`, `[queue unit]`, `[gear]`,
`[route]`, `[construct main]`, `[SKILL UPDATE]`; "fell at" / "is down at" mark deaths.

## Lessons that cost something

- Read failures happen (rate limits, slow server): a failed read must never be treated as "nothing needed".
- `api.call` retries a request that got no answer (status 0) 3 times, for every endpoint (`api.RETRIES`); acts that move goods or coins (`api._SPENDS`: buy, sell, give, withdraw ...) are repeated only if the request never left, because a lost answer may mean it was done. Do not add retry loops at call sites; never answer a failed read with an old value.
- Only the **last** queued act is kept: do not fire a second act before the first one's `busy_until`.
- `gather resource=ore` takes the nearest vein of any ring; steer to a node and use `gather at`.
- A lease must be taken by whoever moves a hero; a watcher that holds a lease for ever freezes the hero.
- Level range: creatures far below the hero give almost no XP, far above kill it; the owner's current range is `rules.LEVEL_MIN` (4) to finisher+3.
- Do not widen safety margins (`AGGRO_RANGE`, `LEVEL_SPAN`) without the owner's word.
