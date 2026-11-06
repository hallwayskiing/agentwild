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







    "ember": {"zh": "Ember", "members": [928], "hero_goal": 15, "rings": ("forest",), "region": "eastS", "center": (378, 256),







               "elites": True, "bounties": True, "bread": True, "bandage": True},







    "solstice": {"zh": "Solstice", "members": [None], "hero_goal": 15, "rings": ("forest",), "region": "westS", "center": (180, 320),







                "bread": True, "bandage": True},







    "silverwing": {"zh": "Silverwing", "members": [925, 933, 930, 932], "hero_goal": 15, "rings": ("forest",), "region": "eastN", "center": (344, 192),







                "bread": True},







    "nightjar": {"zh": "Nightjar", "members": [927, 929, 931, 934], "hero_goal": 15, "rings": ("forest",), "region": "eastS", "center": (344, 320),







                "bread": True},







    "astral": {"zh": "Astral", "members": [None, 928], "hero_goal": 99, "rings": ("meadow", "forest"), "region": "land",







                "elites": True, "bounties": True, "bread": True, "land": True, "bandage": True},







}







MAP_CAMP = (256, 256)







REGION_SPLIT_X = 256







AUTO_HUNT = False                # hunting requires an explicit persistent `hunt` order







AUTO_GIVE_MAIN = True            # gear, food, ladder/Abyss preparation and repair support may transfer to main







AUTO_COLLECT_MAIN = False        # periodic pooling of heroes' coins and valuables to main is disabled















# --- creature aggro & harvest rules --------------------------------------------------------------------------
# (the owner, 2026-11-05): 采矿与伐木安全范围限制
# safe_rings 之外的资源点由主号和领主前往，其他英雄回营
# --- the corrupted land (wild/landwatch.py) ------------------------------------------------------------------------------







CORRUPT_LAND = {"period": 7200,          # a land starts every 2 hours ...







                "open": 1200,            # ... open for 20 minutes ...







                "guardian": 900,         # ... then its guardian for 15; landwatch computes the next start from `ends_at`







                "grace": 180,            # look every 20 s for this long after the predicted start







                "fallback": 600,         # with no land seen yet: look this often







                "rings": ("meadow", "forest"),   # worth going when it opens in meadow or forest ...







                "max_level": 12,        # ... with maximum creature level up to 12







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







ROUTE_SAFETY = False             # owner: do not block or detour the tribe route for nearby creatures















# --- health ------------------------------------------------------------------------------------------------------------







HOME_BELOW = 0.40                # below 40%: queue recovery







AUTO_RECOVER = True              # the owner's automatic camp/tribe recovery order







RECOVER_TRIBE_GOODS = 20         # recovery goes to the tribe when depositable goods exceed this, or training quota remains







FIGHT_FLOOR = 0.20               # shared threshold: pre-fight model, stop line and ambush decisions







TRIP_MIN_AP = 25                 # 8 AP waypoint + about 16 AP walk, with room for home and variation







HEALED = 0.80                    # leave the tribe land at this much health







TELEPORT = False                 # never pay for a teleport (10 coins + 8 points): the tribe is reached on foot, unless the owner says so















# --- the tribe store, on every visit -----------------------------------------------------------------------------------







TRIBE_TOTEM = (341, 265)         # HALL migrated here on the owner's instruction







TRIBE_SPOT = (340, 265)          # verified standing place beside the totem







TRIBE_TRAINING = (342, 266)







TRIBE_WORKBENCH = (342, 270)
CAMP_WORKBENCH = (258, 262)
SALVAGE_PLAIN_GEAR = True   # the owner (2026-11-05): at camp or tribe, auto salvage all plain gear; keep >=1 pickaxe and >=1 axe in bag besides worn
SALVAGE_RUNED_BELOW_LEVEL = 10  # the owner (2026-11-05): auto salvage runed gear below level 10







TRIBE_LAND = 6







TRIBE_WAYPOINT = (356, 256)      # forest waypoint on the 512-tile map







STORE_KINDS = ("wood", "stone", "ore", "copper_ore", "iron_ore", "hide", "bone", "herb", "wheat_seed", "whetstone", "plating")







COIN_KEEP = 30                   # coins carried at most; the excess goes into the tribe treasury. Nothing is topped up from







                                 # the treasury (the owner, 2026-11-05): only the chief may take coins out, so a hero cannot







WORTH_STORING = 5                # this many goods in the bag make a visit to the store worthwhile







FOOD_KEEP = 5                    # each hero keeps 5 bread
MEAT_KEEP = 10                   # each hero keeps 10 meat (the owner, 2026-11-05): surplus goes in, shortfall comes out







STORE_CURRENCIES = False         # 严格白名单：不自动存任何通货/符文/宝石（只在多存少补中处理 dust）







KEEP_CURRENCIES = ("plating",)   # ... except these







STORE_EXCLUDED = ("shiftrune", "roast", "gem_sapphire_1", "goldmelt", "essence_hawk", "addrune", "gem_emerald_1", "crownseal")















# --- food and health ---------------------------------------------------------------------------------------------------







HEAL_BELOW = 0.70                # squads with `bread`: below this share of health the hero eats bandage > bread > roast ...







HEAL_TO = 0.80                   # ... up to this share







BANDAGE_WANT = {LORD: 30, None: 60}   # bandages stocked at the camp quartermaster (150 deeds per 3); the lord is stocked first







AUTO_RESTOCK_BANDAGE = False     # off (the owner, 2026-11-05): bandages are never auto-bought at the quartermaster







EAT_BELOW = 0                    # eat under or at this hunger (0: eat only when hunger drops to 0); roast first, then bread







ROAST_BATCH = 10                  # raw meat is never eaten: cook this many into roast (3 points per batch up to 10)















# --- gear ----------------------------------------------------------------------------------------------------------------







GEAR_GAIN = 2.0                  # gear is dealt out inside each squad only, to the lord first, when a piece beats by this







GEAR_PRIORITY = (LORD, None)      # owner: lord first (weapon locked), main second, then fill the other heroes' slots







UPKEEP_EVERY = 300               # seconds between the one periodic check: talent points, food, gear dealing, market listings







LOCKED_SLOTS = {928: ("weapon", "tool"), None: ("weapon",)}   # lord's weapon & tool locked, main's weapon locked







GEAR_REACH = 3                   # gear changes hands only between heroes this close (tiles): nobody walks to meet







USE_CURRENCIES = False           # never put runes or seals on gear without an explicit order







SELL_SURPLUS = False             # OFF (listing on the market is charged); True = a bag piece every squadmate beats goes on sale







PRICE_BASE = {"plain": 10, "runed": 50}







PRICE_PER_AFFIX = 10







NOT_FOR_SALE = ("treasured", "mythic", "set")







IDENTIFY_KEEP_COINS = 12         # identify drops in the camp while at least this many coins remain







IDENTIFY_MIN_LEVEL = 10          # automatically identify equipment level 10 and above















# --- repair (the owner, 2026-11-05) --------------------------------------------------------------------------------------







DUST_BELOW = 0.20                # at the camp or on the tribe's land: dust-repair every worn piece under this share of its durability







REPAIR_TRIBE_BROKEN = 2          # count zero-durability worn gear and one needed bag item for each empty slot







REPAIR_BROKEN_WEAPON = True      # a broken worn weapon triggers repairs on its own







REPAIR_BETTER_BAG_WEAPON = True  # repair a broken usable bag weapon that scores above the worn one







DUST_KEEP = 30                   # each character keeps 30 dust; surplus goes to the store and shortages are restocked







SUPPLY_KEEP = {"bread": FOOD_KEEP, "meat": MEAT_KEEP, "dust": DUST_KEEP}







STORE_WHITELIST = set(STORE_KINDS) | set(SUPPLY_KEEP)















# --- the Abyss -----------------------------------------------------------------------------------------------------------







ABYSS = False                    # OFF: the Abyss watch only runs when the file abyss.go exists







ABYSS_MIN_LEVEL = 7              # go in only when the lord, the main character and every hero are at least this level







ABYSS_BANK_AP = 120              # the main character banks this many points first (abyss.go: go now with 60)















# --- spending ------------------------------------------------------------------------------------------------------------







# no real money and no on-chain transaction, ever; deeds and market purchases only on the owner's word























def squad_name(unit):







    """The name of the squad a unit hunts in (the temporary land squad does not count)."""







    return next((v["zh"] for v in SQUADS.values() if unit in v["members"] and not v.get("land")), "")























def describe():







    """The rules in Chinese, with the values in force."""







    sq = "；".join("%s：%s" % (v["zh"], "、".join("主角" if u is None else str(u) for u in v["members"])) for v in SQUADS.values())







    return [







        "【部落位置】图腾 %s，训练场 %s；从森林传送点 %s 入内" % (TRIBE_TOTEM, TRIBE_TRAINING, TRIBE_WAYPOINT),







        "【自动恢复】%s：血量低于 %d%% 进入恢复；可存材料超过 %d 或有训练额度（满足一个即可）就直达森林传送点回部落，边训练边回血，两项都没有才回营地休息" % (







            "开启" if AUTO_RECOVER else "关闭", HOME_BELOW * 100, RECOVER_TRIBE_GOODS),







        "【打怪指令】%s；每个角色须有 hunt 指令才打怪，其他命令可中断" % ("自动" if AUTO_HUNT else "只按指令"),







        "【物品转移】定时汇集金币和通货%s；装备分配、食物分享、天梯及深渊准备、修理互助%s；材料和通货通过部落仓库存取" % (







            "开启" if AUTO_COLLECT_MAIN else "关闭", "可转给主角" if AUTO_GIVE_MAIN else "不转给主角"),







        "【小队】" + sq + "；各队只打自己区域的怪，找不到怪就往本队中心点靠拢；魔王可打精英、领每日悬赏",







        "【打怪】目标等级 ≤ 收尾者等级+%d，且 ≥ %d 级；等级低的先收尾；没目标就往中心点走" % (LEVEL_SPAN, LEVEL_MIN),







        "【腐化之地】按游戏给的结束时间算出下一次开启的时刻，到点立刻检测；开在%s、怪物 %d-%d 级、且主角和魔王都在打怪（没被命令占用）时，立刻组成一队前往；结束或被命令占用就各回原队；开启前 %d 分钟的等待与回血一样：有存货或受伤就回部落存取、回血，否则在营地等待" % (







            "草甸" if CORRUPT_LAND["ring"] == "meadow" else CORRUPT_LAND["ring"], *CORRUPT_LAND["levels"], CORRUPT_LAND["stage"] // 60),







        "【高级怪】主动攻击范围按 %d 格算，普通寻路保持 %d 格以上距离；部落路线安全阻拦%s；被伏击：打得过就还击，打不过回营地" % (AGGRO_RANGE, AGGRO_RANGE + 1, "开启" if ROUTE_SAFETY else "关闭"),







        "【血量】先吃食物回血，仍血<%d%% 才进入恢复；开战模型、战中停手和伏击判断共用 %d%% 血量底线；主角和魔王血<%d%% 时按绷带>面包>烤肉吃到 %d%%，其他队用面包和烤肉；绷带自动补充：%s；回到 %d%% 离开部落；不花钱治疗" % (







            HOME_BELOW * 100, FIGHT_FLOOR * 100, HEAL_BELOW * 100, HEAL_TO * 100, "开" if AUTO_RESTOCK_BANDAGE else "关", HEALED * 100),







        "【回部落】经森林路点步行，只在路线安全、有路点、行动点≥%d 时出发；恢复命令执行不了时在营地等待；"







        "强制命令（orders.txt 里写 force，如 `0 force home`）立刻执行，不管路是否关闭、有没有强怪，排在该英雄所有任务前面" % TRIP_MIN_AP,







        "【部落仓库】严格白名单存仓：全存（木头、石头、兽皮、草药、骨头、铜矿石、铁矿石、小麦种子、磨刀石、护甲片）；多存少补（肉保留 %d、面包保留 %d、符尘保留 %d）；熟肉、绷带、符文及宝石留在身上，不自动存仓；金币每人最多带 %d，多的存入金库" % (







            MEAT_KEEP, FOOD_KEEP, DUST_KEEP, COIN_KEEP),







        "【吃饭】饱食≤%d（掉完再吃）或受伤时吃：先烤肉，再面包；只剩生肉就先烤 %d 块（3 行动点）；不自动买肉" % (EAT_BELOW, ROAST_BATCH),







        "【修理】只用符文尘，不用金币；营地或部落工坊附近修理耐久低于 %.0f%% 的穿戴装备、填补空位的损坏装备和比当前武器评分更高的损坏武器；武器损坏、出现更好的损坏武器或损坏装备达 %d 件时，没有粉尘回部落补给，有粉尘回营地修理；每人保留 %d 粉尘，多存少补；金币不足以回城时安全步行回营地" % (







            DUST_BELOW * 100, REPAIR_TRIBE_BROKEN, DUST_KEEP),







        "【定时检查】每 %d 分钟一次：加点、肉食分发、匀装备、挂交易所；加点另在每场战斗后检查（锐利>坚毅>鹰眼>屠精>其余）" % (UPKEEP_EVERY // 60),







        "【装备】品质第一（珍藏>符刻>普通），同品质比数值；只在各小队内部、相距≤%d 格的人之间调配，魔王优先，提升≥%.0f 才换；不自动用符文；"







        "魔王的武器锁定；只自动鉴定 %d 级及以上装备；挂交易所：%s" % (GEAR_REACH, GEAR_GAIN, IDENTIFY_MIN_LEVEL, "开" if SELL_SURPLUS else "关"),







        "【装备优先级】魔王第一（武器锁定）、主角第二；其他英雄尽量穿满各部位",







        "【深渊】%s；开启后魔王、主角和所有英雄都≥%d 级才进，主角先攒 %d 行动点" % (







            "只在 abyss.go 文件存在时运行" if not ABYSS else "常开", ABYSS_MIN_LEVEL, ABYSS_BANK_AP),







    ]







