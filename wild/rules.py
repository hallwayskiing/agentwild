"""The owner's standing orders, all in one place. Every module reads its policy from here; to change a rule, change it
here and restart. `python wild.py rules` prints them in Chinese.

Precedence when two rules meet: safety (never walk into known danger, never spend real money) first, then the owner's
explicit orders below, then experience.
"""

# --- who is who --------------------------------------------------------------------------------------------------------
LORD = 928                       # the staked demon lord
# The hunting parties. Keys: `zh` the name shown to the owner; `members` unit ids (None = the main character); `rings` where it
# hunts; `region` a key of regions.REGIONS (the part of the map its creatures come from, so squads never chase the same one);
# `center` where members drift to when no creature is in sight; `hero_goal` the level up to which the lowest level takes the
# kills first; `elites`/`bounties` fight elites / claim the daily bounties; `bread` heal with items in the field (HEAL_*);
# `close` members stay within this many tiles of each other; `land` the corrupted-land squad (wild/landwatch.py).
SQUADS = {
    "forest": {"zh": "魔王·森林西北", "members": [928], "hero_goal": 15, "rings": ("forest",), "region": "westN", "center": (181, 192),
               "elites": True, "bounties": True, "bread": True, "bandage": True},
    "forestE": {"zh": "主角·森林西南", "members": [None], "hero_goal": 15, "rings": ("forest",), "region": "westS", "center": (180, 320),
                "bread": True, "bandage": True},
    "forestB": {"zh": "英雄一队·森林东北", "members": [925, 933, 930, 932], "hero_goal": 15, "rings": ("forest",), "region": "eastN", "center": (344, 192),
                "bread": True},
    "forestC": {"zh": "英雄二队·森林东南", "members": [927, 929, 931, 934], "hero_goal": 15, "rings": ("forest",), "region": "eastS", "center": (344, 320),
                "bread": True},
    "corrupt": {"zh": "主角+魔王·腐化之地", "members": [None, 928], "hero_goal": 99, "rings": ("meadow",), "region": "land",
                "elites": True, "bounties": True, "bread": True, "land": True, "bandage": True},
}
MAP_CAMP = (256, 256)
REGION_SPLIT_X = 256
AUTO_HUNT = False                # hunting requires an explicit persistent `hunt` order

# --- the corrupted land (wild/landwatch.py) ------------------------------------------------------------------------------
CORRUPT_LAND = {"period": 7200,          # a land starts every 2 hours ...
                "open": 1200,            # ... open for 20 minutes ...
                "guardian": 900,         # ... then its guardian for 15; landwatch computes the next start from `ends_at`
                "grace": 180,            # look every 20 s for this long after the predicted start
                "fallback": 600,         # with no land seen yet: look this often
                "ring": "meadow",        # worth going only when it opens in this ring ...
                "levels": [1, 6],        # ... with these creature levels
                "units": (None, 928),    # who goes (the main character and the lord), when they are hunting
                "stage": 900,            # ... and who wait this many seconds before a land of that kind is due, at the tribe when worthwhile else in the camp (every other land is the
                                         # lesser forest one, so the kind of the next is guessed from the last one seen)
                "reach": 5}              # the squad hunts this many tiles beyond the land's own radius

# --- hunting -----------------------------------------------------------------------------------------------------------
LEVEL_SPAN = 3                   # targets up to the finisher's level +3 ...
LEVEL_MIN = 4                    # ... and never below this creature level (the owner, 2026-11-05)
HERO_GOAL = 7                    # default `hero_goal` of a squad
# finisher order: the lowest level first (the slowest hero holds the Abyss back); a squad with no target drifts to its centre

# --- strong creatures ---------------------------------------------------------------------------------------------------
AGGRO_RANGE = 3                  # a strong creature (4+ levels above the walker) strikes within this many tiles (the owner's
                                 # measure); walkers keep one tile more away from the ones they know, and go round them

# --- health ------------------------------------------------------------------------------------------------------------
HOME_BELOW = 0.40                # below 40%: queue recovery (see tribe.recover for the tribe-versus-camp choice)
AUTO_RECOVER = True              # the owner's automatic camp/tribe recovery order
RECOVER_TRIBE_GOODS = 20         # recovery goes to the tribe when depositable goods exceed this, or training quota remains
FIGHT_FLOOR = 0.20               # a fight must end over this share of health: the model's check before it, the stop line during it
TRIP_MIN_AP = 35                 # 8 AP waypoint + about 16 AP walk, with room for home and variation
HEALED = 0.90                    # leave the tribe land at this much health
TELEPORT = False                 # never pay for a teleport (10 coins + 8 points): the tribe is reached on foot, unless the owner says so

# --- the tribe store, on every visit -----------------------------------------------------------------------------------
TRIBE_TOTEM = (341, 265)         # HALL migrated here on the owner's instruction
TRIBE_SPOT = (340, 265)          # verified standing place beside the totem
TRIBE_TRAINING = (342, 266)
TRIBE_LAND = 6
TRIBE_WAYPOINT = (356, 256)      # forest waypoint on the 512-tile map
STORE_KINDS = ("wood", "stone", "ore", "iron_ore", "hide", "bone", "herb", "wheat_seed")
COIN_KEEP = 30                   # coins carried at most; over that goes to the tribe treasury, under that is topped up from it
WORTH_STORING = 5                # this many goods in the bag make a visit to the store worthwhile
FOOD_KEEP = 5                    # each hero keeps 5 meat and 5 bread (and 5 roast): the rest goes in, a shortfall comes out
STORE_CURRENCIES = True          # heroes (not the main character) put runes, stones, dust, crystals, gems in the store too
KEEP_CURRENCIES = ("plating",)   # ... except these

# --- food and health ---------------------------------------------------------------------------------------------------
HEAL_BELOW = 0.70                # squads with `bread`: below this share of health the hero eats bandage > bread > roast ...
HEAL_TO = 0.80                   # ... up to this share
BANDAGE_WANT = {LORD: 30, None: 60}   # bandages stocked at the camp quartermaster (150 deeds per 3); the lord is stocked first
AUTO_RESTOCK_BANDAGE = False     # off (the owner, 2026-11-05): bandages are never auto-bought at the quartermaster
EAT_BELOW = 30                   # eat under this hunger (or when hurt and there is room); roast first, then bread
ROAST_BATCH = 3                  # raw meat is never eaten: cook this many into roast (3 points) when nothing else is left
BUY_MEAT_WHEN_STARVING = True    # a starving hero with no squadmate to share buys 3 meat at the camp (3 coins each at most)

# --- gear ----------------------------------------------------------------------------------------------------------------
GEAR_GAIN = 2.0                  # gear is dealt out inside each squad only, to the lord first, when a piece beats by this
GEAR_PRIORITY = (LORD, None)      # owner: lord first (weapon locked), main second, then fill the other heroes' slots
UPKEEP_EVERY = 300               # seconds between the one periodic check: talent points, food, gear dealing, market listings
LOCKED_SLOTS = {928: ("weapon",)}   # the demon lord's weapon is locked: never dealt, swapped or auto-replaced
GEAR_REACH = 3                   # gear changes hands only between heroes this close (tiles): nobody walks to meet
USE_CURRENCIES = False           # never put runes or seals on gear without an explicit order
SELL_SURPLUS = False             # OFF (listing on the market is charged); True = a bag piece every squadmate beats goes on sale
PRICE_BASE = {"plain": 10, "runed": 50}
PRICE_PER_AFFIX = 10
NOT_FOR_SALE = ("treasured", "mythic", "set")
IDENTIFY_KEEP_COINS = 12         # identify drops in the camp while at least this many coins remain

# --- the Abyss -----------------------------------------------------------------------------------------------------------
ABYSS = False                    # OFF: the Abyss watch only runs when the file abyss.go exists
ABYSS_MIN_LEVEL = 7              # go in only when the lord, the main character and every hero are at least this level
ABYSS_BANK_AP = 120              # the main character banks this many points first (abyss.go: go now with 60)

# --- spending ------------------------------------------------------------------------------------------------------------
# no real money and no on-chain transaction, ever; deeds and market purchases only on the owner's word


def squad_name(unit):
    """The Chinese name of the squad a unit hunts in (the corrupted-land squad does not count)."""
    return next((v["zh"] for v in SQUADS.values() if unit in v["members"] and not v.get("land")), "")


def describe():
    """The rules in Chinese, with the values in force."""
    sq = "；".join("%s：%s" % (v["zh"], "、".join("主角" if u is None else str(u) for u in v["members"])) for v in SQUADS.values())
    return [
        "【部落位置】图腾 %s，训练场 %s；从森林传送点 %s 入内" % (TRIBE_TOTEM, TRIBE_TRAINING, TRIBE_WAYPOINT),
        "【自动恢复】%s：血量低于 %d%% 进入恢复；可存材料超过 %d 或有训练额度（满足一个即可）就直达森林传送点回部落，边训练边回血，两项都没有才回营地休息" % (
            "开启" if AUTO_RECOVER else "关闭", HOME_BELOW * 100, RECOVER_TRIBE_GOODS),
        "【打怪指令】%s；每个角色须有 hunt 指令才打怪，其他命令可中断" % ("自动" if AUTO_HUNT else "只按指令"),
        "【小队】" + sq + "；各队只打自己区域的怪，找不到怪就往本队中心点靠拢；魔王可打精英、领每日悬赏",
        "【打怪】目标等级 ≤ 收尾者等级+%d，且 ≥ %d 级；等级低的先收尾；没目标就往中心点走" % (LEVEL_SPAN, LEVEL_MIN),
        "【腐化之地】按游戏给的结束时间算出下一次开启的时刻，到点立刻检测；开在%s、怪物 %d-%d 级、且主角和魔王都在打怪（没被命令占用）时，立刻组成一队前往；结束或被命令占用就各回原队；开启前 %d 分钟的等待与回血一样：有存货或受伤就回部落存取、回血，否则在营地等待" % (
            "草甸" if CORRUPT_LAND["ring"] == "meadow" else CORRUPT_LAND["ring"], *CORRUPT_LAND["levels"], CORRUPT_LAND["stage"] // 60),
        "【高级怪】主动攻击范围按 %d 格算，寻路时自动保持 %d 格以上距离并绕开已知的高级怪；进部落经森林传送点，路线不安全就等待（强制命令除外）；被伏击：打得过就还击，打不过回营地" % (AGGRO_RANGE, AGGRO_RANGE + 1),
        "【血量】先吃食物回血，仍血<%d%% 才进入恢复；每场战斗要保证打完血量≥%d%%（开打前模型估算、战斗中停手线都用它）；主角和魔王血<%d%% 时按绷带>面包>烤肉吃到 %d%%，其他队用面包和烤肉；绷带自动补充：%s；回到 %d%% 离开部落；不花钱治疗" % (
            HOME_BELOW * 100, FIGHT_FLOOR * 100, HEAL_BELOW * 100, HEAL_TO * 100, "开" if AUTO_RESTOCK_BANDAGE else "关", HEALED * 100),
        "【回部落】经森林路点步行，只在路线安全、有路点、行动点≥%d 时出发；恢复命令执行不了时在营地等待；"
        "强制命令（orders.txt 里写 force，如 `0 force home`）立刻执行，不管路是否关闭、有没有强怪，排在该英雄所有任务前面" % TRIP_MIN_AP,
        "【部落仓库】每次到部落：先存材料，再把面包和肉补到各 %d，多余的面包/肉/烤肉也存进去；绷带不存；金币每人最多带 %d，多的存入金库，不足从金库补到 %d；英雄存符文类通货（%s 除外），主角的符文宝石留在身上" % (
            FOOD_KEEP, COIN_KEEP, COIN_KEEP, "、".join(KEEP_CURRENCIES)),
        "【吃饭】饱食<%d 或受伤时吃：先烤肉，再面包；只剩生肉就先烤 %d 块（3 行动点）；饿了又没人分肉时在营地买 3 块肉" % (EAT_BELOW, ROAST_BATCH),
        "【修理】只修坏了的装备（工具坏了才修）；回营地或部落时耐久低于30%的用符文尘修",
        "【定时检查】每 %d 分钟一次：加点、肉食分发、匀装备、挂交易所；加点另在每场战斗后检查（锐利>坚毅>鹰眼>屠精>其余）" % (UPKEEP_EVERY // 60),
        "【装备】品质第一（珍藏>符刻>普通），同品质比数值；只在各小队内部、相距≤%d 格的人之间调配，魔王优先，提升≥%.0f 才换；不自动用符文；"
        "魔王的武器锁定；挂交易所：%s" % (GEAR_REACH, GEAR_GAIN, "开" if SELL_SURPLUS else "关"),
        "【装备优先级】魔王第一（武器锁定）、主角第二；其他英雄尽量穿满各部位；513暂不参与",
        "【深渊】%s；开启后魔王、主角和所有英雄都≥%d 级才进，主角先攒 %d 行动点" % (
            "只在 abyss.go 文件存在时运行" if not ABYSS else "常开", ABYSS_MIN_LEVEL, ABYSS_BANK_AP),
    ]
