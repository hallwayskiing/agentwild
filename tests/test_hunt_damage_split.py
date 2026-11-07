import unittest
from wild import ambush, rules, combat


class HuntDamageSplitTests(unittest.TestCase):
    def test_rules_max_level_diff(self):
        self.assertEqual(rules.MAX_LEVEL_DIFF, 5)

    def test_ambush_ignores_creatures_more_than_5_levels_below(self):
        m = {"level": 15, "hp": 300, "max_hp": 300, "attack": 50, "crit": 0.1, "defense": 20}
        # Creature level 9 is 6 levels below 15 (> 5 levels below): no threats to fight back
        low_creature = [{"kind": "wolf", "level": 9, "hp": 100, "attacks": True}]
        # threats list is empty, can_fight_back returns True, 1.0 (no threat left)
        ok, left = ambush.can_fight_back(m, low_creature)
        self.assertTrue(ok)
        self.assertEqual(left, 1.0)

        # Creature level 10 is within 5 levels: evaluated as real fight
        ok_creature = [{"kind": "wolf", "level": 10, "hp": 100, "attacks": True}]
        ok, left = ambush.can_fight_back(m, ok_creature)
        self.assertTrue(ok)

    def test_ambush_fight_back_skips_low_level(self):
        m = {"level": 15, "hp": 300, "max_hp": 300}
        # If fight_back is called with only low level creatures, it skips them and returns True
        res = ambush.fight_back(0, m, [{"kind": "wolf", "level": 8, "hp": 50, "id": 999}])
        self.assertTrue(res)


    def test_rules_target_max_dist(self):
        self.assertEqual(rules.TARGET_MAX_DIST, 25)

    def test_all_squads_are_solo(self):
        for name, sq in rules.SQUADS.items():
            self.assertEqual(len(sq["members"]), 1, f"Squad {name} must have exactly 1 member")

    def test_pick_target_uses_center_radius_25(self):
        from wild import hunt
        sq = hunt.Squad.__new__(hunt.Squad)
        sq.center = (300, 300)
        sq.view = {}
        sq.close = None
        sq.elites = True
        sq.gone = {}
        sq.skipped = {}
        sq.idle = 0
        sq.is_land_active = lambda uid: False
        sq.region = lambda x, y: True
        sq.rings = ("forest",)

        # target 1: at (310, 310) -> distance from center (300, 300) is 10 <= 25.
        t1 = {"id": 1, "kind": "wolf", "level": 12, "x": 310, "y": 310, "hp": 100}
        # target 2: at (330, 300) -> distance from center (300, 300) is 30 > 25 (even if near fin!).
        t2 = {"id": 2, "kind": "wolf", "level": 12, "x": 330, "y": 300, "hp": 100}

        sq.fetch = lambda fin_id, fin: [t1, t2]

        fin = {"level": 12, "ap": 50, "x": 328, "y": 300, "hp": 200, "max_hp": 200, "ring": "forest"}
        ready = {925: fin}
        res = sq.pick_target(925, fin, ready)
        self.assertEqual([t["id"] for t in res], [1])


if __name__ == "__main__":
    unittest.main()
